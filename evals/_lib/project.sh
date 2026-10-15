# Shared by the cases' scaffold scripts, which source it in an empty
# workspace. It puts a launcher at tools/cpp-repl, so that runs with and
# without the plugin can both reach the REPL, and makes the workspace a git
# repository.
#
# The launcher runs a copy of cpp-repl's bin/, src/ and docker/Dockerfile,
# kept in tools/.cpp-repl/. A launcher into the checkout itself would lead a
# run without the plugin to the checkout's skills/, and so measure the skills
# in both arms.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p src tools/.cpp-repl/docker
cp -r "$ROOT/bin" "$ROOT/src" tools/.cpp-repl/
cp "$ROOT/docker/Dockerfile" tools/.cpp-repl/docker/
find tools/.cpp-repl -name __pycache__ -prune -exec rm -rf {} +
cat > tools/cpp-repl <<'EOF'
#!/bin/sh
exec python3 "$(dirname "$(readlink -f "$0")")/.cpp-repl/bin/cpp-repl" "$@"
EOF
chmod +x tools/cpp-repl
git init -q .
