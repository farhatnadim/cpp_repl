#!/usr/bin/env python3
"""A stand-in for clang++ beside the fake clang-repl, for the tests of the
precompiled header: it writes the file -o names and appends its command line
to the file FAKE_CLANGXX_LOG names. With FAKE_CLANGXX_FAIL=1, or when the
file FAKE_CLANGXX_FAIL_WHEN_EXISTS names exists, it prints an error and
exits 1, as a compile that failed."""

import os
import sys


def main():
    argv = sys.argv[1:]
    log = os.environ.get('FAKE_CLANGXX_LOG')
    if log:
        with open(log, 'a') as f:
            f.write(' '.join(argv) + '\n')
    marker = os.environ.get('FAKE_CLANGXX_FAIL_WHEN_EXISTS')
    if os.environ.get('FAKE_CLANGXX_FAIL') == '1' or (marker and os.path.exists(marker)):
        sys.stderr.write("preludes.h:1:10: fatal error: 'broken.h' file not found\n1 error generated.\n")
        return 1
    if '-o' in argv:
        with open(argv[argv.index('-o') + 1], 'w') as f:
            f.write('fake\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
