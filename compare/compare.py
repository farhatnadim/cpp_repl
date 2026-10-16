#!/usr/bin/env python3
"""Raw clang-repl against cpp-repl, measured: the same session typed into
both, their start-up, and what an input costs in each.

    compare/compare.py run > compare/results/compare-VERSION.json
    compare/compare.py render compare/results/compare-*.json

`run` drives the clang-repl cpp-repl itself would run (or --clang-repl PATH)
twice: raw, over a pipe, one line at a time as a person typing does; and
through cpp-repl's own session, as the prompt runs it. It writes one JSON
document to stdout. `render` reads the documents of every clang-repl and
rewrites the tables of compare/README.md between their markers, the newest
23.x release in the main column and the other versions where they differ.

The raw side borrows cpp-repl's end-of-output marker: after each input, a
`%lib` of a path that cannot exist, whose error line says where the input's
output ends. A raw clang-repl that hangs on an input is killed after the
deadline, one that crashes is gone; either way a fresh process replays the
inputs that had worked, so that the rows after it still have a raw answer,
marked as a fresh process. The cpp-repl side runs each input through one
Session, batch off, as the prompt does: a crash or an exception there is
survived, as the document is meant to show.
"""

import argparse
import io
import json
import os
import re
import resource
import select
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parent
sys.path.insert(0, str(CHECKOUT / 'src'))
sys.path.insert(0, str(CHECKOUT / 'tests' / 'compat'))

from cpp_repl.config import Config, Options, default_std, find_clang_repl, clang_repl_version  # noqa: E402
from cpp_repl.lexer import transform  # noqa: E402
from cpp_repl.session import JsonOutput, Session  # noqa: E402
from cpp_repl.sources import inputs_from_repl  # noqa: E402
from harness import NO_DUMPS  # noqa: E402

DEADLINE = 20          # seconds a raw clang-repl gets to answer one input
# Raw clang-repl gets no other first input: a setvbuf(stdout, nullptr, _IONBF, 0),
# as cpp-repl's prologue sends, made raw clang-repl lose every std::cout write
# that no std::endl flushed (measured on 18, 23 and 24). Over a pipe its stdout
# stays buffered, so the session's own std::cout lines end in std::endl.
MARK_PATH = '/nonexistent/cpp-repl/'
SESSION = HERE / 'session.repl'
RESULTS = HERE / 'results'
DOCUMENT = HERE / 'README.md'
CPP_REPL = CHECKOUT / 'bin' / 'cpp-repl'
RUNS = 3               # start-up measurements per command
INPUTS = 20            # per-input latency measurements per kind


def scratch_env(scratch):
    env = dict(os.environ, HOME=str(scratch / 'home'), TMPDIR=str(scratch / 'tmp'), XDG_STATE_HOME=str(scratch / 'state'))
    env.pop('LD_PRELOAD', None)
    for d in ('home', 'tmp', 'state'):
        (scratch / d).mkdir(exist_ok=True)
    return env


# -- raw clang-repl -----------------------------------------------------------

