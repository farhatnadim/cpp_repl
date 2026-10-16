"""Flags, their precedence, cpp-repl.toml, compile_commands.json and the
command line. No clang-repl needed."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from conftest import CLANG_REPL
from cpp_repl.cli import make_parser, split_compiler_flags
from cpp_repl.config import (CORE_PRELUDE, PRELUDE_DIR, STD_PRELUDE, Config, ConfigError, Options,
                             clang_repl_version, compile_command_flags, compile_commands_entry, default_std,
                             find_clang_repl, find_library, floor_std, merge_flags, parse_flags, read_toml,
                             stdlib_runtime)


def test_parse_flags(tmp_path):
    assert parse_flags(['-I', 'inc', '-isystem/sys', '-DX=1', '-U', 'Y', '-O2', '-std=c++23',
                        '-march=native', '-fopenmp', '-Wno-unused', '-pthread', '-stdlib=libc++',
                        '-include', 'pch.h', '--sysroot=/sr', '-isysroot', '/sr2', '--gcc-toolchain=/gcc',
                        '-nostdinc++'], tmp_path) == [
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
        ('other', ('-stdlib=libc++',)),
        ('prelude', ('-include', str(tmp_path / 'pch.h'))),
        ('other', ('--sysroot', '/sr')),
        ('other', ('-isysroot', '/sr2')),
        ('other', ('--gcc-toolchain=/gcc',)),
        ('other', ('-nostdinc++',)),
    ]
    with pytest.raises(ConfigError):
        parse_flags(['-lfoo'])
    with pytest.raises(ConfigError):
        parse_flags(['-I'])
    with pytest.raises(ConfigError):
        parse_flags(['-include'])


def test_merge_flags_precedence():
    low = parse_flags(['-I/low', '-DMODE=1', '-std=c++20', '-O1', '-Wno-a', '-include', '/a.h'])
    high = parse_flags(['-I/high', '-I/low', '-DMODE=2', '-O3', '-include', '/b.h', '-include', '/a.h'])
    flags, std, opt, preludes = merge_flags([low, high])
    assert flags == ['-I', '/high', '-I', '/low', '-Wno-a', '-DMODE=1', '-DMODE=2']
    assert (std, opt) == ('-std=c++20', '-O3')
    assert preludes == [Path('/a.h'), Path('/b.h')]


def test_the_newest_clang_repl_wins(tmp_path, monkeypatch):
    """An unversioned clang-repl on PATH no longer beats a newer clang-repl-N
    beside it: the newest LLVM version wins. CPP_REPL_CLANG_REPL still names
    one outright."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    # Versions above any installed in /usr/lib/llvm-N, which is searched too.
    for name, version in (('clang-repl', '98.1.3'), ('clang-repl-99', '99.1.4'), ('clang-repl-97', '97.1.0')):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho "Ubuntu LLVM version {version}"\n')
        script.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir))
    monkeypatch.delenv('CPP_REPL_CLANG_REPL', raising=False)
    assert find_clang_repl() == str(bin_dir / 'clang-repl-99')
    monkeypatch.setenv('CPP_REPL_CLANG_REPL', str(bin_dir / 'clang-repl'))
    assert find_clang_repl() == str(bin_dir / 'clang-repl')


def test_libcxx_needs_its_runtime(tmp_path):
    """-stdlib=libc++ loads libc++.so.1 before any code runs, as clang-repl
    itself is linked with libstdc++; without one installed, the start says
    which package brings it."""
    assert stdlib_runtime(['-O2'], '/usr/bin/clang-repl-18') is None
    assert stdlib_runtime(['-stdlib=libc++'], None) is None
    try:
        installed = find_library('c++')
    except ConfigError:
        installed = None
    if installed:
        pytest.skip('libc++ is installed here')
    fake = Path(__file__).resolve().parent / 'fake_clang_repl.py'
    with pytest.raises(ConfigError, match=r'-stdlib=libc\+\+: no libc\+\+\.so\.1 .* libc\+\+-99-dev'):
        Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(fake), flags=['-stdlib=libc++']))
    cfg = Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(fake)))
    assert cfg.runtime is None and not cfg.prologue[0].startswith('%lib')


