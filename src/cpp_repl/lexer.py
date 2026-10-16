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
# via_file is set: then the input goes to clang-repl through a temporary file
# and an #include, which keeps the lines a preprocessor directive needs.
Send = namedtuple('Send', 'text kind via_file')

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
CONDITIONAL_LINE = re.compile(r'^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|elifdef|elifndef|else|endif)\b', re.M)
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


def token_is(tokens, i, text, kind='op'):
    return i < len(tokens) and tokens[i].text == text and tokens[i].kind == kind


def match_bracket(tokens, i):
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


def initialiser_brace(tokens, j, mem_inits):
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
                initialiser = initialiser_brace(tokens, j, mem_inits)
            depth += 1
        elif t.text in ')]}':
            depth -= 1
            if depth == 0 and t.text == ')' and token_is(tokens, j + 1, ':'):
                mem_inits = True
            if depth == 0 and t.text == '}' and not initialiser and j + 1 < len(tokens):
                after = tokens[j + 1]
                if (after.kind == 'ident' and after.text in CONTROL) or token_is(tokens, j + 1, '{'):
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
    if token_is(tokens, i, '{'):
        return match_bracket(tokens, i)
    if t.kind != 'ident' or t.text not in CONTROL:
        return _plain_end(tokens, i)
    j = i + 1
    if t.text in ('for', 'while', 'switch', 'if'):
        if t.text == 'if':
            if token_is(tokens, j, '!'):
                j += 1
            if token_is(tokens, j, 'constexpr', 'ident') or token_is(tokens, j, 'consteval', 'ident'):
                j += 1
        if token_is(tokens, j, '('):
            j = match_bracket(tokens, j) + 1
        if j >= n:
            return n - 1
        end = _stmt_end(tokens, j)
        if t.text == 'if' and token_is(tokens, end + 1, 'else', 'ident'):
            return _stmt_end(tokens, end + 2) if end + 2 < n else n - 1
        return end
    if t.text == 'do':
        end = _stmt_end(tokens, j) if j < n else n - 1
        if token_is(tokens, end + 1, 'while', 'ident'):
            k = end + 2
            if token_is(tokens, k, '('):
                k = match_bracket(tokens, k) + 1
            return k if token_is(tokens, k, ';') else min(k, n) - 1
        return end
    # try
    if not token_is(tokens, j, '{'):
        return _plain_end(tokens, i)
    end = match_bracket(tokens, j)
    while token_is(tokens, end + 1, 'catch', 'ident'):
        k = end + 2
        if token_is(tokens, k, '('):
            k = match_bracket(tokens, k) + 1
        if not token_is(tokens, k, '{'):
            return n - 1
        end = match_bracket(tokens, k)
    return end


def split_statements(tokens):
    """[(tokens, is_control_flow)] for each statement, in order. Directives
    belong to the statement after them, as a #pragma does."""
    out, i, n = [], 0, len(tokens)
    while i < n:
        if token_is(tokens, i, ';'):
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
        control = token_is(tokens, j, '{') or (first.kind == 'ident' and first.text in CONTROL)
        out.append((tokens[i:end + 1], control))
        i = end + 1
    return out


def unsplice(text):
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
    s = unsplice(text).strip()
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
TYPE_WORDS = {'auto', 'int', 'char', 'bool', 'float', 'double', 'void', 'wchar_t', 'char8_t',
               'char16_t', 'char32_t', 'long', 'short', 'unsigned', 'signed', 'const', 'volatile'}
# What ends a lambda's introducer [...].
_AFTER_CAPTURES = {'(', '{', '<', '->'}
_LAMBDA_WORDS = {'mutable', 'constexpr', 'consteval', 'noexcept', 'static'}

def angle_end(tokens, i):
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
                j = match_bracket(tokens, j)
            if depth <= 0:
                return j + 1
        j += 1
    return len(tokens)


def _skip_attributes(tokens, i):
    """Index past the [[...]], alignas(...) and __attribute__((...)) at i."""
    n = len(tokens)
    while i < n:
        if token_is(tokens, i, '[') and token_is(tokens, i + 1, '['):
            i = match_bracket(tokens, i) + 1
        elif (tokens[i].kind == 'ident' and tokens[i].text in ('alignas', '__attribute__', '__declspec')
                and token_is(tokens, i + 1, '(')):
            i = match_bracket(tokens, i + 1) + 1
        else:
            break
    return i


