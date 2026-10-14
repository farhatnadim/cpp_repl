# Matmul benchmark: nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0125Z

Run 2026-10-14T01:25:22+00:00 to 2026-10-14T01:41:25+00:00 on AMD Ryzen 9 5950X 16-Core Processor (nadim-Alienware-Aurora-Ryzen-Edition), LLVM 23. Values are GFLOP/s (2n³ per call over the median time per call); higher is better. Ratios are paired within a round, with the range over rounds in brackets.

## One thread

| n | Eigen, native | NumPy | Eigen in cpp-repl | cpp-repl + compiled kernel | cpp-repl, no flags | REPL / native |
|---|---:|---:|---:|---:|---:|---:|
| 32 | 50.3 | 27.8 | 50.6 | 50.4 | 0.2 | 0.999 [0.964–1.008] |
| 64 | 56.7 | 50.7 | 57.8 | 58.0 | 0.2 | 1.011 [1.003–1.022] |
| 128 | 63.2 | 58.5 | 62.8 | 63.4 | 0.2 | 1.003 [0.989–1.006] |
| 256 | 62.7 | 61.7 | 63.7 | 63.5 | 0.2 | 1.016 [1.002–1.019] |
| 384 | 62.1 | 62.3 | 63.1 | 63.2 | 0.2 | 1.020 [1.002–1.026] |
| 512 | 62.1 | 60.4 | 62.0 | 63.1 | 0.2 | 1.008 [0.987–1.011] |
| 768 | 64.6 | 63.7 | 64.7 | 64.3 | 0.2 | 1.002 [0.999–1.003] |
| 1024 | 64.0 | 60.7 | 64.4 | 63.1 | 0.2 | 1.019 [0.997–1.024] |
| 1536 | 64.5 | 62.6 | 63.5 | 64.2 | skipped | 0.980 [0.972–1.002] |
| 2048 | 64.3 | 60.9 | 63.5 | 64.1 | skipped | 0.986 [0.980–0.991] |
| 3072 | 63.1 | 62.7 | 62.7 | 61.9 | skipped | 0.989 [0.977–0.997] |

## 16 threads

| n | NumPy, 16 threads | Eigen, native, OpenMP | Eigen in cpp-repl, OpenMP | cpp-repl + compiled OpenMP kernel | REPL / native | NumPy speed-up | Eigen native speed-up |
|---|---:|---:|---:|---:|---:|---:|---:|
| 32 | 27.6 | 50.9 | 50.4 | 51.1 | 0.992 [0.975–1.009] | 1.0× | 1.0× |
| 64 | 50.6 | 123.9 | 122.2 | 122.8 | 0.986 [0.980–0.994] | 1.0× | 2.2× |
| 128 | 273.6 | 122.6 | 119.4 | 121.2 | 0.971 [0.970–0.979] | 4.7× | 1.9× |
| 256 | 284.2 | 261.6 | 260.7 | 262.9 | 0.996 [0.994–0.998] | 4.6× | 4.2× |
| 384 | 358.0 | 388.7 | 387.7 | 387.2 | 0.996 [0.996–0.997] | 5.7× | 6.3× |
| 512 | 447.9 | 434.8 | 432.3 | 431.2 | 0.994 [0.980–0.995] | 7.4× | 7.0× |
| 768 | 565.1 | 552.7 | 542.9 | 549.0 | 0.976 [0.966–1.000] | 8.9× | 8.6× |
| 1024 | 558.5 | 594.3 | 576.3 | 593.2 | 0.969 [0.928–1.004] | 9.2× | 9.3× |
| 1536 | 605.4 | 654.5 | 655.5 | 657.1 | 1.001 [0.992–1.008] | 9.7× | 10.1× |
| 2048 | 599.4 | 657.9 | 655.4 | 657.5 | 0.995 [0.958–1.005] | 9.8× | 10.2× |
| 3072 | 485.9 | 663.7 | 659.5 | 660.7 | 0.985 [0.937–0.999] | 7.7× | 10.5× |
| 4096 | 535.1 | 600.5 | 599.3 | 603.0 | 0.995 [0.949–1.020] | — | — |

## Latency

Cold start, from launch: process start-up, then headers (REPL) or `import numpy`, then compiling the kernel (for native: the whole program, before launch), then the first product at n = 256. Warm session (REPL only): what re-running the compiled kernel, and typing a new Eigen expression (`A.transpose() * B`), cost beyond their own run time.

