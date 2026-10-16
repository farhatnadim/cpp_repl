#!/usr/bin/env python3
"""What raw clang-repl does with the inputs cpp-repl rewrites.

Each probe starts a fresh clang-repl, feeds it a few lines over a pipe, and
records what came back: the output, the exit status, or a hang. Every probe
runs with HOME and TMPDIR in a scratch directory, without core dumps
(RLIMIT_CORE=0, and PR_SET_DUMPABLE cleared by the first input), and with a
deadline, since some inputs make clang-repl loop forever.

    tests/compat/harness.py --clang-repl clang-repl-18 > tests/compat/results/compat-18.json
    scripts/compat_table.py tests/compat/results/*.json

The JSON holds, for each probe, its verdict (a few words) and the raw output.
"""

import argparse
import json
import os
import re
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEADLINE = 20   # seconds per probe

# The first input of every probe: no core dump, no crash report.
NO_DUMPS = 'extern "C" int prctl(int, ...); int cpp_repl_no_dumps = prctl(4, 0, 0, 0, 0);'


def probe(name, title, lines, verdict, flags=(), cpp_repl=''):
    return {'name': name, 'title': title, 'lines': lines, 'verdict': verdict, 'flags': list(flags),
            'cpp_repl': cpp_repl}


def says(text):
    return lambda out, rc, hung: text in out


def crashed(rc):
    if rc is not None and rc < 0:
        try:
            return f'crashes ({signal.Signals(-rc).name})'
        except ValueError:
            return f'crashes (signal {-rc})'
    return None


def common(out, rc, hung):
    """The verdict shared by every probe for a hang or a crash, else None."""
    if hung:
        return 'hangs (killed after %d s)' % DEADLINE
    if 'Inline asm not supported' in out:
        return 'fatal error: no inline asm in the JIT'
    return crashed(rc)


def v_unclosed(out, rc, hung):
    return common(out, rc, hung) or ('works' if 'after' in out else 'errors')


def v_two_loops(out, rc, hung):
    if common(out, rc, hung):
        return common(out, rc, hung)
    m = re.search(r'L1:([0-9 ]*)\n', out), re.search(r'L2:([0-9 ]*)\n', out)
    first, second = (x.group(1).strip() if x else None for x in m)
    if first == '1 2 3' and second == '10 20 30':
        return 'works'
    if 'Symbols not found' in out or 'materialize' in out:
        return f'first loop prints {first!r}; the second fails to link'
    return f'prints {first!r} and {second!r}'


def v_nested(out, rc, hung):
    got = ' '.join(re.findall(r'\d\d', out.split('NESTED:')[-1])) if 'NESTED:' in out else ''
    return common(out, rc, hung) or ('works' if got == '00 01 10 11' else f'prints {got!r}')


def v_try(out, rc, hung):
    if common(out, rc, hung):
        return common(out, rc, hung)
    if 'caught 7' in out and 'next ok' in out:
        return 'works'
    if 'Symbols not found' in out or 'materialize' in out or 'JIT session error' in out:
        return 'link error' + (', and later inputs fail' if 'next ok' not in out else '')
    return 'errors'


def v_bare_builtin(out, rc, hung):
    if common(out, rc, hung):
        return common(out, rc, hung)
    m = re.search(r'\(int\) 42', out)
    if m:
        return 'prints (int) 42'
    return "prints 'Not implement yet.'" if 'Not implement yet.' in out else 'prints nothing'


def v_bare_class(out, rc, hung):
    if common(out, rc, hung):
        return common(out, rc, hung)
    if 'Symbols not found' in out or 'materialize' in out or 'JIT session error' in out:
        return 'link error'
    m = re.search(r'\(std::vector<int>[^)]*\) (.*)', out)
    return f'prints {m.group(1).strip()[:30]}' if m else ('prints nothing' if 'error' not in out else 'errors')


def v_bare_void(out, rc, hung):
    return common(out, rc, hung) or ('runs it' if 'void ran' in out else 'does not run it')


def v_ran(marker):
    return lambda out, rc, hung: common(out, rc, hung) or ('works' if marker in out else
                                                           'errors' if 'error' in out else 'prints nothing')


def v_prompt(out, rc, hung):
    return common(out, rc, hung) or ('prints a prompt' if 'clang-repl>' in out else 'prints no prompt')


def v_lib(out, rc, hung):
    m = re.search(r'^.*/nonexistent/cpp-repl/MARKER.*$', out, re.M)
    return common(out, rc, hung) or (m.group(0).replace('/nonexistent/cpp-repl/MARKER', '<path>') if m else 'no error line')


