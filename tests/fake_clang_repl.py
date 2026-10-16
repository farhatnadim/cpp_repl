#!/usr/bin/env python3
"""A stand-in for clang-repl that speaks just enough of its protocol to test
the backend and the session without a compiler.

It reads one input per line and answers:
  %lib PATH            an error naming PATH, as clang-repl does for a missing file
  %quit                exits
  #error TEXT          a parse error
  #include <print>     the "file not found" of a header the standard library lacks
  printf("TEXT\\n");   TEXT
  say("TEXT")          TEXT and a newline       say_raw("TEXT")   TEXT alone
  crash()              dies of SIGSEGV          hang()            never answers
  exit(N)              exits with status N
  link_fail()          a JIT link error         too_many()        clang's "too many errors", then a loop
  limit()              clang's "too many errors" for a limit asked with -ferror-limit, then the next input
  taint()              a parse error after which this process cannot link stale(), as clang-repl
                       keeps a failed input's template instantiations without their code
  stale()              "linked", or a JIT link error in a process that has seen taint()
  syntax error         anything with 'error_here' in it fails to parse
  int NAME ..., double NAME ..., struct NAME ...   declares NAME; declared again, "redefinition of 'NAME'"
  ...repl::print_expr(((N), ...))...   prints N; with void_call() for N, the void mark
  ...return N; ... repl::finish(...)   the end mark around N, as a statement that returned from main
  ...throw_it(); ... repl::finish(...) the end mark of an uncaught std::runtime_error, "boom"
  ...repl::finish(...)   after the output and before an end mark, the run mark: the statement ran 1.5 µs
  #include "FILE"      FILE's lines but #line directives, joined into one, and answered as above
anything else is silently accepted.

Environment: FAKE_PROMPT is printed before each input is read (as clang-repl
built without libedit does); FAKE_ECHO=1 echoes each input back; FAKE_SPLIT=1
writes output one byte at a time; FAKE_MARKER is a format for the %lib error
({path}); FAKE_HANG_START=1 never answers at all; FAKE_START_ERROR=1 starts
with the error clang-repl prints for a prelude that does not compile, and
carries on as clang-repl does.
"""

import os
import re
import signal
import sys
import time

PROMPT = os.environ.get('FAKE_PROMPT', '')
SPLIT = os.environ.get('FAKE_SPLIT') == '1'
ECHO = os.environ.get('FAKE_ECHO') == '1'
MARKER = os.environ.get('FAKE_MARKER', 'error: {path}: cannot open shared object file: No such file or directory')


def out(text):
    if SPLIT:
        for ch in text:
            sys.stdout.write(ch)
            sys.stdout.flush()
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def main():
    if '--version' in sys.argv:
        print('Fake LLVM (http://llvm.org/):\n  LLVM version 99.1.2\n  Optimized build.')
        return 0
    if os.environ.get('FAKE_HANG_START') == '1':
        while True:
            time.sleep(1)
    if os.environ.get('FAKE_START_ERROR') == '1':
        out("<built-in>:1:10: fatal error: 'missing.h' file not found\n")
    n = 0
    tainted = False
    declared = set()
    while True:
        out(PROMPT)
        line = sys.stdin.readline()
        if not line:
            return 0
        line = line.rstrip('\n')
        if ECHO:
            out(line + '\n')
        n += 1
        if line == '%quit':
            return 0
        if line.startswith('%lib '):
            path = line[5:]
            if not os.path.exists(path):
                out(MARKER.format(path=path) + '\n')
            continue
        if line.startswith('#error'):
            out(f'input_line_{n}:1:2: error: {line[7:]}\n    1 | {line}\n      |  ^\nerror: Parsing failed.\n')
            continue
        m = re.fullmatch(r'#include "(.*)"', line)
        if m and os.path.isfile(m.group(1)):
            with open(m.group(1)) as f:
                line = ' '.join(text.strip() for text in f.read().split('\n') if not text.startswith('#line '))
        # What repl::finish writes after a statement that ran in a function.
        mark = '\x1d~1500\x1d\n' if 'repl::finish(' in line else ''
        if line.startswith('#include <print>'):
            out(f"input_line_{n}:1:10: fatal error: 'print' file not found\nerror: Parsing failed.\n")
            continue
        m = re.match(r'printf\("(.*)\\n"\);', line)
        if m:
            out(m.group(1) + '\n' + mark)
            continue
        m = re.search(r'say_raw\("(.*?)"\)', line)
        if m:
            out(m.group(1) + mark)
            continue
        m = re.search(r'say\("(.*?)"\)', line)
        if m:
            out(m.group(1) + '\n' + mark)
            continue
        if 'crash()' in line:
            os.kill(os.getpid(), signal.SIGSEGV)
        m = re.search(r'\bexit\((\d+)\)', line)
        if m:
            sys.stdout.flush()
            os._exit(int(m.group(1)))
        if 'hang()' in line:
            while True:
                time.sleep(1)
        if 'link_fail()' in line:
            out('JIT session error: Symbols not found: [ _Z9link_failv ]\n'
                'error: Failed to materialize symbols: { (main, { __stmts__1 }) }\n')
            continue
        if 'taint()' in line:
            tainted = True
            out(f"input_line_{n}:1:1: error: no matching function for call to 'taint'\n"
                'error: Parsing failed.\n')
            continue
        if 'stale()' in line:
            if tainted:
                out('JIT session error: Symbols not found: [ _ZN4repl3putI1QEEvRSoRKT_ ]\n'
                    'error: Failed to materialize symbols: { (main, { __stmts__1 }) }\n')
            else:
                out('linked\n')
            continue
        if 'too_many()' in line:
            out('fatal error: too many errors emitted, stopping now [-ferror-limit=]\n')
            while True:
                out('error: still stuck\n')
                time.sleep(0.01)
        if 'limit()' in line:
            out(f"input_line_{n}:1:1: error: use of undeclared identifier 'limit'\n"
                'fatal error: too many errors emitted, stopping now [-ferror-limit=]\nerror: Parsing failed.\n')
            continue
        if 'error_here' in line:
            out(f'input_line_{n}:1:1: error: use of undeclared identifier \'error_here\'\nerror: Parsing failed.\n')
            continue
        m = re.search(r'repl::print_expr\(\(\((.*)\), repl::printer<cpp_repl_tag>\{\}\)\);', line)
        if m:
            expr = m.group(1).strip()
            if expr == 'void_call()':
                out('\x1e' + mark)
            elif re.fullmatch(r'-?\d+', expr):
                out(expr + '\n' + mark)
            else:
                out(mark)
            continue
        m = re.search(r'\breturn (-?\d+);', line) if mark else None
        if m:
            out(f'{mark}\x1d{m.group(1)}\x1d\n')
            continue
        if 'throw_it()' in line and mark:
            out(mark + '\x1d!134std::runtime_error: boom\x1d\n')
            continue
        m = re.match(r'(?:int|double|struct) (\w+)\b', line)
        if m:
            if m.group(1) in declared:
                out(f"input_line_{n}:1:8: error: redefinition of '{m.group(1)}'\nerror: Parsing failed.\n")
            else:
                declared.add(m.group(1))
            continue
        out(mark)


if __name__ == '__main__':
    sys.exit(main())
