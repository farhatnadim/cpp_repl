"""cpp-repl --mcp: the session served to a Model Context Protocol client on
stdin and stdout, against tests/fake_clang_repl.py, and once against a real
clang-repl."""

import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLANG_REPL, needs_clang_repl
from cpp_repl import __version__

FAKE = Path(__file__).resolve().parent / 'fake_clang_repl.py'
CPP_REPL = Path(__file__).resolve().parent.parent / 'bin' / 'cpp-repl'


class Client:
    """A line-oriented MCP client over the server's pipes."""

    def __init__(self, cwd, *args, clang_repl=str(FAKE), **env):
        env = dict(os.environ, CPP_REPL_CLANG_REPL=clang_repl, NO_COLOR='1', **env)
        for name in ('FAKE_PROMPT', 'FAKE_SPLIT', 'FAKE_MARKER', 'FAKE_ECHO', 'FAKE_HANG_START'):
            if name not in env or env[name] == os.environ.get(name):
                env.pop(name, None)
        self.log = open(Path(cwd) / 'mcp-stderr.log', 'wb')   # read it when a test fails
        self.process = subprocess.Popen([sys.executable, str(CPP_REPL), '--no-config', '--mcp', *args], cwd=cwd,
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        bufsize=0)
        self.ids = 0

    def write(self, line):
        self.process.stdin.write(line.encode() + b'\n')
        self.process.stdin.flush()

    def send(self, message):
        self.write(json.dumps(message))

    def read(self, wait=120):
        """The next message the server writes, within wait seconds."""
        ready, _, _ = select.select([self.process.stdout], [], [], wait)
        assert ready, f'no answer within {wait} s'
        line = self.process.stdout.readline()
        assert line, 'the server closed its stdout'
        return json.loads(line)

    def notify(self, method, params=None):
        self.send({'jsonrpc': '2.0', 'method': method, **({'params': params} if params else {})})

    def request(self, method, params=None, wait=120):
        self.ids += 1
        self.send({'jsonrpc': '2.0', 'id': self.ids, 'method': method, **({'params': params} if params else {})})
        reply = self.read(wait)
        assert reply['jsonrpc'] == '2.0' and reply['id'] == self.ids, reply
        return reply

    def call(self, tool, wait=120, **arguments):
        """Call the tool with arguments; wait is how long to allow its answer."""
        return self.request('tools/call', {'name': tool, 'arguments': arguments}, wait=wait)

    def initialize(self, version='2025-06-18'):
        reply = self.request('initialize', {'protocolVersion': version, 'capabilities': {},
                                            'clientInfo': {'name': 'test', 'version': '0'}})
        self.notify('notifications/initialized')
        return reply['result']

    def close(self):
        """Close the server's stdin and return its exit status."""
        try:
            self.process.stdin.close()
            return self.process.wait(timeout=60)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.log.close()


@pytest.fixture
def client(tmp_path):
    c = Client(tmp_path)
    yield c
    c.close()


def test_handshake_ping_and_tools(client):
    """initialize answers with the client's protocol version, the server's
    name and version, and tool support; tools/list names the four tools,
    run_cpp marked to be loaded without tool search."""
    init = client.initialize()
    assert init['protocolVersion'] == '2025-06-18'
    assert init['serverInfo'] == {'name': 'cpp-repl', 'version': __version__}
    assert init['capabilities'] == {'tools': {}} and 'run_cpp' in init['instructions']
    assert client.request('ping')['result'] == {}
    tools = client.request('tools/list')['result']['tools']
    assert [t['name'] for t in tools] == ['run_cpp', 'run_cpp_file', 'save_cpp', 'reset_session']
    for tool in tools:
        assert tool['inputSchema']['type'] == 'object' and tool['description'] and tool['title']
    assert [t.get('_meta') for t in tools] == [{'anthropic/alwaysLoad': True}, None, None, None]
    assert tools[0]['inputSchema']['required'] == ['code']
    assert set(tools[0]['inputSchema']['properties']) == {'code', 'name', 'timeout', 'fresh'}


def test_a_protocol_version_the_server_does_not_know_gets_its_newest(client):
    assert client.initialize(version='2099-01-01')['protocolVersion'] == '2025-11-25'


