"""The backend protocol and the session's journal and recovery, against
tests/fake_clang_repl.py: no compiler needed."""

import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cpp_repl import backend as backend_module
from cpp_repl import session as session_module
from cpp_repl.config import Config, Options
from cpp_repl.diagnostics import Diagnostics
from cpp_repl.lexer import in_function
from cpp_repl.session import JsonOutput, Output, Session, SessionError
from cpp_repl.sources import inputs_from_file, inputs_from_repl

FAKE = Path(__file__).resolve().parent / 'fake_clang_repl.py'
CPP_REPL = Path(__file__).resolve().parent.parent / 'bin' / 'cpp-repl'


def make(tmp_path, monkeypatch, batch=True, stop_on_error=False, timeout=None, **env):
    for name in ('FAKE_PROMPT', 'FAKE_SPLIT', 'FAKE_MARKER', 'FAKE_ECHO', 'FAKE_HANG_START'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(backend_module, 'STUCK_GRACE', 0.5)
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(FAKE), stop_on_error=stop_on_error,
                         timeout=timeout))
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


def test_a_declaration_without_its_semicolon_is_a_statement(tmp_path, monkeypatch, made):
    """`int x = 5` is not tried as an expression first: no failed compile,
    and the session is not tainted by one."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('int nosemi = 5')
    text, (sent,), kind = s.journal[-1]
    assert (text, kind) == ('int nosemi = 5', 'stmt') and not s.tainted
    assert Path(sent[len('#include "'):-1]).read_text() == '#line 1 "<input>"\nint nosemi = 5;\n'


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


def test_a_return_ends_the_program(tmp_path, monkeypatch, made):
    """`return N;` ends the program as main()'s return would: a run of files
    stops there and cpp-repl exits with N; the prompt says so and goes on."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    inputs = inputs_from_repl('say("before");\nreturn 3;\nsay("after");\n')
    assert s.run_inputs(inputs, echo=False) is False
    assert stream.getvalue() == 'before\n[main returned 3]\n'
    assert s.exit_status() == 3 and s.failures == 0
    assert [e.text for e in s.journal] == ['say("before");', 'return 3;']
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('if (true) return 2;')
    assert stream.getvalue() == '[main returned 2]\n'
    assert s.run_input('say("still here");')
    assert stream.getvalue().endswith('still here\n')


