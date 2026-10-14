# Goals — cpp_repl

A C++ REPL on top of LLVM's clang-repl, for any library or project: whole-block
input, printed values, crash recovery from a journal, `.cpp` files in and out,
profiles for libraries, and Docker images with the newest clang-repl. Forked
from `openusd-tutorial/repl/usd-repl`.

## Goals

- [x] Port usd-repl into the `cpp_repl` package, behaviour unchanged (parity: 20 of 20 OpenUSD lesson examples identical)
- [x] Fix the `.cpp` conversion bugs: in-block `#pragma`, file-scope `#if` regions, a final `return f();`
- [x] Core prelude with `Show<T>`/`Put<T>`; `usd` and `eigen` profiles
- [x] Configuration: compiler-style flags, `cpp-repl.toml`, `compile_commands.json`
- [x] Backend calibration, fake-backend tests, integration tests and golden sessions
- [x] Compat probes and the "Why a front end" table for clang-repl 18.1.3, 23.1.2 and the 24 snapshot
- [x] Run control flow and lambda statements inside functions (clang-repl 23 crashes on top-level lambdas)
- [x] Docker images: clang-repl 23 from apt.llvm.org, the snapshot, the bench image; `cpp-repl --docker`
- [x] `%time`, `%timeit`, `%flags`; Tab completion from every included header
- [x] Benchmark: NumPy vs Eigen in the REPL vs native Eigen, results in `bench/README.md`
- [x] README with a demo, install, profiles, the compat table and the benchmark summary
- [ ] Build and test the `src` image (written, not built: 30 to 60 minutes of all cores)
- [ ] Report the clang-repl 23 top-level lambda crash upstream, unless it is reported already
- [ ] Later: precompiled headers for startup, a Jupyter kernel, PyPI, CI
