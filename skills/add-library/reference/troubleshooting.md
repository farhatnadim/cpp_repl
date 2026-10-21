# Troubleshooting a profile

Find the row by the `anchor` of a hint in the check report, or by the text
you see.

Before working through the table:
- Fix the first failure only. A later input may need what the failed one
  would have defined. After a link failure, cpp-repl restores the session,
  so the inputs after it run as they would alone; the check marks an input
  `unreliable` only when that restore could not replay every earlier input.
- `CPP_REPL_TRACES=1` makes cpp-repl print clang-repl's crash traces.

## Configuration

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| not-a-flag | `not a compiler flag cpp-repl passes on: -lfoo` | A link flag (`-l`, `-L`, `-Wl,`) or an unsupported option in `flags`. | Put libraries in `libs` as absolute `.so` paths. (`-include FILE` is accepted: cpp-repl parses the file as a prelude.) |
| no-profile | `no profile 'x'; the profiles are: ...` | A user TOML named in `profiles`, or in `-p x`. | Load a TOML with `-c FILE`, or with `-p path/to/x.toml`. `profiles` takes built-in names only. |
| unknown-keys | `unknown keys X; the keys are ...` | A key cpp-repl does not read. | Use only `std`, `include`, `defines`, `flags`, `libs`, `prelude`, `profiles`, `compile_commands` and `pch`. |
| no-library | `no library P` or `no prelude header P` | A wrong path. Paths in a TOML are relative to the TOML. | Fix the path. Under Docker, the file may be missing from the image instead: see docker.md. |

