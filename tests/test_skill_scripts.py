"""The add-library skill's scripts (skills/add-library/scripts/): how they
read ELF files, linker scripts, link lines and --batch transcripts, and the
TOML they write. The tests that run cmake or read installed libraries skip
when those are missing; the end-to-end check needs a clang-repl."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import needs_clang_repl, sibling_clangxx

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'skills' / 'add-library' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402
import check_profile  # noqa: E402
import probe_library  # noqa: E402

LIBC = next((p for p in ('/usr/lib/x86_64-linux-gnu/libc.so.6', '/lib/x86_64-linux-gnu/libc.so.6',
                         '/usr/lib/aarch64-linux-gnu/libc.so.6', '/usr/lib64/libc.so.6') if Path(p).is_file()), None)
LIBZ = _common.ldconfig_cache().get('libz.so.1')


def script(name, *args, cwd):
    """A script's exit status and its JSON."""
    p = subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)], capture_output=True, text=True,
                       cwd=cwd, timeout=600)
    return p.returncode, json.loads(p.stdout)


# -- TOML ---------------------------------------------------------------------

def test_toml_text_reads_back():
    data = {'flags': ['-isystem', '/usr/include/opencv4', '-isystem', '/opt/x/include', '-pthread'],
            'defines': ['A=1', 'B'],
            'libs': ['/x/liba.so', '/x/libb.so', '/x/libc.so'], 'prelude': ['opencv_repl.h'], 'std': 'c++23'}
    text = _common.toml_text(data, ['a comment'])
    assert text.startswith('# a comment\n')
    assert '  "-isystem", "/usr/include/opencv4",\n' in text   # a flag and its value share a line
    assert tomllib.loads(text) == data


def test_toml_text_leaves_out_empty_keys():
    assert _common.toml_text({'libs': [], 'prelude': ['glm_repl.h'], 'std': None}) == 'prelude = ["glm_repl.h"]\n'


# -- ELF files and linker scripts ---------------------------------------------

def test_elf_kind(tmp_path):
    archive = tmp_path / 'libx.a'
    archive.write_bytes(b'!<arch>\n')
    linker_script = tmp_path / 'libc.so'
    linker_script.write_text('/* GNU ld script */\nGROUP ( /lib/libc.so.6 /usr/lib/libc_nonshared.a )\n')
    other = tmp_path / 'notes.txt'
    other.write_text('hello')
    assert _common.elf_kind(archive) == 'archive'
    assert _common.elf_kind(linker_script) == 'script'
    assert _common.elf_kind(other) == 'other'
    assert _common.elf_kind(tmp_path / 'missing.so') is None
    if LIBC:
        assert _common.elf_kind(LIBC) == 'shared'


def test_script_members(tmp_path):
    glibc = tmp_path / 'libc.so'
    glibc.write_text('/* GNU ld script\n   Use the shared library. */\nOUTPUT_FORMAT(elf64-x86-64)\n'
                     'GROUP ( /lib/x86_64-linux-gnu/libc.so.6 /usr/lib/x86_64-linux-gnu/libc_nonshared.a '
                     ' AS_NEEDED ( /lib64/ld-linux-x86-64.so.2 ) )\n')
    assert _common.script_members(glibc) == ['/lib/x86_64-linux-gnu/libc.so.6',
                                             '/usr/lib/x86_64-linux-gnu/libc_nonshared.a',
                                             '/lib64/ld-linux-x86-64.so.2']
    ncurses = tmp_path / 'libncurses.so'
    ncurses.write_text('INPUT(libncurses.so.6 -ltinfo)\n')
    assert _common.script_members(ncurses) == ['libncurses.so.6', '-ltinfo']


