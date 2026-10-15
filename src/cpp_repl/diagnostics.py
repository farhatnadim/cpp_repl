"""What cpp-repl shows of clang's diagnostics.

Full shows them as clang prints them, less what cpp-repl's own plumbing adds:
the #include of the file an input went through, and the second copy of an
error that clang-repl reports twice for a statement at top level.

Brief is for whoever fixes the code and runs it again, a coding agent above
all. It shows each input's first error with its notes (BRIEF_FLAGS, given to
clang, cut the rest at the source), and source lines and carets only under a
diagnostic in the code being run. It leaves out include chains, writes paths
without '..', and names at most three of the symbols an input failed to link
against, the code's own first, demangled when c++filt is installed.
"""

import os
import re
import shutil
import subprocess

from .backend import plain

# Given to clang before the session's own flags, which override them.
BRIEF_FLAGS = ('-ferror-limit=1', '-ftemplate-backtrace-limit=2', '-fconstexpr-backtrace-limit=2',
               '-fmacro-backtrace-limit=2', '-fshow-overloads=best')
MOST_SYMBOLS = 3

_HEADER = re.compile(r'(?P<file>[^\s:][^:\n]*):\d+:\d+: (?P<severity>fatal error|error|warning|note|remark): ')
_SNIPPET = re.compile(r' *\d* \|(?: |$)')   # a source line under a diagnostic, or its carets
_PLUMBING = re.compile(r'In file included from input_line_\d+:\d+:$')
_INCLUDED = re.compile(r'In file included from ')
_DROPPED = re.compile(r'fatal error: too many errors emitted|error: Failed to materialize symbols')
_MISSING = re.compile(r'JIT session error: Symbols not found: \[ ?(.*?) ?\]$')
# Symbols of the prelude and the standard library: rarely the one to look for.
_INTERNAL = re.compile(r'_ZN?K?(?:4repl|St|S[abdios])')


class Diagnostics:
    """Filters what an input prints, line by line, as it arrives. names is
    the set of sources the session runs, as its inputs' origins name them:
    a brief diagnostic in one of them keeps its source lines."""

    def __init__(self, brief, names):
        self.brief, self.names = brief, names
        self.start()

    def start(self):
        """Begin an input."""
        self.seen = set()       # the errors and warnings shown so far
        self.shown = True       # whether the current diagnostic and its notes are shown
        self.snippets = True    # whether the source lines under it are
        self.under = False      # whether the line before was a diagnostic or a source line under one

    def feed(self, text):
        return ''.join(map(self._line, text.splitlines(keepends=True)))

    def _line(self, line):
        p = plain(line).rstrip('\r\n')
        if self.under and _SNIPPET.match(p):
            return line if self.shown and self.snippets else ''
        self.under = False
        if _PLUMBING.match(p):
            return ''
        m = _HEADER.match(p)
        if m:
            self.under = True
            if m.group('severity') != 'note':
                self.shown = p not in self.seen
                self.seen.add(p)
            self.snippets = not self.brief or m.group('file') in self.names
            if not self.shown:
                return ''
            return _shorter(line, m.group('file')) if self.brief else line
        if not self.shown and p.startswith('note: '):
            return ''
        if not self.brief:
            return line
        if _INCLUDED.match(p) or _DROPPED.match(p):
            return ''
        m = _MISSING.match(p)
        if m:
            return f'JIT session error: Symbols not found: [ {_symbols(m.group(1))} ]\n'
        return line


def _shorter(line, path):
    """line with its path written without '..', when that names the same file."""
    if '/../' not in path or line != plain(line):
        return line
    short = os.path.normpath(path)
    try:
        if not os.path.samefile(short, path):
            return line
    except OSError:
        return line
    return short + line[len(path):]


def _symbols(listing):
    """The first MOST_SYMBOLS of a link error's missing symbols, the code's
    own before the prelude's and the standard library's."""
    names = [n.strip() for n in listing.split(',') if n.strip()]
    names.sort(key=lambda n: bool(_INTERNAL.match(n)))
    shown = demangle(names[:MOST_SYMBOLS])
    more = len(names) - len(shown)
    return ', '.join(shown) + (f', and {more} more' if more > 0 else '')


def demangle(names):
    """names demangled by c++filt or llvm-cxxfilt; as they are without either."""
    tool = shutil.which('c++filt') or shutil.which('llvm-cxxfilt')
    if not tool or not names:
        return names
    try:
        out = subprocess.run([tool], input='\n'.join(names) + '\n', capture_output=True, text=True,
                             timeout=10).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return names
    return out if len(out) == len(names) else names