def test_a_header_included_first_is_a_prelude(tmp_path):
    """-include FILE, on the command line or in a build's compile command,
    is parsed before the first input like the preludes, and named by
    --cflags for a saved program."""
    (tmp_path / 'pch.h').write_text('inline int from_pch() { return 1; }\n')
    cfg = Config(Options(cwd=tmp_path, no_config=True, flags=['-include', str(tmp_path / 'pch.h')]), backend=False)
    assert cfg.preludes[-1] == tmp_path / 'pch.h' and '-include' not in cfg.compiler_flags
    assert str(tmp_path) in cfg.cflags()
    with pytest.raises(ConfigError, match='no prelude header'):
        Config(Options(cwd=tmp_path, no_config=True, flags=['-include', 'missing.h']), backend=False)


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
                        '-std=gnu++20 -stdlib=libc++ -include pch.h -imacros m.h -march=native -Wall -Wextra '
                        '-Wno-deprecated -Werror -Werror=return-type -fno-exceptions -o a.o -c a.cpp'}
    notes = []
    # The build's warnings come along; its -Werror stays out: warnings stay warnings.
    assert compile_command_flags(entry, notes) == parse_flags(
        ['-DNDEBUG', '-I', str(tmp_path / 'inc'), '-isystem', '/usr/include/python3.12',
         '-std=gnu++20', '-stdlib=libc++', '-include', str(tmp_path / 'pch.h'), '-march=native', '-Wall', '-Wextra',
         '-Wno-deprecated', '-fno-exceptions'])
    assert notes == ["compile_commands.json's -imacros m.h is left out: a cpp-repl.toml's prelude includes a "
                     'header in every session']


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


@pytest.mark.parametrize('version, std', [
    ((18, 1, 3), '-std=gnu++26'),
    ((23, 1, 2), '-std=gnu++26'),
    ((16, 0, 6), '-std=gnu++23'),
    ((11, 1, 0), '-std=gnu++20'),
    (None, '-std=gnu++20'),
])
def test_the_default_standard_is_the_newest_the_clang_knows(version, std):
    assert default_std(version) == std


def test_the_default_standard_follows_the_clang_repl_found(tmp_path):
    cfg = Config(Options(cwd=tmp_path, no_config=True), backend=False)
    expected = default_std(clang_repl_version(CLANG_REPL)[0]) if CLANG_REPL else '-std=gnu++20'
    assert cfg.std == expected and cfg.describe().split(', ')[1] == expected.removeprefix('-std=')


def test_config_without_a_backend(tmp_path, monkeypatch):
    (tmp_path / 'cpp-repl.toml').write_text('defines = ["FROM_TOML=1"]\nflags = ["-O1", "-std=c++17"]\n')
    sub = tmp_path / 'sub'
    sub.mkdir()
    monkeypatch.chdir(tmp_path)   # -I inc is relative to where cpp-repl runs, not to --cwd
    cfg = Config(Options(cwd=sub, flags=['-O2', '-DFROM_CLI', '-I', 'inc']), backend=False)
    assert cfg.std == '-std=c++20' and any('raised' in n for n in cfg.notes)
    assert cfg.opt == '-O2'
    # The build line of a %save-cpp file names the project file this session found.
    assert cfg.command().startswith(f'cpp-repl --no-config -c {tmp_path / "cpp-repl.toml"} ')
    assert cfg.compiler_flags == ['-std=c++20', '-O2', '-I', str(tmp_path / 'inc'), '-DFROM_TOML=1',
                                  '-DFROM_CLI', '-iquote', str(PRELUDE_DIR), '-iquote', str(sub), '-DCPP_REPL=1']
    assert '-iquote' in cfg.cflags() and str(sub) in cfg.cflags()
    assert cfg.preludes == [CORE_PRELUDE, STD_PRELUDE]
    assert cfg.prompt == 'c++> ' and cfg.more == '...> '
    assert cfg.prologue[-1] == 'setvbuf(stdout, nullptr, _IONBF, 0);'
    assert Config(Options(cwd=sub, no_config=True, no_std=True), backend=False).preludes == [CORE_PRELUDE]