def test_run_cpp_values_errors_and_recovery(client):
    """run_cpp's result is the transcript a piped run prints, and nothing
    else: given structuredContent too, Claude Code would show the model that
    instead. A failed input sets isError. The session is restored after a
    crash and after a timeout, for the next call."""
    client.initialize()
    r = client.call('run_cpp', code='say("one");\n42\n')['result']
    assert r == {'content': [{'type': 'text', 'text': 'c++> say("one");\none\nc++> 42\n42\n'}]}

    r = client.call('run_cpp', code='error_here;\n')['result']
    text = r['content'][0]['text']
    assert r['isError'] is True and text.startswith('c++> error_here;\n')
    assert "undeclared identifier 'error_here'" in text and text.endswith('[exit 1]\n')

    r = client.call('run_cpp', code='say("before");\ncrash();\nsay("not run");\n')['result']
    text = r['content'][0]['text']
    assert r['isError'] is True and text.startswith('c++> say("before");\nbefore\nc++> crash();\n')
    assert '[clang-repl crashed (SIGSEGV): restored 3 inputs]' in text   # the journal: one, 42, before
    assert text.endswith('[the program ended: 1 input not run]\n[exit 139]\n')
    r = client.call('run_cpp', code='say("after");\n')['result']
    assert r == {'content': [{'type': 'text', 'text': 'c++> say("after");\nafter\n'}]}

    r = client.call('run_cpp', code='hang();\n', timeout=1)['result']
    text = r['content'][0]['text']
    assert r['isError'] is True and 'ran past 1 s' in text and text.endswith('[exit 124]\n')
    r = client.call('run_cpp', code='say("still here");\n')['result']
    assert r['content'][0]['text'] == 'c++> say("still here");\nstill here\n'


def test_a_cpp_name_runs_a_program(client, tmp_path):
    """A .cpp name runs the code as a program, main()'s body one statement at
    a time, and main()'s declarations stay as the session's."""
    client.initialize()
    program = 'int main()\n{\n    say("in main");\n    int count = 3;\n    return 0;\n}\n'
    r = client.call('run_cpp', code=program, name='prog.cpp')['result']
    text = r['content'][0]['text']
    assert 'isError' not in r and text.startswith('c++> say("in main");\nin main\nc++> int count = 3;\n')
    assert '[exit' not in text
    r = client.call('run_cpp', code='%journal\n')['result']
    assert 'int count = 3;' in r['content'][0]['text']   # main()'s declarations are the session's


def test_run_cpp_file_then_the_programs_variables(client, tmp_path):
    (tmp_path / 'prog.cpp').write_text('#include <vector>\n\nint main()\n{\n    say("hi");\n    int count = 3;\n'
                                       '    return 0;\n}\n')
    client.initialize()
    r = client.call('run_cpp_file', path='prog.cpp')['result']
    text = r['content'][0]['text']
    assert 'isError' not in r and 'c++> say("hi");\nhi\n' in text and '[exit' not in text
    # main()'s declarations are the session's: the journal holds them for the next call.
    r = client.call('run_cpp', code='%journal\n')['result']
    assert 'int count = 3;' in r['content'][0]['text']


def test_save_cpp_reset_and_quit(client, tmp_path):
    client.initialize()
    client.call('run_cpp', code='say("one");\n42\n')
    r = client.call('save_cpp', path='saved.cpp')['result']
    assert r['content'][0]['text'] == f'c++> %save-cpp {tmp_path}/saved.cpp\nsaved 2 inputs to {tmp_path}/saved.cpp\n'
    saved = (tmp_path / 'saved.cpp').read_text()
    assert 'int main()' in saved and 'say("one");' in saved and 'show(42)' in saved
    r = client.call('reset_session')['result']
    assert r['content'][0]['text'] == 'c++> %reset\n[reset: restored 0 inputs]\n'
    r = client.call('run_cpp', code='%journal\n')['result']
    assert r['content'][0]['text'] == 'c++> %journal\n'   # nothing journaled
    client.call('run_cpp', code='say("two");\n')
    r = client.call('run_cpp', code='%journal\n', fresh=True)['result']
    assert r['content'][0]['text'] == 'c++> %journal\n'   # fresh emptied it first
    # The client owns the server's life: %quit would leave it without the tool.
    r = client.call('run_cpp', code='%quit\n')['result']
    assert r['isError'] is True and '%quit has no use here' in r['content'][0]['text']
    assert client.call('run_cpp', code='say("on");\n')['result']['content'][0]['text'] == 'c++> say("on");\non\n'
    assert client.close() == 0


