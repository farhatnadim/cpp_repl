# Files a built-in profile touches

Paths are relative to the cpp_repl checkout, except `templates/`, which is
this skill's directory (`skills/contribute-profile/templates/`). `<name>` is the
profile name, a Python identifier; `<NAME>` is the same in upper case.

| File | Change | Why |
| --- | --- | --- |
| `src/cpp_repl/profiles/<name>/__init__.py` | new: `configure(ctx) -> Profile`, from `templates/profile_init.py` | `cpp-repl -p <name>` imports it. `builtin_profiles()` lists every package in `profiles/`. |
| `src/cpp_repl/profiles/<name>/cpp_repl_<name>.h` | new: the prelude, from the user profile's `<lib>_repl.h` | Precompiled once per configuration, after `cpp_repl.h`. The name must end in `.h` (package data: `profiles/*/*.h`). |
| `src/cpp_repl/sources.py` | add `'cpp_repl_<name>.h'` to `PRELUDE_NAMES` | A saved program's `#include` of the prelude is skipped when the program runs in the REPL. |
| `tests/sessions/<name>.session` | new: golden transcript, `# args: -p <name>`, `# requires: profile=<name>` | `make test-host` runs it, and skips it where the profile cannot start. |
| `tests/test_config.py` | add `test_<name>_profile`, from `templates/test_snippet.py` | The profile configures without clang-repl, or skips where the library is missing. |
| `examples/<name>.repl` | new: the example, with the command as its first comment line | The README points at it, and users try it first. |
| `README.md` | a row in the "Profiles" table, and "Limits" when needed | What `-p <name>` adds, and how the library is found. |
| `src/cpp_repl/docker.py` | add `'<NAME>_ROOT'` to the names `docker_command` passes with `-e` | Under `--docker`, the profile's `configure()` runs again inside the container and reads the variable there. |
| `src/cpp_repl/docker/Dockerfile` | only with the maintainer's agreement: the apt package in the `base` stage | It changes `DOCKERFILE_SHA`, so every user's image reads as stale until they rebuild it. |

Nothing else needs to change:
- `pyproject.toml` already packages `profiles/*/*.h`;
- `config.py` finds profiles by listing the package;
- the golden-session test finds `tests/sessions/*.session` by itself.

## Checks before the commit

- [ ] `cpp-repl -p <name> --cflags` and `--libs` print absolute paths that exist.
- [ ] `cpp-repl -p <name> examples/<name>.repl --batch` exits 0.
- [ ] `<NAME>_ROOT=/some/prefix cpp-repl -p <name> --cflags` uses that prefix,
      or fails with a message that names it.
- [ ] Without the library, `cpp-repl -p <name>` fails with a `ConfigError`
      naming the apt package, and no traceback.
- [ ] `make test`, `make test-host` (the session runs), and
      `make test-docker` when the image exists.
- [ ] `claude plugin validate .` passes. It warns about the root `CLAUDE.md`,
      which is expected.
- [ ] The README row reads like the other rows: what it adds, how
      values print, how the library is found.
