# Matmul: NumPy vs Eigen in cpp-repl vs native Eigen

**The question:** how fast is C++ typed at the cpp-repl prompt, and what does
the REPL's iteration latency cost?

The workload is a dense matrix product, `C = A * B` for n × n doubles, which
NumPy hands to OpenBLAS and Eigen compiles from templates. The same Eigen
source runs three ways: compiled by clang++, typed into cpp-repl, and
compiled ahead of time into a library that cpp-repl calls.

## What is compared

| Group | Arm | What it is |
|---|---|---|
| core, 1 thread | `numpy` | `np.matmul(A, B, out=C)`, float64, C order, OpenBLAS with 1 thread |
| | `eigen-native` | `matmul.cpp` built by clang++ with FLAGS |
| | `eigen-repl` | the same `matmul.cpp` through `cpp-repl --batch` with FLAGS |
| | `eigen-repl-aot` | cpp-repl with default flags, calling `libbench_kernel.so` (built with FLAGS) through `--lib` |
| | `eigen-repl-default` | cpp-repl with `-std=c++20` alone (-O0): what you get out of the box. n ≤ 1024, one round |
| mt, 16 threads | `numpy@16`, `eigen-native-omp`, `eigen-repl-omp`, `eigen-repl-aot-omp` | the same with OpenBLAS threads and OpenMP; the JIT arm loads libomp with `--lib` |
| diag | `naive-native` / `naive-repl` | three plain loops, n ≤ 1024: does `-O3` reach the JIT, and is a gap the JIT's code generation or Eigen? |
| | `eigen-repl-generic` | `-O3` without `-march=native`, the likeliest mistake at the prompt |
| | `eigen-native-gcc` | g++ with FLAGS: "native" is not one number |

FLAGS is `-std=c++20 -O3 -march=native -DNDEBUG`. Every C++ arm records what
it was built with (Eigen version, the `EIGEN_VECTORIZE_*` and `__AVX2__`-style
macros, `NDEBUG`, `__OPTIMIZE__`, `_OPENMP`, Eigen's thread count, the
compiler), so the configuration is checked rather than assumed. The REPL arms
load cpp-repl's own preludes, the core and the standard library's, and no
profile, so nothing changes Eigen's configuration or hides its parse time.

## One source, compiled and in the REPL

[`cpp/matmul.cpp`](cpp/matmul.cpp) is the whole driver. cpp-repl turns it into
cells: each directive and file-scope declaration on its own, then `main()`'s
body. A file-scope `double t = bench_stamp("…");` runs when its cell runs, so
the stamps time the REPL's start-up, the Eigen headers, a trivial cell, and
the cell that defines the kernel. The stamp after the kernel takes
`&bench_kernel`, which makes the JIT generate the kernel's machine code inside
that cell. Compiled, the same stamps run during static initialisation,
microseconds apart, and the harness times the compiler instead.

The driver keeps three rules:
- **Every `#if` lives in [`cpp/bench.hpp`](cpp/bench.hpp).** The preprocessor sees a header whole.
- **`main()` holds no work of its own.** At the prompt its variables become globals and its loops run inside functions of their own.
- **The kernel is a file-scope, non-inline function.** The REPL compiles Eigen's GEMM once, in its cell.

[`cpp/session.repl`](cpp/session.repl) follows `matmul.cpp` in the latency
runs and measures a warm session: calling the compiled kernel again, and
typing a product the session has not used yet (`A.transpose() * B`).

## Protocol

- **Where:** the `cpp-repl:bench-llvm23` image is the primary target. There the JIT and clang++ are the same
  LLVM (23.1.2) and NumPy is the pip wheel with OpenBLAS.
  - A host run uses clang++-18 with clang-repl-18 and a Python whose NumPy uses OpenBLAS (the pip wheel
    does; `--python` names it).
  - clang-repl 18 cannot run Eigen's kernels. Eigen 3.4 uses inline assembly for its FMA (with clang) and in
    its GEBP kernel, and clang-repl 18 stops with "Inline asm not supported by this streamer because we
    don't have an asm parser for this target". The host's REPL Eigen arms therefore record that error;
    clang-repl 23 runs them.
  - The NumPy arm refuses a NumPy linked to netlib's reference BLAS (Ubuntu's `python3-numpy`), which would
    time Fortran loops rather than NumPy.
