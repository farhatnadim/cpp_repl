"""The wheel carries every file the package reads."""

import fnmatch
import json
import re
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


def test_versions_agree():
    """config.LLVM_RELEASE is the one place the release major is set: the
    default image, the Dockerfile's default and the checked-in probe results
    follow it, and the floor is no newer."""
    from cpp_repl.config import DEFAULT_IMAGE, LLVM_RELEASE, MIN_VERSION
    assert DEFAULT_IMAGE == f'cpp-repl:llvm{LLVM_RELEASE}'
    assert MIN_VERSION[0] <= LLVM_RELEASE
    dockerfile = (PACKAGE / 'docker' / 'Dockerfile').read_text()
    assert set(re.findall(r'^ARG LLVM_MAJOR=(\d+)$', dockerfile, re.M)) == {str(LLVM_RELEASE)}
    results = json.loads((ROOT / 'tests' / 'compat' / 'results' / f'compat-llvm{LLVM_RELEASE}.json').read_text())
    assert results['version'].split('.')[0] == str(LLVM_RELEASE)
