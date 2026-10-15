# cpp-repl:llvm23 plus the packages this project's cpp-repl profiles read from /usr.
# probe_library.py image --config .cpp-repl/<lib>.toml writes the package list.
# Build it before first use, then pass the tag to cpp-repl:
#   docker build -t cpp-repl-<project>:llvm23 .cpp-repl
#   cpp-repl --docker --image cpp-repl-<project>:llvm23 -c .cpp-repl/<lib>.toml
FROM cpp-repl:llvm23
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      lib<name>-dev \
 && rm -rf /var/lib/apt/lists/*
