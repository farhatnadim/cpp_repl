"""When an input is complete, how it splits into statements, and what is sent
for it. No clang-repl needed."""

import pytest

import itertools

from cpp_repl.lexer import (completeness, in_function, is_definition, lex, needs_function, needs_lines,
                            open_conditionals, split_statements, trailing_expression, transform)


def fresh():
    names = itertools.count(1)
    return lambda: f'f{next(names)}'


@pytest.mark.parametrize('text, state', [
    ('int x = 1;', 'complete'),
    ('x', 'complete'),
    ('x +', 'incomplete'),
    ('f(1,', 'incomplete'),
    ('auto v = std::vector<int>{1,', 'incomplete'),
    ('std::vector<int>{1, 2}', 'complete'),
    ('for (int i = 0; i < 3; ++i) {', 'incomplete'),
    ('for (int i = 0; i < 3; ++i) {\n  f(i);\n}', 'complete'),
    ('if (x) {\n  f();\n}', 'extendable'),
    ('if (x) { f(); }', 'complete'),
    ('if (x) {\n  f();\n} else {\n  g();\n}', 'extendable'),
    ('if (x)', 'incomplete'),
    ('try {\n  f();\n}', 'incomplete'),
    ('try {\n  f();\n} catch (...) {\n}', 'extendable'),
    ('do {\n  f();\n}', 'incomplete'),
    ('do { f(); } while (x);', 'complete'),
    ('do { f(); } while (x)', 'incomplete'),
    ('/* open comment', 'incomplete'),
    ('int a = 1; // a comment {', 'complete'),
    ('auto s = "a { brace";', 'complete'),
    ("char c = '{';", 'complete'),
    ('auto r = R"(a { raw\nstring)";', 'complete'),
    ('auto r = R"x(a )" raw\n)x";', 'complete'),
    ('int big = 1\'000\'000;', 'complete'),
    ('#include <vector>', 'complete'),
    ('#define TWICE(x) \\', 'incomplete'),
    ('#ifdef FOO', 'incomplete'),
    ('#ifdef FOO\nint x;\n#endif', 'complete'),
    ('#if A\n#if B\n#endif', 'incomplete'),
    ('#pragma omp parallel for', 'incomplete'),
    ('#pragma GCC diagnostic ignored "-Wunused"', 'complete'),
    ('#pragma once', 'complete'),
    ('%lib /x.so', 'complete'),
    ('', 'complete'),
    ('struct S {', 'incomplete'),
    ('struct S { int a; };', 'complete'),
    ('int f() {\n  return 1;\n}', 'complete'),
])
def test_completeness(text, state):
    assert completeness(text) == state


@pytest.mark.parametrize('text, kind, sent', [
    ('int x = 1;', 'stmt', 'int x = 1;'),
    ('x', 'expr', 'x'),
    ('std::vector<int>{1, 2}', 'expr', 'std::vector<int>{1, 2}'),
    ('std::vector<int>{1, 2};', 'stmt', 'std::vector<int>{1, 2};'),
    ('std::map<int, int> m{{1, 2}}', 'expr', 'std::map<int, int> m{{1, 2}}'),
    ('for (auto p : v) {\n  f(p);\n}', 'stmt', in_function('f1', 'for (auto p : v) { f(p); }')),
    ('try { f(); } catch (...) { g(); }', 'stmt', in_function('f1', 'try { f(); } catch (...) { g(); }')),
    ('if (x) f(); else g();', 'stmt', in_function('f1', 'if (x) f(); else g();')),
    ('int a = 1; for (;;) {} for (;;) {}', 'stmt',
     'int a = 1; ' + in_function('f1', 'for (;;) {}') + ' ' + in_function('f2', 'for (;;) {}')),
    ('std::sort(v.begin(), v.end(), [](int a, int b) { return a > b; });', 'stmt',
     in_function('f1', 'std::sort(v.begin(), v.end(), [](int a, int b) { return a > b; });')),
    ('auto g = [](int x) { return x; };', 'stmt', 'auto g = [](int x) { return x; };'),
    ('int n = count(v, [](int x) { return x > 1; });', 'stmt', 'int n = count(v, [](int x) { return x > 1; });'),
    ('x = arr[i](3);', 'stmt', 'x = arr[i](3);'),
    ('int a = 1; a', 'stmt', 'int a = 1; a'),
    ('#include <vector>', 'pp', '#include <vector>'),
    ('#define TWICE(x) \\\n  ((x) * 2)', 'pp', '#define TWICE(x)    ((x) * 2)'),
    ('int f() { return 1; }', 'stmt', 'int f() { return 1; }'),
    ('auto s = R"(a\nb)";', 'stmt', 'auto s = "a\\nb";'),
    ('#pragma omp parallel for\nfor (int i = 0; i < 4; ++i) f(i);',
     'stmt', in_function('f1', '_Pragma("omp parallel for") for (int i = 0; i < 4; ++i) f(i);')),
    ('#pragma pack(push, 1)\nstruct S { char c; int i; };',
     'stmt', '_Pragma("pack(push, 1)") struct S { char c; int i; };'),
    ('#pragma message("a \\"quoted\\" word")\nint y;',
     'stmt', '_Pragma("message(\\"a \\\\\\"quoted\\\\\\" word\\")") int y;'),
    ('   ', 'empty', ''),
    ('// just a comment', 'empty', ''),
])
def test_transform(text, kind, sent):
    send = transform(text, fresh=fresh())
    assert (send.kind, send.text, send.file) == (kind, sent, False)


