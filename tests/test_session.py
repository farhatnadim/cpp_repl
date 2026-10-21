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


def test_a_header_the_library_lacks_is_explained(tmp_path, monkeypatch, made):
    """#include <print> on a standard library without it: the error names
    the input's line, through a placed file, and a note says which library
    has the header. A directive that works is journaled through its file."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('#include <print>')
    out = stream.getvalue()
    assert "fatal error: 'print' file not found" in out
    assert "[<print> is C++23, in libstdc++ 14 and libc++ 17: this clang-repl's standard library lacks it]" in out
    assert s.run_input('import std;')
    assert "[clang-repl has no module 'std': #include the headers]" in stream.getvalue()
    assert s.run_input('#define ANSWER 42')
    text, (sent,), kind = s.journal[-1]
    assert (text, kind) == ('#define ANSWER 42', 'pp')
    assert Path(sent[len('#include "'):-1]).read_text() == '#line 1 "<input>"\n#define ANSWER 42\n'


def test_a_lambda_uses_a_global_without_capturing_it(tmp_path, monkeypatch, made):
    """`[cap]` of a variable typed at the prompt, a global, and `[&]` at file
    scope are errors clang reports; cpp-repl drops the capture, says so, and
    runs the lambda on the global itself. The journal keeps the input as
    typed, and what ran replays."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('int cap = 1;')
    assert s.run_input('auto f = [cap](int x) { return x + cap; };')
    assert stream.getvalue() == '[cap is a global here: the lambda uses it without a capture]\n'
    text, (sent,), kind = s.journal[-1]
    assert text == 'auto f = [cap](int x) { return x + cap; };' and s.failures == 0
    assert Path(sent[len('#include "'):-1]).read_text() == '#line 1 "<input>"\nauto f = [](int x) { return x + cap; };\n'
    assert s.run_input('auto g = [&] { return cap; };')
    assert stream.getvalue().endswith('[a lambda at file scope has no capture-default: it uses the globals it names]\n')
    assert s.journal[-1].text == 'auto g = [&] { return cap; };'


