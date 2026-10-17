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
clang-repl 23 and newer, and Docker images carry the newest. It installs
with `uv tool install cpp-repl`.

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
| `src/cpp_repl/docker/` | the Dockerfile for the images with clang-repl from apt.llvm.org, or built from source; in the package, so that `cpp-repl --docker` builds them from an install |
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
make check          # before a pull request: every test against the host's clang-repl (23 or newer), the wheel, then claude plugin validate
make wheel-check    # build the wheel with uv and run cpp-repl from it
uv run cpp-repl     # a clone through uv: its .venv from uv.lock, nothing installed
make check-docker   # the same inside cpp-repl:llvm23, built first: what CI runs
make pr             # make check, push the branch N-slug and open its pull request, which closes issue N
make issue KIND=bug|feature|task TITLE="..." [BODY="..."]   # file an issue on the board
make test           # unit and fake-backend tests; no clang-repl needed
make test-host      # everything, against the host's clang-repl (23 or newer: with an older one the backend tests skip)
make tutorials      # docs/tutorials' transcripts against runs
make parity         # the OpenUSD lessons through cpp-repl -p usd against their compiled programs
make compat         # what raw clang-repl does with the inputs cpp-repl rewrites
make compat-table   # the README's "Why a front end" table, written between its markers from tests/compat/results
make compat-diff    # a probe run against the checked-in results (FILE=tests/compat/results/compat-llvm-snapshot.json)
make track          # what track.yml runs weekly: the snapshot image, the suite and the probes in it, the probes diffed
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
- **`make check` before the pull request.** Every test against clang-repl 23
  or newer, then `claude plugin validate`. CI (`.github/workflows/check.yml`)
  runs the same suite inside the `cpp-repl:llvm23` image, `make check-docker`,
  where clang compiles against GCC 13 as on the author's machine. `make pr`
  runs `make check`, pushes, and opens the pull request titled as the issue,
  with `Closes #N`. A pull request that closes no issue fails CI.
- **CI on the pull request is the evidence;** a passing local run is not.
  Merges are rebases, the branch is deleted on merge, the issue closes with
  it. The user merges (`gh pr merge --rebase`, or `--auto` to queue it),
  unless they tell the agent to. An agent never closes an issue.
- **A release** is a tag `vX.Y.Z` on main. `make version VERSION=x.y.z` on
  an issue branch sets `src/cpp_repl/__init__.py`, the one place the version
  lives, and `make pr` lands it; `make tag` on main then tags and pushes, and
  `.github/workflows/release.yml` builds the wheel, runs it against
  clang-repl 23, publishes to PyPI through trusted publishing and makes the
  GitHub release. A tag never moves: a botched release is the next patch
  number. The plugin keeps tracking main: `plugin.json` names no version, so
  an install follows the commits.
- **GOALS.md** stays the roadmap summary the workspace status tool reads; the
  board holds the work.

## Tracking clang-repl

`config.LLVM_RELEASE` is the release the Docker images carry and CI runs
(23); `MIN_VERSION` is the oldest cpp-repl accepts. The Makefile's `LLVM`,
the default image, the Dockerfile's `ARG LLVM_MAJOR` and the checked-in
`tests/compat/results/compat-llvm23.json` follow it, and
`tests/test_package.py` checks that they agree. `make compat-table` writes
the README's "Why a front end" table between its markers, and a test fails
when the table differs from the results.

`.github/workflows/track.yml` runs every Monday and on demand: the snapshot
image (LLVM main) and the release image are built, the suite and the compat
probes run in each, and the verdicts are diffed against the checked-in
results. A changed verdict, a failing test, or apt.llvm.org carrying the next
major files a task issue, or adds to the open one, with the diff and the run,
whose artifacts hold the new JSON and the test log. The snapshot never runs
on a pull request: it changes under the branch. The tutorials' transcripts
are recordings of one clang-repl (`<!-- clang-repl: 23 -->`), so
`make test-snapshot` skips them; the probes and the rest of the suite are
the snapshot's signal.

When the issue comes:
1. `gh issue develop N --checkout`; `gh run download RUN -n compat-snapshot
   -D tests/compat/results/`, or `make docker-snapshot compat-snapshot`;
   read the diff.
2. A failing test is a cpp-repl bug to fix. A verdict that moved from a
   workaround to `works` changes no code.
3. `make compat-table`, adjust the prose under the table, `make pr`.

When apt.llvm.org carries the next major, say 24:
1. `LLVM_RELEASE = 24` in `config.py`, `ARG LLVM_MAJOR=24` in the Dockerfile
   (both stages); `MIN_VERSION` stays until something needs the newer one.
2. `make docker test-docker`; fix what fails.
3. `make compat-docker compat-table`: `compat-llvm24.json` joins the table,
   the snapshot moves on, `compat-llvm23.json` stays.
4. clang-24 and clang-tools-24 on the host, `make tutorials-update`, read
   the diff, and the tutorials' `<!-- clang-repl: 24 -->` pins.
5. The prose that names 23: README, this file, `src/cpp_repl/docker/README.md`,
   the skills, the workflows' comments.
6. `make pr`, then a release: `DEFAULT_IMAGE` ships in the wheel.

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
