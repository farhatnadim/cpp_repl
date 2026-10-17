"""The wheel carries every file the package reads."""

import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / 'src' / 'cpp_repl'


def test_every_data_file_is_package_data():
    """pyproject's package-data names every non-Python file under the
    package, so that a header or the Dockerfile cannot fall out of the
    wheel."""
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    patterns = pyproject['tool']['setuptools']['package-data']['cpp_repl']
    files = [p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
             if p.is_file() and p.suffix != '.py' and '__pycache__' not in p.parts
             and not p.name.startswith('.')]
    assert files
    missing = [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in patterns)]
    assert not missing, f'not in pyproject package-data: {missing}'
