"""C++ lexing and statement structure: just enough C++ to find where inputs
and statements end, and what to send to clang-repl for each one. Comments are
dropped."""

import itertools
import re
from collections import namedtuple

_TOKEN = re.compile(r'''
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<open_comment>/\*)
  | (?P<raw>(?:u8|[uUL])?R"(?P<delim>[^()\\\s"]{0,16})\(.*?\)(?P=delim)")
  | (?P<open_raw>(?:u8|[uUL])?R"[^()\\\s"]{0,16}\()
  | (?P<str>(?:u8|[uUL])?"(?:\\.|[^"\\\n])*"?)
  | (?P<chr>(?:u8|[uUL])?'(?:\\.|[^'\\\n])*'?)
  | (?P<ident>[A-Za-z_]\w*)
  | (?P<num>\.?\d(?:[eEpP][+-]|[\w.'])*)
  | (?P<op>::|->|\.\.\.|<<=|>>=|<=>|&&|\|\||<<|>>|\+\+|--|[-+*/%&|^!=<>]=|.)
''', re.X | re.S)
_KINDS = ('ws', 'comment', 'open_comment', 'raw', 'open_raw', 'str', 'chr', 'ident', 'num', 'op')

# kind is one of _KINDS[3:] or 'directive': a preprocessor line inside code.
Token = namedtuple('Token', 'kind text space start')   # space: whitespace came before it

CONTROL = {'for', 'while', 'do', 'if', 'switch', 'try'}
# An input ending in one of these is not finished yet.
CONTINUES = {'=', ',', '<<', '&&', '||', '+', '-', '/', '%', '|', '^', '?', ':', '::',
             '->', '.', '<', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=',
             '&=', '|=', '^=', '<<=', '>>='}

# What to send for one input. text is a single line, or several lines when
# file is set: then the input goes to clang-repl through a temporary file and
# an #include, which keeps the lines a preprocessor directive needs.
Send = namedtuple('Send', 'text kind file')

# Where an input starts in its source: the path, the line (from 1) and column
# (from 0) of its first character, and how many columns its later lines lost
# when the input was dedented. Code sent with an origin goes through a file in
# which every character sits at its source's line and column, after a #line
# directive: clang then reports diagnostics, __FILE__ and __LINE__ in the
# source's own terms.
Origin = namedtuple('Origin', 'path line col indent')


def located(origin, text, offset):
    """The origin of what starts at offset in text, an input from origin."""
    line = text.count('\n', 0, offset)
    col = offset - (text.rfind('\n', 0, offset) + 1)
    return origin._replace(line=origin.line + line, col=(origin.col if line == 0 else origin.indent) + col)


def _place(s, start, end, origin):
    """s[start:end], part of an input from origin, as lines in which every
    character sits at its line and column in the source."""
    o = located(origin, s, start)
    name = str(o.path).replace('\\', '\\\\').replace('"', '\\"')
    first, *rest = s[start:end].split('\n')
    return (f'#line {o.line} "{name}"\n' + ' ' * o.col + first
            + ''.join('\n' + ' ' * o.indent + line for line in rest))

_PRAGMA = re.compile(r'#\s*pragma\b\s*(.*)\Z', re.S)
# Pragmas that apply to the statement after them.
_STATEMENT_PRAGMA = re.compile(r'#\s*pragma\s+(omp|clang\s+loop|unroll|nounroll|GCC\s+(unroll|ivdep)|ivdep|simd|loop)\b')
# A line of a conditional region, and a directive token that is one.
_CONDITIONAL = re.compile(r'^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b', re.M)
_CONDITIONAL_DIRECTIVE = re.compile(r'#[ \t]*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b')


def _directive_end(text, pos):
    """Where the preprocessor directive starting at pos ends: at the end of
    its line, after any lines it continues onto with a backslash."""
    while True:
        newline = text.find('\n', pos)
        if newline < 0:
            return len(text)
        if text[pos:newline].rstrip(' \t\r').endswith('\\'):
            pos = newline + 1
            continue
        return newline


