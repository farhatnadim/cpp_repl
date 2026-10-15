---
description: "add-printer: an OpenCV profile whose Mats print every element"
tags: [printer]
max_turns: 80
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

When I print a cv::Mat in cpp-repl (tools/cpp-repl) it dumps every single pixel, and a
std::vector of Mats is a mess. The profile is in .cpp-repl/. Can you make Mats print compactly,
both on their own and inside a vector?
