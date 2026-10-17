#!/usr/bin/env bash
# bench/docker.sh: run bench/run.py inside the bench image, where clang-repl
# and clang++ come from the same LLVM and NumPy is the pip wheel with OpenBLAS.
#
#   bench/docker.sh --quick            about 3 minutes
#   bench/docker.sh                    the full protocol, about 30 minutes
#   bench/docker.sh --arms core --dry-run
#
# The checkout is mounted read-only at its own path, so paths in the results
# match the host's, with bench/build and bench/results mounted writable on
# top. bench/run.py picks clang++ to match the image's clang-repl and finds
# libomp through it; every argument here goes to bench/run.py. Set
# BENCH_IMAGE to use another image.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(dirname "$here")
image=${BENCH_IMAGE:-cpp-repl:bench-llvm23}

if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "bench/docker.sh: no image $image; run make docker-bench first" >&2
  exit 2
fi
image_id=$(docker image inspect --format '{{.Id}}' "$image")
mkdir -p "$repo/bench/build" "$repo/bench/results"

tty=()
if [ -t 0 ] && [ -t 1 ]; then
  tty=(-t)
fi

exec docker run --rm --init -i "${tty[@]}" \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp/bench-home \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e BENCH_IMAGE_ID="$image_id" \
  -e BENCH_HOST="$(hostname)" \
  -v "$repo:$repo:ro" \
  -v "$repo/bench/build:$repo/bench/build" \
  -v "$repo/bench/results:$repo/bench/results" \
  -w "$repo" \
  "$image" \
  bash -c 'python=/opt/bench-venv/bin/python3
           gxx=$(command -v g++ || command -v g++-13 || true)
           exec "$python" bench/run.py --python "$python" ${gxx:+--gxx "$gxx"} "$@"' bench-docker "$@"