def lex(text):
    """The significant tokens of text, and whether a /* comment or a raw
    string literal is left open at its end. A '#' that starts a line starts
    a directive token, which runs to the end of the line."""
    tokens, space, pos = [], False, 0
    while pos < len(text):
        if text[pos] == '#' and not text[text.rfind('\n', 0, pos) + 1:pos].strip():
            end = _directive_end(text, pos)
            tokens.append(Token('directive', text[pos:end], space, pos))
            space, pos = True, end
            continue
        m = _TOKEN.match(text, pos)
        kind = next(k for k in _KINDS if m.group(k) is not None)
        pos = m.end()
        if kind in ('ws', 'comment'):
            space = True
        elif kind in ('open_comment', 'open_raw'):
            return tokens, True
        else:
            tokens.append(Token(kind, m.group(kind), space, m.start()))
            space = False
    return tokens, False


def _one_line(token):
    """A raw string spanning lines, rewritten as an ordinary literal: every
    input has to fit on one line."""
    if token.kind != 'raw' or '\n' not in token.text:
        return token.text
    m = re.fullmatch(r'(u8|[uUL])?R"([^(]*)\((.*)\)\2"', token.text, re.S)
    body = m.group(3).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return (m.group(1) or '') + '"' + body + '"'


def _directive(token, lines):
    """A directive token as text. A #pragma becomes the _Pragma operator, which
    fits on one line with the code around it. Any other directive keeps a line
    of its own when lines is set."""
    text = re.sub(r'\\[ \t]*\n', ' ', token.text).strip()
    if '"' not in text:
        text = re.sub(r'\s*(//.*|/\*.*?\*/)\s*$', '', text)
    m = _PRAGMA.match(text)
    if m:
        return '_Pragma("%s")' % m.group(1).strip().replace('\\', '\\\\').replace('"', '\\"')
    return '\n' + text + '\n' if lines else text


def needs_lines(tokens):
    """Whether tokens hold a directive other than a #pragma, which needs a line
    of its own, so that the input has to go through a file."""
    return any(t.kind == 'directive' and not _PRAGMA.match(t.text.strip()) for t in tokens)


def join(tokens, lines=False):
    """Tokens back to one line of text, a single space where there was any.
    With lines set, a directive other than a #pragma keeps a line of its own."""
    out = []
    for i, t in enumerate(tokens):
        text = _directive(t, lines) if t.kind == 'directive' else _one_line(t)
        out.append((' ' if t.space and i else '') + text)
    return ''.join(out)


def _is(tokens, i, text, kind='op'):
    return i < len(tokens) and tokens[i].text == text and tokens[i].kind == kind


def _match(tokens, i):
    """Index of the bracket that closes the one at i (the last token if none does)."""
    depth = 0
    for j in range(i, len(tokens)):
        t = tokens[j]
        if t.kind == 'op' and t.text in '([{':
            depth += 1
        elif t.kind == 'op' and t.text in ')]}':
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


def _initialiser_brace(tokens, j, mem_inits):
    """Whether the '{' at j opens an initialiser rather than a block or a
    body: `x{1}` among a constructor's member initialisers (mem_inits: a
    ')' and a ':' came before), which the body's '{' follows."""
    return mem_inits and j > 0 and (tokens[j - 1].kind == 'ident' or tokens[j - 1].text == '>')


def _plain_end(tokens, i):
    """End of a statement that is not control flow: its ';' at depth 0. A '}'
    closing a top-level block also ends it when control flow or another
    block follows."""
    depth, mem_inits, initialiser = 0, False, False
    for j in range(i, len(tokens)):
        t = tokens[j]
        if t.kind != 'op':
            continue
        if t.text in '([{':
            if depth == 0 and t.text == '{':
                initialiser = _initialiser_brace(tokens, j, mem_inits)
            depth += 1
        elif t.text in ')]}':
            depth -= 1
            if depth == 0 and t.text == ')' and _is(tokens, j + 1, ':'):
                mem_inits = True
            if depth == 0 and t.text == '}' and not initialiser and j + 1 < len(tokens):
                after = tokens[j + 1]
                if (after.kind == 'ident' and after.text in CONTROL) or _is(tokens, j + 1, '{'):
                    return j
        elif t.text == ';' and depth == 0:
            return j
    return len(tokens) - 1


