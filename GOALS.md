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
- [x] Compat probes and the "Why a front end" table for clang-repl 18.1.3, 23.1.4 and the 24 snapshot
- [x] Run control flow and lambda statements inside functions (clang-repl 23 crashes on top-level lambdas)
- [x] Docker images: clang-repl 23 from apt.llvm.org, the snapshot, the bench image; `cpp-repl --docker`
- [x] `%time`, `%timeit`, `%flags`; Tab completion from every included header
- [x] Benchmark: NumPy vs Eigen in the REPL vs native Eigen, results in `bench/README.md`
- [x] README with a demo, install, profiles, the compat table and the benchmark summary
- [x] Claude Code plugin: `add-library`, `add-printer` and `contribute-profile` skills, with probe and check scripts, tests and evals
- [x] `glm` profile, made by `contribute-profile` as the worked example of a contributed profile
- [x] openusd-tutorial runs on cpp-repl: its `repl/usd-repl` is a launcher over the usd profile, which its CMake build fetches at a pinned commit
- [ ] Run the eval suite under `claude plugin eval` (its sandbox needs user namespaces, which AppArmor blocks on this host; `evals/headless.py` runs the cases meanwhile; see `evals/README.md`)
- [ ] Build and test the `src` image (written, not built: 30 to 60 minutes of all cores)
- [ ] Report the clang-repl 23 top-level lambda crash upstream, unless it is reported already
- [x] clang-repl 23 and newer only; the usd profile runs there (a patched copy of TBB 2020's headers, the libraries loaded as clang-repl starts)
- [x] Precompiled headers: the preludes precompiled once per configuration (`pch.py`), so that on clang-repl 23 a session starts in 0.5 s where it took 2 s, and in 1 s with OpenUSD where it took 5 s
- [x] Tutorials in `docs/tutorials/`: a first session, programs, libraries, scripts and agents, the Claude Code plugin; every transcript checked against a run (`scripts/check_tutorial.py`, `make tutorials`)
- [x] `run-cpp` skill: an agent runs C++ without a build and without the prompt (inputs piped in, `--batch`, `--json`, `--serve`/`--connect`), with two eval cases
- [x] The plugin installs from GitHub: `claude-plugin` fast-forwarded into `main` (2026-10-16)
- [x] `evals/headless.py`: the eval cases with `claude -p`, where `claude plugin eval`'s sandbox cannot run
- [ ] Later: a Jupyter kernel, PyPI, CI
