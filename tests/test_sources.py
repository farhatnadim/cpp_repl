"""C++ sources and .repl files as inputs. No clang-repl needed."""

import textwrap

from cpp_repl.lexer import Origin
from cpp_repl.sources import (BlockReader, inputs_from_cpp, inputs_from_file, inputs_from_repl,
                              split_directives)


def cpp(text):
    return textwrap.dedent(text).lstrip()


def test_main_body_one_statement_at_a_time():
    src = cpp('''
        #include <iostream>
        // a comment at file scope
        static int twice(int x)
        {
          return 2 * x;   // kept with its function
        }

        int main()
        {
          int a = twice(2);
          for (int i = 0; i < a; ++i) {
            std::cout << i << '\\n';
          }
          return 0;
        }
        ''')
    assert inputs_from_cpp(src) == [
        '#include <iostream>',
        'static int twice(int x)\n{\n  return 2 * x;   // kept with its function\n}',
        'int a = twice(2);',
        "for (int i = 0; i < a; ++i) {\n  std::cout << i << '\\n';\n}",
        'return 0;',
    ]


def test_main_s_return_is_an_input():
    """A `return` in main() is run as it is: it ends the program with its
    value, which cpp-repl --batch exits with."""
    assert inputs_from_cpp('int run();\nint main() { return run(); }') == ['int run();', 'return run();']
    assert inputs_from_cpp('int main() { f(); return EXIT_SUCCESS; }') == ['f();', 'return EXIT_SUCCESS;']
    assert inputs_from_cpp('int main() { if (x) return -1; }') == ['if (x) return -1;']


def test_where_file_scope_items_end():
    """A '}' inside an initialiser or a type's body ends no item, and a
    function-try-block goes on with its handlers."""
    assert inputs_from_cpp('int x = []{ return 1; }();\nint main() { return x; }') == \
        ['int x = []{ return 1; }();', 'return x;']
    assert inputs_from_cpp('auto f = [](int a) { return a; };\nint g() { return f(1); }') == \
        ['auto f = [](int a) { return a; };', 'int g() { return f(1); }']
    assert inputs_from_cpp('int arr[] = {1, 2}, n = 2;\nauto m = std::map<int, int>{{1, 2}}.size();') == \
        ['int arr[] = {1, 2}, n = 2;', 'auto m = std::map<int, int>{{1, 2}}.size();']
    assert inputs_from_cpp('struct S { int x; } s;\nenum class E { a } e;\ntemplate <class T> struct Box { T v; } b{1};') == \
        ['struct S { int x; } s;', 'enum class E { a } e;', 'template <class T> struct Box { T v; } b{1};']
    assert inputs_from_cpp('int f() try { return 1; } catch (...) { return 2; }\nint main() { return f(); }') == \
        ['int f() try { return 1; } catch (...) { return 2; }', 'return f();']
    assert inputs_from_cpp('namespace n { int k; }\nextern "C" { int c(); }\nint main() {}') == \
        ['namespace n { int k; }', 'extern "C" { int c(); }']


def test_main_parameters_get_stand_ins():
    inputs = inputs_from_cpp('int main(int ac, char *av[]) { use(ac, av); }', program='prog.cpp')
    assert inputs == ['int ac = 1;',
                      'char *cpp_repl_argv[] = {const_cast<char *>("prog.cpp"), nullptr};',
                      'char **av = cpp_repl_argv;',
                      'use(ac, av);']
    assert inputs_from_cpp('int main(void) { go(); }') == ['go();']


def test_conditional_regions_stay_whole():
    src = cpp('''
        #include <cstdio>
        #ifdef FAST
        #  define MODE 1
        int fast = 1;
        #else
        int fast = 0;
        #endif
        int main() { printf("%d\\n", fast); }
        ''')
    assert inputs_from_cpp(src) == [
        '#include <cstdio>',
        '#ifdef FAST\n#  define MODE 1\nint fast = 1;\n#else\nint fast = 0;\n#endif',
        'printf("%d\\n", fast);',
    ]


def test_pragmas_stay_with_their_code():
    src = cpp('''
        #pragma once
        #pragma pack(push, 1)
        struct S { char c; int i; };
        #pragma pack(pop)
        int main()
        {
          #pragma omp parallel for
          for (int i = 0; i < 4; ++i)
            work(i);
        }
        ''')
    assert inputs_from_cpp(src) == [
        '#pragma pack(push, 1)\nstruct S { char c; int i; };',
        '#pragma pack(pop)',
        '#pragma omp parallel for\nfor (int i = 0; i < 4; ++i)\n  work(i);',
    ]


