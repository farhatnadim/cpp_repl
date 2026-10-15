"""LIBRARY: <what a session gets: its headers and libraries>, and how <its
main types> print.

The library is found from $NAME_ROOT (an install prefix), then pkg-config,
then the usual prefixes. An install is a prefix holding include/MARKER.
"""

import re
import subprocess
import sysconfig
from pathlib import Path

from ...config import ConfigError, Profile

HERE = Path(__file__).resolve().parent

# Fill these in from the user profile (.cpp-repl/<lib>.toml) and the library.
NAME = 'name'                       # the profile: cpp-repl -p name
LIBRARY = 'Library'                 # how the startup line names it
ROOT_VARIABLE = 'NAME_ROOT'         # an install prefix that overrides discovery
PKG_CONFIG = 'module'               # its pkg-config module, or None
MARKER = 'library/library.hpp'      # a header every install has, under include/
VERSION = ('library/version.hpp', r'#define LIBRARY_VERSION "([^"]+)"')   # file under include/, regex
LIBS = ['name']                     # lib<name>.so to load, dependencies first; [] when header-only
DEFINES = []                        # the compile definitions its package exports
APT = 'libname-dev'                 # the Debian/Ubuntu package that provides it
PREFIXES = ['/usr/local', '/usr', '~/.local', '/opt/name']

HELP = """\
  <a line or two for %help: how its values print>
"""

IMPLICIT = ('/usr/include', '/usr/local/include')


def _is_prefix(path):
    return (Path(path).expanduser() / 'include' / MARKER).is_file()


def _pkg_config(*args):
    """pkg-config's answer for the module, in words; None when it has none."""
    if not PKG_CONFIG:
        return None
    try:
        out = subprocess.run(['pkg-config', *args, PKG_CONFIG], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.split() if out.returncode == 0 else None


def _lib_dirs(prefix):
    prefix = Path(prefix).expanduser()
    arch = sysconfig.get_config_var('MULTIARCH')
    dirs = ([prefix / 'lib' / arch] if arch else []) + [prefix / 'lib64', prefix / 'lib']
    return [d for d in dirs if d.is_dir()]


def find(ctx):
    """(include directories, library directories) of the install to use."""
    value = ctx.env.get(ROOT_VARIABLE)
    if value:
        if not _is_prefix(value):
            raise ConfigError(f'${ROOT_VARIABLE} is {value}, which holds no include/{MARKER}')
        return [Path(value).expanduser() / 'include'], _lib_dirs(value)
    cflags = _pkg_config('--cflags-only-I')
    libdir = _pkg_config('--variable=libdir')
    if cflags is not None and (libdir or not LIBS):
        return [Path(f[2:]) for f in cflags if f.startswith('-I')], [Path(d) for d in libdir or []]
    for prefix in PREFIXES:
        if _is_prefix(prefix):
            return [Path(prefix).expanduser() / 'include'], _lib_dirs(prefix)
    raise ConfigError(f'the {NAME} profile needs {LIBRARY}: install {APT}, or set {ROOT_VARIABLE} '
                      'to its install prefix')


def _library(dirs, name):
    for d in dirs:
        candidate = d / f'lib{name}.so'
        if candidate.exists():
            return candidate
    raise ConfigError(f'no lib{name}.so in {", ".join(map(str, dirs)) or "the library directories"}: '
                      f'install {APT}')


def version(includes):
    header, pattern = VERSION
    for d in [*includes, *map(Path, IMPLICIT)]:
        path = d / header
        if path.is_file():
            m = re.search(pattern, path.read_text(errors='replace'))
            if m:
                return '.'.join(g for g in m.groups() if g)
    return '?'


def configure(ctx):
    includes, lib_dirs = find(ctx)
    flags = []
    for d in includes:
        if str(d) not in IMPLICIT:   # -isystem /usr/include breaks #include_next
            flags += ['-isystem', str(d)]
    flags += ['-D' + d for d in DEFINES]
    return Profile(name=NAME, flags=flags, libs=[_library(lib_dirs, n) for n in LIBS],
                   prelude=[HERE / f'cpp_repl_{NAME}.h'], help=HELP,
                   banner=f'{LIBRARY} {version(includes)}', mounts=includes + lib_dirs)
