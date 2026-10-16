"""Files as inputs: a C++ source, or a .repl of inputs as typed."""

import re
import textwrap
from pathlib import Path

from .lexer import (CONDITIONAL_LINE, TYPE_WORDS, Origin, angle_end, completeness, initialiser_brace, lex,
                    located, match_bracket, split_statements, token_is)

CPP_SUFFIXES = {'.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hh', '.hpp', '.hxx', '.h++'}

# The prelude headers. Every session has loaded them already, so an #include
# of one in a source (a saved session has them) is skipped.
PRELUDE_NAMES = frozenset({'cpp_repl.h', 'cpp_repl_std.h', 'cpp_repl_usd.h', 'cpp_repl_eigen.h',
                           'cpp_repl_glm.h', 'usd_repl.h'})


class Input(str):
    """The text of one input, and its origin (see lexer.Origin): where in a
    file it starts, or None when that is not known. An input typed at the
    prompt is a plain str."""

    def __new__(cls, text, origin=None):
        self = super().__new__(cls, text)
        self.origin = origin
        return self


# What a line that extends an 'extendable' input starts with: an else or a
# catch after a block, and after a function head its body's brace, a
# constructor's initialisers, a requires-clause or a trailing return type.
_EXTENDS = re.compile(r'\s*(?:(?:else|catch|requires)\b|\{|:(?!:)|->)')


class BlockReader:
    """Groups lines into complete inputs. next_line(more) returns the next line
    (more: a block is in progress) and raises EOFError at the end. line is the
    number of the line the last input started on, from 1."""

    def __init__(self, next_line):
        self.next_line = next_line
        self.pending = None
        self.count = 0   # lines read
        self.line = 0

    def _next(self, more):
        """(the next line, its number), or (None, None) at the end."""
        if self.pending is not None:
            (line, number), self.pending = self.pending, None
            return line, number
        try:
            line = self.next_line(more)
        except EOFError:
            return None, None
        self.count += 1
        return line, self.count

    def read(self):
        lines, extendable = [], False
        while True:
            line, number = self._next(bool(lines))
            if line is None:
                if lines:
                    return '\n'.join(lines)
                raise EOFError
            if extendable:
                extendable = False
                if not _EXTENDS.match(line):
                    if line.strip():
                        self.pending = (line, number)
                    return '\n'.join(lines)
            elif not lines and not line.strip():
                continue
            if not lines:
                self.line = number
            lines.append(line)
            state = completeness('\n'.join(lines))
            if state == 'complete':
                return '\n'.join(lines)
            extendable = state == 'extendable'


def inputs_from_repl(text, path=None):
    """A .repl file's inputs, each with its origin in path when one is given."""
    lines = iter(text.splitlines())

    def next_line(more):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError

    reader, inputs = BlockReader(next_line), []
    while True:
        try:
            block = reader.read()
        except EOFError:
            return inputs
        inputs.append(Input(block, Origin(path, reader.line, 0, 0) if path else None))


_TYPE_HEADS = {'struct', 'class', 'union', 'enum', 'typedef'}


def _declares_type(tokens, i):
    """Whether the item at i is a type definition, whose body a declarator
    and a ';' may follow: `struct S { ... } s;`, templates included."""
    j = i
    while j < len(tokens) and tokens[j].kind == 'directive':
        j += 1
    if token_is(tokens, j, 'template', 'ident') and token_is(tokens, j + 1, '<'):
        j = angle_end(tokens, j + 1)
    return j < len(tokens) and tokens[j].kind == 'ident' and tokens[j].text in _TYPE_HEADS


def _item_end(tokens, i):
    """A file-scope item ends at its ';' at depth 0, or at the '}' closing a
    function or namespace body that no ';' follows. A '}' inside an
    initialiser (`int x = []{ return 1; }();`) or a type's body
    (`struct S { } s;`) ends nothing, and a function-try-block goes on with
    its handlers."""
    depth, initialiser, type_body = 0, False, _declares_type(tokens, i)
    mem_inits, mem_init = False, False   # reading a constructor's member initialisers; inside one
    for j in range(i, len(tokens)):
        t = tokens[j]
        if t.kind != 'op':
            continue
        if t.text == '=' and depth == 0:
            initialiser = True
        elif t.text in '([{':
            if depth == 0 and t.text == '{':
                mem_init = initialiser_brace(tokens, j, mem_inits)
            depth += 1
        elif t.text in ')]}':
            depth -= 1
            if depth == 0 and t.text == ')' and token_is(tokens, j + 1, ':'):
                mem_inits = True
            if (depth == 0 and t.text == '}' and not initialiser and not type_body and not mem_init
                    and not token_is(tokens, j + 1, ';') and not token_is(tokens, j + 1, 'catch', 'ident')):
                return j
        elif t.text == ';' and depth == 0:
            return j
    return len(tokens) - 1


