"""The session's libraries, loaded as clang-repl starts.

clang-repl 23 runs the dynamic initialisers of the prelude headers as it
starts, before any input: OpenUSD's, for one, register Python converters
there, calling into libusd_python. So the libraries must be in the process
before the preludes are compiled, which a %lib cannot do. A small library,
built once with the clang beside clang-repl and preloaded into it, turns core
dumps off (a crash in a library's static initialiser would otherwise leave a
crash report, apport's on Ubuntu), then loads the libraries that
CPP_REPL_PRELOAD names, one per line, and takes both variables out of the
environment, so that a program the session runs inherits neither. A library
that fails to load is an error at start-up, as clang-repl's own are.
"""

import hashlib
import os
import subprocess
from pathlib import Path

from .config import sibling_clang

SOURCE = r'''
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>

__attribute__((constructor)) static void cpp_repl_preload(void)
{
    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);
    const char *libs = getenv("CPP_REPL_PRELOAD");
    char *copy = libs ? strdup(libs) : NULL, *rest = NULL;
    unsetenv("CPP_REPL_PRELOAD");
    unsetenv("LD_PRELOAD");
    for (char *lib = copy ? strtok_r(copy, "\n", &rest) : NULL; lib; lib = strtok_r(NULL, "\n", &rest))
        if (!dlopen(lib, RTLD_NOW | RTLD_GLOBAL))
            fprintf(stderr, "error: %s\n", dlerror());
    free(copy);
}
'''


def shim(cfg):
    """The preloaded library, built if need be; None when there is no clang
    to build it with, or it does not build."""
    clang = sibling_clang(cfg.clang_repl)
    if not clang:
        return None
    digest = hashlib.sha256(f'{Path(clang).resolve()}\n{SOURCE}'.encode()).hexdigest()[:12]
    path = cfg.state / 'preload' / f'cpp_repl_preload-{digest}.so'
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    source = path.with_name(f'{path.stem}.{os.getpid()}.c')
    built = path.with_name(f'{path.stem}.{os.getpid()}.so')
    try:
        source.write_text(SOURCE)
        done = subprocess.run([clang, '-x', 'c', '-shared', '-fPIC', '-O2', '-o', str(built), str(source)],
                              capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        if done.returncode:
            return None
        os.replace(built, path)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        for f in (source, built):
            try:
                f.unlink()
            except OSError:
                pass
    return path


def environment(cfg, libraries):
    """cfg.env with the preload set for these libraries, in order; cfg.env
    itself when there are none, or no preload library."""
    if not libraries:
        return cfg.env
    path = shim(cfg)
    if not path:
        return cfg.env
    return dict(cfg.env, LD_PRELOAD=str(path), CPP_REPL_PRELOAD='\n'.join(str(lib) for lib in libraries))