def _stmt_end(tokens, i):
    """Index of the last token of the statement starting at i."""
    n = len(tokens)
    if i >= n:
        return n - 1
    t = tokens[i]
    if _is(tokens, i, '{'):
        return _match(tokens, i)
    if t.kind != 'ident' or t.text not in CONTROL:
        return _plain_end(tokens, i)
    j = i + 1
    if t.text in ('for', 'while', 'switch', 'if'):
        if t.text == 'if':
            if _is(tokens, j, '!'):
                j += 1
            if _is(tokens, j, 'constexpr', 'ident') or _is(tokens, j, 'consteval', 'ident'):
                j += 1
        if _is(tokens, j, '('):
            j = _match(tokens, j) + 1
        if j >= n:
            return n - 1
        end = _stmt_end(tokens, j)
        if t.text == 'if' and _is(tokens, end + 1, 'else', 'ident'):
            return _stmt_end(tokens, end + 2) if end + 2 < n else n - 1
        return end
    if t.text == 'do':
        end = _stmt_end(tokens, j) if j < n else n - 1
        if _is(tokens, end + 1, 'while', 'ident'):
            k = end + 2
            if _is(tokens, k, '('):
                k = _match(tokens, k) + 1
            return k if _is(tokens, k, ';') else min(k, n) - 1
        return end
    # try
    if not _is(tokens, j, '{'):
        return _plain_end(tokens, i)
    end = _match(tokens, j)
    while _is(tokens, end + 1, 'catch', 'ident'):
        k = end + 2
        if _is(tokens, k, '('):
            k = _match(tokens, k) + 1
        if not _is(tokens, k, '{'):
            return n - 1
        end = _match(tokens, k)
    return end


def split_statements(tokens):
    """[(tokens, is_control_flow)] for each statement, in order. Directives
    belong to the statement after them, as a #pragma does."""
    out, i, n = [], 0, len(tokens)
    while i < n:
        if _is(tokens, i, ';'):
            i += 1
            continue
        j = i
        while j < n and tokens[j].kind == 'directive':
            j += 1
        if j == n:
            out.append((tokens[i:], False))
            break
        end = _stmt_end(tokens, j)
        first = tokens[j]
        control = _is(tokens, j, '{') or (first.kind == 'ident' and first.text in CONTROL)
        out.append((tokens[i:end + 1], control))
        i = end + 1
    return out


def _unsplice(text):
    """Join backslash-continued lines."""
    return re.sub(r'\\[ \t]*\n', ' ', text)


def _open_conditionals(tokens):
    """How many #if, #ifdef and #ifndef directives among tokens have no
    #endif yet."""
    depth = 0
    for t in tokens:
        m = _CONDITIONAL_DIRECTIVE.match(t.text) if t.kind == 'directive' else None
        if m and m.group(1) in ('if', 'ifdef', 'ifndef'):
            depth += 1
        elif m and m.group(1) == 'endif':
            depth -= 1
    return depth


def open_conditionals(text):
    """How many #if, #ifdef and #ifndef lines in text have no #endif yet.
    A '#' inside a comment or a raw string is no directive."""
    return _open_conditionals(lex(text)[0])


def completeness(text):
    """'complete', 'incomplete', or 'extendable': complete, but a multi-line
    if or try that an else or catch on the next line would still extend, or
    a function head such as `int f()` that its body on the next line
    would."""
    s = _unsplice(text).strip()
    if not s:
        return 'complete'
    if s.endswith('\\'):
        return 'incomplete'
    if s[0] == '%' and '\n' not in s:
        return 'complete'
    tokens, unclosed = lex(s)
    if unclosed or _open_conditionals(tokens) > 0:
        return 'incomplete'
    if not tokens:
        return 'complete'
    if all(t.kind == 'directive' for t in tokens):
        # A pragma such as `#pragma omp parallel for` waits for its statement.
        return 'incomplete' if _STATEMENT_PRAGMA.match(tokens[-1].text) else 'complete'
    depth = 0
    for t in tokens:
        if t.kind == 'op' and t.text in '([{':
            depth += 1
        elif t.kind == 'op' and t.text in ')]}':
            depth -= 1
    if depth > 0:
        return 'incomplete'
    last = tokens[-1]
    if (last.kind == 'op' and last.text in CONTINUES) or (last.kind == 'ident' and last.text in ('else', 'do')):
        return 'incomplete'
    stmts = split_statements(tokens)
    if not stmts:
        return 'complete'
    toks, control = stmts[-1]
    first = next((t for t in toks if t.kind != 'directive'), toks[0])
    if control and first.kind == 'ident':
        head = first.text
        if not (last.kind == 'op' and last.text in (';', '}')):
            return 'incomplete'
        if head == 'do' and last.text != ';':
            return 'incomplete'
        if head == 'try' and not any(t.kind == 'ident' and t.text == 'catch' for t in toks):
            return 'incomplete'
        if head in ('if', 'try') and last.text == '}' and '\n' in s:
            return 'extendable'
        return 'complete'
    return _head_state(toks) or 'complete'