def test_dev_link(tmp_path):
    real = tmp_path / 'libfoo.so.1.2.3'
    real.write_bytes(b'')
    (tmp_path / 'libfoo.so').symlink_to(real.name)
    assert _common.dev_link(real) == str(tmp_path / 'libfoo.so')
    other = tmp_path / 'libbar.so.2'
    other.write_bytes(b'')
    (tmp_path / 'libbar.so').symlink_to(real.name)   # points elsewhere: kept as it is
    assert _common.dev_link(other) == str(other)


@pytest.mark.skipif(not (Path('/lib').is_symlink() and LIBC and LIBC.startswith('/usr/')),
                    reason='not a merged /usr system')
def test_canonical_prefers_usr():
    assert _common.canonical(LIBC.removeprefix('/usr')) == LIBC


def test_scratch_falls_back_to_the_project(tmp_path, monkeypatch):
    real = _common.tempfile.mkdtemp

    def refuse_outside(prefix=None, dir=None):   # a sandbox that allows writes in the project only
        if dir is None:
            raise PermissionError('read-only file system')
        return real(prefix=prefix, dir=dir)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_common.tempfile, 'mkdtemp', refuse_outside)
    with _common.scratch('probe-') as d:
        assert d.parent == tmp_path / '.cpp-repl' / '.tmp'
    assert not d.exists()
    _common.ignore_in_dot_dir(tmp_path, '.tmp/')   # listed once
    assert (tmp_path / '.cpp-repl' / '.gitignore').read_text() == '.tmp/\n'


# -- probe_library ------------------------------------------------------------

@pytest.mark.parametrize('name, other, same', [
    ('opencv', 'OpenCV', True), ('opencv', 'opencv4', True), ('opencv', 'c++', False),
    ('opencv', 'opencv_core', True), ('glm', 'gl', False), ('glm', 'GL', False), ('fmt', 'fmtmsg.h', False),
    ('yaml-cpp', 'yaml', False), ('yaml', 'yaml-cpp', True), ('eigen', 'Eigen3', True),
    ('boost', 'Boost-1.83.0', True), ('pcl', 'pcl_common', True), ('libfmt', 'fmt', True),
])
def test_similar(name, other, same):
    assert probe_library.similar(name, other) is same


@pytest.mark.parametrize('package, targets, components', [
    ('OpenCV', ['opencv_core', 'opencv_imgproc'], {'core', 'imgproc'}),
    ('Boost', ['Boost::filesystem', 'Boost::headers'], {'filesystem', 'headers'}),
    ('spdlog', ['spdlog::spdlog', 'spdlog::spdlog_header_only'], set()),
    ('glm', ['glm::glm'], set()),
    ('yaml-cpp', ['yaml-cpp::yaml-cpp'], set()),
])
def test_components_of(package, targets, components):
    assert probe_library.components_of(package, [{'name': t} for t in targets]) == components


def resolution():
    return probe_library.Resolution(clangxx=None, root=Path('/nonexistent'))


def test_link_items(tmp_path):
    res = resolution()
    archive = tmp_path / 'libyaml-cpp.a'
    archive.write_bytes(b'!<arch>\n')
    for item in ('-lm', '-lpthread', '-pthread', '-Wl,--as-needed', '-Wl,-rpath,/opt/x/lib', str(archive),
                 '-Wl,-Bstatic', '-framework'):
        res.link_item(item, 'test')
    assert res.libs == []
    assert {p['kind'] for p in res.problems} == {'static', 'framework'}
    assert {d['item'] for d in res.dropped} >= {'-lm', '-pthread', '-Wl,--as-needed', '-Wl,-rpath,/opt/x/lib'}


@pytest.mark.skipif(not LIBZ, reason='no libz.so.1 found')
def test_linker_script_is_followed(tmp_path):
    res = resolution()
    script_file = tmp_path / 'libwrap.so'
    script_file.write_text(f'GROUP ( {LIBZ} {LIBC} -lm )\n')   # libc and libm are the C runtime
    res.library_file(str(script_file), 'test')
    assert [lib['path'] for lib in res.libs] == [_common.dev_link(_common.canonical(LIBZ))]
    assert {d['item'] for d in res.dropped} >= {str(script_file), '-lm'}


