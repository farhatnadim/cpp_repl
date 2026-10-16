---
name: add-library
description: Add a C++ library to cpp-repl (the C++ REPL on clang-repl) so that it works at the prompt. It finds the library's headers and shared libraries, writes a profile (a TOML file and a prelude header) in the user's project, makes the library's main types print readably, and proves it all with a checked example session. Use this whenever someone wants to try, explore, prototype with or learn a C++ library interactively (OpenCV, OpenUSD, fmt, spdlog, Boost, glm, PCL, yaml-cpp, Armadillo, their own project's library), asks for a cpp-repl or clang-repl profile, or pastes a cpp-repl error after loading a library, such as "Symbols not found", "file not found", "clang-repl failed to start", or a built-in profile (-p usd, -p eigen, -p glm) that cannot find its install.
argument-hint: <library> [cmake package | pkg-config module | include dir]
allowed-tools:
  - Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" *)
  - Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/check_profile.py" *)
  - Bash(cpp-repl *)
---

# Add a library to cpp-repl

cpp-repl runs C++ through clang-repl's JIT. To use a library at its prompt it
needs four things, and each one fails in its own way when it is wrong:

- **the headers**, parsed once at startup from a prelude header;
- **the shared libraries**, loaded before the first input. A static archive
  cannot be loaded at all.
- **the library's compile definitions**. Without them, headers and binaries
  disagree, which shows up as link errors or crashes.
- **printers**, so that a bare expression such as `img` shows something
  useful.

You produce these files in the user's project, then hand back one command:

```
.cpp-repl/<lib>.toml        the profile: flags, defines, libs, prelude
.cpp-repl/<lib>_repl.h      the prelude: the library's headers and its printers
.cpp-repl/<lib>.repl        an example session that exercises the library
.cpp-repl/<lib>.session     the example's transcript, for later re-checks
.cpp-repl/<lib>.check.json  the last check report

cpp-repl -c .cpp-repl/<lib>.toml
```

Two scripts do the mechanical work and print JSON. Run them from the
project's root.
- `probe_library.py` finds the library and resolves it.
- `check_profile.py` proves the profile.

Read their output rather than guessing paths or flags.

## 1. The toolchain

```
python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" env
```

- **No clang-repl and no Docker image:** stop and pass on the install hint
  from `notes`.
- **clang-repl 18:** it runs most libraries. Its JIT cannot assemble inline
  asm, though. If the library needs that, use Docker (step 7).
- **Built-in profile:** if the library is in `cpp_repl.builtin_profiles`, it
  needs no profile of its own. Run
  `cpp-repl -p NAME --batch "${CLAUDE_SKILL_DIR}/../../examples/NAME.repl"`:
  it shows how the library's values print, and fails with an install hint
  when the library is missing.
  - **Install not found:** when `project.compile_commands` lists a build that
    uses the library, add `--build-dir` with that build. The usd profile
    reads `USD_ROOT` from the `CMakeCache.txt` beside it.
  - **Hand back** the command that worked, and stop. Tell the user that
    `cpp-repl -p NAME prog.cpp` runs a program's `main()` one statement at a
    time and keeps the prompt open, so the project's own examples load as they
    are.
  - **Every session in this project:** a `cpp-repl.toml` at the project root
    holding `profiles = ["NAME"]`, plus `compile_commands = "build"` when the
    build supplies the install, makes a plain `cpp-repl` do the same. Offer
    it, and write it when the user asks.
  - **The profile's own files**, in `src/cpp_repl/profiles/NAME/` under
    `cpp_repl.checkout`, belong to cpp-repl. Printers for its types go in a
    prelude layered over it, as add-printer describes.
- **The project already builds against the library:** look for a
  `compile_commands.json` in `project.compile_commands` whose entries use the
  library. If one does, the profile can point at it with
  `compile_commands = "../build"`, and then needs only `libs` and a prelude.

## 2. Find it

```
python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" find NAME --scan .
```

Pick the source in this order.
1. **A CMake package.** It carries the definitions and dependencies the
   library's authors intended.
2. **A pkg-config module.** These are often incomplete: spdlog's lacks
   `FMT_SHARED`.
3. **Bare headers.** These give no libraries at all.

For modular packages (OpenCV, Boost, PCL, VTK, Qt), resolve only the
components the user needs. `suggest.components` comes from the project's
`#include` lines. Without one, choose from what the user wants to do: core and
imgproc for image arithmetic, say. Each extra module costs startup time.

`--scan DIR` also looks under DIR for the library's own headers and built
shared libraries, as for a library in a source tree next to the project:
`tree` lists them, and `suggest` resolves them when nothing is installed.
Point `--scan` at the tree that holds the library, `..` for a sibling
checkout.

If nothing is found, ask where the library is: a custom install goes in with
`--prefix DIR`, a source tree with `--scan DIR`. If it is not there at all,
name the package that provides it (`libopencv-dev`, say) and let the user
install it; installing needs sudo, which is their call.

## 3. Resolve it

```
python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" resolve --cmake PKG --components a,b --toml
python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" resolve --pkg-config MODULE --keep-libs x,y --toml
python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" resolve --headers DIR --lib /abs/libx.so --toml
```

The output includes:
- include directories and defines;
- `libs`, in load order with dependencies first;
- `dropped`, with the reason for each entry;
- `packages`, the apt packages, for Docker;
- a draft `toml`.

