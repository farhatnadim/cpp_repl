"""The session: the journal, recovery, commands and saving."""

import re
import shlex
import shutil
import signal
import sys
import time
from collections import namedtuple
from datetime import date
from functools import partial
from pathlib import Path

from .backend import _ANY_ERROR, _STARTUP_ERROR, Backend, BackendError, outcome, plain
from .config import session_dir_name
from .diagnostics import Diagnostics
from .lexer import (Origin, _open_end, _unsplice, in_function, is_definition, join, lex, located,
                    split_statements, trailing_expression, transform)
from .sources import PRELUDE_NAMES, Input, inputs_from_file, split_directives

STARTUP_DEADLINE = 300   # seconds; a large prelude on a cold disk takes a while
# Where an input typed at the prompt starts, as diagnostics name it.
TYPED = Origin('<input>', 1, 0, 0)

HELP = """\
Type C++ as you would inside main(). Enter runs the input once its brackets
balance and its statement is finished, and starts a new line otherwise. A
multi-line if or try runs after an empty line, so that an else or catch can
still follow. Esc then Enter runs a block as it stands.

A bare expression, with no semicolon, prints its value; show(x) does the same.

  %undo           drop the last input      %restart        rebuild from the journal
  %reset          start again, empty       %journal        list the inputs so far
  %save FILE      the journal as a .repl   %save-cpp FILE  the journal as a C++ program
  %load FILE      run a .cpp or a .repl    %lib PATH       load a shared library
  %time INPUT     run INPUT and print how long it took, compiling included
  %timeit INPUT   time INPUT in a loop, as IPython does
  %flags          this session's compiler flags, preludes and libraries
  %quit           leave (or Ctrl-D)

Tab completes names; Ctrl-R searches the history. Ctrl-C stops a running
input and rebuilds the session without it, and so does a crash. Names can't be
redefined: use a new name, assign, or %undo. The JIT compiles at -O0 unless
cpp-repl started with -O1, -O2 or -O3 (add --native for AVX and FMA).
"""

# text: as typed. sent: the lines clang-repl ran. kind: 'stmt', 'expr' (a bare
# expression with a value), 'void' (a bare void expression), 'pp' or 'lib'.
Entry = namedtuple('Entry', 'text sent kind')

VOID_MARK = '\x1e'   # what repl::print_expr writes after a void expression


class SessionError(Exception):
    """The session cannot go on: clang-repl does not start."""


class Output:
    """Where the session writes, and whether the cursor is at the start of a
    line. The stream is sys.stdout unless one is given."""

    def __init__(self, stream=None):
        self._stream = stream
        self.at_line_start = True

    def write(self, text):
        if text:
            stream = self._stream or sys.stdout
            stream.write(text)
            stream.flush()
            self.at_line_start = plain(text).endswith('\n')

    def end_line(self):
        if not self.at_line_start:
            self.write('\n')


OUT = Output()


def format_seconds(seconds):
    for unit, scale in (('s', 1), ('ms', 1e3), ('µs', 1e6)):
        if seconds >= 1 / scale or unit == 'µs':
            return f'{seconds * scale:.3g} {unit}'


_INCLUDE_NAME = re.compile(r'\s*#\s*include\s*["<]([^">]*)[">]')


