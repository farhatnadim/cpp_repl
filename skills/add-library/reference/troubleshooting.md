# Troubleshooting a profile

Find the row by the `anchor` of a hint in the check report, or by the text
you see.

Before working through the table:
- Fix the first failure only. After a link failure, clang-repl's state is
  unreliable, and later inputs fail for that reason alone.
- `CPP_REPL_TRACES=1` makes cpp-repl print clang-repl's crash traces.

## Configuration

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| not-a-flag | `not a compiler flag cpp-repl passes on: -lfoo` | A link flag (`-l`, `-L`, `-Wl,`) or an unsupported option in `flags`. | Put libraries in `libs` as absolute `.so` paths. Move `-include` into the prelude. |
| no-profile | `no profile 'x'; the profiles are: ...` | A user TOML named in `profiles`, or in `-p x`. | Load a TOML with `-c FILE`, or with `-p path/to/x.toml`. `profiles` takes built-in names only. |
| unknown-keys | `unknown keys X; the keys are ...` | A key cpp-repl does not read. | Use only `std`, `include`, `defines`, `flags`, `libs`, `prelude`, `profiles` and `compile_commands`. |
| no-library | `no library P` or `no prelude header P` | A wrong path. Paths in a TOML are relative to the TOML. | Fix the path. Under Docker, the file may be missing from the image instead: see docker.md. |

## Starting

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| failed-to-start | `cpp-repl: clang-repl failed to start:` | Something in the profile stops clang-repl. | Read `startup.diagnosis`: the check reruns without the libraries, then with each one alone, to name the culprit. |
| static-or-script | `invalid ELF header` or `file too short` after `failed to start` | `libs` lists a static archive (`.a`) or a linker script (glibc's `libc.so`, ncurses' `libncurses.so`). | Re-resolve: the probe follows linker scripts and refuses archives. See resolve.md, `static`. |
| dependency-not-found | `libY.so.N: cannot open shared object file` | A library needs another that is not on the loader's path. | List libY's absolute path in `libs`, before the library that needs it. |
| crash | `[clang-repl crashed (SIGSEGV)]` at startup or on the first call | The profile's defines or `-m` flags differ from the library's build. Or a library's static initialiser crashed. | Compare the defines with `resolve`'s. Check the load order. Under clang-repl 18, a global with a dynamic initialiser in the prelude never runs: move such code out of the prelude. |
| slow-start | the check warns `slow-start` | The prelude includes too much. | Include fewer headers: the ones the user needs, rather than an umbrella header. Or accept the cost, with `--budget`. |
| timeout | `clang-repl gave no answer within 300 s`, or a step `timeout` | A huge prelude, or a library initialiser that blocks. | Trim the prelude. Check what the library does when it loads (licence checks, network). |

## Running the example

| id | Text | Cause | Fix |
| --- | --- | --- | --- |
| file-not-found | `fatal error: 'x.h' file not found` | No include directory holds that header. | Add the directory to `flags` as `-isystem /abs/dir`, or fix the `#include`. |
| undeclared | `use of undeclared identifier 'cv'`, or `no member named ... in namespace` | The header that declares the name is not included. Or the prelude failed part-way: cpp-repl does not notice that, and starts anyway. | Look at the check's `prelude` step first, then the prelude's includes. |
| symbols-not-found | `JIT session error: Symbols not found: [ _ZN... ]` | The code calls a function that no loaded library defines. | `probe_library.py symbol '_ZN...' --config .cpp-repl/<lib>.toml` names the library that defines it. Add that library to `libs`. It also takes a demangled prefix such as `cv::GaussianBlur`. |
| materialize | `error: Failed to materialize symbols`, with no `Symbols not found` | An earlier input failed to link, and the session now fails on everything. | Fix the first failure only. |
| libcxx | symbols with `St3__1` in them | The library was built against libc++. | Unsupported: see resolve.md, `libcxx`. |
| old-abi | `std::string` symbols without `B5cxx11` (`_ZN...Ss...`) | The library was built with the old libstdc++ string ABI, as some prebuilt binaries are (older libtorch releases). | `defines = ["_GLIBCXX_USE_CXX11_ABI=0"]`. |
| openmp | symbols such as `__kmpc_fork_call` or `omp_get_num_threads` | The code uses OpenMP. | `-fopenmp` in `flags`, and the matching `libomp.so` in `libs`. |
| inline-asm | `error in backend: Inline asm not supported by this streamer` | A clang-repl 18 limit: its JIT cannot assemble inline asm (Eigen's dynamic products, some SIMD code). | Use clang-repl 23 through `--docker`. For a few libraries, a define turns the asm off. |
| stuck | `[clang-repl got stuck on that input]` | A header incompatible with C++20 or with clang flooded the compiler with errors. | Read the first error above it. |
| name-clash | `redefinition of 'index' as different kind of symbol` | Inputs typed at the prompt are globals, and some names clash with C library functions. | Use another name in the example. |
| redefinition | `redefinition of 'x'` | The same variable is defined twice in one session. | Use a new name, or assign instead. |

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
