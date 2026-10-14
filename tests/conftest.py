"""Shared fixtures. Every test gets its own state directory, so that nothing
touches ~/.local/state/cpp-repl, ~/.clang-repl-history or /var/crash."""

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from cpp_repl.config import ConfigError, find_clang_repl  # noqa: E402


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    """A private XDG_STATE_HOME for each test."""
    directory = tmp_path / 'state'
    monkeypatch.setenv('XDG_STATE_HOME', str(directory))
    return directory / 'cpp-repl'


def real_clang_repl():
    """The clang-repl the backend tests use, or None."""
    try:
        return find_clang_repl()
    except ConfigError:
        return None


CLANG_REPL = real_clang_repl()
needs_clang_repl = pytest.mark.skipif(CLANG_REPL is None, reason='no clang-repl (set CPP_REPL_CLANG_REPL)')


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
