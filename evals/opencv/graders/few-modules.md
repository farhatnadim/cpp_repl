---
type: regex
match: not_contains
target: { source: file, path: .cpp-repl/opencv.toml }
pattern: 'libopencv_(viz|dnn|stitching|ml|cvv|videostab)'
---
