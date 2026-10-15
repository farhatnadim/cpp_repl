---
type: regex
match: not_contains
target: { source: file, path: .cpp-repl/spdlog.toml }
pattern: '"/usr(/local)?/include"'
---
