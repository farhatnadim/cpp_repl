"""Files as inputs: a C++ source, or a .repl of inputs as typed."""

import re
import textwrap
from pathlib import Path

from .lexer import _CONDITIONAL, _is, _match, completeness, lex, split_statements

CPP_SUFFIXES = {'.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hh', '.hpp', '.hxx', '.h++'}

# The prelude headers. Every session has loaded them already, so an #include
# of one in a source (a saved session has them) is skipped.
PRELUDE_NAMES = frozenset({'cpp_repl.h', 'cpp_repl_std.h', 'cpp_repl_usd.h', 'cpp_repl_eigen.h',
                           'usd_repl.h'})


class BlockReader:
    """Groups lines into complete inputs. next_line(more) returns the next line
    (more: a block is in progress) and raises EOFError at the end."""

    def __init__(self, next_line):
        self.next_line = next_line
        self.pending = None

    def _next(self, more):
        if self.pending is not None:
            line, self.pending = self.pending, None
            return line
        try:
            return self.next_line(more)
        except EOFError:
            return None

    def read(self):
        lines, extendable = [], False
        while True:
            line = self._next(bool(lines))
            if line is None:
                if lines:
                    return '\n'.join(lines)
                raise EOFError
            if extendable:
                extendable = False
                if not re.match(r'\s*(?:else|catch)\b', line):
                    if line.strip():
                        self.pending = line
                    return '\n'.join(lines)
            elif not lines and not line.strip():
                continue
            lines.append(line)
            state = completeness('\n'.join(lines))
            if state == 'complete':
                return '\n'.join(lines)
            extendable = state == 'extendable'


def inputs_from_repl(text):
    lines = iter(text.splitlines())

    def next_line(more):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError

    reader, inputs = BlockReader(next_line), []
    while True:
        try:
            inputs.append(reader.read())
        except EOFError:
            return inputs


def _item_end(tokens, i):
    """A file-scope item ends at its ';' at depth 0, or at the '}' closing a
    function or namespace body that no ';' follows."""
    depth = 0
    for j in range(i, len(tokens)):
        t = tokens[j]
        if t.kind != 'op':
            continue
        if t.text in '([{':
            depth += 1
        elif t.text in ')]}':
            depth -= 1
            if (depth == 0 and t.text == '}' and not _is(tokens, j + 1, ';')
                    and tokens[i].text not in ('struct', 'class', 'union', 'enum', 'typedef')):
                return j
        elif t.text == ';' and depth == 0:
            return j
    return len(tokens) - 1


def _main(item):
    """(the tokens of main's parameters, the tokens of its body) if item is
    main's definition, else None."""
    for k, t in enumerate(item):
        if (t.kind == 'ident' and t.text == 'main' and k and item[k - 1].text in ('int', 'auto')
                and _is(item, k + 1, '(')):
            close = _match(item, k + 1)
            for b in range(close + 1, len(item)):
                if _is(item, b, '{'):
                    return item[k + 2:close], item[b + 1:_match(item, b)]
                if _is(item, b, ';'):
                    return None
    return None


def _source(text, toks):
    """The source text of toks as written: its lines, comments and
    indentation, dedented."""
    start, end = toks[0].start, toks[-1].start + len(toks[-1].text)
    before = text[text.rfind('\n', 0, start) + 1:start]
    return textwrap.dedent((before if before.isspace() else '') + text[start:end]).strip()


_TYPE_WORDS = {'int', 'char', 'const', 'void', 'unsigned', 'signed', 'auto', 'wchar_t'}


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
        elif t.kind == 'ident' and depth == 0 and t.text not in _TYPE_WORDS:
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


_CONSTANT = {'EXIT_SUCCESS', 'EXIT_FAILURE', 'true', 'false'}


def _constant(toks):
    """Whether an expression is a constant, as in `return 0;`."""
    return all(t.kind == 'num' or (t.kind == 'op' and t.text in '-+()')
               or (t.kind == 'ident' and t.text in _CONSTANT) for t in toks)


