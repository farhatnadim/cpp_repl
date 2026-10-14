"""Colour: echoed inputs as C++, blocks of output through a profile's
Pygments lexer (usda for the usd profile), and a library's own errors and
warnings by severity."""

from .backend import plain


class Painter:
    """blocks: [(regex matching a block's first line, Pygments lexer name)]. A
    block runs to the end of an input's output. diagnostics: [(regex whose
    group 1 is a severity, {severity: SGR colour})]; red when the severity has
    no colour of its own."""

    def __init__(self, blocks=(), diagnostics=()):
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound
        self._highlight, self._formatter = highlight, TerminalFormatter()
        self._cpp = get_lexer_by_name('cpp')
        self._blocks = []
        for regex, name in blocks:
            try:
                self._blocks.append((regex, get_lexer_by_name(name)))
            except ClassNotFound:
                pass   # an older Pygments: the block stays plain
        self._diagnostics = list(diagnostics)
        self._block = None   # (lexer, lines) held back until flush()

    @staticmethod
    def create(blocks=(), diagnostics=()):
        """A Painter, or None without Pygments."""
        try:
            return Painter(blocks, diagnostics)
        except ImportError:
            return None

    def feed(self, text):
        """Paint output as it arrives. A block is held back until flush(), so
        the lexer sees all of it."""
        out = []
        for line in text.splitlines(keepends=True):
            if self._block is None:
                for regex, lexer in self._blocks:
                    if regex.match(plain(line)):
                        self._block = (lexer, [])
                        break
            if self._block is not None:
                self._block[1].append(line)
                continue
            for regex, colours in self._diagnostics:
                m = regex.match(line)
                if m:
                    colour = colours.get(m.group(1), '1;31')
                    line = f'\x1b[{colour}m{m.group(0)}\x1b[0m{line[m.end():]}'
                    break
            out.append(line)
        return ''.join(out)

    def flush(self):
        if self._block is None:
            return ''
        (lexer, lines), self._block = self._block, None
        return self._highlight(''.join(lines), lexer, self._formatter)

    def code(self, text):
        return self._highlight(text, self._cpp, self._formatter).rstrip('\n')

    @staticmethod
    def note(text):
        return f'\x1b[33m{text}\x1b[0m'

    @staticmethod
    def prompt(text):
        return f'\x1b[1;32m{text}\x1b[0m'