# Things that may come just before a declaration's type.
_SPECIFIERS = {'const', 'constexpr', 'constinit', 'static', 'inline', 'thread_local', 'volatile',
               'extern', 'mutable', 'register', 'unsigned', 'signed', 'long', 'short', 'typename',
               'struct', 'class', 'enum', 'union'}
_TYPE_WORDS = {'auto', 'int', 'char', 'bool', 'float', 'double', 'void', 'wchar_t', 'char8_t',
               'char16_t', 'char32_t', 'long', 'short', 'unsigned', 'signed', 'const', 'volatile'}
# What ends a lambda's introducer [...].
_AFTER_CAPTURES = {'(', '{', '<', '->'}
_LAMBDA_WORDS = {'mutable', 'constexpr', 'consteval', 'noexcept', 'static'}

_names = itertools.count(1)


def fresh_name():
    """A function name no input uses: cpp_repl_1, cpp_repl_2, ..."""
    return f'cpp_repl_{next(_names)}'


def _angle_end(tokens, i):
    """Index just past the template argument list opening at tokens[i]. A
    bracketed part, as in enable_if_t<(N > 4)>, is skipped whole."""
    depth, j = 0, i
    while j < len(tokens):
        t = tokens[j]
        if t.kind == 'op':
            if t.text == '<':
                depth += 1
            elif t.text in ('>', '>>'):
                depth -= len(t.text)
            elif t.text in '([{':
                j = _match(tokens, j)
            if depth <= 0:
                return j + 1
        j += 1
    return len(tokens)


def _skip_attributes(tokens, i):
    """Index past the [[...]], alignas(...) and __attribute__((...)) at i."""
    n = len(tokens)
    while i < n:
        if _is(tokens, i, '[') and _is(tokens, i + 1, '['):
            i = _match(tokens, i) + 1
        elif (tokens[i].kind == 'ident' and tokens[i].text in ('alignas', '__attribute__', '__declspec')
                and _is(tokens, i + 1, '(')):
            i = _match(tokens, i + 1) + 1
        else:
            break
    return i


def _declares(tokens, i):
    """Whether tokens[i] is a declared name: an identifier that =, {, (, [,
    ; or , follows, or a declarator in parentheses, as in int (*f)(int)."""
    if _is(tokens, i, '(') and (_is(tokens, i + 1, '*') or _is(tokens, i + 1, '&')):
        return True
    return (i + 1 < len(tokens) and tokens[i].kind == 'ident'
            and tokens[i + 1].kind == 'op' and tokens[i + 1].text in ('=', '{', '(', '[', ';', ','))


def _declarator_at(tokens):
    """Where a statement that looks like a variable declaration declares its
    name: after attributes, specifiers, a type (a keyword, or a name with ::
    and <...>) and * and &, the index of the name that =, {, (, [, ; or a
    comma follows, of the [ of a structured binding, or of the ( of a
    declarator in parentheses. None when the statement is no declaration.
    Directives are left out first, so the index is into the tokens without
    them."""
    tokens = [t for t in tokens if t.kind != 'directive']
    i, n = _skip_attributes(tokens, 0), len(tokens)
    typed = False   # the specifiers hold a type word: `unsigned long n` needs no other
    while i < n and tokens[i].kind == 'ident' and tokens[i].text in _SPECIFIERS:
        typed = typed or tokens[i].text in _TYPE_WORDS
        i += 1
    if i < n and tokens[i].kind == 'ident' and not (typed and _declares(tokens, i)):
        if tokens[i].text == 'decltype' and _is(tokens, i + 1, '('):
            i = _match(tokens, i + 1) + 1
        else:
            i += 1
        while i < n:
            if _is(tokens, i, '::') and i + 1 < n and tokens[i + 1].kind == 'ident':
                i += 2
            elif _is(tokens, i, '<'):
                i = _angle_end(tokens, i)
            elif tokens[i].kind == 'ident' and tokens[i].text in _TYPE_WORDS:
                i += 1
            else:
                break
    elif not typed:
        return None
    while i < n and (tokens[i].text in ('*', '&', '&&') and tokens[i].kind == 'op'
                     or tokens[i].kind == 'ident' and tokens[i].text in ('const', 'volatile')):
        i += 1
    if _is(tokens, i, '[') or _declares(tokens, i):   # a structured binding: auto [a, b] = ...
        return i
    return None


