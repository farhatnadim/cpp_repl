# Scripts and agents

<!-- clang-repl: 23 -->

cpp-repl's prompt waits for a terminal. A script, a test, or a coding agent
such as Claude Code or Codex has none, and runs cpp-repl in the ways this walk
shows: inputs piped in, files with `--batch`, `--json` to read the result,
and a session that stays between runs. Each example is a shell command and
what it printed.

## One question, one run

Pipe the inputs in. Each is echoed with its prompt, so each output sits
under the input that printed it:

<!-- check: sh -->
```
$ cpp-repl <<'EOF'
std::vector<int> v{1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
v | std::views::chunk(3)
std::format("{:>8.3f}", 3.14159)
EOF
c++> std::vector<int> v{1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
c++> v | std::views::chunk(3)
[[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]]
c++> std::format("{:>8.3f}", 3.14159)
"   3.142"
```

`-q` leaves the echo out, so that the output is the program's own. cpp-repl's
own notes, in brackets, stay:

```
$ cpp-repl -q <<'EOF'
int x = 1;
double x = 2.5;
std::cout << x << '\n';
EOF
[x redefined: 0 inputs replayed]
2.5
```

`--batch` runs files, and only files. Given none, it says so, rather than
running nothing and exiting 0:

```
$ cpp-repl --batch <<< 'std::cout << 1;'; echo "exit $?"
cpp-repl: --batch runs files: name one, or leave out --batch to run inputs piped in
exit 2
```

## Errors, brief

Without a terminal, cpp-repl keeps clang's diagnostics brief: each input's
first error with its notes, source lines only from the code being run, no
include chains, and template backtraces of two levels.

<!-- file: sorted.cpp -->
```cpp
#include <algorithm>
#include <vector>

struct Point { int x, y; };

int main()
{
    std::vector<Point> points{{2, 1}, {1, 2}};
    std::sort(points.begin(), points.end());
}
```

<!-- check: sh -->
```
$ cpp-repl --batch -q sorted.cpp; echo "exit $?"
/usr/include/c++/13/bits/predefined_ops.h:45:23: error: invalid operands to binary expression ('Point' and 'Point')
/usr/include/c++/13/bits/stl_heap.h:232:8: note: in instantiation of function template specialization '__gnu_cxx::__ops::_Iter_less_iter::operator()<__gnu_cxx::__normal_iterator<Point *, std::vector<Point>>, __gnu_cxx::__normal_iterator<Point *, std::vector<Point>>>' requested here
/usr/include/c++/13/bits/stl_heap.h:356:9: note: (skipping 6 contexts in backtrace; use -ftemplate-backtrace-limit=0 to see all)
sorted.cpp:9:10: note: in instantiation of function template specialization 'std::sort<__gnu_cxx::__normal_iterator<Point *, std::vector<Point>>>' requested here
    9 |     std::sort(points.begin(), points.end());
      |          ^
/usr/include/c++/13/bits/stl_iterator.h:1189:5: note: candidate template ignored: could not match '__normal_iterator<_IteratorL, _Container>' against 'Point'
/usr/include/c++/13/bits/stl_iterator.h:1189:5: note: candidate template ignored: could not match '__normal_iterator<_IteratorL, _Container>' against 'Point'
/usr/include/c++/13/bits/stl_iterator.h:1208:5: note: candidate template ignored: could not match '__normal_iterator<_Iterator, _Container>' against 'Point'
error: Parsing failed.
exit 1
```

That is ten lines; `--diagnostics full`, the prompt's default, prints 238 for
the same error.

## The exit status

The exit status is the program's, as a shell reports a compiled one's:

| Status | When |
| --- | --- |
| N | `main()` returned N, or the code called `exit(N)` |
| 128 + signal | a crash: 139 for SIGSEGV, 134 for `abort()` and for an exception nothing caught |
| 124 | an input ran past `--timeout`, which stops it and goes on without it |
| 1 | an input failed to compile or to link |
| 0 | everything ran |
| 2 | cpp-repl could not start: no clang-repl 23 or newer, a flag it does not pass on, a missing file |

<!-- check: sh -->
```
$ cpp-repl -q <<< 'std::vector<int>{1, 2}.at(5)'; echo "exit $?"
[uncaught exception: std::out_of_range: vector::_M_range_check: __n (which is 5) >= this->size() (which is 2)]
exit 134
$ cpp-repl -q --timeout 2 <<'EOF'; echo "exit $?"
while (true) {}
std::cout << "after\n";
EOF
[that input ran past 2 s: restored 0 inputs]
after
exit 124
```