def _main(item):
    """(the tokens of main's parameters, the tokens of its body) if item is
    main's definition, else None."""
    for k, t in enumerate(item):
        if (t.kind == 'ident' and t.text == 'main' and k and item[k - 1].text in ('int', 'auto')
                and token_is(item, k + 1, '(')):
            close = match_bracket(item, k + 1)
            for b in range(close + 1, len(item)):
                if token_is(item, b, '{'):
                    return item[k + 2:close], item[b + 1:match_bracket(item, b)]
                if token_is(item, b, ';'):
                    return None
    return None


def _source(text, toks):
    """(the source text of toks as written: its lines, comments and
    indentation, dedented; how many columns dedenting took off its lines)."""
    start, end = toks[0].start, toks[-1].start + len(toks[-1].text)
    before = text[text.rfind('\n', 0, start) + 1:start]
    whole = (before if before.isspace() else '') + text[start:end]
    dedented = textwrap.dedent(whole)
    # The first line holds a token, so it lost what every line lost.
    return dedented.strip(), len(whole.split('\n')[0]) - len(dedented.split('\n')[0])


def _input(text, toks, origin):
    """The input for toks, with its origin when the text's is known."""
    source, indent = _source(text, toks)
    if origin is None:
        return Input(source)
    where = located(origin, text, toks[0].start)
    return Input(source, where._replace(indent=where.indent + indent))


def _parameters(params):
    """The names of a function's parameters, from the tokens between its
    parentheses."""
    names, depth, current = [], 0, None
    for t in params:
        if t.kind == 'op' and t.text in '([{<':
            depth += 1
        elif t.kind == 'op' and t.text in ')]}>':
            depth -= 1
        elif t.kind == 'op' and t.text == ',' and depth == 0:
            names.append(current)
            current = None
        elif t.kind == 'ident' and depth == 0 and t.text not in TYPE_WORDS:
            current = t.text
    names.append(current)
    return [n for n in names if n]


def _main_inputs(params, program):
    """Inputs that stand in for main's parameters: argc counts one argument,
    and argv holds the program's name."""
    names = _parameters(params)
    inputs = []
    if names:
        inputs.append(f'int {names[0]} = 1;')
    if len(names) > 1:
        name = program.replace('\\', '\\\\').replace('"', '\\"')
        inputs.append(f'char *cpp_repl_argv[] = {{const_cast<char *>("{name}"), nullptr}};')
        inputs.append(f'char **{names[1]} = cpp_repl_argv;')
    return inputs


def _cpp_items(text, program='main', origin=None):
    """The inputs of a stretch of a C++ source with no directive at file
    scope; origin is where text starts, or None. main()'s statements come one
    by one, its `return` included: run, it ends the program with its value,
    as it does compiled."""
    tokens, _ = lex(text)
    items, i = [], 0
    while i < len(tokens):
        if token_is(tokens, i, ';'):
            i += 1
            continue
        end = _item_end(tokens, i)
        item = tokens[i:end + 1]
        main = _main(item)
        if main is None:
            items.append(_input(text, item, origin))
        else:
            params, body = main
            # A #pragma just before main() applies at file scope.
            name = next(t.start for t in item if t.kind == 'ident' and t.text == 'main')
            items.extend(Input(t.text.strip()) for t in item if t.kind == 'directive' and t.start < name)
            items.extend(Input(line) for line in _main_inputs(params, program))
            items.extend(_input(text, toks, origin) for toks, _ in split_statements(body))
        i = end + 1
    return items


