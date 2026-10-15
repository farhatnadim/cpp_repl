#!/usr/bin/env python3
"""Run openusd-tutorial's lessons through cpp-repl -p usd and as the compiled
programs, and diff what they print and the files they write.

The lessons are the `repl_*` tests of an openusd-tutorial build: each test's
command names the lesson's sources, and the test of the same name without
`repl_` runs the compiled program. Every run starts in a fresh working
directory holding a copy of the lesson's _assets, as the lessons get under
ctest.

Both outputs are stdout and stderr in the order they were written: the
compiled program runs under `stdbuf -o0`, as clang-repl's stdout is
unbuffered. cpp-repl echoes each input after its prompt, ending the line
before it, so the echoed lines and the newlines at the very end are left
out of the comparison.

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
ECHO = re.compile(r'^(usd|\.\.\.)> ')   # cpp-repl's prompts, before each echoed input line


def lessons(build):
    """[(name, sources, clang_repl, program)] for each repl_* test of the
    build, program being the command of the compiled lesson's test."""
    out = subprocess.run(['ctest', '--test-dir', str(build), '--show-only=json-v1'],
                         capture_output=True, text=True, check=True).stdout
    tests = json.loads(out)['tests']
    programs = {t['name']: t['command'] for t in tests if not t['name'].startswith('repl_')}
    found = []
    for test in tests:
        if not test['name'].startswith('repl_'):
            continue
        cmd, name = test['command'], test['name'].removeprefix('repl_')
        sources = cmd[cmd.index('--batch') + 1:cmd.index('--cwd')]
        clang_repl = cmd[cmd.index('--clang-repl') + 1] if '--clang-repl' in cmd else None
        found.append((name, [Path(s) for s in sources], clang_repl, programs.get(name)))
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


def normalise(text, workdir):
    return text.replace(str(workdir), '<cwd>').rstrip('\n')


def run(argv, workdir):
    """(exit status, stdout and stderr as written)."""
    proc = subprocess.run(argv, cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors='replace', timeout=600,
                          env=dict(os.environ, NO_COLOR='1'))
    return proc.returncode, proc.stdout


def compare(name, sources, clang_repl, program, args, scratch):
    native_dir, repl_dir = scratch / name / 'native', scratch / name / 'cpp-repl'
    if not program:
        return name, None, [f'no compiled lesson: the build has no test named {name}']
    stage(native_dir, sources, args.tutorial)
    stage(repl_dir, sources, args.tutorial)
    backend = ['--clang-repl', args.clang_repl or clang_repl] if (args.clang_repl or clang_repl) else []
    repl_argv = [str(CPP_REPL), *shlex.split(args.cpp_repl_args), '--batch', *map(str, sources),
                 '--cwd', str(repl_dir), '--build-dir', str(args.build), *backend]
    native_rc, native_out = run(['stdbuf', '-o0', *program], native_dir)
    repl_rc, repl_out = run(repl_argv, repl_dir)
    repl_out = ''.join(line for line in repl_out.splitlines(keepends=True) if not ECHO.match(line))

    problems = []
    if native_rc != repl_rc:
        problems.append(f'exit status: compiled {native_rc}, cpp-repl {repl_rc}')
    a, b = normalise(native_out, native_dir), normalise(repl_out, repl_dir)
    if a != b:
        diff = difflib.unified_diff(a.splitlines(), b.splitlines(), 'compiled', 'cpp-repl', lineterm='')
        problems.append('output differs:\n' + '\n'.join(list(diff)[:60]))
    files_a, files_b = snapshot(native_dir), snapshot(repl_dir)
    if files_a.keys() != files_b.keys():
        problems.append(f'files differ: only compiled {sorted(files_a.keys() - files_b.keys())}, '
                        f'only cpp-repl {sorted(files_b.keys() - files_a.keys())}')
    for rel in sorted(files_a.keys() & files_b.keys()):
        fa = normalise(files_a[rel].decode('utf-8', 'replace'), native_dir)
        fb = normalise(files_b[rel].decode('utf-8', 'replace'), repl_dir)
        if fa != fb:
            diff = difflib.unified_diff(fa.splitlines(), fb.splitlines(), f'compiled/{rel}',
                                        f'cpp-repl/{rel}', lineterm='')
            problems.append(f'{rel} differs:\n' + '\n'.join(list(diff)[:40]))
    return name, repl_rc, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('only', nargs='*', help='lesson names (default: all)')
    parser.add_argument('--tutorial', type=Path, default=ROOT.parent / 'openusd-tutorial')
    parser.add_argument('--build', type=Path, help='the tutorial build (default: TUTORIAL/build)')
    parser.add_argument('--clang-repl', help='default: the one the ctest commands name')
    parser.add_argument('--cpp-repl-args', default='-p usd',
                        help='how cpp-repl is asked to run a lesson (default: %(default)s)')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--keep', action='store_true', help='keep the working directories')
    args = parser.parse_args()
    args.tutorial = args.tutorial.resolve()
    args.build = (args.build or args.tutorial / 'build').resolve()

    found = lessons(args.build)
    if args.only:
        found = [entry for entry in found if entry[0] in args.only]
    scratch = Path(tempfile.mkdtemp(prefix='cpp-repl-parity-'))
    failed = 0
    try:
        with ThreadPoolExecutor(args.jobs) as pool:
            jobs = [pool.submit(compare, *entry, args, scratch) for entry in found]
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
    print(f'{len(found) - failed} of {len(found)} lessons alike')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
