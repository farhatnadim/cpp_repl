# Your first session

<!-- clang-repl: 23 -->

This walk starts cpp-repl and types C++ into it. Along the way it shows what
cpp-repl does with blocks, values, your own types, errors, a name declared
twice and a crash, and the commands that undo and save. It needs cpp-repl and
a clang-repl 23 or newer (see [the index](README.md)).

## Start it

<!-- check: off -->
```
$ cpp-repl
cpp-repl: clang-repl 23.1.4, gnu++26, -O0, in /home/you/scratch
%help lists the commands; Ctrl-D quits. Starting clang-repl...
c++>
```

The first line says which clang-repl runs, the C++ standard, the
optimisation level and the working directory. The first start of a
configuration precompiles cpp-repl's headers, which takes a few seconds;
every later start takes about half a second. Without a clang-repl 23 on the
host, `cpp-repl --docker` runs the same in a container (`make docker` builds
the image).

## Statements and values

<!-- check: session -->
```
c++> std::vector<int> v{5, 3, 8, 1};
c++> std::sort(v.begin(), v.end());
c++> v
[1, 3, 5, 8]
c++> v.size() * 2
8
```

An input that ends in a semicolon is a statement: it runs, and shows only
what the code prints. An expression without the semicolon prints its value.
The standard library is there without an `#include`.

Values print the way you would write them:

```
c++> std::map<std::string, int> ages{{"ada", 36}, {"alan", 41}};
c++> ages
{"ada": 36, "alan": 41}
c++> std::optional<double> maybe;
c++> maybe
nullopt
c++> maybe = 2.5;
c++> maybe
2.5
c++> std::make_tuple(1, "two", 3.0)
(1, "two", 3)
```

## Blocks finish themselves

Enter runs an input once its brackets balance and its statement is
complete. Until then it opens a new line, marked `...>`:

```
c++> for (const auto &[name, age] : ages) {
...>   std::cout << name << " is " << age << '\n';
...> }
ada is 36
alan is 41
```

A function head waits for its body, and a multi-line `if` for a possible
`else` (an empty line runs it):

```
c++> int twice(int x)
...> {
...>   return 2 * x;
...> }
c++> twice(21)
42
```

## Your own types

A type of your own prints as its name until it has a printer, and cpp-repl
says how to give it one:

```
c++> struct Point { double x, y; };
c++> Point p{1, 2};
c++> p
<Point>
[Point prints as <Point>: an operator<<, or a repl::Put<Point> specialisation, gives it a printer]
c++> std::ostream &operator<<(std::ostream &os, const Point &q) { return os << "Point(" << q.x << ", " << q.y << ")"; }
c++> p
Point(1, 2)
c++> std::vector<Point>{{0, 0}, {3, 4}}
[Point(0, 0), Point(3, 4)]
```

A non-member `operator<<` is the simplest printer. It can come after the
first print, and a list of the type uses it too.

## Errors

An input that fails to compile prints clang's error, pointing into the input,
and leaves the session as it was:

```
c++> v.push_bak(9);
<input>:1:3: error: no member named 'push_bak' in 'std::vector<int>'
    1 | v.push_bak(9);
      | ~ ^
error: Parsing failed.
c++> v.push_back(9);
c++> v
[1, 3, 5, 8, 9]
```

## A name declared again

```
c++> int x = 1;
c++> double x = 2.5;
[x redefined: 22 inputs replayed]
c++> x
2.5
```

The session is rebuilt from its journal without the old declaration, and
says how many inputs it replayed. An input that needs the old `x` keeps it.

## A crash costs a replay

<!-- check: off -->
```
c++> int *nowhere = nullptr;
c++> *nowhere = 42;
[clang-repl crashed (SIGSEGV): restored 25 inputs]
c++> v
[1, 3, 5, 8, 9]
```

A crash, Ctrl-C on a running input, or a failed link ends that clang-repl.
cpp-repl replays the journal of the inputs that worked into a spare
clang-repl it keeps running, so every variable comes back. An exception that
nothing catches is named, and ends the program, not the session.

## Commands

<!-- check: session -->
```
c++> int a = 1;
c++> int b = 2;
c++> %journal
  1 int a = 1;
  2 int b = 2;
c++> %undo
undo: int b = 2;
[undo: restored 1 input]
c++> %journal
  1 int a = 1;
c++> %save first.repl
saved 1 input to …/first.repl
```

`%journal` lists the inputs so far, and `%undo` drops the last one and
rebuilds the session without it. `%save` writes the journal as a `.repl`
file, which `cpp-repl first.repl` runs again, keeping the prompt. `%help`
lists every command; Ctrl-D or `%quit` quits.

## Next

[Programs without a build](02-programs.md): a `.cpp` file run statement by
statement, `--batch`, and `%save-cpp`.
