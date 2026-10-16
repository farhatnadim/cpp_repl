---
name: run-cpp
description: Run C++ without a build through cpp-repl, the C++ REPL on clang-repl, and read what it prints - check what a standard-library or library call returns before stating it, run a .cpp file's main() and inspect its variables afterwards, try a snippet and iterate on its compiler errors, or time a statement. Use this before making a claim about C++ behaviour that a run can settle, when the user asks to try, check or run C++ code or a C++ library's API at a REPL, or names cpp-repl, clang-repl or a project's REPL launcher such as repl/usd-repl. Not for building a CMake project, and not for making a library loadable or fixing a pasted cpp-repl error such as "Symbols not found" - the add-library skill does that.
argument-hint: "[file.cpp | file.repl | the C++ to try]"
allowed-tools:
  - Bash(cpp-repl *)
---

# Run C++ in cpp-repl

cpp-repl runs C++ through clang-repl's JIT. Inputs are statements as typed
inside `main()`, and a bare expression, with no semicolon, prints its value. A
run takes about a second. The first start of a configuration takes a few
seconds more, since it precompiles the headers.

Run it non-interactively. Its prompt waits for a terminal that you do not
have.

## 1. Which command

Pick the first that applies:
1. **The project's own launcher.** AGENTS.md or the README may name one, such
   as openusd-tutorial's `repl/usd-repl`, which adds the profile, the build's
   flags and a lesson's working directory. Where its `--help` and this skill
   differ, its `--help` wins.
2. **A `cpp-repl.toml`** in the working directory or above. Plain `cpp-repl`
   reads it without saying so; `--json` names the file it read.
   `--no-config` leaves it out, for a check of the standard library alone.
3. **A built-in profile:** `-p usd`, `-p eigen` or `-p glm`.
4. **Any other library** is not set up yet. Run the add-library skill first.

Inside Claude Code, the plugin's `bin/` is on PATH, so `cpp-repl` is the
plugin's copy. Inside a cpp_repl checkout, run `bin/cpp-repl`, so the working
tree runs.

This skill pre-approves commands that start with `cpp-repl`. A launcher, or
`bin/cpp-repl`, asks for permission as usual.

## 2. One question, one run

Pipe the inputs in, without `--batch`:

