"""One clang-repl process, and how its output is read.

Inputs go to clang-repl's stdin; its stdout and stderr come back merged in one
pipe. After each input, a %lib of a path that cannot exist makes clang-repl
print an error naming that path, which marks where the input's output ends.
Unlike a C++ marker, it adds nothing to the session. Each marker carries a
nonce and a sequence number, so output can never be mistaken for one.
"""

import os
import re
import resource
import secrets
import select
import signal
import subprocess
import time

_PARSE_FAILED = re.compile(r'^error: Parsing failed\.|^fatal error: ', re.M)
_LINK_FAILED = re.compile(r'^JIT session error: |^error: Failed to materialize symbols'
                          r'|^error: Duplicate definition of symbol', re.M)
_ANY_ERROR = re.compile(r'^error: ', re.M)
# At startup, an error anywhere: a prelude that fails to compile shows as
# "/x/prelude.h:3:1: error: ..." or "...: fatal error: ...", and clang-repl
# carries on without the rest of it.
_STARTUP_ERROR = re.compile(r'^(?:.*?:\d+:\d+: )?(?:fatal )?error: ', re.M)
_NOISE = re.compile(r'In file included from <<< inputs >>>:\d+:\s*$')
# Clang's line when an input reaches -ferror-limit. clang-repl 18 can loop
# forever after it; a newer one, or a limit asked for, answers the marker.
_STUCK = b'too many errors emitted'
STUCK_GRACE = 3   # seconds the marker gets after that line
_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')

# LLVM's crash handler: a trace starts with one of these lines and goes on
# with numbered frames. cpp-repl reports the crash in a line of its own, so
# the trace is dropped unless CPP_REPL_TRACES=1.
_TRACE_START = re.compile(r'^(Stack dump|PLEASE submit a bug report)')
_TRACE_FRAME = re.compile(r'^\s*\d+[.\s]')
_SHOW_TRACES = os.environ.get('CPP_REPL_TRACES') == '1'

MARK_PATH = '/nonexistent/cpp-repl/'
_ECHO = b'%lib ' + MARK_PATH.encode()
_PROBE = '__CPP_REPL_PROBE__'


def plain(text):
    """text without colour codes."""
    return _ANSI.sub('', text)


def outcome(output):
    """'ok', 'error' (it did not compile) or 'link' (it compiled but did not
    link, which leaves clang-repl unreliable)."""
    text = plain(output)
    if _LINK_FAILED.search(text):
        return 'link'
    if _PARSE_FAILED.search(text):
        return 'error'
    return 'ok'


def _no_core_dumps():
    """Make every clang-repl started from now on write no core file when it
    crashes: the limit is inherited. (A preexec_fn would do it per child, but
    is unsafe while another thread runs, and Tab completion uses one.)"""
    soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    if soft != 0:
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))


class BackendError(Exception):
    """clang-repl did not answer in time."""


