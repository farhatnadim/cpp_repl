"""The session: the journal, recovery, commands and saving."""

import json
import os
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

from . import pch
from .backend import ANY_ERROR, STARTUP_ERROR, Backend, BackendError, outcome, plain
from .config import session_dir_name
from .diagnostics import SNIPPET, Diagnostics
from .lexer import (Origin, open_end, unsplice, declared_names, in_function, is_definition, join, lambda_free_names,
                    lex, located, rewrite_captures, split_statements, trailing_expression, transform)
from .sources import PRELUDE_NAMES, Input, inputs_from_file, split_directives

STARTUP_DEADLINE = 300   # seconds; a large prelude on a cold disk takes a while
# Where an input typed at the prompt starts, as diagnostics name it.
TYPED = Origin('<input>', 1, 0, 0)

HELP = """\
Type C++ as you would inside main(). Enter runs the input once its brackets
balance and its statement is finished, and starts a new line otherwise. A
multi-line if or try, and a function head such as `int f()`, run after an
empty line, so that an else, a catch or the body can still follow. Esc then
Enter runs a block as it stands.

A bare expression, with no semicolon, prints its value; show(x) does the same.
A `return N;` ends the program as main()'s return does: a file stops there,
and cpp-repl --batch exits with N. So does an exception nothing catches, as
abort() would (status 134): the prompt says what was thrown and goes on.

  %undo           drop the last input      %restart        rebuild from the journal
  %reset          start again, empty       %journal        list the inputs so far
  %save FILE      the journal as a .repl   %save-cpp FILE  the journal as a C++ program
  %load FILE      run a .cpp or a .repl    %lib PATH       load a shared library
  %time INPUT     run INPUT and print how long it took, and how long the code ran
  %timeit INPUT   time INPUT in a loop, as IPython does
  %flags          this session's compiler flags, preludes and libraries
  %quit           leave (or Ctrl-D)

Tab completes names; Ctrl-R searches the history. Ctrl-C stops a running
input and rebuilds the session without it, and so does a crash. A name
declared again replaces the old one: the session is rebuilt without it, and
says so; an input that needs the old one keeps it. The JIT compiles at -O0
unless cpp-repl started with -O1, -O2 or -O3 (add --native for AVX and FMA).
"""

# text: as typed. sent: the lines clang-repl ran. kind: 'stmt', 'expr' (a bare
# expression with a value), 'void' (a bare void expression), 'pp' or 'lib'.
Entry = namedtuple('Entry', 'text sent kind')

VOID_MARK = '\x1e'   # what repl::print_expr writes after a void expression
# What the prelude writes for a value with no printer: <type> between two marks.
_UNPRINTABLE = re.compile('\x1f<(.*?)>\x1f')
# What repl::finish writes when a statement ended the program, on a line of
# its own: the status between two marks; after an uncaught exception, '!',
# the status and what was thrown.
_END_MARK = re.compile('\x1d(!?)(-?\\d+)([^\x1d]*)\x1d\n?')
# What repl::finish writes for every statement that ran in a function: how
# long the run took, in nanoseconds after a '~', between two marks.
_RUN_MARK = re.compile('\x1d~(\\d+)\x1d\n?')
# What repl::started writes as a statement begins to run (backend.START_MARK).
_START_MARK = re.compile('\x1d>\x1d\n?')
# A line of clang's diagnostics: a header, clang-repl's verdict, an include
# chain, or a source line or carets under a header. The echo holds these
# back until the program's own output starts, so that an error cpp-repl
# handles itself (a redefinition) is never shown.
_DIAGNOSTIC_LINE = re.compile(r'(?:\S[^:\n]*:\d+:\d+: )?(?:fatal error|error|warning|note|remark): '
                              r'|In file included from |' + SNIPPET.pattern)
# What clang says of a name declared again.
_REDEFINED = re.compile(r"error: redefinition of '([^']+)'")
# What clang says of a lambda capturing a variable typed at the prompt, which
# is a global, and of a lambda at file scope with a capture-default: the
# capture goes, and the lambda uses the global itself (lexer.rewrite_captures).
_CAPTURED = re.compile(r"error: '([^']+)' cannot be captured because it does not have automatic storage duration")
_CAPTURE_DEFAULT = re.compile(r'error: non-local lambda expression cannot have a capture-default')


def _handled(diagnostics):
    """Whether cpp-repl acts on these held diagnostics itself, rather than
    show them: a redefinition, or a capture a global cannot have."""
    return any(pattern.search(diagnostics) for pattern in (_REDEFINED, _CAPTURED, _CAPTURE_DEFAULT))


