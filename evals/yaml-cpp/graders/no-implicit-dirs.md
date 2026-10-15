---
type: regex
match: not_contains
target: { source: file, path: .cpp-repl/yaml-cpp.toml }
pattern: '"/usr(/local)?/include"'
---
