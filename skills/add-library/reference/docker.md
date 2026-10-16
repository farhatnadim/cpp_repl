# A profile under Docker

`cpp-repl --docker` runs the same command inside an image that holds
clang-repl 23: `cpp-repl:llvm23`, which `make docker` builds in a cpp_repl
checkout. Use it in two cases:
- the host has no clang-repl 23 or newer, which cpp-repl needs;
- the user asks for it.

## What the container sees

cpp-repl mounts these directories at their own paths:
- the working directory and its state directory, read-write;
- the cpp-repl checkout, read-only;
- every directory the profile reads outside the image's own tree, read-only:
  include directories, libraries, preludes and `compile_commands.json`.

A path therefore means the same inside the container as outside it.

The image's own tree (`/usr`, `/lib`, `/etc`) is never mounted, so a library
installed on the host under `/usr` is invisible inside the image. The image
needs its own copy, from apt. Libraries under `/opt`, under `/usr/local`, in
`$HOME` or in a conda environment are mounted, so they work as they are.

## A derived image

```
python3 "${CLAUDE_SKILL_DIR}/scripts/probe_library.py" image --config .cpp-repl/<lib>.toml
```

The command:
- lists which of the profile's paths are missing from the image, counting the
  headers the prelude includes, which covers a header-only library under
  `/usr/include`;
- maps the missing paths to the host's apt packages;
- writes a Dockerfile.

Save that Dockerfile as `.cpp-repl/Dockerfile` (it starts `FROM cpp-repl:llvm23`),
then build it under the suggested tag:

```
docker build -t cpp-repl-<project>:llvm23 .cpp-repl
python3 "${CLAUDE_SKILL_DIR}/scripts/check_profile.py" --config .cpp-repl/<lib>.toml \
    --example .cpp-repl/<lib>.repl --docker --image cpp-repl-<project>:llvm23 --runs 1
```

**Build the image before the first `--image` run.** cpp-repl builds only its
own `cpp-repl:llvmN` images. Given any other tag that does not exist, it stops
with `there is no image X` and the command that builds it.

Notes:
- **One image per project.** When the project has several profiles, add each
  profile's packages to the same Dockerfile.
- **Package names come from the host.** The image is Ubuntu 24.04. On another
  host distribution, check the names: `apt-cache search`, inside the image.
- **No round trip or `prove_libs` under `--docker`.** The check skips those
  two steps. It still runs the prelude through the image's clang++, and runs
  the startup and the example inside the image.
- **The start command:**
  `cpp-repl --docker --image cpp-repl-<project>:llvm23 -c .cpp-repl/<lib>.toml`.
- **Two limits of clang 23, on the host as in the image:**
  - clang 20 and newer reject an enumeration constant in TBB 2020's
    `tbb/task.h`, which some OpenUSD builds install. The usd profile
    (`-p usd`, or `profiles = ["usd"]`) reads a patched copy of those
    headers; a profile that includes TBB 2020 without it fails to start;
  - it crashes on lambdas in top-level statements, which cpp-repl works
    around by running such statements inside functions.