def test_compile_flags():
    res = resolution()
    for flag in ('-DSPDLOG_SHARED_LIB', '-pthread', '-std=c++23', '-Werror', '-fopenmp', '-stdlib=libc++'):
        res.compile_flag(flag)
    res.compile_flag('-include', 'config.h')
    res.include('/usr/include')
    assert res.defines == ['SPDLOG_SHARED_LIB']
    assert res.flags == ['-pthread']
    assert res.std == 'c++23'
    assert {p['kind'] for p in res.problems} == {'openmp', 'unsupported-flag'}
    assert any(d['item'] == '/usr/include' for d in res.dropped)


needs_cmake = pytest.mark.skipif(not _common.shutil.which('cmake'), reason='no cmake')


@needs_cmake
@pytest.mark.skipif(not Path('/usr/include/glm/glm.hpp').is_file(), reason='no glm')
def test_resolve_header_only_cmake_package(tmp_path):
    code, out = script('probe_library.py', 'resolve', '--cmake', 'glm', '--toml', cwd=tmp_path)
    assert code == 0, out
    assert out['header_only'] and out['libs'] == [] and out['problems'] == []
    assert tomllib.loads(out['toml']) == {'prelude': ['glm_repl.h']}


@needs_cmake
@pytest.mark.skipif(not Path('/usr/include/spdlog/spdlog.h').is_file(), reason='no spdlog')
def test_resolve_cmake_defines_and_dependencies(tmp_path):
    code, out = script('probe_library.py', 'resolve', '--cmake', 'spdlog', cwd=tmp_path)
    assert code == 0, out
    assert {'SPDLOG_SHARED_LIB', 'SPDLOG_COMPILED_LIB', 'SPDLOG_FMT_EXTERNAL'} <= set(out['defines'])
    names = [Path(lib['path']).name for lib in out['libs']]
    assert names.index('libfmt.so') < names.index('libspdlog.so')   # dependencies load first


def test_find_unknown_library(tmp_path):
    code, out = script('probe_library.py', 'find', 'nosuchlibrary-xyz', cwd=tmp_path)
    assert code == 2 and out['suggest'] is None


def test_find_a_library_in_a_source_tree(tmp_path):
    """A library next to the project, in a source tree with a build of its
    own, rather than installed."""
    lib = tmp_path / 'mylib'
    (lib / 'include' / 'mylib').mkdir(parents=True)
    (lib / 'include' / 'mylib' / 'point.h').write_text('int norm();\n')
    (lib / 'build').mkdir()
    so = lib / 'build' / 'libmylib.so'
    so.write_bytes(b'\x7fELF\x02\x01\x01' + bytes(9) + (3).to_bytes(2, 'little') + bytes(64))
    (tmp_path / 'app').mkdir()
    (tmp_path / 'app' / 'main.cpp').write_text('#include <mylib/point.h>\nint main() { return norm(); }\n')
    code, out = script('probe_library.py', 'find', 'mylib', '--scan', '..', cwd=tmp_path / 'app')
    assert code == 0, out
    assert out['tree'] == {'headers': [{'dir': str(lib / 'include' / 'mylib'), 'include_root': str(lib / 'include'),
                                        'sample': ['point.h']}], 'libs': [str(so)]}
    assert out['suggest']['command'] == ['resolve', '--headers', str(lib / 'include'), '--lib', str(so),
                                         '--name', 'mylib']
    assert out['project_includes'] == [{'include': 'mylib/point.h', 'count': 1}]


# -- check_profile ------------------------------------------------------------

def checker(max_lines=12):
    c = check_profile.Checker.__new__(check_profile.Checker)
    c.cfg = SimpleNamespace(prompt='c++> ', more='...> ')
    c.args = SimpleNamespace(max_lines=max_lines)
    c.report = {'warnings': [], 'hints': []}
    return c


