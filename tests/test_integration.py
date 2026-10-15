"""cpp-repl against a real clang-repl: CPP_REPL_CLANG_REPL, or the newest on
PATH. Skipped when there is none.

Golden sessions (tests/sessions/*.session) are transcripts: cpp-repl --batch
runs their inputs and must print them back. CPP_REPL_UPDATE_GOLDEN=1 rewrites
them from what this clang-repl prints (check the diff, then put back any
<any> lines it replaced)."""

import io
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


def run_cpp_repl(args, cwd, timeout=600):
    env = dict(os.environ, NO_COLOR='1', CPP_REPL_CLANG_REPL=CLANG_REPL)
    return subprocess.run([sys.executable, str(CPP_REPL), *args], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)


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


def test_emit_and_flags(tmp_path):
    (tmp_path / 'p.cpp').write_text('int main() { int a = 1; return a; }\n')
    assert run_cpp_repl(['--emit', 'p.cpp'], tmp_path).stdout == 'int a = 1;\na;\n'
    cflags = run_cpp_repl(['--cflags', '-O2', '-DX=1'], tmp_path).stdout
    assert '-O2' in cflags and '-DX=1' in cflags and 'prelude' in cflags


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
