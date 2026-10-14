# Matmul benchmark: nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0106Z

Run 2026-10-14T01:06:12+00:00 to 2026-10-14T01:22:50+00:00 on AMD Ryzen 9 5950X 16-Core Processor (nadim-Alienware-Aurora-Ryzen-Edition), LLVM 23. Values are GFLOP/s (2n³ per call over the median time per call); higher is better. Ratios are paired within a round, with the range over rounds in brackets.

## One thread

| n | Eigen, native | NumPy | Eigen in cpp-repl | cpp-repl + compiled kernel | cpp-repl, no flags | REPL / native |
|---|---:|---:|---:|---:|---:|---:|
| 32 | 50.8 | 28.3 | 51.6 | 51.3 | 0.2 | 1.014 [1.002–1.018] |
| 64 | 58.1 | 51.7 | 59.0 | 59.0 | 0.2 | 1.008 [1.007–1.018] |
| 128 | 63.9 | 59.6 | 64.6 | 64.4 | 0.2 | 1.009 [1.005–1.009] |
| 256 | 64.5 | 64.0 | 64.8 | 64.9 | 0.2 | 1.006 [0.994–1.007] |
| 384 | 64.4 | 64.2 | 64.5 | 64.5 | 0.2 | 1.000 [1.000–1.004] |
| 512 | 64.2 | 62.0 | 64.0 | 64.2 | 0.2 | 0.996 [0.995–1.004] |
| 768 | 66.5 | 65.8 | 66.9 | 67.2 | 0.2 | 1.004 [0.993–1.017] |
| 1024 | 65.7 | 63.4 | 65.7 | 66.3 | 0.2 | 0.998 [0.995–1.003] |
| 1536 | 66.4 | 64.8 | 66.6 | 66.8 | skipped | 1.001 [1.000–1.009] |
| 2048 | 65.3 | 62.2 | 65.5 | 65.6 | skipped | 1.003 [0.984–1.008] |
| 3072 | 64.6 | 64.4 | 66.5 | 66.9 | skipped | 1.028 [1.027–1.031] |

## 16 threads

| n | NumPy, 16 threads | Eigen, native, OpenMP | Eigen in cpp-repl, OpenMP | cpp-repl + compiled OpenMP kernel | REPL / native | NumPy speed-up | Eigen native speed-up |
|---|---:|---:|---:|---:|---:|---:|---:|
| 32 | 28.5 | 52.2 | 51.4 | 51.8 | 0.988 [0.946–0.995] | 1.0× | 1.0× |
| 64 | 52.5 | 125.1 | 124.0 | 123.8 | 0.999 [0.985–1.002] | 1.0× | 2.2× |
| 128 | 274.7 | 122.9 | 123.0 | 123.1 | 1.003 [0.993–1.005] | 4.6× | 1.9× |
| 256 | 282.6 | 268.1 | 266.4 | 267.5 | 0.993 [0.889–1.003] | 4.4× | 4.2× |
| 384 | 354.3 | 396.4 | 395.8 | 396.3 | 1.000 [0.943–1.000] | 5.5× | 6.2× |
| 512 | 449.5 | 443.1 | 439.1 | 442.4 | 0.994 [0.965–0.997] | 7.2× | 6.9× |
| 768 | 573.7 | 565.5 | 560.2 | 565.5 | 0.987 [0.981–1.000] | 8.7× | 8.5× |
| 1024 | 524.1 | 600.4 | 601.4 | 587.2 | 1.002 [0.990–1.019] | 8.3× | 9.1× |
| 1536 | 610.1 | 678.2 | 662.8 | 685.6 | 0.980 [0.958–1.006] | 9.4× | 10.2× |
| 2048 | 607.2 | 660.5 | 659.1 | 660.1 | 0.999 [0.993–1.000] | 9.8× | 10.1× |
| 3072 | 584.1 | 683.6 | 678.6 | 682.9 | 0.978 [0.967–1.004] | 9.1× | 10.6× |
| 4096 | 552.3 | 625.3 | 616.4 | 622.2 | 0.991 [0.950–0.992] | — | — |

## Latency

