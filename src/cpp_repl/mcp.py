"""cpp-repl --mcp: one session served to a Model Context Protocol client on
stdin and stdout, for Claude Code's plugin and any other MCP client.

The transport is MCP's stdio: one JSON-RPC 2.0 message per line, requests
on stdin, answers on stdout, and nothing else on stdout. A tools-only server
needs five methods, initialize, notifications/initialized, ping, tools/list
and tools/call, which are answered here without the mcp package, as
server.py answers its own socket protocol.

The tools run in the session as cpp-repl --connect's requests do:

    run_cpp        code as typed inside main(), or a .cpp file's text
    run_cpp_file   a .cpp, .h or .repl file
    save_cpp       %save-cpp PATH
    reset_session  %reset

The session starts at the first tool call, so that the handshake does not
wait for clang-repl; a machine without one answers each call with the reason.
Calls run one at a time, on the main thread, since recovering from a crash
(Session.restore) sets a signal handler. A call's result is what a piped run
prints, for the model to read: each input after the prompt, its output,
cpp-repl's notes in brackets, then the exit status when it is not 0. It is
text alone: given structuredContent too, Claude Code hands the model that
JSON in place of the text.
"""

import io
import json
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError
from .server import run_request
from .session import Output, SessionError
from .sources import inputs_from_cpp, inputs_from_repl, is_cpp

PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
DEFAULT_TIMEOUT = 60    # seconds an input may take, compiling included, when the call names none
TEXT_LIMIT = 40_000     # characters of transcript in a result: Claude Code files results past 50,000
INPUT_NAME = '<input>'  # the origin of run_cpp's code, as diagnostics name it

INSTRUCTIONS = (
    "cpp-repl runs C++ through clang-repl's JIT, with no build. Use run_cpp before stating what a "
    'standard-library or library call does, to try a snippet and read its compiler errors, or to see how a '
    'value prints. Use run_cpp_file to run a .cpp program, then run_cpp to read its variables. The session '
    'stays between calls. Its configuration (profiles, include directories, libraries, the cpp-repl.toml of '
    'the directory it started in) is fixed for the server\'s life.'
)

_TIMEOUT_SCHEMA = {
    'type': 'number',
    'description': 'Seconds an input may take, compiling included. One that runs past is stopped, the session '
                   'is rebuilt without it, the run goes on, and the result ends with [exit 124]. Default 60.',
}
_FRESH_SCHEMA = {
    'type': 'boolean',
    'description': 'Start from an empty session first: every declaration is gone, and the configuration '
                   '(profiles, flags, libraries) stays.',
}

TOOLS = [
    {
        'name': 'run_cpp',
        'title': 'Run C++ in the session',
        # The one tool loaded up front: the others wait behind Claude Code's tool search, by name.
        'description': (
            'Run C++ in cpp-repl, the REPL on clang-repl, without a build, and read what it prints. The code is '
            'what you would type inside main(): statements, declarations and #include lines. A bare expression, '
            'with no semicolon, prints its value, so there is no need to write output code: '
            'std::views::iota(1, 13) | std::views::stride(4) prints [1, 5, 9], a std::map prints '
            '{"apples": 3, "pears": 5}, a vector of pairs [(1, 0.5), (2, 1.5)], an empty optional nullopt, and '
            'any type with an operator<< prints through it. Lines group into inputs as at the prompt, so one call '
            'may carry several. Variables are '
            "the session's globals: they keep their values for the next call, and a name declared again replaces "
            'the old declaration, with a note. The result is the transcript: each input after the prompt '
            '("c++> "), its output under it, cpp-repl\'s notes in brackets, and "[exit N]" when the run\'s status '
            'is not 0: 1 for an input that did not compile or link, 124 for one past the timeout, 134 for an '
            'uncaught exception, 139 for a crash, after which the session is rebuilt. A compile error names '
            '<input>:LINE:COL, LINE counting the lines of this call\'s code. A statement runs in about a second; the first call of a '
            'configuration takes a few seconds more. %help lists the %-commands, which run here too. With a '
            'name ending in .cpp, the code runs as a program: its file-scope items, then main()\'s body one '
            "statement at a time; a return from main() ends that run, and the globals stay readable."
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'code': {'type': 'string', 'description': 'The C++ to run: one or more inputs, as typed inside '
                                                          'main(), or a whole program when name ends in .cpp.'},
                'name': {'type': 'string', 'description': 'The file the code belongs to: quoted #include lines '
                                                          'resolve beside it, diagnostics name it, and a .cpp name '
                                                          "runs the code as a program. Default: <input>, run as "
                                                          'typed at the prompt.'},
                'timeout': _TIMEOUT_SCHEMA,
                'fresh': _FRESH_SCHEMA,
            },
            'required': ['code'],
        },
        '_meta': {'anthropic/alwaysLoad': True},
    },
    {
        'name': 'run_cpp_file',
        'title': 'Run a C++ file in the session',
        'description': (
            'Run a C++ source or a .repl file in the session, as cpp-repl --batch would: a .cpp or .h file\'s '
            "file-scope items, then main()'s body one statement at a time, with quoted includes resolving beside "
            "the file; a .repl file's inputs as they are. Nothing is built. A return from main() ends that run, "
            "and main()'s declarations stay as the session's globals, so run_cpp reads a program's variables "
            "after it ran. A relative path is relative to the session's directory."
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'The .cpp, .h or .repl file to run.'},
                'timeout': _TIMEOUT_SCHEMA,
                'fresh': _FRESH_SCHEMA,
            },
            'required': ['path'],
        },
    },
    {
        'name': 'save_cpp',
        'title': 'Save the session as a C++ program',
        'description': (
            'Write the session so far as a C++ program at path: includes and definitions at file scope, the rest '
            "in main(), bare expressions as show(...) calls. Its first line says how it builds, with the session's "
            "own options: clang++ $(cpp-repl --cflags) FILE $(cpp-repl --libs). A relative path is relative to "
            "the session's directory."
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'path': {'type': 'string', 'description': 'The .cpp file to write.'}},
            'required': ['path'],
        },
    },
    {
        'name': 'reset_session',
        'title': 'Empty the session',
        'description': 'Empty the session: every declaration is gone, as at a fresh start, and the configuration '
                       '(profiles, flags, libraries) stays.',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
]


