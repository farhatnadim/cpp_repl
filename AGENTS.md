# AGENTS.md

Guidance for AI agents working in this repository. In the author's workspace
the directory above holds a workspace-level `AGENTS.md`, which still applies
(a clone has none); this file adds what is specific to cpp_repl.

## What this project is

`cpp-repl` is a C++ REPL: a Python front end over LLVM's `clang-repl`. It reads
whole blocks, rewrites the shapes clang-repl gets wrong, prints values, keeps a
journal so that a crash or Ctrl-C at the prompt costs a replay, and converts between `.cpp`
files and sessions. Profiles (`usd`, `eigen`, `glm`, `torch`) and project configuration
(`cpp-repl.toml`, `compile_commands.json`) point it at a library. It runs
clang-repl 23 and newer, and Docker images carry the newest.

The repository is also a Claude Code plugin (`.claude-plugin/`). Its skills
run C++ for an agent (`run-cpp`) and add a library to cpp-repl
(`add-library`, `add-printer` and `contribute-profile`), in `skills/`.

It began as a fork of `openusd-tutorial/repl/usd-repl` on 2026-10-13: commit
2c41aad plus that tree's uncommitted `_source()` change. Since 2026-10-16 the
tutorial's `repl/usd-repl` is a launcher over cpp-repl's usd profile, which its
CMake build fetches at a pinned commit. Fixes land here, and the tutorial moves
its pin.

## Who writes the code

The workspace rule applies: the user writes the code, agents assist. On
2026-10-13 the user granted Claude the v0.1 implementation: scaffold, front end,
profiles, Docker images, tests and benchmark. The grant covered v0.1. Later
features follow the workspace rule unless the user extends it. Fixing a bug
found in review stays in scope.

The plugin's skills are a standing grant, given on 2026-10-14, for whoever runs
them:
- An agent running `add-library` or `add-printer` writes the profile, prelude,
  printers, example and check report those skills describe.
- An agent running `contribute-profile` in this repository writes a built-in
  profile:
  - its module and header under `src/cpp_repl/profiles/<name>/`;
  - its test, golden session, example and README row;
  - its line in `PRELUDE_NAMES` and in `docker.py`'s environment list.

The rest of the front end (lexer, sources, session, backend, config) follows
the rule above.

## Layout

| Path | What it holds |
| --- | --- |
| `src/cpp_repl/lexer.py` | C++ tokens, statement boundaries, when an input is complete, what to send |
| `src/cpp_repl/sources.py` | `.cpp` and `.repl` files as inputs |
| `src/cpp_repl/config.py` | flags, profiles, `cpp-repl.toml`, `compile_commands.json`, the clang-repl binary |
| `src/cpp_repl/pch.py` | the preludes as a precompiled header, built once per configuration |
| `src/cpp_repl/preload.py` | the session's libraries loaded into clang-repl as it starts, core dumps off first |
| `src/cpp_repl/backend.py` | one clang-repl process and the end-of-output marker protocol |
| `src/cpp_repl/session.py` | the journal, recovery, redefinition, commands, `%save-cpp` |
| `src/cpp_repl/server.py` | `--serve` and `--connect`: one session on a Unix socket |
| `src/cpp_repl/diagnostics.py` | what is shown of clang's diagnostics: brief or full |
| `src/cpp_repl/ui.py`, `painter.py` | prompt_toolkit or readline input; colour |
| `src/cpp_repl/prelude/` | `cpp_repl.h`, the C++ every session starts from |
| `src/cpp_repl/profiles/` | one Python module and one header per library |
| `docker/Dockerfile` | images with clang-repl from apt.llvm.org, or built from source |
| `tests/` | unit tests, a fake clang-repl, golden sessions, compat probes |
| `bin/` | `cpp-repl` and `cpp-repl-docker`, run from the checkout |
| `scripts/` | `parity.py` (the tutorial's lessons against their compiled programs), `compat_table.py`, `check_tutorial.py` (the tutorials' transcripts against runs) |
| `docs/tutorials/` | five walks through cpp-repl, every transcript checked against a run |
| `bench/` | NumPy vs Eigen in the REPL vs native Eigen |
| `.claude-plugin/` | the Claude Code plugin and marketplace manifests; the plugin root is the repo root |
| `skills/` | the plugin's skills; `skills/add-library/scripts/` probes libraries and checks profiles |
| `evals/` | `claude plugin eval` cases for the skills, and `headless.py`, which runs them with `claude -p`; results go to `evals/results/` (ignored) |

