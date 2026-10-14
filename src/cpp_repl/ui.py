"""Interactive input: IPython-style through prompt_toolkit when it is
installed, plain readline otherwise; and the names Tab offers."""

import atexit
import hashlib
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from .config import sibling_clang
from .lexer import completeness
from .session import HELP, OUT
from .sources import BlockReader

_IDENTIFIER = re.compile(r'\b[A-Za-z][A-Za-z0-9_]{2,}\b')
_CAPITALISED = re.compile(r'\b[A-Z][A-Za-z0-9]{2,}\b')
_COMMENT_OR_STRING = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"', re.S)


def _header_words(paths, pattern=_IDENTIFIER):
    """The identifiers in some headers, comments and strings left out."""
    words = set()
    for p in paths:
        try:
            words.update(pattern.findall(_COMMENT_OR_STRING.sub(' ', Path(p).read_text(errors='replace'))))
        except OSError:
            pass
    return words


def included_headers(cfg):
    """Every header the session's preludes include, as listed by the clang++
    that comes with its clang-repl; [] when there is none. clang -H leaves
    out what -include brings in, so a source #includes the preludes instead."""
    clang = sibling_clang(cfg.clang_repl) if cfg.clang_repl else None
    if not clang:
        return []
    with tempfile.TemporaryDirectory(dir=cfg.tmp if cfg.tmp.is_dir() else None) as tmp:
        source = Path(tmp) / 'preludes.cpp'
        source.write_text(''.join(f'#include "{h}"\n' for h in cfg.preludes))
        args = [clang, '-fsyntax-only', '-H', *cfg.compiler_flags, str(source)]
        try:
            err = subprocess.run(args, capture_output=True, text=True, timeout=120,
                                 stdin=subprocess.DEVNULL, env=cfg.env).stderr
        except (OSError, subprocess.TimeoutExpired):
            return []
    return list(dict.fromkeys(re.findall(r'^\.+ (.+)$', err, re.M)))


def completion_words(cfg):
    """The names Tab offers for a configuration: the identifiers of every
    header its preludes include, and the class names in its profiles'
    completion directories. Cached in the state directory, by configuration."""
    key = hashlib.sha256(repr((cfg.clang_repl, cfg.compiler_flags,
                               [str(h) for h in cfg.preludes])).encode()).hexdigest()[:16]
    cache = cfg.state / 'completion' / f'{key}.txt'
    try:
        return set(cache.read_text().split())
    except OSError:
        pass
    words = _header_words(included_headers(cfg)) | _header_words(cfg.preludes)
    for d in cfg.completion_dirs:
        words |= _header_words(sorted(Path(d).glob('*.h')), _CAPITALISED)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text('\n'.join(sorted(words)))
    except OSError:
        pass
    return words


class Names:
    """What Tab offers: names from the headers, the session's own
    identifiers, and the commands. The header names are gathered in the
    background from the start, so that the first Tab is quick."""

    def __init__(self, session):
        self.session, self.words = session, None
        self._found = None
        self._thread = threading.Thread(target=self._gather, daemon=True)
        self._thread.start()

    def _gather(self):
        self._found = completion_words(self.session.cfg)

    def matching(self, prefix):
        if self.words is None:
            self._thread.join()
            self.words = set(self._found or ())
            self.words.update(re.findall(r'%[\w-]+', HELP))
            self.words.update(('show', 'keep', 'tree'))
        names = set(self.words)
        for entry in self.session.journal:
            names.update(re.findall(r'[A-Za-z_]\w*', entry.text))
        return sorted(n for n in names if n.startswith(prefix))


# The colours of Pygments' terminal formatter, so that typed and echoed code
# look the same.
_TOOLKIT_STYLE = {
    'prompt': 'ansigreen bold',
    'continuation': 'ansigreen bold',
    'pygments.keyword': 'ansiblue',
    'pygments.keyword.type': 'ansicyan',
    'pygments.operator.word': 'ansimagenta',
    'pygments.name.builtin': 'ansicyan',
    'pygments.name.function': 'ansigreen',
    'pygments.name.class': 'ansigreen underline',
    'pygments.name.namespace': 'ansicyan underline',
    'pygments.literal.string': 'ansiyellow',
    'pygments.literal.number': 'ansiblue',
    'pygments.comment': 'ansibrightblack',
    'pygments.comment.preproc': 'ansicyan',
    'pygments.comment.preprocfile': 'ansiyellow',
}


