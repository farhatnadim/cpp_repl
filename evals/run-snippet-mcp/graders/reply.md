---
type: llm
---

PASS if the final reply says that chunk(3) gives [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]] and that
the format prints "   3.142" (three spaces, then 3.142), and says these came from running the
code in cpp-repl, through its run_cpp tool.
FAIL if either answer is wrong, if the reply gives them without having run the code, or if the
code was run through the shell when the tool was there.
