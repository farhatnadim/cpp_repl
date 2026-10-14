# AGENTS.md

Guidance for AI agents working in this repository. The workspace-level
`../AGENTS.md` still applies; this file adds what is specific to cpp_repl.

## What this project is

`cpp-repl` is a C++ REPL: a Python front end over LLVM's `clang-repl`. It reads
whole blocks, rewrites the shapes clang-repl gets wrong, prints values, keeps a
journal so that a crash or Ctrl-C costs nothing, and converts between `.cpp`
files and sessions. Profiles (`usd`, `eigen`) and project configuration
(`cpp-repl.toml`, `compile_commands.json`) point it at a library. Docker images
carry the newest clang-repl.

It began as a fork of `openusd-tutorial/repl/usd-repl` on 2026-10-13: commit
2c41aad plus that tree's uncommitted `_source()` change. usd-repl lives on in
openusd-tutorial. A fix to the shared lexer, sources or backend is worth porting
across, deliberately, in either direction.

## Who writes the code

The workspace rule applies: the user writes the code, agents assist. On
2026-10-13 the user granted Claude the v0.1 implementation: scaffold, front end,
profiles, Docker images, tests and benchmark. The grant covered v0.1. Later
features follow the workspace rule unless the user extends it. Fixing a bug
found in review stays in scope.

## Layout

| Path | What it holds |
| --- | --- |
| `src/cpp_repl/lexer.py` | C++ tokens, statement boundaries, when an input is complete, what to send |
| `src/cpp_repl/sources.py` | `.cpp` and `.repl` files as inputs |
| `src/cpp_repl/config.py` | flags, profiles, `cpp-repl.toml`, `compile_commands.json`, the clang-repl binary |
| `src/cpp_repl/backend.py` | one clang-repl process and the end-of-output marker protocol |
| `src/cpp_repl/session.py` | the journal, recovery, commands, `%save-cpp` |
| `src/cpp_repl/ui.py`, `painter.py` | prompt_toolkit or readline input; colour |
| `src/cpp_repl/prelude/` | `cpp_repl.h`, the C++ every session starts from |
| `src/cpp_repl/profiles/` | one Python module and one header per library |
| `docker/Dockerfile` | images with clang-repl from apt.llvm.org, or built from source |
| `tests/` | unit tests, a fake clang-repl, golden sessions, compat probes |
| `bench/` | NumPy vs Eigen in the REPL vs native Eigen |

## Commands

```
make test           # unit and fake-backend tests; no clang-repl needed
make test-host      # everything, against the host's clang-repl
make parity         # the OpenUSD lessons through usd-repl and cpp-repl -p usd, diffed
make compat         # what raw clang-repl does with the inputs cpp-repl rewrites
make docker         # build cpp-repl:llvm23
make test-docker    # the test suite inside the image
make bench-quick    # a 3-minute benchmark run in the bench image
```

## Probing clang-repl directly

- Point `HOME` and `TMPDIR` at a scratch directory, and start the process with
  `RLIMIT_CORE=0` or send `prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);` first.
  Otherwise clang-repl writes `~/.clang-repl-history`, and on Ubuntu apport
  files a crash report of about 95 MB in `/var/crash` with a popup.
- Give every read a deadline. A line ending in an unclosed `{` makes
  clang-repl loop forever.
- Other sessions on this machine use clang-repl too. Never delete
  `~/.clang-repl-history`, or a clang-repl report in `/var/crash` that this
  project did not cause.

## Writing

Comments and docstrings are plain sentences that say what the code does.
State what was done in the affirmative, in few words.
