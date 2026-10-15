---
type: regex
match: not_contains
target: { source: file, path: .cpp-repl/glm.check.json }
pattern: '"kind": "(unprintable|verbose|vector)"'
---