def _toolkit_reader(session, names, colour):
    """A function returning the next input, read through prompt_toolkit: a
    block is one buffer to move around in, C++ is highlighted as it is typed,
    Tab opens a menu. None when prompt_toolkit is not installed."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.lexers import PygmentsLexer
        from prompt_toolkit.styles import Style
        from pygments.lexers.c_cpp import CppLexer
    except ImportError:
        return None

    class NameCompleter(Completer):
        def get_completions(self, document, complete_event):
            m = re.search(r'(%[\w-]*|[A-Za-z_]\w*)$', document.text_before_cursor)
            if m:
                for name in names.matching(m.group(1)):
                    yield Completion(name, start_position=-len(m.group(1)))

    keys = KeyBindings()

    @keys.add('enter')
    def _(event):
        """Run the block once it is finished; otherwise start a new line."""
        buf = event.current_buffer
        doc = buf.document
        state = completeness(doc.text)
        if doc.is_cursor_at_the_end and (
                state == 'complete' or (state == 'extendable' and not doc.current_line.strip())):
            buf.validate_and_handle()
            return
        line = doc.current_line_before_cursor
        indent = line[:len(line) - len(line.lstrip())]
        if line.rstrip().endswith('{'):
            indent += '  '
        buf.insert_text('\n' + indent)

    @keys.add('escape', 'enter')
    def _(event):
        """Run the block as it stands."""
        event.current_buffer.validate_and_handle()

    @keys.add('}')
    def _(event):
        """Take a closing brace typed on an empty, indented line back a level."""
        buf = event.current_buffer
        before = buf.document.current_line_before_cursor
        if before.endswith('  ') and not before.strip():
            buf.delete_before_cursor(2)
        buf.insert_text('}')

    cfg = session.cfg
    prompt = PromptSession(
        message=[('class:prompt', cfg.prompt)],
        prompt_continuation=lambda width, line_number, wrap_count: [('class:continuation', cfg.more)],
        multiline=True,
        key_bindings=keys,
        lexer=PygmentsLexer(CppLexer) if colour else None,
        completer=NameCompleter(),
        complete_while_typing=False,
        auto_suggest=AutoSuggestFromHistory(),
        history=FileHistory(str(cfg.history.with_name('history.prompt_toolkit'))),
        style=Style.from_dict(_TOOLKIT_STYLE if colour else {}),
        include_default_pygments_style=False,
    )
    return lambda: prompt.prompt().rstrip()


def _readline_reader(session, names):
    """A function returning the next input, read line by line through
    readline (when available) and grouped into blocks by BlockReader."""
    try:
        import readline
    except ImportError:
        readline = None
    if readline:
        history = session.cfg.history
        try:
            readline.read_history_file(history)
        except OSError:
            pass
        readline.set_history_length(5000)

        def save_history():
            try:
                readline.write_history_file(history)
            except OSError:
                pass

        atexit.register(save_history)
        matches = []

        def complete(text, state):
            if state == 0:
                matches[:] = names.matching(text)
            return matches[state] if state < len(matches) else None

        readline.set_completer_delims(' \t\n()[]{};,<>=!&|+*/^~?:."\'')
        readline.set_completer(complete)
        readline.parse_and_bind('tab: complete')

    reader = BlockReader(lambda more: input(session.cfg.more if more else session.cfg.prompt))

    def read():
        try:
            return reader.read()
        except KeyboardInterrupt:
            reader.pending = None
            raise

    return read


def interact(session, colour):
    names = Names(session)
    read = _toolkit_reader(session, names, colour) or _readline_reader(session, names)
    while True:
        try:
            text = read()
        except KeyboardInterrupt:
            OUT.write('\n')
            continue
        except EOFError:
            OUT.end_line()
            return
        if not session.run_input(text):
            return
