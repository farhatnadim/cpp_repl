---
description: "run-cpp through the plugin's MCP tools: a program's variable read after its main() returns, without a build"
tags: [run, program, mcp]
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit, mcp__plugin_cpp-repl_cpp-repl__run_cpp, mcp__plugin_cpp-repl_cpp-repl__run_cpp_file, mcp__plugin_cpp-repl_cpp-repl__save_cpp, mcp__plugin_cpp-repl_cpp-repl__reset_session]
---

Without setting up a build, run src/stats.cpp and tell me the value of `median` once main() has
run, to 4 decimal places. Please leave src/stats.cpp as it is. cpp-repl is available here as the
run_cpp and run_cpp_file tools.
