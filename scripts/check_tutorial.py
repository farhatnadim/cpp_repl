#!/usr/bin/env python3
"""Check a tutorial's transcripts against real runs of cpp-repl.

    check_tutorial.py docs/tutorials/*.md
    check_tutorial.py --update docs/tutorials/02-programs.md
    check_tutorial.py --root ../openusd-tutorial --cpp-repl "../openusd-tutorial/repl/usd-repl" TUTORIAL.md

A tutorial is Markdown. Its transcripts are fenced blocks, and HTML comments,
which GitHub does not show, say how each is checked:

  <!-- check: session ARGS -->  a session: every fenced block after it whose
        first line is a prompt (c++> , usd> ) holds its inputs, each with the
        output it printed. A block may start with one `$ ` line, the command
        that starts the session, shown to the reader and not run. ARGS are
        cpp-repl's own; a file among them runs first, and what it prints is
        not compared. The inputs run under --batch, and the output is compared
        as the prompt shows it: a diagnostic in an input names <input> and the
        input's own lines.
  <!-- check: sh -->           shell transcripts: in each fenced block, a `$ `
        line is a command, run with bash (with the lines of a heredoc it
        opens), and the lines up to the next `$ ` are its output, stdout and
        stderr together.
  <!-- check: off -->          the blocks after it are shown and not checked:
        what only a terminal does (Tab, Ctrl-C, a crash's replay), timings.
  <!-- file: NAME -->          the fenced block after it is written to NAME.
  <!-- clang-repl: 23 -->      the clang-repl major the transcripts come from;
        run with another, the tutorial is skipped.
  <!-- requires: profile=glm, command=cmake, header=zlib.h -->  what the
        tutorial needs besides cpp-repl: a profile that configures, a command
        on PATH, a header in /usr/include or /usr/local/include. Without it,
        the tutorial is skipped.

Everything runs in one temporary directory per tutorial, which links to the
top-level entries of --root (the repository the tutorial is in). That
directory and the repository show as `…` in the output. A transcript block outside a session, sh or off is
an error, so that every transcript is checked, or visibly not. The comparison
follows the golden sessions' (tests/sessions): addresses and input_line_N are
normalised, <any> matches a line and <any lines> any number of them; and
timings of %time, %timeit and --json are left out.

Exit status: 0 every transcript matched, 1 one did not, 2 a tutorial does not
parse or a command is wrong, 77 every tutorial was skipped.
"""

import argparse
import difflib
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CHECKOUT / 'src'))

SKIPPED = 77
MARKER = re.compile(r'^<!--\s*(check|file|clang-repl|requires):\s*(.*?)\s*-->\s*$')
FENCE = re.compile(r'^(`{3,}|~{3,})')
PROMPT = re.compile(r'^([\w+]+)> ')
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
# cpp-repl's options and compiler flags that take the next argument as their
# value, so that the value is not taken for a file to run.
WITH_VALUE = {'-p', '--profile', '-c', '--config', '--compile-commands', '--build-dir', '--lib', '-l', '-L',
              '--clang-repl', '--timeout', '--cwd', '--diagnostics', '--serve', '--connect', '--image',
              '--mount', '-I', '-isystem', '-iquote', '-idirafter', '-D', '-U', '-include', '-isysroot',
              '--sysroot'}


# -- comparing transcripts, as tests/test_integration.py compares golden ones ---

def inputs_of(transcript, prompt, more):
    """The inputs of a transcript: each prompt line, with the continuation
    lines after it."""
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
    """Whether actual is the expected transcript, <any> and <any lines> as
    wildcards."""
    pattern = ''
    for line in normalise(expected).split('\n')[:-1]:
        if line == '<any lines>':
            pattern += r'(?:[^\n]*\n)*?'
        elif line == '<any>':
            pattern += r'[^\n]*\n'
        else:
            pattern += re.escape(line) + r'\n'
    return re.fullmatch(pattern, normalise(actual)) is not None


