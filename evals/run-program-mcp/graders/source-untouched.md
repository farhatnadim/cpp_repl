---
type: regex
match: not_contains
target: { source: file, path: src/stats.cpp }
pattern: 'printf|std::cout\s*<<\s*median'
---
