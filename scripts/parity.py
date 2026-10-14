#!/usr/bin/env python3
"""Run the OpenUSD lessons through usd-repl and through cpp-repl, and diff
what they print and the files they write.

The lessons are the `repl_*` tests of an openusd-tutorial build: each test's
command names the lesson's sources. Every run starts in a fresh working
directory holding a copy of the lesson's _assets, as usd-repl's lessons get
under ctest.

    scripts/parity.py                          all lessons, ../openusd-tutorial
    scripts/parity.py 08_xform 14_primvar_deformation
    scripts/parity.py --keep                   leave the working directories

Exit status 1 if any lesson differs.
"""

import argparse
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPP_REPL = ROOT / 'bin' / 'cpp-repl'



def lessons(tutorial, build):
    """[(name, sources, clang_repl)] for each repl_* test of the build."""
    out = subprocess.run(['ctest', '--test-dir', str(build), '--show-only=json-v1', '-L', 'repl'],
                         capture_output=True, text=True, check=True).stdout
    found = []
    for test in json.loads(out)['tests']:
        cmd = test['command']
        sources = cmd[cmd.index('--batch') + 1:cmd.index('--cwd')]
        clang_repl = cmd[cmd.index('--clang-repl') + 1] if '--clang-repl' in cmd else None
        found.append((test['name'].removeprefix('repl_'), [Path(s) for s in sources], clang_repl))
    return found


def lesson_assets(source, tutorial):
    """The _assets directory of the lesson a source belongs to: the nearest
    directory above it whose CMakeLists.txt calls add_usd_example."""
    for d in source.resolve().parents:
        if d == tutorial or tutorial not in d.parents:
            return None
        cmakelists = d / 'CMakeLists.txt'
        if cmakelists.is_file() and re.search(r'^\s*add_usd_example\s*\(', cmakelists.read_text(), re.M):
            assets = d / '_assets'
            return assets if assets.is_dir() else None
    return None


def stage(workdir, sources, tutorial):
    (workdir / '_assets').mkdir(parents=True)
    assets = lesson_assets(sources[0], tutorial)
    if assets:
        shutil.copytree(assets, workdir / '_assets', dirs_exist_ok=True)


def snapshot(workdir):
    """{relative path: bytes} for every file under workdir."""
    return {str(p.relative_to(workdir)): p.read_bytes()
            for p in sorted(workdir.rglob('*')) if p.is_file()}


def normalise(text, workdir, replacements):
    text = text.replace(str(workdir), '<cwd>')
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def run(argv, workdir):
    proc = subprocess.run(argv, cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, errors='replace', timeout=600,
                          env=dict(os.environ, NO_COLOR='1'))
    return proc.returncode, proc.stdout + proc.stderr


def compare(name, sources, clang_repl, args, scratch):
    usd_dir, cpp_dir = scratch / name / 'usd-repl', scratch / name / 'cpp-repl'
    stage(usd_dir, sources, args.tutorial)
    stage(cpp_dir, sources, args.tutorial)
    common = ['--batch', *map(str, sources)]
    backend = ['--clang-repl', args.clang_repl or clang_repl] if (args.clang_repl or clang_repl) else []
    usd_argv = [str(args.usd_repl), *common, '--cwd', str(usd_dir),
                '--build-dir', str(args.build), *backend]
    cpp_argv = [str(CPP_REPL), *shlex.split(args.cpp_repl_args), *common, '--cwd', str(cpp_dir),
                '--build-dir', str(args.build), *backend]
    replacements = [(str(args.usd_repl.parent / 'usd_repl.h'), '<prelude>'),
                    (str(ROOT / 'src' / 'cpp_repl' / 'profiles' / 'usd' / 'usd.h'), '<prelude>')]
    usd_rc, usd_out = run(usd_argv, usd_dir)
    cpp_rc, cpp_out = run(cpp_argv, cpp_dir)

    problems = []
    if usd_rc != cpp_rc:
        problems.append(f'exit status: usd-repl {usd_rc}, cpp-repl {cpp_rc}')
    a, b = normalise(usd_out, usd_dir, replacements), normalise(cpp_out, cpp_dir, replacements)
    if a != b:
        diff = difflib.unified_diff(a.splitlines(), b.splitlines(), 'usd-repl', 'cpp-repl', lineterm='')
        problems.append('output differs:\n' + '\n'.join(list(diff)[:60]))
    files_a, files_b = snapshot(usd_dir), snapshot(cpp_dir)
    if files_a.keys() != files_b.keys():
        problems.append(f'files differ: only usd-repl {sorted(files_a.keys() - files_b.keys())}, '
                        f'only cpp-repl {sorted(files_b.keys() - files_a.keys())}')
    for rel in sorted(files_a.keys() & files_b.keys()):
        fa = normalise(files_a[rel].decode('utf-8', 'replace'), usd_dir, replacements)
        fb = normalise(files_b[rel].decode('utf-8', 'replace'), cpp_dir, replacements)
        if fa != fb:
            diff = difflib.unified_diff(fa.splitlines(), fb.splitlines(), f'usd-repl/{rel}',
                                        f'cpp-repl/{rel}', lineterm='')
            problems.append(f'{rel} differs:\n' + '\n'.join(list(diff)[:40]))
    return name, usd_rc, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('only', nargs='*', help='lesson names (default: all)')
    parser.add_argument('--tutorial', type=Path, default=ROOT.parent / 'openusd-tutorial')
    parser.add_argument('--build', type=Path, help='the tutorial build (default: TUTORIAL/build)')
    parser.add_argument('--usd-repl', type=Path, help='default: TUTORIAL/repl/usd-repl')
    parser.add_argument('--clang-repl', help='default: the one the ctest commands name')
    parser.add_argument('--cpp-repl-args', default='-p usd',
                        help='how cpp-repl is asked to behave like usd-repl (default: %(default)s)')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--keep', action='store_true', help='keep the working directories')
    args = parser.parse_args()
    args.tutorial = args.tutorial.resolve()
    args.build = (args.build or args.tutorial / 'build').resolve()
    args.usd_repl = (args.usd_repl or args.tutorial / 'repl' / 'usd-repl').resolve()

    found = lessons(args.tutorial, args.build)
    if args.only:
        found = [entry for entry in found if entry[0] in args.only]
    scratch = Path(tempfile.mkdtemp(prefix='cpp-repl-parity-'))
    failed = 0
    try:
        with ThreadPoolExecutor(args.jobs) as pool:
            jobs = [pool.submit(compare, name, sources, clang_repl, args, scratch)
                    for name, sources, clang_repl in found]
            for job in jobs:
                name, rc, problems = job.result()
                if problems:
                    failed += 1
                    print(f'DIFF {name}')
                    for p in problems:
                        print('  ' + p.replace('\n', '\n  '))
                else:
                    print(f'same {name} (exit {rc})')
    finally:
        if args.keep:
            print(f'working directories: {scratch}')
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    print(f'{len(found) - failed} of {len(found)} lessons identical')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