# Words that take an operand or head something other than a declaration, so
# that `sizeof x`, `return x` and `true && false` declare nothing.
_NO_TYPE_WORDS = {'return', 'throw', 'co_return', 'co_yield', 'co_await', 'delete', 'new', 'sizeof', 'alignof',
                  'typeid', 'goto', 'case', 'default', 'else', 'do', 'using', 'namespace', 'template', 'operator',
                  'requires', 'concept', 'static_assert', 'asm', 'true', 'false', 'nullptr', 'this'}


def _declares(tokens, i, last=False):
    """Whether tokens[i] is a declared name: an identifier that =, {, (, [,
    ; or , follows, or nothing at all when last is set (`int w` typed
    without its ';'); or a declarator in parentheses, `(*f)` or `(&r)`, that
    a parameter list or an array bound follows, as in int (*f)(int). A call
    such as f(*p) is none."""
    if token_is(tokens, i, '(') and (token_is(tokens, i + 1, '*') or token_is(tokens, i + 1, '&')):
        after = match_bracket(tokens, i) + 1
        return token_is(tokens, after, '(') or token_is(tokens, after, '[')
    if i >= len(tokens) or tokens[i].kind != 'ident':
        return False
    if i + 1 == len(tokens):
        return last
    return tokens[i + 1].kind == 'op' and tokens[i + 1].text in ('=', '{', '(', '[', ';', ',')


def _declarator_at(tokens, bare=False):
    """Where a statement that looks like a variable declaration declares its
    name: after attributes, specifiers, a type (a keyword, or a name with ::
    and <...>) and * and &, the index of the name that =, {, (, [, ; or a
    comma follows (or that ends the statement, after a type), of the [ of a
    structured binding `auto [a, b]`, or of the ( of a declarator in
    parentheses `int (*f)(int)`. None when the statement is no declaration:
    a subscript v[0], a call f(*p), a product m * m.

    bare says the statement has no ';', so a value may be wanted: then
    `n * v[i]`, a plain name, a * or &, and a subscript or a call, is the
    expression it usually is. With a ';' the same shape is `Foo *arr[3];`, a
    declaration, which must stay at file scope.

    Directives are left out first, so the index is into the tokens without
    them."""
    tokens = [t for t in tokens if t.kind != 'directive']
    i, n = _skip_attributes(tokens, 0), len(tokens)
    typed, type_head = False, False   # the specifiers hold a type word (`unsigned long n` needs no other); a struct/class/enum
    while i < n and tokens[i].kind == 'ident' and tokens[i].text in _SPECIFIERS:
        typed = typed or tokens[i].text in TYPE_WORDS
        type_head = type_head or tokens[i].text in _TYPE_HEADS
        i += 1
    keyword, qualified, is_auto = typed, False, False   # what the type is made of
    if i < n and tokens[i].kind == 'ident' and not (typed and _declares(tokens, i, last=True)):
        if tokens[i].text in _NO_TYPE_WORDS:
            return None
        keyword = keyword or tokens[i].text in TYPE_WORDS or tokens[i].text == 'decltype'
        is_auto = tokens[i].text == 'auto'
        if tokens[i].text == 'decltype' and token_is(tokens, i + 1, '('):
            i = match_bracket(tokens, i + 1) + 1
        else:
            i += 1
        while i < n:
            if token_is(tokens, i, '::') and i + 1 < n and tokens[i + 1].kind == 'ident':
                i, qualified = i + 2, True
            elif token_is(tokens, i, '<'):
                i, qualified = angle_end(tokens, i), True
            elif tokens[i].kind == 'ident' and tokens[i].text in TYPE_WORDS:
                i, keyword = i + 1, True
            else:
                break
    elif not typed:
        return None
    pointer = False   # a *, & or && came between the type and the name
    while i < n and (tokens[i].text in ('*', '&', '&&') and tokens[i].kind == 'op'
                     or tokens[i].kind == 'ident' and tokens[i].text in ('const', 'volatile')):
        pointer = pointer or tokens[i].kind == 'op'
        i += 1
    if token_is(tokens, i, '['):   # a structured binding, auto [a, b] = ...; else a subscript
        return i if is_auto else None
    # A name that ends the statement is declared after a type: `int w`,
    # `std::string s`, `Foo f`; `m * m` and `a & b` are expressions, and
    # `class C final` heads a definition.
    if type_head and i + 1 == n and token_is(tokens, i, 'final', 'ident'):
        return None
    if not _declares(tokens, i, last=keyword or qualified or not pointer):
        return None
    if bare and pointer and not (keyword or qualified) and tokens[i + 1].text in ('[', '('):
        return None   # n * v[i], a & b(0): see the docstring
    return i