def _declaration(tokens):
    """Whether a statement looks like a variable declaration (see
    _declarator_at)."""
    return _declarator_at(tokens) is not None


def _has_body_or_end(tokens):
    """Whether a statement holds its ';' or the '{' of its body at depth 0.
    The braces of a requires-expression are no body."""
    i, depth, n = 0, 0, len(tokens)
    while i < n:
        t = tokens[i]
        if t.kind == 'ident' and t.text == 'requires' and depth == 0:
            j = i + 1
            if _is(tokens, j, '('):
                j = _match(tokens, j) + 1
            i = _match(tokens, j) + 1 if _is(tokens, j, '{') else i + 1
            continue
        if t.kind == 'op':
            if t.text in '([{':
                if t.text == '{' and depth == 0:
                    return True
                depth += 1
            elif t.text in ')]}':
                depth -= 1
            elif t.text == ';' and depth == 0:
                return True
        i += 1
    return False


_TYPE_HEADS = {'struct', 'class', 'union', 'enum', 'namespace'}


def _function_head(tokens):
    """Whether a statement is the head of a function definition whose body
    is still to come: a declaration whose name a parameter list follows,
    then trailers only (const, noexcept, override, -> T, requires C<T>)."""
    k = _declarator_at(tokens)
    if k is None or not _is(tokens, k + 1, '('):
        return False
    close = _match(tokens, k + 1)
    return not any(t.kind == 'op' and t.text == '=' for t in tokens[close + 1:])


def _head_state(toks):
    """For a statement with neither its ';' nor the '{' of a body:
    'incomplete' when it can only be the head of what follows (a template
    head, attributes alone, the head of a type or a namespace), 'extendable'
    for a function head such as `int f()`, whose body may follow on the next
    line, in the style that puts a brace on a line of its own. None when the
    statement is something else, a bare expression above all."""
    code = [t for t in toks if t.kind != 'directive']
    if not code or _has_body_or_end(code):
        return None
    i = _skip_attributes(code, 0)
    if i == len(code) or _is(code, i, 'template', 'ident'):
        return 'incomplete'
    while i < len(code) and code[i].kind == 'ident' and code[i].text in _SPECIFIERS - _TYPE_HEADS:
        i += 1
    if i < len(code) and code[i].kind == 'ident' and code[i].text in _TYPE_HEADS and not _declaration(code):
        return 'incomplete'
    return 'extendable' if _function_head(code) else None


def _has_lambda(tokens):
    """Whether tokens hold a lambda expression: a [ that does not follow a
    name, a ) or a ] (which would make it a subscript), closed by a ] that a
    parameter list, a body, or a lambda specifier follows."""
    for i, t in enumerate(tokens):
        if not _is(tokens, i, '['):
            continue
        if i > 0:
            before = tokens[i - 1]
            if (before.kind in ('ident', 'num', 'str', 'chr', 'raw') and before.text != 'return'
                    or before.kind == 'op' and before.text in (')', ']')):
                continue
        close = _match(tokens, i)
        if close + 1 < len(tokens):
            after = tokens[close + 1]
            if (after.kind == 'op' and after.text in _AFTER_CAPTURES
                    or after.kind == 'ident' and after.text in _LAMBDA_WORDS):
                return True
    return False


# The name of a macro, as macros are named: TF_DEFINE_PRIVATE_TOKENS, Q_UNUSED,
# DEFINE_bool. One may expand to a declaration, which belongs at file scope.
_MACRO_NAME = re.compile(r'[A-Z][A-Z0-9_]*|[A-Z]\w*_\w*')


