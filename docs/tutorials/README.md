# Tutorials

Five short walks through cpp-repl, each a few minutes, in this order:

| Tutorial | What it shows |
| --- | --- |
| [Your first session](01-first-session.md) | values, blocks, your own types, errors, a name declared again, a crash and its replay, the commands |
| [Programs without a build](02-programs.md) | a `.cpp` file's `main()` run statement by statement, `--batch` and its exit status, `%save-cpp` |
| [Libraries and your project](03-libraries.md) | the built-in profiles, compiler flags, shared libraries, `compile_commands.json`, `cpp-repl.toml`, printers, speed |
| [Scripts and agents](04-scripts-and-agents.md) | cpp-repl without a terminal: inputs piped in, `--json`, exit statuses, `--serve` and `--connect` |
| [With Claude Code](05-claude-code.md) | the plugin's skills: running C++, adding a library, printers, a built-in profile |

For OpenUSD, the
[openusd-tutorial walkthrough](https://github.com/farhatnadim/openusd-tutorial/blob/repl/repl/TUTORIAL.md)
explores a lesson at the prompt with `-p usd`.

## What they need

cpp-repl (a clone's `bin/cpp-repl`, `pip install -e .`, or the Claude Code
plugin's copy) and a clang-repl 23 or newer: `clang-23` and `clang-tools-23`
from apt.llvm.org, or `make docker` and `cpp-repl --docker`. Tutorial 03 also
uses Eigen, glm, zlib and CMake.

## How the transcripts are checked

Every transcript comes from a run: `scripts/check_tutorial.py` runs each one
again and compares, and `make tutorials` runs it over these files. HTML
comments in the Markdown, which GitHub does not show, say how:
`<!-- check: session ARGS -->` for inputs typed at the prompt,
`<!-- check: sh -->` for shell commands, `<!-- check: off -->` for what only
a terminal shows (a crash's replay, Tab), and `<!-- file: NAME -->` for a
file a tutorial uses. They were recorded with clang-repl 23.1.4 on Ubuntu
24.04, and a run with another clang-repl major skips them.
`make tutorials-update` rewrites the transcripts that differ from a run:
read the diff before committing it.
