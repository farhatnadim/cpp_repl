---
name: contribute-profile
description: Turn a working cpp-repl library profile (from the add-library skill) into a built-in one that ships with cpp-repl as `cpp-repl -p NAME`, inside a clone of github.com/farhatnadim/cpp_repl. Covers discovery code, the printer header, a golden session, tests, the README, the example and the Docker wiring, ready for a pull request.
disable-model-invocation: true
argument-hint: <name> [path to the .cpp-repl/<lib>.toml it starts from]
allowed-tools:
  - Bash(python3 "${CLAUDE_SKILL_DIR}/../add-library/scripts/check_profile.py" *)
  - Bash(python3 "${CLAUDE_SKILL_DIR}/../add-library/scripts/probe_library.py" *)
  - Bash(make test)
  - Bash(make test-host)
  - Bash(python3 -m pytest *)
---

# Contribute a built-in profile

A built-in profile is a Python module and a header under
`src/cpp_repl/profiles/<name>/`. Anyone then loads it with `cpp-repl -p <name>`,
on any machine, so the module has to *find* the library where a user profile
names fixed paths. Everything else carries over from the profile that
add-library made and checked:
- the flags, the defines and the libraries;
- the printers;
- the example.

[reference/checklist.md](reference/checklist.md) lists every file to touch;
[reference/discovery.md](reference/discovery.md) shows how the eigen and usd
profiles find their libraries. The templates named below are in
`${CLAUDE_SKILL_DIR}/templates/`.

The glm profile, in `src/cpp_repl/profiles/glm/`, is a finished example that
this skill made. Its commit, `profiles: add glm`, shows every file a
contribution touches.

## 0. Before starting

- **The working directory is a cpp_repl checkout:** `src/cpp_repl/profiles/`
  exists. The plugin's own copy does not count: contributions go into a clone
  the user owns.
- **AGENTS.md allows it.** Its "Who writes the code" section says what agents
  may write in this repository. The skills' files are covered there. If the
  clone's AGENTS.md says otherwise, follow it and ask the user.
- **The work is an issue and its branch:**
  `make issue KIND=feature TITLE="cpp-repl -p <name> loads <Library>"`, then
  `gh issue develop N --checkout`. AGENTS.md's "How changes land" has the
  rest.
- **The user profile passes `check_profile.py`.** If there is none, run the
  add-library skill first, in a scratch project.

## 1. Name it

A lower-case Python identifier: `opencv`, `glm`, `yaml_cpp`.
- It is a package name, `cpp_repl.profiles.<name>`.
- The golden-session test reads it with `profile=(\w+)`.

## 2. Write the module

Start from `templates/profile_init.py` and fill in what the user profile
already knows: include directories, defines, the libraries and their load
order. Then make it find them:
1. **`$<NAME>_ROOT`, an install prefix.** It has to work, because it is how
   Docker users and custom installs point at the library.
2. **pkg-config, or the package's CMake files read as text.** Never run cmake
   at startup: `configure()` runs at every start, which takes about half a
   second, and a cmake configure takes several.
3. **The usual prefixes.**

When nothing is found, raise `ConfigError` with the apt package and the
variable to set. Give the profile:
- a `banner` with the version, read from a header;
- `help` lines for `%help`;
- `mounts` for any root outside `/usr`.

## 3. Move the header

Copy `<lib>_repl.h` to `src/cpp_repl/profiles/<name>/cpp_repl_<name>.h`.
- **The `.h` ending matters.** `pyproject.toml` packages `profiles/*/*.h`.
- **Write a header comment in the style of `cpp_repl_eigen.h`:** what a session
  gets, and how each type prints.
- **Add `cpp_repl_<name>.h` to `PRELUDE_NAMES` in `src/cpp_repl/sources.py`.**
  A program saved with `%save-cpp` includes it, and running that program in
  the REPL again must skip it.

## 4. Test it

- **A golden session, `tests/sessions/<name>.session`, from
  `templates/golden.session`:**
  - take the inputs from the user profile's example;
  - generate the transcript with
    `CPP_REPL_UPDATE_GOLDEN=1 python3 -m pytest -q tests/test_integration.py -k <name>`;
  - review it by hand, and put `<any>` back on lines that vary between runs
    (timings, random values). Addresses are normalised already.
  - It needs clang-repl 23 or newer, as every backend test does: with an
    older one the test skips, and `CPP_REPL_UPDATE_GOLDEN=1` writes nothing.
    `make test-docker` runs it in the `cpp-repl:llvm23` image.
  - Keep the default `c++> ` prompt: the golden test assumes it for every
    profile but usd.
- **A test in `tests/test_config.py`, from `templates/test_snippet.py`.** It
  skips where the library is not installed.

## 5. Document it

- **The README's "Profiles" table:** one row saying what `-p <name>` adds,
  how values print, and how the library is found.
- **`examples/<name>.repl`:** from the user profile's example. Its first line
  is a comment with the command: `cpp-repl -p <name> examples/<name>.repl`.
- **The README's "Limits":** when the library has a catch, such as a
  standard it needs (`std = "gnu++20"`).

## 6. Docker

- **Add `<NAME>_ROOT` to the environment** that `src/cpp_repl/docker.py`
  passes into the container. It sits next to `USD_ROOT` and `EIGEN_ROOT`.
- **Installing the library in the image is the maintainer's call.** That means
  adding its apt package to the `base` stage of `src/cpp_repl/docker/Dockerfile`, which
  changes the Dockerfile's hash and marks every user's image as out of date.
  Ask the user.
- **Without it, the profile works under Docker when:**
  - the library lives outside `/usr`, which is mounted;
  - or the user builds a derived image, as add-library's docker.md shows.

## 7. Run everything

```
make check                # every test against the host's clang-repl, the wheel, claude plugin validate
make test-docker          # when the cpp-repl:llvm23 image exists
```

The tests print their skips (`-rs`). If `<name>` is among them, the golden
session did not run: find out why before going on.

## 8. Commit

Commit as `profiles: add <name>`. The body says:
- what `-p <name>` gives;
- how the main types print;
- which clang-repl versions and library versions it was tested with.

End it with the attribution lines the session asks for. `make pr` runs
`make check`, pushes the branch and opens the pull request, which closes the
issue; run it only when the user says so.
