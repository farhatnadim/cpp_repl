"""What cpp-repl assumes about clang-repl's output, checked against the
clang-repl in use with the probes of tests/compat/harness.py. The backend
calibrates itself at startup; these assumptions are the ones it cannot learn."""

import importlib.util
import tempfile
from pathlib import Path

import pytest

from conftest import CLANG_REPL, needs_clang_repl

pytestmark = [pytest.mark.backend, needs_clang_repl]

HARNESS = Path(__file__).resolve().parent / 'compat' / 'harness.py'


@pytest.fixture(scope='module')
def harness():
    spec = importlib.util.spec_from_file_location('compat_harness', HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(harness, name):
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        probe = next(p for p in harness.probes(scratch) if p['name'] == name)
        return harness.run_probe(CLANG_REPL, probe, scratch)


def test_a_missing_library_names_its_path(harness):
    """The end-of-output marker is the error a %lib of a missing file prints:
    it must name the path, on one line."""
    result = run(harness, 'lib_error')
    assert '<path>' in result['verdict'], result['output']


def test_a_parse_error_says_so(harness):
    """outcome() tells a failed input by `error: Parsing failed.`."""
    assert run(harness, 'parse_error')['verdict'] == 'error: Parsing failed.'


def test_an_if_region_runs_through_an_include(harness):
    """#if regions are sent as an #include of a file."""
    assert run(harness, 'region')['verdict'] == 'works'