def test_an_uncaught_exception_ends_the_program(tmp_path, monkeypatch, made):
    """An exception nothing catches is named, and ends the program as
    abort() would: a run of files stops with status 134. At the prompt the
    session goes on as it is, with no restart."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    inputs = inputs_from_repl('say("before");\nthrow_it();\nsay("after");\n')
    assert s.run_inputs(inputs, echo=False) is False
    assert stream.getvalue() == 'before\n[uncaught exception: std::runtime_error: boom]\n'
    assert s.exit_status() == 134 and s.failures == 1
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    first = s.backend.proc.pid
    assert s.run_input('throw_it();')
    assert stream.getvalue() == '[uncaught exception: std::runtime_error: boom]\n'
    assert s.backend.proc.pid == first
    assert s.run_input('say("still here");')
    assert stream.getvalue().endswith('still here\n')


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


@pytest.mark.parametrize('inputs, status, failures', [
    (['int a = 1;'], 0, 0),
    (['error_here;', 'int a = 1;'], 1, 1),
    (['error_here;', 'crash();'], 139, 2),           # 128 + SIGSEGV, as a shell reports it
    (['say("done");', 'exit(0);'], 0, 0),            # the program's own end
    (['exit(3);'], 3, 1),
])
def test_exit_status(tmp_path, monkeypatch, made, inputs, status, failures):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    s.run_inputs(inputs, echo=False)
    assert (s.exit_status(), s.failures) == (status, failures)


@pytest.mark.parametrize('batch', [True, False])
def test_an_input_past_its_timeout_is_stopped(tmp_path, monkeypatch, made, batch):
    s, stream = make(tmp_path, monkeypatch, batch=batch, timeout=0.5)
    made.append(s)
    assert s.run_input('int a = 1;')
    first = s.backend.proc.pid
    assert s.run_input('hang();')               # the session goes on, without it
    assert '[that input ran past 0.5 s: restored 1 input]' in stream.getvalue()
    assert s.backend.proc.pid != first
    assert s.run_input('say("alive");')
    assert stream.getvalue().endswith('alive\n')
    assert (s.exit_status(), [e.text for e in s.journal]) == (124, ['int a = 1;', 'say("alive");'])


@pytest.mark.parametrize('stop', [False, True])
def test_stop_on_error(tmp_path, monkeypatch, made, stop):
    s, stream = make(tmp_path, monkeypatch, stop_on_error=stop)
    made.append(s)
    assert s.run_inputs(['say("one");', 'error_here;', 'say("two");', 'say("three");'], echo=False) is not stop
    if stop:
        assert stream.getvalue().endswith('[stopped at the first failure: 2 inputs not run]\n')
        assert 'two' not in stream.getvalue()
    else:
        assert stream.getvalue().endswith('two\nthree\n')


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
    assert path.read_text() == '#line 1 "<input>"\n#ifdef X\nint x;\n#endif\n'
    s.close()
    made.remove(s)
    assert not path.exists()


def test_inputs_from_a_file_sit_at_their_lines(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    (tmp_path / 'p.cpp').write_text('int main()\n{\n  say("one");\n  if (1) {\n    say("two");\n  }\n  42\n}\n')
    assert s.run_inputs(inputs_from_file(tmp_path / 'p.cpp'), echo=False)
    assert stream.getvalue() == 'one\ntwo\n42\n'
    sent = [Path(e.sent[0][len('#include "'):-1]).read_text() for e in s.journal]
    name = str(tmp_path / 'p.cpp')
    assert sent[0] == in_function('cpp_repl_1', f'\n#line 3 "{name}"\n  say("one");\n') + '\n'
    assert sent[1] == in_function('cpp_repl_2', f'\n#line 4 "{name}"\n  if (1) {{\n    say("two");\n  }}\n') + '\n'
    assert f'repl::print_expr(((\n#line 7 "{name}"\n  42), repl::printer' in sent[2]
    assert s.sources == {name}


def test_json_output(tmp_path, monkeypatch, made):
    for name in ('FAKE_PROMPT', 'FAKE_SPLIT', 'FAKE_MARKER', 'FAKE_ECHO', 'FAKE_HANG_START'):
        monkeypatch.delenv(name, raising=False)
    stream = io.StringIO()
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(FAKE), json=True))
    s = Session(cfg, tmp_path, batch=True, out=JsonOutput(stream))
    made.append(s)
    (tmp_path / 'p.repl').write_text('say("one");\n\nerror_here;\n42\n')
    s.run_inputs(inputs_from_file(tmp_path / 'p.repl'), echo=True)
    s.out.finish(s.exit_status(), s)
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    name = str(tmp_path / 'p.repl')
    assert [{k: v for k, v in r.items() if k != 'seconds'} for r in records] == [
        {'input': 1, 'file': name, 'line': 1, 'status': 'ok', 'output': 'one\n'},
        {'input': 2, 'file': name, 'line': 3, 'status': 'error', 'output': records[1]['output']},
        {'input': 3, 'file': name, 'line': 4, 'status': 'ok', 'output': '42\n'},
        {'exit': 1, 'inputs': 3, 'failures': 1},
    ]
    assert "undeclared identifier 'error_here'" in records[1]['output']


def test_quiet_leaves_out_the_echo(tmp_path, monkeypatch):
    for name in ('FAKE_PROMPT', 'FAKE_SPLIT', 'FAKE_MARKER', 'FAKE_ECHO', 'FAKE_HANG_START'):
        monkeypatch.delenv(name, raising=False)
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(FAKE), quiet=True))
    stream = io.StringIO()
    s = Session(cfg, tmp_path, batch=True, out=Output(stream))
    try:
        s.run_inputs(['say("one");', 'say("two");'], echo=True)
    finally:
        s.close()
    assert stream.getvalue() == 'one\ntwo\n'


def test_brief_diagnostics():
    names = {'p.cpp'}
    brief = Diagnostics(brief=True, names=names)
    lines = [
        'In file included from /x/y.h:3:',
        "p.cpp:4:5: error: cannot initialize a variable of type 'int' with an lvalue of type 'const char[2]'",
        '    4 |   int x = "s";',
        '      |       ^   ~~~',
        "/usr/include/c++/13/bits/../bits/stl_algo.h:9:2: note: candidate function not viable",
        '    9 |   void f(int);',
        '      |   ^',
        "p.cpp:4:5: error: cannot initialize a variable of type 'int' with an lvalue of type 'const char[2]'",
        '    4 |   int x = "s";',
        'fatal error: too many errors emitted, stopping now [-ferror-limit=]',
        'error: Parsing failed.',
        '  12 | a table the program printed',
    ]
    shown = brief.feed(''.join(line + '\n' for line in lines)).split('\n')
    assert shown == [lines[1], lines[2], lines[3], lines[4].replace('/bits/../bits/', '/bits/')
                     if Path('/usr/include/c++/13/bits/stl_algo.h').exists() else lines[4],
                     'error: Parsing failed.', lines[-1], '']
    full = Diagnostics(brief=False, names=names)
    shown = full.feed(''.join(line + '\n' for line in lines)).split('\n')
    assert shown == lines[:7] + lines[9:] + ['']   # the copy of the error goes, with its source line
    brief.start()
    symbols = ''.join(f'_Z{len(name)}{name}v, ' for name in ('a', 'b', 'c', 'd'))
    linked = brief.feed(f'JIT session error: Symbols not found: [ _ZN4repl3putI1QEEvRSoRKT_, {symbols}_Z1fv ]\n'
                        'error: Failed to materialize symbols: { (main, { __stmts__1 }) }\n')
    if shutil.which('c++filt'):
        assert linked == 'JIT session error: Symbols not found: [ a(), b(), c(), and 3 more ]\n'
    else:
        assert linked == 'JIT session error: Symbols not found: [ _Z1av, _Z1bv, _Z1cv, and 3 more ]\n'


def test_batch_says_what_the_configuration_changed(tmp_path):
    (tmp_path / 'p.repl').write_text('say("hi");\n')
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')
    out = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '-std=c++17', '--batch', 'p.repl'],
                         cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout
    assert out.startswith('[-std=c++17 raised to -std=c++20: the prelude needs C++20]\n')


def test_a_session_that_stays(tmp_path):
    """cpp-repl --serve SOCKET runs what cpp-repl --connect SOCKET sends, and
    the client exits with the run's status; %quit stops the server."""
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')
    server = subprocess.Popen([sys.executable, str(CPP_REPL), '--no-config', '--serve', 'repl.sock'], cwd=tmp_path,
                              env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def client(*args, stdin=''):
        return subprocess.run([sys.executable, str(CPP_REPL), '--connect', 'repl.sock', *args], cwd=tmp_path,
                              env=env, input=stdin, capture_output=True, text=True, timeout=60)

    try:
        for _ in range(200):
            if (tmp_path / 'repl.sock').exists():
                break
            time.sleep(0.05)
        # Whoever connects runs code as the server's user: the socket is theirs alone.
        assert stat.S_IMODE((tmp_path / 'repl.sock').stat().st_mode) == 0o600
        (tmp_path / 'p.repl').write_text('say("one");\nerror_here;\n')
        run = client('-q', 'p.repl')
        assert run.returncode == 1 and run.stdout.startswith('one\n') and 'error_here' in run.stdout
        records = [json.loads(line) for line in client('--json', '--fresh', stdin='42\n').stdout.splitlines()]
        assert records[0]['output'] == '42\n' and records[-1] == {'exit': 0, 'inputs': 1, 'failures': 0}
        assert client(stdin='%quit\n').returncode == 0
        assert server.wait(timeout=30) == 0
        assert not (tmp_path / 'repl.sock').exists()
        assert client().returncode == 2   # nothing serves there now
    finally:
        server.kill()
        server.wait()


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