def _cpp_items(text, program='main'):
    tokens, _ = lex(text)
    items, i = [], 0
    while i < len(tokens):
        if _is(tokens, i, ';'):
            i += 1
            continue
        end = _item_end(tokens, i)
        item = tokens[i:end + 1]
        main = _main(item)
        if main is None:
            items.append(_source(text, item))
        else:
            params, body = main
            # A #pragma just before main() applies at file scope.
            name = next(t.start for t in item if t.kind == 'ident' and t.text == 'main')
            items.extend(t.text.strip() for t in item if t.kind == 'directive' and t.start < name)
            items.extend(_main_inputs(params, program))
            stmts = [toks for toks, _ in split_statements(body)]
            ret = stmts.pop() if stmts and _is(stmts[-1], 0, 'return', 'ident') else None
            items.extend(_source(text, toks) for toks in stmts)
            if ret is not None:
                # `return 0;` means nothing at the prompt, but `return run();` still runs run().
                expr = ret[1:-1] if _is(ret, len(ret) - 1, ';') else ret[1:]
                if expr and not _constant(expr):
                    items.append(_source(text, expr) + ';')
        i = end + 1
    return items


def split_directives(text):
    """[(kind, text)] in order: 'directive' for a preprocessor line at file
    scope, 'region' for a conditional block at file scope (#if ... #endif,
    everything inside it included), and 'code' for the code between them,
    which keeps its #pragma lines. A directive continued with a backslash is
    joined into one line."""
    parts, code = [], []

    def at_file_scope():
        tokens, open_comment = lex('\n'.join(code))
        depth = sum((t.text in '([{') - (t.text in ')]}') for t in tokens if t.kind == 'op')
        return depth == 0 and not open_comment

    def flush():
        if code:
            parts.append(('code', '\n'.join(code)))
            code.clear()

    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith('#') and not _PRAGMA_LINE.match(line) and at_file_scope():
            flush()
            m = _CONDITIONAL.match(line)
            if m and m.group(1) in ('if', 'ifdef', 'ifndef'):
                region, depth = [], 0
                while i < len(lines):
                    m = _CONDITIONAL.match(lines[i])
                    if m and m.group(1) in ('if', 'ifdef', 'ifndef'):
                        depth += 1
                    elif m and m.group(1) == 'endif':
                        depth -= 1
                    region.append(lines[i])
                    i += 1
                    if depth == 0:
                        break
                parts.append(('region', '\n'.join(region)))
                continue
            directive = line.strip()
            while directive.endswith('\\') and i + 1 < len(lines):
                i += 1
                directive = directive[:-1].rstrip() + ' ' + lines[i].strip()
            parts.append(('directive', directive))
        else:
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


def inputs_from_cpp(text, base=None, program='main', skip=PRELUDE_NAMES):
    """A C++ source as REPL inputs: preprocessor lines, #if regions and
    file-scope declarations and definitions as they are, then main()'s body
    one statement at a time. base is the source's directory, for its quoted
    includes."""
    inputs = []
    for kind, chunk in split_directives(text):
        if kind == 'code':
            inputs.extend(_cpp_items(chunk, program))
        elif kind == 'directive':
            if re.match(r'#\s*pragma\s+once\b', chunk):
                continue
            line = _include(chunk, base, skip)
            if line is not None:
                inputs.append(line)
        else:
            lines = (_include(line, base, skip) for line in chunk.split('\n'))
            inputs.append('\n'.join(line for line in lines if line is not None))
    return inputs


def is_cpp(path):
    return Path(path).suffix in CPP_SUFFIXES


def inputs_from_file(path, skip=PRELUDE_NAMES):
    path = Path(path)
    text = path.read_text()
    if is_cpp(path):
        return inputs_from_cpp(text, path.resolve().parent, path.name, skip)
    return inputs_from_repl(text)