def test_protocol_errors_leave_the_server_up(client):
    client.initialize()
    assert client.request('resources/list')['error']['code'] == -32601
    assert client.request('tools/call', {'name': 'nope', 'arguments': {}})['error']['code'] == -32602
    assert client.request('tools/call', {'name': 'run_cpp', 'arguments': {}})['error']['code'] == -32602
    bad = client.request('tools/call', {'name': 'run_cpp', 'arguments': {'code': 'say("x");', 'timeout': -1}})
    assert bad['error']['code'] == -32602 and 'timeout' in bad['error']['message']
    client.notify('notifications/cancelled', {'requestId': 1})   # ignored
    client.write('not json')
    reply = client.read()
    assert reply == {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
    client.write('')   # an empty line is skipped
    assert client.request('ping')['result'] == {}


def test_long_output_is_cut(client):
    client.initialize()
    code = ''.join(f'say("{i:06d} ' + 'x' * 70 + '");\n' for i in range(700))   # about 55,000 characters of output
    r = client.call('run_cpp', code=code)['result']
    text = r['content'][0]['text']
    assert len(text) < 41_000 and 'characters cut' in text and text.startswith('c++> say("000000')
    assert text.endswith(' ' + 'x' * 70 + '\n')


def test_mcp_refuses_files_and_other_modes(tmp_path):
    (tmp_path / 'p.cpp').write_text('int main() {}\n')
    env = dict(os.environ, CPP_REPL_CLANG_REPL=str(FAKE), NO_COLOR='1')
    for args, said in [(['--mcp', 'p.cpp'], 'runs no files'), (['--mcp', '--json'], 'do not combine'),
                       (['--mcp', '--batch'], 'do not combine'), (['--mcp', '--serve', 's.sock'], 'do not combine'),
                       (['--mcp', '--connect', 's.sock'], 'do not combine')]:
        run = subprocess.run([sys.executable, str(CPP_REPL), '--no-config', *args], cwd=tmp_path, env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        assert run.returncode == 2 and said in run.stderr, (args, run.stderr)


def test_the_client_closing_stdin_ends_the_server(client):
    client.initialize()
    assert client.close() == 0


def test_the_handshake_does_not_wait_for_clang_repl(tmp_path):
    """The session starts at the first tool call: a clang-repl that never
    answers delays no handshake."""
    client = Client(tmp_path, FAKE_HANG_START='1')
    try:
        assert client.initialize()['serverInfo']['name'] == 'cpp-repl'
        assert len(client.request('tools/list', wait=10)['result']['tools']) == 4
    finally:
        assert client.close() == 0


def test_a_session_that_cannot_start_says_why_at_each_call(tmp_path):
    client = Client(tmp_path, '--clang-repl', str(tmp_path / 'no-clang-repl'))
    try:
        client.initialize()
        for _ in range(2):
            r = client.call('run_cpp', code='42\n')['result']
            assert r['isError'] is True and r['content'][0]['text'].startswith('cpp-repl cannot start: ')
            assert 'no-clang-repl' in r['content'][0]['text']
    finally:
        assert client.close() == 0


def test_paths_are_the_sessions(tmp_path):
    """Relative paths resolve against the session's directory, --cwd's, and
    diagnostics name them as given."""
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'prog.cpp').write_text('int main()\n{\n    say("from work");\n    error_here;\n    return 0;\n}\n')
    client = Client(tmp_path, '--cwd', 'work')
    try:
        client.initialize()
        text = client.call('run_cpp_file', path='prog.cpp')['result']['content'][0]['text']
        assert 'from work\n' in text and 'error_here' in text
        r = client.call('run_cpp_file', path='missing.cpp')['result']
        assert r['isError'] is True and r['content'][0]['text'] == 'cannot read missing.cpp: No such file or directory\n'
        client.call('save_cpp', path='saved.cpp')
        assert (work / 'saved.cpp').is_file() and not (tmp_path / 'saved.cpp').exists()
    finally:
        client.close()


@pytest.mark.backend
@needs_clang_repl
def test_a_real_session_prints_a_vector(tmp_path):
    client = Client(tmp_path, clang_repl=CLANG_REPL)
    try:
        client.initialize()
        r = client.call('run_cpp', code='std::vector<int> v{3, 1, 2};\nstd::ranges::sort(v);\nv\n', wait=600)
        r = r['result']
        assert r == {'content': [{'type': 'text', 'text': 'c++> std::vector<int> v{3, 1, 2};\nc++> std::ranges::sort(v);\n'
                                                           'c++> v\n[1, 2, 3]\n'}]}
        r = client.call('run_cpp', code='v.at(7)\n')['result']
        assert r['isError'] is True and '[exit 134]' in r['content'][0]['text']   # an exception nothing caught
        # Diagnostics name the file the code belongs to.
        r = client.call('run_cpp', code='int x = v;\n', name='notes.repl')['result']
        assert r['isError'] is True and 'notes.repl:1:5: error:' in r['content'][0]['text']
        r = client.call('run_cpp', code='v.size()\n')['result']
        assert r['content'][0]['text'] == 'c++> v.size()\n3\n'
    finally:
        client.close()