def test_relative_paths(tmp_path, monkeypatch):
    """A TOML's paths are relative to the TOML, its flags too; the command
    line's to where cpp-repl runs. command() makes them all absolute, for the
    build line in a %save-cpp file."""
    proj = tmp_path / 'proj'
    (proj / 'build').mkdir(parents=True)
    (proj / 'build' / 'compile_commands.json').write_text(json.dumps(
        [{'directory': str(proj / 'build'), 'file': 'a.cpp', 'command': 'c++ -c a.cpp'}]))
    (proj / 'x.toml').write_text('flags = ["-isystem", "vendor/include"]\n')
    (proj / 'libx.so').write_bytes(b'')
    monkeypatch.chdir(proj)
    options = Options(cwd=tmp_path, no_config=True, configs=['x.toml'], compile_commands='build',
                      flags=['-I', 'inc'], libs=['libx.so'])
    cfg = Config(options, backend=False)
    flags = cfg.compiler_flags
    assert flags[flags.index('-isystem') + 1] == str(proj / 'vendor' / 'include')
    assert flags[flags.index('-I') + 1] == str(proj / 'inc')
    monkeypatch.chdir(tmp_path)
    assert cfg.command() == (f'cpp-repl --no-config -c {proj / "x.toml"} --compile-commands '
                             f'{proj / "build" / "compile_commands.json"} -I {proj / "inc"} '
                             f'--lib {proj / "libx.so"}')
    fake = tmp_path / 'clang-repl'
    fake.touch()
    assert f'--clang-repl {fake}' in Config(Options(cwd=tmp_path, no_config=True, clang_repl=str(fake)),
                                           backend=False).command()


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


def test_glm_profile(tmp_path, monkeypatch):
    monkeypatch.delenv('GLM_ROOT', raising=False)
    try:
        cfg = Config(Options(cwd=tmp_path, profiles=['glm']), backend=False)
    except ConfigError as e:
        pytest.skip(str(e))   # glm is not installed here
    assert cfg.preludes[-1].name == 'cpp_repl_glm.h'
    assert cfg.profiles[0].banner.startswith('glm ')
    assert all(lib.is_file() for lib in cfg.libs)
    flags = cfg.compiler_flags
    assert not any(a in ('-I', '-isystem') and b in ('/usr/include', '/usr/local/include')
                   for a, b in zip(flags, flags[1:]))


def test_glm_profile_root_variable(tmp_path, monkeypatch):
    monkeypatch.setenv('GLM_ROOT', str(tmp_path))
    with pytest.raises(ConfigError, match='GLM_ROOT'):
        Config(Options(cwd=tmp_path, profiles=['glm']), backend=False)


def test_glm_profile_root_prefix(tmp_path, monkeypatch):
    (tmp_path / 'include' / 'glm').mkdir(parents=True)
    (tmp_path / 'include' / 'glm' / 'glm.hpp').write_text('')
    monkeypatch.setenv('GLM_ROOT', str(tmp_path))
    cfg = Config(Options(cwd=tmp_path, profiles=['glm']), backend=False)
    assert ['-isystem', str(tmp_path / 'include')] == cfg.profiles[0].flags


def test_usd_profile_takes_the_install_of_the_build(tmp_path, monkeypatch):
    """The OpenUSD named by the CMakeCache.txt beside compile_commands.json,
    or an error: another install found elsewhere would not match the build.
    Its base libraries come first, so a %save-cpp program links."""
    root = tmp_path / 'usd'
    (root / 'include' / 'pxr').mkdir(parents=True)
    (root / 'include' / 'pxr' / 'pxr.h').write_text('#define PXR_MINOR_VERSION 26\n')
    (root / 'lib').mkdir()
    for name in ('usd', 'sdf', 'arch'):
        (root / 'lib' / f'libusd_{name}.so').write_bytes(b'')
    build = tmp_path / 'build'
    build.mkdir()
    (build / 'compile_commands.json').write_text(json.dumps(
        [{'directory': str(build), 'file': 'a.cpp', 'command': 'c++ -c a.cpp'}]))
    (build / 'CMakeCache.txt').write_text(f'USD_ROOT:PATH={root}\n')
    monkeypatch.delenv('USD_ROOT', raising=False)
    options = Options(cwd=tmp_path, no_config=True, profiles=['usd'], compile_commands=str(build))
    cfg = Config(options, backend=False)
    assert [lib.name for lib in cfg.libs] == ['libusd_arch.so', 'libusd_sdf.so', 'libusd_usd.so']
    # The profile's note on its standard holds only while that standard is in use.
    assert cfg.std == '-std=gnu++20' and cfg.profile_notes and 'gnu++20' in cfg.profile_notes[0]
    with_std = Config(Options(cwd=tmp_path, no_config=True, profiles=['usd'], compile_commands=str(build),
                              flags=['-std=gnu++23']), backend=False)
    assert with_std.std == '-std=gnu++23' and with_std.profile_notes == []
    (build / 'CMakeCache.txt').write_text(f'USD_ROOT:PATH={tmp_path / "gone"}\n')
    monkeypatch.setenv('USD_ROOT', str(root))
    with pytest.raises(ConfigError, match='CMakeCache.txt has USD_ROOT=.*gone'):
        Config(options, backend=False)


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
    linked = tmp_path / 'linked'   # a -l library's directory, through LIBRARY_PATH
    linked.mkdir()
    monkeypatch.delenv('CPP_REPL_IMAGE', raising=False)
    monkeypatch.setenv('LIBRARY_PATH', str(linked))
    monkeypatch.setenv('PXR_ROOT', '/opt/usd')
    monkeypatch.delenv('USD_ROOT', raising=False)
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
    assert f'{linked}:{linked}:ro' in volumes
    forwarded = [cmd[i + 1] for i, a in enumerate(cmd) if a == '-e']
    assert 'PXR_ROOT' in forwarded and 'LIBRARY_PATH' in forwarded and 'USD_ROOT' not in forwarded
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