def test_run_seconds_add_up_and_timeit_has_none(tmp_path, monkeypatch, made):
    """An input of several statements runs each in a function of its own, so
    their run marks add up; %timeit times its statement itself and leaves
    no run mark, so --json carries no run_seconds for it."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    s._show('\x1d~1000\x1d\n\x1d~2000\x1d\n')
    assert s.run_seconds == 3e-06
    assert s.run_input('%timeit 42') and s.run_seconds is None


def test_what_the_program_prints_is_never_a_diagnostic(tmp_path, monkeypatch, made):
    """A line of the program's own output that reads like clang's ("fatal
    error: ...", "JIT session error: ...") comes after the start mark, so it
    is output: the input is journaled, nothing is restored or run again."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('say("fatal error: not really");')
    assert s.run_input('say("JIT session error: not really");')
    assert stream.getvalue() == 'fatal error: not really\nJIT session error: not really\n'
    assert [e.text for e in s.journal] == ['say("fatal error: not really");', 'say("JIT session error: not really");']
    assert s.failures == 0
    # The compiler's own lines come before any statement runs, and still count.
    outcome = backend_module.outcome
    assert outcome('fatal error: x\nerror: Parsing failed.\n') == 'error'
    assert outcome('JIT session error: Symbols not found: [ f ]\n' + backend_module.START_MARK + 'fine\n') == 'link'
    assert outcome(backend_module.START_MARK + 'fatal error: x\nerror: Parsing failed.\n') == 'ok'


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
    assert stream.getvalue() == 'before\n[main returned 3]\n[the program ended: 1 input not run]\n'
    assert s.exit_status() == 3 and s.failures == 0 and s.skipped == 1
    assert [e.text for e in s.journal] == ['say("before");', 'return 3;']
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('if (true) return 2;')
    assert stream.getvalue() == '[main returned 2]\n'
    assert s.run_input('say("still here");')
    assert stream.getvalue().endswith('still here\n')


def test_a_name_declared_again_replaces_the_old(tmp_path, monkeypatch, made):
    """`int a` then `double a`: the session is rebuilt without the old a,
    the input runs there, and clang's error is never shown. A name the
    journal did not declare keeps its error."""
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('int a = 1;') and s.run_input('int b = 2;')
    first = s.backend.proc.pid
    assert s.run_input('double a = 1.5;')
    assert stream.getvalue() == '[a redefined: 1 input replayed]\n'
    # The new declaration takes the old one's place; what came after it is replayed on top.
    assert [e.text for e in s.journal] == ['double a = 1.5;', 'int b = 2;'] and s.backend.proc.pid != first
    assert s.run_input('say("alive");')
    assert stream.getvalue().endswith('alive\n')
    s.journal = [e for e in s.journal if e.text != 'int b = 2;']   # as if b were the prelude's
    assert s.run_input('int b = 3;')
    assert "error: redefinition of 'b'" in stream.getvalue()
    assert [e.text for e in s.journal] == ['double a = 1.5;', 'say("alive");']
    # The old entry goes whole: a name declared beside the redefined one goes with it, and the note says so.
    assert s.run_input('int c1 = 1, d1 = 2;') and s.run_input('double c1 = 3.5;')
    assert stream.getvalue().endswith('[c1 redefined: 2 inputs replayed; d1, declared with it, is gone]\n')


def test_a_run_says_what_it_left_unrun(tmp_path, monkeypatch, made):
    """When the program ends before the file does, the inputs left are
    counted and said, as --stop-on-error's are; --json's last line carries
    the count."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    inputs = inputs_from_repl('say("a");\ncrash();\nsay("b");\nsay("c");\n')
    assert s.run_inputs(inputs, echo=False) is False
    assert stream.getvalue().endswith('[clang-repl crashed (SIGSEGV)]\n[the program ended: 2 inputs not run]\n')
    assert s.skipped == 2 and s.exit_status() == 139
    out = io.StringIO()
    s.out = JsonOutput(out)
    s.out.finish(s.exit_status(), s)
    assert json.loads(out.getvalue()) == {'exit': 139, 'inputs': 2, 'failures': 1, 'skipped': 2}


def test_an_uncaught_exception_ends_the_program(tmp_path, monkeypatch, made):
    """An exception nothing catches is named, and ends the program as
    abort() would: a run of files stops with status 134. At the prompt the
    session goes on as it is, with no restart."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    inputs = inputs_from_repl('say("before");\nthrow_it();\nsay("after");\n')
    assert s.run_inputs(inputs, echo=False) is False
    assert stream.getvalue() == ('before\n[uncaught exception: std::runtime_error: boom]\n'
                                 '[the program ended: 1 input not run]\n')
    assert s.exit_status() == 134 and s.failures == 1
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    first = s.backend.proc.pid
    assert s.run_input('throw_it();')
    assert stream.getvalue() == '[uncaught exception: std::runtime_error: boom]\n'
    assert s.backend.proc.pid == first
    assert s.run_input('say("still here");')
    assert stream.getvalue().endswith('still here\n')


def test_a_stuck_line_arriving_in_pieces_is_seen(tmp_path, monkeypatch, made):
    """clang's "too many errors emitted" may reach the backend split across
    reads (the fake writes a byte at a time): it is still the sign that
    clang-repl 18 is stuck, and the session is rebuilt."""
    s, stream = make(tmp_path, monkeypatch, batch=False, FAKE_SPLIT='1')
    made.append(s)
    assert s.run_input('int a = 1;')
    assert s.run_input('too_many();') is True
    assert '[clang-repl got stuck on that input: restored 1 input]' in stream.getvalue()
    assert s.run_input('say("alive");')
    assert stream.getvalue().endswith('alive\n')


def test_tab_offers_the_commands_and_the_prelude_s_names(tmp_path, monkeypatch, made):
    from cpp_repl.ui import Names
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    names = Names(s)
    assert '%help' in names.matching('%he') and 'show' in names.matching('sho')
    assert 'tree' not in names.matching('tre')   # the usd profile's, offered with -p usd only


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
    # The first run's link error was the earlier error's: the note and the second run show.
    assert stream.getvalue().endswith('error: Parsing failed.\n[that input failed to link after an earlier error: '
                                      'restored 1 input; running it again]\nlinked\n')
    assert s.failures == 1                  # the input that failed to compile
    assert [e.text for e in s.journal] == ['int a = 1;', 'stale();']


def test_an_input_that_fails_to_link_again_shows_its_error_once(tmp_path, monkeypatch, made):
    s, stream = make(tmp_path, monkeypatch, batch=False)
    made.append(s)
    assert s.run_input('taint();')
    assert s.run_input('link_fail();')      # fails in the fresh clang-repl too: its own error
    assert stream.getvalue().endswith(
        '[that input failed to link after an earlier error: restored 0 inputs; running it again]\n'
        'JIT session error: Symbols not found: [ _Z9link_failv ]\n'
        'error: Failed to materialize symbols: { (main, { __stmts__1 }) }\n'
        '[that input failed to link: restored 0 inputs]\n')
    assert stream.getvalue().count('Symbols not found') == 1
    assert s.failures == 2


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


def test_ctrl_c_under_batch_exits_130(tmp_path, monkeypatch, made):
    """A Ctrl-C that stops an input of a --batch run ends it with 130, as a
    shell reports Ctrl-C, where it used to count as a failed input (1)."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.5)
        assert s.run_inputs(inputs_from_repl('say("one");\nhang();\nsay("two");\n'), echo=False) is False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert s.exit_status() == 130 and s.interrupted == 1


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
                 'void_call()', 'if (a) {\n  say("yes");\n}',
                 # A variable a later function or lambda names stays a global, and
                 # so does one its initialiser names (base, through n).
                 'int base = 1;', 'int n = base + 2;', 'int f() { return n; }', 'f()',
                 'int seen = 0;', 'auto count = [](int q) { return q + seen; };', 'count(1)',
                 'seen, 42',   # a comma expression is one argument to show()
                 # A name a lambda captures stays a local of main(), as typed.
                 'int cap2 = 1;', 'auto h = [cap2](int q) { return q + cap2; };'):
        assert s.run_input(text)
    s.run_input(f'%save {tmp_path / "s.repl"}')
    assert (tmp_path / 's.repl').read_text().startswith('#include <vector>\nint twice')
    s.run_input(f'%save-cpp {tmp_path / "s.cpp"}')
    assert stream.getvalue().endswith('; base, n, seen at file scope, for the functions and lambdas that use them\n')
    cpp = (tmp_path / 's.cpp').read_text()
    assert '// Globals, as at the prompt: base, n, seen. A function or a lambda below\n' in cpp
    assert '#include "cpp_repl.h"\n#include "cpp_repl_std.h"\n#include <vector>\n' in cpp
    assert ('\nint twice(int x) { return 2 * x; }\n\nint base = 1;\n\nint n = base + 2;\n\nint f() { return n; }\n\n'
            'int seen = 0;\n\nint main()\n{\n') in cpp
    assert ('  int a = twice(2);\n  show(42);\n  void_call();\n  if (a) {\n    say("yes");\n  }\n  show(f());\n'
            '  auto count = [](int q) { return q + seen; };\n  show(count(1));\n  show((seen, 42));\n'
            '  int cap2 = 1;\n  auto h = [cap2](int q) { return q + cap2; };\n  return 0;\n}') in cpp


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
    assert f'repl::print_expr(((\n#line 7 "{name}"\n  42\n), repl::printer' in sent[2]   # the line is the user's
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
        {'input': 1, 'file': name, 'line': 1, 'status': 'ok', 'run_seconds': 1.5e-06, 'output': 'one\n'},
        {'input': 2, 'file': name, 'line': 3, 'status': 'error', 'output': records[1]['output']},
        {'input': 3, 'file': name, 'line': 4, 'status': 'ok', 'run_seconds': 1.5e-06, 'output': '42\n'},
        {'exit': 1, 'inputs': 3, 'failures': 1, 'skipped': 0},
    ]
    assert "undeclared identifier 'error_here'" in records[1]['output']


def test_json_output_of_an_input_run_again(tmp_path, monkeypatch, made):
    for name in ('FAKE_PROMPT', 'FAKE_SPLIT', 'FAKE_MARKER', 'FAKE_ECHO', 'FAKE_HANG_START'):
        monkeypatch.delenv(name, raising=False)
    stream = io.StringIO()
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(FAKE), json=True))
    s = Session(cfg, tmp_path, batch=True, out=JsonOutput(stream))
    made.append(s)
    (tmp_path / 'p.repl').write_text('taint();\nstale();\n')
    s.run_inputs(inputs_from_file(tmp_path / 'p.repl'), echo=True)
    record = json.loads(stream.getvalue().splitlines()[1])
    assert (record['status'], record['output']) == ('ok', 'linked\n')
    assert record['notes'] == ['that input failed to link after an earlier error: restored 0 inputs; running it again']


def test_time_tells_the_run_from_the_compile(tmp_path, monkeypatch, made):
    """%time says how long the code itself ran, which the prelude reports for
    a statement that ran in a function. A declaration at file scope runs in
    none, so its line has the wall time alone."""
    s, stream = make(tmp_path, monkeypatch)
    made.append(s)
    assert s.run_input('%time say("timed");')
    timed = stream.getvalue()
    assert timed.startswith('timed\nwall time: ') and timed.endswith(', of which the code ran 1.5 µs\n')
    assert s.run_input('%time int plain = 1;')
    rest = stream.getvalue()[len(timed):]
    assert rest.startswith('wall time: ') and 'of which' not in rest


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
    run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '-std=c++17', '--batch', 'p.repl'],
                         cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    # On stderr: a batch run's stdout is the program's own.
    assert run.stderr == '[-std=c++17 raised to -std=c++20: the prelude needs C++20]\n'
    assert run.stdout == 'c++> say("hi");\nhi\n'
    run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '-std=c++17', '--batch', '--json', 'p.repl'],
                         cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert json.loads(run.stdout.splitlines()[0]) == {'note': '-std=c++17 raised to -std=c++20: the prelude needs C++20'}


def test_batch_without_files_and_serve_with_them_say_so(tmp_path):
    """--batch with no files would run nothing and exit 0, the inputs piped
    in going to a program that never runs; --serve never runs files. Both
    stop before clang-repl starts, with what to do instead."""
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')
    run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '--batch'], cwd=tmp_path, env=env,
                         input='say("hi");\n', capture_output=True, text=True, timeout=60)
    assert (run.returncode, run.stdout) == (2, '')
    assert run.stderr == 'cpp-repl: --batch runs files: name one, or leave out --batch to run inputs piped in\n'
    run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config'], cwd=tmp_path, env=env,
                         input='say("hi");\n', capture_output=True, text=True, timeout=60)
    assert (run.returncode, run.stdout) == (0, 'c++> say("hi");\nhi\n')
    (tmp_path / 'p.repl').write_text('say("hi");\n')
    run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '--serve', 'repl.sock', 'p.repl'],
                         cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and not (tmp_path / 'repl.sock').exists()
    assert run.stderr == ('cpp-repl: --serve runs no files: start it without them, then send them with '
                          '--connect\n')


def test_the_files_after_the_end_of_the_program_are_named(tmp_path):
    """A return from main() ends the program, as it does compiled, so the
    files after it never run: the run names them, and --json counts their
    inputs as skipped. A final `return 0;` says nothing of its own."""
    (tmp_path / 'prog.cpp').write_text('int main()\n{\n    say("ran");\n    return 0;\n}\n')
    (tmp_path / 'more.repl').write_text('say("after");\nsay("again");\n')
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')

    def run(*args):
        return subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '--batch', *args], cwd=tmp_path,
                              env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    done = run('prog.cpp', 'more.repl')
    assert (done.returncode, done.stdout) == (0, 'c++> say("ran");\nran\nc++> return 0;\n'
                                                 '[main returned 0: more.repl not run]\n')
    records = [json.loads(line) for line in run('--json', 'prog.cpp', 'more.repl').stdout.splitlines()]
    assert records[-2:] == [{'note': 'main returned 0: more.repl not run'},
                            {'exit': 0, 'inputs': 2, 'failures': 0, 'skipped': 2}]
    (tmp_path / 'prog.cpp').write_text('int main()\n{\n    say("ran");\n    return 3;\n}\n')
    done = run('prog.cpp', 'more.repl')
    assert done.returncode == 3
    assert done.stdout.endswith('[main returned 3]\n[the program ended: more.repl not run]\n')


@pytest.mark.skipif(os.geteuid() == 0, reason='root writes anywhere')
def test_a_state_directory_that_cannot_be_written_says_so(tmp_path):
    """A read-only HOME, as in a coding agent's sandbox, stops the start with
    what to set, not a traceback."""
    (tmp_path / 'p.repl').write_text('say("hi");\n')
    locked = tmp_path / 'locked'
    locked.mkdir(mode=0o555)
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1', XDG_STATE_HOME=str(locked / 'state'))
    try:
        run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '--batch', 'p.repl'], cwd=tmp_path,
                             env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    finally:
        locked.chmod(0o755)
    assert run.returncode == 2
    assert run.stderr == (f'cpp-repl: cannot write the state directory {locked}/state/cpp-repl (Permission denied): '
                          'set XDG_STATE_HOME to a writable directory\n')


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
        assert records[0]['output'] == '42\n' and records[-1] == {'exit': 0, 'inputs': 1, 'failures': 0, 'skipped': 0}
        # --timeout and --stop-on-error hold for the run; the configuration is the server's.
        assert client('-q', '--timeout', '1', stdin='hang();\n').returncode == 124
        (tmp_path / 's.repl').write_text('error_here;\nsay("after");\n')
        stopped = client('-q', '--stop-on-error', 's.repl')
        assert stopped.returncode == 1 and 'after' not in stopped.stdout and 'not run' in stopped.stdout
        fixed = client('-p', 'eigen', stdin='42\n')
        assert fixed.returncode == 2 and 'configuration is fixed' in fixed.stderr
        assert client('-q', stdin='say("still here");\n').stdout == 'still here\n'
        assert client(stdin='%quit\n').returncode == 0
        assert server.wait(timeout=30) == 0
        assert not (tmp_path / 'repl.sock').exists()
        assert client().returncode == 2   # nothing serves there now
    finally:
        server.kill()
        server.wait()


def test_a_client_that_goes_away_mid_run(tmp_path):
    """A --connect client killed while its output streams: the server reads
    the input it is on to its end, stops the run there, and the next client
    gets a clean session whose journal holds what ran. SIGTERM then ends the
    server with 1, as cpp-repl's handler says."""
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
        (tmp_path / 'big.repl').write_text(''.join(f'say("line {i}");\n' for i in range(2000)))
        gone = subprocess.Popen([sys.executable, str(CPP_REPL), '--connect', 'repl.sock', '-q', 'big.repl'],
                                cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        assert gone.stdout.readline() == b'line 0\n'
        gone.kill()
        gone.wait()
        after = client('--json', stdin='42\n')
        records = [json.loads(line) for line in after.stdout.splitlines()]
        assert [r.get('output') for r in records if 'input' in r] == ['42\n'], after.stdout   # none of big.repl's
        assert records[-1] == {'exit': 0, 'inputs': 1, 'failures': 0, 'skipped': 0}
        entries = [line for line in client(stdin='%journal\n').stdout.splitlines() if line[:3].strip().isdigit()]
        assert entries[0].endswith(' say("line 0");') and entries[-1].endswith(' 42')
        assert 2 < len(entries) < 2001   # the run stopped once the client was found gone
        server.send_signal(signal.SIGTERM)
        assert server.wait(timeout=30) == 1
        assert not (tmp_path / 'repl.sock').exists()
    finally:
        server.kill()
        server.wait()


def test_serve_refuses_a_path_that_is_not_a_socket(tmp_path):
    """cpp-repl --serve FILE, FILE being a regular file, used to replace the
    file with the socket and delete it on exit."""
    precious = tmp_path / 'precious.txt'
    precious.write_text('keep me\n')
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')
    run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '--serve', 'precious.txt'], cwd=tmp_path,
                         env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and run.stderr == f'cpp-repl: precious.txt exists and is not a socket\n'
    assert precious.read_text() == 'keep me\n'


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