def needs_function(tokens, control):
    """Whether a statement runs inside a function of its own (see
    in_function): control flow, which clang-repl 18 mis-compiles at top
    level; a `return` or `throw`, which ends the program as main()'s would; a
    statement with a lambda in it, which crashes clang-repl 23 at top level;
    and any other expression statement, so that an exception it throws is
    caught. A declaration such as `auto f = [](int x) { ... };` stays where
    it is, and so does a definition: a struct, namespace or function belongs
    at file scope, where later inputs can see it. So does a statement that
    starts with a macro's name, since the macro may declare something."""
    code = [t for t in tokens if t.kind != 'directive']
    if not code:
        return False
    first = code[0]
    if control or (first.kind == 'ident' and first.text in ('return', 'throw')):
        return True
    if _declaration(code) or _definition(code):
        return False
    return _has_lambda(code) or not (first.kind == 'ident' and _MACRO_NAME.fullmatch(first.text))


def in_function(name, body):
    """body as the body of a new function, and a call of it. The function
    returns repl::ended, which an int converts to: `return 1;` inside body,
    as in main()'s `if (bad) return 1;`, becomes the program's exit status,
    an exception that leaves body is named and ends the program as abort()
    would, and body falling off its end returns nothing. repl::finish()
    reports the end to the session."""
    return (f'repl::ended {name}() {{ try {{ {body} }} catch (...) {{ return repl::uncaught(); }} return {{}}; }} '
            f'repl::finish({name}());')


def _end(toks):
    return toks[-1].start + len(toks[-1].text)


def transform(text, wrap=True, fresh=fresh_name, origin=None, statement=False):
    """What to send to clang-repl for one input, as a Send whose kind is 'pp',
    'stmt', 'expr' or 'empty'. A statement that cannot run at top level (see
    needs_function) runs as the body of a new function named fresh();
    wrap=False leaves every statement as it is, for code that goes inside a
    function anyway. A block of preprocessor lines (an #if region), or code
    holding a directive other than a #pragma, goes through a file. With
    statement set, a bare expression is sent as a statement.

    With an origin (see Origin), code and #if regions go through a file in
    which they sit at their source's lines and columns. An input with a line
    splice outside a directive goes as one line, as without an origin."""
    s = _unsplice(text).strip()
    if origin is not None:
        origin = located(origin, text, len(text) - len(text.lstrip())) if s == text.strip() else None
    if _CONDITIONAL.match(s):
        return Send(_place(s, 0, len(s), origin) if origin else s, 'pp', True)   # an #if region
    tokens, _ = lex(s)
    if tokens and all(t.kind == 'directive' for t in tokens):
        return Send(s, 'pp', len(tokens) > 1)
    if not tokens:
        return Send('', 'empty', False)
    stmts = split_statements(tokens)
    if not stmts:
        return Send('', 'empty', False)
    lines = needs_lines(tokens)
    if len(stmts) == 1 and _open_end(*stmts[0]) and _statement_shaped(stmts[0][0]):
        statement = True   # `int x = 5` without its ';': a statement, with no value to print
    if not statement and not lines and len(stmts) == 1 and _open_end(*stmts[0]):
        toks = stmts[0][0]
        if origin:
            # The caller writes the expression into a call: its first line
            # has to start after the #line directive.
            return Send('\n' + _place(s, toks[0].start, _end(toks), origin), 'expr', True)
        return Send(join(toks), 'expr', False)
    parts = []
    for toks, control in stmts:
        semicolon = statement and _open_end(toks, control)
        if origin:
            line = _place(s, toks[0].start, _end(toks), origin) + (';' if semicolon else '')
        else:
            line = join(toks, lines) + (';' if semicolon else '')
        complete = control or semicolon or _is(toks, len(toks) - 1, ';')
        if wrap and complete and needs_function(toks, control):
            # Placed, the function's head and tail keep lines of their own,
            # out of the source lines a diagnostic shows.
            line = in_function(fresh(), f'\n{line}\n' if origin else line)
        parts.append(line)
    return Send(('\n' if origin else ' ').join(parts), 'stmt', lines or origin is not None)


