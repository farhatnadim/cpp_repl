# Docker images

`Dockerfile` holds the toolchain images cpp-repl runs in. The images contain no
cpp-repl code: `cpp-repl --docker` mounts the checkout at its own path,
read-only unless it is the working directory, so an image only changes when
the toolchain does.

| Target | Tag | What it adds |
| --- | --- | --- |
| `base` | — | ubuntu:24.04, Python 3.12, prompt_toolkit, Pygments, pytest, libstdc++ 13, Eigen 3.4.0, glm 0.9.9.8, spdlog 1.12, CMake 3.28, libpython3.12 |
| `apt` | `cpp-repl:llvm23`, `cpp-repl:llvm23.1.4` | `clang-23` and `clang-tools-23` (clang-repl) from apt.llvm.org, with `libc++-23-dev` and `libc++abi-23-dev` for `-stdlib=libc++` |
| `bench` | `cpp-repl:bench-llvm23` | `apt` plus libomp, g++-13, a venv at `/opt/bench-venv` with NumPy (OpenBLAS wheel), threadpoolctl and matplotlib, and Eigen 5.0.1 at `$EIGEN5_ROOT` (`/opt/eigen-5.0.1`) |
| `snapshot` | `cpp-repl:llvm-snapshot` | clang and clang-repl from apt.llvm.org's development snapshot (LLVM main; 24 in October 2026), with its libc++ |
| `src` | `cpp-repl:llvm-src-<ref>` | clang and clang-repl built from llvm-project at `LLVM_REF` (default `llvmorg-23.1.3`); no libc++ |

In every image `clang`, `clang++` and `clang-repl` resolve without a version
suffix. `/etc/cpp-repl-llvm-version` records the package version (or the git
ref) and `clang-repl --version`.

## Building

The directory is the whole build context; nothing is copied into the images.

```sh
SHA=$(sha256sum docker/Dockerfile | cut -d' ' -f1)
docker build --target apt      --build-arg DOCKERFILE_SHA=$SHA -t cpp-repl:llvm23        docker/
docker build --target bench    --build-arg DOCKERFILE_SHA=$SHA -t cpp-repl:bench-llvm23  docker/
docker build --target snapshot --build-arg DOCKERFILE_SHA=$SHA -t cpp-repl:llvm-snapshot docker/
docker build --target src      --build-arg DOCKERFILE_SHA=$SHA --build-arg LLVM_REF=llvmorg-23.1.3 \
             -t cpp-repl:llvm-src-llvmorg-23.1.3 docker/
```

`make docker`, `make docker-bench`, `make docker-snapshot` and `make docker-src`
run the same commands. `DOCKERFILE_SHA` lands in the label
`io.cpp-repl.dockerfile-sha256`, so `cpp-repl --docker` can tell an image built
from an older Dockerfile. `LLVM_MAJOR` (default 23; the Makefile calls it `LLVM`) picks another
apt.llvm.org release for `apt` and `bench`: 23 or newer, since cpp-repl refuses an older
clang-repl.

Every instruction works with the legacy builder, which `docker build` uses when
buildx is not installed. The legacy builder runs every stage that precedes
`--target`, so the stages go from cheapest to most expensive: `apt` and
`bench` never build `snapshot` or `src`. With buildx installed
(`sudo apt install docker-buildx`), only the stages a target needs are built.

## Times and sizes

On a Ryzen 9 5950X with a fast connection:

| Target | Build | Size |
| --- | --- | --- |
| `apt` | under a minute | 711 MB |
| `bench` | a few minutes | 1.03 GB |
| `snapshot` | under a minute | 702 MB |
| `src` | an estimate: 30–60 minutes of all cores, about 25 GB of build cache; not built yet | — |

## Notes

- The base is the host's Ubuntu release (24.04). Libraries built on the host,
  such as an OpenUSD install linking `libpython3.12` and libstdc++, load in the
  container when mounted at their own path.
- clang picks GCC 13's installation (`Selected GCC installation:
  /usr/lib/gcc/x86_64-linux-gnu/13`) in `apt` and `snapshot`, since `clang-N`
  depends on `libstdc++-13-dev`.
- The bench venv uses NumPy from PyPI because Ubuntu's `python3-numpy` uses
  the reference BLAS. `threadpoolctl` reports OpenBLAS with the `Haswell`
  kernels on Zen 3.
- Eigen 5 names itself in `EIGEN_VERSION_STRING` ("5.0.1"). Its
  `EIGEN_WORLD/MAJOR/MINOR_VERSION` macros read 3.5.0.