class Raw:
    """One raw clang-repl over a pipe, with the marker protocol."""

    def __init__(self, clang_repl, std, scratch):
        self.proc = subprocess.Popen([clang_repl, f'--Xcc=-std={std}'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=scratch_env(scratch), cwd=scratch,
                                     start_new_session=True)
        self.buffer = b''
        self.seq = 0
        output, state, _ = self.send([NO_DUMPS])
        self.startup_output = output

    def send(self, lines, deadline=DEADLINE):
        """(output, state, seconds) of lines sent one by one, then the marker.
        state is 'ok', 'hung' (killed after the deadline), 'crashed (SIG...)'
        or 'exited (code N)'."""
        self.seq += 1
        token = f'{MARK_PATH}SYNC_{self.seq}'
        start = time.monotonic()
        try:
            for line in lines + [f'%lib {token}']:
                self.proc.stdin.write(line.encode() + b'\n')
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return self._drain(start, token)
        until = start + deadline
        while True:
            idx = self.buffer.find(token.encode())
            if idx >= 0:
                newline = self.buffer.find(b'\n', idx)
                if newline >= 0:
                    line_start = self.buffer.rfind(b'\n', 0, idx) + 1
                    out = self.buffer[:line_start]
                    self.buffer = self.buffer[newline + 1:]
                    return out.decode('utf-8', 'replace'), 'ok', time.monotonic() - start
            left = until - time.monotonic()
            if left <= 0 or not select.select([self.proc.stdout], [], [], left)[0]:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
                return self.buffer.decode('utf-8', 'replace'), 'hung', time.monotonic() - start
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                return self._drain(start, token)
            self.buffer += chunk

    def _drain(self, start, token):
        rc = self.proc.wait()
        out = self.buffer.decode('utf-8', 'replace').replace(token, '')
        self.buffer = b''
        if rc < 0:
            try:
                state = f'crashed ({signal.Signals(-rc).name})'
            except ValueError:
                state = f'crashed (signal {-rc})'
        else:
            state = f'exited (code {rc})'
        return out, state, time.monotonic() - start

    def alive(self):
        return self.proc.poll() is None

    def quit(self):
        """(exit status after %quit, or the one it already has; what it wrote
        on its way out). A pipe-fed clang-repl prints the values of bare
        expressions through a buffer of its own, which flushes at exit."""
        after = self.buffer.decode('utf-8', 'replace')
        self.buffer = b''
        if self.alive():
            try:
                out, _ = self.proc.communicate(b'%quit\n', timeout=DEADLINE)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                out, _ = self.proc.communicate()
            except (BrokenPipeError, OSError):
                out = b''
                self.proc.wait()
            after += (out or b'').decode('utf-8', 'replace')
        return self.proc.returncode, after


def run_raw_session(clang_repl, std, inputs, scratch):
    """Each input's raw answer. After a hang or a crash, a fresh process
    replays the inputs that worked; the next row says so."""
    raw = Raw(clang_repl, std, scratch)
    rows, worked, fresh = [], [], False
    for text in inputs:
        if text.strip() == '%quit':
            rc, after = raw.quit()
            rows.append({'text': text, 'output': after, 'state': f'exited (code {rc})', 'seconds': 0, 'fresh': fresh,
                         'at_exit': True})
            break
        output, state, seconds = raw.send(text.split('\n'))
        rows.append({'text': text, 'output': output, 'state': state, 'seconds': round(seconds, 3), 'fresh': fresh})
        fresh = False
        if state == 'ok':
            worked.append(text)
        else:
            raw = Raw(clang_repl, std, scratch)
            for earlier in worked:
                raw.send(earlier.split('\n'))
            fresh = True
    if raw.alive():
        raw.quit()
    return rows


def raw_latency(clang_repl, std, scratch):
    """Seconds per input, raw, for three kinds of input."""
    raw = Raw(clang_repl, std, scratch)
    raw.send(['#include <iostream>'])
    kinds = {'declaration': [f'int d{n} = {n};' for n in range(INPUTS)],
             'expression': [f'd{n} + 1' for n in range(INPUTS)],
             'statement': [f"std::cout << d{n} << '\\n';" for n in range(INPUTS)]}
    out = {}
    for kind, lines in kinds.items():
        times = []
        for line in lines:
            _, state, seconds = raw.send([line])
            if state != 'ok':
                break
            times.append(seconds)
        out[kind] = times
    raw.quit()
    return out


# -- cpp-repl -----------------------------------------------------------------

def make_session(clang_repl, scratch):
    stream = io.StringIO()
    cfg = Config(Options(cwd=scratch, no_config=True, clang_repl=clang_repl, diagnostics='full'))
    session = Session(cfg, scratch, batch=False, out=JsonOutput(stream))
    return session, stream, cfg


def run_cpp_repl_session(clang_repl, inputs, scratch):
    """Each input's answer from one cpp-repl session, as the prompt runs it:
    the --json record, with the lines written between inputs folded in."""
    session, stream, cfg = make_session(clang_repl, scratch)
    rows, read = [], 0
    try:
        for text in inputs:
            start = time.monotonic()
            quit_status = None
            try:
                session.run_inputs([text], echo=False)
            except SystemExit as e:   # %quit
                quit_status = e.code if isinstance(e.code, int) else 0
            seconds = time.monotonic() - start
            new = stream.getvalue()[read:]
            read = len(stream.getvalue())
            row = {'text': text, 'output': '', 'notes': [], 'status': 'ok', 'seconds': round(seconds, 3)}
            for line in new.splitlines():
                record = json.loads(line)
                if 'input' in record:
                    row['output'] += record.get('output', '')
                    row['notes'] += record.get('notes', [])
                    row['status'] = record['status']
                    for key in ('run_seconds', 'returned'):
                        if key in record:
                            row[key] = record[key]
                elif 'note' in record:
                    row['notes'].append(record['note'])
                elif 'output' in record:
                    row['output'] += record['output']
            if quit_status is not None:
                row['status'] = f'exited (code {quit_status})'
            rows.append(row)
            if quit_status is not None:
                break
    finally:
        session.close()
    return rows, cfg


def cpp_repl_latency(clang_repl, scratch):
    session, stream, _ = make_session(clang_repl, scratch)
    kinds = {'declaration': [f'int d{n} = {n};' for n in range(INPUTS)],
             'expression': [f'd{n} + 1' for n in range(INPUTS)],
             'statement': [f"std::cout << d{n} << '\\n';" for n in range(INPUTS)]}
    out = {}
    try:
        for kind, lines in kinds.items():
            times, runs = [], []
            for line in lines:
                start = time.monotonic()
                session.run_input(line)
                times.append(time.monotonic() - start)
                if session.run_seconds is not None:
                    runs.append(session.run_seconds)
            out[kind] = times
            out[kind + '_run'] = runs
    finally:
        session.close()
    return out


# -- start-up -----------------------------------------------------------------

def timed(command, env, cwd, stdin=b'', runs=RUNS, stdins=None):
    """Wall seconds of each of runs runs of command, stdin fed to it (or
    stdins, one per run: a served session takes a new name each time, since
    a name declared again replays the session)."""
    times = []
    for i in range(runs):
        start = time.monotonic()
        subprocess.run(command, input=stdins[i] if stdins else stdin, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=env, cwd=cwd, timeout=600)
        times.append(round(time.monotonic() - start, 3))
    return times


def startup(clang_repl, std, inputs, scratch):
    env = scratch_env(scratch)
    empty = scratch / 'empty.repl'
    empty.write_text('')
    includes = [t for t in inputs if t.startswith('#include')]

    def cpp_repl(*extra):
        return [sys.executable, str(CPP_REPL), '--no-config', '--clang-repl', clang_repl, *extra, '--batch', '-q',
                str(empty)]

    def precompiled(*extra):
        """The timing of a start whose preludes are precompiled: the first
        start of a configuration builds the PCH, so one run goes untimed."""
        subprocess.run(cpp_repl(*extra), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=env, cwd=scratch, timeout=600)
        return timed(cpp_repl(*extra), env, scratch)

    out = {'raw': timed([clang_repl, f'--Xcc=-std={std}'], env, scratch, b'%quit\n'),
           'raw_with_includes': timed([clang_repl, f'--Xcc=-std={std}'], env, scratch,
                                      ('\n'.join(includes) + '\n%quit\n').encode()),
           'cpp_repl': precompiled(),
           'cpp_repl_no_pch': timed(cpp_repl('--no-pch'), env, scratch),
           'cpp_repl_no_std': precompiled('--no-std')}
    if os.environ.get('USD_ROOT'):
        try:
            out['cpp_repl_usd'] = precompiled('-p', 'usd')
            out['cpp_repl_usd_no_pch'] = timed(cpp_repl('-p', 'usd', '--no-pch'), env, scratch)
        except subprocess.TimeoutExpired:
            pass
    # A served session: a warm run, and a fresh one in the spare.
    sock = 'repl.sock'
    server = subprocess.Popen([sys.executable, str(CPP_REPL), '--no-config', '--clang-repl', clang_repl, '--serve', sock],
                              env=env, cwd=scratch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            if (scratch / sock).exists():
                break
            time.sleep(0.05)
        time.sleep(3)   # the spare warms
        client = [sys.executable, str(CPP_REPL), '--connect', sock, '-q']
        out['connect'] = timed(client, env, scratch, stdins=[f'int warm{i} = {i};\n'.encode() for i in range(RUNS)])
        out['connect_fresh'] = timed(client + ['--fresh'], env, scratch, b'int fresh = 1;\n', runs=1)
        subprocess.run(client, input=b'%quit\n', env=env, cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
        server.wait(timeout=60)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
    if shutil.which('docker') and not Path('/.dockerenv').exists():
        image = os.environ.get('CPP_REPL_IMAGE', 'cpp-repl:llvm23')
        if subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode == 0:
            out['docker_container'] = timed(['docker', 'run', '--rm', image, 'true'], env, scratch)
    return out


def checkout_commit():
    """The checkout's commit, read from .git itself: the images have no git."""
    try:
        head = (CHECKOUT / '.git' / 'HEAD').read_text().strip()
        if head.startswith('ref: '):
            head = (CHECKOUT / '.git' / head[5:]).read_text().strip()
        return head[:7]
    except OSError:
        return '?'


def machine():
    model = ''
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                model = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    return {'cpu': model, 'cpus': os.cpu_count(), 'load': [round(x, 2) for x in os.getloadavg()],
            'in_container': Path('/.dockerenv').exists()}


def run(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1]))
    clang_repl = find_clang_repl(args.clang_repl)
    version, text = clang_repl_version(clang_repl)
    std = default_std(version).removeprefix('-std=')
    inputs = [str(t) for t in inputs_from_repl(SESSION.read_text())]
    commit = checkout_commit()
    with tempfile.TemporaryDirectory(prefix='cpp-repl-compare-') as tmp:
        scratch = Path(tmp)
        print(f'clang-repl {text} at {clang_repl}, {std}', file=sys.stderr)
        print('raw session', file=sys.stderr)
        raw_rows = run_raw_session(clang_repl, std, inputs, scratch)
        print('cpp-repl session', file=sys.stderr)
        repl_rows, cfg = run_cpp_repl_session(clang_repl, inputs, scratch)
        print('latency', file=sys.stderr)
        latency = {'raw': raw_latency(clang_repl, std, scratch), 'cpp_repl': cpp_repl_latency(clang_repl, scratch)}
        print('start-up', file=sys.stderr)
        starts = startup(clang_repl, std, inputs, scratch)
    document = {'date': time.strftime('%Y-%m-%d', time.gmtime()), 'machine': machine(),
                'raw': {'clang_repl': clang_repl, 'version': text, 'std': std, 'deadline': DEADLINE},
                'cpp_repl': {'commit': commit, 'argv': cfg.argv},
                'inputs': [{'n': i + 1, 'text': text, 'raw': raw, 'cpp_repl': repl}
                           for i, (text, raw, repl) in enumerate(zip(inputs, raw_rows, repl_rows))],
                'startup': starts, 'latency': latency}
    json.dump(document, sys.stdout, indent=1)
    print()
    return 0


# -- rendering ----------------------------------------------------------------

def version_key(doc):
    return tuple(int(p) if p.isdigit() else 0 for p in doc['raw']['version'].split('.'))


def normalise(text):
    text = re.sub(r'^In file included from <<< inputs >>>:\d+:\n', '', text, flags=re.M)
    text = re.sub(r'0x[0-9a-fA-F]+', '0x…', text)
    text = re.sub(r'input_line_\d+', 'input_line_N', text)
    text = re.sub(r'cpp_repl_\d+', 'cpp_repl_N', text)
    text = re.sub(r'/tmp/cpp-repl-compare-[^/\s]+/?', '', text)
    text = re.sub(r'\(lambda at [^)]*\)', '(lambda)', text)
    return text


def cell(text):
    """Markdown table cell text: one line, pipes escaped, long text cut."""
    text = ' ⏎ '.join(line for line in text.strip().split('\n') if line.strip())
    text = text.replace('|', '\\|')
    return text if len(text) <= 160 else text[:157] + '…'


def raw_cell(row):
    out = normalise(row['output']).strip()
    lines = [line for line in out.split('\n') if line.strip()]
    if row.get('at_exit'):
        return row['state'] + ('; on its way out, the values it had buffered: ' + cell(out) if lines else '')
    if row['state'] == 'hung':
        verdict = f'hangs (killed after {DEADLINE} s); session lost'
        if lines:
            verdict += f': {len(lines)} lines of errors first'
    elif row['state'].startswith('crashed'):
        verdict = f"{row['state']}; session lost"
        if lines:
            verdict += ': ' + cell(lines[0])[:100]
    elif row['state'].startswith('exited'):
        verdict = row['state'] + (': ' + cell(lines[-1]) if lines else '')
    elif not lines:
        bare = not row['text'].startswith(('#', '%')) and transform(row['text']).kind == 'expr'
        verdict = 'nothing yet: the value is buffered until exit (last row)' if bare else 'nothing'
    else:
        error = next((line for line in lines if 'error:' in line), None)
        verdict = cell(error if error else out)
    return ('(fresh process, earlier inputs replayed) ' if row['fresh'] else '') + verdict


def repl_cell(row):
    out = normalise(row['output']).strip()
    parts = [cell(out)] if out else []
    parts += [cell(f'[{note}]') for note in row.get('notes', [])]
    if row['status'] != 'ok':
        parts.append(f'status {row["status"]}')
    return ' '.join(parts) or 'nothing'


def session_table(docs, main):
    others = [d for d in docs if d is not main]
    heading = ['#', 'Input', f"raw clang-repl {main['raw']['version']}", 'cpp-repl']
    if others:
        heading.append(' / '.join(d['raw']['version'] for d in others) + ', where different')
    lines = ['| ' + ' | '.join(heading) + ' |', '|' + ' --- |' * len(heading)]
    for i, row in enumerate(main['inputs']):
        text = cell(row['text']).replace('`', '\\`')
        cells = [str(row['n']), f'`{text}`', raw_cell(row['raw']), repl_cell(row['cpp_repl'])]
        if others:
            diffs = []
            for d in others:
                other = d['inputs'][i] if i < len(d['inputs']) else None
                if other is None:
                    continue
                for side, key, render in (('raw', 'raw', raw_cell), ('cpp-repl', 'cpp_repl', repl_cell)):
                    mine, theirs = (render({**row[key], 'fresh': False}), render({**other[key], 'fresh': False}))
                    if mine != theirs:
                        diffs.append(f"{d['raw']['version']} {side}: {theirs}")
            cells.append('; '.join(diffs) if diffs else 'same')
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def seconds(values):
    """The median of values, in s or ms as its size wants."""
    if not values:
        return '—'
    median = statistics.median(values)
    return f'{median:.2f} s' if median >= 1 else f'{median * 1000:.1f} ms' if median >= 0.01 else f'{median * 1000:.2f} ms'


def startup_table(docs):
    rows = [('raw clang-repl, `%quit` at once', 'raw'),
            ("raw clang-repl, the session's five `#include`s first", 'raw_with_includes'),
            ('cpp-repl, an empty `--batch` run, the preludes precompiled', 'cpp_repl'),
            ('cpp-repl `--no-pch`: the preludes parsed', 'cpp_repl_no_pch'),
            ('cpp-repl `--no-std` (the core prelude alone, precompiled)', 'cpp_repl_no_std'),
            ('cpp-repl `-p usd`, precompiled', 'cpp_repl_usd'),
            ('cpp-repl `-p usd --no-pch`', 'cpp_repl_usd_no_pch'),
            ('a run in a served session (`--connect`)', 'connect'),
            ('a `--connect --fresh` run, the spare ready', 'connect_fresh'),
            ('the container alone (`docker run IMAGE true`)', 'docker_container')]
    heading = [f'Start-up, median of {RUNS} runs'] + [f"clang-repl {d['raw']['version']}" for d in docs]
    lines = ['| ' + ' | '.join(heading) + ' |', '|' + ' --- |' * len(heading)]
    for title, key in rows:
        values = [seconds(d['startup'].get(key, [])) for d in docs]
        if all(v == '—' for v in values):
            continue
        lines.append('| ' + ' | '.join([title] + values) + ' |')
    return '\n'.join(lines)


def latency_table(docs):
    heading = [f'One input, median of {INPUTS}'] + [f"clang-repl {d['raw']['version']}" for d in docs]
    lines = ['| ' + ' | '.join(heading) + ' |', '|' + ' --- |' * len(heading)]
    for kind, title in (('declaration', 'a declaration, `int dN = N;`'), ('expression', 'a bare expression, `dN + 1`'),
                        ('statement', "a statement, `std::cout << dN << '\\n';`")):
        for side, label in (('raw', 'raw clang-repl'), ('cpp_repl', 'cpp-repl')):
            values = [seconds(d['latency'][side].get(kind, [])) for d in docs]
            lines.append('| ' + ' | '.join([f'{title}, {label}'] + values) + ' |')
        runs = [d['latency']['cpp_repl'].get(kind + '_run', []) for d in docs]
        if any(runs):
            values = [f'{statistics.median(r) * 1e6:.1f} µs' if r else '—' for r in runs]
            lines.append('| ' + ' | '.join([f'{title}, of which the code ran (cpp-repl)'] + values) + ' |')
    return '\n'.join(lines)


def versions_line(docs, main):
    parts = []
    for d in docs:
        m = d['machine']
        where = 'in a container' if m['in_container'] else 'on the host'
        parts.append(f"clang-repl {d['raw']['version']} ({where}, load {m['load'][0]} on {m['cpus']} CPUs, {d['date']})")
    return (f"cpp-repl at commit {main['cpp_repl']['commit']}, against " + '; '.join(parts)
            + f". The main column is clang-repl {main['raw']['version']}; raw clang-repl ran with `--Xcc=-std={main['raw']['std']}`, cpp-repl's default for it.")


def render(args):
    docs = sorted((json.loads(Path(p).read_text()) for p in args.results), key=version_key)
    if not docs:
        sys.exit('render needs the JSON of at least one run')
    main = next((d for d in reversed(docs) if version_key(d)[0] == 23), docs[-1])
    blocks = {'versions': versions_line(docs, main), 'session': session_table(docs, main),
              'startup': startup_table(docs), 'latency': latency_table(docs)}
    text = DOCUMENT.read_text()
    for name, body in blocks.items():
        pattern = re.compile(rf'(<!-- generated: {name} -->\n)(?:.*?\n)?(<!-- end: {name} -->)', re.S)
        if not pattern.search(text):
            sys.exit(f'{DOCUMENT} has no markers for {name}')
        text = pattern.sub(lambda m: m.group(1) + body + '\n' + m.group(2), text)
    DOCUMENT.write_text(text)
    print(f'{DOCUMENT} rendered from {len(docs)} run(s); main column clang-repl {main["raw"]["version"]}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run', help='measure both sides with one clang-repl; JSON to stdout')
    r.add_argument('--clang-repl', help='the clang-repl to use (default: the one cpp-repl would run)')
    r.set_defaults(func=run)
    d = sub.add_parser('render', help='rewrite the tables of compare/README.md from the JSON of each run')
    d.add_argument('results', nargs='+')
    d.set_defaults(func=render)
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
