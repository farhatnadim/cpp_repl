# cpp-repl

A C++ REPL for any library, on top of LLVM's
[clang-repl](https://clang.llvm.org/docs/ClangRepl.html). You type C++ as you
would inside `main()`, and:

- **Blocks finish themselves.** Enter runs an input once its brackets balance
  and its statement is complete, and opens a new line otherwise. A multi-line
  `if`, braces or not, waits for its `else`, a function head such as `int f()` for its body,
  and a `template <...>` line, an attribute or `struct S` for what they head,
  so C++ is typed as it is written.
- **Names can be declared again.** `int x = 1;` then `double x = 2.5;`: the
  session is rebuilt without the old `x` and says so. An input that needs the
  old one keeps it.
- **Values print.** A bare expression prints its value: containers, maps,
  tuples, optionals, variants, smart pointers and wide strings, and your own
  types through their `operator<<`. Profiles add Eigen matrices and OpenUSD
  stages.
- **A crash costs a replay at the prompt.** A segfault, a Ctrl-C or a link
  failure replays the session's journal into a spare clang-repl that is
  already running: the inputs come back in a fraction of a second, every
  variable with them.
  An exception nothing catches is named and ends the program, not the
  session. (A `--batch` run stops where the program would: at a crash, an
  `exit()` or a `return` from `main()`.)
- **Programs and sessions convert both ways.**
  - `cpp-repl prog.cpp` runs `main()` one statement at a time and leaves you
    at the prompt, its variables in scope.
  - `%save-cpp` writes the session back out as a program that builds.
- **It runs at native speed.** With `-O3 --native`, Eigen typed at the prompt
  multiplies matrices as fast as clang++-compiled Eigen, and as fast as NumPy
  (see [Speed](#speed)). `%timeit` times a statement as IPython does.
- **It starts in half a second.** The first start of a configuration
  precompiles its preludes, a few seconds; every start after that takes
  about half a second, and about a second with OpenUSD's headers
  ([Start-up](#start-up)).
- **Errors point at your lines.** An error in a file you run names its line
  and column, `prog.cpp:12:5`, as clang++ would. `--batch` keeps errors brief,
  for a coding agent that fixes the code and runs it again.
- **It points at your project.** It takes `compile_commands.json`, a
  `cpp-repl.toml`, compiler flags and shared libraries.
- **It runs clang-repl 23 and LLVM's development snapshot (24).**
  It works around what each version gets wrong
  ([Why a front end](#why-a-front-end)), and Docker images carry the newest
  ones. The default standard is the newest the clang knows: gnu++26 from
  clang 17.

```
$ cpp-repl --docker
c++> std::vector<int> v{5, 3, 8, 1};
c++> std::sort(v.begin(), v.end(), [](int a, int b) { return a > b; });
c++> v
[8, 5, 3, 1]
c++> std::map<std::string, int> ages{{"ada", 36}, {"alan", 41}};
c++> ages
{"ada": 36, "alan": 41}
c++> for (const auto &[name, age] : ages) {
...>   std::cout << name << " is " << age << '\n';
...> }
ada is 36
alan is 41
c++> int *p = nullptr;
c++> *p = 42;
[clang-repl crashed (SIGSEGV): restored 7 inputs]
c++> v
[8, 5, 3, 1]

$ cpp-repl --docker -p eigen -O3 --native
c++> Eigen::Matrix2d m{{1, 2}, {3, 4}};
c++> m * m.inverse()
Matrix<double> 2×2
    1    0
    0    1
c++> Eigen::MatrixXd A = Eigen::MatrixXd::Random(1024, 1024), B = Eigen::MatrixXd::Random(1024, 1024);
c++> Eigen::MatrixXd C(1024, 1024);
c++> %timeit C.noalias() = A * B;
34.6 ms ± 440 µs per loop (mean ± std. dev. of 7 runs, 10 loops each; JIT at -O3)
```

That product is 64 GFLOP/s on one core of a Ryzen 9 5950X, the same as
clang++ -O3 makes of it.

The [tutorials](docs/tutorials/README.md) walk through a first session,
programs without a build, libraries and your project, scripts and coding
agents, and the Claude Code plugin, each in a few minutes.

## Install and run

Linux, Python 3.11 or newer, and either clang-repl 23 or newer, or Docker.

```sh
git clone https://github.com/farhatnadim/cpp_repl.git && cd cpp_repl

bin/cpp-repl                 # the newest clang-repl on PATH or in /usr/lib/llvm-N/bin: 23 or newer (apt.llvm.org: clang-23, clang-tools-23)
make docker                  # or: an image with clang-repl 23 from apt.llvm.org, about a minute
bin/cpp-repl-docker          # cpp-repl in that image; the same as cpp-repl --docker

pip install -e '.[ui]'       # optional: cpp-repl on PATH, with prompt_toolkit for highlighting as you type
```

Without prompt_toolkit, input goes through readline, a line at a time. With
Pygments, output is coloured (`--no-color` or `NO_COLOR=1` turns it off).
`cpp-repl --docker` mounts the directory it runs in, the session's working
directory, the checkout and whatever your configuration reads, each at its
own path, so paths mean the same inside the container and out; `--image TAG`
picks another image (default `$CPP_REPL_IMAGE`, then `cpp-repl:llvm23`),
`--mount DIR` mounts more, and `--dry-run` prints the `docker run` command.

### Which clang-repl

| clang-repl | Where it comes from | Notes |
| --- | --- | --- |
| 23 | `make docker`, or `clang-23` and `clang-tools-23` from apt.llvm.org | The oldest cpp-repl runs. Runs Eigen's kernels and OpenMP. cpp-repl works around its crash on lambdas at top level. |
| main | `make docker-snapshot` | LLVM's development snapshot, for trying fixes that are not released yet. |
| any ref | `make docker-src LLVM_REF=...` | Built from llvm-project in 30 to 60 minutes. |

## Using it

```
c++> std::vector<int> v{5, 3, 8};
c++> std::sort(v.begin(), v.end());
c++> v
[3, 5, 8]
c++> for (int x : v) {
...>   std::cout << x * x << ' ';
...> }
9 25 64
c++> std::map<std::string, int>{{"one", 1}, {"two", 2}}
{"one": 1, "two": 2}
```

- **Enter** runs the input once it is complete. **Esc then Enter** (with
  prompt_toolkit) runs a block as it stands. A pasted block can mix `#include`
  lines and code. If it ends in a bare expression, its value prints, as a
  notebook cell's does.
- **Tab** completes names from every header the preludes include (read
  through the clang++ beside clang-repl), and the session's own names.
  **Ctrl-R** searches the history.
- **Ctrl-C** stops a running input and rebuilds the session without it.
- **A `return N;`** ends the program as `main()`'s return does, and an
  exception nothing catches as `abort()` would; the prompt says so and goes
  on.

| Command | What it does |
| --- | --- |
| `%undo` | drop the last input (the session is replayed without it) |
| `%restart`, `%reset` | rebuild from the journal; start again, empty |
| `%journal` | list the inputs so far |
| `%save FILE`, `%save-cpp FILE` | the journal as a `.repl` file, or as a C++ program |
| `%load FILE` | run a C++ source or a `.repl` file |
| `%lib PATH` | load a shared library |
| `%time INPUT` | run INPUT, then print its wall time and how long the code itself ran, compiling apart (a bare expression's run includes printing it) |
| `%timeit INPUT` | time INPUT in a loop, as IPython does |
| `%flags` | the session's flags, preludes and libraries |
| `%help`, `%quit` | |

The other options: `--cwd DIR` is the session's working directory, where
quoted includes are looked for and the program's files land; `--no-config`
leaves any `cpp-repl.toml` unread; `--clang-repl PATH` names the clang-repl
to run (default `$CPP_REPL_CLANG_REPL`, then the newest found);
`--build-dir DIR` is `--compile-commands DIR`; `--no-pch` starts from the
prelude headers rather than the precompiled header of them
([Start-up](#start-up)); `--version`.

### Files

```sh
cpp-repl prog.cpp            # run prog.cpp's main(), then keep the prompt (on a terminal)
cpp-repl --batch prog.cpp    # run it and exit
cpp-repl --emit prog.cpp     # print the inputs prog.cpp becomes
cpp-repl a.repl              # a session saved with %save
```

A C++ source becomes inputs like this:
- Each `#include`, `#define` and file-scope declaration or function is one input.
- An `#if ... #endif` region is one input.
- `main()`'s body runs one statement at a time, after everything else in the
  file: a function declared above `main()` and defined below it exists when
  `main()`'s statements run, as in the compiled program, whose globals are
  initialised before `main()` too. Its `argc` and `argv` get stand-in values.
- A `return` in `main()` runs as it is and ends the program with its value,
  as it does compiled: nothing after it runs, and `--batch` exits with it.
  The files named after it do not run either, and the run names them:
  `[main returned 0: more.repl not run]`.
- Quoted includes next to the source are found. A `#pragma` stays with the
  statement after it. `CPP_REPL` is defined, so a source can tell where it
  runs.

Each input reaches clang at its own line and column, so an error names the
place in the file, and so do `__FILE__` and `__LINE__`. An input typed at the
prompt is `<input>`, and input piped in is `<stdin>`.

The code reads cpp-repl's stdin, as the compiled program would:
`cpp-repl --batch prog.cpp < input.txt`. At the prompt it reads the terminal,
and when the inputs themselves come through stdin, it reads end-of-file.

`%save-cpp FILE` writes the session as a program:
- includes and definitions at file scope;
- a variable that a function or a lambda typed later uses stays a global, as
  it was at the prompt (and so does one its initialiser uses), initialised
  before `main()` runs; the file and `%save-cpp` say which;
- everything else in `main()`;
- bare expressions as `show(...)` calls.

Build it with the session's own flags:

```sh
clang++ $(cpp-repl -p eigen -O2 --cflags) saved.cpp $(cpp-repl -p eigen -O2 --libs)
```

### In a script or a coding agent's loop

```sh
cpp-repl --batch -q --timeout 30 prog.cpp     # what prog prints, and its errors
cpp-repl --batch --json prog.cpp              # a JSON object per input
```

- **Brief diagnostics.** With `--batch`, or with input piped in, cpp-repl
  shows what fixing the code needs:
  - each input's first error, with its notes;
  - source lines and carets only under a diagnostic in your code;
  - no include chains, and template backtraces of two levels;
  - for a link failure, the first three missing symbols, demangled.

  A `std::sort` of a type with no `operator<` costs 10 lines instead of 238
  (clang-repl 23.1.4).
  `--diagnostics full` shows everything clang prints, as at the prompt.
- **Inputs piped in, or `--batch` with files.** `cpp-repl <<'EOF' ... EOF`
  runs the inputs it reads, then exits with the program's status.
  `--batch` runs files, and only files: given none, it exits 2.
- **`-q`** leaves out the echo of each input, so that the output is the
  program's own, with the errors where they arise, and the program's stderr
  merged in; cpp-repl's own notes, in brackets, stay. What cpp-repl says of the configuration (a `-std` raised, a
  `cpp-repl.toml` of another user skipped) goes to stderr; a profile's note
  is the prompt's and `%flags`'s.
- **`--json`** writes a JSON object per input instead: `input`, `file` and
  `line` (when the input came from one), `status` (`ok`, `error`, `link`,
  `crash`, `exit`, `stuck`, `timeout`, `interrupted`, `exception`),
  `seconds`, `output`, `notes` (cpp-repl's own lines about the input),
  `run_seconds` for a statement (how long its code ran, compiling it apart;
  a declaration at file scope has none, and `%timeit` times its statement
  itself), and `returned` when the input returned from `main()`. Between
  inputs, `{"output": TEXT}` or `{"note": TEXT}` carry what else is written:
  the configuration's notes, the `cpp-repl.toml` read. The last line is
  `{"exit": N, "inputs": N, "failures": N, "skipped": N}`, `skipped` being
  the inputs left unrun, those of later files included, once the program
  ended or `--stop-on-error` stopped. When cpp-repl cannot start (exit 2),
  there is no last line, and stderr says why.
- **The exit status is the program's:** what `main()` returned, the `N` of
  its `exit(N)`, or 128 + the signal that killed it (139 for a segfault, 134
  for `abort()` and for an exception nothing caught, which cpp-repl names:
  `[uncaught exception: std::invalid_argument: stoi]`). Otherwise 130 when
  Ctrl-C stopped an input, else 124 when one ran past `--timeout`, else 1
  when an input failed to compile or link, else 0. It is 2 when cpp-repl
  cannot start (no clang-repl, a flag it does not pass on, no image for
  `--docker`, nothing serving at a `--connect` socket), 1 after SIGTERM or
  SIGHUP, and 141 when whoever read the output went away.
- **`--stop-on-error`** stops at the first input that fails, as the rest
  usually depends on it.
- **`--timeout SECONDS`** stops an input that takes longer, compiling
  included, and rebuilds the session without it. The run goes on, and exits
  with 124 at the end, as `timeout` does.
- **A session that stays.** `cpp-repl --serve SOCKET` keeps a session
  running, its profiles loaded. `cpp-repl --connect SOCKET [FILE...]` runs
  files, or stdin, in it, and exits with the run's status. A run there takes
  0.07 s, against about half a second for a new cpp-repl whose preludes are
  precompiled ([Start-up](#start-up)), or 2 s and more when they are not.
  The session's names carry over from run to run, and a `return` from
  `main()` ends only that run, so a program's variables can be read after
  it. `--fresh` starts a run from an empty session instead, for a program
  run again after an edit, in the spare clang-repl the server keeps warm,
  and the session stays empty for the runs after it: 0.1 s when the spare
  is ready, and the next spare takes a start-up to warm, so a `--fresh` run
  sooner than that after the last waits for it.
  Files go with `--connect`: `--serve` with a file exits 2. Each command
  resolves a relative socket path against its own directory, and `%save`
  writes relative to the server's. `--timeout`,
  `--stop-on-error`, `-q` and `--json` apply to the run, and so does
  `--diagnostics` as far as cpp-repl's own filter goes: the limits clang
  itself applies in brief mode are the server's, so a server meant to answer
  `--diagnostics full` starts with it. The flags, profiles and libraries are
  the server's. `%quit` stops the server (exit 0; SIGTERM ends it with 1).
  Whoever connects runs code as the server's user, so the socket is created
  readable and writable by that user alone, and a client killed mid-run
  stops the run after the input it was on.

  ```sh
  cpp-repl --serve repl.sock -p eigen &
  cpp-repl --connect repl.sock -q --fresh prog.cpp
  echo 'm.rows()' | cpp-repl --connect repl.sock
  ```

### Speed: -O and --native

The JIT compiles at **-O0** unless asked otherwise, which is quick to compile
and slow to run: Eigen's matrix product runs about 300 times slower than
compiled. `-O2` or `-O3` turns the optimiser on, and `--native`
(`-march=native`) lets it use AVX and FMA. With both, code typed at the prompt
runs as fast as clang++'s ([Speed](#speed)). `%timeit` says which level is in
use.

```sh
cpp-repl -p eigen -O3 --native
```

### Start-up

Parsing the prelude headers was most of a start. So the first start of a
configuration precompiles them, with the clang++ beside clang-repl, into
`~/.local/state/cpp-repl/pch/`, and every start after that loads the
precompiled header instead. On clang-repl 23.1.4:

| Start, inside the image | From the headers | Precompiled | The first start |
| --- | ---: | ---: | ---: |
| the standard preludes | about 2 s | 0.53 s | 2.2 s |
| `-p usd` | 4.8 s | 1.05 s | 5.6 s |
| `-p torch`, on the host, the CUDA context included | 12 s | 3.5 s | 15 s |

- A configuration is the clang-repl, the flags, the preludes' contents and
  the libraries; each has its own entry, and one unused for a month goes.
  `rm -r ~/.local/state/cpp-repl/pch` is always safe.
- A header a prelude includes changing is noticed by clang, by content: the
  session rebuilds the entry once and says `[the precompiled header did not
  load: rebuilt]`. A build that fails, or a start that fails with the
  precompiled header, is noted, and the session starts from the headers;
  the failure is remembered for a day.
- `%flags` names the precompiled header in use, or why there is none.
- `--no-pch`, or `pch = false` in a `cpp-repl.toml`, starts from the headers.
  Without a clang++ beside clang-repl there is no precompiled header either.
- It needs clang-repl 23: 18 never emitted the inline functions and inline
  variables whose definitions came from a precompiled header, so an input
  that used one failed to link.

## Your project, your libraries

Compiler flags go to clang as they are: `-I DIR`, `-isystem DIR`,
`-iquote DIR`, `-idirafter DIR`, `-D NAME=VALUE`, `-U NAME`, `-O0` to `-O3`
(`-Os`, `-Oz`, `-Ofast` too), `-std=c++23`, `-stdlib=libc++`, `-include FILE`
(a prelude of your own, parsed before the first input), `-march=...`,
`-f...`, `-W...`, `-w`, `-pedantic`, `-g`, `-pthread`, `-isysroot`,
`--sysroot`, `--gcc-toolchain=`, `-nostdinc`, `-nostdinc++`; `-fsanitize` is
refused, since the JIT cannot run sanitised code. Shared libraries load with `--lib PATH`,
or with `-l NAME` and `-L DIR`, found as the linker finds them: in the `-L`
directories, `$LIBRARY_PATH`, the usual places, then the loader's cache. A
linker script, such as glibc's `libm.so`, is followed to the library it
names. With `-stdlib=libc++`, cpp-repl loads libc++'s runtime first, since
clang-repl itself is linked with libstdc++; the `llvm23` and `snapshot`
images carry it (the `src` image builds clang alone), and with it the C++23
library (`std::println`, `std::expected`, `std::mdspan`) that libstdc++ 13
lacks. With `-fno-exceptions`, a statement runs without the
handler that names an exception nothing catches; with `-fno-rtti`, such an
exception is named `std::exception`, and a `std::any` prints `any(a value)`.

```sh
cpp-repl -I include -DFAST=1 -L build -l mylib -O2
cpp-repl --compile-commands build      # flags as your build compiles: -I, -D, -std, -m, -include
```

From `compile_commands.json`, cpp-repl takes the flags of the entry for the
file you load (or the first entry), response files (`@flags.rsp`) read: its
include directories, definitions and undefinitions, standard, `-stdlib=`,
sysroot and GCC toolchain, `-m` flags, `-pthread`, `-nostdinc++`, the `-f`
flags that change the language (exceptions, RTTI, OpenMP, `char8_t`, the
signedness of `char`, sized deallocation, coroutines), a forced `-include` as
a prelude, and its `-W` flags, `-Werror` apart: warnings stay warnings at a
prompt. `-imacros` is left out, with a note. The optimisation level stays the
REPL's own.

A `cpp-repl.toml` in the working directory, or any directory above it on the
same filesystem, says the same once for a project. It is a build file: its
`libs` load and its `prelude` runs at startup, so one owned by another user
is left alone, with a note, and the startup line, `%flags` and `--json` name
the one read. `-c FILE` reads another, and so does `-p path/to/x.toml`; paths
in it are relative to the file, and a key that takes a list takes one string
too:

```toml
std = "c++23"
include = ["include", "third_party/fmt/include"]
defines = ["FAST=1"]
flags = ["-O2", "-march=native"]
libs = ["build/libmylib.so"]
prelude = ["tools/repl_prelude.h"]   # -include'd into every session
profiles = ["eigen"]
compile_commands = "build"
pch = false                          # start from the headers; true, the default, precompiles them once
```

The precedence, from lowest to highest:
1. profiles (a TOML's `profiles` load before the command line's `-p`)
2. compile_commands.json
3. cpp-repl.toml
4. `-c` files, then `-p x.toml` files
5. the command line

For `-I`, the first match wins; for `-D`, the last definition wins. Without
a `-std`, the session compiles as the newest standard its clang knows:
gnu++26 from clang 17, gnu++23 from clang 12. The prelude's templates need
C++20, so a lower `-std` is raised, and the startup line says so. Headers
that use what C++26 removed fail under the default (OpenCV 4.6: arithmetic
between different enumeration types); `std = "gnu++20"` in the profile, or
`-std=gnu++20`, keeps them compiling.

### Environment

| Variable | What it does |
| --- | --- |
| `CPP_REPL_CLANG_REPL` | the clang-repl to run, before the newest found (`--clang-repl` wins) |
| `CPP_REPL_IMAGE` | the image `--docker` runs (`--image` wins) |
| `CPP_REPL_TRACES=1` | keep LLVM's stack trace when clang-repl crashes; by default cpp-repl reports the crash in one line |
| `NO_COLOR` | no colour, as `--no-color` |
| `XDG_STATE_HOME` | where the state directory goes: `$XDG_STATE_HOME/cpp-repl`, default `~/.local/state/cpp-repl`. It holds the prompt's history, the completion cache, the precompiled preludes of each configuration (`pch/`), the library that loads a session's libraries as clang-repl starts (`preload/`), the patched TBB headers of `-p usd`, each session's input files, `--docker`'s home (`docker-home/`), and the `home` and `tmp` the code sees. A read-only one stops the start with exit 2: point it at a writable directory |
| `LIBRARY_PATH` | directories `-l NAME` looks in, after `-L` |
| `USD_ROOT` or `PXR_ROOT`, `EIGEN_ROOT` or `EIGEN3_INCLUDE_DIR`, `GLM_ROOT` | where a profile's library is, before pkg-config and the usual places (the usd profile reads the `CMakeCache.txt` beside `compile_commands.json` first) |

The code's own `HOME` and `TMPDIR` are the state directory's `home` and
`tmp`, where clang-repl's line history and a library's fatal-error stacks
go, so `getenv("HOME")` shows them; `LD_PRELOAD` is not passed on.

### Profiles

| Profile | What it adds |
| --- | --- |
| `-p eigen` | `<Eigen/Dense>`, found through `EIGEN_ROOT`, pkg-config or the usual places. A matrix prints with its shape; past 10 rows or columns, only the first and last three. A comma initialiser (`m << 1, 2, 3`) prints the matrix it filled; quaternions, angle-axis rotations and transforms print too. |
| `-p glm` | `<glm/glm.hpp>` with the matrix-transform and quaternion extensions, found through `GLM_ROOT`, pkg-config or the usual prefixes. A vector prints as `vec3(1, 2, 3)`, a quaternion as `quat(w, x, y, z)`, a matrix as its rows. |
| `-p torch` | libtorch, PyTorch's C++ API: `<torch/torch.h>`, its libraries, and the GPU when the install has CUDA (`torch::cuda::is_available()`). A tensor prints as libtorch prints it, values then `[ CUDAFloatType{2,3} ]`; past a thousand elements, its first and last values. An error prints the first line of `c10::Error`'s message, and `repl::thrown()` holds the backtrace. The install is found through `TORCH_ROOT` (the libtorch zip, or a pip install's `site-packages/torch`), then the active virtual environment, then the Python running cpp-repl. |
| `-p usd` | OpenUSD's libraries and `using namespace pxr`. A stage prints its usda; a prim its type, properties and children; an attribute its value and time samples. `tree(stage)` prints the hierarchy. The install is found through the `CMakeCache.txt` next to `compile_commands.json`, then `USD_ROOT` or `PXR_ROOT`, then the usual places. The profile asks for C++20, since OpenUSD's headers do not build as C++23 or later; a `-std` of yours still wins. |

A profile of your own is a `cpp-repl.toml`-style file, loaded with `-c`. To
make your types print, give them a non-member `operator<<` in their own
namespace, where argument-dependent lookup finds it, as it finds it for any
template. cpp-repl finds one defined after the type first printed, in a list
too, and for an enum. A type that merely converts to `bool` or to a number
does not print through the stream's own inserters: it prints as `<Type>`,
with a note that says how to give it a printer. For a display of more than
one line, specialise `repl::Show<T>`; for one line inside a list,
`repl::Put<T>`. See `src/cpp_repl/prelude/cpp_repl.h`, and
`src/cpp_repl/profiles/eigen/cpp_repl_eigen.h` for an example.

### With Claude Code

This repository is also a [Claude Code](https://claude.com/claude-code)
plugin. Its skills run C++ for an agent, and add a library to cpp-repl for
you:
- they run snippets and programs without a build, and read what they print;
- they find a library's headers and shared libraries;
- they write a profile into your project;
- they make the library's types print;
- they check the result with an example session.

```
/plugin marketplace add farhatnadim/cpp_repl
/plugin install cpp-repl@cpp-repl
```

The same from a shell: `claude plugin install cpp-repl --marketplace
farhatnadim/cpp_repl` (Claude Code 2.1.275 or newer). `claude plugin
marketplace update cpp-repl`, then `claude plugin update cpp-repl@cpp-repl`,
brings in a newer one.

| Skill | What it does |
| --- | --- |
| `/cpp-repl:run-cpp` | Runs C++ without a build and reads what it prints: what a call returns, a `.cpp` file's `main()` and its variables afterwards, a snippet's errors, a statement's timing. It picks the project's launcher, its `cpp-repl.toml` or a built-in profile, and runs cpp-repl without the prompt, which waits for a terminal an agent does not have. |
| `/cpp-repl:add-library NAME` | Writes `.cpp-repl/NAME.toml`, a prelude with printers, and an example. Then checks them: the prelude compiles, the session starts, every input runs, the shared libraries are really used, and a `%save-cpp` of the example builds and prints the same. |
| `/cpp-repl:add-printer` | Makes a type print well: a readable display on its own, and one line inside a list. |
| `/cpp-repl:contribute-profile NAME` | In a clone of this repository, turns a working profile into a built-in `-p NAME`, with its tests. The `glm` profile was made this way. |

Asking in words works too, as in "let me try OpenCV in cpp-repl". The skill
hands back one command:

```
$ cpp-repl -c .cpp-repl/opencv.toml
c++> cv::Mat img = cv::Mat::zeros(480, 640, CV_8UC3);
c++> cv::circle(img, {320, 240}, 100, {0, 0, 255}, cv::FILLED);
c++> img
Mat 480×640 8UC3
  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)  ...  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)
  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)  ...  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)
  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)  ...  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)
        ...        ...        ...  ...        ...        ...        ...
  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)  ...  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)
  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)  ...  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)
  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)  ...  (0, 0, 0)  (0, 0, 0)  (0, 0, 0)
c++> img.at<cv::Vec3b>(240, 320)
[0, 0, 255]
c++> cv::Mat gray;
c++> cv::cvtColor(img, gray, cv::COLOR_BGR2GRAY);
c++> cv::countNonZero(gray)
31417
```

- **cpp-repl comes with it.** The plugin puts its `bin/` on Claude Code's
  PATH, so `cpp-repl` runs as it is. It still needs a clang-repl 23 or
  newer, or Docker.
- **The profile stays in your project,** in `.cpp-repl/`. Nothing is written
  to the plugin.
- **Contributors load their own clone** with `claude --plugin-dir .`, so the
  skills come from the working tree. With the plugin also installed, turn
  the installed copy off in `.claude/settings.local.json`
  (`{"enabledPlugins": {"cpp-repl@cpp-repl": false}}`).
- **[Tutorial 05](docs/tutorials/05-claude-code.md)** walks through each
  skill.

## Why a front end

clang-repl is a real C++ JIT, but typed at directly it hangs, crashes or
misprints on everyday input. In what follows:
- *raw* means clang-repl alone, fed over a pipe by
  [`tests/compat/harness.py`](tests/compat/harness.py);
- *cpp-repl* means the same input typed into cpp-repl.

python3 scripts/compat_table.py tests/compat/results/*.json
| Raw clang-repl | 23.1.4 | 24.0.0 | cpp-repl |
| --- | --- | --- | --- |
| A line ending in an open `{` | hangs (killed after 20 s) | hangs (killed after 20 s) | waits for the block to close |
| Two top-level range-for loops | works | works | runs each loop inside a function |
| Nested top-level `for` loops (llvm#89506) | works | works | runs the loop inside a function |
| A top-level `try`/`catch` | works | works | runs it inside a function |
| A lambda called in a top-level statement | crashes (SIGSEGV) | works | runs the statement inside a function |
| A lambda passed to a template in a top-level statement | crashes (SIGSEGV) | works | runs the statement inside a function |
| A bare `x + 1`, no semicolon | prints (int) 42 | prints (int) 42 | prints 42 |
| A bare `std::vector<int>{1, 2, 3}` | link error | link error | prints [1, 2, 3] |
| A bare call of a void function | does not run it | does not run it | runs it |
| Inline assembly, as in Eigen's matrix product | works | works | the same (a clang-repl limit) |
| An `#if` region, through an `#include`d file | works | works | sends #if regions this way |
| A dynamic initialiser in an `-include`d header | runs it | runs it | keeps the prelude free of them |
| A prompt written to a pipe | prints no prompt | prints no prompt | learns it at startup and strips it |
| The error `%lib` prints for a missing file | error: <path>: cannot open shared object file: No such file or directory | error: <path>: cannot open shared object file: No such file or directory | uses it as the end-of-output marker |
| How a parse error ends | error: Parsing failed. | error: Parsing failed. | classifies it as an error |
| `-Xcc -O2` in the JIT | __OPTIMIZE__ = 1 | __OPTIMIZE__ = 1 | -O2 on its command line |
| The JIT without `-O` | __OPTIMIZE__ undefined | __OPTIMIZE__ undefined | -O0 too; see %timeit's note |
| `-Xcc -march=native` in the JIT | __AVX2__ = 1 | __AVX2__ = 1 | --native |
| A `return 3;` at top level | crashes (SIGSEGV) | crashes (SIGSEGV) | ends the program with 3, as main() would |
| A `goto` to a label clang-repl never saw | an error | an error | the same error |
| A file-scope lambda whose init-capture is not a constant | prints f 1: the closure holds zero | prints f 1: the closure holds zero | the same (a clang-repl limit, README Limits) |
| `%help` | known | known | its own %help |
| `%undo` | works | works | replays the journal without the last input |

Probed with tests/compat/harness.py: 23.1.4 on 2026-10-16, 24.0.0 on 2026-10-16.

The way around each problem:
- **The unclosed `{`:** cpp-repl never sends an incomplete input.
- **Loops, `try` and lambdas:** cpp-repl runs these inside a function of their
  own. Lambdas at top level are a new crash in 23.1 and already fixed on
  main.
- **Printing a value:** cpp-repl always calls its own printer. That is how
  class values print in every version.
- **Crash reports:** cpp-repl turns core dumps off before any library loads,
  so a crash leaves no report behind (Ubuntu's apport writes them).
- **`return` at top level:** cpp-repl runs it inside a function, where it
  ends the program with its value; typed raw, it segfaults 23.1.4 and 24.
- **`goto` to an unknown label:** an error on 23 and 24.
- **A file-scope lambda's init-capture:** a clang-repl limit cpp-repl cannot
  lift (README, Limits): the closure's initialiser never runs, on every
  version, unless the capture is a constant.

[compare/](compare/README.md) types one whole session into raw clang-repl
23.1.4 and into cpp-repl and shows both answers, side by side, with the
start-up and the cost of an input measured on each clang-repl.

## Speed

The question was how fast C++ typed at the prompt runs, against native
C++ and NumPy. The workload is a dense matrix product of n × n doubles. The
same Eigen source ran compiled by clang++ 23, typed into cpp-repl over
clang-repl 23, and as a kernel compiled ahead of time and called from the
REPL. NumPy ran with OpenBLAS.

The method:
- three rounds;
- pinned cores;
- validity checks on every result;
- hypotheses written down before the first run.

Everything is in [bench/](bench/README.md).

![GFLOP/s against n](bench/results/nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0125Z-gflops.png)

| One thread, n = 1024 | GFLOP/s |
| --- | ---: |
| Eigen, compiled by clang++ -O3 -march=native | 64.0 |
| Eigen typed into cpp-repl -O3 --native | 64.4 |
| cpp-repl calling a kernel compiled ahead of time | 63.1 |
| NumPy (OpenBLAS) | 60.7 |
| cpp-repl with no flags (-O0) | 0.2 |

- **Tuned, the JIT is the compiler.** With `-O3 --native`, Eigen at the
  prompt runs at 0.98–1.02 of native at every size from 32 to 3072. With 16
  threads, OpenMP inside the JIT runs at 0.97–1.00 of native, up to
  660 GFLOP/s.
- **Untuned, it is about 300× slower.** The default is -O0, and leaving out
  `--native` costs almost two thirds of the speed.
- **NumPy was 1.5–6% behind Eigen from n = 512 up,** half as fast at n = 32
  and ahead at n = 384, contrary to the prediction written down before the
  run.
- **What the REPL costs is compile time, not run time.**
  - Cold start to the first product: 3.2 s, against 2.5 s to compile and
    run natively and 0.06 s in NumPy.
  - In a running session, calling the kernel again costs 0.08 s, and a new
    product expression 0.72 s to compile.

## What C++ you can type

Every construct below was typed into cpp-repl on clang-repl 23.1.4 and the
24 snapshot, with the same result on each
(`tests/sessions/features.session` holds the transcript; the ends of a
program are integration tests):
- the C++20 language: concepts and `requires`, abbreviated templates
  (`auto add(auto a, auto b)`), `consteval`, `constinit`, `if constexpr`,
  structured bindings, designated initialisers, `<=>`, `co_await` in a
  function, `[[nodiscard]]`, user-defined literals, `extern "C"`, static
  locals, `goto`, lambdas anywhere (passed to `std::sort`, immediately
  invoked, in a `std::thread`), class templates with out-of-line members,
  virtual functions, `using namespace std`, multi-line raw strings, digit
  separators;
- the C++23 language where the clang has it (`if consteval`, deducing
  `this`, `auto(x)`), and `std::expected` from clang 19, or with
  `-stdlib=libc++`;
- the standard library of C++20 without an `#include`: containers, ranges and
  views, `std::format`, `std::variant`, `std::optional`, `std::atomic`,
  `std::thread` and `std::jthread`, `std::chrono` literals, `std::numbers`,
  `std::source_location`, `std::span`, `std::bit_cast`;
- an uncaught exception, a `return` from `main()`, `exit()` and a crash,
  each ending the program as it does compiled.

What a version lacks is the standard library's: `<print>` and
`<mdspan>` need libstdc++ 14 or libc++ (`cpp-repl --docker -stdlib=libc++`),
`<flat_map>` libstdc++ 15 or libc++ 20, `<generator>` libstdc++ 14, and
cpp-repl says so when an `#include` of them fails. `import std;` has no
module file in any of them, and cpp-repl says so.

## Limits

- **Variables typed at the prompt are globals.** A name can clash with a C
  library function: `int index = 3;` fails. A lambda can neither name a
  global in its capture list nor, at file scope, have a capture-default, so
  `[x]`, `[&x]`, `[=]` and `[&]` go, cpp-repl says so, and the lambda uses
  the global itself: a change to `x` after the lambda was made shows in it,
  where `[x]` would have kept a copy.
- **A lambda typed at the prompt whose init-capture is not a constant**
  (`auto f = [v = base](int x) { return v + x; };`) holds zero for `v`:
  clang-repl 23 and 24 never run that closure's initialiser. `[n = 5]`
  is fine, a `std::function` holding the lambda is fine, and so is any
  lambda inside a function.
- **Rebuilding replays the journal,** file writes and other side effects
  included; declaring a name again replays the inputs after its old
  declaration.
- **Code that reads file descriptor 0 itself,** with `read(0, ...)` or
  `/dev/stdin`, reads what cpp-repl sends clang-repl. `stdin` and `std::cin`
  read the program's input.
- **An input with a backslash line splice outside a directive goes as one
  line,** and so does `%timeit`'s, and its errors name `input_line_N`.
- **An exception thrown by a file-scope declaration's initialiser** (`auto
  x = parse("bad");`) is named, on one line, and still ends the session,
  which is rebuilt; one thrown by a statement is caught and named, unless the statement starts with a
  macro's name (`TF_DEFINE_PRIVATE_TOKENS(...)`), which runs at file scope
  since it may declare something. Likewise a line that reads like a
  diagnostic (`fatal error: …`) printed by such an initialiser, or by a
  header as it loads, is taken for clang's; printed by a statement, it is
  output.
- **`[clang-repl crashed (SIGSEGV)]` covers both the program and the compiler
  crashing.**
- **The prelude's global names** are `show` and `keep` (`timeit` and the
  rest live in `repl::`): a variable of your own named `keep` fails.
- **The first start of a configuration takes a few seconds:** the preludes
  are precompiled then ([Start-up](#start-up)). After that a session starts
  in about half a second. `--no-pch`, and a clang-repl with no clang++
  beside it, parse the preludes at every start:
  about 2 s, or 1.5 s with `--no-std`, which leaves out the standard library
  prelude; OpenUSD adds about 3 s. Docker adds about 0.15 s.
- **Your headers must compile with your clang-repl.** OpenUSD's do not build
  as C++23 or later, so the `usd` profile compiles as C++20. clang 20 and
  newer reject an enumeration constant in TBB 2020's `tbb/task.h`, which
  OpenUSD's build installs, so the profile reads a patched copy of TBB's
  headers, the enumeration given the `unsigned` type it had; the ABI is the
  same.
- **C++20 is the floor.** The default is the newest standard the clang knows.

## How it works

- **[`lexer.py`](src/cpp_repl/lexer.py)** finds where inputs and statements
  end. It decides what clang-repl gets:
  - control flow, lambda statements and expression statements inside a
    function of their own, where a `return` ends the program and an
    exception is caught; declarations and definitions at file scope;
  - each input through a temporary file and an `#include`, after a `#line`
    directive, at its source's lines and columns;
  - a bare expression through `repl::print_expr`.
- **[`diagnostics.py`](src/cpp_repl/diagnostics.py)** filters what clang
  prints: brief or full, and in both without clang-repl's repeated errors.
- **[`backend.py`](src/cpp_repl/backend.py)** runs clang-repl over a pipe.
  - **Marker:** after each input it sends `%lib` of a path that cannot exist.
    The error clang-repl prints marks where the input's output ends, and adds
    nothing to the session.
  - **Calibration:** at startup it learns the marker's text and any prompt.
    It also checks that a parse error is recognised.
- **[`session.py`](src/cpp_repl/session.py)** journals the inputs that
  worked. After a crash, a Ctrl-C, a link failure, `%undo` or a name declared
  again, it replays them into a spare clang-repl started in advance.
- **[`server.py`](src/cpp_repl/server.py)** is `--serve` and `--connect`: one
  session on a Unix socket, for a coding agent's loop.
- **[`pch.py`](src/cpp_repl/pch.py)** precompiles the preludes once per
  configuration ([Start-up](#start-up)), and
  **[`preload.py`](src/cpp_repl/preload.py)** loads the session's libraries
  into clang-repl as it starts, core dumps off first.
- **[`prelude/cpp_repl.h`](src/cpp_repl/prelude/cpp_repl.h)** is the C++
  every session starts from: `show`, `Show<T>`/`Put<T>`, `keep` and `timeit`.
  Profiles add their own headers.

cpp-repl began as `repl/usd-repl` in
[openusd-tutorial](https://github.com/farhatnadim/openusd-tutorial), a REPL for learning OpenUSD's C++ API,
and that `repl/usd-repl` now runs cpp-repl's usd profile.
`scripts/parity.py` checks that cpp-repl runs that tutorial's 20 lessons as
their compiled programs do: the same output, the same files written.

## Related work

| Project | What it is | Difference |
| --- | --- | --- |
| [cling](https://github.com/root-project/cling) | CERN's C++ interpreter (ROOT), on its own LLVM fork | Mature and full-featured, but it is LLVM 20 and its own build. cpp-repl uses stock clang-repl, and any of them. |
| [xeus-cpp](https://github.com/compiler-research/xeus-cpp) | A Jupyter kernel over CppInterOp and clang-repl, with a WebAssembly build | Notebooks rather than a terminal. CppInterOp supports up to LLVM 22. |
| [clang-repl-kernel](https://pypi.org/project/clang-repl-kernel/) | A Python Jupyter kernel over clang-repl | Tested with clang 18. |

## Development

```sh
make check         # before a pull request: every test against the host's clang-repl (23 or newer), then claude plugin validate
make check-docker  # the same inside cpp-repl:llvm23, built first: what CI runs
make test          # unit and fake-backend tests: no clang-repl needed
make test-host     # every test against the host's clang-repl
make test-docker   # every test inside cpp-repl:llvm23 (make docker first)
make parity        # openusd-tutorial's lessons through cpp-repl -p usd and compiled, diffed
make compat        # probe the host's clang-repl; make compat-docker for the images
make compat-table  # the "Why a front end" table from the probes
make bench-quick   # a 3-minute benchmark run (make docker-bench first)
make tutorials     # docs/tutorials' transcripts against runs (make tutorials-update rewrites them)
claude plugin validate .                                 # the Claude Code plugin and its skills
evals/headless.py --case yaml-cpp                        # one skill eval, headless (real model calls)
claude plugin eval . --case yaml-cpp --runs 1 --ablation none   # the same where its sandbox runs
```

The test suite passes on clang-repl 23.1.4 (`make test-docker`) and the 24
snapshot (`make test-snapshot`); `make test-host` runs it against the host's
clang-repl, and skips what needs one when that is older than 23:
- unit tests;
- a fake clang-repl that prints prompts, splits its output and crashes on cue;
- golden transcripts;
- integration tests for crashes, Ctrl-C, link failures and `%save-cpp` round
  trips.

Changes land by pull request from an issue on the project board;
[CONTRIBUTING.md](CONTRIBUTING.md) has the steps. [AGENTS.md](AGENTS.md)
describes the layout, the rule in full, and how to probe clang-repl safely.

## License

MIT. See [LICENSE](LICENSE).