def _declaration(tokens, bare=False):
    """Whether a statement looks like a variable declaration (see
    _declarator_at)."""
    return _declarator_at(tokens, bare) is not None


def _has_body_or_end(tokens):
    """Whether a statement holds its ';' or the '{' of its body at depth 0.
    The braces of a requires-expression are no body."""
    i, depth, n = 0, 0, len(tokens)
    while i < n:
        t = tokens[i]
        if t.kind == 'ident' and t.text == 'requires' and depth == 0:
            j = i + 1
            if token_is(tokens, j, '('):
                j = match_bracket(tokens, j) + 1
            i = match_bracket(tokens, j) + 1 if token_is(tokens, j, '{') else i + 1
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
    if k is None or not token_is(tokens, k + 1, '('):
        return False
    close = match_bracket(tokens, k + 1)
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
    if i == len(code) or token_is(code, i, 'template', 'ident'):
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
        if not token_is(tokens, i, '['):
            continue
        if i > 0:
            before = tokens[i - 1]
            if (before.kind in ('ident', 'num', 'str', 'chr', 'raw') and before.text != 'return'
                    or before.kind == 'op' and before.text in (')', ']')):
                continue
        close = match_bracket(tokens, i)
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


def in_function(name, body, timed=True, exceptions=True):
    """body as the body of a new function, and a call of it. The function
    returns repl::ended, which an int converts to: `return 1;` inside body,
    as in main()'s `if (bad) return 1;`, becomes the program's exit status,
    an exception that leaves body is named and ends the program as abort()
    would, and body falling off its end returns nothing. repl::timed() times
    the call, unless timed is off, and repl::finish() reports the run and
    the end to the session; repl::started() marks where the statement's own
    output begins, after the compiler's. Compiled with -fno-exceptions
    (exceptions off), the body has no handler: a try would not compile."""
    call = f'repl::timed({name})' if timed else f'{name}()'
    inside = f'try {{ {body} }} catch (...) {{ return repl::uncaught(); }}' if exceptions else f'{{ {body} }}'
    return f'repl::ended {name}() {{ repl::started(); {inside} return {{}}; }} repl::finish({call});'


def _end(toks):
    return toks[-1].start + len(toks[-1].text)


def transform(text, wrap=True, fresh=None, origin=None, as_statement=False, exceptions=True):
    """What to send to clang-repl for one input, as a Send whose kind is 'pp',
    'stmt', 'expr' or 'empty'. A statement that cannot run at top level (see
    needs_function) runs as the body of a new function named fresh()
    (cpp_repl_1, cpp_repl_2, ... for this call, when no fresh is given);
    wrap=False leaves every statement as it is, for code that goes inside a
    function anyway, and exceptions says whether the function may catch (see
    in_function). A block of preprocessor lines (an #if region), or code
    holding a directive other than a #pragma, goes through a file. With
    as_statement set, a bare expression is sent as a statement.

    With an origin (see Origin), code and #if regions go through a file in
    which they sit at their source's lines and columns. An input with a line
    splice outside a directive goes as one line, as without an origin."""
    s = unsplice(text).strip()
    if fresh is None:
        names = itertools.count(1)

        def fresh():
            return f'cpp_repl_{next(names)}'
    if origin is not None:
        origin = located(origin, text, len(text) - len(text.lstrip())) if s == text.strip() else None
    if CONDITIONAL_LINE.match(s):
        return Send(_place(s, 0, len(s), origin) if origin else s, 'pp', True)   # an #if region
    tokens, _ = lex(s)
    if tokens and all(t.kind == 'directive' for t in tokens):
        if origin:   # placed, so that an #include that fails names its line
            return Send(_place(s, 0, len(s), origin), 'pp', True)
        return Send(s, 'pp', len(tokens) > 1)
    if not tokens:
        return Send('', 'empty', False)
    stmts = split_statements(tokens)
    if not stmts:
        return Send('', 'empty', False)
    lines = needs_lines(tokens)
    if len(stmts) == 1 and open_end(*stmts[0]) and _statement_shaped(stmts[0][0]):
        as_statement = True   # `int x = 5` without its ';': a statement, with no value to print
    if not as_statement and not lines and len(stmts) == 1 and open_end(*stmts[0]):
        toks = stmts[0][0]
        if origin:
            # The caller writes the expression into a call: its first line
            # has to start after the #line directive.
            return Send('\n' + _place(s, toks[0].start, _end(toks), origin), 'expr', True)
        return Send(join(toks), 'expr', False)
    parts = []
    for toks, control in stmts:
        semicolon = as_statement and open_end(toks, control)
        if origin:
            line = _place(s, toks[0].start, _end(toks), origin) + (';' if semicolon else '')
        else:
            line = join(toks, lines) + (';' if semicolon else '')
        complete = control or semicolon or token_is(toks, len(toks) - 1, ';')
        if wrap and complete and needs_function(toks, control):
            # Placed, the function's head and tail keep lines of their own,
            # out of the source lines a diagnostic shows.
            line = in_function(fresh(), f'\n{line}\n' if origin else line, exceptions=exceptions)
        parts.append(line)
    return Send(('\n' if origin else ' ').join(parts), 'stmt', lines or origin is not None)