| arm | start-up | headers / import | kernel compile | first product | warm re-run | new expression |
|---|---:|---:|---:|---:|---:|---:|
| Eigen in cpp-repl | 1.333 s [1.308–1.341] | 1.002 s [0.993–1.004] | 0.517 s [0.515–0.520] | 3.174 s [3.136–3.175] | 0.078 s [0.077–0.079] | 0.720 s [0.720–0.723] |
| Eigen, native | 0.002 s [0.002–0.002] | — | 2.473 s [2.466–2.495] | 2.476 s [2.470–2.499] | — | — |
| NumPy | — | 0.053 s [0.053–0.053] | — | 0.055 s [0.054–0.055] | — | — |
| cpp-repl, no flags | 1.297 s [1.272–1.300] | 0.680 s [0.668–0.681] | 0.189 s [0.186–0.192] | 2.415 s [2.381–2.419] | 0.017 s [0.016–0.017] | 0.310 s [0.310–0.313] |
| cpp-repl + compiled kernel | 1.301 s [1.278–1.302] | 0.680 s [0.665–0.685] | 0.001 s [0.001–0.001] | 2.084 s [2.045–2.091] | 0.016 s [0.016–0.016] | 0.367 s [0.365–0.369] |

## Diagnostics

| n | plain loops, native | plain loops in cpp-repl | cpp-repl without -march=native | Eigen, native, g++ | naive REPL / native | generic / tuned REPL | g++ / clang++ |
|---|---:|---:|---:|---:|---:|---:|---:|
| 32 | 20.7 | 20.7 | 20.1 | 39.0 | 0.997 [0.889–1.004] | 0.396 [0.395–0.412] | 0.779 [0.767–0.784] |
| 64 | 20.8 | 20.8 | 22.0 | 43.2 | 0.994 [0.937–1.002] | 0.380 [0.375–0.383] | 0.759 [0.750–0.762] |
| 128 | 20.2 | 20.7 | 23.2 | 46.6 | 1.024 [1.010–1.030] | 0.365 [0.359–0.373] | 0.738 [0.730–0.751] |
| 256 | 20.5 | 20.5 | 23.4 | 47.0 | 1.023 [0.994–1.153] | 0.366 [0.357–0.372] | 0.752 [0.734–0.754] |
| 384 | 20.8 | 20.8 | 22.9 | 46.8 | 1.000 [0.997–1.022] | 0.358 [0.345–0.379] | 0.751 [0.749–0.755] |
| 512 | 18.3 | 19.1 | 23.4 | 46.9 | 1.038 [1.008–1.048] | 0.374 [0.371–0.381] | 0.756 [0.753–0.766] |
| 768 | 22.8 | 22.7 | 23.0 | 48.4 | 0.963 [0.959–1.045] | 0.354 [0.353–0.368] | 0.753 [0.747–0.756] |
| 1024 | 21.4 | 21.4 | 23.6 | 47.5 | 0.953 [0.777–1.026] | 0.367 [0.365–0.367] | 0.744 [0.743–0.750] |
| 1536 | skipped | skipped | 23.3 | 47.6 | n/a | 0.367 [0.361–0.368] | 0.743 [0.726–0.744] |
| 2048 | skipped | skipped | 23.3 | 47.8 | n/a | 0.363 [0.362–0.380] | 0.742 [0.741–0.754] |
| 3072 | skipped | skipped | 23.4 | 48.7 | n/a | 0.373 [0.370–0.387] | 0.771 [0.757–0.794] |

## Hypotheses

Written down before the first run (bench/README.md). Each is judged on the per-round values: pass when every round meets it, fail when none does, inconclusive otherwise.