class _Invalid(Exception):
    """A request whose params are wrong: a JSON-RPC -32602 error."""


def _cut(text, limit):
    """text, or its head and tail when it is longer than limit, with a line
    saying how much was cut."""
    if len(text) <= limit:
        return text
    head, tail = limit * 3 // 4, limit // 4
    return f'{text[:head]}\n[... {len(text) - head - tail} characters cut ...]\n{text[-tail:]}'


class _Unavailable(Exception):
    """The session cannot start: the call's answer says why, and the next
    call tries again."""


def _text(text, error=False):
    """A tool's result: text for the model, and isError when the run failed."""
    result = {'content': [{'type': 'text', 'text': text}]}
    if error:
        result['isError'] = True
    return result


class _Server:
    """The session, and the answers to one client's messages. The session
    starts at the first tool call, so that the handshake does not wait for
    clang-repl, and a machine without one still answers, with the reason."""

    def __init__(self, start, stdout):
        self.start, self.stdout = start, stdout
        self.session = None
        self.new = False   # whether the session started in this call: fresh then has nothing to empty
        self.quiet = Output(sys.stderr)   # where the session writes between calls
        self.stop = None   # the status to exit with, once a signal ended the session
        self.tools = {'run_cpp': self.run_cpp, 'run_cpp_file': self.run_cpp_file, 'save_cpp': self.save_cpp,
                      'reset_session': self.reset_session}

    # -- the protocol -----------------------------------------------------

    def send(self, message):
        self.stdout.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
        self.stdout.flush()

    def error(self, id, code, message):
        self.send({'jsonrpc': '2.0', 'id': id, 'error': {'code': code, 'message': message}})

    def handle(self, message):
        if isinstance(message, list):   # a batch, which MCP dropped in 2025-06-18: answered one by one
            for one in message:
                self.handle(one)
            return
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            if isinstance(message, dict) and message.get('id') is not None:
                self.error(message['id'], -32600, 'invalid request')
            return
        method, id = message.get('method'), message.get('id')
        if method is None or id is None:
            return   # a response to a request of ours, of which there are none, or a notification
        params = message.get('params') or {}
        try:
            result = self.dispatch(method, params if isinstance(params, dict) else {})
        except _Invalid as e:
            self.error(id, -32602, str(e))
            return
        if result is None:
            self.error(id, -32601, f'method not found: {method}')
            return
        self.send({'jsonrpc': '2.0', 'id': id, 'result': result})

    def dispatch(self, method, params):
        if method == 'initialize':
            asked = params.get('protocolVersion')
            return {'protocolVersion': asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1],
                    'capabilities': {'tools': {}}, 'serverInfo': {'name': 'cpp-repl', 'version': __version__},
                    'instructions': INSTRUCTIONS}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'tools/call':
            name, arguments = params.get('name'), params.get('arguments') or {}
            if not isinstance(arguments, dict):
                raise _Invalid('arguments must be an object')
            tool = self.tools.get(name)
            if tool is None:
                raise _Invalid(f'unknown tool: {name}')
            try:
                return tool(arguments)
            except _Unavailable as e:
                return _text(f'{e}\n', error=True)
        return None

    # -- the tools --------------------------------------------------------

    def started(self):
        """The session, started at the first call."""
        if self.session is None:
            try:
                self.session = self.start()
            except (ConfigError, SessionError) as e:
                raise _Unavailable(f'cpp-repl cannot start: {e}') from None
            self.session.out = self.quiet
            self.new = True
        return self.session

    def where(self, path):
        """path, relative to the session's directory unless it is absolute."""
        path = Path(path).expanduser()
        return path if path.is_absolute() else self.session.cwd / path

    def inputs(self, text, name, path=None):
        """text's inputs, named name in diagnostics: a C++ source's when path
        is a C++ file's, whose quoted includes resolve beside it, else as
        typed at the prompt."""
        if path is not None and is_cpp(path):
            return inputs_from_cpp(text, path.parent, path.name, self.session.prelude_names, path=name)
        return inputs_from_repl(text, path=name)

    def run_cpp(self, arguments):
        code, name = arguments.get('code'), arguments.get('name') or INPUT_NAME
        if not isinstance(code, str):
            raise _Invalid("run_cpp needs 'code', a string")
        if not isinstance(name, str):
            raise _Invalid("'name' must be a string")
        self.started()
        return self.run(self.inputs(code, name, None if name == INPUT_NAME else self.where(name)), arguments)

    def run_cpp_file(self, arguments):
        path = arguments.get('path')
        if not isinstance(path, str) or not path:
            raise _Invalid("run_cpp_file needs 'path', a string")
        self.started()
        full = self.where(path)
        try:
            text = full.read_text()
        except (OSError, UnicodeDecodeError) as e:
            return _text(f'cannot read {path}: {getattr(e, "strerror", None) or e}\n', error=True)
        return self.run(self.inputs(text, path, full), arguments)

    def save_cpp(self, arguments):
        path = arguments.get('path')
        if not isinstance(path, str) or not path:
            raise _Invalid("save_cpp needs 'path', a string")
        self.started()
        return self.run([f'%save-cpp {self.where(path)}'], {})

    def reset_session(self, arguments):
        self.started()
        return self.run(['%reset'], {})

    def run(self, inputs, arguments):
        """Run inputs in the session, as one --connect request would, and
        answer with what the run printed: the inputs echoed, their output
        and the session's notes, as cpp-repl prints them for input piped in,
        then the exit status when it is not 0. isError marks a run in which
        an input failed, ran past the timeout, or was interrupted."""
        timeout = arguments.get('timeout', DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise _Invalid("'timeout' must be a positive number of seconds")
        if any(str(text).split(None, 1)[:1] == ['%quit'] for text in inputs):
            # The client owns the server's life: a %quit would leave it without the tool.
            return _text('%quit has no use here: the session lasts as long as the MCP client keeps it. '
                         'reset_session empties it.\n', error=True)
        request = {'timeout': timeout, 'fresh': bool(arguments.get('fresh')) and not self.new}
        self.new = False
        buffer = io.StringIO()
        session = self.session
        try:
            self.stop = run_request(session, request, inputs, Output(buffer))
            status = session.exit_status()
            failed = bool(session.failures or session.timeouts or session.interrupted)
        finally:
            session.out = self.quiet
        text = buffer.getvalue()
        if status:
            text += ('' if not text or text.endswith('\n') else '\n') + f'[exit {status}]\n'
        return _text(_cut(text, TEXT_LIMIT), error=failed)


def serve(start, stdin=None, stdout=None):
    """Answer the MCP client on stdin until it closes stdin or a signal
    arrives. start() makes the session, at the first tool call; it raises
    ConfigError or SessionError when the session cannot start. Returns the
    exit status."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    server = _Server(start, stdout)
    sys.stderr.write('cpp-repl: serving an MCP client on stdin and stdout; the session starts at its first call\n')
    sys.stderr.flush()
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode('utf-8', 'replace'))
            except ValueError:
                server.error(None, -32700, 'parse error')
                continue
            server.handle(message)
            if server.stop is not None:
                return server.stop
    except KeyboardInterrupt:
        return 130
    return 0
