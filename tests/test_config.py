"""Flags, their precedence, cpp-repl.toml, compile_commands.json and the
command line. No clang-repl needed."""

import json
import os
from pathlib import Path

import pytest

from cpp_repl.cli import split_compiler_flags
from cpp_repl.config import (CORE_PRELUDE, PRELUDE_DIR, STD_PRELUDE, Config, ConfigError, Options,
                             compile_command_flags, compile_commands_entry, floor_std, merge_flags,
                             parse_flags, read_toml)


def test_parse_flags(tmp_path):
    assert parse_flags(['-I', 'inc', '-isystem/sys', '-DX=1', '-U', 'Y', '-O2', '-std=c++23',
                        '-march=native', '-fopenmp', '-Wno-unused', '-pthread'], tmp_path) == [
        ('include', ('-I', str(tmp_path / 'inc'))),
        ('include', ('-isystem', '/sys')),
        ('define', ('-DX=1',)),
        ('define', ('-UY',)),
        ('opt', ('-O2',)),
        ('std', ('-std=c++23',)),
        ('other', ('-march=native',)),
        ('other', ('-fopenmp',)),
        ('other', ('-Wno-unused',)),
        ('other', ('-pthread',)),
    ]
    with pytest.raises(ConfigError):
        parse_flags(['-lfoo'])
    with pytest.raises(ConfigError):
        parse_flags(['-I'])


def test_merge_flags_precedence():
    low = parse_flags(['-I/low', '-DMODE=1', '-std=c++20', '-O1', '-Wno-a'])
    high = parse_flags(['-I/high', '-I/low', '-DMODE=2', '-O3'])
    flags, std, opt = merge_flags([low, high])
    assert flags == ['-I', '/high', '-I', '/low', '-Wno-a', '-DMODE=1', '-DMODE=2']
    assert (std, opt) == ('-std=c++20', '-O3')


@pytest.mark.parametrize('std, raised', [
    ('-std=c++17', '-std=c++20'),
    ('-std=gnu++14', '-std=gnu++20'),
    ('-std=c++98', '-std=c++20'),
    ('-std=c++1z', '-std=c++20'),
    ('-std=c++20', None),
    ('-std=gnu++2a', None),
    ('-std=c++23', None),
    ('-std=c++26', None),
])
def test_floor_std(std, raised):
    flag, note = floor_std(std)
    if raised:
        assert flag == raised and note
    else:
        assert flag == std and note is None


def test_compile_command_flags(tmp_path):
    entry = {'directory': str(tmp_path), 'file': 'a.cpp',
             'command': 'c++ -DNDEBUG -I inc -isystem /usr/include/python3.12 -O3 -g -fPIC '
                        '-std=gnu++20 -march=native -Wall -Wno-deprecated -fno-exceptions '
                        '-o a.o -c a.cpp'}
    assert compile_command_flags(entry) == parse_flags(
        ['-DNDEBUG', '-I', str(tmp_path / 'inc'), '-isystem', '/usr/include/python3.12',
         '-std=gnu++20', '-march=native', '-Wno-deprecated', '-fno-exceptions'])


def test_compile_commands_entry_for_the_file(tmp_path):
    db = tmp_path / 'compile_commands.json'
    db.write_text(json.dumps([
        {'directory': str(tmp_path), 'file': 'a.cpp', 'command': 'c++ -DA -c a.cpp'},
        {'directory': str(tmp_path), 'file': str(tmp_path / 'b.cpp'), 'command': 'c++ -DB -c b.cpp'},
    ]))
    assert compile_commands_entry(db, [tmp_path / 'b.cpp'])['command'].endswith('b.cpp')
    assert compile_commands_entry(db, [tmp_path / 'other.cpp'])['command'].endswith('a.cpp')


def test_read_toml(tmp_path):
    (tmp_path / 'cpp-repl.toml').write_text(
        'std = "c++23"\ninclude = ["include"]\ndefines = ["FAST=1"]\nflags = ["-O2"]\n'
        'libs = ["build/libx.so"]\nprelude = ["extra.h"]\nprofiles = ["eigen"]\n'
        'compile_commands = "build"\n')
    p = read_toml(tmp_path / 'cpp-repl.toml')
    assert p.flags == ['-std=c++23', '-I', str(tmp_path / 'include'), '-DFAST=1', '-O2']
    assert p.libs == [tmp_path / 'build' / 'libx.so']
    assert p.prelude == [tmp_path / 'extra.h']
    assert p.profiles == ['eigen']
    assert p.compile_commands == tmp_path / 'build'
    (tmp_path / 'bad.toml').write_text('include_dirs = ["x"]\n')
    with pytest.raises(ConfigError, match='unknown keys include_dirs'):
        read_toml(tmp_path / 'bad.toml')


