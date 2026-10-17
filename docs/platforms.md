# Where cpp-repl runs, and the first test of a release

cpp-repl runs on Linux: `pch.py` locks with `fcntl`, `preload.py` turns core
dumps off with `prctl` and loads libraries through `LD_PRELOAD`, and
`config.py` reads `ldconfig`'s cache and ELF headers. The other platforms
reach it through a Linux container or a Linux VM.

| Platform | Route | What to expect |
| --- | --- | --- |
| Ubuntu 24.04 | native: `clang-23` and `clang-tools-23` from apt.llvm.org, the release's wheel with pip | every step passes |
| Ubuntu 22.04 | native; its Python is 3.10, so the wheel needs a newer one (`uv python install 3.12`, or deadsnakes), and apt.llvm.org's suite is `llvm-toolchain-jammy-23` | passes with that Python |
| Windows 11 | WSL2 with Ubuntu: as Ubuntu above. Docker Desktop with the WSL2 backend: `cpp-repl --docker` from the WSL2 shell | passes |
| Windows, native | no route: `fcntl`, `prctl`, `LD_PRELOAD` and Unix sockets have no counterpart there | `cpp-repl --version` fails on import |
| macOS, Apple silicon or Intel | Docker Desktop: `cpp-repl --docker` builds `cpp-repl:llvm23` for the machine's architecture (apt.llvm.org has arm64 packages) | passes, to be confirmed |
| macOS, native | Homebrew's `llvm` carries clang-repl, but the Linux-only pieces above stop the session | the smoke test shows where; a port is listed below |

## The smoke test

[`scripts/smoke.sh`](../scripts/smoke.sh) installs the newest release's
wheel, or the wheel given as its argument, into a fresh virtual environment
and runs what a C++ developer does first: the version, a session, a program
with `--batch`, Eigen when its headers are there, the Docker command, and a
session in the image when Docker and the image are there. Each step prints
ok, FAIL or skipped, and the summary is what to paste into the release's
issue, one comment per machine.

Ubuntu, and Windows in a WSL2 shell (noble; jammy on 22.04):

```sh
curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | sudo tee /etc/apt/keyrings/apt.llvm.org.asc > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/apt.llvm.org.asc] https://apt.llvm.org/noble/ llvm-toolchain-noble-23 main" | sudo tee /etc/apt/sources.list.d/apt.llvm.org.list
sudo apt update && sudo apt install clang-23 clang-tools-23 libeigen3-dev
curl -fsSLO https://raw.githubusercontent.com/farhatnadim/cpp_repl/main/scripts/smoke.sh && bash smoke.sh
```

macOS, with Docker Desktop running:

```sh
curl -fsSLO https://raw.githubusercontent.com/farhatnadim/cpp_repl/main/scripts/smoke.sh && bash smoke.sh
pip3 install WHEEL_URL && cpp-repl --docker     # builds the image when asked; then bash smoke.sh again for the docker step
```

Windows, native, for the record (PowerShell):

```
py -3.12 -m pip install WHEEL_URL
cpp-repl --version
```

`WHEEL_URL` is the README's install line. The smoke test fetches it itself
when run without an argument.

## What a macOS port needs

- `preload.py`: `prctl` is Linux; macOS turns core dumps off with
  `setrlimit(RLIMIT_CORE, 0)`, and the shim would load through
  `DYLD_INSERT_LIBRARIES` in place of `LD_PRELOAD`, or the libraries go to
  clang-repl as `%lib` lines.
- `config.py`: `ldconfig -p` has no counterpart; Homebrew's and Xcode's
  library directories and `otool -L` take its place, the ELF checks become
  Mach-O checks, and `find_clang_repl` looks in `/opt/homebrew/opt/llvm/bin`
  and `/usr/local/opt/llvm/bin`.
- The precompiled preludes: clang-repl with `-fincremental-extensions` on
  macOS, to be tried.
- `-stdlib=libc++` is the only standard library there.

Windows stays WSL2.