TRANSCRIPT = '''\
c++> int x = 1;
c++> x
1
c++> for (int i = 0; i < 2; ++i) {
...>   std::cout << i;
...> }
01
c++> big
''' + ''.join(f'{i}\n' for i in range(20)) + '''\
c++> std::vector<P>{p}
[<P>]
c++> f();
JIT session error: Symbols not found: [ _Z1fv ]
error: Failed to materialize symbols: { (main, { __orc_init_func.incr_module_5 }) }
[that input failed to link: restored 5 inputs]
c++> nope;
input_line_7:1:1: error: use of undeclared identifier 'nope'
error: Parsing failed.
c++> say(x);
JIT session error: Symbols not found: [ _Z3sayi ]
error: Failed to materialize symbols: { (main, { __orc_init_func.incr_module_8 }) }
[that input failed to link after an earlier error: restored 5 inputs; running it again]
1
c++> g();
JIT session error: Symbols not found: [ _Z1gv ]
error: Failed to materialize symbols: { (main, { __orc_init_func.incr_module_10 }) }
replay stopped at: for (int i = 0; i < 2; ++i) {
[that input failed to link: restored 2 inputs]
c++> x + 1
2
c++> *p = 1;
[clang-repl crashed (SIGSEGV)]
'''
INPUTS = ['int x = 1;', 'x', 'for (int i = 0; i < 2; ++i) {\n  std::cout << i;\n}', 'big', 'std::vector<P>{p}',
          'f();', 'nope;', 'say(x);', 'g();', 'x + 1', '*p = 1;', 'never_run()']


def test_transcript_split_and_classified():
    c = checker()
    results = c.classify(c.split(TRANSCRIPT, INPUTS))
    assert [r['status'] for r in results] == ['ok', 'ok', 'ok', 'ok', 'ok', 'link', 'error', 'ok', 'link', 'ok',
                                              'crash', 'not-run']
    assert results[2]['output'] == '01'
    assert results[3]['lines'] == 20
    # say(x); failed to link after nope; failed to compile: cpp-repl restored the
    # session and ran it again, and that run is the one that counts.
    assert results[7].get('reran') and (results[7]['output'], results[7]['lines']) == ('1', 1)
    assert [i for i, r in enumerate(results) if r.get('reran')] == [7]
    # The restore after g(); could not replay every input: what follows is unreliable.
    assert [i for i, r in enumerate(results) if r.get('unreliable')] == [9, 10]
    kinds = {(w['kind'], w['input']) for w in c.report['warnings']}
    assert kinds == {('verbose', 'big'), ('unprintable', 'std::vector<P>{p}')}


def test_hints():
    hints = check_profile.hints_for('JIT session error: Symbols not found: [ _ZN2cv12GaussianBlurE ]\n'
                                    'error: Failed to materialize symbols: { }', '.cpp-repl/x.toml')
    anchors = [h['anchor'] for h in hints]
    assert anchors == ['symbols-not-found']
    assert "symbol '_ZN2cv12GaussianBlurE' --config .cpp-repl/x.toml" in hints[0]['advice']
    # A printer instantiated by an input that failed to compile: no library is missing.
    stale = check_profile.hints_for('JIT session error: Symbols not found: [ _ZN9__gnu_cxxeqIPK1QE, '
                                    '_ZN4repl3putI1QEEvRSoRKT_ ]', 'x.toml')
    assert [h['anchor'] for h in stale] == ['stale-template']
    dso =check_profile.hints_for('/usr/bin/ld: /usr/lib/x86_64-linux-gnu/libfmt.so.9: error adding symbols: '
                                  'DSO missing from command line', 'x.toml')
    assert dso[0]['anchor'] == 'dso-missing' and 'libfmt.so.9' in dso[0]['advice']
    # Headers that use what C++26 removed, under cpp-repl's default standard.
    for text in ("mat.inl.hpp:281:22: error: invalid arithmetic between different enumeration types "
                 "('cv::_InputArray::KindFlag' and 'cv::AccessFlag')",
                 'error: invalid bitwise operation between different enumeration types',
                 "error: invalid arithmetic between enumeration type 'E' and floating-point type 'double'"):
        assert [h['anchor'] for h in check_profile.hints_for(text, 'x.toml')] == ['older-standard']


