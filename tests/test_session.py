"""The backend protocol and the session's journal and recovery, against
tests/fake_clang_repl.py: no compiler needed."""

import io
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from cpp_repl import backend as backend_module
from cpp_repl import session as session_module
from cpp_repl.config import Config, Options
from cpp_repl.session import Output, Session, SessionError

FAKE = Path(__file__).resolve().parent / 'fake_clang_repl.py'
CPP_REPL = Path(__file__).resolve().parent.parent / 'bin' / 'cpp-repl'


def make(tmp_path, monkeypatch, batch=True, **env):
    for name in ('FAKE_PROMPT', 'FAKE_SPLIT', 'FAKE_MARKER', 'FAKE_ECHO', 'FAKE_HANG_START'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(backend_module, 'STUCK_GRACE', 0.5)
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(FAKE)))
    stream = io.StringIO()
    s = Session(cfg, tmp_path, batch=batch, out=Output(stream))
    return s, stream


@pytest.fixture
def made():
    sessions = []
    yield sessions
    for s in sessions:
        s.close()


VARIANTS = [
    {},
    {'FAKE_PROMPT': 'clang-repl> '},
    {'FAKE_SPLIT': '1'},
    {'FAKE_PROMPT': 'clang-repl> ', 'FAKE_SPLIT': '1'},
    {'FAKE_MARKER': 'error: Failed to load library: {path}'},
    {'FAKE_MARKER': 'error: Failed to load library: {path}', 'FAKE_PROMPT': '>>> '},
]


@pytest.mark.parametrize('env', VARIANTS, ids=lambda e: ','.join(f'{k}={v}' for k, v in e.items()) or 'plain')
def test_output_between_markers(tmp_path, monkeypatch, made, env):
    s, stream = make(tmp_path, monkeypatch, **env)
    made.append(s)
    assert s.backend.knows_errors
    assert s.run_input('say("hello");')
    assert s.run_input('say_raw("no newline");')
    assert s.run_input('42')
    out = stream.getvalue()
    assert out == 'hello\nno newline\n42\n'
    assert [e.kind for e in s.journal] == ['stmt', 'stmt', 'expr']


def test_an_echoed_marker_is_not_the_marker(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch, FAKE_ECHO='1')
    made.append(s)
    assert s.run_input('say("hi");')
    assert 'hi\n' in stream.getvalue()
    assert s.run_input('say("again");')
    assert 'again\n' in stream.getvalue()