_DURATION = r'\d+(?:\.\d+)?(?:e-?\d+)? ?(?:ns|µs|us|ms|s)\b'


def untimed(text):
    """text without the timings that change from run to run."""
    text = re.sub(rf'^{_DURATION} ± {_DURATION} per loop \(mean ± std\. dev\. of \d+ runs?, \d+ loops? each',
                  'T ± T per loop (mean ± std. dev. of N runs, N loops each', text, flags=re.M)
    text = re.sub(rf'(wall time: |of which the code ran ){_DURATION}', r'\1T', text)
    return re.sub(r'"(run_)?seconds": [0-9.e-]+', r'"\1seconds": T', text)


# -- reading a tutorial ----------------------------------------------------------

@dataclass
class Block:
    first: int         # the index of its first line in the tutorial
    lines: list        # its lines, between the fences
    lead: int = 0      # lines shown and not run: a session's `$ cpp-repl ...`

    @property
    def text(self):
        return '\n'.join(self.lines[self.lead:]) + '\n'


@dataclass
class Session:
    args: list
    line: int
    blocks: list = field(default_factory=list)


@dataclass
class Tutorial:
    path: Path
    lines: list
    major: int = None
    requires: list = field(default_factory=list)
    steps: list = field(default_factory=list)   # ('file', name, text), ('session', Session), ('sh', Block)
    errors: list = field(default_factory=list)


def heredoc_word(command):
    """The word that ends the heredoc a shell line opens, or None: an
    unquoted << (not <<<), and the word after it, quoted or not."""
    quote, i = None, 0
    while i < len(command):
        c = command[i]
        if quote:
            if c == quote:
                quote = None
            elif c == '\\' and quote == '"':
                i += 1
        elif c in '\'"':
            quote = c
        elif c == '\\':
            i += 1
        elif command.startswith('<<<', i):
            i += 2
        elif command.startswith('<<', i):
            m = HEREDOC.match(command, i)
            if m:
                return m.group(2)
            i += 1
        i += 1
    return None


def is_transcript(lines):
    return bool(lines) and (bool(PROMPT.match(lines[0])) or lines[0].startswith('$ '))


def read_tutorial(path):
    """The tutorial's markers and blocks, in order, with what does not parse
    in errors."""
    path = Path(path)
    t = Tutorial(path, path.read_text().split('\n'))
    mode, session, pending_file, i = None, None, None, 0
    lines = t.lines
    while i < len(lines):
        line = lines[i]
        m = MARKER.match(line.strip())
        if m:
            kind, value = m.groups()
            if kind == 'clang-repl':
                if not value.isdigit():
                    t.errors.append(f'{path}:{i + 1}: clang-repl takes a major version, such as 23')
                else:
                    t.major = int(value)
            elif kind == 'file':
                pending_file = value
            elif kind == 'requires':
                t.requires += [r.strip() for r in value.split(',') if r.strip()]
            else:
                words = shlex.split(value)
                if words[:1] == ['session']:
                    mode, session = 'session', Session(words[1:], i + 1)
                    t.steps.append(('session', session))
                elif words in (['sh'], ['off']):
                    mode, session = words[0], None
                else:
                    t.errors.append(f'{path}:{i + 1}: a check is "session ARGS", "sh" or "off", not {value!r}')
            i += 1
            continue
        fence = FENCE.match(line)
        if not fence:
            if line.strip() and pending_file:
                t.errors.append(f'{path}:{i + 1}: file: {pending_file} is not followed by a fenced block')
                pending_file = None
            i += 1
            continue
        close = next((j for j in range(i + 1, len(lines)) if lines[j].startswith(fence.group(1))), None)
        if close is None:
            t.errors.append(f'{path}:{i + 1}: a fenced block that does not close')
            break
        block = Block(i + 1, lines[i + 1:close])
        if pending_file:
            t.steps.append(('file', pending_file, block.text))
            pending_file = None
        elif is_transcript(block.lines):
            if mode == 'session':
                block.lead = 1 if block.lines[0].startswith('$ ') else 0
                if block.lead and (len(block.lines) < 2 or not PROMPT.match(block.lines[1])):
                    t.errors.append(f'{path}:{i + 1}: a shell transcript in a session: mark it <!-- check: sh -->')
                else:
                    session.blocks.append(block)
            elif mode == 'sh':
                if block.lines[0].startswith('$ '):
                    t.steps.append(('sh', block))
                else:
                    t.errors.append(f'{path}:{i + 1}: a session transcript under <!-- check: sh -->')
            elif mode != 'off':
                t.errors.append(f'{path}:{i + 1}: a transcript outside <!-- check: session/sh/off -->')
        i = close + 1
    for kind, *rest in t.steps:
        if kind == 'session' and not rest[0].blocks:
            t.errors.append(f'{path}:{rest[0].line}: a session with no transcript after it')
    return t


