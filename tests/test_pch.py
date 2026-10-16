"""The precompiled header of the preludes (pch.py), against the fake
clang-repl with a fake clang++ beside it: no compiler needed."""

import io
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpp_repl import pch
from cpp_repl.config import Config, ConfigError, Options
from cpp_repl.session import Output, Session

HERE = Path(__file__).resolve().parent
CPP_REPL = HERE.parent / 'bin' / 'cpp-repl'
PCH_STEP = '-x c++-header'   # what the precompiling call of clang++ has


@pytest.fixture
def toolchain(tmp_path, monkeypatch):
    """The fake clang-repl with the fake clang++ beside it, in a bin/ of
    their own (copies: sibling_clang resolves links), and the clang++'s
    precompiling calls. The clang-repl is clang-repl-99, so that without the
    clang++ beside it the one looked for on PATH is clang++-99: none."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for source, name in ((HERE / 'fake_clang_repl.py', 'clang-repl-99'), (HERE / 'fake_clangxx.py', 'clang++')):
        target = bin_dir / name
        shutil.copy(source, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / 'clang++.log'
    monkeypatch.setenv('FAKE_CLANGXX_LOG', str(log))
    for name in ('FAKE_CLANGXX_FAIL', 'FAKE_CLANGXX_FAIL_WHEN_EXISTS', 'FAKE_PCH_STALE', 'FAKE_PCH_LINK_FAIL'):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(clang_repl=str(bin_dir / 'clang-repl-99'),
                           builds=lambda: [c for c in (log.read_text().splitlines() if log.exists() else [])
                                           if PCH_STEP in c])


@pytest.fixture
def made():
    sessions = []
    yield sessions
    for s in sessions:
        s.close()


def config(toolchain, cwd, **options):
    return Config(Options(cwd=cwd, no_config=True, clang_repl=toolchain.clang_repl, **options))


def start(toolchain, cwd, **options):
    """A session as the prompt runs one, its notes in the stream."""
    cfg = config(toolchain, cwd, **options)
    stream = io.StringIO()
    return Session(cfg, cwd, batch=False, out=Output(stream)), stream


def test_the_key_depends_on_what_matters(toolchain, tmp_path):
    """The working directory does not count: its -iquote matters to nothing
    in the preludes. The flags, the preludes and their contents, and the
    libraries do."""
    a = config(toolchain, tmp_path / 'a')
    assert pch.key(a) == pch.key(config(toolchain, tmp_path / 'b'))
    assert pch.key(config(toolchain, tmp_path, flags=['-O2'])) != pch.key(a)
    assert pch.key(config(toolchain, tmp_path, no_std=True)) != pch.key(a)
    own = tmp_path / 'own.h'
    own.write_text('int one = 1;\n')
    c = config(toolchain, tmp_path, flags=['-include', str(own)])
    before = pch.key(c)
    assert before != pch.key(a)
    own.write_text('int one = 2;\n')
    assert pch.key(c) != before
    lib = tmp_path / 'libx.so'
    lib.write_text('')
    assert pch.key(config(toolchain, tmp_path, libs=[str(lib)])) != pch.key(a)


def test_built_once_then_cached(toolchain, tmp_path, made):
    s, stream = start(toolchain, tmp_path)
    made.append(s)
    cfg = s.cfg
    assert cfg.pch and cfg.pch.pch.is_file()
    assert cfg.pch.header == cfg.pch.dir.parent / f'{cfg.pch.dir.name}.h'   # beside the entry, never moved
    assert cfg.pch.header.read_text() == ''.join(f'#include "{h}"\n' for h in cfg.preludes)
    builds = toolchain.builds()
    assert len(builds) == 1 and '-Xclang -fincremental-extensions' in builds[0]
    assert f' {cfg.pch.header} -o ' in builds[0] and f'-iquote {tmp_path} ' not in builds[0]
    xcc = [a[len('--Xcc='):] for a in cfg.argv[1:] if a.startswith('--Xcc=')]
    assert '-include' not in xcc and xcc[-3:] == ['-fpch-validate-input-files-content', '-include-pch',
                                                  str(cfg.pch.pch)]
    assert '-include-pch' not in cfg.cflags()
    assert 'precompiled header' not in stream.getvalue()
    assert f'pch:         {cfg.pch.pch}\n' in s.describe_flags()
    # Another session with the same configuration, elsewhere, finds it.
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    t, _ = start(toolchain, elsewhere)
    made.append(t)
    assert t.cfg.pch == cfg.pch and len(toolchain.builds()) == 1


def test_no_pch_and_a_toml_s_pch_false(toolchain, tmp_path, made):
    s, _ = start(toolchain, tmp_path, no_pch=True)
    made.append(s)
    assert s.cfg.pch is None and not s.cfg.pch_wanted and toolchain.builds() == []
    assert '--Xcc=-include' in s.cfg.argv and 'pch:         none (--no-pch)' in s.describe_flags()
    off = tmp_path / 'off.toml'
    off.write_text('pch = false\n')
    t, _ = start(toolchain, tmp_path, configs=[str(off)])
    made.append(t)
    assert t.cfg.pch is None and toolchain.builds() == []
    assert f'pch:         none (pch = false in {off.resolve()})' in t.describe_flags()
    bad = tmp_path / 'bad.toml'
    bad.write_text('pch = "no"\n')
    with pytest.raises(ConfigError, match='pch must be true or false'):
        config(toolchain, tmp_path, configs=[str(bad)])


def test_no_clangxx_beside_clang_repl_means_no_pch(toolchain, tmp_path, made):
    os.unlink(Path(toolchain.clang_repl).parent / 'clang++')
    s, _ = start(toolchain, tmp_path)
    made.append(s)
    assert s.cfg.pch is None and 'pch:         none (no clang++ beside clang-repl to build one)' in s.describe_flags()


def test_a_failed_build_is_remembered_for_a_day(toolchain, tmp_path, made, monkeypatch):
    monkeypatch.setenv('FAKE_CLANGXX_FAIL', '1')
    s, stream = start(toolchain, tmp_path)
    made.append(s)
    note = ("[no precompiled header: precompiling the preludes failed: preludes.h:1:10: fatal error: 'broken.h' "
            'file not found; starting from the headers]')
    assert s.cfg.pch is None and note in stream.getvalue() and len(toolchain.builds()) == 1
    assert '--Xcc=-include' in s.cfg.argv and s.run_input('say("alive")') and stream.getvalue().endswith('alive\n')
    assert 'pch:         none (precompiling the preludes failed' in s.describe_flags()
    failed = pch._cache(s.cfg) / f'{pch.key(s.cfg)}.failed'
    assert failed.is_file()
    monkeypatch.delenv('FAKE_CLANGXX_FAIL')
    t, stream = start(toolchain, tmp_path)   # the next start goes without at once, and says nothing
    made.append(t)
    assert t.cfg.pch is None and 'precompiled header' not in stream.getvalue() and len(toolchain.builds()) == 1
    os.utime(failed, (time.time() - 2 * 86400,) * 2)
    u, stream = start(toolchain, tmp_path)
    made.append(u)
    assert u.cfg.pch and len(toolchain.builds()) == 2 and not failed.exists()


def test_concurrent_builds_make_one_entry(toolchain, tmp_path):
    cfg = config(toolchain, tmp_path)
    results, errors = [], []

    def go():
        try:
            results.append(pch.prepare(cfg))
        except Exception as e:   # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(results)) == 1 and len(toolchain.builds()) == 1
    assert not list(pch._cache(cfg).glob('*.building-*'))


def test_a_stale_pch_is_rebuilt_once(toolchain, tmp_path, made, monkeypatch):
    s, _ = start(toolchain, tmp_path)
    made.append(s)
    flag = tmp_path / 'stale.flag'
    monkeypatch.setenv('FAKE_PCH_STALE', str(flag))
    t, stream = start(toolchain, tmp_path)
    made.append(t)
    assert '[the precompiled header did not load: rebuilt]' in stream.getvalue()
    assert t.cfg.pch and len(toolchain.builds()) == 2 and flag.exists()
    assert t.run_input('say("alive")') and stream.getvalue().endswith('alive\n')


def test_a_stale_pch_whose_rebuild_fails_starts_from_the_headers(toolchain, tmp_path, made, monkeypatch):
    s, _ = start(toolchain, tmp_path)
    made.append(s)
    flag = tmp_path / 'stale.flag'
    monkeypatch.setenv('FAKE_PCH_STALE', str(flag))
    monkeypatch.setenv('FAKE_CLANGXX_FAIL_WHEN_EXISTS', str(flag))
    t, stream = start(toolchain, tmp_path)
    made.append(t)
    assert t.cfg.pch is None and len(toolchain.builds()) == 2
    assert '[no precompiled header: precompiling the preludes failed' in stream.getvalue()
    assert '--Xcc=-include' in t.cfg.argv
    assert t.run_input('say("alive")') and stream.getvalue().endswith('alive\n')


def test_a_start_that_fails_with_the_pch_goes_on_from_the_headers(toolchain, tmp_path, made, monkeypatch):
    """What clang-repl 23 does with OpenUSD's PCH: the start fails to link.
    The session starts from the headers, and so do the next day's, at once."""
    monkeypatch.setenv('FAKE_PCH_LINK_FAIL', '1')
    s, stream = start(toolchain, tmp_path)
    made.append(s)
    assert '[clang-repl did not start with the precompiled header: starting from the headers]' in stream.getvalue()
    assert s.cfg.pch is None and '--Xcc=-include' in s.cfg.argv
    assert s.run_input('say("alive")') and stream.getvalue().endswith('alive\n')
    assert 'pch:         none (clang-repl did not start with it: JIT session error' in s.describe_flags()
    t, stream = start(toolchain, tmp_path)
    made.append(t)
    assert t.cfg.pch is None and 'precompiled header' not in stream.getvalue() and len(toolchain.builds()) == 1


