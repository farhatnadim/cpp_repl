"""cpp-repl against a real clang-repl: CPP_REPL_CLANG_REPL, or the newest on
PATH. Skipped when there is none.

Golden sessions (tests/sessions/*.session) are transcripts: cpp-repl --batch
runs their inputs and must print them back. CPP_REPL_UPDATE_GOLDEN=1 rewrites
them from what this clang-repl prints (check the diff, then put back any
<any> lines it replaced)."""

import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLANG_REPL, clang_repl_major, needs_clang_repl, sibling_clangxx
from cpp_repl.config import Config, ConfigError, Options
from cpp_repl.session import Output, Session

ROOT = Path(__file__).resolve().parent.parent
CPP_REPL = ROOT / 'bin' / 'cpp-repl'
SESSIONS = sorted((ROOT / 'tests' / 'sessions').glob('*.session'))

pytestmark = [pytest.mark.backend, needs_clang_repl]


def run_cpp_repl(args, cwd, timeout=600, stdin=None):
    """cpp-repl's result; stdin is the text it reads, or none."""
    env = dict(os.environ, NO_COLOR='1', CPP_REPL_CLANG_REPL=CLANG_REPL)
    return subprocess.run([sys.executable, str(CPP_REPL), *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=env, input=stdin,
                          stdin=subprocess.DEVNULL if stdin is None else None)


# -- golden sessions ----------------------------------------------------------

def read_session(path):
    """(header {key: value}, header text, transcript)."""
    header, head_lines = {}, []
    lines = path.read_text().split('\n')
    while lines and lines[0].startswith('#') and not re.match(r'#(include|define|if|pragma)', lines[0]):
        line = lines.pop(0)
        head_lines.append(line)
        key, sep, value = line[1:].partition(':')
        if sep and key.strip() in ('args', 'requires'):
            header[key.strip()] = value.strip()
    return header, '\n'.join(head_lines), '\n'.join(lines)


def inputs_of(transcript, prompt, more):
    inputs, current = [], None
    for line in transcript.split('\n'):
        if line.startswith(prompt):
            if current is not None:
                inputs.append('\n'.join(current))
            current = [line[len(prompt):]]
        elif current is not None and line.startswith(more):
            current.append(line[len(more):])
        elif current is not None:
            inputs.append('\n'.join(current))
            current = None
    if current is not None:
        inputs.append('\n'.join(current))
    return inputs


def normalise(text):
    text = re.sub(r'0x[0-9a-fA-F]+', '0x…', text)
    text = re.sub(r'input_line_\d+', 'input_line_N', text)
    return text.rstrip('\n') + '\n'


def matches(expected, actual):
    pattern = ''
    for line in normalise(expected).split('\n')[:-1]:
        if line == '<any lines>':
            pattern += r'(?:[^\n]*\n)*?'
        elif line == '<any>':
            pattern += r'[^\n]*\n'
        else:
            pattern += re.escape(line) + r'\n'
    return re.fullmatch(pattern, normalise(actual)) is not None


def unmet(requires):
    """Why a session cannot run here, or None."""
    for item in filter(None, (r.strip() for r in requires.split(','))):
        m = re.fullmatch(r'llvm>=(\d+)', item)
        if m and clang_repl_major() < int(m.group(1)):
            return f'needs clang-repl {m.group(1)} or newer'
        m = re.fullmatch(r'profile=(\w+)', item)
        if m:
            try:
                Config(Options(profiles=[m.group(1)], no_config=True), backend=False)
            except ConfigError as e:
                return str(e)
    return None


@pytest.mark.parametrize('path', SESSIONS, ids=lambda p: p.stem)
def test_golden_session(path, tmp_path):
    header, head, transcript = read_session(path)
    reason = unmet(header.get('requires', ''))
    if reason:
        pytest.skip(reason)
    args = shlex.split(header.get('args', ''))
    if 'profile=' in header.get('requires', ''):
        # A library's own headers may not compile with this clang-repl (OpenUSD
        # built with TBB 2020 does not, with clang-repl 23): that is a skip.
        empty = tmp_path / 'empty.repl'
        empty.write_text('')
        start = run_cpp_repl([*args, '--batch', '--no-color', str(empty)], tmp_path)
        if 'clang-repl failed to start' in start.stderr:
            first = next((line for line in start.stderr.splitlines() if 'error:' in line), start.stderr)
            pytest.skip(f'the profile does not start with this clang-repl: {first.strip()}')
    prompt = 'usd> ' if '-p usd' in header.get('args', '') else 'c++> '
    inputs = inputs_of(transcript, prompt, '.' * (len(prompt) - 2) + '> ')
    script = tmp_path / f'{path.stem}.repl'
    script.write_text('\n'.join(inputs) + '\n')
    result = run_cpp_repl([*args, '--batch', '--no-color', str(script)], tmp_path)
    actual = result.stdout + result.stderr
    if os.environ.get('CPP_REPL_UPDATE_GOLDEN') == '1':
        path.write_text(head + '\n' + actual)
        return
    assert matches(transcript, actual), f'{path.name} differs:\n{actual}'


# -- recovery, against the real thing ----------------------------------------

def make_session(tmp_path, **options):
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=CLANG_REPL, **options))
    stream = io.StringIO()
    return Session(cfg, tmp_path, batch=False, out=Output(stream)), stream