def test_docker_runs_where_cpp_repl_runs(tmp_path, monkeypatch):
    """With --cwd elsewhere, the container still starts in the directory
    cpp-repl runs in, so that relative paths on the command line mean the
    same inside."""
    from types import SimpleNamespace

    from cpp_repl.docker import docker_command
    here, session = tmp_path / 'here', tmp_path / 'session'
    here.mkdir()
    session.mkdir()
    monkeypatch.chdir(here)
    monkeypatch.delenv('CPP_REPL_IMAGE', raising=False)
    options = Options(cwd=session, no_config=True)
    _, cmd = docker_command(['--docker', '--cwd', 'session'], SimpleNamespace(image=None, mount=[]), options)
    volumes = [cmd[i + 1] for i, a in enumerate(cmd) if a == '-v']
    assert f'{session}:{session}' in volumes and f'{here}:{here}:ro' in volumes
    assert cmd[cmd.index('-w') + 1] == str(here)


@pytest.mark.parametrize('image, expected', [
    ('proj-opencv:llvm23', 'docker build -t proj-opencv:llvm23 DIR'),
    ('cpp-repl:llvm-snapshot', 'docker-snapshot'),
    ('cpp-repl:llvm19', '--target apt --build-arg LLVM_MAJOR=19 --build-arg DOCKERFILE_SHA='),
])
def test_docker_builds_only_its_own_images(image, expected, tmp_path, monkeypatch, capsys):
    """A missing image: cpp-repl offers to build its own clang-repl images,
    and leaves any other tag to whoever made its Dockerfile."""
    import subprocess
    from types import SimpleNamespace

    from cpp_repl import docker
    monkeypatch.setattr(docker.shutil, 'which', lambda name: '/usr/bin/docker')
    monkeypatch.setattr(docker.subprocess, 'run',
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout='', stderr=''))
    monkeypatch.setattr(docker.os, 'execvp', lambda *a: pytest.fail('ran docker'))
    args = SimpleNamespace(image=image, mount=[], dry_run=False)
    options = Options(cwd=tmp_path, no_config=True)
    assert docker.run_in_docker(['--docker', '--image', image], args, options) == 2
    err = capsys.readouterr().err
    assert f'there is no image {image}' in err and expected in err


def test_the_inputs_of_ended_sessions_go(tmp_path, monkeypatch):
    """A session replays its inputs from its directory for as long as it
    runs: one on this machine goes when its process has gone, any other
    after a week."""
    from cpp_repl import config
    monkeypatch.setattr(config, '_alive', lambda pid: pid == os.getpid())
    host, month_ago = os.uname().nodename, time.time() - 30 * 86400
    dirs = {f'{host}-{os.getpid()}-1': month_ago,   # this process: kept, however old
            f'{host}-12345-1': None,                 # ended: gone at once
            'abc123def456-7-1': None,                # a container's, recent: kept
            'abc123def456-8-1': month_ago,           # a container's, a month old: gone
            '1234-5678': month_ago}                  # an older cpp-repl's name, a month old: gone
    for name, mtime in dirs.items():
        (tmp_path / name).mkdir()
        if mtime:
            os.utime(tmp_path / name, (mtime, mtime))
    config._prune(tmp_path, days=7, ended=config.session_ended)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([f'{host}-{os.getpid()}-1', 'abc123def456-7-1'])
    assert config.session_dir_name().startswith(f'{host}-{os.getpid()}-')