def _statement_shaped(toks):
    """Whether an input without its ';' is a statement rather than an
    expression with a value to print: a declaration (`int x = 5`,
    `std::vector<int> v{1, 2}`), a return or a throw."""
    code = [t for t in toks if t.kind != 'directive']
    if not code:
        return False
    return _declaration(code) or (code[0].kind == 'ident' and code[0].text in ('return', 'throw'))


_DEFINITION_HEADS = {'struct', 'class', 'union', 'enum', 'template', 'typedef', 'namespace', 'using',
                     'extern', 'concept'}
# What may stand between a function's parameter list and its body's '{'.
_BEFORE_BODY = {')', 'const', 'volatile', 'override', 'final', 'noexcept', '&', '&&', 'try'}


def _skip_initialisers(tokens, i):
    """Index past a constructor's member initialisers starting at i, after
    the ':': `x(1), y{2}, Base<T>(args)...`, up to the body's '{'."""
    n = len(tokens)
    while i < n:
        while i < n and (tokens[i].kind == 'ident' or _is(tokens, i, '::')):
            i += 1
        if _is(tokens, i, '<'):
            i = _angle_end(tokens, i)
        if not (_is(tokens, i, '(') or _is(tokens, i, '{')):
            break
        i = _match(tokens, i) + 1
        if _is(tokens, i, '...'):
            i += 1
        if not _is(tokens, i, ','):
            break
        i += 1
    return i


def _definition(tokens):
    """is_definition() for an input already lexed."""
    tokens = [t for t in tokens if t.kind != 'directive']
    i, n = _skip_attributes(tokens, 0), len(tokens)
    if i >= n or _is(tokens, i, '['):   # nothing, or a lambda
        return False
    while i < n and tokens[i].kind == 'ident' and tokens[i].text in ('inline', 'static', 'constexpr', 'consteval',
                                                                     'constinit', 'export'):
        i += 1
    head = tokens[i].text if i < n else ''
    if head in _DEFINITION_HEADS:
        return True
    if head in CONTROL or not _is(tokens, n - 1, '}'):
        return False
    # A function definition: a parameter list, then its trailers (const,
    # noexcept, -> T, requires C<T>, a constructor's member initialisers),
    # then the body. An '=' at depth 0 makes it a declaration with an
    # initialiser instead.
    depth, params, trailing = 0, False, False
    while i < n:
        t = tokens[i]
        if t.kind == 'ident' and t.text == 'requires' and depth == 0 and params:
            trailing = True
        elif t.kind == 'op':
            if depth == 0:
                if t.text == '=':
                    return False
                if t.text == '->' and params:
                    trailing = True
                elif t.text == ':' and params:
                    return _is(tokens, _skip_initialisers(tokens, i + 1), '{')
                elif t.text == '{':
                    return params and (trailing or tokens[i - 1].text in _BEFORE_BODY)
            if t.text in '([{':
                depth += 1
            elif t.text in ')]}':
                depth -= 1
                params = params or (depth == 0 and t.text == ')')
        i += 1
    return False


def is_definition(text):
    """Whether an input belongs at file scope in a C++ program: a type,
    namespace, template or function definition."""
    tokens, _ = lex(_unsplice(text))
    return _definition(tokens)


def _open_end(toks, control):
    """Whether a statement ends without its ';', as a bare expression does.
    A '}' ends a block or a definition, but a '}' that closes a brace
    initialiser, as in std::vector<int>{1, 2}, ends an expression."""
    if control or _is(toks, len(toks) - 1, ';') or toks[-1].kind == 'directive':
        return False
    return not _is(toks, len(toks) - 1, '}') or not _definition(toks)


def trailing_expression(text):
    """For several statements ending in a bare expression, as a pasted block
    might: (the statements, the expression, where the expression starts in
    text), so that the expression's value can be printed, as a notebook cell
    prints its last value. None otherwise. The place is None when a line
    splice moved it."""
    s = _unsplice(text).strip()
    tokens, _ = lex(s)
    stmts = split_statements(tokens)
    if len(stmts) < 2 or not _open_end(*stmts[-1]):
        return None
    start = stmts[-1][0][0].start
    lead = len(text) - len(text.lstrip())
    return s[:start].rstrip(), s[start:], lead + start if s == text.strip() else None
