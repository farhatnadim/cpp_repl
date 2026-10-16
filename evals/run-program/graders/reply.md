---
type: llm
---

PASS if the final reply gives median = 9.9644, says it came from running src/stats.cpp in
cpp-repl (no build), and src/stats.cpp was left as it was.
FAIL if the value is wrong or estimated, if the reply built the program with CMake or a compiler,
or if it edited src/stats.cpp.