- **Allocator:** Eigen allocates its GEMM workspace on every call, about 0.5 MB at n = 256, which sits at
  glibc's initial mmap threshold.
  - A fresh native process therefore maps, faults in and unmaps the workspace on every call, costing
    25–30% at n = 256 on this machine. The long-lived clang-repl process has already raised its threshold
    and does not pay this.
  - Every arm runs with `GLIBC_TUNABLES` raising the threshold to 32 MB, so the arms differ in code, not in
    allocator state. `--no-malloc-tuning` shows the effect.
- **Pinning:** one-thread arms run on CPU 6 (CCD0, not one of the preferred cores 2 and 7; its SMT sibling
  22 stays idle). Multi-thread arms run on CPUs 0–15, one thread per physical core, with
  `OMP_PLACES=cores OMP_PROC_BIND=close`. The harness runs on CPU 31. On another machine the defaults follow
  its topology.
- **Inputs:** A(i, j) = ((31i + 17j) mod 97)/97 and B(i, j) = ((7i + 13j) mod 89)/89, the same matrices in
  NumPy's row-major and Eigen's column-major storage. Buffers of 4 MB or more are 2 MB aligned and offered
  transparent huge pages, as NumPy's are. The product is `C.noalias() = A * B` over `Eigen::Map`s.
- **Sizes:** 32 64 128 256 384 512 768 1024 1536 2048 3072, plus 4096 with 16 threads. A size whose predicted
  call time, t(previous) × (n/previous)³, exceeds 10 s is skipped and recorded, and so is every size after it.
- **Timing:**
  - Warm-up: at least 2 calls and 0.25 s. The first call is reported on its own.
  - Samples: each sample times k calls, with k chosen so the sample lasts at least 20 ms. Sampling stops after
    at least 5 samples and 1 s, and at 50 samples or 30 s.
  - Every raw per-call time goes into the result file.
- **Rounds:** three. Within a round the one-thread arms run first and the 16-thread arms after, each in a
  seeded random order. A 10 Hz sampler records the pinned CPUs' clock and the package temperature. Before
  each arm the harness waits up to 30 s for the CPU to come within 5 °C of idle. It changes no system
  setting.
- **Latency pass:** three rounds of cold starts at n = 256 for `numpy`, `eigen-native` (compile plus run),
  `eigen-repl`, `eigen-repl-aot` and `eigen-repl-default`, each REPL run followed by `session.repl`.
- **Statistics:**
  - Headline: the median time per call over all samples of all rounds.
  - Spread: the interquartile range and the minimum of those samples, and the range of the per-round medians.
  - Comparisons: arms are compared by paired per-round ratios.
- **Validity:** a failed check makes the run invalid; it does not decide a hypothesis.
  - Freivalds' check: ‖Cv − A(Bv)‖ / ‖A(Bv)‖ < 1e-10.
  - `sum(C)` equals its exact value (computed in rationals from the formulas above) to 1e-10.
  - GFLOP/s stays within the peak (16 FLOP per cycle per core × threads × the maximum clock).
  - Each REPL arm reports the same Eigen, vectorisation, `NDEBUG` and `__OPTIMIZE__` as its native arm.
  - NumPy's BLAS is OpenBLAS, MKL or BLIS, running the requested number of threads.

## Hypotheses

These were written on 2026-10-13, before the first full run. Each is judged on the per-round values: it
passes when every round meets it, fails when none does, and is inconclusive when the rounds straddle the
threshold. Every verdict is reported, whichever way it falls.

- **H0:** the configuration is as intended (every validity check passes).
- **H1:** `eigen-repl` runs at 0.95–1.05 of `eigen-native` for n ≥ 256 (within ±10% below that). If it fails,
  the naive pair says whether the gap is the JIT's code generation or Eigen's configuration.