`--stop-on-error` stops at the first input that fails, since what follows
usually needs it.

## --json

`--json` writes one JSON object per input, and a last line with the totals:

<!-- check: sh -->
```
$ cpp-repl --json <<'EOF'
int answer = 42;
answer / 2
answer.size()
EOF
{"input": 1, "file": "<stdin>", "line": 1, "status": "ok", "seconds": 0.001, "output": ""}
{"input": 2, "file": "<stdin>", "line": 2, "status": "ok", "seconds": 0.015, "run_seconds": 1.051e-05, "output": "21\n"}
{"input": 3, "file": "<stdin>", "line": 3, "status": "error", "seconds": 0.001, "output": "<stdin>:3:7: error: member reference base type 'int' is not a structure or union\n    3 | answer.size();\n      | ~~~~~~^~~~~\nerror: Parsing failed.\n"}
{"exit": 1, "inputs": 3, "failures": 1, "skipped": 0}
```

- `status` is `ok`, `error`, `link`, `exception`, `crash`, `exit`, `stuck`,
  `timeout` or `interrupted`.
- `file`, `line`, `run_seconds` (how long the code ran, compiling apart),
  `notes` and `returned` appear when they apply.
- When cpp-repl cannot start, there is no last line: the reason is on stderr,
  and the exit status is 2.

## A session that stays

Starting a session costs about half a second, and a program's variables are
gone when it ends. `--serve` keeps one session running on a Unix socket, and
`--connect` runs files, or stdin, in it, in about 0.07 s. A `return` from
`main()` ends only that run, so a program's variables can be read after it:

<!-- file: stats.cpp -->
```cpp
#include <algorithm>
#include <iostream>
#include <vector>

int main()
{
    std::vector<double> samples{4.0, 1.5, 9.25, 3.0, 7.5};
    std::sort(samples.begin(), samples.end());
    double median = samples[samples.size() / 2];
    std::cout << "median " << median << '\n';
    return 0;
}
```

<!-- check: sh -->
```
$ cpp-repl --serve repl.sock > serve.log 2>&1 &
$ for i in $(seq 100); do test -S repl.sock && break; sleep 0.1; done
$ cpp-repl --connect repl.sock -q stats.cpp
median 4
$ cpp-repl --connect repl.sock <<'EOF'
samples
median * 2
EOF
c++> samples
[1.5, 3, 4, 7.5, 9.25]
c++> median * 2
8
$ cpp-repl --connect repl.sock --fresh <<< 'median'; echo "exit $?"
c++> median
<stdin>:1:1: error: use of undeclared identifier 'median'
    1 | median;
      | ^~~~~~
error: Parsing failed.
exit 1
$ cpp-repl --connect repl.sock <<< '%quit'
c++> %quit
```

- **The server's configuration is fixed.** Profiles, flags, libraries and
  `--cwd` go on the `--serve` line. `--connect` takes `--timeout`,
  `--stop-on-error`, `--diagnostics`, `-q`, `--json` and `--fresh`, and
  anything else exits 2.
- **`--fresh` empties the session** in the spare clang-repl the server keeps
  warm, and the session stays empty for later runs.
- **The socket appears once the session is ready.** Each command resolves a
  relative path against its own directory, so run both from one. AF_UNIX
  allows 107 bytes.
- **Files go with `--connect`.** `--serve` with a file exits 2.
- **`%quit` stops the server,** with exit 0. A crash in a run restores the
  session for the next run, without the input that crashed.
- **Whoever connects runs code as the server's user,** so the socket is
  readable and writable by that user alone.

## For a coding agent

- **Never the prompt.** It waits for a terminal the agent does not have. Use
  inputs piped in, `--batch FILE`, or `--connect`.
- **Read the exit status first,** then the output, or `--json`.
- **A read-only HOME,** as in a sandbox, needs `XDG_STATE_HOME` pointed at a
  writable directory. cpp-repl keeps its precompiled headers and session
  files there.
- **A project may have its own launcher,** such as openusd-tutorial's
  `repl/usd-repl`, which adds its profile and build. Its AGENTS.md or README
  says so.

The cpp-repl Claude Code plugin teaches all of this to an agent, in its
run-cpp skill ([With Claude Code](05-claude-code.md)), and cpp-repl's
AGENTS.md says the same for agents that read only that.

## Next

[With Claude Code](05-claude-code.md): the plugin and its four skills.
