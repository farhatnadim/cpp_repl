#!/usr/bin/env bash
# The first test of a cpp-repl release on a machine: the wheel installed into
# a fresh virtual environment, then what a C++ developer does first. Each
# step prints ok, FAIL or skipped with a reason, every step runs even after
# a failure, and the summary at the end is what to paste into the release's
# issue (docs/platforms.md says which route each platform takes).
#
#   scripts/smoke.sh                # the newest GitHub release's wheel
#   scripts/smoke.sh WHEEL          # a wheel file, or its URL
#
# Needs python3, 3.11 or newer. The session steps need a clang-repl 23 or
# newer on PATH or in /usr/lib/llvm-N/bin and are skipped without one; the
# Docker step runs when docker and the image cpp-repl:llvm23 are there.
set -u

repo=https://github.com/farhatnadim/cpp_repl
wheel=${1:-}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
results=()
failed=0
inputs='std::vector<int> v{3, 1, 2};\nstd::ranges::sort(v);\nv\n'

say() { printf '%s\n' "$*"; }

# record STATUS NAME DETAIL
record() {
  local detail=${3:0:100}
  results+=("$(printf '%-8s %-11s %s' "$1" "$2" "$detail")")
  [ "$1" = FAIL ] && failed=1
  say "  $1: $2${detail:+ ($detail)}"
}

# check NAME EXPECTED COMMAND: ok when the command's output holds EXPECTED
check() {
  local name=$1 expected=$2 command=$3 out status
  out=$(bash -c "$command" 2>&1 < /dev/null)
  status=$?
  if [[ "$out" == *"$expected"* ]]; then
    record ok "$name" "$(printf '%s' "$out" | tail -1)"
  else
    record FAIL "$name" "exit $status: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
  fi
}

say "cpp-repl smoke test on $(uname -sr), $(uname -m)"

py=$(command -v python3 || command -v python || true)
if [ -z "$py" ]; then
  record FAIL python "no python3 on PATH"
  pyv=none
else
  pyv=$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    record ok python "$pyv"
  else
    record FAIL python "$pyv; cpp-repl needs 3.11 or newer"
  fi
fi

if [ -z "$wheel" ]; then
  wheel=$(curl -fsSL "https://api.github.com/repos/farhatnadim/cpp_repl/releases/latest" 2>/dev/null \
            | sed -n 's/.*"browser_download_url": *"\([^"]*\.whl\)".*/\1/p' | head -1)
  [ -n "$wheel" ] || record FAIL wheel "no wheel in the newest release at $repo/releases"
fi

bin=
if [ -n "$py" ] && [ -n "$wheel" ] && "$py" -m venv "$work/venv" > "$work/venv.log" 2>&1; then
  bin="$work/venv/bin"
  [ -d "$bin" ] || bin="$work/venv/Scripts"
  if "$bin/python" -m pip install -q "$wheel" > "$work/pip.log" 2>&1; then
    record ok install "$(basename "$wheel")"
  else
    record FAIL install "$(tail -2 "$work/pip.log" | tr '\n' ' ')"
  fi
elif [ -n "$py" ] && [ -n "$wheel" ]; then
  record FAIL install "python3 -m venv failed: $(tail -1 "$work/venv.log")"
fi
[ -n "$bin" ] && export PATH="$bin:$PATH"

check version 'cpp-repl ' 'cpp-repl --version'

clang_repl=
if [ -n "$bin" ]; then
  clang_repl=$("$bin/python" -c 'from cpp_repl.config import find_clang_repl; print(find_clang_repl())' 2>/dev/null || true)
fi
if [ -n "$clang_repl" ]; then
  say "clang-repl: $clang_repl, $("$clang_repl" --version 2>/dev/null | sed -n 's/.*LLVM version \([0-9.]*\).*/\1/p' | head -1)"
  check session '[1, 2, 3]' "printf '$inputs' | cpp-repl -q"
  printf '#include <iostream>\nint main() { std::cout << "hello from a program\\n"; return 0; }\n' > "$work/hello.cpp"
  check batch 'hello from a program' "cpp-repl --batch -q '$work/hello.cpp'"
  if [ -d /usr/include/eigen3 ] || [ -d /usr/local/include/eigen3 ] || [ -d /opt/homebrew/include/eigen3 ]; then
    check eigen '10' "printf 'Eigen::Matrix2d m{{1, 2}, {3, 4}};\nm.sum()\n' | cpp-repl -q -p eigen"
  else
    record skipped eigen "no Eigen headers (libeigen3-dev, or brew install eigen)"
  fi
else
  for step in session batch eigen; do
    record skipped "$step" "no clang-repl 23 or newer: clang-23 and clang-tools-23 from apt.llvm.org, or cpp-repl --docker"
  done
fi

check docker-cmd '-m cpp_repl' 'cpp-repl --no-config --docker --dry-run'
if command -v docker > /dev/null 2>&1 && docker image inspect cpp-repl:llvm23 > /dev/null 2>&1; then
  check docker '[1, 2, 3]' "printf '$inputs' | cpp-repl --no-config --docker -q"
elif command -v docker > /dev/null 2>&1; then
  record skipped docker "no image cpp-repl:llvm23: cpp-repl --docker once in a terminal builds it"
else
  record skipped docker "no docker"
fi

say
say "Summary: $(uname -sr) $(uname -m), python $pyv, clang-repl ${clang_repl:-none}, docker $(command -v docker > /dev/null 2>&1 && echo yes || echo no)"
printf '  %s\n' "${results[@]}"
exit $failed
