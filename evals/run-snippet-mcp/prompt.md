---
description: "run-cpp through the plugin's MCP tool: two standard-library questions answered with run_cpp, not with Bash"
tags: [run, std, mcp]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit, mcp__plugin_cpp-repl_cpp-repl__run_cpp, mcp__plugin_cpp-repl_cpp-repl__run_cpp_file, mcp__plugin_cpp-repl_cpp-repl__save_cpp, mcp__plugin_cpp-repl_cpp-repl__reset_session]
---

What does `std::views::chunk(3)` give for the numbers 1 to 10, and what exactly does
`std::format("{:>8.3f}", 3.14159)` print, spaces included? Check both by running them rather
than from memory. cpp-repl is available here as the run_cpp tool.