def split_directives(text):
    """[(kind, text, offset)] in order, offset being where the part starts in
    text: 'directive' for a preprocessor line at file scope, 'region' for a
    conditional block at file scope (#if ... #endif, everything inside it
    included), and 'code' for the code between them, which keeps its #pragma
    lines. A directive continued with a backslash is joined into one line."""
    parts, code = [], []
    lines = text.split('\n')
    starts = [0]   # where each line starts in text
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    code_start = 0

    def at_file_scope():
        """Whether the code so far ends at file scope: brackets balanced,
        and no comment or raw string left open (a '#' inside one is text)."""
        tokens, unclosed = lex('\n'.join(code))
        depth = sum((t.text in '([{') - (t.text in ')]}') for t in tokens if t.kind == 'op')
        return depth == 0 and not unclosed

    def flush():
        if code:
            parts.append(('code', '\n'.join(code), code_start))
            code.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith('#') and not _PRAGMA_LINE.match(line) and at_file_scope():
            flush()
            m = CONDITIONAL_LINE.match(line)
            if m and m.group(1) in ('if', 'ifdef', 'ifndef'):
                region, depth, first = [], 0, i
                while i < len(lines):
                    m = CONDITIONAL_LINE.match(lines[i])
                    if m and m.group(1) in ('if', 'ifdef', 'ifndef'):
                        depth += 1
                    elif m and m.group(1) == 'endif':
                        depth -= 1
                    region.append(lines[i])
                    i += 1
                    if depth == 0:
                        break
                parts.append(('region', '\n'.join(region), starts[first]))
                continue
            directive, first = line.strip(), starts[i] + len(line) - len(line.lstrip())
            while directive.endswith('\\') and i + 1 < len(lines):
                i += 1
                directive = directive[:-1].rstrip() + ' ' + lines[i].strip()
            parts.append(('directive', directive, first))
        else:
            if not code:
                code_start = starts[i]
            code.append(line)
        i += 1
    flush()
    return parts


# A #pragma other than `once` stays with the code after it, as a #pragma inside a
# function does.
_PRAGMA_LINE = re.compile(r'\s*#\s*pragma\s+(?!once\b)')

_INCLUDE = re.compile(r'^(\s*#\s*include\s*)(["<])([^">]+)([">])(.*)$')


def _include(line, base, skip):
    """An #include line as a session takes it: None for a prelude header,
    which every session has loaded; a quoted include made absolute when the
    file sits next to the source; otherwise the line as it is."""
    m = _INCLUDE.match(line)
    if not m:
        return line
    if Path(m.group(3)).name in skip:
        return None
    if m.group(2) == '"' and base is not None and not Path(m.group(3)).is_absolute():
        candidate = base / m.group(3)
        if candidate.is_file():
            return f'{m.group(1)}"{candidate.resolve()}"{m.group(5)}'
    return line


def inputs_from_cpp(text, base=None, program='main', skip=PRELUDE_NAMES, path=None):
    """A C++ source as REPL inputs: preprocessor lines, #if regions and
    file-scope declarations and definitions as they are, then main()'s body
    one statement at a time. base is the source's directory, for its quoted
    includes; path, when given, is the source as the inputs' origins name it."""
    whole = Origin(path, 1, 0, 0) if path else None
    inputs = []
    for kind, chunk, at in split_directives(text):
        origin = located(whole, text, at) if whole else None
        if kind == 'code':
            inputs.extend(_cpp_items(chunk, program, origin))
        elif kind == 'directive':
            if re.match(r'#\s*pragma\s+once\b', chunk):
                continue
            line = _include(chunk, base, skip)
            if line is not None:
                inputs.append(Input(line, origin))
        else:
            lines = [_include(line, base, skip) for line in chunk.split('\n')]
            # A line left out would move the lines after it.
            inputs.append(Input('\n'.join(line for line in lines if line is not None),
                                origin if None not in lines else None))
    return inputs


def is_cpp(path):
    return Path(path).suffix in CPP_SUFFIXES


def inputs_from_file(path, skip=PRELUDE_NAMES):
    """A C++ source's or a .repl file's inputs, with their origins in it."""
    path = Path(path)
    text = path.read_text()
    if is_cpp(path):
        return inputs_from_cpp(text, path.resolve().parent, path.name, skip, path=str(path))
    return inputs_from_repl(text, path=str(path))
