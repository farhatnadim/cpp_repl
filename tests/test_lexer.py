"""When an input is complete, how it splits into statements, and what is sent
for it. No clang-repl needed."""

import pytest

import itertools

from cpp_repl.lexer import (Origin, completeness, declared_names, in_function, is_definition, lex, located,
                            needs_function, needs_lines, open_conditionals, split_statements, trailing_expression,
                            transform)


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
    # Braces or not, a multi-line if waits for its else; a one-line one runs.
    ('if (x)\n  f();', 'extendable'),
    ('if (x)\n  f();\nelse\n  g();', 'extendable'),
    ('if (x) f();', 'complete'),
    ('for (int i = 0; i < 3; ++i)\n  f(i);', 'complete'),
    ('while (x)\n  f();', 'complete'),
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
    ('const char *glsl = R"(', 'incomplete'),
    ('const char *glsl = R"(\n#version 330 core', 'incomplete'),
    ('const char *glsl = R"(\n#version 330 core\nvoid main() {}\n)";', 'complete'),
    ('auto s = R"(\n#if 0\n)";', 'complete'),
    ('auto s = u8R"x(\n#endif\n)x";', 'complete'),
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
    # The heads of what goes on on the next line, as C++ is written.
    ('template <class T>', 'incomplete'),
    ('template <class T, int N = 3>', 'incomplete'),
    ('template <class T> requires Num<T>', 'incomplete'),
    ('template <class T> requires requires (T t) { t.f(); }', 'incomplete'),
    ('template <class T>\nT twice(T x) { return x + x; }', 'complete'),
    ('template <class T> using V = std::vector<T>;', 'complete'),
    ('template <class T> struct S;', 'complete'),
    ('[[nodiscard]]', 'incomplete'),
    ('[[nodiscard]] int g() { return 2; }', 'complete'),
    ('[[nodiscard]]\nint h() { return 3; }', 'complete'),
    ('struct S', 'incomplete'),
    ('struct S : Base', 'incomplete'),
    ('class C final', 'incomplete'),
    ('enum class E : int', 'incomplete'),
    ('namespace N', 'incomplete'),
    ('struct S;', 'complete'),
    ('struct S s;', 'complete'),
    ('namespace fs = std::filesystem;', 'complete'),
    ('struct S\n{\n  int x = 1;\n};', 'complete'),
    ('int f()', 'extendable'),
    ('int main()', 'extendable'),
    ('static int f(int x) const noexcept', 'extendable'),
    ('auto f() -> int', 'extendable'),
    ('[[nodiscard]] constexpr int f(int x)', 'extendable'),
    ('T f(T x) requires C<T>', 'extendable'),
    ('int f()\n{\n  return 1;\n}', 'complete'),
    ('int f() = delete', 'complete'),
    ('f()', 'complete'),
    ('ns::f(1)', 'complete'),
    ("std::string(5, 'a')", 'complete'),
    ('a < b', 'complete'),
    ('f<int>(1)', 'complete'),
    ('if consteval { } else { }', 'complete'),
    ('if consteval {\n}', 'extendable'),
    ('if !consteval { }', 'complete'),
])
def test_completeness(text, state):
    assert completeness(text) == state


