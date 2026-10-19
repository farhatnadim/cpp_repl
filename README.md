# cpp-repl

A C++ REPL on top of LLVM's
[clang-repl](https://clang.llvm.org/docs/ClangRepl.html), for the standard
library, your project and any library. You type C++ as you would inside
`main()`:

- **Blocks finish themselves.** Enter runs an input once its brackets balance
  and its statement is complete, and opens a new line otherwise: a multi-line
  `if` waits for its `else`, a function head for its body.
- **Values print.** A bare expression prints its value: containers, maps,
  tuples, optionals, variants, smart pointers, your own types through their
  `operator<<`, and a profile's types, an Eigen matrix or an OpenUSD stage.
- **A crash costs a replay.** A segfault, a Ctrl-C or a link failure replays
  the session's journal into a spare clang-repl already running, every
  variable with it. An exception nothing catches is named and ends the
  program, not the session.
- **Names can be declared again.** `int x = 1;` then `double x = 2.5;`
  rebuilds the session without the old `x`, and says so.
- **Programs and sessions convert both ways.** `cpp-repl prog.cpp` runs
  `main()` one statement at a time and leaves you at the prompt, its
  variables in scope; `%save-cpp` writes the session back out as a program
  that builds.
- **It runs at native speed.** With `-O3 --native`, Eigen typed at the prompt
  multiplies matrices as fast as clang++-compiled Eigen ([Speed](#speed)).
  `%timeit` times a statement as IPython does.
- **It points at your project.** `compile_commands.json`, a `cpp-repl.toml`,
  compiler flags and shared libraries. Profiles load Eigen, glm, libtorch
  and OpenUSD.
- **It runs without a terminal.** `--batch`, `--json`, `--timeout` and a
  session that stays, for scripts and coding agents, with errors kept brief
  and pointed at your lines.
- **It runs clang-repl 23 and newer,** working around what each version gets
  wrong ([Why a front end](#why-a-front-end)); Docker images carry the
  release and LLVM's snapshot.

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

## Install

Linux, Python 3.11 or newer, and clang-repl 23 or newer, or Docker.

```sh
WHEEL=https://github.com/farhatnadim/cpp_repl/releases/download/v0.2.0/cpp_repl-0.2.0-py3-none-any.whl
pip install $WHEEL           # or pipx install $WHEEL, or uv tool install $WHEEL
cpp-repl                     # the newest clang-repl on PATH or in /usr/lib/llvm-N/bin (apt.llvm.org: clang-23 and clang-tools-23)
cpp-repl --docker            # without one: in an image with clang-repl 23, which it offers to build (about a minute)
```

- **Releases.** Each [release](https://github.com/farhatnadim/cpp_repl/releases)
  carries the wheel and the sdist. [docs/platforms.md](docs/platforms.md)
  says where cpp-repl runs (Linux; WSL2 or Docker Desktop on Windows and
  macOS) and how `scripts/smoke.sh` tests a release on a machine.
- **Docker.** `--docker` mounts the directory it runs in, the session's
  working directory, the package and whatever your configuration reads, each
  at its own path, so paths mean the same inside the container and out.
  `--image TAG` picks another image (default `$CPP_REPL_IMAGE`, then
  `cpp-repl:llvm23`), `--mount DIR` mounts more, and `--dry-run` prints the
  `docker run` command. The images hold the toolchain alone: clang-repl 23
  from apt.llvm.org, LLVM's development snapshot (`make docker-snapshot`),
  or a build from any llvm-project ref (`make docker-src`);
  [src/cpp_repl/docker/README.md](src/cpp_repl/docker/README.md) has them.
- **From a clone,** `bin/cpp-repl` runs the checkout as it is, and
  `uv run cpp-repl` runs it through uv's own environment.

Input goes through prompt_toolkit, with highlighting as you type, and output
is coloured (`--no-color` or `NO_COLOR=1` turns it off).

## Where to go next

| | |
| --- | --- |
| [docs/tutorials/](docs/tutorials/README.md) | six walks of a few minutes each: a first session, programs without a build, libraries and your project, scripts and agents, the Claude Code plugin, libtorch on the GPU |
| [examples/](examples/) | one session per profile, run as its first line says: `cpp-repl -p eigen examples/eigen.repl` |
| `cpp-repl --help`, `%help` | every option, and the commands at the prompt |
| [With Claude Code](#with-claude-code) | the plugin and its four skills, for an agent that runs C++ or adds a library |
| [compare/](compare/README.md), [bench/](bench/README.md) | raw clang-repl against cpp-repl, one session side by side; the matrix-product benchmark |
| [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) | how a change lands; the layout, the commands, and the rules for agents |

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

- **Enter** runs the input once it is complete; **Esc then Enter** runs a
  block as it stands. A pasted block can mix `#include` lines and code, and
  if it ends in a bare expression, its value prints.
- **Tab** completes names from every header the preludes include and the
  session's own names. **Ctrl-R** searches the history.
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
| `%time INPUT` | run INPUT, then print its wall time and how long the code itself ran, compiling apart |
| `%timeit INPUT` | time INPUT in a loop, as IPython does |
| `%flags` | the session's flags, preludes and libraries |
| `%help`, `%quit` | |

The other options: `--cwd DIR` is the session's working directory, where
quoted includes are looked for and the program's files land; `--no-config`
leaves any `cpp-repl.toml` unread; `--clang-repl PATH` names the clang-repl
to run (default `$CPP_REPL_CLANG_REPL`, then the newest found);
`--build-dir DIR` is `--compile-commands DIR`; `--no-pch` starts from the
prelude headers rather than the precompiled header of them
([Start-up](#start-up)).

### Files

```sh
cpp-repl prog.cpp            # run prog.cpp's main(), then keep the prompt (on a terminal)
cpp-repl --batch prog.cpp    # run it and exit, with the program's exit status
cpp-repl --emit prog.cpp     # print the inputs prog.cpp becomes
cpp-repl a.repl              # a session saved with %save
```

A C++ source becomes inputs: each `#include`, `#define`, `#if` region and
file-scope declaration is one, and `main()`'s body runs one statement at a
time after everything else in the file, as in the compiled program, where
the globals are initialised before `main()` too. Its `argc` and `argv` get
stand-in values. A `return` in `main()` ends the program with its value, and
the files named after it do not run: `[main returned 0: more.repl not run]`.
Quoted includes next to the source are found, and `CPP_REPL` is defined, so
a source can tell where it runs.

Each input reaches clang at its own line and column, so an error names the
place in the file, `prog.cpp:12:5`, and so do `__FILE__` and `__LINE__`. The
code reads cpp-repl's stdin, as the compiled program would:
`cpp-repl --batch prog.cpp < input.txt`.

`%save-cpp FILE` writes the session as a program: includes and definitions
at file scope, the rest in `main()`, bare expressions as `show(...)` calls. A
variable that a function or lambda typed later uses stays a global, as it
was at the prompt, and the file says which. Build it with the session's own
flags:

```sh
clang++ $(cpp-repl -p eigen -O2 --cflags) saved.cpp $(cpp-repl -p eigen -O2 --libs)
```

### In a script or a coding agent's loop

```sh
cpp-repl <<'EOF'                               # inputs piped in: each echoed, then its output
std::vector<int> v{3, 1, 2};
std::ranges::sort(v);
v
EOF
cpp-repl --batch -q --timeout 30 prog.cpp      # what prog prints, and its errors; -q leaves out the echo
cpp-repl --batch --json prog.cpp               # a JSON object per input
cpp-repl --serve repl.sock -p eigen &          # a session that stays: --connect runs files, or stdin, in it
cpp-repl --connect repl.sock -q --fresh prog.cpp
echo 'm.rows()' | cpp-repl --connect repl.sock
cpp-repl --mcp                                 # an MCP server on stdin and stdout, as the plugin runs it
```

- **Errors are brief** with `--batch` or input piped in: each input's first
  error with its notes, carets only in your code, no include chains, three
  missing symbols demangled. A `std::sort` of a type with no `operator<`
  costs 10 lines instead of 238. `--diagnostics full` shows everything.
- **The exit status is the program's:** `main()`'s return, `exit(N)`'s N, or
  128 + the signal that killed it (139 for a segfault, 134 for `abort()` and
  an uncaught exception); else 130 after Ctrl-C, 124 after a `--timeout`, 1
  when an input failed to compile or link, 0 otherwise; 2 when cpp-repl
  could not start. `--stop-on-error` stops at the first failure.
- **`--json`** writes one object per input: `input`, `file`, `line`,
  `status` (`ok`, `error`, `link`, `crash`, `exit`, `stuck`, `timeout`,
  `interrupted`, `exception`), `seconds`, `run_seconds`, `output`, `notes`
  and `returned`; between inputs, `{"output": TEXT}` or `{"note": TEXT}`; a
  last line `{"exit": N, "inputs": N, "failures": N, "skipped": N}`.
- **A session that stays.** `--serve SOCKET` keeps a session running, its
  profiles loaded, and `--connect SOCKET [FILE...]` runs in it in 0.07 s,
  against about half a second for a new cpp-repl. Names carry over from run
  to run, and a `return` from `main()` ends only that run, so a program's
  variables can be read after it. `--fresh` runs in an empty session, in the
  spare the server keeps warm. The flags, profiles and libraries are the
  server's; `--connect` takes `--timeout`, `--stop-on-error`,
  `--diagnostics`, `-q`, `--json` and `--fresh`. `%quit` stops the server.
- **An MCP tool.** `--mcp` serves the session to a [Model Context
  Protocol](https://modelcontextprotocol.io) client on stdin and stdout, with
  the tools `run_cpp`, `run_cpp_file`, `save_cpp` and `reset_session`. A
  result is the transcript a piped run prints, and `isError` marks a failed
  input. The session stays between calls, as with `--serve`. The Claude Code
  plugin declares this server.

[Tutorial 04](docs/tutorials/04-scripts-and-agents.md) shows each with its
output, and the [run-cpp skill](skills/run-cpp/SKILL.md) says the same for
an agent.

### Speed: -O and --native

The JIT compiles at **-O0** unless asked otherwise: quick to compile, and
about 300 times slower to run on Eigen's matrix product. `-O2` or `-O3`
turns the optimiser on, and `--native` (`-march=native`) lets it use AVX and
FMA. With both, code typed at the prompt runs as fast as clang++'s
([Speed](#speed)); `%timeit` says which level is in use.

```sh
cpp-repl -p eigen -O3 --native
```

### Start-up

The first start of a configuration precompiles the prelude headers, with the
clang++ beside clang-repl, into `~/.local/state/cpp-repl/pch/`; every start
after that loads them. On clang-repl 23.1.4:

| Start, inside the image | From the headers | Precompiled | The first start |
| --- | ---: | ---: | ---: |
| the standard preludes | about 2 s | 0.53 s | 2.2 s |
| `-p usd` | 4.8 s | 1.05 s | 5.6 s |
| `-p torch`, on the host, the CUDA context included | 12 s | 3.5 s | 15 s |

A configuration is the clang-repl, the flags, the preludes' contents and the
libraries; an entry unused for a month goes, and `rm -r
~/.local/state/cpp-repl/pch` is always safe. A header that changed is
noticed by clang, and the entry is rebuilt once. `%flags` names the
precompiled header in use, or why there is none; `--no-pch`, or `pch =
false` in a `cpp-repl.toml`, starts from the headers.

## Your project, your libraries

Compiler flags go to clang as they are: `-I`, `-isystem`, `-iquote`,
`-idirafter`, `-D`, `-U`, `-O0` to `-O3`, `-std=`, `-stdlib=libc++`,
`-include FILE` (a prelude of your own), `-march=`, `-f...`, `-W...`, `-g`,
`-pthread`, the sysroot and toolchain flags; `-fsanitize` is refused, since
the JIT cannot run sanitised code. Shared libraries load with `--lib PATH`,
or with `-l NAME` and `-L DIR`, found as the linker finds them. With
`-stdlib=libc++`, cpp-repl loads libc++'s runtime first; the images carry
it, and with it the C++23 library (`std::println`, `std::expected`,
`std::mdspan`) that libstdc++ 13 lacks.

```sh
cpp-repl -I include -DFAST=1 -L build -l mylib -O2
cpp-repl --compile-commands build      # flags as your build compiles: -I, -D, -std, -m, -include
```

From `compile_commands.json`, cpp-repl takes the flags of the entry for the
file you load, or the first entry: include directories, definitions, the
standard, `-stdlib=`, sysroot and toolchain, `-m` flags, the `-f` flags that
change the language, a forced `-include` as a prelude, and its `-W` flags,
`-Werror` apart. The optimisation level stays the REPL's own.

A `cpp-repl.toml` in the working directory, or any directory above it, says
the same once for a project. It is a build file: its `libs` load and its
`prelude` runs at startup, so one owned by another user is left alone, with
a note. `-c FILE` reads another; paths in it are relative to the file, and a
key that takes a list takes one string too:

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

The precedence, from lowest to highest: profiles, `compile_commands.json`,
`cpp-repl.toml`, `-c` files, the command line. Without a `-std`, the session
compiles as the newest standard its clang knows (gnu++26 from clang 17);
C++20 is the floor, and a lower `-std` is raised. Headers that use what
C++26 removed (OpenCV 4.6) compile with `std = "gnu++20"`.

### Profiles

| Profile | What it adds |
| --- | --- |
| `-p eigen` | `<Eigen/Dense>`, found through `EIGEN_ROOT`, pkg-config or the usual places. A matrix prints with its shape; past 10 rows or columns, only the first and last three. A comma initialiser (`m << 1, 2, 3`) prints the matrix it filled; quaternions, angle-axis rotations and transforms print too. |
| `-p glm` | `<glm/glm.hpp>` with the matrix-transform and quaternion extensions, found through `GLM_ROOT`, pkg-config or the usual prefixes. A vector prints as `vec3(1, 2, 3)`, a quaternion as `quat(w, x, y, z)`, a matrix as its rows. |
| `-p torch` | libtorch, PyTorch's C++ API: `<torch/torch.h>`, its libraries, and the GPU when the install has CUDA (`torch::cuda::is_available()`). A tensor prints as libtorch prints it, values then `[ CUDAFloatType{2,3} ]`; past a thousand elements, its first and last values. An error prints the first line of `c10::Error`'s message, and `repl::thrown()` holds the backtrace. The install is found through `TORCH_ROOT` (the libtorch zip, or a pip install's `site-packages/torch`), then the active virtual environment, then the Python running cpp-repl (a `uv tool install`'s own Python has no torch: set `TORCH_ROOT`, or activate the venv). |
| `-p usd` | OpenUSD's libraries and `using namespace pxr`. A stage prints its usda; a prim its type, properties and children; an attribute its value and time samples. `tree(stage)` prints the hierarchy. The install is found through the `CMakeCache.txt` next to `compile_commands.json`, then `USD_ROOT` or `PXR_ROOT`, then the usual places. The profile asks for C++20, since OpenUSD's headers do not build as C++23 or later; a `-std` of yours still wins. |

A profile of your own is a `cpp-repl.toml`-style file, loaded with `-c`;
the [add-library skill](#with-claude-code) writes one. To make your types
print, give them a non-member `operator<<` in their own namespace; cpp-repl
finds one typed after the type first printed, in a list too. For a display
of more than one line, specialise `repl::Show<T>`; for one line inside a
list, `repl::Put<T>`. See `src/cpp_repl/prelude/cpp_repl.h`, and
`src/cpp_repl/profiles/eigen/cpp_repl_eigen.h` for an example.

### Environment

| Variable | What it does |
| --- | --- |
| `CPP_REPL_CLANG_REPL` | the clang-repl to run, before the newest found (`--clang-repl` wins) |
| `CPP_REPL_IMAGE` | the image `--docker` runs (`--image` wins) |
| `CPP_REPL_TRACES=1` | keep LLVM's stack trace when clang-repl crashes; by default cpp-repl reports the crash in one line |
| `NO_COLOR` | no colour, as `--no-color` |
| `XDG_STATE_HOME` | where the state directory goes: `$XDG_STATE_HOME/cpp-repl`, default `~/.local/state/cpp-repl`. It holds the prompt's history, the completion cache, the precompiled preludes (`pch/`), the preload library, the patched TBB headers of `-p usd`, each session's input files, `--docker`'s home, and the `home` and `tmp` the code sees. A read-only one stops the start with exit 2: point it at a writable directory |
| `LIBRARY_PATH` | directories `-l NAME` looks in, after `-L` |
| `USD_ROOT` or `PXR_ROOT`, `EIGEN_ROOT` or `EIGEN3_INCLUDE_DIR`, `GLM_ROOT`, `TORCH_ROOT` | where a profile's library is, before pkg-config and the usual places |

The code's own `HOME` and `TMPDIR` are the state directory's `home` and
`tmp`, where clang-repl's line history and a library's fatal-error stacks
go; `LD_PRELOAD` is not passed on.

## With Claude Code

This repository is also a [Claude Code](https://claude.com/claude-code)
plugin. Its skills and its MCP server run C++ for an agent, and its skills
add a library to cpp-repl:

```
/plugin marketplace add farhatnadim/cpp_repl
/plugin install cpp-repl@cpp-repl
```

The same from a shell: `claude plugin install cpp-repl --marketplace
farhatnadim/cpp_repl`. `claude plugin marketplace update cpp-repl`, then
`claude plugin update cpp-repl@cpp-repl`, brings in a newer one.

| Skill | What it does |
| --- | --- |
| `/cpp-repl:run-cpp` | Runs C++ without a build and reads what it prints: what a call returns, a `.cpp` file's `main()` and its variables afterwards, a snippet's errors, a statement's timing. It picks the project's launcher, its `cpp-repl.toml` or a built-in profile, and runs cpp-repl without the prompt, which waits for a terminal an agent does not have. |
| `/cpp-repl:add-library NAME` | Writes `.cpp-repl/NAME.toml`, a prelude with printers, and an example. Then checks them: the prelude compiles, the session starts, every input runs, the shared libraries are really used, and a `%save-cpp` of the example builds and prints the same. |
| `/cpp-repl:add-printer` | Makes a type print well: a readable display on its own, and one line inside a list. |
| `/cpp-repl:contribute-profile NAME` | In a clone of this repository, turns a working profile into a built-in `-p NAME`, with its tests. The `glm` profile was made this way. |

The plugin also declares an MCP server, `cpp-repl --mcp`, with the tools
`run_cpp`, `run_cpp_file`, `save_cpp` and `reset_session`: Claude Code calls
cpp-repl directly, in one session that stays for the conversation, and the
run-cpp skill says when. Any MCP client runs the same command
([An MCP tool](#in-a-script-or-a-coding-agents-loop)).

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
- **Contributors load their own clone** with `claude --plugin-dir .`, with
  the installed copy turned off in `.claude/settings.local.json`
  (`{"enabledPlugins": {"cpp-repl@cpp-repl": false}}`).
- **[Tutorial 05](docs/tutorials/05-claude-code.md)** walks through each
  skill with replies from real runs.

### The skills, for any agent

Each skill is a directory under [skills/](skills/): its `SKILL.md` is the
procedure, `reference/` the detail it points at, `scripts/` what it runs
and `templates/` what it starts from. They are Markdown and Python, so an
agent without the plugin reads them the same way, and
[AGENTS.md](AGENTS.md) says how any agent runs cpp-repl.

| Skill | Read | Run |
| --- | --- | --- |
| [run-cpp](skills/run-cpp/SKILL.md) | which command fits, one run, a program then its variables, reading the result, handing code back, the rules | `cpp-repl` with inputs piped in, `--batch`, `--serve` and `--connect` |
| [add-library](skills/add-library/SKILL.md) | eight steps from the toolchain to the report; `reference/resolve.md` for what `resolve` found, `troubleshooting.md` by the check's hints, `docker.md` for a derived image | `scripts/probe_library.py` (`env`, `find`, `resolve`, `image`, `symbol`) and `scripts/check_profile.py`; the `templates/` the files start from |
| [add-printer](skills/add-printer/SKILL.md) | which printer to write; `reference/contract.md` for how cpp-repl prints a value, `patterns.md` for Eigen, OpenUSD, OpenCV, glm and enums worked through | add-library's `check_profile.py` again |
| [contribute-profile](skills/contribute-profile/SKILL.md) | the steps to a built-in `-p NAME`; `reference/checklist.md` for every file touched, `discovery.md` for how a profile finds its library | `make check`; the `templates/` for the module, the test and the golden session |

## Why a front end

clang-repl is a real C++ JIT, but typed at directly it hangs, crashes or
misprints on everyday input. In what follows, *raw* means clang-repl alone,
fed over a pipe by [`tests/compat/harness.py`](tests/compat/harness.py), and
*cpp-repl* the same input typed into cpp-repl.

<!-- compat-table -->
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
<!-- /compat-table -->

The way around each problem:
- **The unclosed `{`:** cpp-repl never sends an incomplete input.
- **Loops, `try` and lambdas:** cpp-repl runs these inside a function of
  their own. Lambdas at top level are a crash in 23.1, fixed on main.
- **Printing a value:** cpp-repl always calls its own printer, which is how
  class values print in every version.
- **Crash reports:** cpp-repl turns core dumps off before any library loads,
  so a crash leaves no report behind.
- **`return` at top level:** cpp-repl runs it inside a function, where it
  ends the program with its value; typed raw, it segfaults 23 and 24.
- **A file-scope lambda's init-capture:** a clang-repl limit cpp-repl cannot
  lift ([Limits](#limits)): the closure's initialiser never runs unless the
  capture is a constant.

[compare/](compare/README.md) types one whole session into raw clang-repl
and into cpp-repl and shows both answers side by side, with the start-up
and the cost of an input measured on each clang-repl.

## Speed

How fast does C++ typed at the prompt run, against native C++ and NumPy?
The workload is a dense matrix product of n × n doubles: the same Eigen
source compiled by clang++ 23, typed into cpp-repl over clang-repl 23, and
as a kernel compiled ahead of time and called from the REPL; NumPy with
OpenBLAS. Three rounds, pinned cores, validity checks on every result, and
hypotheses written down before the first run. Everything is in
[bench/](bench/README.md).

![GFLOP/s against n](bench/results/nadim-Alienware-Aurora-Ryzen-Edition-llvm23-20261014-0125Z-gflops.png)

| One thread, n = 1024 | GFLOP/s |
| --- | ---: |
| Eigen, compiled by clang++ -O3 -march=native | 64.0 |
| Eigen typed into cpp-repl -O3 --native | 64.4 |
| cpp-repl calling a kernel compiled ahead of time | 63.1 |
| NumPy (OpenBLAS) | 60.7 |
| cpp-repl with no flags (-O0) | 0.2 |

- **Tuned, the JIT is the compiler.** With `-O3 --native`, Eigen at the
  prompt runs at 0.98–1.02 of native at every size from 32 to 3072, and
  OpenMP inside the JIT at 0.97–1.00 of native with 16 threads.
- **Untuned, it is about 300× slower.** The default is -O0, and leaving out
  `--native` costs almost two thirds of the speed.
- **What the REPL costs is compile time, not run time.** Cold start to the
  first product: 3.2 s, against 2.5 s to compile and run natively. In a
  running session, calling the kernel again costs 0.08 s, and a new product
  expression 0.72 s to compile.

## Agents

[bench/agents/](bench/agents/README.md) asks whether a coding agent solves
C++ tasks more often, faster or cheaper with cpp-repl. Claude Code runs
Terminal-Bench 4.0's C++ tasks through Harbor in three arms: cpp-repl as an
MCP tool, cpp-repl as the run-cpp skill, and cpp-repl unmentioned. The
harness is in place, and its sanity checks pass on this machine; the agent
trials are still to run.

## What C++ you can type

The C++20 language and the C++23 language where the clang has it, the
standard library of C++20 without an `#include` (containers, ranges and
views, `std::format`, `std::variant`, `std::thread`, `std::chrono`,
`std::span`), and an uncaught exception, a `return` from `main()`, `exit()`
and a crash, each ending the program as it does compiled. Every construct
was typed into cpp-repl on clang-repl 23.1.4 and the 24 snapshot with the
same result (`tests/sessions/features.session` holds the transcript).

What a version lacks is the standard library's: `<print>` and `<mdspan>`
need libstdc++ 14 or libc++ (`cpp-repl --docker -stdlib=libc++`),
`<flat_map>` libstdc++ 15 or libc++ 20, `<generator>` libstdc++ 14, and
cpp-repl says so when an `#include` of them fails. `import std;` has no
module file in any of them, and cpp-repl says so.

## Limits

- **Variables typed at the prompt are globals.** A name can clash with a C
  library function: `int index = 3;` fails. A lambda at file scope can
  neither name a global in its capture list nor have a capture-default, so
  `[x]`, `[&x]`, `[=]` and `[&]` go, cpp-repl says so, and the lambda reads
  the global as it is when called.
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
  x = parse("bad");`) is named and still ends the session, which is
  rebuilt; one thrown by a statement is caught and named, unless the
  statement starts with a macro's name (`TF_DEFINE_PRIVATE_TOKENS(...)`),
  which runs at file scope since it may declare something.
- **`[clang-repl crashed (SIGSEGV)]` covers both the program and the
  compiler crashing.**
- **The prelude's global names** are `show` and `keep` (`timeit` and the
  rest live in `repl::`): a variable of your own named `keep` fails.
- **The first start of a configuration takes a few seconds,** precompiling
  the preludes ([Start-up](#start-up)); after that about half a second.
  `--no-pch`, and a clang-repl with no clang++ beside it, parse the preludes
  at every start: about 2 s, and OpenUSD adds 3 s. Docker adds about 0.15 s.
- **Your headers must compile with your clang-repl.** OpenUSD's do not build
  as C++23 or later, so the `usd` profile compiles as C++20, and reads a
  patched copy of TBB 2020's headers, which clang 20 and newer reject.
- **C++20 is the floor.** The default is the newest standard the clang knows.

## How it works

- **[`lexer.py`](src/cpp_repl/lexer.py)** finds where inputs and statements
  end, and decides what clang-repl gets: control flow, lambda statements and
  expression statements inside a function of their own, where a `return`
  ends the program and an exception is caught; declarations at file scope;
  each input through a temporary file and an `#include`, after a `#line`
  directive, at its source's lines and columns; a bare expression through
  `repl::print_expr`.
- **[`backend.py`](src/cpp_repl/backend.py)** runs clang-repl over a pipe.
  After each input it sends `%lib` of a path that cannot exist; the error
  clang-repl prints marks where the input's output ends. At startup it
  learns the marker's text and any prompt.
- **[`session.py`](src/cpp_repl/session.py)** journals the inputs that
  worked and, after a crash, a Ctrl-C, a link failure, `%undo` or a name
  declared again, replays them into a spare clang-repl started in advance.
- **[`diagnostics.py`](src/cpp_repl/diagnostics.py)** filters what clang
  prints: brief or full.
- **[`server.py`](src/cpp_repl/server.py)** is `--serve` and `--connect`.
- **[`mcp.py`](src/cpp_repl/mcp.py)** is `--mcp`: the session served to an
  MCP client on stdin and stdout. The plugin declares it.
- **[`pch.py`](src/cpp_repl/pch.py)** precompiles the preludes once per
  configuration, and **[`preload.py`](src/cpp_repl/preload.py)** loads the
  session's libraries into clang-repl as it starts, core dumps off first.
- **[`prelude/cpp_repl.h`](src/cpp_repl/prelude/cpp_repl.h)** is the C++
  every session starts from: `show`, `Show<T>`/`Put<T>`, `keep` and
  `timeit`. Profiles add their own headers.

cpp-repl began as `repl/usd-repl` in
[openusd-tutorial](https://github.com/farhatnadim/openusd-tutorial), a REPL
for learning OpenUSD's C++ API, and that launcher now runs cpp-repl's usd
profile; `scripts/parity.py` checks that cpp-repl runs the tutorial's 20
lessons as their compiled programs do.

## Related work

| Project | What it is | Difference |
| --- | --- | --- |
| [cling](https://github.com/root-project/cling) | CERN's C++ interpreter (ROOT), on its own LLVM fork | Mature and full-featured, but it is LLVM 20 and its own build. cpp-repl uses stock clang-repl, and any of them. |
| [xeus-cpp](https://github.com/compiler-research/xeus-cpp) | A Jupyter kernel over CppInterOp and clang-repl, with a WebAssembly build | Notebooks rather than a terminal. CppInterOp supports up to LLVM 22. |
| [clang-repl-kernel](https://pypi.org/project/clang-repl-kernel/) | A Python Jupyter kernel over clang-repl | Tested with clang 18. |

## Development

```sh
make check         # before a pull request: every test against the host's clang-repl (23 or newer), the wheel, then claude plugin validate
make check-docker  # the same inside cpp-repl:llvm23, built first: what CI runs
make test          # unit and fake-backend tests: no clang-repl needed
make test-host     # every test against the host's clang-repl
make test-docker   # every test inside cpp-repl:llvm23 (make docker first)
make wheel-check   # build the wheel with uv and run cpp-repl from it
make tutorials     # docs/tutorials' transcripts against runs (make tutorials-update rewrites them)
make parity        # openusd-tutorial's lessons through cpp-repl -p usd and compiled, diffed
make compat        # probe the host's clang-repl; make compat-docker for the images; make compat-table for the README's table
make track         # what track.yml runs weekly: the snapshot image, the suite and the probes in it, the probes diffed
make bench-quick   # a 3-minute benchmark run (make docker-bench first)
make version VERSION=x.y.z   # on an issue branch: set the version, then make pr
make tag           # on main after the merge: tag vX.Y.Z and push it; release.yml makes the GitHub release
claude plugin validate .                                 # the plugin and its skills
evals/headless.py --case yaml-cpp                        # one skill eval, headless (real model calls)
```

The suite runs on clang-repl 23.1.4 (`make test-docker`) and the 24 snapshot
(`make test-snapshot`): unit tests, a fake clang-repl that prints prompts,
splits its output and crashes on cue, golden transcripts, and integration
tests for crashes, Ctrl-C, link failures and `%save-cpp` round trips.
[`track.yml`](.github/workflows/track.yml) runs it every Monday against
clang-repl's snapshot and release, with the compat probes, and files an
issue when a verdict, a test or apt.llvm.org's newest major changes.

Changes land by pull request from an issue on the project board;
[CONTRIBUTING.md](CONTRIBUTING.md) has the steps, and [AGENTS.md](AGENTS.md)
the layout, the commands, the release steps and how to probe clang-repl
safely.

## License

MIT. See [LICENSE](LICENSE).
