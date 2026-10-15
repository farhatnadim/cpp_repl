---
type: llm
---

PASS if the final reply tells the user that cpp-repl has glm built in, gives a command that starts
it with `-p glm` (directly or through tools/cpp-repl), and shows or describes how glm's vectors
and matrices print.
FAIL if it writes a profile of its own for glm, gives no command that starts the REPL with glm,
or claims output it did not run.
