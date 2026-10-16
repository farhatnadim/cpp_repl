# syntax=docker/dockerfile:1
#
# Toolchain images for cpp-repl. The cpp-repl code is never copied in: it is
# mounted at run time (`cpp-repl --docker`), so an image only changes when the
# toolchain does.
#
#   base      ubuntu:24.04 with Python, the UI packages, libstdc++ 13, Eigen 3.4,
#             glm, spdlog and CMake
#   apt      + clang, clang-repl and libc++ from apt.llvm.org (LLVM_MAJOR, default 23)
#   bench     + libomp, g++-13, a venv with NumPy (OpenBLAS wheel), threadpoolctl,
#             matplotlib, and Eigen 5.0.1 in /opt for the tagged benchmark run
#   snapshot  + clang and clang-repl from apt.llvm.org's development snapshot
#   src       clang and clang-repl built from llvm-project at LLVM_REF
#
# Build from this directory (it is the whole context; nothing is copied):
#
#   docker build --target apt      -t cpp-repl:llvm23        docker/
#   docker build --target bench    -t cpp-repl:bench-llvm23  docker/
#   docker build --target snapshot -t cpp-repl:llvm-snapshot docker/
#   docker build --target src      -t cpp-repl:llvm-src-llvmorg-23.1.3 docker/
#
# Every instruction works with the legacy builder as well as with BuildKit.
# The legacy builder (what `docker build` uses when buildx is not installed)
# builds every stage that comes before --target, so the stages are ordered from
# cheapest to most expensive: apt and bench never pay for snapshot or src.
#
# The base is the same Ubuntu release as the host it was made on, so libraries
# built on the host (an OpenUSD install, say) load inside the container when
# mounted at their own path.

ARG UBUNTU=24.04

# ---------------------------------------------------------------------------
FROM ubuntu:${UBUNTU} AS base

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1

# libstdc++-13-dev and libgcc-13-dev are the C++ library clang compiles
# against; libpython3.12t64 is what host-built libraries linking Python need.
# Eigen, glm and spdlog are the libraries the tests load (the eigen and glm
# profiles, the add-library probe, tutorial 03), and cmake resolves the
# probe's CMake packages and builds tutorial 03's project.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      cmake \
      pkg-config \
      python3 \
      python3-prompt-toolkit \
      python3-pygments \
      python3-pytest \
      libstdc++-13-dev \
      libgcc-13-dev \
      libc6-dev \
      libeigen3-dev \
      libglm-dev \
      libspdlog-dev \
      libpython3.12t64 \
      libpython3.12-dev \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
FROM base AS apt

ARG LLVM_MAJOR=23

# apt.llvm.org's signing key, read by apt through signed-by. The key is ASCII
# armoured, which apt accepts from a file ending in .asc.
ADD https://apt.llvm.org/llvm-snapshot.gpg.key /etc/apt/keyrings/apt.llvm.org.asc