def test_libraries_named_with_l(tmp_path, monkeypatch):
    """-l NAME is found as the linker finds it, in the -L directories first;
    a linker script is followed to the shared object it names."""
    monkeypatch.delenv('LIBRARY_PATH', raising=False)
    elf = b'\x7fELF' + bytes(60)
    lib = tmp_path / 'lib'
    lib.mkdir()
    (lib / 'libfoo.so').write_bytes(elf)
    (lib / 'libbar.so').write_text('/* GNU ld script */\nINPUT ( libbar.so.2 )\n')
    (lib / 'libbar.so.2').write_bytes(elf)
    (lib / 'libqux.so.1').write_bytes(elf)
    (lib / 'libqux.so.10').write_bytes(elf)
    (lib / 'libbaz.a').write_bytes(b'!<arch>\n')
    monkeypatch.chdir(tmp_path)
    flags, rest = split_compiler_flags(['-L', 'lib', '-lfoo', '-l', 'bar', '-lqux', '-I', 'inc'])
    args = make_parser().parse_args(rest)
    cfg = Config(Options(cwd=tmp_path, no_config=True, link_names=args.link_names, link_dirs=args.link_dirs),
                 backend=False)
    assert flags == ['-I', 'inc']
    assert cfg.libs == [lib / 'libfoo.so', lib / 'libbar.so.2', lib / 'libqux.so.10']
    assert cfg.link_flags()[-1] == str(lib / 'libqux.so.10')
    assert '-L ' + str(lib) + ' -l foo -l bar -l qux' in cfg.command()
    with pytest.raises(ConfigError, match='only .*libbaz.a, a static archive'):
        Config(Options(cwd=tmp_path, no_config=True, link_names=['baz'], link_dirs=['lib']), backend=False)
    with pytest.raises(ConfigError, match='no library for -lnothere'):
        Config(Options(cwd=tmp_path, no_config=True, link_names=['nothere'], link_dirs=['lib']), backend=False)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='glibc')
def test_a_system_library_named_with_l():
    """glibc's libm.so is a linker script: -lm is libm.so.6."""
    assert find_library('m').name.startswith('libm.so.')


def test_what_the_configuration_leaves_out_is_said(tmp_path):
    build = tmp_path / 'build'
    build.mkdir()
    (build / 'pch.h').write_text('')
    (build / 'compile_commands.json').write_text(json.dumps([{
        'directory': str(build), 'file': 'a.cpp',
        'command': 'c++ -include pch.h -imacros m.h -Iinc -fsanitize=address -c a.cpp'}]))
    cfg = Config(Options(cwd=tmp_path, no_config=True, compile_commands=str(build)), backend=False)
    assert cfg.preludes[-1] == build / 'pch.h'   # the build's forced include is a prelude of the session's
    assert any("-imacros m.h is left out" in n for n in cfg.notes)
    assert not any(f.startswith('-fsanitize') for f in cfg.flags)   # the build's own, left out
    with pytest.raises(ConfigError, match="-fsanitize=address: clang-repl's JIT cannot run sanitized code"):
        Config(Options(cwd=tmp_path, no_config=True, flags=['-fsanitize=address']), backend=False)


def test_exceptions_follow_the_flags(tmp_path):
    """The function a statement runs in catches an uncaught exception only
    when the code compiles with exceptions; the last flag wins."""
    def exceptions(*flags):
        return Config(Options(cwd=tmp_path, no_config=True, flags=list(flags)), backend=False).exceptions
    assert exceptions() is True
    assert exceptions('-fno-exceptions') is False
    assert exceptions('-fno-exceptions', '-fexceptions') is True
    assert exceptions('-fexceptions', '-fno-exceptions') is False


def test_compile_command_response_files(tmp_path):
    """A build that keeps its flags in a response file (`c++ @flags.rsp`)
    still gives the session its include directories and definitions; one
    response file may name another, and a missing one is left as it is."""
    (tmp_path / 'flags.rsp').write_text('-I inc -DFOO=1\n"-DQUOTED=a b" @more.rsp\n')
    (tmp_path / 'more.rsp').write_text('-std=gnu++20\n')
    entry = {'directory': str(tmp_path), 'file': 'a.cpp', 'command': 'c++ @flags.rsp @missing.rsp -c a.cpp -o a.o'}
    assert compile_command_flags(entry) == parse_flags(['-I', str(tmp_path / 'inc'), '-DFOO=1', '-DQUOTED=a b',
                                                        '-std=gnu++20'])