@pytest.mark.parametrize('text, kind, sent', [
    ('int x = 1;', 'stmt', 'int x = 1;'),
    ('x', 'expr', 'x'),
    ('std::vector<int>{1, 2}', 'expr', 'std::vector<int>{1, 2}'),
    ('std::vector<int>{1, 2};', 'stmt', in_function('f1', 'std::vector<int>{1, 2};')),
    # A declaration without its ';' is a statement, not a value to print.
    ('std::map<int, int> m{{1, 2}}', 'stmt', 'std::map<int, int> m{{1, 2}};'),
    ('int x = 5', 'stmt', 'int x = 5;'),
    ('enum E e = E::a', 'stmt', 'enum E e = E::a;'),
    ('auto f = [](int a) { return a; }', 'stmt', 'auto f = [](int a) { return a; };'),
    ('return x', 'stmt', in_function('f1', 'return x;')),
    ('x = 5', 'expr', 'x = 5'),
    # A declaration with nothing after its name is one too; what only looks
    # like one (a subscript, a call with *p or &x, a product) has a value.
    ('int w', 'stmt', 'int w;'),
    ('int *p', 'stmt', 'int *p;'),
    ('std::string s', 'stmt', 'std::string s;'),
    ('std::vector<int> v', 'stmt', 'std::vector<int> v;'),
    ('Foo f', 'stmt', 'Foo f;'),
    ('unsigned long n', 'stmt', 'unsigned long n;'),
    ('int (*fp)(int) = nullptr', 'stmt', 'int (*fp)(int) = nullptr;'),
    ('auto [a, b] = p', 'stmt', 'auto [a, b] = p;'),
    ('const auto &[c, d] = q', 'stmt', 'const auto &[c, d] = q;'),
    ('v[0]', 'expr', 'v[0]'),
    ('m["k"]', 'expr', 'm["k"]'),
    ('grid[1][2]', 'expr', 'grid[1][2]'),
    ('x[0] = 5', 'expr', 'x[0] = 5'),
    ('f(*p)', 'expr', 'f(*p)'),
    ('g(&x)', 'expr', 'g(&x)'),
    ('std::abs(*p)', 'expr', 'std::abs(*p)'),
    ('n * v[i]', 'expr', 'n * v[i]'),
    ('a & b[0]', 'expr', 'a & b[0]'),
    ('n * f(i)', 'expr', 'n * f(i)'),
    ('m * m', 'expr', 'm * m'),
    ('a & b', 'expr', 'a & b'),
    ('true && false', 'expr', 'true && false'),
    ('sizeof x', 'expr', 'sizeof x'),
    ('Foo *q', 'expr', 'Foo *q'),   # as likely a product: the statement runs after the expression fails
    ('[](int x) { return x; }', 'expr', '[](int x) { return x; }'),
    ('S::S() : x{1} { }', 'stmt', 'S::S() : x{1} { }'),
    ('for (auto p : v) {\n  f(p);\n}', 'stmt', in_function('f1', 'for (auto p : v) { f(p); }')),
    ('try { f(); } catch (...) { g(); }', 'stmt', in_function('f1', 'try { f(); } catch (...) { g(); }')),
    ('if (x) f(); else g();', 'stmt', in_function('f1', 'if (x) f(); else g();')),
    ('int a = 1; for (;;) {} for (;;) {}', 'stmt',
     'int a = 1; ' + in_function('f1', 'for (;;) {}') + ' ' + in_function('f2', 'for (;;) {}')),
    ('std::sort(v.begin(), v.end(), [](int a, int b) { return a > b; });', 'stmt',
     in_function('f1', 'std::sort(v.begin(), v.end(), [](int a, int b) { return a > b; });')),
    ('auto g = [](int x) { return x; };', 'stmt', 'auto g = [](int x) { return x; };'),
    ('int n = count(v, [](int x) { return x > 1; });', 'stmt', 'int n = count(v, [](int x) { return x > 1; });'),
    ('x = arr[i](3);', 'stmt', in_function('f1', 'x = arr[i](3);')),
    ('int a = 1; a', 'stmt', 'int a = 1; a'),
    ('return run();', 'stmt', in_function('f1', 'return run();')),
    ('#pragma unroll\nreturn 0;', 'stmt', in_function('f1', '_Pragma("unroll") return 0;')),
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
    assert (send.kind, send.text, send.via_file) == (kind, sent, False)


def test_in_function():
    """`return 1;` from a main() body becomes the program's exit status: the
    function a statement runs in returns repl::ended, which an int converts
    to, and falls off its end otherwise. An exception that leaves the
    statement is caught and named. The call is timed, unless the caller
    times the statement itself, as %timeit does."""
    assert in_function('f1', 'if (x) return 1;') == (
        'repl::ended f1() { repl::started(); try { if (x) return 1; } catch (...) { return repl::uncaught(); } '
        'return {}; } repl::finish(repl::timed(f1));')
    assert in_function('f2', 'g();', timed=False).endswith(' repl::finish(f2(), false);')   # no run mark
    # Compiled with -fno-exceptions, a try would not compile: no handler.
    assert in_function('f3', 'g();', exceptions=False) == (
        'repl::ended f3() { repl::started(); { g(); } return {}; } repl::finish(repl::timed(f3));')
    assert transform('g();', fresh=fresh(), exceptions=False).text == in_function('f1', 'g();', exceptions=False)


def test_regions_and_block_directives_go_through_a_file():
    region = '#ifdef FOO\nint foo = 1;\n#else\nint foo = 2;\n#endif'
    assert transform(region) == (region, 'pp', True)
    block = 'for (int i = 0; i < 2; ++i) {\n#ifdef X\n  f(i);\n#endif\n}'
    send = transform(block, fresh=fresh())
    assert send.kind == 'stmt' and send.via_file
    assert '\n#ifdef X\n' in send.text and '\n#endif\n' in send.text
    assert 'repl::ended f1() { repl::started(); try { for' in send.text


def test_transform_without_wrapping():
    assert transform('for (int i = 0; i < 3; ++i) s += i;', wrap=False).text == \
        'for (int i = 0; i < 3; ++i) s += i;'


@pytest.mark.parametrize('text, expected', [
    ('int a = 1; int b = 2;', [(False, 'int a = 1;'), (False, 'int b = 2;')]),
    ('for (;;) {} x;', [(True, 'for (;;) {}'), (False, 'x;')]),
    ('if (a) {} else if (b) {} else {} y;', [(True, 'if (a) {} else if (b) {} else {}'), (False, 'y;')]),
    ('do {} while (0); z;', [(True, 'do {} while (0);'), (False, 'z;')]),
    ('{ a(); } b();', [(True, '{ a(); }'), (False, 'b();')]),
    ('S::S() : x{1}, y{2} { } for (;;) {}', [(False, 'S::S() : x{1}, y{2} { }'), (True, 'for (;;) {}')]),
    ('void f() { } { a(); }', [(False, 'void f() { }'), (True, '{ a(); }')]),
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
    # The shapes a function definition takes.
    ('S::S() : x{1} { }', True),
    ('S::S(int v) : Base<T>(v)..., x_{v}, y(v) { }', True),
    ('void f() requires Num<int> { }', True),
    ('template <class T> requires Num<T> T f(T x) { return x; }', True),
    ('int f() try { return 1; } catch (...) { return 2; }', True),
    ('auto f() -> std::vector<int> { return {}; }', True),
    ('int f() const & noexcept(true) { return 1; }', True),
    ('constexpr long double operator""_kg(long double x) { return x * 1000; }', True),
    ('inline namespace v1 { int k; }', True),
    ('static void f() { }', True),
    ('[[nodiscard]] int f() { return 1; }', True),
    ('template <class T> concept Num = std::is_arithmetic_v<T>;', True),
    # What is not one, though it ends in '}'.
    ('[](int x) { return x; }', False),
    ('[]() mutable { }', False),
    ('std::array<int, (N > 2)> arr{1}', False),
    ('S s{[](int x) { return x; }}', False),
    ('S::S() = default;', False),
    ('int f(), g();', False),
])
def test_is_definition(text, definition):
    assert is_definition(text) is definition


@pytest.mark.parametrize('text, names', [
    ('int x = 5;', ['x']),
    ('int a, *b = &a, c[3];', ['a', 'b', 'c']),
    ('auto [p, q] = f();', ['p', 'q']),
    ('int (*fp)(int) = f;', ['fp']),
    ('static const int N = 3;', ['N']),
    ('[[maybe_unused]] int q = 1;', ['q']),
    ('int f(int);', ['f']),
    ('int f() { return 1; }', ['f']),
    ('int S::f() const { return 1; }', ['f']),
    ('S::S() : x{1} { }', ['S']),
    ('extern "C" int cfn() { return 9; }', ['cfn']),
    ('std::ostream &operator<<(std::ostream &os, const P &p) { return os; }', ['operator<<']),
    ('constexpr long double operator""_kg(long double x) { return x; }', ['operator""_kg']),
    ('struct S { int x; };', ['S']),
    ('struct S { } s, *t;', ['S', 's', 't']),
    ('enum E { A, B = 3 };', ['E', 'A', 'B']),
    ('enum class C { A };', ['C']),
    ('namespace N { int k; }', ['N']),
    ('namespace fs = std::filesystem;', ['fs']),
    ('using V = std::vector<int>;', ['V']),
    ('using std::cout;', ['cout']),
    ('using namespace std;', []),
    ('typedef unsigned long ulong;', ['ulong']),
    ('template <class T> T twice(T x) { return x + x; }', ['twice']),
    ('template <class T> requires Num<T> T cube(T x) { return x * x * x; }', ['cube']),
    ('template <class T> struct Box { T v; };', ['Box']),
    ('template <class T> concept Num = std::is_arithmetic_v<T>;', ['Num']),
    ('x = 5;', []),
    ('f(1);', []),
    ('std::cout << x;', []),
    ('#pragma once\nint y;', ['y']),
])
def test_declared_names(text, names):
    assert declared_names(lex(text)[0]) == names


@pytest.mark.parametrize('text, names, defaults, rewritten', [
    ('auto add = [base](int x) { return base + x; };', {'base'}, False, 'auto add = [](int x) { return base + x; };'),
    ('[&base](int x) { return base + x; }', {'base'}, False, '[](int x) { return base + x; }'),
    ('[=, &k, this, xs...]() mutable { }', {'k', 'xs'}, True, '[this, xs...]() mutable { }'),
    ('[k = k, &j](int) { }', {'k', 'j'}, False, '[k = k](int) { }'),
    ('f([a] { return a; }, [&b] { return b; })', {'a', 'b'}, False, 'f([] { return a; }, [] { return b; })'),
    ('auto g = [&]() { return base; };', set(), True, 'auto g = []() { return base; };'),
    ('[&](int x) { return x; }', set(), False, '[&](int x) { return x; }'),   # a default is kept unless asked
    ('v[base] + 1', {'base'}, False, 'v[base] + 1'),   # a subscript
    ('[base](int x) { return base; }', {'other'}, False, '[base](int x) { return base; }'),
])
def test_rewrite_captures(text, names, defaults, rewritten):
    from cpp_repl.lexer import rewrite_captures
    assert rewrite_captures(text, names, defaults) == rewritten


@pytest.mark.parametrize('text, free', [
    ('auto f = [](int q) { return q + seen; };', {'q', 'seen'}),
    ('auto f = [cap](int q) { return q + cap; };', {'q'}),
    ('auto f = [&cap, k = 2](int q) -> int { return q + cap + k + lim; };', {'q', 'lim'}),
    ('auto f = [=](int q) { return q + seen; };', set()),
    ('std::for_each(v.begin(), v.end(), [](int x) { total += x; });', {'x', 'total'}),
    ('int n = count(v, [](int x) { return x > lim; });', {'x', 'lim'}),
    ('v[base] + 1', set()),
])
def test_lambda_free_names(text, free):
    """Keywords come along (`return`): no declaration names one."""
    from cpp_repl.lexer import lambda_free_names
    assert lambda_free_names(lex(text)[0]) - {'return'} == free


def test_trailing_expression():
    assert trailing_expression('int a = 1;\na') == ('int a = 1;', 'a', 11)
    assert trailing_expression('int a = 1;\na + 1;') is None
    assert trailing_expression('a') is None
    assert trailing_expression('  int a = 1;\nstd::vector<int>{a, 2}') == ('int a = 1;', 'std::vector<int>{a, 2}', 13)
    assert trailing_expression('int a = \\\n 1;\na')[2] is None   # a line splice moved it


ORIGIN = Origin('prog.cpp', 7, 2, 2)   # line 7, column 2; later lines lost 2 columns to dedenting


def test_an_input_sits_at_its_source_lines_and_columns():
    send = transform('int a = 1;', origin=ORIGIN)
    assert send == ('#line 7 "prog.cpp"\n  int a = 1;', 'stmt', True)
    send = transform('int a = 1; for (;;) {\n  f();\n}', fresh=fresh(), origin=ORIGIN)
    assert send.text == ('#line 7 "prog.cpp"\n  int a = 1;\n' + in_function(
        'f1', '\n#line 7 "prog.cpp"\n             for (;;) {\n    f();\n  }\n'))


def test_a_placed_expression_and_its_statement():
    send = transform('v.size()', origin=ORIGIN)
    assert send == ('\n#line 7 "prog.cpp"\n  v.size()', 'expr', True)
    send = transform('v.size()  // a comment', origin=ORIGIN, as_statement=True, fresh=fresh())
    assert send.text == in_function('f1', '\n#line 7 "prog.cpp"\n  v.size();\n')


def test_what_keeps_no_place():
    region = '#ifdef X\nint x;\n#endif'
    assert transform(region, origin=ORIGIN).text == '#line 7 "prog.cpp"\n  ' + region.replace('\n', '\n  ')
    assert transform('#include <vector>') == ('#include <vector>', 'pp', False)
    assert transform('#include <vector>', origin=ORIGIN) == ('#line 7 "prog.cpp"\n  #include <vector>', 'pp', True)
    send = transform('int a = \\\n  1;', origin=ORIGIN)   # a line splice: one line, as without an origin
    assert send == ('int a = 1;', 'stmt', False)
    assert transform('x', as_statement=True, fresh=fresh()).text == in_function('f1', 'x;')
    # The session's second try of a failed expression: a statement at file scope.
    assert transform('Foo *q', as_statement=True, wrap=False, origin=ORIGIN) == ('#line 7 "prog.cpp"\n  Foo *q;', 'stmt', True)


def test_located():
    text = 'int a;\n  int b;'
    assert located(ORIGIN, text, 0) == ORIGIN
    assert located(ORIGIN, text, 4) == Origin('prog.cpp', 7, 6, 2)
    assert located(ORIGIN, text, 9) == Origin('prog.cpp', 8, 4, 2)


def test_open_conditionals():
    assert open_conditionals('#if A\n#ifdef B\n#endif') == 1
    assert open_conditionals('#ifndef A\n#else\n#endif') == 0
    assert open_conditionals('int x;  // a #if after code is no directive') == 0
    assert open_conditionals('auto s = R"(\n#if 0\n)";\n/*\n#ifdef X\n*/') == 0


def test_an_open_raw_string_is_unclosed():
    assert lex('auto s = R"x(abc')[1] is True
    assert lex('auto s = R"x(abc)x";')[1] is False
    assert [t.kind for t in lex('auto s = R"x(abc)x";')[0]] == ['ident', 'ident', 'op', 'raw', 'op']


@pytest.mark.parametrize('text, wrapped', [
    ('std::for_each(v.begin(), v.end(), [&](int x) { s += x; });', True),
    ('cb = [] { return 1; };', True),
    ('auto [a, b] = f([] { return 1; });', False),
    ('std::function<int(int)> h = [](int x) { return x; };', False),
    ('const auto &r = pick(v, [](int x) { return x; });', False),
    ('std::vector<std::pair<int, int>> w = make([] { return 1; });', False),
    ('[[maybe_unused]] int q = 1;', False),
    ('int n = m[k];', False),
    ('int n = f(m[k]);', False),
    ('struct S { int m() { auto l = [] { return 7; }; return l(); } };', False),
    ('template <class T> struct W { T v; auto get() { return [this] { return v; }; } };', False),
    ('namespace N { auto f = [] { return 1; }; }', False),
    ('int twice_each(std::vector<int> v) { std::for_each(v.begin(), v.end(), [](int &x) { x *= 2; }); return v[0]; }',
     False),
    ('std::enable_if_t<(N > 4), int> k = count(v, [](int x) { return x > 1; });', False),
    ('return 1;', True),
    ('throw std::runtime_error("x");', True),
    # Expression statements run inside a function, where an exception is caught.
    ('v.at(9);', True),
    ('std::cout << x;', True),
    ('returned = 1;', True),
    ('++n;', True),
    ('std::string(5, \'a\');', True),
    ('ns::f(x);', True),
    # Declarations and definitions stay at file scope, whatever their shape.
    ('unsigned long n = 1;', False),
    ('long double d = 1;', False),
    ('long *p = nullptr;', False),
    ('unsigned char buf[16];', False),
    ('int (*fp)(int) = f;', False),
    ('alignas(16) float v[4];', False),
    ('#pragma pack(1)\nint packed;', False),
    ('std::string s(5, \'a\');', False),
    ('static_assert(sizeof(int) == 4);', True),
    ('using namespace std;', False),
    ('struct S;', False),
    # A macro may declare something: it stays at file scope.
    ('TF_DEFINE_PRIVATE_TOKENS(_tokens, ((a, "a")));', False),
    ('DEFINE_bool(verbose, false, "say more");', False),
    ('PXR_NAMESPACE_USING_DIRECTIVE;', False),
    ('[](int x) { return x; };', True),
    ('S::S() : x{1} { }', False),
    # With its ';', what could be a declaration is one: `Foo *arr[3];` must
    # not become a local. A subscript or a call with *p is a statement.
    ('Foo *arr[3];', False),
    ('int w;', False),
    ('Foo f;', False),
    ('v[0];', True),
    ('f(*p);', True),
    ('g(&x);', True),
    ('m * m;', False),   # the shape of `Foo *p;`
])
def test_needs_function(text, wrapped):
    tokens, _ = lex(text)
    (toks, control), = split_statements(tokens)
    assert needs_function(toks, control) is wrapped