def _top_level_comma(tokens):
    """Whether a comma stands outside every (), [] and {} in tokens. (One
    inside <> counts too, and the parentheses it earns do no harm.)"""
    depth = 0
    for t in tokens:
        if t.kind != 'op':
            continue
        if t.text in '([{':
            depth += 1
        elif t.text in ')]}':
            depth -= 1
        elif t.text == ',' and depth == 0:
            return True
    return False


def printer_hint(name):
    """How to give the type name a printer, for a note."""
    if name.startswith('std::'):
        return f'[{name} prints as <{name}>: a repl::Put<{name}> specialisation gives it a printer]'
    where = ' in its own namespace' if '::' in name.split('<')[0] else ''
    return f'[{name} prints as <{name}>: an operator<<{where}, or a repl::Put<{name}> specialisation, gives it a printer]'


class SessionError(Exception):
    """The session cannot go on: clang-repl does not start."""


class Output:
    """Where the session writes, and whether the cursor is at the start of a
    line. The stream is sys.stdout unless one is given. begin(), failed()
    and end() bracket each input run from a file, for JsonOutput."""

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

    @property
    def gone(self):
        """Whether whoever read the stream has gone, as a --connect client
        that died has: a run then stops after the input it is on."""
        return getattr(self._stream, 'gone', False)

    def note(self, text):
        """A line of cpp-repl's own, about the input that ran."""
        self.end_line()
        self.write(text + '\n')

    def begin(self, number, origin):
        pass

    def failed(self, status):
        pass

    def returned(self, status):
        """The input returned from main() with status."""

    def ran(self, seconds):
        """The input's code ran for seconds, compiling it apart."""

    def end(self):
        pass


class JsonOutput(Output):
    """One JSON object per line for each input, in place of the transcript
    (cpp-repl --json): {"input": N, "file": PATH, "line": N, "status": S,
    "seconds": T, "run_seconds": T, "output": TEXT, "notes": [TEXT, ...],
    "returned": N}, where status is the first of ok, error (it did not
    compile), link, exception (one left the input uncaught), crash, exit (the
    program called exit()), stuck, timeout or interrupted, run_seconds is how
    long a statement's code ran, compiling it apart (a declaration at file
    scope has none), and returned is main's return value when the input
    returned. Anything written between inputs is {"output": TEXT}
    or {"note": TEXT}, and the last line is {"exit": STATUS, "inputs": N,
    "failures": N, "skipped": N}, skipped being the inputs left unrun once
    the program ended or --stop-on-error stopped the run."""

    def __init__(self, stream=None):
        super().__init__(stream)
        self.records = []   # the inputs running: a %load runs inputs inside its own

    def _emit(self, record):
        stream = self._stream or sys.stdout
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush()

    def write(self, text):
        if not text:
            return
        if self.records:
            self.records[-1]['output'] += text
        else:
            self._emit({'output': text})

    def end_line(self):
        pass

    def note(self, text):
        text = text[1:-1] if text.startswith('[') and text.endswith(']') else text
        if self.records:
            self.records[-1]['notes'].append(text)
        else:
            self._emit({'note': text})

    def begin(self, number, origin):
        self.records.append({'input': number, 'origin': origin, 'status': 'ok', 'output': '', 'notes': [],
                             'start': time.monotonic()})

    def failed(self, status):
        if self.records and self.records[-1]['status'] == 'ok':
            self.records[-1]['status'] = status

    def returned(self, status):
        if self.records:
            self.records[-1]['returned'] = status

    def ran(self, seconds):
        if self.records:
            self.records[-1]['run_seconds'] = round(seconds, 9)

    def end(self):
        r = self.records.pop()
        record = {'input': r['input']}
        if r['origin']:
            record.update(file=str(r['origin'].path), line=r['origin'].line)
        record.update(status=r['status'], seconds=round(time.monotonic() - r['start'], 3))
        if 'run_seconds' in r:
            record['run_seconds'] = r['run_seconds']
        record['output'] = r['output']
        if r['notes']:
            record['notes'] = r['notes']
        if 'returned' in r:
            record['returned'] = r['returned']
        self._emit(record)

    def finish(self, status, session):
        """End what is still running, as after %quit, then the last line."""
        while self.records:
            self.end()
        self._emit({'exit': status, 'inputs': session.inputs_run, 'failures': session.failures,
                    'skipped': session.skipped})


OUT = Output()


def format_seconds(seconds):
    for unit, scale in (('s', 1), ('ms', 1e3), ('µs', 1e6)):
        if seconds >= 1 / scale or unit == 'µs':
            return f'{seconds * scale:.3g} {unit}'