@pytest.fixture
def session(tmp_path):
    s, stream = make_session(tmp_path)
    yield s, stream
    s.close()


def test_a_crash_costs_nothing(session):
    s, stream = session
    assert s.run_input('int a = 41;')
    s.run_input('*(volatile int *)nullptr = 1;')
    assert 'restored 1 input' in stream.getvalue()
    assert s.run_input('a + 1')
    assert stream.getvalue().endswith('42\n')


def test_ctrl_c_stops_an_endless_loop(session):
    s, stream = session
    assert s.run_input('int a = 7;')

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 1.0)
        s.run_input('while (true) {}')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert '[interrupted: restored 1 input]' in stream.getvalue()
    assert s.run_input('a')
    assert stream.getvalue().endswith('7\n')


def test_a_link_failure_is_undone(session):
    s, stream = session
    assert s.run_input('int defined_one() { return 1; }')
    assert s.run_input('int declared_only();')
    s.run_input('declared_only();')
    assert 'that input failed to link' in stream.getvalue()
    assert s.run_input('defined_one() + 1')
    assert stream.getvalue().endswith('2\n')


def test_save_cpp_builds_and_prints_the_same(session, tmp_path):
    clangxx = sibling_clangxx()
    if not clangxx:
        pytest.skip('no clang++ next to clang-repl')
    s, stream = session
    inputs = ['#include <numeric>', 'std::vector<int> v{1, 2, 3, 4};',
              'int total(const std::vector<int> &x) { return std::accumulate(x.begin(), x.end(), 0); }',
              'total(v)', 'for (int x : v) {\n  std::cout << x << ";";\n}\nstd::cout << "\\n";',
              'std::map<int, char>{{1, \'a\'}}']
    for text in inputs:
        assert s.run_input(text), stream.getvalue()
    in_repl = stream.getvalue()
    source = tmp_path / 'saved.cpp'
    s.run_input(f'%save-cpp {source}')
    cflags = shlex.split(run_cpp_repl(['--cflags', '--no-config'], tmp_path).stdout)
    exe = tmp_path / 'saved'
    build = subprocess.run([clangxx, *cflags, str(source), '-o', str(exe)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    assert subprocess.run([str(exe)], capture_output=True, text=True).stdout == in_repl


def test_a_usd_session_saved_as_cpp_links(tmp_path):
    """--libs names the OpenUSD libraries a saved program calls into directly:
    SdfPath::GetName lives in sdf, which usd only pulls in at run time."""
    try:
        Config(Options(profiles=['usd'], no_config=True), backend=False)
    except ConfigError as e:
        pytest.skip(str(e))
    clangxx = sibling_clangxx()
    if not clangxx:
        pytest.skip('no clang++ next to clang-repl')
    (tmp_path / 'usd.repl').write_text('auto stage = UsdStage::CreateInMemory();\n'
                                       'auto xf = UsdGeomXform::Define(stage, SdfPath("/World"));\n'
                                       'xf.GetPrim().GetPath().GetName()\n'
                                       '%save-cpp saved.cpp\n')
    usd = ['-p', 'usd', '--no-config']
    repl = run_cpp_repl([*usd, '--batch', '--no-color', 'usd.repl'], tmp_path)
    if 'clang-repl failed to start' in repl.stderr:
        pytest.skip('the usd profile does not start with this clang-repl')
    assert repl.returncode == 0, repl.stdout + repl.stderr
    cflags = shlex.split(run_cpp_repl([*usd, '--cflags'], tmp_path).stdout)
    libs = shlex.split(run_cpp_repl([*usd, '--libs'], tmp_path).stdout)
    build = subprocess.run([clangxx, *cflags, 'saved.cpp', *libs, '-o', 'saved'], cwd=tmp_path,
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    assert subprocess.run([str(tmp_path / 'saved')], capture_output=True, text=True).stdout == '"World"\n'


def test_a_program_runs_and_keeps_its_variables(tmp_path):
    (tmp_path / 'helper.h').write_text('inline int helper() { return 20; }\n')
    (tmp_path / 'prog.cpp').write_text('''\
#include <cstdio>
#include "helper.h"
#ifdef CPP_REPL
static const char *where = "repl";
#else
static const char *where = "native";
#endif
static int finish(int n)
{
  std::printf("finish %d %s\\n", n, where);
  return 0;
}
int main(int argc, char **argv)
{
  int n = helper() + argc;
  #pragma GCC unroll 2
  for (int i = 0; i < 2; ++i)
    std::printf("i=%d\\n", i);
  return finish(n);
}
''')
    result = run_cpp_repl(['--batch', '--no-color', 'prog.cpp'], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'i=0\ni=1\n' in result.stdout
    assert 'finish 21 repl\n' in result.stdout


def test_a_return_inside_main_s_control_flow(tmp_path):
    """`if (...) { ...; return 1; }` in main() runs, taken or not, with no
    warning about the function cpp-repl runs it in. A return leaves that
    input only: the file's later inputs still run."""
    (tmp_path / 'prog.cpp').write_text('''\
#include <cstdio>
int main()
{
  int n = 2;
  if (n == 0) {
    std::puts("none");
    return 1;
  }
  if (n == 2) {
    std::puts("two");
    return 2;
  }
  std::printf("n=%d\\n", n);
  return 0;
}
''')
    result = run_cpp_repl(['--batch', '--no-color', 'prog.cpp'], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '\ntwo\n' in result.stdout and '\nn=2\n' in result.stdout and '\nnone\n' not in result.stdout
    assert 'warning' not in result.stdout + result.stderr


def test_an_error_names_its_line_in_the_file(tmp_path):
    """Each input reaches clang at its own line and column: errors, __FILE__
    and __LINE__ name the file. Brief diagnostics, the default with --batch,
    keep to the code's own lines."""
    (tmp_path / 'p.cpp').write_text('''\
#include <cstdio>
#include <vector>
int main()
{
  std::vector<int> v{3, 1, 2};
  for (int x : v) {
    int y = "s";
  }
  std::sort(v.begin(), v.end(), [](int a) { return a; });
  std::printf("%s:%d\\n", __FILE__, __LINE__);
}
''')
    result = run_cpp_repl(['--no-config', '--batch', '--no-color', 'p.cpp'], tmp_path)
    out = result.stdout
    assert result.returncode == 1, out + result.stderr
    assert "p.cpp:7:9: error: cannot initialize a variable of type 'int'" in out
    assert '\n    7 |     int y = "s";\n' in out
    assert 'p.cpp:9:33: note: candidate function not viable' in out
    assert out.endswith('p.cpp:10\n')
    assert 'In file included from' not in out and 'cpp_repl_' not in out and 'input_line' not in out
    assert len(out.splitlines()) < 40
    full = run_cpp_repl(['--no-config', '--batch', '--no-color', '--diagnostics', 'full', 'p.cpp'], tmp_path)
    assert len(full.stdout.splitlines()) > 2 * len(out.splitlines())


@pytest.mark.parametrize('end, status', [('std::abort();', 134), ('std::exit(5);', 5),
                                         ('*(volatile int *)nullptr = 1;', 139)])
def test_the_exit_status_is_the_program_s(tmp_path, end, status):
    """A program that crashes or calls exit() ends cpp-repl --batch with the
    status a shell would report for it."""
    (tmp_path / 'p.repl').write_text(f'std::puts("before");\n{end}\nstd::puts("after");\n')
    result = run_cpp_repl(['--no-config', '--batch', '--no-color', 'p.repl'], tmp_path)
    assert result.returncode == status, result.stdout + result.stderr
    assert 'before' in result.stdout and 'after' not in result.stdout


def test_an_endless_loop_stops_at_the_timeout(tmp_path):
    (tmp_path / 'p.repl').write_text('int n = 0;\nwhile (true) { ++n; }\nstd::puts("after");\n')
    result = run_cpp_repl(['--no-config', '--batch', '--no-color', '--timeout', '3', 'p.repl'], tmp_path)
    assert result.returncode == 124, result.stdout + result.stderr
    assert '[that input ran past 3 s: restored 1 input]' in result.stdout
    assert result.stdout.endswith('after\n')


def test_a_program_reads_cpp_repl_s_stdin(tmp_path):
    """cpp-repl --batch prog.cpp < input: the code reads the input, through
    std::cin and C's stdin, as the compiled program would."""
    (tmp_path / 'p.cpp').write_text('''\
#include <cstdio>
#include <iostream>
#include <string>
int main()
{
  int n = 0;
  std::cin >> n;
  std::string word;
  std::cin >> word;
  int m = 0;
  std::scanf("%d", &m);
  std::printf("%d %s %d\\n", n, word.c_str(), m);
}
''')
    result = run_cpp_repl(['--no-config', '--batch', '--no-color', 'p.cpp'], tmp_path, stdin='41 hello 7\n')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith('41 hello 7\n')


def test_piped_inputs_leave_the_code_end_of_file(tmp_path):
    result = run_cpp_repl(['--no-config', '--no-color'], tmp_path, timeout=120,
                          stdin='int k = 5;\nstd::cin >> k;\nstd::cin.eof()\n')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.endswith('true\n')


def test_quiet_and_json(tmp_path):
    (tmp_path / 'p.cpp').write_text('#include <cstdio>\nint main()\n{\n  std::puts("hi");\n  int x = "s";\n}\n')
    quiet = run_cpp_repl(['--no-config', '--batch', '--no-color', '-q', 'p.cpp'], tmp_path)
    assert quiet.returncode == 1
    assert quiet.stdout.startswith('hi\np.cpp:5:7: error: ') and 'c++>' not in quiet.stdout
    result = run_cpp_repl(['--no-config', '--batch', '--json', 'p.cpp'], tmp_path)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [(r.get('line'), r.get('status')) for r in records[:-1]] == [(1, 'ok'), (4, 'ok'), (5, 'error')]
    assert records[1]['output'] == 'hi\n' and records[-1] == {'exit': 1, 'inputs': 3, 'failures': 1}


def test_a_library_named_with_l(tmp_path):
    """-L DIR -l NAME loads libNAME.so, and --libs links a saved program with it."""
    clangxx = sibling_clangxx()
    if not clangxx:
        pytest.skip('no clang++ next to clang-repl')
    (tmp_path / 'answer.cpp').write_text('int answer() { return 42; }\n')
    (tmp_path / 'lib').mkdir()
    build = subprocess.run([clangxx, '-shared', '-fPIC', 'answer.cpp', '-o', 'lib/libanswer.so'], cwd=tmp_path,
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    (tmp_path / 'p.repl').write_text('int answer();\nanswer()\n')
    result = run_cpp_repl(['--no-config', '--batch', '-q', '-L', 'lib', '-lanswer', 'p.repl'], tmp_path)
    assert (result.returncode, result.stdout) == (0, '42\n'), result.stdout + result.stderr
    libs = run_cpp_repl(['--no-config', '-L', 'lib', '-l', 'answer', '--libs'], tmp_path).stdout
    assert str(tmp_path / 'lib' / 'libanswer.so') in libs


def test_emit_and_flags(tmp_path):
    (tmp_path / 'p.cpp').write_text('int main() { int a = 1; return a; }\n')
    assert run_cpp_repl(['--emit', 'p.cpp'], tmp_path).stdout == 'int a = 1;\na;\n'
    cflags = run_cpp_repl(['--cflags', '-O2', '-DX=1'], tmp_path).stdout
    assert '-O2' in cflags and '-DX=1' in cflags and 'prelude' in cflags


def test_an_error_limit_cuts_an_input_short_and_the_run_goes_on(tmp_path):
    """-ferror-limit=1 stops clang at the first error of an input; the next
    input still runs."""
    (tmp_path / 'p.repl').write_text('int a = "s"; int b = "t"; int c = "u";\n'
                                     'std::puts("alive");\n')
    result = run_cpp_repl(['--no-config', '-ferror-limit=1', '--diagnostics', 'full', '--batch', '--no-color',
                           'p.repl'], tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count('error: cannot initialize') == 1
    assert 'too many errors emitted' in result.stdout
    assert result.stdout.endswith('alive\n')
    assert 'stuck' not in result.stdout


def test_a_prelude_that_fails_to_compile_stops_the_start(tmp_path):
    (tmp_path / 'bad.h').write_text('int broken = ;\n')
    (tmp_path / 'bad.toml').write_text('prelude = ["bad.h"]\n')
    (tmp_path / 'empty.repl').write_text('')
    result = run_cpp_repl(['--no-config', '-c', 'bad.toml', '--batch', 'empty.repl'], tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'clang-repl failed to start' in result.stderr
    assert 'bad.h:1:' in result.stderr and 'error: expected expression' in result.stderr


def test_completion_reads_every_included_header(tmp_path):
    from cpp_repl.ui import completion_words, included_headers
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=CLANG_REPL))
    if not sibling_clangxx():
        pytest.skip('no clang++ next to clang-repl')
    assert len(included_headers(cfg)) > 100
    words = completion_words(cfg)
    assert {'push_back', 'unordered_map', 'accumulate', 'make_shared', 'timeit'} <= words