def test_golden_patterns():
    assert check_profile.matches('a\n<any>\nc\n', 'a\nb\nc\n')
    assert check_profile.matches('a\n<any lines>\nd\n', 'a\nb\nc\nd\n')
    assert check_profile.matches('p = 0x…\n', 'p = 0x7ffd1234\n')
    assert not check_profile.matches('a\nc\n', 'a\nb\nc\n')


# -- end to end -----------------------------------------------------------------

@pytest.mark.backend
@needs_clang_repl
@pytest.mark.skipif(sibling_clangxx() is None, reason='no clang++ next to clang-repl')
def test_check_profile_end_to_end(tmp_path):
    dot = tmp_path / '.cpp-repl'
    dot.mkdir()
    (dot / 'point.toml').write_text('defines = ["POINT_SCALE=2"]\nprelude = ["point_repl.h"]\n')
    (dot / 'point_repl.h').write_text(
        '#pragma once\n#include "cpp_repl.h"\n'
        'struct Point { double x, y; };\n'
        'inline Point scaled(Point p) { return {p.x * POINT_SCALE, p.y * POINT_SCALE}; }\n'
        'inline std::vector<Point> many(int n) { return std::vector<Point>(n); }\n'
        'inline std::ostream &operator<<(std::ostream &os, const Point &p)\n'
        '{ return os << "Point(" << p.x << ", " << p.y << ")"; }\n')
    (dot / 'point.repl').write_text('Point p{1, 2};\nscaled(p)\nstd::vector<Point>{p, p}\n')
    env_cmd = ['--config', dot / 'point.toml', '--example', dot / 'point.repl', '--runs', '1',
               '--report', dot / 'point.check.json', '--golden', dot / 'point.session']
    code, out = script('check_profile.py', *env_cmd, cwd=tmp_path)
    assert code == 0, json.dumps(out, indent=2)
    steps = out['steps']
    assert steps['prove_libs']['skipped'] and steps['roundtrip']['same']
    outputs = [i['output'] for i in steps['example']['inputs']]
    assert outputs == ['', 'Point(2, 4)', '[Point(1, 2), Point(1, 2)]']
    assert (dot / 'point.session').is_file() and (dot / 'point.check.json').is_file()
    code, out = script('check_profile.py', *env_cmd, cwd=tmp_path)   # the golden transcript now compares
    assert code == 0 and out['steps']['golden']['ok']
    # An input that could not link because the one before it failed to compile
    # (troubleshooting.md, stale-template): cpp-repl ran it again, and the
    # check reads that run as the one that counts.
    (dot / 'stale.repl').write_text('Point p{1, 2};\nmany()\nmany(2)\n')
    code, out = script('check_profile.py', '--config', dot / 'point.toml', '--example', dot / 'stale.repl',
                       '--runs', '1', cwd=tmp_path)
    inputs = out['steps']['example']['inputs']
    assert code == 1 and [i['status'] for i in inputs] == ['ok', 'error', 'ok']
    assert inputs[2].get('reran') and inputs[2]['output'] == '[Point(0, 0), Point(0, 0)]'
    assert not any(i.get('unreliable') for i in inputs) and out['steps']['example']['first_failure'] == 1
    # A prelude that fails half-way stops cpp-repl's start; the prelude step shows why.
    (dot / 'point_repl.h').write_text('#pragma once\n#include "no_such_header.h"\n')
    code, out = script('check_profile.py', *env_cmd[:4], '--runs', '1', cwd=tmp_path)
    assert code == 1 and not out['steps']['prelude']['ok']
    assert out['hints'][0]['anchor'] == 'file-not-found'
