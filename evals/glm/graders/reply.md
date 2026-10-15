---
type: llm
---

PASS if the final reply gives the user a command that starts cpp-repl with the new profile
(such as `cpp-repl -c .cpp-repl/<name>.toml`, or the same through tools/cpp-repl), says which
files were written, and says what was checked or what still fails.
FAIL if it gives no command that starts the REPL with the library, or if it claims that
everything works while also describing a failure it did not resolve.
