"""Shared fixtures. Every test gets its own state directory, so that nothing
touches ~/.local/state/cpp-repl, ~/.clang-repl-history or /var/crash."""

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from cpp_repl.config import MIN_VERSION, ConfigError, clang_repl_version, find_clang_repl  # noqa: E402


@pytest.fixture(scope='session')
def built_once(tmp_path_factory):
    """The directories of what cpp-repl builds once per configuration (the
    precompiled headers, the preload library, the TBB overlay), shared by the
    whole run, so that the backend tests build each once."""
    return {name: tmp_path_factory.mktemp(name) for name in ('pch', 'preload', 'overlay')}


@pytest.fixture(autouse=True)
def state(tmp_path_factory, monkeypatch, built_once):
    """A private XDG_STATE_HOME for each test, apart from its tmp_path, with
    what is built once shared by the run."""
    root = tmp_path_factory.mktemp('state')
    monkeypatch.setenv('XDG_STATE_HOME', str(root))
    directory = root / 'cpp-repl'
    directory.mkdir()
    for name, shared in built_once.items():
        (directory / name).symlink_to(shared)
    return directory


def real_clang_repl():
    """The clang-repl the backend tests use, or None: none is found, or it
    is older than cpp-repl runs."""
    try:
        found = find_clang_repl()
        return found if clang_repl_version(found)[0] >= MIN_VERSION else None
    except ConfigError:
        return None


CLANG_REPL = real_clang_repl()
needs_clang_repl = pytest.mark.skipif(CLANG_REPL is None, reason=f'no clang-repl {MIN_VERSION[0]} or newer '
                                                                 '(set CPP_REPL_CLANG_REPL, or make test-docker)')


def clang_repl_major():
    import re
    import subprocess
    if CLANG_REPL is None:
        return 0
    out = subprocess.run([CLANG_REPL, '--version'], capture_output=True, text=True).stdout
    m = re.search(r'LLVM version (\d+)', out)
    return int(m.group(1)) if m else 0


def sibling_clangxx():
    from cpp_repl.config import sibling_clang
    return sibling_clang(CLANG_REPL) if CLANG_REPL else None


@pytest.fixture
def no_tty_env(monkeypatch):
    monkeypatch.setenv('NO_COLOR', '1')
    monkeypatch.delenv('CPP_REPL_IMAGE', raising=False)