def test_a_batch_run_s_notes_go_to_stderr(toolchain, tmp_path, monkeypatch):
    prog = tmp_path / 'prog.repl'
    prog.write_text('say("hi")\n')
    monkeypatch.setenv('FAKE_PCH_LINK_FAIL', '1')
    done = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', '--clang-repl', toolchain.clang_repl,
                           '--batch', '-q', str(prog)], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout == 'hi\n'
    assert 'did not start with the precompiled header' in done.stderr


def test_blamed_knows_clang_s_word_on_a_pch():
    """What clang-repl printed at start-up on 2026-10-16 for a header changed
    since the PCH was built, a missing PCH and a PCH of another standard;
    and what is no fault of the PCH's."""
    p = pch.PCH(Path('/c'), Path('/c.h'), Path('/c/preludes.pch'))
    assert pch.blamed("fatal error: file '/x/inc.h' has been modified since the precompiled header "
                      "'/c/preludes.pch' was built: content changed\n", p)
    assert pch.blamed("error: unable to read PCH file /c/preludes.pch: 'No such file or directory'\n", p)
    assert pch.blamed('error: C++20 was enabled in PCH file but is currently disabled\n', p)
    assert not pch.blamed("/x/own.h:3:1: error: unknown type name 'foo'\nerror: Parsing failed.\n", p)
    assert not pch.blamed('JIT session error: Symbols not found: [ _Z1fv ]\n', p)


def test_the_command_line_switch(toolchain, tmp_path):
    prog = tmp_path / 'prog.repl'
    prog.write_text('say("hi")\n')
    base = [sys.executable, str(CPP_REPL), '--no-config', '--clang-repl', toolchain.clang_repl, '--batch', '-q']
    done = subprocess.run(base + ['--no-pch', str(prog)], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout == 'hi\n' and toolchain.builds() == []
    done = subprocess.run(base + [str(prog)], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout == 'hi\n' and len(toolchain.builds()) == 1
