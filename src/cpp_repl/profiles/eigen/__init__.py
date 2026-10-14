"""Eigen: <Eigen/Dense> in every session, and matrices printed with their
shape, as NumPy prints arrays.

The headers are found from $EIGEN_ROOT (a directory holding Eigen/Dense, or
an install prefix), then pkg-config, then the usual places.
"""

import re
import subprocess
from pathlib import Path

from ...config import ConfigError, Profile

HERE = Path(__file__).resolve().parent
CANDIDATES = ['/usr/include/eigen3', '/usr/local/include/eigen3', '/opt/homebrew/include/eigen3']

HELP = """\
  A matrix or array prints with its shape; past 10 rows or columns, only the
  first and last three. An expression such as A * B prints its value.
  %timeit C.noalias() = A * B   times a product (compile with -O2 or -O3 and
                                --native for speed: the JIT is -O0 by default)
"""


def _headers(path):
    path = Path(path).expanduser()
    for candidate in (path, path / 'include' / 'eigen3'):
        if (candidate / 'Eigen' / 'Dense').is_file():
            return candidate
    return None


def find_headers(ctx):
    """The directory holding Eigen/Dense."""
    value = ctx.env.get('EIGEN_ROOT') or ctx.env.get('EIGEN3_INCLUDE_DIR')
    if value:
        found = _headers(value)
        if not found:
            raise ConfigError(f'no Eigen/Dense under {value}')
        return found
    try:
        out = subprocess.run(['pkg-config', '--cflags-only-I', 'eigen3'], capture_output=True,
                             text=True, timeout=10).stdout
        for flag in out.split():
            found = _headers(flag.removeprefix('-I'))
            if found:
                return found
    except (OSError, subprocess.TimeoutExpired):
        pass
    for candidate in CANDIDATES:
        found = _headers(candidate)
        if found:
            return found
    raise ConfigError('the eigen profile needs the Eigen headers: install libeigen3-dev, '
                      'or set EIGEN_ROOT to the directory holding Eigen/Dense')


def version(headers):
    macros = headers / 'Eigen' / 'src' / 'Core' / 'util' / 'Macros.h'
    text = macros.read_text(errors='replace') if macros.is_file() else ''
    for name in ('Eigen/Version', 'Eigen/src/Core/util/Macros.h'):
        p = headers / name
        if p.is_file():
            m = re.search(r'#define EIGEN_VERSION_STRING "([^"]+)"', p.read_text(errors='replace'))
            if m:
                return m.group(1)
    parts = [re.search(rf'#define EIGEN_{n}_VERSION (\d+)', text) for n in ('WORLD', 'MAJOR', 'MINOR')]
    return '.'.join(m.group(1) for m in parts if m) or '?'


def configure(ctx):
    headers = find_headers(ctx)
    return Profile(name='eigen', flags=['-isystem', str(headers)], prelude=[HERE / 'cpp_repl_eigen.h'],
                   completion=[headers / 'Eigen' / 'src' / 'Core'], help=HELP,
                   banner=f'Eigen {version(headers)}', mounts=[headers])
