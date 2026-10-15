"""cpp-repl --docker: the same command, run in an image that carries the
newest clang-repl.

The image holds the toolchain only. The cpp_repl checkout (read-only), the
working directory, the state directory, and every directory the
configuration reads that the image lacks (include directories, libraries,
preludes, compile_commands.json, the files given) are mounted at their own
paths, so that every path means the same inside the container and out.
"""

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import PACKAGE, Config, ConfigError, state_dir

DEFAULT_IMAGE = 'cpp-repl:llvm23'
CHECKOUT = PACKAGE.parent.parent
# What the image has of its own; anything else is mounted.
_IMAGE_HAS = ('/usr/', '/lib/', '/lib64/', '/bin/', '/sbin/', '/etc/', '/opt/bench-venv/', '/opt/eigen-')


def _image_has(path):
    text = str(path) + '/'
    return text.startswith(_IMAGE_HAS) and not text.startswith('/usr/local/')


def _inside(path, directories):
    return any(path == d or d in path.parents for d in directories)


def mounts(cfg, extra, files):
    """The directories to mount read-only, none inside another."""
    candidates = [Path(e).expanduser() for e in extra]
    flags = cfg.compiler_flags
    for i, a in enumerate(flags[:-1]):
        if a in ('-I', '-isystem', '-iquote', '-idirafter'):
            candidates.append(Path(flags[i + 1]))
    candidates += [lib.parent for lib in cfg.libs] + [h.parent for h in cfg.preludes] + cfg.mounts
    if cfg.compile_commands:
        candidates.append(cfg.compile_commands.parent)
    candidates += [Path(f).resolve().parent for f in files]
    found = sorted({c.resolve() for c in candidates if c.exists() and not _image_has(c.resolve())},
                   key=lambda p: len(p.parts))
    kept = []
    for p in found:
        if not _inside(p, kept):
            kept.append(p)
    return kept


def _without(argv, with_value, alone):
    """argv without the options that only the host side reads."""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in alone:
            i += 1
        elif a in with_value:
            i += 2
        elif any(a.startswith(name + '=') for name in with_value):
            i += 1
        else:
            out.append(a)
            i += 1
    return out


def docker_command(argv, args, options):
    """The docker run command line for this cpp-repl command line."""
    image = args.image or os.environ.get('CPP_REPL_IMAGE') or DEFAULT_IMAGE
    cfg = Config(options, backend=False)   # profiles and flags resolved here, for the mounts
    cwd = options.cwd
    state = state_dir()
    home = state / 'docker-home'
    home.mkdir(parents=True, exist_ok=True)   # made here, or Docker makes it root's
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    cmd = ['docker', 'run', '--rm', '--init', '-i'] + (['-t'] if tty else [])
    cmd += ['--user', f'{os.getuid()}:{os.getgid()}', '--detach-keys', 'ctrl-^,ctrl-^',
            '-e', f'HOME={home}', '-e', f'XDG_STATE_HOME={state.parent}', '-e', 'PYTHONDONTWRITEBYTECODE=1']
    for name in ('TERM', 'NO_COLOR', 'COLUMNS', 'LINES', 'USD_ROOT', 'EIGEN_ROOT', 'GLM_ROOT'):
        if name in os.environ:
            cmd += ['-e', name]
    # Each directory is mounted once (Docker refuses a second mount at the
    # same path); the working directory may be the checkout itself.
    volumes = {cwd: 'rw'}
    volumes.setdefault(CHECKOUT.resolve(), 'ro')
    volumes.setdefault(state, 'rw')
    for m in mounts(cfg, args.mount, options.files):
        if not _inside(m, list(volumes)):
            volumes[m] = 'ro'
    for path, mode in volumes.items():
        cmd += ['-v', f'{path}:{path}' + (':ro' if mode == 'ro' else '')]
    cmd += ['-w', str(cwd)]
    inner = _without(argv, {'--image', '--mount', '--clang-repl'}, {'--docker', '--dry-run'})
    cmd += [image, 'python3', str(CHECKOUT / 'bin' / 'cpp-repl'), *inner]
    return image, cmd


def run_in_docker(argv, args, options):
    image, cmd = docker_command(argv, args, options)
    if args.dry_run:
        print(shlex.join(cmd))
        return 0
    if not shutil.which('docker'):
        raise ConfigError('docker is not installed')
    probe = subprocess.run(['docker', 'image', 'inspect', '--format',
                            '{{ index .Config.Labels "io.cpp-repl.dockerfile-sha256" }}', image],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        built_from = probe.stdout.strip()
        current = hashlib.sha256((CHECKOUT / 'docker' / 'Dockerfile').read_bytes()).hexdigest()
        if built_from not in ('', 'unknown', '<no value>', current):
            sys.stderr.write(f'cpp-repl: {image} was built from an older docker/Dockerfile; '
                             'rebuild it with: make docker\n')
    else:
        major = image.removeprefix('cpp-repl:llvm')
        build = (['docker', 'build', '--target', 'apt']
                 + (['--build-arg', f'LLVM_MAJOR={major}'] if major.isdigit() else [])
                 + ['-t', image, str(CHECKOUT / 'docker')])
        if sys.stdin.isatty() and sys.stdout.isatty():
            answer = input(f'cpp-repl: there is no image {image}. Build it now (a few minutes)? [y/N] ')
            if answer.strip().lower() not in ('y', 'yes'):
                return 2
            if subprocess.run(build).returncode != 0:
                return 2
        else:
            sys.stderr.write(f'cpp-repl: there is no image {image}; build it with\n  {shlex.join(build)}\n')
            return 2
    os.execvp('docker', cmd)
