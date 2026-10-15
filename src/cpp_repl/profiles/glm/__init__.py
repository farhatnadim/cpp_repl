"""glm: the header-only OpenGL Mathematics library in every session, and its
vectors, matrices and quaternions printed as values.

The headers are found from $GLM_ROOT (an install prefix), then pkg-config,
then the usual prefixes. An install is a prefix holding include/glm/glm.hpp.
"""

import re
import subprocess
from pathlib import Path

from ...config import ConfigError, Profile

HERE = Path(__file__).resolve().parent
ROOT_VARIABLE = 'GLM_ROOT'
MARKER = 'glm/glm.hpp'
IMPLICIT = ('/usr/include', '/usr/local/include')
PREFIXES = ['/usr', '/usr/local', '~/.local', '/opt/glm']

HELP = """\
  A vector prints as vec3(1, 2, 3), a quaternion as quat(w, x, y, z). A matrix
  prints as its rows, as on paper, though glm stores columns: m[c][r] is row
  r, column c. Inside a list, a matrix is nested brackets.
"""


def _is_prefix(path):
    return (Path(path).expanduser() / 'include' / MARKER).is_file()


def _pkg_config():
    """The include directories pkg-config names for glm; None when it has none."""
    try:
        out = subprocess.run(['pkg-config', '--cflags-only-I', 'glm'], capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [Path(f[2:]) for f in out.stdout.split() if f.startswith('-I')]


def find_includes(ctx):
    """The include directories of the install to use."""
    value = ctx.env.get(ROOT_VARIABLE)
    if value:
        if not _is_prefix(value):
            raise ConfigError(f'${ROOT_VARIABLE} is {value}, which holds no include/{MARKER}')
        return [Path(value).expanduser() / 'include']
    dirs = _pkg_config()
    if dirs is not None and any((d / MARKER).is_file() for d in [*dirs, *map(Path, IMPLICIT)]):
        return dirs
    for prefix in PREFIXES:
        if _is_prefix(prefix):
            return [Path(prefix).expanduser() / 'include']
    raise ConfigError(f'the glm profile needs the glm headers: install libglm-dev, or set {ROOT_VARIABLE} '
                      'to the install prefix holding include/glm/glm.hpp')


def version(includes):
    for d in [*includes, *map(Path, IMPLICIT)]:
        path = d / 'glm' / 'detail' / 'setup.hpp'
        if path.is_file():
            text = path.read_text(errors='replace')
            parts = [re.search(rf'#define GLM_VERSION_{n}\s+(\d+)', text)
                     for n in ('MAJOR', 'MINOR', 'PATCH', 'REVISION')]
            return '.'.join(m.group(1) for m in parts if m) or '?'
    return '?'


def configure(ctx):
    includes = find_includes(ctx)
    flags = []
    for d in includes:
        if str(d) not in IMPLICIT:   # -isystem /usr/include breaks #include_next
            flags += ['-isystem', str(d)]
    return Profile(name='glm', flags=flags, prelude=[HERE / 'cpp_repl_glm.h'], help=HELP,
                   banner=f'glm {version(includes)}', mounts=includes)
