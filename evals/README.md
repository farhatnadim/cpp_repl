# Evals for the Claude Code plugin

`claude plugin eval` runs these cases against the plugin in this repository.
Each case is a prompt a user might type, run in a fresh workspace. The case's
`scaffold.sh` seeds that workspace with:
- a small CMake project that uses one library;
- `tools/cpp-repl`, a launcher for a copy of this checkout's cpp-repl.

The copy holds only `bin/`, `src/` and the Dockerfile. A launcher into the
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
| `cmake-question` | A near miss: an OpenCV build question, where add-library must stay quiet |

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

Until then, the same prompts can run headless with the plugin loaded from the
checkout:

```
bash evals/yaml-cpp/scaffold.sh       # in an empty directory
claude -p "$(sed '1,/^---$/d' evals/yaml-cpp/prompt.md)" \
    --plugin-dir /path/to/cpp_repl --allowedTools Bash Read Write Edit Glob Grep Skill
```

The computed graders (`regex`, `tool_used`, `file_exists`) can then be checked
by hand against the workspace.