Cold start, from launch: process start-up, then headers (REPL) or `import numpy`, then compiling the kernel (for native: the whole program, before launch), then the first product at n = 256. Warm session (REPL only): what re-running the compiled kernel, and typing a new Eigen expression (`A.transpose() * B`), cost beyond their own run time.

| arm | start-up | headers / import | kernel compile | first product | warm re-run | new expression |
|---|---:|---:|---:|---:|---:|---:|
| Eigen in cpp-repl | 1.306 s [1.304–1.401] | 0.988 s [0.981–1.142] | 0.507 s [0.501–0.652] | 3.112 s [3.093–3.506] | 0.075 s [0.075–0.079] | 0.707 s [0.705–0.716] |
| Eigen, native | 0.002 s [0.002–0.002] | — | 2.414 s [2.358–2.419] | 2.417 s [2.362–2.423] | — | — |
| NumPy | — | 0.052 s [0.052–0.053] | — | 0.054 s [0.053–0.054] | — | — |
| cpp-repl, no flags | 1.281 s [1.277–1.282] | 0.671 s [0.669–0.672] | 0.186 s [0.185–0.187] | 2.380 s [2.379–2.386] | 0.016 s [0.015–0.016] | 0.306 s [0.304–0.308] |
| cpp-repl + compiled kernel | 1.279 s [1.275–1.281] | 0.669 s [0.669–0.671] | 0.001 s [0.001–0.001] | 2.050 s [2.044–2.053] | 0.015 s [0.015–0.016] | 0.361 s [0.359–0.362] |

## Diagnostics

| n | plain loops, native | plain loops in cpp-repl | cpp-repl without -march=native | Eigen, native, g++ | naive REPL / native | generic / tuned REPL | g++ / clang++ |
|---|---:|---:|---:|---:|---:|---:|---:|
| 32 | 20.9 | 20.9 | 20.3 | 39.6 | 1.003 [0.977–1.007] | 0.393 [0.387–0.399] | 0.779 [0.775–0.785] |
| 64 | 21.1 | 20.6 | 22.4 | 44.0 | 0.979 [0.975–1.000] | 0.381 [0.379–0.382] | 0.751 [0.750–0.760] |
| 128 | 20.5 | 20.9 | 23.8 | 47.6 | 1.019 [1.005–1.020] | 0.365 [0.364–0.373] | 0.741 [0.736–0.748] |
| 256 | 20.9 | 20.4 | 24.1 | 48.1 | 0.980 [0.973–1.060] | 0.373 [0.364–0.375] | 0.740 [0.739–0.749] |
| 384 | 21.2 | 21.2 | 24.0 | 47.5 | 0.999 [0.999–1.001] | 0.372 [0.365–0.374] | 0.735 [0.733–0.745] |
| 512 | 19.7 | 19.1 | 24.1 | 47.8 | 0.967 [0.966–0.971] | 0.375 [0.363–0.378] | 0.746 [0.736–0.755] |
| 768 | 23.2 | 23.3 | 23.7 | 48.7 | 1.007 [0.997–1.064] | 0.354 [0.347–0.357] | 0.732 [0.697–0.741] |
| 1024 | 22.8 | 22.8 | 24.3 | 48.6 | 1.002 [0.994–1.051] | 0.369 [0.369–0.370] | 0.729 [0.722–0.741] |
| 1536 | skipped | skipped | 24.2 | 48.8 | n/a | 0.363 [0.362–0.364] | 0.736 [0.731–0.744] |
| 2048 | skipped | skipped | 23.9 | 48.7 | n/a | 0.366 [0.359–0.374] | 0.745 [0.744–0.747] |
| 3072 | skipped | skipped | 24.4 | 49.3 | n/a | 0.366 [0.362–0.368] | 0.764 [0.759–0.766] |

## Hypotheses

Written down before the first run (bench/README.md). Each is judged on the per-round values: pass when every round meets it, fail when none does, inconclusive otherwise.