## Commands

```
make check          # before a pull request, what CI runs: every test against the host's clang-repl (23 or newer), then claude plugin validate
make pr             # make check, push the branch N-slug and open its pull request, which closes issue N
make issue KIND=bug|feature|task TITLE="..." [BODY="..."]   # file an issue on the board
make test           # unit and fake-backend tests; no clang-repl needed
make test-host      # everything, against the host's clang-repl (23 or newer: with an older one the backend tests skip)
make tutorials      # docs/tutorials' transcripts against runs
make parity         # the OpenUSD lessons through cpp-repl -p usd against their compiled programs
make compat         # what raw clang-repl does with the inputs cpp-repl rewrites
make docker         # build cpp-repl:llvm23 (make docker-snapshot: LLVM main)
make test-docker    # the test suite inside the image (make test-snapshot: in the snapshot's)
make bench-quick    # a 3-minute benchmark run in the bench image
claude plugin validate .   # the plugin's manifests and skills
evals/headless.py --case <case>   # one skill eval with claude -p; real model calls
claude plugin eval . --case <case> --runs 1 --ablation none   # the same, where its sandbox runs
```

## How changes land

Since v0.1.0 (2026-10-16) work is tracked on GitHub and lands by pull request,
for the user and for agents alike.

- **An issue first.** A bug, a feature or a task is an issue on the board,
  project 18 (github.com/users/farhatnadim/projects/18), before it is code:
  `make issue KIND=bug|feature|task TITLE="..." [BODY="..."]`, which files it
  with the label and puts it on the board (`gh issue create --project` fails
  in gh 2.45), or the forms on the web, which do the same. A problem not fixed
  on the spot is filed the same way. A title is one sentence that states the
  fact.
- **A branch per issue.** `gh issue develop N --checkout` makes `N-slug`,
  linked to the issue. Nothing goes to main directly: a ruleset requires a
  pull request with a passing `check`, for the owner too.
- **`make check` before the pull request.** It runs what CI runs
  (`.github/workflows/check.yml`): every test against clang-repl 23 or newer,
  then `claude plugin validate`. `make pr` runs it, pushes, and opens the pull
  request titled as the issue, with `Closes #N`. A pull request that closes no
  issue fails CI.
- **CI on the pull request is the evidence;** a passing local run is not.
  Merges are rebases, the branch is deleted on merge, the issue closes with
  it. The user merges (`gh pr merge --rebase`, or `--auto` to queue it),
  unless they tell the agent to. An agent never closes an issue.
- **A release** is a tag `vX.Y.Z` on main with a GitHub release
  (`gh release create`); `pyproject.toml` carries the same version. The
  plugin keeps tracking main: `plugin.json` names no version, so an install
  follows the commits.
- **GOALS.md** stays the roadmap summary the workspace status tool reads; the
  board holds the work.

## Using cpp-repl from an agent

cpp-repl's prompt waits for a terminal, which an agent does not have. Run it
one of these ways:

```
cpp-repl <<'EOF'                          # inputs piped in: each echoed, then its output
std::vector<int> v{3, 1, 2};
std::ranges::sort(v);
v
EOF
cpp-repl --batch -q --timeout 60 prog.cpp # files: the program's output, exit status the program's
cpp-repl --serve repl.sock > serve.log 2>&1 &   # a session that stays: --connect runs in it
```

- **`--batch` runs files only.** With none it exits 2.
- **A `return` from `main()` ends the run,** and the files after it do not
  run. To read a program's variables after it, run it with `--connect` in a
  `--serve` session, then send the reads with `--connect`.
- **`--json`** gives one object per input. The exit status is the program's:
  2 means cpp-repl could not start.
- **Inside this checkout, run `bin/cpp-repl`.** With the plugin installed,
  `cpp-repl` on Claude Code's PATH is the installed copy. Load the working
  tree with `claude --plugin-dir .`, with the installed copy turned off in
  `.claude/settings.local.json`:
  `{"enabledPlugins": {"cpp-repl@cpp-repl": false}}`.

`skills/run-cpp/SKILL.md` has the details, and
`docs/tutorials/04-scripts-and-agents.md` shows each with its output.

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