def test_config_without_a_backend(tmp_path):
    (tmp_path / 'cpp-repl.toml').write_text('defines = ["FROM_TOML=1"]\nflags = ["-O1", "-std=c++17"]\n')
    sub = tmp_path / 'sub'
    sub.mkdir()
    cfg = Config(Options(cwd=sub, flags=['-O2', '-DFROM_CLI', '-I', 'inc']), backend=False)
    assert cfg.std == '-std=c++20' and any('raised' in n for n in cfg.notes)
    assert cfg.opt == '-O2' and cfg.opt_level == 2
    assert cfg.compiler_flags == ['-std=c++20', '-O2', '-I', str(sub / 'inc'), '-DFROM_TOML=1',
                                  '-DFROM_CLI', '-iquote', str(PRELUDE_DIR), '-DCPP_REPL=1']
    assert cfg.preludes == [CORE_PRELUDE, STD_PRELUDE]
    assert cfg.prompt == 'c++> ' and cfg.more == '...> '
    assert cfg.prologue[-1] == 'setvbuf(stdout, nullptr, _IONBF, 0);'
    assert Config(Options(cwd=sub, no_config=True, no_std=True), backend=False).preludes == [CORE_PRELUDE]


def test_profiles_and_libraries_load_in_order(tmp_path):
    lib = tmp_path / 'libdemo.so'
    lib.write_bytes(b'')
    cfg = Config(Options(cwd=tmp_path, libs=[str(lib)]), backend=False)
    assert cfg.prologue[0].startswith('prctl(PR_SET_DUMPABLE')
    assert cfg.prologue[-1] == f'%lib {lib}'
    with pytest.raises(ConfigError, match='no profile'):
        Config(Options(cwd=tmp_path, profiles=['nonesuch']), backend=False)
    with pytest.raises(ConfigError, match='no library'):
        Config(Options(cwd=tmp_path, libs=[str(tmp_path / 'missing.so')]), backend=False)


@pytest.mark.skipif(not Path('/usr/include/eigen3/Eigen/Dense').is_file(), reason='no Eigen headers')
def test_eigen_profile(tmp_path, monkeypatch):
    monkeypatch.delenv('EIGEN_ROOT', raising=False)
    cfg = Config(Options(cwd=tmp_path, profiles=['eigen'], native=True), backend=False)
    assert cfg.preludes[-1].name == 'cpp_repl_eigen.h'
    assert '-march=native' in cfg.compiler_flags
    assert cfg.profiles[0].banner.startswith('Eigen ')
    assert 'cpp-repl -p eigen --native' == cfg.command()


def test_split_compiler_flags():
    flags, rest = split_compiler_flags(['-p', 'eigen', '-O3', '-I', 'inc', '-march=native', 'a.cpp',
                                        '-DX', '--batch', '-isystem', '/x', '-pthread', '--lib', 'l.so'])
    assert flags == ['-O3', '-I', 'inc', '-march=native', '-DX', '-isystem', '/x', '-pthread']
    assert rest == ['-p', 'eigen', 'a.cpp', '--batch', '--lib', 'l.so']


def test_docker_command(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from cpp_repl.docker import CHECKOUT, docker_command
    libdir = tmp_path / 'libs'
    libdir.mkdir()
    (libdir / 'libdemo.so').write_bytes(b'')
    extra = tmp_path / 'data'
    extra.mkdir()
    monkeypatch.delenv('CPP_REPL_IMAGE', raising=False)
    options = Options(cwd=tmp_path / 'work', no_config=True, libs=[str(libdir / 'libdemo.so')],
                      flags=['-I', '/usr/include/eigen3', '-O2'])
    (tmp_path / 'work').mkdir()
    args = SimpleNamespace(image=None, mount=[str(extra)])
    argv = ['--docker', '--mount', str(extra), '--clang-repl', '/host/clang-repl', '-O2', '--batch', 'x.cpp']
    image, cmd = docker_command(argv, args, options)
    assert image == 'cpp-repl:llvm23'
    volumes = [cmd[i + 1] for i, a in enumerate(cmd) if a == '-v']
    assert f'{tmp_path / "work"}:{tmp_path / "work"}' in volumes
    assert f'{CHECKOUT}:{CHECKOUT}:ro' in volumes
    assert f'{libdir}:{libdir}:ro' in volumes and f'{extra}:{extra}:ro' in volumes
    assert not any(v.startswith('/usr/include') for v in volumes)   # the image has its own
    assert cmd[cmd.index(image) + 1:] == ['python3', str(CHECKOUT / 'bin' / 'cpp-repl'), '-O2', '--batch', 'x.cpp']
    assert ['--user', f'{os.getuid()}:{os.getgid()}'] == cmd[cmd.index('--user'):cmd.index('--user') + 2]


def test_docker_command_from_the_checkout(monkeypatch):
    """Run from the checkout itself, it is mounted once, writable."""
    from types import SimpleNamespace

    from cpp_repl.docker import CHECKOUT, docker_command
    monkeypatch.delenv('CPP_REPL_IMAGE', raising=False)
    options = Options(cwd=CHECKOUT, no_config=True)
    _, cmd = docker_command(['--docker'], SimpleNamespace(image=None, mount=[]), options)
    volumes = [cmd[i + 1] for i, a in enumerate(cmd) if a == '-v']
    targets = [v.split(':')[1] for v in volumes]
    assert len(targets) == len(set(targets))
    assert f'{CHECKOUT}:{CHECKOUT}' in volumes