_INCLUDE_NAME = re.compile(r'\s*#\s*include\s*["<]([^">]*)[">]')
# Headers of C++23 and C++26 that a standard library may lack yet: the
# standard each came with, and which libraries have it.
_NEWER_HEADERS = {'print': ('C++23', 'libstdc++ 14 and libc++ 17'), 'generator': ('C++23', 'libstdc++ 14'),
                  'mdspan': ('C++23', 'libstdc++ 14 and libc++ 18'), 'flat_map': ('C++23', 'libstdc++ 15 and libc++ 20'),
                  'flat_set': ('C++23', 'libstdc++ 15 and libc++ 20'), 'spanstream': ('C++23', 'libstdc++ 12 and libc++ 20'),
                  'stdfloat': ('C++23', 'libstdc++ 13'), 'text_encoding': ('C++26', 'libstdc++ 15')}
# `import std;`: no clang-repl has a standard library module to import.
_NO_MODULE = re.compile(r"fatal error: module '([^']+)' not found")


class Session:
    """program_input is the file descriptor whose input the code reads, as
    stdin and std::cin: by default, none (the code reads end-of-file)."""

    def __init__(self, cfg, cwd, batch, painter=None, out=None, program_input=None):
        self.cfg, self.cwd, self.batch, self.painter = cfg, cwd, batch, painter
        self.out = out or OUT
        try:
            self.program_input = os.dup(program_input) if program_input is not None else None
        except OSError:
            self.program_input = None
        if self.program_input is None:
            self.program_input = os.open(os.devnull, os.O_RDONLY)
        self.journal = []
        self.new_run()
        self.echo = not (cfg.options.quiet or cfg.options.json)   # whether run_inputs shows each input
        self.tainted = False   # an input failed to compile in this clang-repl
        self.returned = None   # the status the input running ended the program with (see _show)
        self.thrown = None     # what an uncaught exception was, when that ended it
        self.run_seconds = None   # how long the input's code ran, when it ran in a function
        self.spare = None
        self.files = 0
        self.functions = 0   # functions made to run inputs in: cpp_repl_1, ...
        self.inputs_dir = cfg.inputs / session_dir_name()
        self.prelude_names = PRELUDE_NAMES | {h.name for h in cfg.preludes}
        self.sources = set()   # what the inputs' origins name
        self.unprintable = {}  # type: whether a note has said how to print it
        self.diagnostics = Diagnostics(cfg.options.diagnostics == 'brief', self.sources)
        self.pch_prepared = False   # whether the precompiled header was looked for, or built
        self.backend = self._start()
        if not self.backend.knows_errors:
            self._note(f'[clang-repl {cfg.version_text} reports errors in a way cpp-repl does not '
                       'recognise: a failed input may be journaled]')
        if not batch:
            self.spare = self._backend()

    def _backend(self):
        return Backend(self.cfg, self.cwd, self.program_input)

    def _start(self, backend=None):
        """A backend that has loaded the libraries: the one given if it got
        there, else a new one. The first start prepares the precompiled
        header of the preludes, when the configuration wants one; a start
        that fails because of it rebuilds it once, then goes without."""
        cfg = self.cfg
        if cfg.pch_wanted and not self.pch_prepared:
            self.pch_prepared = True
            try:
                cfg.set_pch(pch.prepare(cfg))
            except pch.PCHError as e:
                cfg.set_pch(None, reason=str(e))
                if not e.remembered:
                    self._config_note(f'[no precompiled header: {e}; starting from the headers]')
        output, rebuilt = '', False
        candidates = [backend, None]
        while candidates:
            candidate = candidates.pop(0)
            b = candidate or self._backend()
            try:
                output, alive = b.collect(b.ready, deadline=STARTUP_DEADLINE)
                if alive and outcome(output) == 'ok' and not STARTUP_ERROR.search(plain(output)):
                    if b.calibrate(deadline=60):
                        return b
                    output = b.how_it_ended()
            except BackendError as e:
                output = str(e)
            b.kill()
            if not cfg.pch:
                continue
            # A start that fails with the precompiled header: an out-of-date
            # one is built again, once; otherwise the session starts from the
            # headers, and the next day's starts go without it at once.
            if pch.blamed(plain(output), cfg.pch) and not rebuilt:
                rebuilt = True
                try:
                    cfg.set_pch(pch.prepare(cfg, rebuild=True))
                    self._config_note('[the precompiled header did not load: rebuilt]')
                except pch.PCHError as e:
                    cfg.set_pch(None, reason=str(e))
                    self._config_note(f'[no precompiled header: {e}; starting from the headers]')
            else:
                first = next((line for line in plain(output).splitlines() if 'error' in line), 'no answer')
                reason = f'clang-repl did not start with it: {first.strip()[:200]}'
                pch.remember_failure(cfg, reason)
                cfg.set_pch(None, reason=reason)
                self._config_note('[clang-repl did not start with the precompiled header: starting from the headers]')
            candidates = [None]
        raise SessionError('clang-repl failed to start:\n' + output)

    def _config_note(self, text):
        """A note on the configuration: on stderr in a batch run, whose
        stdout is the program's; anywhere else, as the session's notes go."""
        if self.batch and not isinstance(self.out, JsonOutput):
            sys.stderr.write(text + '\n')
        else:
            self._note(text)

    def close(self):
        if self.spare:
            self.spare.kill()   # nothing in it to keep
            self.spare.proc.wait()
        self.backend.close()
        if self.program_input is not None:
            os.close(self.program_input)
            self.program_input = None
        shutil.rmtree(self.inputs_dir, ignore_errors=True)

    def _note(self, text):
        self.out.note(self.painter.note(text) if self.painter else text)

    # -- restoring --------------------------------------------------------

    def new_run(self):
        """Count afresh what a run of files reports: for each that
        cpp-repl --serve runs."""
        self.failures = 0
        self.inputs_run = 0      # inputs run from files
        self.skipped = 0         # inputs of files left unrun once the run stopped
        self.timeouts = 0        # inputs stopped at --timeout
        self.interrupted = 0     # inputs Ctrl-C stopped
        self.program_ends = 0    # inputs that ended the program: a return from main, exit(), a crash
        self.end_status = None   # the exit status the first of those stands for (see _status)
        self.end_returned = False   # whether that first end was a return from main
        self.stop_reason = None  # why run_inputs stopped a run, for the files left after it

    def restore(self, journal, reason, then='', note=True):
        """Replace the backend with a fresh one that has replayed journal.
        then ends the note that says so, unless note is False."""
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
                self.spare = self._backend()
        finally:
            signal.signal(signal.SIGINT, previous)
        if note:
            self._note(f'[{reason}: restored {len(kept)} input{"s" if len(kept) != 1 else ""}{then}]')

    # -- running ----------------------------------------------------------

    def _show(self, text):
        """Write backend output, its diagnostics filtered and the prelude's
        marks taken out, painted when colour is on."""
        text = _UNPRINTABLE.sub(self._unprintable, self.diagnostics.feed(text))
        text = _END_MARK.sub(self._returned, _RUN_MARK.sub(self._ran, _START_MARK.sub('', text)))
        self.out.write(self.painter.feed(text) if self.painter else text)

    def _ran(self, m):
        # An input of several statements runs each in a function of its own:
        # their runs add up.
        self.run_seconds = (self.run_seconds or 0) + int(m.group(1)) / 1e9
        self.out.ran(self.run_seconds)
        return ''

    def _unprintable(self, m):
        self.unprintable.setdefault(m.group(1), False)
        return f'<{m.group(1)}>'

    def _returned(self, m):
        self.returned = int(m.group(2))
        self.thrown = m.group(3).strip() if m.group(1) else None
        return ''

    def _show_end(self):
        if self.painter:
            self.out.write(self.painter.flush())
        self.out.end_line()
        for name, told in self.unprintable.items():
            if not told and not name.startswith('('):   # a lambda's or an unnamed type's
                self._note(printer_hint(name))
            self.unprintable[name] = True

    def _send(self, line, echo=True, hold=None):
        """Run one line. Returns (output, result), result being 'ok', 'error',
        'link', 'dead', 'interrupted' or 'timeout'. The echo holds back the
        diagnostics an input starts with until its own output begins; when
        nothing else came and hold(text) says so, they stay held, in
        self.held, for the caller to act on or show."""
        self.diagnostics.start()
        self.returned = self.thrown = self.run_seconds = None
        self.held = ''
        held, live = [], False

        def show(text):
            nonlocal live
            if not live and all(_DIAGNOSTIC_LINE.match(plain(l)) or not l.strip() for l in text.splitlines()):
                held.append(text)
                return
            live = True
            self._show(''.join(held) + text)
            held.clear()

        try:
            output, alive = self.backend.run(line, echo=show if echo else None, deadline=self.cfg.options.timeout)
        except (KeyboardInterrupt, BackendError) as stop:
            self.backend.kill()
            self._show(''.join(held))
            self._show_end()
            return '', 'interrupted' if isinstance(stop, KeyboardInterrupt) else 'timeout'
        if echo:
            text = ''.join(held)
            if hold and text and hold(plain(text)):
                self.held = text
            else:
                self._show(text)
            self._show_end()
        if not alive:
            return output, 'dead'
        return output, outcome(output)

    def _after(self, text, sent, output, result, kind='stmt', journal=True, again=None):
        """Journal or recover after an input ran. Returns whether the run
        goes on: always at the prompt, where the session is rebuilt as
        needed; in a batch run, unless clang-repl died or was interrupted.
        again(), when given, runs the input once more."""
        if result == 'ok':
            if journal:
                self.journal.append(Entry(text, sent, kind))
            if self.returned is not None:
                # The statement ended the program: it returned from main(),
                # or an exception left it. A file's final `return 0;` goes
                # without saying; anything else is said.
                self._program_ended(self.returned, returned=self.thrown is None)
                if self.thrown is not None:
                    self.out.failed('exception')
                    self.failures += 1
                    self._note(f'[uncaught exception: {self.thrown}]')
                else:
                    self.out.returned(self.returned)
                    if self.returned != 0 or getattr(text, 'origin', TYPED) is TYPED:
                        self._note(f'[main returned {self.returned}]')
            return True
        if result == 'link' and again and self.tainted:
            # clang-repl keeps the template instantiations of an input that
            # failed to compile, without their code, so a later input that
            # needs one cannot link. It never ran: run it in a fresh clang-repl.
            self.restore(self.journal, 'that input failed to link after an earlier error',
                         then='; running it again')
            return again()
        status = None
        if result == 'dead':
            how, status = self.backend.how_it_ended(), self._status()
            self._program_ended(status)
        self.out.failed(self._label(result, status))
        if status != 0:   # exit(0) ends the program as it would end compiled
            self.failures += 1
        if result == 'error':
            self.tainted = True
        if result == 'link':
            # A failed link leaves clang-repl failing every later input.
            self.restore(self.journal, 'that input failed to link')
        elif self.batch:
            if result == 'dead':
                self._note(f'[{how}]')
        elif result == 'dead':
            self.restore(self.journal, how)
        elif result == 'interrupted':
            self.restore(self.journal, 'interrupted')
        if result == 'interrupted':
            self.interrupted += 1
        if result == 'timeout':
            self.timeouts += 1
            self.restore(self.journal, f'that input ran past {self.cfg.options.timeout:g} s')
        return not self.batch or result not in ('dead', 'interrupted')

    def _program_ended(self, status, returned=False):
        """The program is over, as the compiled one would be: main returned,
        exit() was called, or it crashed. The first end's status counts."""
        if self.end_status is None:
            self.end_status = status
            self.end_returned = returned
        self.program_ends += 1

    def _status(self):
        """The exit status the end of clang-repl stands for, as a shell
        reports a program's: 128 + the signal that killed it, or the status
        the program passed to exit(). None when it got stuck on errors."""
        if self.backend.stuck:
            return None
        rc = self.backend.proc.wait()
        return 128 - rc if rc < 0 else rc

    def _label(self, result, status):
        """result as --json names it."""
        if result != 'dead':
            return result
        if status is None:
            return 'stuck'
        return 'crash' if self.backend.proc.returncode < 0 else 'exit'

    def exit_status(self):
        """What cpp-repl exits with after running files: the status of the
        program's end, if main returned one, or it crashed or called exit()
        with one; else 130, as a shell reports Ctrl-C, if one stopped an
        input; else 124, as timeout(1) reports it, if an input ran past
        --timeout; else 1 if an input failed; else 0."""
        return self.end_status or (130 if self.interrupted else 124 if self.timeouts else 1 if self.failures else 0)

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

    def run_input(self, text, redefine=True, as_typed=None):
        """Run one complete input: an Input, which says where it comes from,
        or a str typed at the prompt. Returns False when the session should
        stop. A name the input declares again replaces the old one (see
        _redefine), unless redefine is False. The journal keeps as_typed
        when it is given: the input as the user wrote it, when what runs is
        a rewriting of it."""
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
        send = transform(text, fresh=self._fresh, origin=origin, exceptions=self.cfg.exceptions)
        if send.kind == 'empty':
            return True
        kind = send.kind
        again = partial(self.run_input, text, as_typed=as_typed)
        journaled = text if as_typed is None else as_typed
        if kind == 'expr':
            # Run in a function, for the local struct that print_expr needs, and
            # because a lambda in a top-level statement crashes clang-repl 23.
            # A placed expression ends its line, so that a diagnostic's source
            # line shows the user's text, and none of this plumbing.
            printed = in_function(self._fresh(), 'struct cpp_repl_tag; '
                                  f'repl::print_expr((({send.text}{chr(10) if send.via_file else ""}), '
                                  'repl::printer<cpp_repl_tag>{}));', exceptions=self.cfg.exceptions)
            line = self._file(printed) if send.via_file else printed
            output, result = self._send(line, echo=False)
            if result != 'error':
                if VOID_MARK in output:
                    kind, output = 'void', output.replace(VOID_MARK, '')
                if not (result == 'link' and self.tainted):   # else it runs again, with a note
                    self._show(output)
                self._show_end()
                return self._after(journaled, [line], output, result, kind, again=again)
            # Not something print_expr takes. The attempt failed to compile,
            # which can leave stale instantiations behind like any error. What
            # is no expression is a declaration the lexer did not see as one
            # (`Foo *p`), or wrong: it goes at file scope, unwrapped, where a
            # declaration belongs. Inside a function it would be a local,
            # silently gone with the statement.
            self.tainted = True
            send, kind = transform(text, fresh=self._fresh, origin=origin, as_statement=True, wrap=False,
                                  exceptions=self.cfg.exceptions), 'stmt'
        line = self._file(send.text) if send.via_file else send.text
        output, result = self._send(line, hold=_handled if redefine and kind == 'stmt' else None)
        if self.held:
            held, self.held = self.held, ''   # the redefinition's own inputs reset it
            redefined = _REDEFINED.search(plain(held))
            if redefined:
                replaced = self._redefine(redefined.group(1), text)
                if replaced is not None:
                    return replaced
            else:
                names = list(dict.fromkeys(_CAPTURED.findall(plain(held))))
                defaults = _CAPTURE_DEFAULT.search(plain(held)) is not None
                rewritten = rewrite_captures(text, set(names), defaults)
                if rewritten != text:
                    if names:
                        are = 'is a global here' if len(names) == 1 else 'are globals here'
                        self._note(f'[{", ".join(names)} {are}: the lambda uses {"it" if len(names) == 1 else "them"} '
                                   'without a capture]')
                    if defaults:
                        self._note('[a lambda at file scope has no capture-default: it uses the globals it names]')
                    rerun = Input(rewritten, text.origin) if isinstance(text, Input) else rewritten
                    return self.run_input(rerun, redefine=redefine, as_typed=journaled)
            self._show(held)
            self._show_end()
        if kind == 'pp' and result == 'error' and 'file not found' in output:
            m = _INCLUDE_NAME.match(stripped)
            if m and m.group(1) in _NEWER_HEADERS:
                standard, libraries = _NEWER_HEADERS[m.group(1)]
                self._note(f'[<{m.group(1)}> is {standard}, in {libraries}: this clang-repl\'s standard library lacks it]')
        if result == 'error' and (m := _NO_MODULE.search(plain(output))):
            self._note(f"[clang-repl has no module '{m.group(1)}': #include the headers]")
        return self._after(journaled, [line], output, result, kind, again=again)

    def _declares(self, entry, name):
        return entry.kind == 'stmt' and name in declared_names(lex(unsplice(entry.text))[0])

    def _redefine(self, name, text):
        """text declares name again: run it in the place of the newest
        journal entry that declared name. A fresh clang-repl replays the
        inputs before that entry, runs text, then replays the inputs after
        it on top of the new declaration. Returns what text's run returned;
        None when the old name is not the journal's (a prelude's, say), when
        text fails anyway, or when a later input cannot take the new
        declaration: the session is then as it was, and the caller shows the
        error."""
        at = next((i for i in range(len(self.journal) - 1, -1, -1) if self._declares(self.journal[i], name)), None)
        if at is None:
            return None
        was = self.journal
        self.restore(was[:at], 'redefinition', note=False)
        ran = self.run_input(text, redefine=False) if len(self.journal) == at else False
        if not self.journal or self.journal[-1].text != text:
            self.restore(was, 'redefinition', note=False)
            self._note(f'[{name} stays as it is: the input failed]')
            return None
        for entry in was[at + 1:]:
            if not all(self._replay(line) for line in entry.sent):
                self.restore(was, 'redefinition', note=False)
                self._note(f'[{name} stays as it is: {entry.text.splitlines()[0]} needs the one there was]')
                return None
            self.journal.append(entry)
        replayed = len(was) - 1
        # The old entry went whole: `int a = 1, b = 2;` redefined as `a` loses b too.
        gone = [n for n in declared_names(lex(unsplice(was[at].text))[0]) if n != name]
        with_it = f'; {", ".join(gone)}, declared with it, {"is" if len(gone) == 1 else "are"} gone' if gone else ''
        self._note(f'[{name} redefined: {replayed} input{"s" if replayed != 1 else ""} replayed{with_it}]')
        return ran

    def _replay(self, line):
        """Run a journaled line again, its output discarded: whether it ran."""
        output, alive = self.backend.run(line)
        return alive and outcome(output) == 'ok'

    def run_inputs(self, inputs, echo):
        """Run inputs in order. Returns False when the run should stop: once
        the program ended, by a return from main, exit() or a crash, as it
        would compiled (a session at the prompt goes on, rebuilt), and with
        --stop-on-error at the first input that fails."""
        for i, text in enumerate(inputs):
            self.inputs_run += 1
            self.out.begin(self.inputs_run, getattr(text, 'origin', None))
            try:
                if echo and self.echo:
                    self._echo_input(text)
                failures, ends = self.failures, self.program_ends
                if not self.run_input(text) or self.program_ends > ends:
                    self.stop_reason = 'the program ended' if self.program_ends > ends else 'the run stopped'
                    self._left_unrun(len(inputs) - i - 1, self.stop_reason)
                    return False
                if self.failures > failures and self.cfg.options.stop_on_error:
                    self.stop_reason = 'stopped at the first failure'
                    self._left_unrun(len(inputs) - i - 1, self.stop_reason)
                    return False
            finally:
                self.out.end()
            if self.out.gone:
                # The input was read to its end, so the journal and clang-repl
                # agree; nobody is left to run the rest for.
                self.stop_reason = 'the client went away'
                self._left_unrun(len(inputs) - i - 1, self.stop_reason)
                return False
        return True

    def _left_unrun(self, left, why):
        """Say, and count for --json, the inputs a run leaves unrun."""
        self.skipped += left
        if left:
            self._note(f'[{why}: {left} input{"s" if left != 1 else ""} not run]')

    def files_left_unrun(self, files):
        """Say, and count for --json, the files after the one whose run
        stopped: a return from main ends the program, as it does compiled,
        and a file run after it would run in a program that is over."""
        if not files:
            return
        self.skipped += sum(len(inputs_from_file(f, skip=self.prelude_names)) for f in files)
        # A file's final `return 0;` goes without saying (_after), so it is said here.
        why = 'main returned 0' if self.end_returned and self.end_status == 0 else self.stop_reason
        self._note(f'[{why}: {", ".join(map(str, files))} not run]')

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
        if send.kind in ('empty', 'pp') or send.via_file:
            self.out.write('usage: %timeit STATEMENT or %timeit EXPRESSION\n')
            return True
        if send.kind == 'expr':
            # The value, if any, is kept, so that the work is not optimised away.
            body = f'repl::keep_result([&]() -> decltype(auto) {{ return ({send.text}); }});'
        else:
            body = send.text
        output, result = self._send(in_function(self._fresh(), f'repl::timeit([]{{ {body} }}, "{self.cfg.opt or "-O0"}");',
                                                timed=False, exceptions=self.cfg.exceptions))
        return self._after(text, [], output, result, journal=False)

    # -- saving -----------------------------------------------------------

    def _file_scope(self):
        """Which journal entries the saved program puts at file scope, and the
        variables among them. A definition goes there. A variable typed at
        the prompt is a global there; in the program it stays one when a
        function or a lambda typed later names it, and so, in turn, does a
        variable its initialiser names: inside main() it would be out of the
        function's reach, and a local the lambda does not capture. The journal
        is read backwards, so what a later entry needs is known first."""
        at_file_scope, needed, hoisted = set(), set(), []
        for i in range(len(self.journal) - 1, -1, -1):
            entry = self.journal[i]
            if entry.kind != 'stmt':
                continue
            text = unsplice(entry.text).strip()
            tokens, _ = lex(text)
            idents = {t.text for t in tokens if t.kind == 'ident'}
            if is_definition(text):
                at_file_scope.add(i)
                needed |= idents
                continue
            stmts = split_statements(tokens)
            names = [n for toks, control in stmts if not control for n in declared_names(toks)]
            declaration = bool(names) and all(not control and declared_names(toks) for toks, control in stmts)
            if declaration and needed.intersection(names):
                at_file_scope.add(i)
                hoisted[:0] = [n for n in names if n not in hoisted]
                needed |= idents
            else:
                # A lambda's body may name a global without capturing it; a
                # name it captures must stay a local of main(), as typed.
                needed |= lambda_free_names(tokens)
        return at_file_scope, hoisted

    def as_cpp(self, path):
        """(The journal as a C++ program, the variables it keeps at file
        scope): directives and definitions at file scope, with the variables
        a later function or lambda uses (see _file_scope); the rest inside
        main(), bare expressions through show()."""
        at_file_scope, hoisted = self._file_scope()
        includes, libs, scope, body = [], [], [], []
        for i, entry in enumerate(self.journal):
            text = unsplice(entry.text).strip()
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
                # A comma outside brackets (`a, b`) would make two arguments.
                expression = join(tokens)
                body.append(f'show(({expression}));' if _top_level_comma(tokens) else f'show({expression});')
            elif entry.kind == 'void':
                body.append(f'{join(tokens)};')
            elif i in at_file_scope:
                scope.append(entry.text.strip())
            else:
                stmts = split_statements(tokens)
                if stmts and open_end(*stmts[-1]):
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
        if hoisted:
            out += ['//',
                    f'// Globals, as at the prompt: {", ".join(hoisted)}. A function or a lambda below',
                    '// uses them, so they stay out of main() and are initialised before it runs.']
        out += [f'#include "{h.name}"' for h in self.cfg.preludes]
        out += includes
        if libs:
            out += ['', '// The session also loaded these libraries; link them as well:']
            out += [f'//   {lib}' for lib in libs]
        for item in scope:
            out += ['', item]
        out += ['', 'int main()', '{']
        for stmt in body:
            out += [('  ' + line) if line.strip() else '' for line in stmt.split('\n')]
        if not body or not body[-1].startswith('return'):
            out.append('  return 0;')
        out += ['}', '']
        return '\n'.join(out), hoisted

    def describe_flags(self):
        cfg = self.cfg
        lines = [f'clang-repl:  {cfg.clang_repl} ({cfg.version_text})',
                 f'flags:       {shlex.join(cfg.compiler_flags)}',
                 'preludes:    ' + ('\n             '.join(str(h) for h in cfg.preludes))]
        if cfg.pch:
            lines.append(f'pch:         {cfg.pch.pch}')
        else:
            lines.append(f'pch:         none ({cfg.pch_reason})')
        if cfg.runtime:
            lines.append(f'runtime:     {cfg.runtime}')
        if cfg.libs:
            lines.append('libraries:   ' + '\n             '.join(str(lib) for lib in cfg.libs))
        lines.append(f'diagnostics: {cfg.options.diagnostics}')
        if cfg.compile_commands:
            lines.append(f'from:        {cfg.compile_commands}')
        for t in cfg.tomls:
            lines.append(f'from:        {t.name}')
        lines += cfg.notes + cfg.profile_notes
        return '\n'.join(lines) + '\n'

    # -- commands ---------------------------------------------------------

    def command(self, text):
        name, _, arg = text.partition(' ')
        arg = arg.strip()
        if name == '%help':
            self.out.write(HELP + self.cfg.help)
        elif name == '%quit':
            raise SystemExit(self.exit_status() if self.batch else 0)
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
                where = ''
                if name == '%save':
                    path.write_text(''.join(e.text + '\n' for e in self.journal))
                else:
                    program, hoisted = self.as_cpp(path)
                    path.write_text(program)
                    if hoisted:
                        where = f'; {", ".join(hoisted)} at file scope, for the functions and lambdas that use them'
                self.out.write(f'saved {len(self.journal)} inputs to {path.resolve()}{where}\n')
        elif name == '%load':
            if not arg or not Path(arg).expanduser().is_file():
                self.out.write('usage: %load FILE (a C++ source or a .repl)\n')
            else:
                inputs = inputs_from_file(Path(arg).expanduser(), skip=self.prelude_names)
                return self.run_inputs(inputs, echo=True) or not self.batch
        elif name == '%lib':
            line = f'%lib {Path(arg).expanduser()}' if arg else '%lib'
            output, result = self._send(line)
            if result == 'ok' and ANY_ERROR.search(plain(output)):
                result = 'error'
            return self._after(text, [line], output, result, 'lib')
        elif name == '%time':
            if not arg:
                self.out.write('usage: %time INPUT\n')
                return True
            start = time.monotonic()
            self.run_seconds = None
            ok = self.run_input(arg)
            wall = format_seconds(time.monotonic() - start)
            self.out.end_line()
            if self.run_seconds is None:   # nothing ran in a function: a declaration, a directive, a % command
                self.out.write(f'wall time: {wall}\n')
            else:
                self.out.write(f'wall time: {wall}, of which the code ran {format_seconds(self.run_seconds)}\n')
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