Exit status 3 means `problems[]` is not empty. Handle each one by its `kind`,
as [reference/resolve.md](reference/resolve.md) describes. A static-only
library is the common case. List shared objects only, never an `.a`.

## 4. Write the files

Start from the templates in `${CLAUDE_SKILL_DIR}/templates/`.

**`<lib>.toml`:** start from the draft, which already follows these rules.
- **Third-party include directories as `flags = ["-isystem", "/abs/dir"]`.**
  `include` gives `-I`, which shows the library's own warnings. A relative
  path in `flags`, as everywhere in the TOML, is relative to the TOML.
- **Never `/usr/include` or `/usr/local/include`.** The compiler searches
  them already, and listing them breaks `#include_next` in the standard
  library.
- **Defines exactly as resolved.** A define the library was built with (such
  as `SPDLOG_COMPILED_LIB`) decides what its headers expect from the binary.
- **Every library the user's code calls directly, in load order.** The REPL
  would also find a dependency through the library that needs it. The native
  build of a `%save-cpp` file does not: it needs every library on its link
  line.
**`<lib>_repl.h`:**
- `#include "cpp_repl.h"` first.
- Then the headers the user will reach for most: the project's own `#include`
  lines (`project_includes`), or the umbrella header of a small library.
- Every header adds startup time. Keep the profile's cost under about 1 s
  unless the user wants more: OpenCV's core and imgproc cost 0.8–1.4 s.
- Printers come later, in step 6.

**`<lib>.repl`:** 6 to 12 inputs.
- Construct the main types, and print them with bare expressions (no
  semicolon).
- Make at least one call into a function the shared library defines. A call
  that only reaches header code proves nothing about `libs`.
- Print one value inside a `std::vector`, which shows the one-line form.
- Leave out anything that blocks or depends on the machine: windows,
  `cv::waitKey`, stdin, files that may not exist.

## 5. Check it

```
python3 "${CLAUDE_SKILL_DIR}/scripts/check_profile.py" --config .cpp-repl/<lib>.toml \
    --example .cpp-repl/<lib>.repl --report .cpp-repl/<lib>.check.json --runs 1
```

It checks, in order:
- that the profile configures;
- that the prelude compiles natively;
- the startup cost;
- every input of the example;
- that the example really needs the libraries;
- that a `%save-cpp` of the example builds and prints the same.

Read `failed_steps`, the `status` of each input, and `hints`. Each hint names
a row of [reference/troubleshooting.md](reference/troubleshooting.md) by its
`anchor`.

- **Fix the first failure only.** A later input may need what the failed one
  would have defined. After a link failure, cpp-repl restores the session, so
  the inputs after it run as they would alone. An input that failed to link
  only because an earlier input failed to compile is run again: the report
  marks it `reran`, and its `status` is that run's. An input marked
  `unreliable` ran after a restore that could not replay every earlier input.
- **Re-run after each fix.** After three attempts at the same failure, stop
  and report what you found, rather than piling on flags.
- **Run once more for the final report.** Leave out `--runs 1`, so that the
  startup time is the best of two runs.

## 6. Make it print

The check warns `unprintable` (`<TypeName>`), `verbose` (more than 12 lines)
or `vector` (an item spread across lines). For any of these, follow the
**add-printer** skill: write the printers into `<lib>_repl.h`, then check
again.

Once the output reads well, save the transcript by adding
`--golden .cpp-repl/<lib>.session --update-golden`. Later runs with `--golden`
then catch drift after a library upgrade.

## 7. Docker

Use Docker only when the user asks for it, or when the library needs
clang-repl 23 (inline asm, or newer language features). Follow
[reference/docker.md](reference/docker.md). In short:
1. `probe_library.py image --config .cpp-repl/<lib>.toml` lists what the image
   lacks and writes a Dockerfile.
2. Build that image under its own tag before first use.
3. Check with `--docker --image TAG`.

## 8. Report

Tell the user, briefly:
- **The command:** `cpp-repl -c .cpp-repl/<lib>.toml`, with
  `--docker --image TAG` if needed. Inside Claude Code, `cpp-repl` is the
  plugin's copy. In the user's own terminal it exists only once installed.
  When `env` showed `on_path_is_this_checkout` (or no `on_path`), say how to
  install it: `pipx install git+https://github.com/farhatnadim/cpp_repl`, or a
  clone's `bin/cpp-repl`.
- **The files written.**
- **The startup cost:** `startup.delta`.
- **How each main type prints:** one example line each.
- **What was checked:** say which steps passed, which were skipped and why,
  and any limit that was hit. The report file is the evidence, and a claim
  goes no further than it.

Then offer the two next steps:
- **Every session in this project:** merge the profile's keys into the
  project's `cpp-repl.toml`, rewriting paths relative to that file.
- **Built into cpp-repl:** contribute it as `cpp-repl -p <lib>`, with the
  `contribute-profile` skill in a clone of cpp_repl.

## Rules

- Write only inside `.cpp-repl/`, plus the project's `cpp-repl.toml` when the
  user asks.
- The scripts and cpp-repl's own files are read-only here.
- Installing packages and running sudo are the user's decisions: give the
  command, and run it only when they say so.
- The scripts run this plugin's copy of cpp-repl. If the user's `cpp-repl` is a
  different one (`cpp_repl.on_path` in `env`), mention it: profiles behave the
  same only on the same version.