class Backend:
    """A clang-repl process. The constructor sends the configuration's
    prologue and a marker; collect(backend.ready) waits for it, and
    calibrate() then learns what this clang-repl prints besides output."""

    def __init__(self, cfg, cwd):
        _no_core_dumps()
        self.proc = subprocess.Popen(cfg.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, cwd=cwd, env=cfg.env,
                                     start_new_session=True)
        self.fd = self.proc.stdout.fileno()
        self.buffer = b''
        self.nonce = secrets.token_hex(4)
        self.seq = 0
        self.stuck = False
        self.in_trace = False       # inside a crash trace from LLVM
        self.prompt = b''          # printed before each input is read; learned by calibrate()
        # What the marker line holds before the token: clang-repl's error prefix
        # and the path. Output with no newline at its end shares that line.
        self.mark_prefix = b'error: ' + MARK_PATH.encode()
        self.last_prefix = b''
        self.knows_errors = True   # whether outcome() recognises this clang-repl's errors
        try:
            for line in cfg.prologue:
                self._write(line)
            self.ready = self._mark()
        except (BrokenPipeError, OSError):
            self.ready = '__CPP_REPL_NEVER__'   # collect() then reports the dead process

    def _write(self, line):
        self.proc.stdin.write(line.encode() + b'\n')
        self.proc.stdin.flush()

    def _mark(self):
        self.seq += 1
        token = f'__CPP_REPL_SYNC_{self.nonce}_{self.seq}__'
        self._write(f'%lib {MARK_PATH}{token}')
        return token

    def calibrate(self, deadline):
        """Learn the prompt this clang-repl prints before reading an input,
        if any: it would otherwise start every input's output. Builds with
        libedit print none on a pipe. Then check that a parse error is
        recognised as one. Returns whether the process is still alive."""
        output, alive = self.collect(self._mark(), deadline=deadline)
        if not alive:
            return False
        if self.last_prefix.endswith(MARK_PATH.encode()):
            self.mark_prefix = self.last_prefix
        self._write(f'printf("{_PROBE}\\n");')
        output, alive = self.collect(self._mark(), deadline=deadline)
        line = next((line for line in output.splitlines() if _PROBE in line), None)
        if alive and line:
            self.prompt = plain(line[:line.index(_PROBE)]).encode()
        if not alive:
            return False
        self._write('#error cpp-repl-selftest')
        output, alive = self.collect(self._mark(), deadline=deadline)
        self.knows_errors = alive and outcome(output) == 'error'
        return alive

    def _trace(self, line):
        """Whether line belongs to a crash trace from LLVM."""
        if _SHOW_TRACES:
            return False
        if _TRACE_START.match(line):
            self.in_trace = True
        elif self.in_trace and not _TRACE_FRAME.match(line):
            self.in_trace = False
        return self.in_trace

    def run(self, line, echo=None, deadline=None):
        """Send one input. Returns (output, alive). With a deadline in
        seconds, raises BackendError when it passes."""
        try:
            self._write(line)
            token = self._mark()
        except (BrokenPipeError, OSError):
            return '', False
        return self.collect(token, echo, deadline)

    def _find(self, token):
        """Where token starts in the buffer, skipping an echo of the %lib
        line that asked for it; -1 if it has not arrived."""
        idx = self.buffer.find(token)
        while idx >= 0 and self.buffer[max(0, idx - len(_ECHO)):idx] == _ECHO:
            idx = self.buffer.find(token, idx + len(token))
        return idx

    def collect(self, token, echo=None, deadline=None):
        """Read up to the line holding the marker for token, which is
        dropped. echo(text) receives whole lines as they arrive. Returns
        (output, alive). With a deadline in seconds, raises BackendError when
        it passes."""
        tok, pieces, first = token.encode(), [], [True]
        until = time.monotonic() + deadline if deadline else None
        stuck_by = None   # when to give up on the marker after the error limit

        def emit(data):
            if first[0] and data:
                first[0] = False
                if self.prompt and data.startswith(self.prompt):
                    data = data[len(self.prompt):]
            text = ''.join(line for line in data.decode('utf-8', 'replace').splitlines(keepends=True)
                           if not _NOISE.match(plain(line)) and not self._trace(plain(line)))
            if text:
                pieces.append(text)
                if echo:
                    echo(text)

        while True:
            idx = self._find(tok)
            if idx >= 0:
                newline = self.buffer.find(b'\n', idx)
                if newline >= 0:
                    line_start = self.buffer.rfind(b'\n', 0, idx) + 1
                    self.last_prefix = self.buffer[line_start:idx]
                    cut = idx - len(self.mark_prefix)
                    if cut < line_start or self.buffer[cut:idx] != self.mark_prefix:
                        cut = line_start   # an unexpected marker line: drop all of it
                    tail = self.buffer[:cut]
                    if self.prompt and tail.endswith(self.prompt):
                        tail = tail[:-len(self.prompt)]
                    emit(tail)
                    self.buffer = self.buffer[newline + 1:]
                    return ''.join(pieces), True
            else:
                newline = self.buffer.rfind(b'\n')
                if newline >= 0:
                    emit(self.buffer[:newline + 1])
                    self.buffer = self.buffer[newline + 1:]
            waits = [t for t in (until, stuck_by) if t is not None]
            if waits:
                left = min(waits) - time.monotonic()
                if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                    if stuck_by is not None and time.monotonic() >= stuck_by:
                        self.stuck = True
                        self.kill()
                        stuck_by = None   # read what is left, to the end
                        continue
                    raise BackendError(f'clang-repl gave no answer within {deadline:.0f} s')
            chunk = os.read(self.fd, 65536)
            if not chunk:
                emit(self.buffer)
                self.buffer = b''
                return ''.join(pieces), False
            if _STUCK in chunk and stuck_by is None:
                stuck_by = time.monotonic() + STUCK_GRACE
            self.buffer += chunk

    def kill(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self):
        if self.proc.poll() is None:
            try:
                self._write('%quit')
                self.proc.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self.kill()
        self.proc.wait()

    def how_it_ended(self):
        if self.stuck:
            return 'clang-repl got stuck on that input'
        rc = self.proc.wait()
        if rc < 0:
            try:
                return f'clang-repl crashed ({signal.Signals(-rc).name})'
            except ValueError:
                return f'clang-repl crashed (signal {-rc})'
        return f'clang-repl exited (code {rc})'