def test_in_function():
    """`return 1;` from a main() body leaves the function a statement runs in:
    the diagnostic for a value returned from a void function is off for that
    function alone."""
    assert in_function('f1', 'if (x) return 1;') == (
        '_Pragma("clang diagnostic push") _Pragma("clang diagnostic ignored \\"-Wreturn-type\\"") '
        'void f1() { if (x) return 1; } _Pragma("clang diagnostic pop") f1();')


def test_regions_and_block_directives_go_through_a_file():
    region = '#ifdef FOO\nint foo = 1;\n#else\nint foo = 2;\n#endif'
    assert transform(region) == (region, 'pp', True)
    block = 'for (int i = 0; i < 2; ++i) {\n#ifdef X\n  f(i);\n#endif\n}'
    send = transform(block, fresh=fresh())
    assert send.kind == 'stmt' and send.file
    assert '\n#ifdef X\n' in send.text and '\n#endif\n' in send.text
    assert 'void f1() { for' in send.text


def test_transform_without_wrapping():
    assert transform('for (int i = 0; i < 3; ++i) s += i;', wrap=False).text == \
        'for (int i = 0; i < 3; ++i) s += i;'


@pytest.mark.parametrize('text, expected', [
    ('int a = 1; int b = 2;', [(False, 'int a = 1;'), (False, 'int b = 2;')]),
    ('for (;;) {} x;', [(True, 'for (;;) {}'), (False, 'x;')]),
    ('if (a) {} else if (b) {} else {} y;', [(True, 'if (a) {} else if (b) {} else {}'), (False, 'y;')]),
    ('do {} while (0); z;', [(True, 'do {} while (0);'), (False, 'z;')]),
    ('{ a(); } b();', [(True, '{ a(); }'), (False, 'b();')]),
])
def test_split_statements(text, expected):
    tokens, _ = lex(text)
    got = [(control, ' '.join(t.text for t in toks)) for toks, control in split_statements(tokens)]
    want = [(control, ' '.join(t.text for t in lex(s)[0])) for control, s in expected]
    assert got == want


def test_directives_belong_to_the_next_statement():
    tokens, _ = lex('#pragma unroll\nfor (;;) {}\n#pragma once')
    stmts = split_statements(tokens)
    assert [control for _, control in stmts] == [True, False]
    assert stmts[0][0][0].kind == 'directive'
    assert needs_lines(tokens) is False
    assert needs_lines(lex('{\n#ifdef X\n#endif\n}')[0]) is True


@pytest.mark.parametrize('text, definition', [
    ('struct S { int a; };', True),
    ('template <class T> T twice(T x) { return 2 * x; }', True),
    ('namespace n { int k; }', True),
    ('int f() { return 1; }', True),
    ('auto g() -> int { return 2; }', True),
    ('void h() const {}', True),
    ('int x = 1;', False),
    ('std::vector<int>{1, 2}', False),
    ('auto l = [] { return 1; };', False),
    ('for (;;) {}', False),
])
def test_is_definition(text, definition):
    assert is_definition(text) is definition


def test_trailing_expression():
    assert trailing_expression('int a = 1;\na') == ('int a = 1;', 'a')
    assert trailing_expression('int a = 1;\na + 1;') is None
    assert trailing_expression('a') is None
    assert trailing_expression('int a = 1;\nstd::vector<int>{a, 2}') == ('int a = 1;', 'std::vector<int>{a, 2}')


def test_open_conditionals():
    assert open_conditionals('#if A\n#ifdef B\n#endif') == 1
    assert open_conditionals('#ifndef A\n#else\n#endif') == 0
    assert open_conditionals('int x;  // a #if after code is no directive') == 0


@pytest.mark.parametrize('text, wrapped', [
    ('std::for_each(v.begin(), v.end(), [&](int x) { s += x; });', True),
    ('cb = [] { return 1; };', True),
    ('auto [a, b] = f([] { return 1; });', False),
    ('std::function<int(int)> h = [](int x) { return x; };', False),
    ('const auto &r = pick(v, [](int x) { return x; });', False),
    ('std::vector<std::pair<int, int>> w = make([] { return 1; });', False),
    ('[[maybe_unused]] int q = 1;', False),
    ('m[k] = 2;', False),
    ('f(m[k]);', False),
])
def test_needs_function(text, wrapped):
    tokens, _ = lex(text)
    (toks, control), = split_statements(tokens)
    assert needs_function(toks, control) is wrapped