|  | hypothesis | verdict | evidence |
|---|---|---|---|
| H0 | the configuration is as intended (every validity check passes) | fail | 979 of 1246 checks passed |
| H1 | Eigen in cpp-repl runs at 0.95–1.05 of native for n ≥ 256 | pass | n=256: 1.006 [0.994–1.007], n=384: 1.000 [1.000–1.004], n=512: 0.996 [0.995–1.004], n=768: 1.004 [0.993–1.017], n=1024: 0.998 [0.995–1.003], n=1536: 1.001 [1.000–1.009], n=2048: 1.003 [0.984–1.008], n=3072: 1.028 [1.027–1.031] |
| H2 | cpp-repl with no flags is at least 20× slower than native at n = 256 | pass | speed ratio 0.003 |
| H3 | cpp-repl calling the compiled kernel runs at 0.97–1.03 of native for n ≥ 128 | inconclusive | n=128: 0.999 [0.997–1.017], n=256: 1.002 [1.000–1.009], n=384: 1.000 [0.992–1.011], n=512: 1.004 [0.991–1.005], n=768: 1.011 [1.004–1.011], n=1024: 1.012 [0.990–1.019], n=1536: 1.006 [1.004–1.006], n=2048: 1.004 [0.994–1.008], n=3072: 1.034 [1.008–1.037] |
| H4 | cpp-repl without -march=native reaches at most 0.6 of cpp-repl with it, for n ≥ 512 | pass | n=512: 0.375 [0.363–0.378], n=768: 0.354 [0.347–0.357], n=1024: 0.369 [0.369–0.370], n=1536: 0.363 [0.362–0.364], n=2048: 0.366 [0.359–0.374], n=3072: 0.366 [0.362–0.368] |
| H5 | NumPy is at least as fast as native Eigen for n ≥ 512, and Eigen is ahead at n ≤ 64 | fail | n ≥ 512: n=512: 0.964 [0.961–0.974], n=768: 0.985 [0.965–0.991], n=1024: 0.954 [0.949–0.966], n=1536: 0.971 [0.966–0.978], n=2048: 0.948 [0.945–0.954], n=3072: 0.995 [0.994–0.997]; n ≤ 64: n=32: 0.558 [0.555–0.564], n=64: 0.884 [0.883–0.894]; NumPy first catches up at n = 384 |
| H6 | with 16 threads, OpenMP in the JIT runs at 0.95–1.05 of native or fails with a recorded reason; the compiled OpenMP kernel runs at 0.95–1.05 | inconclusive | JIT: n=32: 0.988 [0.946–0.995], n=64: 0.999 [0.985–1.002], n=128: 1.003 [0.993–1.005], n=256: 0.993 [0.889–1.003], n=384: 1.000 [0.943–1.000], n=512: 0.994 [0.965–0.997], n=768: 0.987 [0.981–1.000], n=1024: 1.002 [0.990–1.019], n=1536: 0.980 [0.958–1.006], n=2048: 0.999 [0.993–1.000], n=3072: 0.978 [0.967–1.004], n=4096: 0.991 [0.950–0.992]; compiled kernel: n=32: 0.994 [0.974–1.000], n=64: 0.995 [0.981–1.008], n=128: 0.993 [0.991–1.008], n=256: 0.996 [0.993–1.002], n=384: 1.000 [1.000–1.000], n=512: 1.002 [0.992–1.002], n=768: 1.001 [0.993–1.001], n=1024: 1.004 [0.966–1.006], n=1536: 1.013 [0.997–1.016], n=2048: 1.001 [0.993–1.002], n=3072: 0.998 [0.981–1.005], n=4096: 0.995 [0.985–1.009] |
| H7 | in a warm session, re-running the compiled kernel costs under 0.2 s beyond the run itself | pass | overhead 0.075 s [0.075–0.079] over 3 rounds |

## Failed checks

