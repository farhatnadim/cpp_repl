# How a built-in profile finds its library

`configure(ctx)` runs every time cpp-repl starts with `-p <name>`, so
discovery has to be quick and must never touch the network or run cmake.
`ctx` holds:
- `ctx.env`, the environment;
- `ctx.cwd`, the working directory;
- `ctx.compile_commands`, the `compile_commands.json` in use, or None.

## The order

1. **An environment variable naming the install.** The eigen profile reads
   `$EIGEN_ROOT`: "a directory holding Eigen/Dense, or an install prefix".
   The usd profile reads `$USD_ROOT` and `$PXR_ROOT`. When the variable is set
   but wrong, fail: a silent fallback hides the mistake.

   ```python
   value = ctx.env.get('EIGEN_ROOT') or ctx.env.get('EIGEN3_INCLUDE_DIR')
   if value:
       found = _headers(value)
       if not found:
           raise ConfigError(f'no Eigen/Dense under {value}')
       return found
   ```

2. **The project's own build.** The usd profile reads `USD_ROOT` from the
   `CMakeCache.txt` next to the `compile_commands.json` in use. A project that
   builds against the library thereby picks the same install.

   ```python
   if ctx.compile_commands:
       cache = Path(ctx.compile_commands).parent / 'CMakeCache.txt'
       if cache.is_file():
           m = re.search(r'^USD_ROOT:PATH=(.*)$', cache.read_text(errors='replace'), re.M)
   ```

3. **pkg-config.** It is quick (a few milliseconds), and gives include
   directories and the library directory:
   - `pkg-config --cflags-only-I MODULE`;
   - `pkg-config --variable=libdir MODULE`.

   Give it a deadline, and treat a missing `pkg-config` as "not found".

4. **The package's CMake files, read as text.** The usd profile reads
   `INTERFACE_COMPILE_DEFINITIONS` from `pxrTargets.cmake`, and skips
   generator expressions (`$<...>`):

   ```python
   for value in re.findall(r'INTERFACE_COMPILE_DEFINITIONS "([^"]*)"', targets.read_text(errors='replace')):
       found += [d for d in value.split(';') if d and '$<' not in d]
   ```

   Use this for defines a package exports that pkg-config lacks. spdlog's
   CMake package adds `FMT_SHARED`, for example.

5. **The usual places.** A short list of prefixes:
   - the system: `/usr`, `/usr/local`;
   - the user's: `~/.local`;
   - the library's own: `/opt/<name>`.

   For each, check for a header every install has.

When nothing is found, the `ConfigError` names the apt package and the
variable, as the eigen profile's does:

```python
raise ConfigError('the eigen profile needs the Eigen headers: install libeigen3-dev, '
                  'or set EIGEN_ROOT to the directory holding Eigen/Dense')
```

## What configure returns

```python
return Profile(name='<name>',
               flags=['-isystem', str(include)] + ['-D' + d for d in defines],
               libs=[...],                       # absolute paths, dependencies first
               prelude=[HERE / 'cpp_repl_<name>.h'],
               completion=[],                    # directories of *.h files for Tab; leave empty for .hpp libraries
               help=HELP,                        # lines for %help
               banner=f'<Name> {version}',       # the startup line names it
               mounts=[root])                    # directories cpp-repl --docker mounts; /usr is skipped anyway
```

- **No implicit include directories in `flags`.** Leave out `/usr/include`
  and `/usr/local/include`, since `-isystem /usr/include` breaks
  `#include_next` in libstdc++.
- **`libs` holds absolute paths, the `lib*.so` links where they exist.**
  Find them in the library directory, trying in turn:
  - `<prefix>/lib/<multiarch>`;
  - `<prefix>/lib64`;
  - `<prefix>/lib`.
- **The version** comes from a header (the usd profile reads
  `PXR_MAJOR_VERSION`), or from pkg-config's `--modversion`.
- **Tab completion** already covers every header the prelude includes.
  `completion` adds class names from directories of `*.h` files, and only from
  those.
- **A profile that links Python, as OpenUSD can,** finds `Python.h` the way
  `python_include()` in the usd profile does.
