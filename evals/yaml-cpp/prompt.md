---
description: "yaml-cpp: one .so beside a .a, a generator expression in its CMake package"
tags: [library, shared]
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

This app parses its config with yaml-cpp. Set things up so I can load YAML snippets and poke
at YAML::Node values in cpp-repl (launcher: tools/cpp-repl).