```
$ cpp-repl --no-config <<'EOF'
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

- **Echo.** Each input is echoed with its prompt, so each output sits under
  the input that printed it. `-q` leaves the echo out; cpp-repl's own `[...]`
  notes stay.
- **`--batch` runs files, and only files:** `cpp-repl --batch check.repl`.
  Given no files, it exits 2.
- **`--timeout 60`** stops an input that runs too long, rebuilds the session
  without it, and goes on. The run then exits 124.
- **Scratch files** (`.repl` inputs, `.cpp` copies) go in a scratch or
  temporary directory, unless the user wants them in the project.

## 3. A program, then its variables

`cpp-repl --batch prog.cpp` runs everything `prog.cpp` declares, then
`main()`'s body one statement at a time. Nothing is built.

A `return` in `main()` ends the program, as it does compiled. A file named
after it does not run, and the run says so:
`[main returned 0: more.repl not run]`. To read `main()`'s variables after it
has run, keep a session that stays:

```
cpp-repl --no-config --serve repl.sock > serve.log 2>&1 &    # or your tool's background mode
for i in $(seq 100); do test -S repl.sock && break; sleep 0.1; done
cpp-repl --connect repl.sock prog.cpp
cpp-repl --connect repl.sock <<'EOF'
median
samples.size()
EOF
cpp-repl --connect repl.sock <<< '%quit'
```

- **`main()`'s declarations are the session's globals,** so they outlive its
  `return`. The `return` ends only that run.
- **Configuration goes on the `--serve` line:** `-p`, `-c`, flags, libraries
  and `--cwd`. `--connect` takes only `--timeout`, `--stop-on-error`,
  `--diagnostics`, `-q`, `--json` and `--fresh`. Anything else exits 2.
- **`--fresh`** runs in an empty session, and the session stays empty for
  later runs.
- **Files.** `--serve` with a file exits 2: send files with `--connect`.
- **The socket.**
  - It appears once the session is ready.
  - Run both commands from one directory: each resolves a relative path
    against its own.
  - Keep the path short (AF_UNIX allows 107 bytes).
  - `%save FILE` and `%save-cpp FILE` write relative to the server's
    directory.

Without a server, copy `prog.cpp` to a scratch file and print what you need
before its `return`.

## 4. Reading the result

- **The exit status is the program's:**
  - what `main()` returned, or the N of `exit(N)`;
  - 128 + the signal for a crash: 139 for SIGSEGV, and 134 for `abort()` or
    an exception nothing caught (`[uncaught exception: std::out_of_range:
    ...]`);
  - otherwise 124 when an input ran past `--timeout`;
  - otherwise 1 when an input failed to compile or link;
  - otherwise 0.

  Exit 2 means cpp-repl could not start: no clang-repl 23 or newer, a flag it
  does not pass on, a missing file. Its message on stderr says which.
  `--docker` runs it in an image when the host's clang-repl is too old and an
  image exists (`docker images 'cpp-repl*'`).
- **Errors are brief off a terminal:** each input's first error, with its
  notes, at `<stdin>:LINE:COL` or `prog.cpp:LINE:COL`. `--diagnostics full`
  shows everything clang prints.
- **`--json`** writes one object per input:
  - always: `input`, `status`, `seconds` and `output`;
  - when they apply: `file`, `line`, `run_seconds`, `notes` and `returned`;
  - `status` is one of `ok`, `error`, `link`, `exception`, `crash`, `exit`,
    `stuck`, `timeout` or `interrupted`;
  - the last line is `{"exit": N, "inputs": N, "failures": N, "skipped": N}`.

  When cpp-repl cannot start there is no last line: read stderr.
- **A crash** (`[clang-repl crashed (SIGSEGV)]`) ends a batch or piped run.
  A served session restores itself for the next run, without the input that
  crashed.

## 5. Writing inputs

- **A bare expression prints its value.** That covers containers, maps,
  optionals, tuples, the profiles' types, and any type with a non-member
  `operator<<`. Followed by a semicolon, it runs as a statement and prints
  nothing.
- **A name can be declared again.** The session is rebuilt without the old
  declaration, with a note.
- **Variables typed at the prompt are globals.**
  - A C library name clashes: `int index = 3;` fails.
  - `show` and `keep` are the prelude's.
  - A lambda cannot capture a global. cpp-repl drops `[x]`, `[&x]`, `[=]` and
    `[&]` with a note, and the lambda reads the global as it is when called.
  - An init-capture that is not a constant (`[v = base]`) holds zero, which
    is a clang-repl limit. Use a constant, a `std::function`, or a function.
- **`return N;`** ends the program with N.
- **Multi-line blocks** group into inputs as they would when typed: an `if`
  waits for its `else`, a function head for its body.

## 6. Handing code back

When the user wants to keep what worked, `%save-cpp FILE` writes the session as
a program. Includes and definitions go at file scope, the rest in `main()`,
and bare expressions become `show(...)` calls. Build it with the session's own
options:

```
clang++ $(cpp-repl -p eigen --cflags) saved.cpp $(cpp-repl -p eigen --libs)
```

## 7. Report

Say what you ran, and quote the lines that answer the question. A claim goes no
further than the run: one clang-repl, one library version, the inputs shown.
When the run could not start (exit 2), pass its message on, together with the
way out it names.

## Rules

- **Never start the interactive prompt or raw clang-repl.**
  - The prompt waits for a terminal.
  - Raw clang-repl hangs on a line that ends in an open `{`, and crashes on
    inputs that cpp-repl rewrites.
- **Leave `XDG_STATE_HOME` alone** unless cpp-repl exits 2 with "cannot write
  the state directory" (a read-only HOME, as in a sandbox). Then point it at
  a writable directory and run again. cpp-repl keeps its precompiled headers
  there, and a new directory builds them again.
- **Never delete `~/.clang-repl-history` or clang-repl reports in
  `/var/crash`.** Other sessions on the machine write them.
- **Install nothing.** Installing clang-repl 23 (apt.llvm.org) or building
  cpp-repl's Docker image is the user's call: give the command.
