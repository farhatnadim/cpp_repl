"""C++ sources and .repl files as inputs. No clang-repl needed."""

import textwrap

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
    ]


def test_a_final_return_still_runs_its_expression():
    assert inputs_from_cpp('int run();\nint main() { return run(); }') == ['int run();', 'run();']
    assert inputs_from_cpp('int main() { f(); return EXIT_SUCCESS; }') == ['f();']
    assert inputs_from_cpp('int main() { return -1; }') == []


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


def test_split_directives():
    assert split_directives('#include <a>\n#define X \\\n  1\nint x;\n#ifdef Y\nint y;\n#endif\nint z;') == [
        ('directive', '#include <a>'),
        ('directive', '#define X 1'),
        ('code', 'int x;'),
        ('region', '#ifdef Y\nint y;\n#endif'),
        ('code', 'int z;'),
    ]
    # A directive inside a block is code.
    assert split_directives('void f() {\n#ifdef Y\n  y();\n#endif\n}') == \
        [('code', 'void f() {\n#ifdef Y\n  y();\n#endif\n}')]


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
