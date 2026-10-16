#!/usr/bin/env python3
"""Check a cpp-repl profile against an example session.

  check_profile.py --config .cpp-repl/opencv.toml --example .cpp-repl/opencv.repl \\
                   --report .cpp-repl/opencv.check.json

The steps, each in the report:
  config      cpp-repl reads the profile; every library is a shared object;
              no implicit include directory; prelude names are unique
  prelude     clang++ -fsyntax-only over the preludes: a prelude that fails
              stops cpp-repl's start, and this step shows why in full
  startup     seconds to a ready session, against the same without the profile
  example     the example under cpp-repl --batch, input by input
  prove_libs  the example without the profile's libraries: some input must
              then fail to link, or the example never calls into them
  roundtrip   the example saved with %save-cpp, built with clang++ and the
              profile's --cflags and --libs, prints what the REPL printed
  golden      the transcript against a saved one (--golden FILE)

Prints one JSON object, and writes it to --report. Exit status: 0 every
check passed, 1 a check failed, 2 bad usage or configuration, 124 a run
passed its deadline.
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
import time
from pathlib import Path

from _common import (CPP_REPL_BIN, FAILED, OK, TIMEOUT, USAGE, elf_kind, ignore_in_dot_dir, make_temp,
                     project_root, state_dir_status, toml_text, toolchain)

CRASH = re.compile(r'^\[(clang-repl (?:crashed \([^)]*\)|exited \(code -?\d+\)|got stuck on that input))\]$', re.M)
UNPRINTABLE = re.compile(r'^<[^\n]*>$')
# cpp-repl's note on a type that printed as <type>, after the input's output.
PRINTER_HINT = re.compile(r'^\[[^\n]* prints as <[^\n]*>: [^\n]*\]\n', re.M)
# The generic printer's <type name> as an item of a printed container.
UNPRINTABLE_ITEM = re.compile(r'(?:^|[\[{(]|, )(<[A-Za-z_:][^\n]*?>)(?=$|[\]})]|, )', re.M)
CONTAINER = re.compile(r'^\s*std::(vector|array|map|unordered_map|set|list|deque|pair|tuple|optional)\b')
IMPLICIT = ('/usr/include', '/usr/local/include')

# A failing output's text, the troubleshooting.md row that explains it, and
# what to do first. {T} is the profile, {0} the first group of the match.
HINTS = [
    (r'Symbols not found: \[[^\]]*?(_ZN4repl\w*)', 'stale-template',
     'no library is missing: an earlier input that failed to compile instantiated a printer, and clang-repl '
     'kept it without its code. Fix that input, then run again'),
    (r'Symbols not found: \[(?![^\]]*_ZN4repl)\s*([^\s,\]]+)', 'symbols-not-found',
     "a library is missing from libs: probe_library.py symbol '{0}' --config {T} names the one that defines it"),
    (r'Failed to materialize symbols', 'materialize',
     'an earlier input failed to link and left the backend unreliable: fix the first failure only'),
    (r"fatal error: '([^']+)' file not found", 'file-not-found',
     'no include directory holds {0}: add it to flags as -isystem DIR, or fix the #include'),
    (r'[Ii]nline asm', 'inline-asm',
     "clang-repl 18's JIT cannot assemble inline asm: run the check with --docker (clang-repl 23)"),
    (r'invalid ELF header|file too short', 'static-or-script',
     'a static archive or a linker script is listed in libs: list the shared library it stands for'),
    (r'cannot open shared object file', 'dependency-not-found',
     'a dependency of a listed library is not on the loader path: list its absolute path in libs first'),
    (r'St3__1', 'libcxx', 'the library was built with libc++; cpp-repl uses libstdc++'),
    (r'__kmpc_|omp_get_', 'openmp', 'the code uses OpenMP: add -fopenmp to flags and the matching libomp.so to libs'),
    (r'use of undeclared identifier|no member named .* in namespace', 'undeclared',
     'a name the example uses is not declared: check the prelude step, then the prelude includes'),
    (r'explicit specialization of .* after instantiation', 'printer-after-instantiation',
     'a printer was declared after its type had printed: printers go in the prelude header'),
    (r'ambiguous partial specializations', 'ambiguous-printers',
     'two printers match one type: constrain each on the library\'s own types'),
    (r'crashed \(SIG', 'crash', 'a crash: compare the profile\'s defines with the package\'s, and check the libraries\' load order'),
    (r'(\S+\.so[\w.]*): error adding symbols: DSO missing from command line', 'dso-missing',
     'the code calls {0} directly, and the REPL found it only as a dependency: list it (its lib*.so link) in libs'),
    (r"undefined reference to `([^'(]+)", 'dso-missing',
     'the native build lacks the library that defines {0}: probe_library.py symbol "{0}" --config {T}'),
    (r'there is no image', 'no-image', 'build the derived image first: docker build -t TAG .cpp-repl'),
    (r'clang-repl failed to start', 'failed-to-start',
     'the session did not start: see startup.diagnosis, then CPP_REPL_TRACES=1 for a crash trace'),
]


def hints_for(text, toml):
    out = []
    for pattern, anchor, advice in HINTS:
        m = re.search(pattern, text, re.M)
        if m:
            first = m.group(1) if m.groups() else ''
            out.append({'match': m.group(0)[:120], 'anchor': anchor, 'advice': advice.format(first, T=toml)})
    return out


# -- golden transcripts, as tests/test_integration.py compares them ----------

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


# ---------------------------------------------------------------------------

class Checker:
    def __init__(self, args):
        from cpp_repl.config import Config, ConfigError, Options
        self.args = args
        self.cwd = Path.cwd()
        self.root = project_root(self.cwd)
        self.toml = Path(args.config).resolve()
        self.example = Path(args.example).resolve()
        self.tmp = make_temp('cpp-repl-check-')
        self.report = {'ok': False, 'config': str(self.toml), 'example': str(self.example), 'steps': {},
                       'warnings': [], 'hints': []}
        self.env = dict(os.environ, NO_COLOR='1')
        state, writable = state_dir_status()
        if not writable:   # sandboxes: keep cpp-repl's state inside the project
            self.env['XDG_STATE_HOME'] = str(self.root / '.cpp-repl' / '.state')
            self.report['state_dir'] = self.env['XDG_STATE_HOME']
            ignore_in_dot_dir(self.root, '.state/')
        if args.cpp_repl:
            self.cpp_repl = shlex.split(args.cpp_repl)
        else:
            self.cpp_repl = [sys.executable, str(CPP_REPL_BIN)]
        # Full diagnostics: the hints read clang's own text, mangled symbols included.
        self.common = ['--no-color', '--diagnostics', 'full']
        self.common += [] if args.with_project_config else ['--no-config']
        if args.clang_repl:
            self.common += ['--clang-repl', args.clang_repl]
        if args.docker:
            self.common += ['--docker'] + (['--image', args.image] if args.image else [])
        self.report['cpp_repl'] = shlex.join(self.cpp_repl)
        try:
            self.cfg = Config(Options(cwd=self.cwd, configs=[str(self.toml)], no_config=not args.with_project_config,
                                      clang_repl=args.clang_repl), backend=False)
        except ConfigError as e:
            self.cfg = None
            self.config_error = str(e)
        self.clangxx = None
        if not args.docker:
            try:
                binary, text, major, self.clangxx = toolchain(args.clang_repl)
                self.report['clang_repl'] = {'path': binary, 'version': text, 'major': major}
            except ConfigError as e:
                self.report['clang_repl'] = {'error': str(e)}
        self.transcript = None
        self.inputs = None

    # -- running cpp-repl ------------------------------------------------------

    def cpp_repl_run(self, extra, timeout=None):
        """(exit status or None on timeout, stdout, stderr, seconds)."""
        cmd = self.cpp_repl + self.common + extra
        start = time.monotonic()
        try:
            p = subprocess.run(cmd, cwd=self.cwd, env=self.env, capture_output=True, text=True, errors='replace',
                               timeout=timeout or self.args.timeout, stdin=subprocess.DEVNULL)
            return p.returncode, p.stdout, p.stderr, time.monotonic() - start
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
            err = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
            return None, out, err, time.monotonic() - start

    def variant(self, libs):
        """A copy of the profile with other libs, written in the temporary
        directory with every path made absolute."""
        from cpp_repl.config import read_toml
        profile = read_toml(self.toml)
        data = {'flags': profile.flags, 'libs': [str(p) for p in libs], 'prelude': [str(p) for p in profile.prelude],
                'profiles': profile.profiles,
                'compile_commands': str(profile.compile_commands) if profile.compile_commands else None}
        path = self.tmp / f'variant-{len(list(self.tmp.glob("variant-*")))}.toml'
        path.write_text(toml_text(data))
        return path

    # -- steps -----------------------------------------------------------------

    def step_config(self):
        step = {'ok': False}
        if self.cfg is None:
            step['error'] = self.config_error
            self.report['hints'] += hints_for(self.config_error, self.args.config)
            return step
        from cpp_repl.config import read_toml
        own = read_toml(self.toml)
        step['preludes'] = [str(h) for h in self.cfg.preludes]
        step['libs'] = [str(lib) for lib in self.cfg.libs]
        step['flags'] = self.cfg.flags
        problems = []
        for lib in own.libs:
            kind = elf_kind(lib)
            if kind != 'shared':
                problems.append(f'{lib} is {kind or "unreadable"}, not a shared library')
        flags = own.flags
        for i, a in enumerate(flags):
            value = flags[i + 1] if a in ('-I', '-isystem') and i + 1 < len(flags) else (
                a[2:] if a.startswith('-I') else a[len('-isystem'):] if a.startswith('-isystem') else None)
            if value and os.path.normpath(value) in IMPLICIT:
                problems.append(f'{value} is searched by the compiler already; listing it breaks #include_next')
        names = [h.name for h in self.cfg.preludes]
        for name in {n for n in names if names.count(n) > 1}:
            problems.append(f'two preludes are named {name}; %save-cpp would include only one')
        for which in ('--cflags', '--libs'):
            code, out, err, _ = self.cpp_repl_run(['-c', str(self.toml), which], timeout=120)
            step[which.strip('-')] = out.strip() if code == 0 else None
            if code != 0:
                problems.append(f'cpp-repl {which} failed: {err.strip()}')
        step['problems'] = problems
        step['ok'] = not problems
        return step

    def step_prelude(self):
        """Every prelude compiled natively, as clang-repl parses it at startup."""
        source = self.tmp / 'preludes.cpp'
        source.write_text(''.join(f'#include "{h}"\n' for h in self.cfg.preludes) + 'int main() {}\n')
        flags = list(self.cfg.compiler_flags)
        if self.args.docker:
            from cpp_repl.docker import DEFAULT_IMAGE, mounts
            image = self.args.image or os.environ.get('CPP_REPL_IMAGE') or DEFAULT_IMAGE
            volumes = [str(p) for p in mounts(self.cfg, [], [])] + [str(self.tmp)]
            cmd = ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}']
            for v in dict.fromkeys(volumes):
                cmd += ['-v', f'{v}:{v}:ro']
            cmd += [image, 'clang++', '-fsyntax-only', *flags, str(source)]
        else:
            if not self.clangxx:
                return {'ok': False, 'error': 'no clang++ next to clang-repl to compile the preludes with'}
            cmd = [self.clangxx, '-fsyntax-only', *flags, str(source)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, errors='replace', timeout=600,
                               cwd=self.cwd, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {'ok': False, 'error': str(e)}
        text = p.stdout + p.stderr
        errors = [line for line in text.splitlines() if re.search(r'\b(fatal )?error:', line)]
        step = {'ok': p.returncode == 0, 'compiler': cmd[0] if not self.args.docker else 'clang++ in the image',
                'errors': errors[:15]}
        warnings = [line for line in text.splitlines() if 'warning:' in line]
        if warnings:
            step['warnings'] = warnings[:10]
        if p.returncode != 0:
            step['output'] = text.strip().splitlines()[:40]
            self.report['hints'] += hints_for(text, self.args.config)
        return step

    def step_startup(self):
        empty = self.tmp / 'empty.repl'
        empty.write_text('')
        with_profile, without = [], []
        for _ in range(self.args.runs):
            code, out, err, seconds = self.cpp_repl_run(['--batch', '-c', str(self.toml), str(empty)])
            if code != 0:
                step = {'ok': False, 'exit': code, 'output': (out + err).strip().splitlines()[:40]}
                if code is None:
                    step['error'] = f'no ready session within {self.args.timeout} s'
                step['diagnosis'] = self.diagnose_startup(empty)
                self.report['hints'] += hints_for(out + err, self.args.config)
                return step
            with_profile.append(seconds)
            code, _, _, seconds = self.cpp_repl_run(['--batch', str(empty)])
            if code == 0:
                without.append(seconds)
        best, base = min(with_profile), min(without) if without else None
        step = {'ok': True, 'seconds': round(best, 2), 'baseline': round(base, 2) if base else None,
                'delta': round(best - base, 2) if base else None, 'runs': len(with_profile)}
        if step['delta'] is not None and step['delta'] > self.args.budget:
            self.report['warnings'].append({'kind': 'slow-start', 'input': None,
                                            'detail': f'the profile adds {step["delta"]} s to startup '
                                                      f'(budget {self.args.budget} s): trim the prelude includes'})
        return step

    def diagnose_startup(self, empty):
        """Which part of the profile stops the session from starting."""
        from cpp_repl.config import read_toml
        libs = read_toml(self.toml).libs
        if not libs:
            return 'the profile lists no libraries: the prelude headers or the flags stop clang-repl'
        code, out, err, _ = self.cpp_repl_run(['--batch', '-c', str(self.variant([])), str(empty)])
        if code != 0:
            return 'it fails without its libraries too: the prelude headers or the flags stop clang-repl'
        culprits = []
        for lib in libs:
            code, out, err, _ = self.cpp_repl_run(['--batch', '-c', str(self.variant([lib])), str(empty)])
            if code != 0:
                culprits.append({'lib': str(lib), 'output': (out + err).strip().splitlines()[-6:]})
        if culprits:
            return {'fails_alone': culprits}
        return 'every library loads alone: the order of libs matters (dependencies first), or two conflict'

    def split(self, stdout, inputs):
        """[(input, output or None when it never ran)] from a --batch transcript."""
        prompt, more = self.cfg.prompt, self.cfg.more
        pieces, pos, starts = [], 0, []
        for text in inputs:
            first, *rest = text.split('\n')
            echo = prompt + first + '\n' + ''.join(more + r + '\n' for r in rest)
            at = stdout.find(echo, pos)
            while at > 0 and stdout[at - 1] != '\n':
                at = stdout.find(echo, at + 1)
            if at < 0:
                starts.append(None)
                continue
            starts.append((at, at + len(echo)))
            pos = at + len(echo)
        for i, text in enumerate(inputs):
            if starts[i] is None:
                pieces.append((text, None))
                continue
            end = next((s[0] for s in starts[i + 1:] if s), len(stdout))
            pieces.append((text, PRINTER_HINT.sub('', stdout[starts[i][1]:end])))
        return pieces

    def classify(self, pieces, timed_out=False):
        from cpp_repl.backend import outcome
        from cpp_repl.lexer import transform
        results, poisoned = [], False
        last_run = max((i for i, (_, out) in enumerate(pieces) if out is not None), default=-1)
        for i, (text, output) in enumerate(pieces):
            entry = {'input': text}
            if output is None:
                entry['status'] = 'not-run'
            else:
                crash = CRASH.search(output)
                if timed_out and i == last_run:
                    entry['status'] = 'timeout'
                elif crash:
                    entry['status'] = 'stuck' if 'stuck' in crash.group(1) else 'crash'
                else:
                    entry['status'] = outcome(output)
                lines = output.rstrip('\n').split('\n') if output.strip() else []
                entry['lines'] = len(lines)
                entry['output'] = '\n'.join(lines[:40]) + ('\n...' if len(lines) > 40 else '')
                if poisoned:
                    entry['unreliable'] = True
                if entry['status'] == 'link':
                    poisoned = True
                stripped = text.strip()
                is_expr = not stripped.startswith(('%', '#')) and transform(text, wrap=False).kind == 'expr'
                entry['bare_expression'] = is_expr
                if entry['status'] == 'ok' and is_expr:
                    shown = output.strip()
                    item = UNPRINTABLE_ITEM.search(shown)
                    if UNPRINTABLE.fullmatch(shown):
                        self.warn('unprintable', text, f'prints as {shown}: the type has no printer')
                    elif item:
                        self.warn('unprintable', text, f'an item prints as {item.group(1)}: its type has no '
                                                       'printer')
                    elif len(lines) > self.args.max_lines:
                        self.warn('verbose', text, f'prints {len(lines)} lines (budget {self.args.max_lines})')
                    if CONTAINER.match(stripped) and len(lines) > 1:
                        self.warn('vector', text, 'a container of values prints across lines: its element type '
                                                  'needs a one-line Put<T>')
            results.append(entry)
        return results

    def warn(self, kind, text, detail):
        self.report['warnings'].append({'kind': kind, 'input': text, 'detail': detail})

    def run_example(self, toml, example):
        from cpp_repl.sources import PRELUDE_NAMES, inputs_from_file
        inputs = inputs_from_file(example, skip=PRELUDE_NAMES | {h.name for h in self.cfg.preludes})
        code, out, err, seconds = self.cpp_repl_run(['--batch', '-c', str(toml), str(example)])
        return inputs, code, out, err, seconds

    def step_example(self):
        inputs, code, out, err, seconds = self.run_example(self.toml, self.example)
        self.inputs, self.transcript = inputs, out
        results = self.classify(self.split(out, inputs), timed_out=code is None)
        failed = [i for i, r in enumerate(results) if r['status'] != 'ok']
        step = {'ok': code == 0 and not failed, 'exit': code, 'seconds': round(seconds, 2), 'inputs': results,
                'first_failure': failed[0] if failed else None}
        if err.strip():
            step['stderr'] = err.strip().splitlines()[:20]
        if failed:
            first = results[failed[0]]
            self.report['hints'] += hints_for((first.get('output') or '') + err, self.args.config)
        elif code not in (0, None):
            self.report['hints'] += hints_for(out + err, self.args.config)
        if code is None:
            step['error'] = f'the example passed its {self.args.timeout} s deadline'
        return step

    def step_prove_libs(self):
        from cpp_repl.config import read_toml
        if not read_toml(self.toml).libs:
            return {'ok': True, 'skipped': 'the profile lists no libraries (header-only)'}
        variant = self.variant([])
        inputs, code, out, err, _ = self.run_example(variant, self.example)
        results = self.classify_quietly(self.split(out, inputs), code is None)
        linked = [r['input'] for r in results if r['status'] in ('link', 'crash')]
        step = {'ok': bool(linked), 'needs_libs': linked[:5]}
        if not linked:
            step['error'] = ('every input runs without the libraries: add an input that calls a function the '
                             'shared libraries define, so that the check proves libs')
        return step

    def classify_quietly(self, pieces, timed_out):
        saved = list(self.report['warnings'])
        try:
            return self.classify(pieces, timed_out)
        finally:
            self.report['warnings'] = saved

    def step_roundtrip(self):
        if not self.clangxx:
            return {'ok': False, 'error': 'no clang++ next to clang-repl to build with'}
        saved = self.tmp / 'saved.cpp'
        script = self.tmp / 'roundtrip.repl'
        text = self.example.read_text()
        script.write_text(text.rstrip('\n') + f'\n%save-cpp {saved}\n')
        inputs, code, out, err, _ = self.run_example(self.toml, script)
        if not saved.is_file():
            return {'ok': False, 'error': '%save-cpp wrote nothing', 'output': (out + err).splitlines()[-10:]}
        # What the REPL printed: every input's output but the % commands'.
        pieces = self.split(out, inputs)
        repl = ''.join(o or '' for t, o in pieces if not t.strip().startswith('%'))
        cflags = shlex.split(self.report['steps']['config'].get('cflags') or '')
        libs = shlex.split(self.report['steps']['config'].get('libs') or '')
        exe = self.tmp / 'saved'
        build = [self.clangxx, *cflags, str(saved), *libs, '-o', str(exe)]
        try:
            p = subprocess.run(build, capture_output=True, text=True, errors='replace', timeout=900,
                               cwd=self.tmp, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {'ok': False, 'built': False, 'error': str(e)}
        step = {'built': p.returncode == 0, 'command': shlex.join(build)}
        if p.returncode != 0:
            step.update(ok=False, output=(p.stdout + p.stderr).strip().splitlines()[:30])
            self.report['hints'] += hints_for(p.stdout + p.stderr, self.args.config)
            return step
        try:
            r = subprocess.run([str(exe)], capture_output=True, text=True, errors='replace',
                               timeout=self.args.timeout, cwd=self.cwd, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            step.update(ok=False, error='the native program passed its deadline')
            return step
        native = r.stdout + r.stderr
        a, b = normalise(repl), normalise(native)
        same = a == b or sorted(a.splitlines()) == sorted(b.splitlines())
        step.update(ok=same and r.returncode == 0, same=same, exit=r.returncode)
        if not same:
            step['diff'] = list(difflib.unified_diff(a.splitlines(), b.splitlines(), 'repl', 'native',
                                                     lineterm='', n=1))[:40]
        return step

    def step_golden(self):
        golden = Path(self.args.golden)
        args = f'-c {os.path.relpath(self.toml, self.cwd)}'
        if self.args.update_golden or not golden.is_file():
            head = ['# A golden session: check_profile.py --golden compares the example\'s transcript with it.',
                    '# <any> matches one line, <any lines> any number; addresses are normalised.',
                    f'# args: {args}']
            golden.write_text('\n'.join(head) + '\n' + self.transcript)
            return {'ok': True, 'written': str(golden)}
        lines = golden.read_text().split('\n')
        while lines and lines[0].startswith('#') and not re.match(r'#(include|define|if|pragma)', lines[0]):
            lines.pop(0)
        expected = '\n'.join(lines)
        ok = matches(expected, self.transcript)
        step = {'ok': ok, 'file': str(golden)}
        if not ok:
            step['diff'] = list(difflib.unified_diff(normalise(expected).splitlines(),
                                                     normalise(self.transcript).splitlines(),
                                                     'golden', 'now', lineterm='', n=1))[:40]
        return step

    # -- all of it -------------------------------------------------------------

    def run(self):
        steps = self.report['steps']
        try:
            steps['config'] = self.step_config()
            if not steps['config']['ok']:
                return self.finish(USAGE if self.cfg is None else FAILED)
            steps['prelude'] = self.step_prelude()
            steps['startup'] = self.step_startup()
            if not steps['startup']['ok']:
                return self.finish(TIMEOUT if steps['startup'].get('exit', 0) is None else FAILED)
            steps['example'] = self.step_example()
            if steps['example']['exit'] is None:
                return self.finish(TIMEOUT)
            if self.args.docker:
                steps['prove_libs'] = {'ok': True, 'skipped': 'not run with --docker'}
            elif not self.args.no_prove_libs:
                steps['prove_libs'] = self.step_prove_libs()
            if self.args.docker:
                steps['roundtrip'] = {'ok': True, 'skipped': 'not run with --docker'}
            elif self.args.no_roundtrip:
                steps['roundtrip'] = {'ok': True, 'skipped': '--no-roundtrip'}
            elif steps['example']['ok']:
                steps['roundtrip'] = self.step_roundtrip()
            else:
                steps['roundtrip'] = {'ok': True, 'skipped': 'the example failed'}
            if self.args.golden:
                steps['golden'] = self.step_golden()
            ok = all(s.get('ok') for s in steps.values())
            return self.finish(OK if ok else FAILED)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def finish(self, code):
        steps = self.report['steps']
        self.report['ok'] = code == OK
        self.report['failed_steps'] = [name for name, s in steps.items() if not s.get('ok')]
        seen, hints = set(), []
        for h in self.report['hints']:
            if h['anchor'] not in seen:
                seen.add(h['anchor'])
                hints.append(h)
        self.report['hints'] = hints
        text = json.dumps(self.report, indent=2, default=str)
        if self.args.report:
            Path(self.args.report).write_text(text + '\n')
        sys.stdout.write(text + '\n')
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', required=True, help='the profile, a cpp-repl.toml-style file')
    parser.add_argument('--example', required=True, help='a .repl (or .cpp) the profile must run')
    parser.add_argument('--report', help='also write the JSON report to this file')
    parser.add_argument('--golden', help='compare the transcript with this file (written when missing)')
    parser.add_argument('--update-golden', action='store_true', help='rewrite the --golden file')
    parser.add_argument('--docker', action='store_true', help='run cpp-repl --docker (clang-repl 23)')
    parser.add_argument('--image', help='the image for --docker')
    parser.add_argument('--clang-repl', help='the clang-repl to run')
    parser.add_argument('--cpp-repl', help="the cpp-repl command (default: this checkout's bin/cpp-repl)")
    parser.add_argument('--with-project-config', action='store_true',
                        help="also read the project's cpp-repl.toml, as a plain cpp-repl does")
    parser.add_argument('--max-lines', type=int, default=12, help='lines a printed value may take (default 12)')
    parser.add_argument('--budget', type=float, default=1.0, help='seconds the profile may add to startup')
    parser.add_argument('--runs', type=int, default=2, help='startup runs, the best one counts (default 2)')
    parser.add_argument('--timeout', type=int, default=300, help='seconds per cpp-repl run (default 300)')
    parser.add_argument('--no-roundtrip', action='store_true', help='skip the %%save-cpp build')
    parser.add_argument('--no-prove-libs', action='store_true', help='skip the run without libraries')
    args = parser.parse_args()
    for path in (args.config, args.example):
        if not Path(path).is_file():
            sys.stdout.write(json.dumps({'ok': False, 'error': f'no such file: {path}'}) + '\n')
            return USAGE
    return Checker(args).run()


if __name__ == '__main__':
    sys.exit(main())
