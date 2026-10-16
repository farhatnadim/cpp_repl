---
description: "run-cpp: two standard-library questions answered from a cpp-repl run, not from memory"
tags: [run, std]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

What does `std::views::chunk(3)` give for the numbers 1 to 10, and what exactly does
`std::format("{:>8.3f}", 3.14159)` print, spaces included? Check both by running them rather
than from memory. cpp-repl is available here (the launcher is tools/cpp-repl).
