"""The cpp-repl command."""

import argparse
import atexit
import importlib.util
import os
import shlex
import signal
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, Options, builtin_profiles
from .lexer import Origin
from .painter import Painter
from .session import OUT, JsonOutput, Session, SessionError
from .sources import BlockReader, Input, inputs_from_file

DESCRIPTION = """\
A C++ REPL on top of clang-repl. It reads whole blocks, prints the value of a
bare expression, rebuilds the session after a crash or Ctrl-C, and turns
C++ sources into sessions and sessions back into C++ programs.

  cpp-repl                          an empty session
  cpp-repl -p eigen -O2 --native    Eigen loaded, optimised for this CPU
  cpp-repl prog.cpp                 run prog.cpp's main() and keep the prompt
  cpp-repl --batch prog.cpp         run it and exit (exit status: see below)
  cpp-repl --docker                 the same, in an image with the newest clang-repl
"""

EPILOG = """\
Compiler flags go to clang as they are: -I DIR, -isystem DIR, -iquote DIR,
-idirafter DIR, -D NAME[=VALUE], -U NAME, -O0 to -O3, -std=..., -stdlib=libc++,
-include FILE (a prelude of your own), -isysroot DIR, --sysroot DIR,
--gcc-toolchain=DIR, -nostdinc++, -m..., -f..., -W..., -g, -pthread. A
cpp-repl.toml in the working directory or above adds its own (see the
README). Inside the REPL, %help lists the commands.

Exit status after running files: 0 when every input ran; 1 when an input
failed to compile or link; when the program crashed or called exit(N), what a
shell reports for it: 128 + the signal (139 for SIGSEGV, 134 for abort()), or
N; else 124 when an input ran past --timeout. 2 when cpp-repl cannot start,
130 after Ctrl-C.
"""

_WITH_VALUE = ('-I', '-isystem', '-iquote', '-idirafter', '-D', '-U', '-include', '-isysroot', '--sysroot')
_PREFIXES = ('-I', '-isystem', '-iquote', '-idirafter', '-D', '-U', '-O', '-std=', '-stdlib=', '-include',
             '-isysroot', '--sysroot=', '--gcc-toolchain=', '-nostdinc', '-m', '-f', '-W', '-g')


def split_compiler_flags(argv):
    """(compiler flags, the rest of argv). Compiler flags are the ones
    clang-repl gets; the rest are cpp-repl's own options and files."""
    flags, rest, i = [], [], 0
    while i < len(argv):
        a = argv[i]
        if a == '--':
            rest += argv[i:]
            break
        if a in _WITH_VALUE and i + 1 < len(argv):
            flags += argv[i:i + 2]
            i += 2
            continue
        if a.startswith(_PREFIXES) or a in ('-pthread', '-pedantic', '-w'):
            flags.append(a)
        else:
            rest.append(a)
        i += 1
    return flags, rest