def v_parse_error(out, rc, hung):
    return common(out, rc, hung) or ('error: Parsing failed.' if 'error: Parsing failed.' in out else
                                     'other: ' + next((l for l in out.splitlines() if 'error' in l), '?'))


def v_flag(macro):
    def judge(out, rc, hung):
        c = common(out, rc, hung)
        if c:
            return c
        m = re.search(rf'{macro}=(\d+)', out)
        return f'{macro} = {m.group(1)}' if m else f'{macro} undefined'
    return judge


def v_command(out, rc, hung):
    c = common(out, rc, hung)
    if c:
        return c
    if 'unknown' in out.lower() or 'Parsing failed' in out:
        return 'unknown'
    return 'known'


def v_include_init(out, rc, hung):
    return common(out, rc, hung) or ('runs it' if 'init ran' in out else 'does not run it')


def probes(scratch):
    header = scratch / 'region.h'
    header.write_text('#ifdef CPP_REPL_PROBE\nint region_value = 2;\n#else\nint region_value = 1;\n#endif\n'
                      'int region_ok = printf("region %d\\n", region_value);\n')
    init = scratch / 'init.h'
    init.write_text('#include <cstdio>\ninline int init_value = printf("init ran\\n");\n')
    return [
        probe('unclosed', 'A line ending in an open `{`',
              ['#include <cstdio>', 'int f() {', 'return 1; }', 'printf("after\\n");'],
              v_unclosed, cpp_repl='waits for the block to close'),
        probe('two_loops', 'Two top-level range-for loops',
              ['#include <cstdio>', 'int v[] = {1, 2, 3};', 'printf("L1:");',
               'for (int x : v) printf("%d ", x);', 'printf("\\n");', 'printf("L2:");',
               'for (int x : v) printf("%d ", 10 * x);', 'printf("\\n");'],
              v_two_loops, cpp_repl='runs each loop inside a function'),
        probe('nested', 'Nested top-level `for` loops (llvm#89506)',
              ['#include <cstdio>', 'printf("NESTED:");',
               'for (int i = 0; i < 2; ++i) for (int j = 0; j < 2; ++j) printf("%d%d ", i, j);',
               'printf("\\n");'],
              v_nested, cpp_repl='runs the loop inside a function'),
        probe('try', 'A top-level `try`/`catch`',
              ['#include <cstdio>', 'try { throw 7; } catch (int e) { printf("caught %d\\n", e); }',
               'printf("next ok\\n");'],
              v_try, cpp_repl='runs it inside a function'),
        probe('lambda', 'A lambda called in a top-level statement',
              ['#include <cstdio>', '[] { printf("lambda ran\\n"); }();'],
              v_ran('lambda ran'), cpp_repl='runs the statement inside a function'),
        probe('lambda_arg', 'A lambda passed to a template in a top-level statement',
              ['#include <cstdio>', 'template <class F> void call(F f) { f(); }',
               'call([] { printf("lambda ran\\n"); });'],
              v_ran('lambda ran'), cpp_repl='runs the statement inside a function'),
        probe('bare_builtin', 'A bare `x + 1`, no semicolon',
              ['int x = 41;', 'x + 1', '%quit'],
              v_bare_builtin, cpp_repl='prints 42'),
        probe('bare_class', 'A bare `std::vector<int>{1, 2, 3}`',
              ['#include <vector>', 'std::vector<int>{1, 2, 3}', '%quit'],
              v_bare_class, cpp_repl='prints [1, 2, 3]'),
        probe('bare_void', 'A bare call of a void function',
              ['#include <cstdio>', 'void f() { printf("void ran\\n"); }', 'f()', '%quit'],
              v_bare_void, cpp_repl='runs it'),
        probe('inline_asm', 'Inline assembly, as in Eigen\'s matrix product',
              ['#include <cstdio>', 'int asm_r = 0;',
               'void asm_f() { asm volatile("mov $5, %0" : "=r"(asm_r)); } ', 'asm_f();',
               'printf("asm %d\\n", asm_r);'],
              v_ran('asm 5'), cpp_repl='the same (a clang-repl limit)'),
        probe('region', 'An `#if` region, through an `#include`d file',
              ['#include <cstdio>', f'#include "{header}"'],
              v_ran('region 1'), cpp_repl='sends #if regions this way'),
        probe('include_init', 'A dynamic initialiser in an `-include`d header',
              ['#include <cstdio>', 'printf("next\\n");'],
              v_include_init, flags=[f'--Xcc=-include', f'--Xcc={init}'],
              cpp_repl='keeps the prelude free of them'),
        probe('prompt', 'A prompt written to a pipe',
              ['int quiet = 1;'], v_prompt, cpp_repl='learns it at startup and strips it'),
        probe('lib_error', 'The error `%lib` prints for a missing file',
              ['%lib /nonexistent/cpp-repl/MARKER'], v_lib, cpp_repl='uses it as the end-of-output marker'),
        probe('parse_error', 'How a parse error ends',
              ['#error probe'], v_parse_error, cpp_repl='classifies it as an error'),
        probe('opt', '`-Xcc -O2` in the JIT',
              ['#include <cstdio>', 'printf("__OPTIMIZE__=%d\\n", __OPTIMIZE__ + 0);'],
              v_flag('__OPTIMIZE__'), flags=['--Xcc=-O2'], cpp_repl='-O2 on its command line'),
        probe('no_opt', 'The JIT without `-O`',
              ['#include <cstdio>', 'printf("__OPTIMIZE__=%d\\n", __OPTIMIZE__ + 0);'],
              v_flag('__OPTIMIZE__'), cpp_repl='-O0 too; see %timeit\'s note'),
        probe('native', '`-Xcc -march=native` in the JIT',
              ['#include <cstdio>', 'printf("__AVX2__=%d\\n", __AVX2__ + 0);'],
              v_flag('__AVX2__'), flags=['--Xcc=-march=native'], cpp_repl='--native'),
        probe('return_top', 'A `return 3;` at top level',
              ['#include <cstdio>', 'return 3;', 'printf("after\\n");'],
              lambda out, rc, hung: common(out, rc, hung) or ('ignored' if 'after' in out and 'error' not in out
                                                               else 'an error' if 'after' in out else 'errors'),
              cpp_repl='ends the program with 3, as main() would'),
        probe('goto_label', 'A `goto` to a label clang-repl never saw',
              ['#include <cstdio>', 'goto nowhere;', 'printf("after\\n");'],
              lambda out, rc, hung: common(out, rc, hung) or ('an error' if 'after' in out else 'errors'),
              cpp_repl='the same error, or a restored session on 18'),
        probe('init_capture', 'A file-scope lambda whose init-capture is not a constant',
              ['#include <cstdio>', 'int base = 10;', 'auto f = [v = base](int x) { return v + x; };',
               'printf("f %d\\n", f(1));'],
              lambda out, rc, hung: common(out, rc, hung) or ('works' if 'f 11' in out else
                                                               'prints f 1: the closure holds zero' if 'f 1' in out
                                                               else 'errors'),
              cpp_repl='the same (a clang-repl limit, README Limits)'),
        probe('help', '`%help`', ['%help'], v_command, cpp_repl='its own %help'),
        probe('undo', '`%undo`', ['int u = 1;', '%undo', 'int u = 2;'],
              lambda out, rc, hung: common(out, rc, hung) or ('works' if 'redefinition' not in out
                                                               and 'Parsing failed' not in out else 'fails'),
              cpp_repl='replays the journal without the last input'),
    ]