- checksum: `naive-native` n=32 round 1: relative error 5.089511266839421e-10
- checksum: `naive-native` n=64 round 1: relative error 1.214206943578934e-10
- checksum: `naive-native` n=128 round 1: relative error 8.37287879704813e-10
- checksum: `naive-native` n=384 round 1: relative error 1.542836862752848e-09
- checksum: `naive-native` n=512 round 1: relative error 6.953231732521429e-10
- checksum: `naive-native` n=768 round 1: relative error 8.383028698948872e-10
- checksum: `eigen-repl-default` n=32 round 1: relative error 5.089511266839421e-10
- checksum: `eigen-repl-default` n=64 round 1: relative error 1.214206943578934e-10
- checksum: `eigen-repl-default` n=128 round 1: relative error 8.37287879704813e-10
- checksum: `eigen-repl-default` n=384 round 1: relative error 1.542836862752848e-09
- checksum: `eigen-repl-default` n=512 round 1: relative error 6.953231732521429e-10
- checksum: `eigen-repl-default` n=768 round 1: relative error 8.383028698948872e-10
- checksum: `eigen-repl-aot` n=32 round 1: relative error 5.089511266839421e-10
- checksum: `eigen-repl-aot` n=64 round 1: relative error 1.214206943578934e-10
- checksum: `eigen-repl-aot` n=128 round 1: relative error 8.37287879704813e-10
- checksum: `eigen-repl-aot` n=384 round 1: relative error 1.542836862752848e-09
- checksum: `eigen-repl-aot` n=512 round 1: relative error 6.953231732521429e-10
- checksum: `eigen-repl-aot` n=768 round 1: relative error 8.383028698948872e-10
- checksum: `eigen-repl-aot` n=1536 round 1: relative error 1.4254689547779044e-10
- checksum: `eigen-repl-aot` n=2048 round 1: relative error 2.1017044232328697e-09
- checksum: `eigen-repl-aot` n=3072 round 1: relative error 6.93608623468494e-10
- checksum: `naive-repl` n=32 round 1: relative error 5.089511266839421e-10
- checksum: `naive-repl` n=64 round 1: relative error 1.214206943578934e-10
- checksum: `naive-repl` n=128 round 1: relative error 8.37287879704813e-10
- checksum: `naive-repl` n=384 round 1: relative error 1.542836862752848e-09
- checksum: `naive-repl` n=512 round 1: relative error 6.953231732521429e-10
- checksum: `naive-repl` n=768 round 1: relative error 8.383028698948872e-10
- checksum: `eigen-native` n=32 round 1: relative error 5.089511266839421e-10
- checksum: `eigen-native` n=64 round 1: relative error 1.214206943578934e-10
- checksum: `eigen-native` n=128 round 1: relative error 8.37287879704813e-10
- checksum: `eigen-native` n=384 round 1: relative error 1.542836862752848e-09
- checksum: `eigen-native` n=512 round 1: relative error 6.953231732521429e-10
- checksum: `eigen-native` n=768 round 1: relative error 8.383028698948872e-10
- checksum: `eigen-native` n=1536 round 1: relative error 1.4254689547779044e-10
- checksum: `eigen-native` n=2048 round 1: relative error 2.1017044232328697e-09
- checksum: `eigen-native` n=3072 round 1: relative error 6.93608623468494e-10
- checksum: `eigen-repl-generic` n=32 round 1: relative error 5.089511266839421e-10
- checksum: `eigen-repl-generic` n=64 round 1: relative error 1.214206943578934e-10
- checksum: `eigen-repl-generic` n=128 round 1: relative error 8.37287879704813e-10
- checksum: `eigen-repl-generic` n=384 round 1: relative error 1.542836862752848e-09

## Environment

|  |  |
|---|---|
| CPU | AMD Ryzen 9 5950X 16-Core Processor: 16 cores, 32 threads, 16 FLOP/cycle/core, max 5086 MHz |
| mean clock while timing | 1 thread 4702 MHz |
|  | 16 threads 3445 MHz |
| temperature | 64–91 °C |
| clang++ | Ubuntu clang version 23.1.2 (++20260919103626+4b1925210476-1~exp1~20260919223755.77) |
| clang-repl | Ubuntu LLVM version 23.1.2 |
| g++ | g++-13 (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0 |
| Eigen | 3.4.0 (/usr/include/eigen3) |
| NumPy | 2.5.4 with openblas 0.3.34.106.0 (Haswell) |
| huge pages | {'enabled': 'madvise', 'defrag': 'madvise'} |
| frequency policy | amd-pstate-epp, governor performance, preference performance, boost 1 |
| load average at start | 2.04, 1.23, 0.85 |
| container | sha256:1ee41fb6500cb013ce20e94565378066ae32d1f8eb1419a22a366b377db72140 |