- **H2:** `eigen-repl-default` is at least 20× slower than `eigen-native` at n = 256 (prior: 50–200×).
- **H3:** `eigen-repl-aot` runs at 0.97–1.03 of `eigen-native` for n ≥ 128.
- **H4:** `eigen-repl-generic` reaches at most 0.6 of `eigen-repl` for n ≥ 512.
- **H5:** NumPy is at least as fast as `eigen-native` for n ≥ 512 (prior: 10–30% ahead), and Eigen is ahead at
  n ≤ 64. The report names the size where they cross.
- **H6:** with 16 threads, `eigen-repl-omp` runs at 0.95–1.05 of `eigen-native-omp`, or fails with a recorded
  reason. `eigen-repl-aot-omp` runs at 0.95–1.05 either way.
- **H7:** in a warm session, re-running the compiled kernel costs under 0.2 s beyond its own run time.

## Running it

```sh
bench/docker.sh --quick                       # 1 round, n = 256 and 1024: about 3 minutes
bench/docker.sh                               # the full protocol: about 30 minutes
python3 bench/report.py bench/results/<run>.json

# On the host, with a NumPy that uses OpenBLAS:
python3 bench/run.py --python /path/to/venv/bin/python
```

`bench/run.py --help` lists the options:
- `--arms core,mt,diag` or arm names
- `--threads 1`
- `--sizes 256,1024` or `--sizes 64:1024`
- `--rounds`
- `--no-latency` and `--latency-only`
- `--no-malloc-tuning`
- `--dry-run`, which prints every build and launch without running them

Run one benchmark at a time on an otherwise idle machine. Host and Docker runs share `bench/build/`.

Each run writes `bench/results/<host>-llvm<N>-<UTC time>[-tag].json` and keeps every arm's output under
`bench/results/logs/`. `report.py` adds, next to the JSON:
- `.md`: tables, hypothesis verdicts, environment and failed checks;
- `.csv`: one row per arm, phase, round and size;
- `-gflops.png`: GFLOP/s against n, with the peak at the measured clock;
- `-ratio.png`: each arm relative to its native arm, with a ±5% band.

`python3 bench/test_bench.py` checks the harness's own arithmetic.

## Results

The full run of 2026-10-14 in `cpp-repl:bench-llvm23`: Ryzen 9 5950X, clang
and clang-repl 23.1.2, Eigen 3.4.0, NumPy 2.5.4 with OpenBLAS 0.3.34, three
rounds, all 1246 validity checks passed. The complete report, with every
table, the environment and the per-round ranges, is
[`results/nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0125Z.md`](results/nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0125Z.md).
The first full run is not the result: its checksums were written with too
few digits ([`results/earlier/`](results/earlier/README.md)).

![GFLOP/s against n](results/nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0125Z-gflops.png)

**One thread, GFLOP/s** (median of all samples; the core's peak at the
measured 4.62 GHz is 74):

| n | Eigen, native | Eigen in cpp-repl | cpp-repl + compiled kernel | NumPy | cpp-repl, no flags |
|---:|---:|---:|---:|---:|---:|
| 32 | 50.3 | 50.6 | 50.4 | 27.8 | 0.2 |
| 128 | 63.2 | 62.8 | 63.4 | 58.5 | 0.2 |
| 256 | 62.7 | 63.7 | 63.5 | 61.7 | 0.2 |
| 1024 | 64.0 | 64.4 | 63.1 | 60.7 | 0.2 |
| 3072 | 63.1 | 62.7 | 61.9 | 62.7 | skipped |

**16 threads, GFLOP/s:**

| n | Eigen, native, OpenMP | Eigen in cpp-repl, OpenMP | NumPy |
|---:|---:|---:|---:|
| 128 | 122.6 | 119.4 | 273.6 |
| 1024 | 594.3 | 576.3 | 558.5 |
| 3072 | 663.7 | 659.5 | 485.9 |

**Latency at n = 256** (cold start, from launch; warm, beyond the call's own
run time):

