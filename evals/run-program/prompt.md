---
description: "run-cpp: a program's variable read after its main() returns, without a build"
tags: [run, program]
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Without setting up a build, run src/stats.cpp and tell me the value of `median` once main() has
run, to 4 decimal places. Please leave src/stats.cpp as it is. cpp-repl is available here (the
launcher is tools/cpp-repl).