class Session:
    def __init__(self, cfg, cwd, batch, painter=None, out=None):
        self.cfg, self.cwd, self.batch, self.painter = cfg, cwd, batch, painter
        self.out = out or OUT
        self.journal = []
        self.failures = 0
        self.tainted = False   # an input failed to compile in this clang-repl
        self.spare = None
        self.files = 0
        self.functions = 0   # functions made to run inputs in: cpp_repl_1, ...
        self.inputs_dir = cfg.inputs / session_dir_name()
        self.prelude_names = PRELUDE_NAMES | {h.name for h in cfg.preludes}
        self.sources = set()   # what the inputs' origins name
        self.diagnostics = Diagnostics(cfg.options.diagnostics == 'brief', self.sources)
        self.backend = self._start()
        if not self.backend.knows_errors:
            self._note(f'[clang-repl {cfg.version_text} reports errors in a way cpp-repl does not '
                       'recognise: a failed input may be journaled]')
        if not batch:
            self.spare = Backend(cfg, cwd)

    def _start(self, backend=None):
        """A backend that has loaded the libraries: the one given if it got
        there, else a new one."""
        output = ''
        for candidate in (backend, None):
            b = candidate or Backend(self.cfg, self.cwd)
            try:
                output, alive = b.collect(b.ready, deadline=STARTUP_DEADLINE)
                if alive and outcome(output) == 'ok' and not _STARTUP_ERROR.search(plain(output)):
                    if b.calibrate(deadline=60):
                        return b
                    output = b.how_it_ended()
            except BackendError as e:
                output = str(e)
            b.kill()
        raise SessionError('clang-repl failed to start:\n' + output)

    def close(self):
        if self.spare:
            self.spare.kill()   # nothing in it to keep
            self.spare.proc.wait()
        self.backend.close()
        shutil.rmtree(self.inputs_dir, ignore_errors=True)

    def _note(self, text):
        self.out.end_line()
        self.out.write((self.painter.note(text) if self.painter else text) + '\n')

    # -- restoring --------------------------------------------------------

    def restore(self, journal, reason, then=''):
        """Replace the backend with a fresh one that has replayed journal.
        then ends the note that says so."""
        self.backend.kill()
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            backend = self._start(self.spare)
            self.spare = None
            self.tainted = False
            kept = []
            for entry in journal:
                ok = True
                for line in entry.sent:
                    output, alive = backend.run(line)
                    if not alive or outcome(output) != 'ok':
                        ok = False
                        break
                if not ok:
                    self._note(f'replay stopped at: {entry.text.splitlines()[0]}')
                    self.tainted = alive
                    if not alive:
                        backend = self._start()
                        for e in kept:
                            for line in e.sent:
                                backend.run(line)
                    break
                kept.append(entry)
            self.backend, self.journal = backend, kept
            if not self.batch:
                self.spare = Backend(self.cfg, self.cwd)
        finally:
            signal.signal(signal.SIGINT, previous)
        self._note(f'[{reason}: restored {len(kept)} input{"s" if len(kept) != 1 else ""}{then}]')

    # -- running ----------------------------------------------------------

    def _show(self, text):
        """Write backend output, its diagnostics filtered, painted when colour
        is on."""
        text = self.diagnostics.feed(text)
        self.out.write(self.painter.feed(text) if self.painter else text)

    def _show_end(self):
        if self.painter:
            self.out.write(self.painter.flush())
        self.out.end_line()

    def _send(self, line, echo=True):
        """Run one line. Returns (output, result), result being 'ok', 'error',
        'link', 'dead' or 'interrupted'."""
        self.diagnostics.start()
        try:
            output, alive = self.backend.run(line, echo=self._show if echo else None)
        except KeyboardInterrupt:
            self.backend.kill()
            self._show_end()
            return '', 'interrupted'
        if echo:
            self._show_end()
        if not alive:
            return output, 'dead'
        return output, outcome(output)

    def _after(self, text, sent, output, result, kind='stmt', journal=True, again=None):
        """Journal or recover after an input ran. Returns True if it worked,
        or in batch mode if the session can go on. again(), when given, runs
        the input once more."""
        if result == 'ok':
            if journal:
                self.journal.append(Entry(text, sent, kind))
            return True
        if result == 'link' and again and self.tainted:
            # clang-repl keeps the template instantiations of an input that
            # failed to compile, without their code, so a later input that
            # needs one cannot link. It never ran: run it in a fresh clang-repl.
            self.restore(self.journal, 'that input failed to link after an earlier error',
                         then='; running it again')
            return again()
        self.failures += 1
        if result == 'error':
            self.tainted = True
        if result == 'link':
            # A failed link leaves clang-repl failing every later input.
            self.restore(self.journal, 'that input failed to link')
        elif self.batch:
            if result == 'dead':
                self._note(f'[{self.backend.how_it_ended()}]')
        elif result == 'dead':
            self.restore(self.journal, self.backend.how_it_ended())
        elif result == 'interrupted':
            self.restore(self.journal, 'interrupted')
        if self.batch:
            return result not in ('dead', 'interrupted')
        return False

    def _fresh(self):
        self.functions += 1
        return f'cpp_repl_{self.functions}'

    def _file(self, text):
        """An #include of a new file holding text, for an input whose
        preprocessor lines must keep their own lines."""
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        self.files += 1
        path = self.inputs_dir / f'input_{self.files}.h'
        path.write_text(text + '\n')
        return f'#include "{path}"'

    def run_input(self, text):
        """Run one complete input: an Input, which says where it comes from,
        or a str typed at the prompt. Returns False when the session should
        stop."""
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith('%'):
            return self.command(stripped)
        origin = getattr(text, 'origin', TYPED)
        parts = split_directives(text)
        if len(parts) > 1:   # a pasted block of #include lines and code
            return all(self.run_input(Input(part, located(origin, text, at) if origin else None))
                       for _, part, at in parts)
        split = trailing_expression(text)
        if split:
            statements, expression, at = split
            known = origin and at is not None
            return all(self.run_input(Input(part, located(origin, text, start) if known else None))
                       for part, start in ((statements, len(text) - len(text.lstrip())), (expression, at)))
        if origin:
            self.sources.add(str(origin.path))
        send = transform(text, fresh=self._fresh, origin=origin)
        if send.kind == 'empty':
            return True
        kind = send.kind
        again = partial(self.run_input, text)
        if kind == 'expr':
            # Run in a function, for the local struct that print_expr needs, and
            # because a lambda in a top-level statement crashes clang-repl 23.
            printed = in_function(self._fresh(), 'struct cpp_repl_tag; '
                                  f'repl::print_expr((({send.text}), repl::printer<cpp_repl_tag>{{}}));')
            line = self._file(printed) if send.file else printed
            output, result = self._send(line, echo=False)
            if result != 'error':
                if VOID_MARK in output:
                    kind, output = 'void', output.replace(VOID_MARK, '')
                if not (result == 'link' and self.tainted):   # else it runs again, with a note
                    self._show(output)
                self._show_end()
                return self._after(text, [line], output, result, kind, again=again) or not self.batch
            # Not something print_expr takes. The attempt failed to compile,
            # which can leave stale instantiations behind like any error.
            self.tainted = True
            send, kind = transform(text, fresh=self._fresh, origin=origin, statement=True), 'stmt'
        line = self._file(send.text) if send.file else send.text
        output, result = self._send(line)
        return self._after(text, [line], output, result, kind, again=again) or not self.batch

    def run_inputs(self, inputs, echo):
        for text in inputs:
            if echo:
                self._echo_input(text)
            if not self.run_input(text):
                return False
        return True

    def _echo_input(self, text):
        prompt, more = self.cfg.prompt, self.cfg.more
        if self.painter:
            text = self.painter.code(text)
            prompt, more = self.painter.prompt(prompt), self.painter.prompt(more)
        first, *rest = text.split('\n')
        self.out.end_line()
        self.out.write(prompt + first + '\n' + ''.join(more + r + '\n' for r in rest))

    def _timeit(self, text):
        send = transform(text, wrap=False)
        if send.kind in ('empty', 'pp') or send.file:
            self.out.write('usage: %timeit STATEMENT or %timeit EXPRESSION\n')
            return True
        body = f'repl::keep({send.text});' if send.kind == 'expr' else send.text
        output, result = self._send(in_function(self._fresh(), f'repl::timeit([]{{ {body} }}, {self.cfg.opt_level});'))
        return self._after(text, [], output, result, journal=False) or not self.batch

    # -- saving -----------------------------------------------------------

    def as_cpp(self, path):
        """The journal as a C++ program: directives and definitions at file
        scope, the rest inside main(), bare expressions through show()."""
        includes, libs, definitions, body = [], [], [], []
        for entry in self.journal:
            text = _unsplice(entry.text).strip()
            tokens, _ = lex(text)
            if entry.kind == 'pp':
                lines = [line for line in entry.text.strip().split('\n')
                         if not (_INCLUDE_NAME.match(line)
                                 and Path(_INCLUDE_NAME.match(line).group(1)).name in self.prelude_names)]
                if lines:
                    includes.append('\n'.join(lines))
            elif entry.kind == 'lib':
                libs.append(text.split(None, 1)[-1])
            elif entry.kind == 'expr':
                body.append(f'show({join(tokens)});')
            elif entry.kind == 'void':
                body.append(f'{join(tokens)};')
            elif is_definition(text):
                definitions.append(entry.text.strip())
            else:
                stmts = split_statements(tokens)
                if stmts and _open_end(*stmts[-1]):
                    body.append(join(tokens) + ';')
                else:
                    body.append(entry.text.strip())
        command = self.cfg.command()
        out = [f'// Saved from a cpp-repl session on {date.today()}. Build it with',
               f'//   clang++ $({command} --cflags) {path.name} $({command} --libs)',
               f'// and run it again in the REPL with: {command} {path.name}',
               '//',
               "// The cpp_repl*.h headers are the session's prelude: show(), keep(),",
               '// timeit(), and what the profiles add.']
        out += [f'#include "{h.name}"' for h in self.cfg.preludes]
        out += includes
        if libs:
            out += ['', '// The session also loaded these libraries; link them as well:']
            out += [f'//   {lib}' for lib in libs]
        for definition in definitions:
            out += ['', definition]
        out += ['', 'int main()', '{']
        for stmt in body:
            out += [('  ' + line) if line.strip() else '' for line in stmt.split('\n')]
        out += ['  return 0;', '}', '']
        return '\n'.join(out)

    def describe_flags(self):
        cfg = self.cfg
        lines = [f'clang-repl:  {cfg.clang_repl} ({cfg.version_text})',
                 f'flags:       {shlex.join(cfg.compiler_flags)}',
                 'preludes:    ' + ('\n             '.join(str(h) for h in cfg.preludes))]
        if cfg.libs:
            lines.append('libraries:   ' + '\n             '.join(str(lib) for lib in cfg.libs))
        lines.append(f'diagnostics: {cfg.options.diagnostics}')
        if cfg.compile_commands:
            lines.append(f'from:        {cfg.compile_commands}')
        for t in cfg.tomls:
            lines.append(f'from:        {t.name}')
        lines += cfg.notes
        return '\n'.join(lines) + '\n'

    # -- commands ---------------------------------------------------------

    def command(self, text):
        name, _, arg = text.partition(' ')
        arg = arg.strip()
        if name == '%help':
            self.out.write(HELP + self.cfg.help)
        elif name == '%quit':
            raise SystemExit(0 if not self.batch or not self.failures else 1)
        elif name == '%undo':
            if not self.journal:
                self.out.write('nothing to undo\n')
            else:
                self.out.write(f'undo: {self.journal[-1].text.splitlines()[0]}\n')
                self.restore(self.journal[:-1], 'undo')
        elif name == '%restart':
            self.restore(self.journal, 'restart')
        elif name == '%reset':
            self.restore([], 'reset')
        elif name == '%journal':
            for i, entry in enumerate(self.journal, 1):
                first, *rest = entry.text.split('\n')
                self.out.write(f'{i:3} {first}\n' + ''.join(f'    {r}\n' for r in rest))
        elif name in ('%save', '%save-cpp'):
            if not arg:
                self.out.write(f'usage: {name} FILE\n')
            else:
                path = Path(arg).expanduser()
                if name == '%save':
                    path.write_text(''.join(e.text + '\n' for e in self.journal))
                else:
                    path.write_text(self.as_cpp(path))
                self.out.write(f'saved {len(self.journal)} inputs to {path.resolve()}\n')
        elif name == '%load':
            if not arg or not Path(arg).expanduser().is_file():
                self.out.write('usage: %load FILE (a C++ source or a .repl)\n')
            else:
                inputs = inputs_from_file(Path(arg).expanduser(), skip=self.prelude_names)
                return self.run_inputs(inputs, echo=True) or not self.batch
        elif name == '%lib':
            line = f'%lib {Path(arg).expanduser()}' if arg else '%lib'
            output, result = self._send(line)
            if result == 'ok' and _ANY_ERROR.search(plain(output)):
                result = 'error'
            return self._after(text, [line], output, result, 'lib') or not self.batch
        elif name == '%time':
            if not arg:
                self.out.write('usage: %time INPUT\n')
                return True
            start = time.monotonic()
            ok = self.run_input(arg)
            self.out.end_line()
            self.out.write(f'wall time: {format_seconds(time.monotonic() - start)}\n')
            return ok
        elif name == '%timeit':
            if not arg:
                self.out.write('usage: %timeit INPUT\n')
                return True
            return self._timeit(arg)
        elif name == '%flags':
            self.out.write(self.describe_flags())
        else:
            self.out.write(f'unknown command {name}; %help lists them\n')
        return True