|  | hypothesis | verdict | evidence |
|---|---|---|---|
| H0 | the configuration is as intended (every validity check passes) | pass | 1246 of 1246 checks passed |
| H1 | Eigen in cpp-repl runs at 0.95–1.05 of native for n ≥ 256 | pass | n=256: 1.016 [1.002–1.019], n=384: 1.020 [1.002–1.026], n=512: 1.008 [0.987–1.011], n=768: 1.002 [0.999–1.003], n=1024: 1.019 [0.997–1.024], n=1536: 0.980 [0.972–1.002], n=2048: 0.986 [0.980–0.991], n=3072: 0.989 [0.977–0.997] |
| H2 | cpp-repl with no flags is at least 20× slower than native at n = 256 | pass | speed ratio 0.003 |
| H3 | cpp-repl calling the compiled kernel runs at 0.97–1.03 of native for n ≥ 128 | inconclusive | n=128: 1.004 [1.003–1.006], n=256: 1.015 [1.006–1.020], n=384: 1.004 [0.998–1.034], n=512: 1.015 [1.004–1.019], n=768: 1.004 [0.992–1.006], n=1024: 0.999 [0.992–1.002], n=1536: 0.995 [0.975–1.011], n=2048: 0.995 [0.994–0.997], n=3072: 0.995 [0.962–0.998] |
| H4 | cpp-repl without -march=native reaches at most 0.6 of cpp-repl with it, for n ≥ 512 | pass | n=512: 0.374 [0.371–0.381], n=768: 0.354 [0.353–0.368], n=1024: 0.367 [0.365–0.367], n=1536: 0.367 [0.361–0.368], n=2048: 0.363 [0.362–0.380], n=3072: 0.373 [0.370–0.387] |
| H5 | NumPy is at least as fast as native Eigen for n ≥ 512, and Eigen is ahead at n ≤ 64 | fail | n ≥ 512: n=512: 0.967 [0.967–0.980], n=768: 0.985 [0.977–1.006], n=1024: 0.942 [0.940–0.987], n=1536: 0.962 [0.946–0.987], n=2048: 0.942 [0.931–0.951], n=3072: 0.975 [0.962–1.007]; n ≤ 64: n=32: 0.551 [0.549–0.552], n=64: 0.886 [0.871–0.895]; NumPy never catches up |
| H6 | with 16 threads, OpenMP in the JIT runs at 0.95–1.05 of native or fails with a recorded reason; the compiled OpenMP kernel runs at 0.95–1.05 | inconclusive | JIT: n=32: 0.992 [0.975–1.009], n=64: 0.986 [0.980–0.994], n=128: 0.971 [0.970–0.979], n=256: 0.996 [0.994–0.998], n=384: 0.996 [0.996–0.997], n=512: 0.994 [0.980–0.995], n=768: 0.976 [0.966–1.000], n=1024: 0.969 [0.928–1.004], n=1536: 1.001 [0.992–1.008], n=2048: 0.995 [0.958–1.005], n=3072: 0.985 [0.937–0.999], n=4096: 0.995 [0.949–1.020]; compiled kernel: n=32: 1.006 [0.992–1.018], n=64: 1.001 [0.984–1.002], n=128: 0.982 [0.972–1.002], n=256: 1.001 [0.986–1.010], n=384: 0.994 [0.975–0.999], n=512: 0.985 [0.984–1.001], n=768: 0.989 [0.986–1.004], n=1024: 0.997 [0.996–1.005], n=1536: 0.999 [0.997–1.015], n=2048: 0.999 [0.993–1.002], n=3072: 0.990 [0.851–1.027], n=4096: 0.989 [0.937–1.009] |
| H7 | in a warm session, re-running the compiled kernel costs under 0.2 s beyond the run itself | pass | overhead 0.078 s [0.077–0.079] over 3 rounds |

## Environment

|  |  |
|---|---|
| CPU | AMD Ryzen 9 5950X 16-Core Processor: 16 cores, 32 threads, 16 FLOP/cycle/core, max 5086 MHz |
| mean clock while timing | 1 thread 4621 MHz |
|  | 16 threads 3461 MHz |
| temperature | 67–91 °C |
| clang++ | Ubuntu clang version 23.1.2 (++20260919103626+4b1925210476-1~exp1~20260919223755.77) |
| clang-repl | Ubuntu LLVM version 23.1.2 |
| g++ | g++-13 (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0 |
| Eigen | 3.4.0 (/usr/include/eigen3) |
| NumPy | 2.5.4 with openblas 0.3.34.106.0 (Haswell) |
| huge pages | {'enabled': 'madvise', 'defrag': 'madvise'} |
| frequency policy | amd-pstate-epp, governor performance, preference performance, boost 1 |
| load average at start | 0.96, 2.87, 2.39 |
| container | sha256:1ee41fb6500cb013ce20e94565378066ae32d1f8eb1419a22a366b377db72140 |

