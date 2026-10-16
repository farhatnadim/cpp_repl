# Resolving a library

`probe_library.py resolve` lists `problems[]`, each with a `kind` and a
`fix`. This page explains each kind, then covers how to choose a source and
the special cases.

## Problem kinds

| kind | What it means | What to do |
| --- | --- | --- |
| `static` | Only a static archive (`.a`) was found, or the package links statically (`-Wl,-Bstatic`). clang-repl loads shared objects only. | Install the shared variant. With vcpkg, use a `*-dynamic` triplet such as `x64-linux-dynamic`. For a source build, `-DBUILD_SHARED_LIBS=ON`. Some libraries have a header-only mode (`FMT_HEADER_ONLY`, `SPDLOG_HEADER_ONLY`): then drop the library from `libs` and add the define. As a last resort, wrap a PIC archive in a shared library: `g++ -shared -o libx.so -Wl,--whole-archive libx.a -Wl,--no-whole-archive`. |
| `unresolved-l` | A `-lNAME` that no search found. | Find the library (`ldconfig -p`, `find / -name 'libNAME.so*'`) and pass `--prefix` for its install prefix, or name the file with `--lib`. |
| `missing` | A path that does not exist. | Usually a stale CMake package or pkg-config file. Fix the install, or resolve from another source. |
| `missing-needed` | A listed library needs another (DT_NEEDED) that the loader cannot find. This is usual for conda, vcpkg and source builds that rely on `LD_LIBRARY_PATH`. | Find that file and list its absolute path in `libs`, **before** the library that needs it. A library already loaded satisfies a later one's dependency by its soname. |
| `libcxx` | The library was built against libc++ (`-stdlib=libc++` among its flags, or `St3__1` in its symbols). Its standard-library types differ from libstdc++'s. | `"-stdlib=libc++"` in `flags`: cpp-repl then compiles against libc++ and loads its runtime before the libraries. Every library in the session must be a libc++ build; most distribution packages are libstdc++ builds, and the two cannot share a session. |
| `openmp` | The package compiles or links with `-fopenmp`. | Add `"-fopenmp"` to `flags`, and add the `libomp.so` that matches the clang-repl's major version to `libs` (`ls /usr/lib/llvm-*/lib/libomp.so`, package `libomp-N-dev`). |
| `include-file` | The package compiles every source with `-include FILE`. | Either `#include "FILE"` at the top of the prelude header, or `"-include", "FILE"` in `flags`: cpp-repl parses the file as a prelude, before the first input. |
| `unsupported-flag` | A compile option cpp-repl does not pass on: `-imacros FILE`, `-Xclang ...` or `-x ...`. | For `-imacros FILE`, put `#include "FILE"` at the top of the prelude header. For `-Xclang`, find out what the option does. It is usually safe to leave out. |
| `framework`, `object`, `unknown-file` | macOS frameworks, object files, or something unexpected on the link line. | Leave it out and see whether the check passes. If not, report it to the user. |

`dropped[]` lists what the probe left out on purpose, with the reason for
each. Read it when something you expected is missing:
- system libraries;
- linker options;
- implicit include directories;
- the members of a linker script;
- what `--keep-libs` excluded.

## Choosing the source

- **CMake package (`--cmake PKG`).** The probe configures a small CMake
  project that links the package, and reads CMake's File API reply.
  - What it gets is what a CMake consumer of the library compiles and links
    with: include directories, compile definitions, compile options, and the
    full link line with dependencies.
  - The targets it links are, in this order: `--targets`, then
    `PKG_LIBRARIES` or `PKG_LIBS`, then `PKG::PKG` and its usual variations,
    then every imported target. `source.targets_from` says which one it used.
- **Components (`--components a,b`).** These go straight into
  `find_package(PKG COMPONENTS a b)`. Use the package's own component names:
  - `core imgproc` for OpenCV;
  - `filesystem system` for Boost;
  - `common io` for PCL;
  - `Core Widgets` for Qt6.
- **pkg-config (`--pkg-config MODULE`).** Each `-l` resolves to a `.so` file.
  `--keep-libs a,b` keeps only those `-l` names: pkg-config's `opencv4` lists
  60 of them.
- **By hand (`--headers DIR --lib PATH --define D`).** For a library with no
  package files, or for the user's own build tree.
- **`--prefix DIR`.** Adds an install prefix to both CMake (as
  `CMAKE_PREFIX_PATH`) and the search for libraries. Use it for:
  - conda: `--prefix "$CONDA_PREFIX"`;
  - vcpkg: `--prefix vcpkg_installed/x64-linux-dynamic`;
  - `/opt` installs;
  - `~/local`.

## The user's own library

When the library is the project itself:
- **Flags and headers:** if a build directory with `compile_commands.json`
  exists, point at it: `compile_commands = "../build"` in the TOML, relative
  to `.cpp-repl/`. cpp-repl then takes `-I`, `-D` and `-std` from that build.
- **Libraries:** list the project's shared libraries in `libs`, also relative
  to `.cpp-repl/`, for example `"../build/libmylib.so"`. A static library
  target needs `BUILD_SHARED_LIBS=ON`, or a shared variant of the target.
- **Prelude:** include the project's public headers.

## Versions

- `source.version` comes from the package, and the TOML's comment records it.
- After a library upgrade, re-run `resolve` and the check. With `--golden` the
  check compares against the saved transcript.
- `dev_link` rewrites versioned paths such as `libfoo.so.1.2.3` to the
  `libfoo.so` link where that link exists. The profile then survives a patch
  upgrade.