def test_parse_errors_are_not_journaled(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('int a = 1;')
    assert s.run_input('error_here;')   # batch mode counts the failure and goes on
    assert "undeclared identifier 'error_here'" in stream.getvalue()
    assert [e.text for e in s.journal] == ['int a = 1;']
    assert s.failures == 1


def test_void_expressions(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('void_call()')
    assert s.journal[-1].kind == 'void'
    assert '\x1e' not in stream.getvalue()


@pytest.mark.parametrize('text, reason', [
    ('crash();', 'clang-repl crashed (SIGSEGV)'),
    ('link_fail();', 'that input failed to link'),
    ('too_many();', 'clang-repl got stuck on that input'),
])
def test_recovery_from_the_journal(tmp_path, monkeypatch, made, text, reason):
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('int a = 1;')
    assert s.run_input('int b = 2;')
    first = s.backend.proc.pid
    assert s.run_input(text) is True        # interactive: the session goes on
    assert f'[{reason}: restored 2 inputs]' in stream.getvalue()
    assert s.backend.proc.pid != first
    assert [e.text for e in s.journal] == ['int a = 1;', 'int b = 2;']
    assert s.run_input('say("alive");')
    assert stream.getvalue().endswith('alive\n')


def test_an_error_limit_asked_for_is_no_hang(tmp_path, monkeypatch, made):
    """With -ferror-limit=N, clang prints "too many errors emitted" and
    clang-repl goes on to the next input. Only a clang-repl that then
    answers nothing is stuck."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    first = s.backend.proc.pid
    assert s.run_input('limit();')
    assert s.failures == 1 and s.backend.proc.pid == first
    assert s.run_input('say("alive");')
    assert stream.getvalue().endswith('alive\n')


def test_batch_goes_on_after_a_link_failure(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('int a = 1;')
    first = s.backend.proc.pid
    assert s.run_input('link_fail();')      # batch mode counts the failure and goes on
    assert '[that input failed to link: restored 1 input]' in stream.getvalue()
    assert s.backend.proc.pid != first
    assert s.run_input('say("alive");')
    assert stream.getvalue().endswith('alive\n')
    assert s.failures == 1


@pytest.mark.parametrize('batch', [False, True])
def test_an_input_kept_from_linking_by_an_earlier_error_runs_again(tmp_path, monkeypatch, made, batch):
    s, stream = make(tmp_path, monkeypatch, batch=batch)
    made.append(s)
    assert s.run_input('int a = 1;')
    assert s.run_input('taint();')
    assert s.run_input('stale();')
    assert ('[that input failed to link after an earlier error: restored 1 input; running it again]'
            in stream.getvalue())
    assert stream.getvalue().endswith('linked\n')
    assert s.failures == 1                  # the input that failed to compile
    assert [e.text for e in s.journal] == ['int a = 1;', 'stale();']


def test_a_link_failure_with_no_earlier_error_runs_once(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('link_fail();')
    assert '[that input failed to link: restored 0 inputs]' in stream.getvalue()
    assert 'running it again' not in stream.getvalue()
    assert s.failures == 1


def test_ctrl_c_stops_an_input(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('int a = 1;')

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.5)
        s.run_input('hang();')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert '[interrupted: restored 1 input]' in stream.getvalue()
    assert s.run_input('say("back");')


def test_undo_reset_and_journal(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    for text in ('int a = 1;', 'int b = 2;', 'int c = 3;'):
        assert s.run_input(text)
    s.run_input('%undo')
    assert [e.text for e in s.journal] == ['int a = 1;', 'int b = 2;']
    s.run_input('%journal')
    assert '  1 int a = 1;\n  2 int b = 2;\n' in stream.getvalue()
    s.run_input('%reset')
    assert s.journal == []


def test_save_and_save_cpp(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    for text in ('#include <vector>', 'int twice(int x) { return 2 * x; }', 'int a = twice(2);', '42',
                 'void_call()', 'if (a) {\n  say("yes");\n}'):
        assert s.run_input(text)
    s.run_input(f'%save {tmp_path / "s.repl"}')
    assert (tmp_path / 's.repl').read_text().startswith('#include <vector>\nint twice')
    s.run_input(f'%save-cpp {tmp_path / "s.cpp"}')
    cpp = (tmp_path / 's.cpp').read_text()
    assert '#include "cpp_repl.h"\n#include "cpp_repl_std.h"\n#include <vector>\n' in cpp
    assert '\nint twice(int x) { return 2 * x; }\n' in cpp
    assert '  int a = twice(2);\n  show(42);\n  void_call();\n  if (a) {\n    say("yes");\n  }\n  return 0;\n}' in cpp


def test_a_backend_that_never_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, 'STARTUP_DEADLINE', 1)
    with pytest.raises(SessionError, match='no answer within 1 s'):
        make(tmp_path, monkeypatch, FAKE_HANG_START='1')


def test_a_prelude_that_fails_to_compile_stops_the_start(tmp_path, monkeypatch):
    with pytest.raises(SessionError, match="failed to start:\n.*'missing.h' file not found"):
        make(tmp_path, monkeypatch, FAKE_START_ERROR='1')


def test_inputs_through_a_file(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('#ifdef X\nint x;\n#endif')
    sent = s.journal[-1].sent[0]
    assert sent.startswith('#include "') and sent.endswith('input_1.h"')
    path = Path(sent[len('#include "'):-1])
    assert path.read_text() == '#ifdef X\nint x;\n#endif\n'
    s.close()
    made.remove(s)
    assert not path.exists()


def test_a_reader_that_goes_away(tmp_path):
    """cpp-repl --batch f | head: a quiet exit, with the status a shell
    reports for a C program that SIGPIPE stopped."""
    (tmp_path / 'many.repl').write_text(''.join(f'say("line {i}");\n' for i in range(200)))
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')
    proc = subprocess.Popen([sys.executable, str(CPP_REPL), '--no-config', '--batch', 'many.repl'],
                            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout.readline() == b'c++> say("line 0");\n'
    proc.stdout.close()
    assert proc.wait(timeout=60) == 141
    assert proc.stderr.read() == b''
