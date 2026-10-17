"""cpp-repl --docker: the same command, run in an image that carries the
newest clang-repl.

The image holds the toolchain only. The cpp_repl package, or the checkout it
runs from (read-only), the session's working directory, the directory
cpp-repl runs in (read-only, and the container's own), the state directory,
and every directory the configuration reads that the image lacks (include
directories, libraries, preludes, compile_commands.json, the files given) are
mounted at their own paths, so that every path means the same inside the
container and out.
"""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_IMAGE, PACKAGE, Config, ConfigError, state_dir

# The Dockerfile and its build context, in a checkout and in an installed
# package alike.
DOCKER_DIR = PACKAGE / 'docker'
# The checkout, when the package runs from one (the plugin's clone and the
# evals' copy have bin/cpp-repl beside src/ too); None from a wheel.
CHECKOUT = PACKAGE.parent.parent if (PACKAGE.parent.parent / 'bin' / 'cpp-repl').is_file() else None
# What the container gets of cpp-repl's own code, mounted read-only.
CODE = CHECKOUT or PACKAGE
# What the image has of its own; anything else is mounted.
_IMAGE_HAS = ('/usr/', '/lib/', '/lib64/', '/bin/', '/sbin/', '/etc/', '/opt/bench-venv/', '/opt/eigen-')


def image_has(path):
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
    candidates += cfg.link_dirs
    candidates += [Path(d) for d in os.environ.get('LIBRARY_PATH', '').split(os.pathsep) if d]   # for -l inside
    if cfg.compile_commands:
        candidates.append(cfg.compile_commands.parent)
    candidates += [Path(f).resolve().parent for f in files]
    found = sorted({c.resolve() for c in candidates if c.exists() and not image_has(c.resolve())},
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
    cfg = Config(options, backend=False, links=False)   # profiles and flags resolved here, for the mounts
    cwd = options.cwd
    state = state_dir()
    home = state / 'docker-home'
    home.mkdir(parents=True, exist_ok=True)   # made here, or Docker makes it root's
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    cmd = ['docker', 'run', '--rm', '--init', '-i'] + (['-t'] if tty else [])
    cmd += ['--user', f'{os.getuid()}:{os.getgid()}', '--detach-keys', 'ctrl-^,ctrl-^',
            '-e', f'HOME={home}', '-e', f'XDG_STATE_HOME={state.parent}', '-e', 'PYTHONDONTWRITEBYTECODE=1']
    # The terminal's, and what the profiles and -l read.
    for name in ('TERM', 'NO_COLOR', 'COLUMNS', 'LINES', 'USD_ROOT', 'PXR_ROOT', 'EIGEN_ROOT', 'EIGEN3_INCLUDE_DIR',
                 'GLM_ROOT', 'TORCH_ROOT', 'LIBTORCH', 'CUDA_HOME', 'LIBRARY_PATH'):
        if name in os.environ:
            cmd += ['-e', name]
    # Each directory is mounted once (Docker refuses a second mount at the
    # same path); the working directory may be the checkout itself.
    volumes = {cwd: 'rw'}
    volumes.setdefault(CODE.resolve(), 'ro')
    volumes.setdefault(state, 'rw')
    # The container runs where this command runs, so that relative paths on
    # the command line (files, -c, -I, --cwd) mean the same inside.
    here = Path.cwd().resolve()
    if not _inside(here, list(volumes)):
        volumes[here] = 'ro'
    for m in mounts(cfg, args.mount, options.files):
        if not _inside(m, list(volumes)):
            volumes[m] = 'ro'
    for path, mode in volumes.items():
        cmd += ['-v', f'{path}:{path}' + (':ro' if mode == 'ro' else '')]
    cmd += ['-w', str(here)]
    inner = _without(argv, {'--image', '--mount', '--clang-repl'}, {'--docker', '--dry-run'})
    # The package's directory is on the path, a checkout's src/ or site-packages;
    # -P keeps the working directory off sys.path.
    cmd += ['-e', f'PYTHONPATH={PACKAGE.parent}', image, 'python3', '-P', '-m', 'cpp_repl', *inner]
    return image, cmd


def dockerfile_sha():
    """The hash of the Dockerfile as shipped, which make docker labels an
    image with."""
    return hashlib.sha256((DOCKER_DIR / 'Dockerfile').read_bytes()).hexdigest()


def _how_to_build(image):
    """The command that builds one of cpp-repl's own images: make in a
    checkout, docker build on the packaged Dockerfile from an install. Any
    other tag is left to whoever made its Dockerfile."""
    for pattern, target in ((r'cpp-repl:llvm(\d+)', 'apt'), (r'cpp-repl:bench-llvm(\d+)', 'bench'),
                            (r'cpp-repl:llvm-snapshot', 'snapshot')):
        m = re.fullmatch(pattern, image)
        if m:
            break
    else:
        return f'docker build -t {image} DIR   (DIR holding its Dockerfile)'
    major = m.group(1) if m.groups() else None
    if CHECKOUT:
        name = {'apt': 'docker', 'bench': 'docker-bench', 'snapshot': 'docker-snapshot'}[target]
        return f'make -C {CHECKOUT} {name}' + (f' LLVM={major}' if major else '')
    arg = f' --build-arg LLVM_MAJOR={major}' if major else ''
    return (f'docker build --target {target}{arg} --build-arg DOCKERFILE_SHA={dockerfile_sha()} '
            f'-t {image} {DOCKER_DIR}')


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
    current = dockerfile_sha()
    if probe.returncode == 0:
        built_from = probe.stdout.strip()
        if built_from not in ('', 'unknown', '<no value>', current):
            sys.stderr.write(f'cpp-repl: {image} was built from an older Dockerfile; '
                             f'rebuild it with: {_how_to_build(image)}\n')
    else:
        m = re.fullmatch(r'cpp-repl:llvm(\d+)', image)
        if not m:
            # Only cpp-repl's own clang-repl images are built here. Any other
            # tag (a profile's derived image, the snapshot) holds more than
            # that build would put in it.
            sys.stderr.write(f'cpp-repl: there is no image {image}; build it first:\n  {_how_to_build(image)}\n')
            return 2
        # Labelled with the Dockerfile's hash, as make docker labels it, so
        # that a later Dockerfile is noticed.
        build = ['docker', 'build', '--target', 'apt', '--build-arg', f'LLVM_MAJOR={m.group(1)}',
                 '--build-arg', f'DOCKERFILE_SHA={current}', '-t', image, str(DOCKER_DIR)]
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
