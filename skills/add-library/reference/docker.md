# A profile under Docker

`cpp-repl --docker` runs the same command inside an image that holds
clang-repl 23: `cpp-repl:llvm23`, which `make docker` builds in a cpp_repl
checkout. Use it in two cases:
- the host has no clang-repl;
- the library needs clang-repl 23. That is the case for inline asm, which
  clang-repl 18's JIT cannot assemble (Eigen's dynamic-size products and some
  SIMD code), or for language features 18 lacks.

## What the container sees

cpp-repl mounts these directories at their own paths, read-only:
- the working directory;
- the cpp-repl checkout;
- its state directory;
- every directory the profile reads outside the image's own tree: include
  directories, libraries, preludes and `compile_commands.json`.

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

**Build the image before the first `--image` run.** Given a tag that does
not exist, cpp-repl offers to build the stock image under that tag, which
lacks the packages.

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
- **Two limits of clang 23:**
  - it rejects the TBB 2020 headers that some OpenUSD builds use;
  - it crashes on lambdas in top-level statements, which cpp-repl works
    around by running such statements inside functions.