def run_probe(clang_repl, p, scratch):
    env = dict(os.environ, HOME=str(scratch / 'home'), TMPDIR=str(scratch / 'tmp'))
    env.pop('LD_PRELOAD', None)
    (scratch / 'home').mkdir(exist_ok=True)
    (scratch / 'tmp').mkdir(exist_ok=True)
    text = '\n'.join([NO_DUMPS] + p['lines'] + ['%quit']) + '\n'
    start = time.monotonic()
    proc = subprocess.Popen([clang_repl, '--Xcc=-std=c++20', *p['flags']], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=scratch,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(text.encode(), timeout=DEADLINE)
        hung = False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        hung = True
    out = out.decode('utf-8', 'replace')
    rc = None if hung else proc.returncode
    return {'verdict': p['verdict'](out, rc, hung), 'exit': rc, 'seconds': round(time.monotonic() - start, 2),
            'output': out[-4000:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--clang-repl', default='clang-repl')
    parser.add_argument('--only', nargs='*', help='probe names')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1]))
    version = subprocess.run([args.clang_repl, '--version'], capture_output=True, text=True).stdout
    m = re.search(r'LLVM version (\S+)', version)
    results = {'clang_repl': args.clang_repl, 'version': m.group(1) if m else version.strip(),
               'date': time.strftime('%Y-%m-%d', time.gmtime()), 'probes': {}}
    with tempfile.TemporaryDirectory(prefix='cpp-repl-compat-') as tmp:
        scratch = Path(tmp)
        for p in probes(scratch):
            if args.only and p['name'] not in args.only:
                continue
            result = run_probe(args.clang_repl, p, scratch)
            result.update(title=p['title'], cpp_repl=p['cpp_repl'])
            results['probes'][p['name']] = result
            print(f"{p['name']:14} {result['verdict']}", file=sys.stderr)
    json.dump(results, sys.stdout, indent=1)
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