def _statement_shaped(toks):
    """Whether an input without its ';' is a statement rather than an
    expression with a value to print: a declaration (`int x = 5`,
    `std::vector<int> v{1, 2}`), a return or a throw."""
    code = [t for t in toks if t.kind != 'directive']
    if not code:
        return False
    return _declaration(code, bare=True) or (code[0].kind == 'ident' and code[0].text in ('return', 'throw'))


_DEFINITION_HEADS = {'struct', 'class', 'union', 'enum', 'template', 'typedef', 'namespace', 'using',
                     'extern', 'concept'}
# What may stand between a function's parameter list and its body's '{'.
_BEFORE_BODY = {')', 'const', 'volatile', 'override', 'final', 'noexcept', '&', '&&', 'try'}


def _skip_initialisers(tokens, i):
    """Index past a constructor's member initialisers starting at i, after
    the ':': `x(1), y{2}, Base<T>(args)...`, up to the body's '{'."""
    n = len(tokens)
    while i < n:
        while i < n and (tokens[i].kind == 'ident' or token_is(tokens, i, '::')):
            i += 1
        if token_is(tokens, i, '<'):
            i = angle_end(tokens, i)
        if not (token_is(tokens, i, '(') or token_is(tokens, i, '{')):
            break
        i = match_bracket(tokens, i) + 1
        if token_is(tokens, i, '...'):
            i += 1
        if not token_is(tokens, i, ','):
            break
        i += 1
    return i


def _definition(tokens):
    """is_definition() for an input already lexed."""
    tokens = [t for t in tokens if t.kind != 'directive']
    i, n = _skip_attributes(tokens, 0), len(tokens)
    if i >= n or token_is(tokens, i, '['):   # nothing, or a lambda
        return False
    while i < n and tokens[i].kind == 'ident' and tokens[i].text in ('inline', 'static', 'constexpr', 'consteval',
                                                                     'constinit', 'export'):
        i += 1
    head = tokens[i].text if i < n else ''
    if head in _DEFINITION_HEADS:
        return True
    if head in CONTROL or not token_is(tokens, n - 1, '}'):
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
                    return token_is(tokens, _skip_initialisers(tokens, i + 1), '{')
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
    tokens, _ = lex(unsplice(text))
    return _definition(tokens)


_DECLARATOR_ENDS = ('=', '{', '(', '[', ';', ',')


def _declarators(tokens, i):
    """The names declared from i on: `a`, then after each ',' at depth 0 and
    any * or &, the next name."""
    names, n = [], len(tokens)
    while i < n:
        while i < n and tokens[i].kind == 'op' and tokens[i].text in ('*', '&', '&&'):
            i += 1
        if i < n and tokens[i].kind == 'ident' and (i + 1 == n or tokens[i + 1].kind == 'op'
                                                      and tokens[i + 1].text in _DECLARATOR_ENDS):
            names.append(tokens[i].text)
        depth = 0
        while i < n:   # to the next ',' at depth 0, or the end
            t = tokens[i]
            if t.kind == 'op':
                if t.text in '([{':
                    depth += 1
                elif t.text in ')]}':
                    depth -= 1
                elif t.text == ',' and depth == 0:
                    i += 1
                    break
                elif t.text == ';' and depth == 0:
                    return names
            i += 1
    return names


def _after_type_head(tokens, i):
    """The names a struct, class, union or enum at i declares: its own, an
    unscoped enum's enumerators, and any declarator after the body."""
    n, head = len(tokens), tokens[i].text
    scoped = head == 'enum' and (token_is(tokens, i + 1, 'class', 'ident') or token_is(tokens, i + 1, 'struct', 'ident'))
    j = _skip_attributes(tokens, i + 1 + scoped)
    names = [tokens[j].text] if j < n and tokens[j].kind == 'ident' else []
    body = next((k for k in range(j, n) if token_is(tokens, k, '{')), None)
    if body is None:
        return names
    end = match_bracket(tokens, body)
    if head == 'enum' and not scoped:
        names += [t.text for k, t in enumerate(tokens[body + 1:end], body + 1) if t.kind == 'ident'
                  and (k == body + 1 or token_is(tokens, k - 1, ','))]
    return names + _declarators(tokens, end + 1)


