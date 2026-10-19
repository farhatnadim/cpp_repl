# Evals for the Claude Code plugin

`claude plugin eval` runs these cases against the plugin in this repository.
Each case is a prompt a user might type, run in a fresh workspace. The case's
`scaffold.sh` seeds that workspace with:
- a small CMake project that uses one library;
- `tools/cpp-repl`, a launcher for a copy of this checkout's cpp-repl.

The copy holds only `bin/` and `src/`, the Dockerfile in it. A launcher into the
checkout itself leads a run without the plugin to `skills/`, as a first try
showed. That run then follows the skills by hand, and the baseline measures
them too.

| Case | What it tests |
| --- | --- |
| `glm-builtin` | A library cpp-repl has built in: add-library hands back `-p glm` and writes no profile |
| `yaml-cpp` | One shared library beside a static one, and a generator expression in the CMake package |
| `spdlog` | Compile definitions from the package, and `libfmt` as a dependency the native build needs |
| `opencv` | A modular package: a few components, many shared libraries, and an `operator<<` that prints every pixel |
| `opencv-printers` | add-printer, on a profile whose `cv::Mat` prints every element |
| `symbols-not-found` | A pasted link error: the library that defines the missing symbol is added |
| `cmake-question` | A near miss: an OpenCV build question, where add-library and run-cpp must stay quiet |
| `run-snippet` | run-cpp: two standard-library questions answered from a run |
| `run-program` | run-cpp: a variable read after `main()` returns, with no build and the source left as it is |

## Running

```
claude plugin eval . --scaffold --trust-plugin --allow-tools Bash Write Edit --no-publish \
    --model claude-sonnet-5-5 --case yaml-cpp --runs 1 --ablation none
```

- **Flags:**
  - `--scaffold` runs the cases' `scaffold.sh`;
  - `--allow-tools Bash Write Edit` lets the agent run the skills' scripts
    and write the profile.
- **Cost:** every run is a real model call. A library case takes about 20 to
  60 turns. Drop `--ablation none` to measure the plugin against a run without
  it, which doubles the runs. Set `--max-cost-usd` for a ceiling.
- **Results:** they go to `evals/results/`, which git ignores.

## The sandbox

When Bash is granted, each run's commands go through Claude Code's sandbox,
which uses bubblewrap on Linux. On Ubuntu 24.04, AppArmor stops unprivileged
user namespaces by default (`kernel.apparmor_restrict_unprivileged_userns = 1`).
Every command then fails before it runs:

```
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

There are two ways to fix it:
- give bubblewrap an AppArmor profile that allows user namespaces;
- set that sysctl to 0, which weakens a system-wide protection, so weigh that
  first.

Until then, `evals/headless.py` runs the same cases with `claude -p`, outside
that sandbox, as you:

```
evals/headless.py --case run-snippet --case yaml-cpp   # or every case, with no --case
evals/headless.py --no-plugin --case opencv            # the baseline arm
```

- **The plugin is a `git archive` of HEAD,** loaded with `--plugin-dir`, so
  commit what a run should see. An installed cpp-repl plugin is turned off
  for the run, and a run fails when the plugin loads other than once.
- **Each case's `scaffold.sh` comes from that copy** too, and seeds a
  temporary workspace (`--keep` keeps it).
- **The computed graders score the run:**
  - `tool_used` against the tool calls;
  - `regex` and `file_exists` against the workspace;
  - a `regex` with no target, against the final reply.

  An `llm` grader is printed with the reply, for a judgement by hand.
- **Cost:** a run spends at most `--budget` dollars (0.6), and no run starts
  once the runs so far cost `--total` (5). Sonnet 5.5 is the default model.
- **Results** go to `evals/results/headless-<time>.json`.

## What a run needs

A clang-repl 23 or newer on the host (cpp-repl refuses an older one), or the
`cpp-repl:llvm23` image for the cases that run with `--docker`. The library
cases need their `-dev` packages: yaml-cpp, spdlog with fmt, OpenCV, glm.
