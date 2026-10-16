"""cpp-repl --serve SOCKET and cpp-repl --connect SOCKET: a session that
stays, for a coding agent's loop.

The server holds one session, with a spare clang-repl started in advance,
and listens on a Unix socket, which is the user's alone. A client sends one
request, a JSON line:

    {"files": [{"name": ..., "path": ..., "text": ...}], "stdin": TEXT,
     "fresh": BOOL, "quiet": BOOL, "json": BOOL,
     "timeout": SECONDS, "stop_on_error": BOOL, "diagnostics": "brief"|"full"}

The server runs the files' inputs, then stdin's, in the session, as
cpp-repl --batch would, and answers with JSON lines: {"out": TEXT} for what
the run writes, as it writes it, and {"exit": STATUS} at the end. With fresh,
the run starts from an empty session, the spare: no start-up, no replay.
Without it, the session goes on from the last run. timeout, stop_on_error
and diagnostics hold for the run; the session's flags, profiles and
libraries are the server's. Requests run one at a time. %quit stops the
server.
"""

import json
import os
import socket
import sys
from pathlib import Path

from .session import JsonOutput, Output
from .sources import inputs_from_cpp, inputs_from_repl, is_cpp


class _Frames:
    """A stream whose writes reach the client as {"out": TEXT} lines."""

    def __init__(self, f):
        self.f = f

    def write(self, text):
        self.f.write((json.dumps({'out': text}, ensure_ascii=False) + '\n').encode())

    def flush(self):
        self.f.flush()


def _inputs(request, session):
    inputs = []
    for item in request.get('files', []):
        path = Path(item['path'])
        if is_cpp(path):
            inputs += inputs_from_cpp(item['text'], path.parent, path.name, session.prelude_names, path=item['name'])
        else:
            inputs += inputs_from_repl(item['text'], path=item['name'])
    if request.get('stdin'):
        inputs += inputs_from_repl(request['stdin'], path='<stdin>')
    return inputs


def _run(session, f):
    """Answer one request. Returns False after %quit."""
    try:
        request = json.loads(f.readline() or b'{}')
    except ValueError:
        return True
    frames = _Frames(f)
    session.out = JsonOutput(frames) if request.get('json') else Output(frames)
    session.echo = not (request.get('quiet') or request.get('json'))
    session.new_run()
    options, diagnostics = session.cfg.options, session.diagnostics
    before = (options.timeout, options.stop_on_error, diagnostics.brief)
    if request.get('timeout') is not None:
        options.timeout = request['timeout']
    if request.get('stop_on_error'):
        options.stop_on_error = True
    if request.get('diagnostics'):
        diagnostics.brief = request['diagnostics'] == 'brief'
    go_on = True
    try:
        if request.get('fresh'):
            session.restore([], 'fresh', note=False)
        session.run_inputs(_inputs(request, session), echo=True)
    except SystemExit:   # %quit
        go_on = False
    finally:
        options.timeout, options.stop_on_error, diagnostics.brief = before
    status = session.exit_status()
    if request.get('json'):
        session.out.finish(status, session)
    f.write((json.dumps({'exit': status}) + '\n').encode())
    f.flush()
    return go_on


def _listening(path):
    """Whether a server answers at path."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        try:
            s.connect(str(path))
            return True
        except OSError:
            return False


def serve(session, address):
    """Run the requests that reach address until %quit, Ctrl-C or SIGTERM."""
    path = Path(address)
    if path.exists():
        if _listening(path):
            sys.stderr.write(f'cpp-repl: a server already listens at {path}\n')
            return 2
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Whoever connects runs code as this user, so the socket is this user's
    # alone (0600) from the moment it exists; bind() creates it under the umask.
    mask = os.umask(0o177)
    try:
        server.bind(str(path))
    except OSError as e:
        server.close()
        sys.stderr.write(f'cpp-repl: cannot listen at {path}: {e.strerror or e}. A socket path holds at most '
                         '107 bytes: a relative path is shorter\n')
        return 2
    finally:
        os.umask(mask)
    server.listen(8)
    sys.stderr.write(f'cpp-repl: serving at {path}; cpp-repl --connect {path} [--fresh] FILE runs FILE here\n')
    sys.stderr.flush()
    try:
        while True:
            conn, _ = server.accept()
            with conn, conn.makefile('rwb') as f:
                try:
                    if not _run(session, f):
                        return 0
                except (BrokenPipeError, ConnectionResetError):
                    pass   # the client went away; the session stays
                finally:
                    session.out = Output(sys.stderr)
    except KeyboardInterrupt:
        return 130
    finally:
        server.close()
        path.unlink(missing_ok=True)


def connect(args, fail):
    """cpp-repl --connect: send the files, or stdin, to the server and write
    what comes back. Returns the run's exit status."""
    request = {'fresh': args.fresh, 'quiet': args.quiet, 'json': args.json, 'files': [], 'timeout': args.timeout,
               'stop_on_error': args.stop_on_error, 'diagnostics': args.diagnostics}
    for f in args.files:
        if not f.is_file():
            return fail(f'no such file: {f}')
        request['files'].append({'name': str(f), 'path': str(f.resolve()), 'text': f.read_text()})
    if not args.files and not sys.stdin.isatty():
        request['stdin'] = sys.stdin.read()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(args.connect)
    except OSError as e:
        s.close()
        return fail(f'no cpp-repl --serve at {args.connect}: {e.strerror or e}')
    with s, s.makefile('rwb') as f:
        f.write((json.dumps(request) + '\n').encode())
        f.flush()
        for line in f:
            frame = json.loads(line)
            if 'out' in frame:
                sys.stdout.write(frame['out'])
                sys.stdout.flush()
            elif 'exit' in frame:
                return frame['exit']
    return fail(f'the server at {args.connect} went away')