| | first product | warm re-run | a new expression |
|---|---:|---:|---:|
| NumPy | 0.06 s | | |
| Eigen, native (compiling included) | 2.5 s | | |
| Eigen in cpp-repl, -O3 | 3.2 s: start 1.3, Eigen headers 1.0, kernel 0.5 | 0.08 s | 0.72 s |
| cpp-repl, no flags | 2.4 s | 0.02 s | 0.31 s |

**The hypotheses:**

| | verdict | what the run says |
|---|---|---|
| H0 | pass | 1246 of 1246 checks passed |
| H1 | pass | Eigen in cpp-repl runs at 0.98–1.02 of native for every n ≥ 256 |
| H2 | pass | with no flags, cpp-repl runs at 0.003 of native: about 300× slower |
| H3 | inconclusive | the compiled kernel is within ±1.5% of native at every size, but single rounds stray to 0.96 and 1.03 |
| H4 | pass | without `-march=native`, cpp-repl reaches 0.35–0.38 of itself with it |
| H5 | fail | NumPy is 1.5–6% behind Eigen for n ≥ 512, and 0.55× at n = 32: it never catches up |
| H6 | inconclusive | OpenMP inside the JIT works, at 0.97–1.00 of native; one round at n = 1024 fell to 0.93 |
| H7 | pass | re-running the compiled kernel in a warm session costs 0.08 s |

**What this means:**
- **Tuned, the JIT is the compiler.** With `-O3 -march=native`, clang-repl
  generates the same code clang++ does. Eigen typed at the prompt runs at
  native speed, as do plain loops (about 21 GFLOP/s both ways, see the full
  report): the optimiser runs in full.
- **The default is not tuned.** cpp-repl compiles at -O0 unless told
  otherwise, about 300× slower. Without `-march=native`, Eigen runs on SSE2
  and loses almost two thirds.
- **A compiled kernel costs nothing to call.** cpp-repl calling a library
  built ahead of time runs at native speed, whatever the session's own flags.
- **NumPy did not win.** The expectation written down beforehand was that
  OpenBLAS's hand-written kernels would beat Eigen's templates by 10–30%.
  On this Zen 3 CPU, one thread, Eigen built by clang 23 is a few percent
  ahead. With 16 threads, OpenBLAS parallelises small products sooner
  (n = 128–256) and falls behind on large ones. g++ 13 builds the same Eigen
  25% slower than clang does.
- **The price is time to compile.** Starting cpp-repl and parsing Eigen
  takes 2.3 s. Each new piece of code compiles in a fraction of a second:
  0.5 s for the GEMM kernel at -O3, 0.7 s for a new product expression. Once
  compiled, it runs at native speed.

## Files

| File | What it does |
|---|---|
| [`run.py`](run.py) | builds the native programs and kernel libraries, launches each arm pinned, samples clock and temperature, checks results, writes the JSON |
| [`arms.py`](arms.py) | the arm table |
| [`sysinfo.py`](sysinfo.py) | CPU, clocks, huge pages, temperature sensor, tool and library versions |
| [`report.py`](report.py) | statistics, tables, plots and hypothesis verdicts, from the JSON alone |
| [`docker.sh`](docker.sh) | runs `run.py` inside the bench image |
| [`cpp/stamp.hpp`](cpp/stamp.hpp) | `bench_stamp()`: a monotonic-clock stamp per cell |
| [`cpp/kernels.hpp`](cpp/kernels.hpp) | `eigen_gemm` and `naive_gemm` over raw pointers |
| [`cpp/bench.hpp`](cpp/bench.hpp) | configuration, buffers, fill, timing, checks, records; every `#if` |
| [`cpp/matmul.cpp`](cpp/matmul.cpp) | the driver, compiled and in the REPL |
| [`cpp/kernel_aot.cpp`](cpp/kernel_aot.cpp) | the `extern "C"` kernel for `libbench_kernel.so` and `libbench_kernel_omp.so` |
| [`cpp/session.repl`](cpp/session.repl) | the warm-session cells |
| [`py/numpy_matmul.py`](py/numpy_matmul.py) | the NumPy arm |
| [`test_bench.py`](test_bench.py) | checks of the exact checksum, size parsing, arm selection, verdicts and latency arithmetic |