def make_parser():
    parser = argparse.ArgumentParser(prog='cpp-repl', description=DESCRIPTION, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('files', nargs='*', type=Path,
                        help="C++ sources (main()'s body runs one statement at a time) "
                             'or saved .repl sessions to run first')
    parser.add_argument('-p', '--profile', action='append', default=[], metavar='NAME',
                        help=f'load a profile ({", ".join(builtin_profiles())}); repeatable')
    parser.add_argument('-c', '--config', action='append', default=[], metavar='FILE',
                        help='read a cpp-repl.toml-style file; repeatable')
    parser.add_argument('--no-config', action='store_true',
                        help='ignore cpp-repl.toml in the working directory and above')
    parser.add_argument('--compile-commands', metavar='PATH',
                        help='take flags from compile_commands.json, or from a build directory holding one')
    parser.add_argument('--build-dir', metavar='DIR', help='the same as --compile-commands DIR')
    parser.add_argument('--lib', action='append', default=[], metavar='PATH',
                        help='load a shared library before the first input; repeatable')
    parser.add_argument('-l', dest='link_names', action='append', default=[], metavar='NAME',
                        help='load libNAME.so, found as the linker finds it: in the -L directories, '
                             "$LIBRARY_PATH, the usual places, then the loader's cache; repeatable")
    parser.add_argument('-L', dest='link_dirs', action='append', default=[], metavar='DIR',
                        help='a directory to look in for -l libraries; repeatable')
    parser.add_argument('--native', action='store_true',
                        help='compile for this CPU (-march=native), so that AVX and FMA are used')
    parser.add_argument('--no-std', action='store_true',
                        help='leave out the standard-library prelude, for a faster start')
    parser.add_argument('--clang-repl', metavar='PATH',
                        help='the clang-repl to run (default: $CPP_REPL_CLANG_REPL, then the newest '
                             'one on PATH)')
    parser.add_argument('--batch', action='store_true',
                        help='run the files and exit (exit status: see below)')
    parser.add_argument('--stop-on-error', action='store_true',
                        help='stop running files at the first input that fails')
    parser.add_argument('--timeout', type=float, metavar='SECONDS',
                        help='stop an input that takes longer, compiling included, and go on without it')
    parser.add_argument('--emit', action='store_true',
                        help='print the inputs the files turn into, without running them')
    parser.add_argument('--cwd', type=Path, help='working directory of the session')
    parser.add_argument('--diagnostics', choices=('brief', 'full'),
                        help="how much of clang's diagnostics to show: brief (each input's first error, "
                             'source lines only from the code being run; the default with --batch or '
                             'input piped in) or full (the default at the prompt)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='leave out the echo of each input run from a file or stdin')
    parser.add_argument('--json', action='store_true',
                        help='write a JSON object per input in place of the transcript: its file, line, '
                             'status, seconds, output and notes; a last line holds the exit status')
    parser.add_argument('--no-color', action='store_true', help='plain text (NO_COLOR=1 does the same)')
    parser.add_argument('--cflags', action='store_true',
                        help='print the compiler flags that build a %%save-cpp file, and exit')
    parser.add_argument('--libs', action='store_true',
                        help='print the linker flags for a %%save-cpp file, and exit')
    parser.add_argument('--version', action='version', version=f'cpp-repl {__version__}')
    stay = parser.add_argument_group('A session that stays, for a coding agent\'s loop')
    stay.add_argument('--serve', metavar='SOCKET',
                      help='keep this session running, and run what cpp-repl --connect SOCKET sends')
    stay.add_argument('--connect', metavar='SOCKET',
                      help='run the files, or stdin, in the session of cpp-repl --serve SOCKET')
    stay.add_argument('--fresh', action='store_true',
                      help='with --connect: run in an empty session, which the server keeps warm')
    docker = parser.add_argument_group('Docker')
    docker.add_argument('--docker', action='store_true',
                        help='run in a Docker image with the newest clang-repl')
    docker.add_argument('--image', help='the image (default: $CPP_REPL_IMAGE, then cpp-repl:llvm23)')
    docker.add_argument('--mount', action='append', default=[], metavar='DIR',
                        help='mount another directory, read-only; repeatable')
    docker.add_argument('--dry-run', action='store_true',
                        help='print the docker command instead of running it')
    return parser


def options_from(args, flags, colour, interactive=False):
    return Options(files=list(args.files), profiles=args.profile, configs=args.config,
                   no_config=args.no_config, compile_commands=args.compile_commands or args.build_dir,
                   flags=flags, libs=args.lib, native=args.native, no_std=args.no_std,
                   clang_repl=args.clang_repl, colour=colour, cwd=(args.cwd or Path.cwd()).resolve(),
                   diagnostics=args.diagnostics or ('full' if interactive else 'brief'),
                   stop_on_error=args.stop_on_error, timeout=args.timeout, quiet=args.quiet, json=args.json,
                   link_names=args.link_names, link_dirs=args.link_dirs)


def _fail(message):
    sys.stderr.write(f'cpp-repl: {message}\n')
    return 2


def _fileno(stream):
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def _stdin_line(more):
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip('\n')


def main(argv=None):
    try:
        return _run(sys.argv[1:] if argv is None else list(argv))
    except BrokenPipeError:
        # Whoever read the output has gone (cpp-repl --batch f.cpp | head).
        # Python flushes stdout at exit: point it at /dev/null, so that the
        # flush stays quiet too.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141   # 128 + SIGPIPE, what a shell reports for a C program


def _run(argv):
    flags, rest = split_compiler_flags(argv)
    args = make_parser().parse_args(rest)

    if args.connect:
        from .server import connect
        if (flags or args.profile or args.config or args.compile_commands or args.build_dir or args.lib
                or args.link_names or args.link_dirs or args.native or args.no_std or args.clang_repl or args.cwd):
            return _fail("--connect runs in the server's session, whose configuration is fixed: its flags, "
                         'profiles, libraries and directory are the server\'s. --timeout, --stop-on-error, '
                         '--diagnostics, -q, --json and --fresh apply to the run')
        return connect(args, _fail)

    if args.docker:
        from .docker import run_in_docker
        try:
            return run_in_docker(argv, args, options_from(args, flags, colour=False))
        except ConfigError as e:
            return _fail(e)

    for f in args.files:
        if not f.is_file():
            return _fail(f'no such file: {f}')
    if args.emit:
        for f in args.files:
            for text in inputs_from_file(f):
                print(text)
        return 0

    interactive = not args.batch and not args.serve and sys.stdin.isatty()
    # A server's output goes to its clients, which paint for themselves.
    colour = (sys.stdout.isatty() and not args.no_color and not args.json and not args.serve
              and not os.environ.get('NO_COLOR'))
    options = options_from(args, flags, colour, interactive)
    try:
        cfg = Config(options, backend=not (args.cflags or args.libs))
    except ConfigError as e:
        return _fail(e)
    if args.cflags or args.libs:
        out = (cfg.cflags() if args.cflags else []) + (cfg.link_flags() if args.libs else [])
        print(shlex.join(out))
        return 0
    cwd = options.cwd
    if not cwd.is_dir():
        return _fail(f'no such directory: {cwd}')
    painter = Painter.create(cfg.blocks, cfg.diagnostics) if colour else None

    if interactive:
        notes = ''.join(f'{note}\n' for note in cfg.notes)
        hint = ''
        if sys.stdout.isatty() and not importlib.util.find_spec('prompt_toolkit'):
            hint = 'For highlighting as you type: pip install prompt_toolkit\n'
        OUT.write(f'cpp-repl: {cfg.describe()}, in {cwd}\n{notes}{hint}'
                  '%help lists the commands; Ctrl-D quits. Starting clang-repl...\n')
    # The code reads cpp-repl's own stdin, as the compiled program would,
    # unless the inputs themselves come through it.
    piped = not interactive and not args.batch and not args.files
    out = JsonOutput() if args.json else OUT
    try:
        # A server's session recovers from a crash and keeps a spare, as
        # the prompt's does.
        session = Session(cfg, cwd, batch=not interactive and not args.serve, painter=painter, out=out,
                          program_input=None if piped else _fileno(sys.stdin))
    except KeyboardInterrupt:
        OUT.write('\n')
        return 130
    except SessionError as e:
        return _fail(e)
    atexit.register(session.close)
    if not interactive:   # the prompt's first line says these
        for note in cfg.notes:
            out.note(f'[{note}]')
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(1))
    if args.serve:
        from .server import serve
        return serve(session, args.serve)

    status = None
    try:
        for f in args.files:
            if not session.run_inputs(inputs_from_file(f, skip=session.prelude_names), echo=True):
                break
        if interactive:
            from .ui import interact
            interact(session, colour=painter is not None)
        elif not args.batch and not args.files:
            # Input piped in: run it like a file.
            reader = BlockReader(_stdin_line)
            inputs = []
            while True:
                try:
                    inputs.append(Input(reader.read(), Origin('<stdin>', reader.line, 0, 0)))
                except EOFError:
                    break
            session.run_inputs(inputs, echo=True)
    except KeyboardInterrupt:
        OUT.write('\n')
        status = 130
    except SystemExit as e:   # %quit, or a signal
        status = e.code if isinstance(e.code, int) else int(e.code is not None)
    if status is None:
        status = 0 if interactive else session.exit_status()
    if args.json:
        out.finish(status, session)
    return status