# -- running it ------------------------------------------------------------------

def prompt_of(block):
    m = PROMPT.match(block.lines[block.lead])
    prompt = m.group(0)
    return prompt, '.' * (len(prompt) - 2) + '> '


def as_typed(output, typed, starts):
    """The output of a --batch run of the file typed as the prompt shows it:
    a diagnostic in one of its inputs names <input> and that input's own
    lines, and so does the source line under it."""
    name = re.escape(typed.name)
    header = re.compile(rf'^(?:\S*/)?{name}:(\d+):(\d+): ')
    other = re.compile(r'^(?:<[^>\n]*>|\S+):\d+:\d+: ')
    gutter = re.compile(r'^( *)(\d+)( \| .*)$')
    out, offset = [], None
    for line in output.split('\n'):
        m = header.match(line)
        if m:
            n = int(m.group(1))
            start = max(s for s in starts if s <= n)
            offset = start - 1
            line = f'<input>:{n - offset}:{m.group(2)}: ' + line[m.end():]
        elif other.match(line):
            offset = None
        elif offset is not None:
            g = gutter.match(line)
            if g:
                width = len(g.group(1)) + len(g.group(2))
                line = str(int(g.group(2)) - offset).rjust(width) + g.group(3)
        out.append(line)
    return '\n'.join(out)