# clang (the driver, for native builds and -print-file-name), clang-tools
# (clang-repl), and libc++ for `cpp-repl -stdlib=libc++`, whose C++23 library
# (std::print, std::expected, std::mdspan) is ahead of libstdc++ 13's. clang-N
# pulls in libstdc++-13-dev, libgcc-13-dev and binutils, so clang selects GCC
# 13's installation by default.
RUN chmod 0644 /etc/apt/keyrings/apt.llvm.org.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/apt.llvm.org.asc] https://apt.llvm.org/noble/ llvm-toolchain-noble-${LLVM_MAJOR} main" \
      > /etc/apt/sources.list.d/apt.llvm.org.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      "clang-${LLVM_MAJOR}" \
      "clang-tools-${LLVM_MAJOR}" \
      "libc++-${LLVM_MAJOR}-dev" \
      "libc++abi-${LLVM_MAJOR}-dev" \
 && dpkg-query -W -f='${Package} ${Version}\n' "clang-tools-${LLVM_MAJOR}" > /etc/cpp-repl-llvm-version \
 && "clang-repl-${LLVM_MAJOR}" --version >> /etc/cpp-repl-llvm-version \
 && rm -rf /var/lib/apt/lists/*

# clang, clang++ and clang-repl resolve to this LLVM without a version suffix.
ENV PATH=/usr/lib/llvm-${LLVM_MAJOR}/bin:${PATH}

ARG DOCKERFILE_SHA=unknown
LABEL org.opencontainers.image.title="cpp-repl" \
      org.opencontainers.image.description="clang-repl ${LLVM_MAJOR} from apt.llvm.org for cpp-repl" \
      org.opencontainers.image.licenses="MIT" \
      io.cpp-repl.target="apt" \
      io.cpp-repl.llvm-major="${LLVM_MAJOR}" \
      io.cpp-repl.dockerfile-sha256="${DOCKERFILE_SHA}"

# ---------------------------------------------------------------------------
FROM apt AS bench

ARG LLVM_MAJOR=23
ARG EIGEN5_VERSION=5.0.1
ARG EIGEN5_SHA256=e9c326dc8c05cd1e044c71f30f1b2e34a6161a3b6ecf445d56b53ff1669e3dec

# libomp for -fopenmp with clang (native builds and the JIT), g++-13 for the
# GCC arm, python3-venv for the NumPy venv, util-linux for taskset.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      "libomp-${LLVM_MAJOR}-dev" \
      g++-13 \
      python3-venv \
      util-linux \
 && rm -rf /var/lib/apt/lists/*

# NumPy from PyPI: the wheel bundles OpenBLAS. Ubuntu's python3-numpy would
# use the reference BLAS. --system-site-packages keeps prompt_toolkit,
# Pygments and pytest from the base image visible.
RUN python3 -m venv --system-site-packages /opt/bench-venv \
 && /opt/bench-venv/bin/pip install --no-cache-dir --disable-pip-version-check \
      numpy threadpoolctl matplotlib \
 && /opt/bench-venv/bin/python -c "import numpy, threadpoolctl; print('numpy', numpy.__version__)"

# Eigen 5.0.1, header-only, for the tagged run. Eigen 3.4.0 stays the default
# at /usr/include/eigen3. Eigen 5 names itself in EIGEN_VERSION_STRING; its
# EIGEN_WORLD/MAJOR/MINOR_VERSION macros read 3.5.0.
ADD https://gitlab.com/libeigen/eigen/-/archive/${EIGEN5_VERSION}/eigen-${EIGEN5_VERSION}.tar.gz /tmp/eigen.tar.gz
RUN echo "${EIGEN5_SHA256}  /tmp/eigen.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/eigen.tar.gz -C /opt \
 && rm /tmp/eigen.tar.gz \
 && test -f "/opt/eigen-${EIGEN5_VERSION}/Eigen/Dense"

ENV PATH=/opt/bench-venv/bin:${PATH} \
    EIGEN5_ROOT=/opt/eigen-${EIGEN5_VERSION}

ARG DOCKERFILE_SHA=unknown
LABEL org.opencontainers.image.description="cpp-repl benchmark image: clang-repl ${LLVM_MAJOR}, libomp, NumPy with OpenBLAS, Eigen 3.4 and ${EIGEN5_VERSION}" \
      io.cpp-repl.target="bench" \
      io.cpp-repl.dockerfile-sha256="${DOCKERFILE_SHA}"

# ---------------------------------------------------------------------------
FROM base AS snapshot

# The development snapshot (LLVM main) lives in the unversioned suite. Its
# packages are still named by major version, so the newest clang-tools-N in
# that suite is the snapshot, and /usr/lib/llvm-snapshot points at it.
ADD https://apt.llvm.org/llvm-snapshot.gpg.key /etc/apt/keyrings/apt.llvm.org.asc

RUN chmod 0644 /etc/apt/keyrings/apt.llvm.org.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/apt.llvm.org.asc] https://apt.llvm.org/noble/ llvm-toolchain-noble main" \
      > /etc/apt/sources.list.d/apt.llvm.org.list \
 && apt-get update \
 && major="$(apt-cache madison $(apt-cache pkgnames clang-tools- | grep -E '^clang-tools-[0-9]+$') \
              | awk '$5 ~ /apt\.llvm\.org/ && $6 ~ /^llvm-toolchain-noble\// {sub("clang-tools-", "", $1); print $1}' \
              | sort -n | tail -1)" \
 && test -n "${major}" \
 && apt-get install -y --no-install-recommends "clang-${major}" "clang-tools-${major}" \
      "libc++-${major}-dev" "libc++abi-${major}-dev" \
 && ln -s "/usr/lib/llvm-${major}" /usr/lib/llvm-snapshot \
 && dpkg-query -W -f='${Package} ${Version}\n' "clang-tools-${major}" > /etc/cpp-repl-llvm-version \
 && "clang-repl-${major}" --version >> /etc/cpp-repl-llvm-version \
 && rm -rf /var/lib/apt/lists/*

ENV PATH=/usr/lib/llvm-snapshot/bin:${PATH}

ARG DOCKERFILE_SHA=unknown
LABEL org.opencontainers.image.title="cpp-repl" \
      org.opencontainers.image.description="clang-repl from apt.llvm.org's development snapshot for cpp-repl" \
      org.opencontainers.image.licenses="MIT" \
      io.cpp-repl.target="snapshot" \
      io.cpp-repl.dockerfile-sha256="${DOCKERFILE_SHA}"

# ---------------------------------------------------------------------------
FROM base AS src-build

ARG LLVM_REF=llvmorg-23.1.3
ARG LLVM_REPO=https://github.com/llvm/llvm-project.git

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git cmake ninja-build g++-13 lld libedit-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

# Any tag, branch or commit: one shallow fetch of that ref.
WORKDIR /src/llvm-project
RUN git init -q . \
 && git remote add origin "${LLVM_REPO}" \
 && git fetch -q --depth 1 origin "${LLVM_REF}" \
 && git checkout -q FETCH_HEAD \
 && git log -1 --format='%H %cd' > /src/llvm-ref

# clang only, for the host target, Release, linked with lld. Linking is what
# runs out of memory first, hence two parallel link jobs. libedit gives
# clang-repl line editing; nothing else optional is wanted.
RUN cmake -G Ninja -S llvm -B /src/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_COMPILER=gcc-13 \
      -DCMAKE_CXX_COMPILER=g++-13 \
      -DCMAKE_INSTALL_PREFIX=/opt/llvm \
      -DLLVM_ENABLE_PROJECTS=clang \
      -DLLVM_TARGETS_TO_BUILD=host \
      -DLLVM_USE_LINKER=lld \
      -DLLVM_PARALLEL_LINK_JOBS=2 \
      -DLLVM_ENABLE_ASSERTIONS=OFF \
      -DLLVM_ENABLE_LIBEDIT=ON \
      -DLLVM_ENABLE_LIBXML2=OFF \
      -DLLVM_ENABLE_ZSTD=OFF \
      -DLLVM_INCLUDE_TESTS=OFF \
      -DLLVM_INCLUDE_BENCHMARKS=OFF \
      -DLLVM_INCLUDE_EXAMPLES=OFF \
      -DLLVM_INCLUDE_DOCS=OFF \
      -DCLANG_INCLUDE_TESTS=OFF \
      -DCLANG_INCLUDE_DOCS=OFF \
      -DCLANG_ENABLE_STATIC_ANALYZER=OFF \
      -DCLANG_ENABLE_ARCMT=OFF \
 && cmake --build /src/build --target clang clang-repl \
 && cmake --install /src/build --strip --component clang \
 && cmake --install /src/build --strip --component clang-repl \
 && cmake --install /src/build --component clang-resource-headers \
 && for link in clang++ clang-cpp; do \
      [ -e "/opt/llvm/bin/${link}" ] || ln -s clang "/opt/llvm/bin/${link}"; \
    done \
 && /opt/llvm/bin/clang-repl --version

# ---------------------------------------------------------------------------
FROM base AS src

ARG LLVM_REF=llvmorg-23.1.3

RUN apt-get update \
 && apt-get install -y --no-install-recommends binutils libedit2 zlib1g \
 && rm -rf /var/lib/apt/lists/*

COPY --from=src-build /opt/llvm /opt/llvm
COPY --from=src-build /src/llvm-ref /etc/cpp-repl-llvm-ref

RUN { echo "${LLVM_REF} $(cat /etc/cpp-repl-llvm-ref)"; /opt/llvm/bin/clang-repl --version; } > /etc/cpp-repl-llvm-version

ENV PATH=/opt/llvm/bin:${PATH}

ARG DOCKERFILE_SHA=unknown
LABEL org.opencontainers.image.title="cpp-repl" \
      org.opencontainers.image.description="clang-repl built from llvm-project ${LLVM_REF} for cpp-repl" \
      org.opencontainers.image.licenses="MIT" \
      io.cpp-repl.target="src" \
      io.cpp-repl.llvm-ref="${LLVM_REF}" \
      io.cpp-repl.dockerfile-sha256="${DOCKERFILE_SHA}"
