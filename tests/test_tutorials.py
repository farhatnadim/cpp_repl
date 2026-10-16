"""The tutorials in docs/tutorials/: every marker and transcript parses, and,
against a real clang-repl, every checked transcript is what a run prints
(scripts/check_tutorial.py)."""

import sys
from pathlib import Path

import pytest

from conftest import CLANG_REPL, needs_clang_repl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import check_tutorial  # noqa: E402

TUTORIALS = sorted((ROOT / 'docs' / 'tutorials').glob('*.md'))


@pytest.mark.parametrize('path', TUTORIALS, ids=lambda p: p.stem)
def test_tutorial_parses(path):
    tutorial = check_tutorial.read_tutorial(path)
    assert not tutorial.errors, '\n'.join(tutorial.errors)


def test_a_transcript_outside_a_check_is_an_error(tmp_path):
    page = tmp_path / 'page.md'
    page.write_text('# A page\n\n```\nc++> 1 + 1\n2\n```\n')
    assert check_tutorial.read_tutorial(page).errors == [
        f'{page}:3: a transcript outside <!-- check: session/sh/off -->']
    page.write_text('<!-- check: off -->\n```\nc++> 1 + 1\n2\n```\n<!-- file: a.cpp -->\n```cpp\nint a;\n```\n')
    tutorial = check_tutorial.read_tutorial(page)
    assert not tutorial.errors and tutorial.steps == [('file', 'a.cpp', 'int a;\n')]


def test_heredocs_and_typed_diagnostics():
    assert check_tutorial.heredoc_word("cpp-repl <<'EOF'; echo $?") == 'EOF'
    assert check_tutorial.heredoc_word("cpp-repl <<< 'std::cout << 1;'") is None
    typed = Path('/tmp/x/typed-3.repl')
    output = ("/tmp/x/typed-3.repl:5:3: error: no member named 'push_bak'\n"
              "    5 | v.push_bak(9);\n      | ~ ^\n"
              "<command line>:1:9: note: expanded from macro 'X'\n    1 | #define X 1\n")
    assert check_tutorial.as_typed(output, typed, [1, 2, 5]) == (
        "<input>:1:3: error: no member named 'push_bak'\n"
        "    1 | v.push_bak(9);\n      | ~ ^\n"
        "<command line>:1:9: note: expanded from macro 'X'\n    1 | #define X 1\n")
    assert check_tutorial.untimed('654 µs ± 3.34 µs per loop (mean ± std. dev. of 7 runs, 500 loops each)') == \
        'T ± T per loop (mean ± std. dev. of N runs, N loops each)'


@pytest.mark.backend
@needs_clang_repl
@pytest.mark.parametrize('path', TUTORIALS, ids=lambda p: p.stem)
def test_tutorial_transcripts(path):
    result = check_tutorial.check(path, clang_repl=CLANG_REPL)
    if result.status == 'skipped':
        pytest.skip(result.report)
    assert result.status == 'ok', result.report