## Starting

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| old-clang-repl | `is clang-repl 18.1.3, and cpp-repl needs 23 or newer` | cpp-repl runs clang-repl 23 and newer, and the newest one found is older. | Install clang-23 and clang-tools-23 from apt.llvm.org, or run the check with `--docker`: `probe_library.py env` lists the images. |
| failed-to-start | `cpp-repl: clang-repl failed to start:` | Something in the profile stops clang-repl. | Read `startup.diagnosis`: the check reruns without the libraries, then with each one alone, to name the culprit. |
| static-or-script | `invalid ELF header` or `file too short` after `failed to start` | `libs` lists a static archive (`.a`) or a linker script (glibc's `libc.so`, ncurses' `libncurses.so`). | Re-resolve: the probe follows linker scripts and refuses archives. See resolve.md, `static`. |
| dependency-not-found | `libY.so.N: cannot open shared object file` | A library needs another that is not on the loader's path. | List libY's absolute path in `libs`, before the library that needs it. |
| crash | `[clang-repl crashed (SIGSEGV)]` at startup or on the first call | The profile's defines or `-m` flags differ from the library's build. Or a library's static initialiser crashed. | Compare the defines with `resolve`'s. Check the load order. A global with a dynamic initialiser in the prelude runs as clang-repl starts, and again in every clang-repl that replaces it: move such code out of the prelude. |
| older-standard | `invalid arithmetic between different enumeration types` (or bitwise operation, comparison) in the prelude or startup step | cpp-repl compiles as C++26 with clang 17 and newer, and the library's headers use what C++26 removed, such as arithmetic between different enumeration types (OpenCV 4.6). | `std = "gnu++20"` in the profile. `%flags` shows the standard in use. |
| slow-start | the check warns `slow-start` | The prelude includes too much. (The check times a start after an untimed one, which precompiles the configuration's preludes.) | Include fewer headers: the ones the user needs, rather than an umbrella header. Or accept the cost, with `--budget`. |
| timeout | `clang-repl gave no answer within 300 s`, or a step `timeout` | A huge prelude, or a library initialiser that blocks. | Trim the prelude. Check what the library does when it loads (licence checks, network). |

## Running the example

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| file-not-found | `fatal error: 'x.h' file not found` | No include directory holds that header. | Add the directory to `flags` as `-isystem /abs/dir`, or fix the `#include`. |
| undeclared | `use of undeclared identifier 'cv'`, or `no member named ... in namespace` | The header that declares the name is not included. (A prelude that fails to compile stops cpp-repl's start instead: failed-to-start.) | Look at the check's `prelude` step first, then the prelude's includes. |
| symbols-not-found | `JIT session error: Symbols not found: [ _ZN... ]` | The code calls a function that no loaded library defines. When the list names `repl::` symbols (`_ZN4repl`), see stale-template. | `probe_library.py symbol '_ZN...' --config .cpp-repl/<lib>.toml` names the library that defines it. Add that library to `libs`. It also takes a demangled prefix such as `cv::GaussianBlur`. |
| stale-template | `[that input failed to link after an earlier error: restored N inputs; running it again]`, for a correct input that follows one that failed to compile. An older cpp-repl shows `Symbols not found` before that note, naming `repl::put`, `repl::show` or a standard-library template | clang-repl 23 and 24 keep the template instantiations of an input that failed to compile, but not their code. A later input that needs one of them cannot link. No library is missing. | Nothing for that input: cpp-repl restores the session and runs it again, at the prompt and under `--batch`, and the check marks it `reran`. Fix the input that failed to compile. |
| materialize | `error: Failed to materialize symbols`, with no `Symbols not found` | Seen only with a cpp-repl from before 2026-10-15, which kept the session after a link failure; it then failed on everything. The current one restores the session. | Update cpp-repl. Until then, fix the first failure only. |
| libcxx | symbols with `St3__1` in them | The library was built against libc++. | `"-stdlib=libc++"` in `flags`, with libc++ builds only in the session: see resolve.md, `libcxx`. |
| old-abi | `std::string` symbols without `B5cxx11` (`_ZN...Ss...`) | The library was built with the old libstdc++ string ABI, as some prebuilt binaries are (older libtorch releases). | `defines = ["_GLIBCXX_USE_CXX11_ABI=0"]`. |
| openmp | symbols such as `__kmpc_fork_call` or `omp_get_num_threads` | The code uses OpenMP. | `-fopenmp` in `flags`, and the matching `libomp.so` in `libs`. |
| stuck | `[clang-repl got stuck on that input]` | A header incompatible with the standard in use (`%flags` shows it) or with clang flooded the compiler with errors. | Read the first error above it. |
| name-clash | `redefinition of 'index' as different kind of symbol` | Inputs typed at the prompt are globals, and some names clash with C library functions. | Use another name in the example. |
| redefinition | `redefinition of 'x'`, then `[x stays as it is: ...]` | A name the session declared is declared again. cpp-repl replaces the old declaration by replaying the inputs around it, and keeps the old one when the new input fails or a later input needs the old one; the note says which. A prelude's or a library's name cannot be replaced (name-clash). | Fix the new declaration, or use another name. |

## Printing

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| unprintable | a value prints as `<TypeName>` | The type has no `operator<<` and no printer. | Follow the add-printer skill. |
| verbose | the check warns `verbose` (more than 12 lines) | The library's `operator<<` prints every element. | add-printer: write `Show<T>` and `Put<T>`. |
| vector | the check warns `vector` | Inside a container, an item uses `Put<T>`, which falls back on the type's multi-line `operator<<`. | add-printer: write `Put<T>`. |
| ambiguous-printers | `ambiguous partial specializations of 'Show<...>'` | Two constrained printers match one type. | Constrain each printer on the library's own types or base classes. |
| printer-after-instantiation | `explicit specialization of 'repl::Show<X>' after instantiation` | A printer was typed after X had already printed. | Put printers in the prelude header, and restart. |

## Building a `%save-cpp` file (the round trip)

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| dso-missing | `libX.so.N: error adding symbols: DSO missing from command line` | The code calls libX directly, and the REPL found libX only because another listed library needs it. | List libX's `lib*.so` link in `libs`. |
| dso-missing | ``undefined reference to `ns::f(...)'`` | A library is missing from the native link line. | `probe_library.py symbol 'ns::f' --config ...`, then add it to `libs`. |
| no-image | `there is no image X` | The derived Docker image has not been built. | `docker build -t X .cpp-repl`. Build it before first use: see docker.md. |
