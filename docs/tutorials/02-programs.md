# Programs without a build

<!-- clang-repl: 23 -->

This walk runs a C++ program in cpp-repl, with no build: its `main()` one
statement at a time, then its variables at the prompt. It covers `--batch`
and its exit status, errors in a file, and `%save-cpp`, which turns a session
back into a program that builds.

## A program

<!-- file: stats.cpp -->
```cpp
#include <algorithm>
#include <iostream>
#include <numeric>
#include <vector>

double mean(const std::vector<double> &xs)
{
    return std::accumulate(xs.begin(), xs.end(), 0.0) / xs.size();
}

int main()
{
    std::vector<double> samples{4.0, 1.5, 9.25, 3.0, 7.5};
    std::sort(samples.begin(), samples.end());
    double median = samples[samples.size() / 2];
    std::cout << "mean " << mean(samples) << ", median " << median << '\n';
    return 0;
}
```

## Run it and keep the prompt

<!-- check: off -->
```
$ cpp-repl stats.cpp
cpp-repl: clang-repl 23.1.4, gnu++26, -O0, in /home/you/scratch
%help lists the commands; Ctrl-D quits. Starting clang-repl...
c++> #include <algorithm>
c++> #include <iostream>
c++> #include <numeric>
c++> #include <vector>
c++> double mean(const std::vector<double> &xs)
...> {
...>     return std::accumulate(xs.begin(), xs.end(), 0.0) / xs.size();
...> }
c++> std::vector<double> samples{4.0, 1.5, 9.25, 3.0, 7.5};
c++> std::sort(samples.begin(), samples.end());
c++> double median = samples[samples.size() / 2];
c++> std::cout << "mean " << mean(samples) << ", median " << median << '\n';
mean 5.05, median 4
c++> return 0;
c++> samples
[1.5, 3, 4, 7.5, 9.25]
c++> median
4
c++> mean({1, 2, 3})
2
```

On a terminal, `cpp-repl stats.cpp` runs everything the file declares, then
`main()`'s body one statement at a time, echoing each input, and leaves you at
the prompt.
`main()`'s variables are the session's, so the next question about them is
one line away. Its final `return 0;` ends the program quietly, and the prompt
goes on.

`--emit` shows the inputs a file becomes:

<!-- check: sh -->
```
$ cpp-repl --emit stats.cpp
#include <algorithm>
#include <iostream>
#include <numeric>
#include <vector>
double mean(const std::vector<double> &xs)
{
    return std::accumulate(xs.begin(), xs.end(), 0.0) / xs.size();
}
std::vector<double> samples{4.0, 1.5, 9.25, 3.0, 7.5};
std::sort(samples.begin(), samples.end());
double median = samples[samples.size() / 2];
std::cout << "mean " << mean(samples) << ", median " << median << '\n';
return 0;
```

## Run it and exit

`--batch` runs the files and exits, as the compiled program would:

```
$ cpp-repl --batch -q stats.cpp; echo "exit $?"
mean 5.05, median 4
exit 0
```

`-q` leaves out the echo of each input, so what is left is the program's
output. The exit status is the program's: what `main()` returned, the N of
`exit(N)`, or 128 + the signal of a crash.

A `return` in `main()` ends the program, as it does compiled, and a file
named after it does not run:

<!-- file: more.repl -->
```
samples.front()
```

```
$ cpp-repl --batch stats.cpp more.repl; echo "exit $?"
c++> #include <algorithm>
c++> #include <iostream>
c++> #include <numeric>
c++> #include <vector>
c++> double mean(const std::vector<double> &xs)
...> {
...>     return std::accumulate(xs.begin(), xs.end(), 0.0) / xs.size();
...> }
c++> std::vector<double> samples{4.0, 1.5, 9.25, 3.0, 7.5};
c++> std::sort(samples.begin(), samples.end());
c++> double median = samples[samples.size() / 2];
c++> std::cout << "mean " << mean(samples) << ", median " << median << '\n';
mean 5.05, median 4
c++> return 0;
[main returned 0: more.repl not run]
exit 0
```

To read a program's variables after its `main()` returned, without a
terminal, keep a session running: [Scripts and agents](04-scripts-and-agents.md)
shows `--serve` and `--connect`.

## Errors point at the file

<!-- file: broken.cpp -->
```cpp
#include <vector>

int main()
{
    std::vector<int> v{1, 2, 3};
    v.push_bak(4);
    return v.size();
}
```

<!-- check: sh -->
```
$ cpp-repl --batch broken.cpp; echo "exit $?"
c++> #include <vector>
c++> std::vector<int> v{1, 2, 3};
c++> v.push_bak(4);
broken.cpp:6:7: error: no member named 'push_bak' in 'std::vector<int>'
    6 |     v.push_bak(4);
      |     ~ ^
error: Parsing failed.
c++> return v.size();
[main returned 3]
exit 3
```

An error names the file, line and column, as clang++ would. Off a terminal
the diagnostics are brief: each input's first error with its notes, and no
include chains; `--diagnostics full` shows everything clang prints. The run
goes on after an input that fails to compile, and here `main()` returned 3,
which is the exit status.

## From a session back to a program

`%save-cpp` writes the session as a program: includes and definitions at file
scope, the rest in `main()`, and each bare expression as a `show(...)` call.

<!-- check: session -->
```
c++> std::map<std::string, int> ages{{"ada", 36}, {"alan", 41}};
c++> ages["grace"] = 85;
c++> ages
{"ada": 36, "alan": 41, "grace": 85}
c++> %save-cpp ages.cpp
saved 3 inputs to …/ages.cpp
```

<!-- check: sh -->
```
$ grep -v '^//' ages.cpp
#include "cpp_repl.h"
#include "cpp_repl_std.h"

int main()
{
  std::map<std::string, int> ages{{"ada", 36}, {"alan", 41}};
  ages["grace"] = 85;
  show(ages);
  return 0;
}
$ clang++ $(cpp-repl --cflags) ages.cpp $(cpp-repl --libs) -o ages && ./ages
{"ada": 36, "alan": 41, "grace": 85}
```

`--cflags` and `--libs` print the session's own flags, so the program builds
with what the REPL compiled: the profiles, `-I`, `-D`, the libraries. Give
them the options the session had, such as `cpp-repl -p eigen --cflags`.
`cpp-repl ages.cpp` runs the saved program in the REPL again.

## Next

[Libraries and your project](03-libraries.md): profiles, flags,
`compile_commands.json`, `cpp-repl.toml`, and printers for your own types.