class Runner:
    def __init__(self, tutorial, root, cpp_repl, clang_repl):
        self.t = tutorial
        self.root = root
        self.cpp_repl = cpp_repl
        self.work = Path(tempfile.mkdtemp(prefix='tutorial-'))
        self.private = Path(tempfile.mkdtemp(prefix='tutorial-inputs-'))
        for entry in sorted(root.iterdir()):
            if not entry.name.startswith('.'):
                (self.work / entry.name).symlink_to(entry)
        self.env = dict(os.environ, NO_COLOR='1')
        self.env.pop('CLICOLOR_FORCE', None)
        if clang_repl:
            self.env['CPP_REPL_CLANG_REPL'] = clang_repl
        # A command that is a path (the checkout's bin/cpp-repl, a launcher)
        # puts its directory first on PATH, for the shell transcripts too.
        first = Path(cpp_repl[1] if cpp_repl[0] == sys.executable else cpp_repl[0])
        if first.parent != Path('.'):
            self.env['PATH'] = f'{first.resolve().parent}{os.pathsep}{self.env.get("PATH", "")}'
        self.groups = []
        self.failures = []   # (block, expected, actual)

    def clean(self, text):
        """text with the run's directory and the repository's as `…`."""
        for d in sorted({str(self.work), str(self.work.resolve()), str(self.root)}, key=len, reverse=True):
            text = text.replace(d, '…')
        return text

    def close(self):
        for group in self.groups:
            try:
                os.killpg(group, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        shutil.rmtree(self.work, ignore_errors=True)
        shutil.rmtree(self.private, ignore_errors=True)

    def run(self):
        for kind, *rest in self.t.steps:
            if kind == 'file':
                (self.work / rest[0]).parent.mkdir(parents=True, exist_ok=True)
                (self.work / rest[0]).write_text(rest[1])
            elif kind == 'session':
                self.session(rest[0])
            else:
                self.shell(rest[0])

    def session(self, session):
        from cpp_repl.sources import PRELUDE_NAMES, inputs_from_file
        prompt, more = prompt_of(session.blocks[0])
        per_block = [inputs_of(b.text.rstrip('\n'), prompt, more) for b in session.blocks]
        typed = self.private / f'typed-{session.line}.repl'
        text, starts = '', []
        for inputs in per_block:
            for item in inputs:
                starts.append(text.count('\n') + 1)
                text += item + '\n'
        typed.write_text(text)
        args, files, value = [], [], False
        for a in session.args:
            here = self.work / a
            if not value and not a.startswith('-') and here.is_file():
                files.append(here)
            args.append(str(here) if not a.startswith('-') and here.exists() else a)
            value = a in WITH_VALUE
        before = sum(len(inputs_from_file(f, skip=PRELUDE_NAMES)) for f in files)
        cmd = [*self.cpp_repl, '--batch', '--no-color', '--diagnostics', 'full', *args, str(typed)]
        try:
            p = subprocess.run(cmd, cwd=self.work, env=self.env, capture_output=True, text=True, errors='replace',
                               stdin=subprocess.DEVNULL, timeout=900)
        except subprocess.TimeoutExpired:
            self.failures.append((session.blocks[0], session.blocks[0].text, '[no answer within 900 s]\n'))
            return
        echoes = [i for i, line in enumerate(p.stdout.split('\n')) if line.startswith(prompt)]
        if len(echoes) <= before:
            self.failures.append((session.blocks[0], session.blocks[0].text,
                                  f'[the typed inputs never ran: exit {p.returncode}]\n{p.stdout}{p.stderr}'))
            return
        out_lines = p.stdout.split('\n')[echoes[before]:]
        actual = self.clean(as_typed('\n'.join(out_lines), typed, starts))
        # The output from each block's first input on is that block's.
        lines, cuts, k = actual.split('\n'), [], 0
        positions = [i for i, line in enumerate(lines) if line.startswith(prompt)]
        for inputs in per_block:
            cuts.append(positions[k] if k < len(positions) else len(lines))
            k += len(inputs)
        cuts.append(len(lines))
        for block, a, b in zip(session.blocks, cuts, cuts[1:]):
            piece = '\n'.join(lines[a:b]).rstrip('\n') + '\n'
            if not matches(untimed(block.text), untimed(piece)):
                self.failures.append((block, block.text, piece))

    def shell(self, block):
        commands, i, lines = [], 0, block.lines
        while i < len(lines):
            command = [lines[i][2:]]
            i += 1
            word = heredoc_word(command[0])
            if word:
                while i < len(lines):
                    command.append(lines[i])
                    i += 1
                    if lines[i - 1] == word:
                        break
            while command[-1].endswith('\\') and i < len(lines):
                command.append(lines[i])
                i += 1
            while i < len(lines) and not lines[i].startswith('$ '):
                i += 1   # what the command printed: compared as a whole, below
            commands.append('\n'.join(command))
        actual = []
        for command in commands:
            p = subprocess.Popen(['bash', '-c', command], cwd=self.work, env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, errors='replace',
                                 start_new_session=True)
            self.groups.append(p.pid)
            try:
                out, _ = p.communicate(timeout=900)
            except subprocess.TimeoutExpired:
                p.kill()
                out = '[no answer within 900 s]\n'
            actual.append(f'$ {command}\n{self.clean(out)}')
        piece = ''.join(a if a.endswith('\n') else a + '\n' for a in actual)
        expected = block.text
        if not matches(untimed(expected), untimed(piece)):
            self.failures.append((block, expected, piece))


@dataclass
class Result:
    path: Path
    status: str        # ok, differs, error or skipped
    report: str = ''


def unmet(requires, clang_repl):
    """Why a tutorial's requirements are not met here, or None."""
    from cpp_repl.config import Config, ConfigError, Options
    for item in requires:
        kind, _, name = item.partition('=')
        if kind == 'profile':
            try:
                Config(Options(profiles=[name], no_config=True, clang_repl=clang_repl), backend=False)
            except ConfigError as e:
                return str(e)
        elif kind == 'command' and not shutil.which(name):
            return f'no {name} on PATH'
        elif kind == 'header' and not any(Path(d, name).is_file() for d in ('/usr/include', '/usr/local/include')):
            return f'no {name} in /usr/include or /usr/local/include'
        elif kind not in ('profile', 'command', 'header'):
            return f'a requirement is profile=, command= or header=, not {item!r}'
    return None


def clang_repl_major(clang_repl):
    from cpp_repl.config import ConfigError, clang_repl_version, find_clang_repl
    try:
        return clang_repl_version(find_clang_repl(clang_repl))[0][0]
    except ConfigError:
        return None


def check(path, root=None, cpp_repl=None, clang_repl=None, update=False):
    """Run a tutorial's transcripts; with update, rewrite the blocks that
    differ from the run."""
    t = read_tutorial(path)
    if t.errors:
        return Result(t.path, 'error', '\n'.join(t.errors))
    major = clang_repl_major(clang_repl or os.environ.get('CPP_REPL_CLANG_REPL'))
    if major is None:
        return Result(t.path, 'error', 'no clang-repl: set CPP_REPL_CLANG_REPL, or pass --clang-repl')
    if t.major is not None and major != t.major:
        return Result(t.path, 'skipped', f'recorded with clang-repl {t.major}, and this one is {major}')
    reason = unmet(t.requires, clang_repl or os.environ.get('CPP_REPL_CLANG_REPL'))
    if reason:
        return Result(t.path, 'skipped', reason)
    root = Path(root or CHECKOUT).resolve()
    command = list(cpp_repl or [sys.executable, str(CHECKOUT / 'bin' / 'cpp-repl')])
    # A relative command names a path from here, not from the run's directory.
    command = [str(Path(c).resolve()) if c != sys.executable and '/' in c and Path(c).exists() else c
               for c in command]
    runner = Runner(t, root, command, clang_repl)
    try:
        runner.run()
    finally:
        runner.close()
    report = []
    for block, expected, actual in runner.failures:
        report.append(f'{t.path}:{block.first}: the transcript differs from the run:')
        report += difflib.unified_diff(normalise(expected).splitlines(), normalise(actual).splitlines(),
                                       'tutorial', 'run', lineterm='', n=2)
    if update and runner.failures:
        lines = list(t.lines)
        for block, _, actual in sorted(runner.failures, key=lambda f: -f[0].first):
            kept = lines[block.first:block.first + block.lead]
            lines[block.first:block.first + len(block.lines)] = kept + actual.rstrip('\n').split('\n')
        t.path.write_text('\n'.join(lines))
        report.append(f'{t.path}: {len(runner.failures)} block(s) rewritten from the run')
    return Result(t.path, 'differs' if runner.failures else 'ok', '\n'.join(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tutorials', nargs='+', type=Path)
    parser.add_argument('--root', type=Path, help='the repository the tutorials are in (default: this checkout)')
    parser.add_argument('--cpp-repl', help="the command that runs cpp-repl (default: this checkout's bin/cpp-repl)")
    parser.add_argument('--clang-repl', help='the clang-repl to run (default: $CPP_REPL_CLANG_REPL, then the newest)')
    parser.add_argument('--update', action='store_true', help='rewrite the blocks that differ from the run')
    args = parser.parse_args()
    results = [check(p, args.root, shlex.split(args.cpp_repl) if args.cpp_repl else None, args.clang_repl,
                     args.update) for p in args.tutorials]
    for r in results:
        print(f'{r.path}: {r.status}')
        if r.report:
            print(r.report)
    statuses = {r.status for r in results}
    if 'error' in statuses:
        return 2
    if 'differs' in statuses:
        return 1
    return SKIPPED if statuses == {'skipped'} else 0

if __name__ == '__main__':
    sys.exit(main())