def test_a_raw_string_s_lines_are_no_directives():
    """A '#' at the start of a line inside a raw string literal, as a shader
    or a script embedded in a source has, is text: the literal stays whole,
    in a file and typed line by line."""
    src = 'const char *vs = R"(\n#version 330 core\nvoid main() {}\n)";\nint main() { puts(vs); }\n'
    assert inputs_from_cpp(src) == ['const char *vs = R"(\n#version 330 core\nvoid main() {}\n)";', 'puts(vs);']
    assert inputs_from_repl(src) == ['const char *vs = R"(\n#version 330 core\nvoid main() {}\n)";',
                                     'int main() { puts(vs); }']


def test_split_directives():
    assert split_directives('#include <a>\n#define X \\\n  1\nint x;\n#ifdef Y\nint y;\n#endif\nint z;') == [
        ('directive', '#include <a>', 0),
        ('directive', '#define X 1', 13),
        ('code', 'int x;', 29),
        ('region', '#ifdef Y\nint y;\n#endif', 36),
        ('code', 'int z;', 59),
    ]
    # A directive inside a block is code.
    assert split_directives('void f() {\n#ifdef Y\n  y();\n#endif\n}') == \
        [('code', 'void f() {\n#ifdef Y\n  y();\n#endif\n}', 0)]
    assert split_directives('int a;\n  #include <b>\n') == [('code', 'int a;', 0), ('directive', '#include <b>', 9),
                                                            ('code', '', 22)]


def test_inputs_know_where_they_start():
    """An input's origin: its first character's line and column in the file,
    and the columns dedenting took off its later lines."""
    src = cpp('''
        #include <cstdio>
        int twice(int x) { return 2 * x; }
        int main()
        {
          int a = 1; int b = twice(a);
          for (int i = 0; i < b; ++i) {
            std::printf("%d\\n", i);
          }
          return twice(b);
        }
        ''')
    origins = [(text, text.origin) for text in inputs_from_cpp(src, path='p.cpp')]
    assert origins == [
        ('#include <cstdio>', Origin('p.cpp', 1, 0, 0)),
        ('int twice(int x) { return 2 * x; }', Origin('p.cpp', 2, 0, 0)),
        ('int a = 1;', Origin('p.cpp', 5, 2, 2)),
        ('int b = twice(a);', Origin('p.cpp', 5, 13, 0)),
        ('for (int i = 0; i < b; ++i) {\n  std::printf("%d\\n", i);\n}', Origin('p.cpp', 6, 2, 2)),
        ('return twice(b);', Origin('p.cpp', 9, 2, 2)),
    ]
    repl = inputs_from_repl('int a = 1;\n\nif (a) {\n  f();\n}\na\n', path='s.repl')
    assert [(text, text.origin.line) for text in repl] == [('int a = 1;', 1), ('if (a) {\n  f();\n}', 3), ('a', 6)]


def test_quoted_includes_next_to_the_source_become_absolute(tmp_path):
    (tmp_path / 'helper.h').write_text('int helper();\n')
    src = tmp_path / 'prog.cpp'
    src.write_text('#include "helper.h"\n#include "missing.h"\n#include <vector>\n'
                   '#include "cpp_repl.h"\nint main() { helper(); }\n')
    assert inputs_from_file(src) == [f'#include "{tmp_path / "helper.h"}"', '#include "missing.h"',
                                     '#include <vector>', 'helper();']


def test_repl_files_are_read_as_typed():
    text = 'int a = 1;\nif (a) {\n  f();\n}\nelse {\n  g();\n}\n\na\n#ifdef X\nint x;\n#endif\n'
    assert inputs_from_repl(text) == ['int a = 1;', 'if (a) {\n  f();\n}\nelse {\n  g();\n}', 'a',
                                      '#ifdef X\nint x;\n#endif']


def test_block_reader_waits_for_else():
    lines = iter(['if (x) {', '  f();', '}', 'g();'])
    reader = BlockReader(lambda more: next(lines))
    assert reader.read() == 'if (x) {\n  f();\n}'
    assert reader.read() == 'g();'