def declared_names(tokens):
    """The names an input declares at file scope: variables (every
    declarator of `int a, *b;`, a structured binding's names), functions
    (an operator's as `operator<<`), types and their unscoped enumerators,
    namespaces, aliases and concepts. For a name clang calls redefined, the
    newest input that declared it is the one a redefinition replaces."""
    code = [t for t in tokens if t.kind != 'directive']
    n = len(code)
    i = _skip_attributes(code, 0)
    if token_is(code, i, 'template', 'ident') and token_is(code, i + 1, '<'):
        i = angle_end(code, i + 1)
        if token_is(code, i, 'requires', 'ident'):
            i = next((k for k in range(i + 1, n) if code[k].kind == 'ident' and (
                code[k].text in _DEFINITION_HEADS | TYPE_WORDS or _declarator_at(code[k:]) is not None)), n)
    while token_is(code, i, 'extern', 'ident') and i + 1 < n and code[i + 1].kind == 'str':
        i += 2
    while i < n and code[i].kind == 'ident' and code[i].text in ('inline', 'static', 'constexpr', 'consteval',
                                                                 'constinit', 'export', 'friend'):
        i += 1
    if i >= n:
        return []
    head = code[i].text if code[i].kind == 'ident' else ''
    if head in ('struct', 'class', 'union', 'enum'):
        return _after_type_head(code, i)
    if head in ('namespace', 'concept'):
        return [code[i + 1].text] if i + 1 < n and code[i + 1].kind == 'ident' else []
    if head == 'using':
        if token_is(code, i + 1, 'namespace', 'ident'):
            return []
        names = [t.text for k, t in enumerate(code[i + 1:], i + 1) if t.kind == 'ident'
                 and (token_is(code, k + 1, '=') or token_is(code, k + 1, ';'))]
        return names[:1]
    if head == 'typedef':
        last = next((t.text for t in reversed(code[i:]) if t.kind == 'ident'), None)
        return [last] if last else []
    for k, t in enumerate(code):
        if t.kind == 'ident' and t.text == 'operator' and k + 1 < n:
            end = next((m for m in range(k + 1, n) if token_is(code, m, '(')), n)
            return ['operator' + ''.join(x.text for x in code[k + 1:end])]
    k = _declarator_at(code)
    if k is not None:
        if token_is(code, k, '['):
            return [t.text for t in code[k + 1:match_bracket(code, k)] if t.kind == 'ident']
        if token_is(code, k, '('):   # int (*fp)(int)
            return [t.text for t in code[k + 1:match_bracket(code, k)] if t.kind == 'ident'][:1]
        return _declarators(code, k)
    # A function defined under a qualified name, `int S::f() {}`, or a
    # constructor `S::S() {}`: the name before its parameter list.
    if not _definition(code):
        return []
    depth = 0
    for k, t in enumerate(code):
        if t.kind != 'op':
            continue
        if t.text == '(' and depth == 0 and k and code[k - 1].kind == 'ident':
            return [code[k - 1].text]
        if t.text in '([{':
            depth += 1
        elif t.text in ')]}':
            depth -= 1
    return []


def open_end(toks, control):
    """Whether a statement ends without its ';', as a bare expression does.
    A '}' ends a block or a definition, but a '}' that closes a brace
    initialiser, as in std::vector<int>{1, 2}, ends an expression."""
    if control or token_is(toks, len(toks) - 1, ';') or toks[-1].kind == 'directive':
        return False
    return not token_is(toks, len(toks) - 1, '}') or not _definition(toks)


def trailing_expression(text):
    """For several statements ending in a bare expression, as a pasted block
    might: (the statements, the expression, where the expression starts in
    text), so that the expression's value can be printed, as a notebook cell
    prints its last value. None otherwise. The place is None when a line
    splice moved it."""
    s = unsplice(text).strip()
    tokens, _ = lex(s)
    stmts = split_statements(tokens)
    if len(stmts) < 2 or not open_end(*stmts[-1]):
        return None
    start = stmts[-1][0][0].start
    lead = len(text) - len(text.lstrip())
    return s[:start].rstrip(), s[start:], lead + start if s == text.strip() else None
