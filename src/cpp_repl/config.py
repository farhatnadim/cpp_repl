"""Configuration: which clang-repl runs, and with which flags, preludes and
libraries.

Flags come from four places, in increasing precedence: profiles, the
compile_commands.json entry of the file being loaded, cpp-repl.toml files,
and the command line. For include directories the first match wins, so the
highest precedence comes first; for defines and other flags the last one
wins, so it comes last. -std and -O take the highest precedence's value.
"""

import functools
import importlib
import json
import os
import pkgutil
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from .diagnostics import BRIEF_FLAGS

PACKAGE = Path(__file__).resolve().parent
PRELUDE_DIR = PACKAGE / 'prelude'
CORE_PRELUDE = PRELUDE_DIR / 'cpp_repl.h'
STD_PRELUDE = PRELUDE_DIR / 'cpp_repl_std.h'
PROJECT_FILE = 'cpp-repl.toml'
DEFAULT_STD = '-std=gnu++20'
PROMPT = 'c++> '


class ConfigError(Exception):
    """A configuration cpp-repl cannot start with; the message says why."""


@dataclass
class Profile:
    """What a library needs in a session. A built-in profile's configure()
    returns one, and so does a TOML file read with -c."""
    name: str
    flags: list = field(default_factory=list)        # compiler flags, as on a command line
    libs: list = field(default_factory=list)         # shared libraries to load first, in order
    prelude: list = field(default_factory=list)      # headers -include'd after the core prelude
    profiles: list = field(default_factory=list)     # built-in profiles a TOML file asks for
    compile_commands: Path | None = None             # a TOML file's compile_commands.json
    prompt: str | None = None
    blocks: list = field(default_factory=list)       # [(regex of a block's first line, Pygments lexer)]
    diagnostics: list = field(default_factory=list)  # [(regex whose group 1 is a severity, {severity: SGR})]
    completion: list = field(default_factory=list)   # header directories for Tab completion
    help: str = ''                                   # lines added to %help
    banner: str = ''                                 # what the startup line says about it
    mounts: list = field(default_factory=list)       # directories `cpp-repl --docker` mounts
    base: Path | None = None                         # what relative paths in flags are relative to


# ---------------------------------------------------------------------------
# Compiler flags.
# ---------------------------------------------------------------------------

_INCLUDE_FLAGS = ('-isystem', '-iquote', '-idirafter', '-I')
_OPT = re.compile(r'-O([0-3sgz]|fast)?')


def parse_flags(args, base=None):
    """Compiler flags as [(kind, (arg, ...))], kind being 'include',
    'define', 'std', 'opt' or 'other'. A flag's value may be attached (-Ifoo)
    or follow it (-I foo). Relative include directories are taken relative to
    base. Raises ConfigError for anything else."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        flag = next((f for f in _INCLUDE_FLAGS if a == f or a.startswith(f)), None)
        if flag or a in ('-D', '-U') or a.startswith(('-D', '-U')):
            flag = flag or a[:2]
            if a == flag:
                if i + 1 >= len(args):
                    raise ConfigError(f'{flag} needs a value')
                value, i = args[i + 1], i + 2
            else:
                value, i = a[len(flag):], i + 1
            if flag in ('-D', '-U'):
                out.append(('define', (flag + value,)))
            else:
                path = Path(value).expanduser()
                if base is not None and not path.is_absolute():
                    path = Path(base) / path
                out.append(('include', (flag, str(path))))
            continue
        if a.startswith('-std='):
            out.append(('std', (a,)))
        elif _OPT.fullmatch(a):
            out.append(('opt', (a,)))
        elif a.startswith(('-m', '-f', '-W', '-g')) or a in ('-pthread', '-pedantic', '-w'):
            out.append(('other', (a,)))
        else:
            raise ConfigError(f'not a compiler flag cpp-repl passes on: {a}')
        i += 1
    return out


# The flags a compile_commands.json entry contributes. -O, -g, -o and the like
# are the build's business, not the REPL's.
_KEPT_F = re.compile(r'-f(no-)?(exceptions|rtti|openmp|char8_t|signed-char|unsigned-char|'
                     r'ms-extensions|gnu-keywords|operator-names|sized-deallocation|'
                     r'aligned-new|coroutines|builtin)')
_SKIP_WITH_VALUE = {'-o', '-MF', '-MT', '-MQ', '-include', '-imacros', '-x', '-Xclang', '-target',
                    '--target', '-arch', '-isysroot', '--sysroot', '-main-file-name'}


def compile_command_flags(entry):
    """The flags worth keeping from one compile_commands.json entry."""
    args = entry.get('arguments') or shlex.split(entry['command'])
    base = Path(entry.get('directory') or '.')
    kept, i = [], 1
    while i < len(args):
        a = args[i]
        if a in _SKIP_WITH_VALUE:
            i += 2
            continue
        if a in _INCLUDE_FLAGS or a in ('-D', '-U'):
            kept += args[i:i + 2]
            i += 2
            continue
        if (a.startswith(_INCLUDE_FLAGS + ('-D', '-U', '-std=', '-Wno-', '-m'))
                or a == '-pthread' or _KEPT_F.fullmatch(a)):
            kept.append(a)
        i += 1
    return parse_flags(kept, base)


def compile_commands_file(path):
    """compile_commands.json itself, given the file or a build directory."""
    path = Path(path).expanduser()
    if path.is_dir():
        path = path / 'compile_commands.json'
    if not path.is_file():
        raise ConfigError(f'no compile_commands.json at {path}')
    return path.resolve()


def compile_commands_entry(path, files=()):
    """The entry for the first of files that has one, else the first entry."""
    try:
        entries = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f'cannot read {path}: {e}')
    if not entries:
        raise ConfigError(f'{path} has no entries')
    for f in (Path(f).resolve() for f in files):
        for entry in entries:
            source = Path(entry.get('file', ''))
            if not source.is_absolute():
                source = Path(entry.get('directory') or '.') / source
            if source.resolve() == f:
                return entry
    return entries[0]


_STD = re.compile(r'-std=(c|gnu)\+\+(\w+)')
_STD_YEAR = {'98': 98, '03': 98, '0x': 11, '11': 11, '1y': 14, '14': 14, '1z': 17, '17': 17,
             '2a': 20, '20': 20, '2b': 23, '23': 23, '2c': 26, '26': 26}


def floor_std(std):
    """(the -std flag to use, a note when it was raised). The prelude's
    constrained templates need C++20."""
    m = _STD.fullmatch(std)
    if not m:
        return std, None
    year = _STD_YEAR.get(m.group(2))
    if year is None or (year != 98 and year >= 20):
        return std, None
    raised = f'-std={m.group(1)}++20'
    return raised, f'{std} raised to {raised}: the prelude needs C++20'


def merge_flags(layers):
    """The final flag list from layers of parsed flags, lowest precedence
    first, and the -std and -O flags it settled on."""
    includes, seen = [], set()
    for layer in reversed(layers):
        for kind, flag in layer:
            if kind == 'include' and flag not in seen:
                seen.add(flag)
                includes.append(flag)
    defines, other, std, opt = [], [], None, None
    for layer in layers:
        for kind, flag in layer:
            if kind == 'define':
                defines.append(flag)
            elif kind == 'other':
                other.append(flag)
            elif kind == 'std':
                std = flag[0]
            elif kind == 'opt':
                opt = flag[0]

    def last_of_each(flags):
        out, seen = [], set()
        for flag in reversed(flags):
            if flag not in seen:
                seen.add(flag)
                out.append(flag)
        return out[::-1]

    flags = [a for flag in includes for a in flag]
    flags += [a for flag in last_of_each(other) + last_of_each(defines) for a in flag]
    return flags, std, opt


# ---------------------------------------------------------------------------
# Profiles and cpp-repl.toml files.
# ---------------------------------------------------------------------------

def builtin_profiles():
    """The names of the built-in profiles."""
    return sorted(m.name for m in pkgutil.iter_modules([str(PACKAGE / 'profiles')]) if m.ispkg)


def load_profile(name, ctx):
    """A built-in profile, configured for this session."""
    if name not in builtin_profiles():
        raise ConfigError(f'no profile {name!r}; the profiles are: {", ".join(builtin_profiles())}. '
                          'A profile of your own is a TOML file: -c FILE')
    module = importlib.import_module(f'{__package__}.profiles.{name}')
    return module.configure(ctx)


def read_toml(path):
    """A cpp-repl.toml file as a Profile. Relative paths in it are relative to
    the file. Its keys: std, include, defines, flags, libs, prelude, profiles,
    compile_commands."""
    path = Path(path).expanduser().resolve()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f'cannot read {path}: {e}')
    known = {'std', 'include', 'defines', 'flags', 'libs', 'prelude', 'profiles', 'compile_commands'}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f'{path}: unknown keys {", ".join(sorted(unknown))}; '
                          f'the keys are {", ".join(sorted(known))}')
    base = path.parent

    def strings(key):
        value = data.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f'{path}: {key} must be a list of strings')
        return value

    def resolved(p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else base / p

    flags = []
    if 'std' in data:
        std = str(data['std'])
        flags.append(std if std.startswith('-std=') else f'-std={std}')
    for directory in strings('include'):
        flags += ['-I', str(resolved(directory))]
    flags += ['-D' + d for d in strings('defines')]
    flags += strings('flags')
    parse_flags(flags, base)   # fail early on a bad flag
    profile = Profile(name=str(path), flags=flags, profiles=strings('profiles'),
                      libs=[resolved(p) for p in strings('libs')],
                      prelude=[resolved(p) for p in strings('prelude')], mounts=[base], base=base)
    if 'compile_commands' in data:
        profile.compile_commands = resolved(data['compile_commands'])
    return profile


def find_project_file(start):
    """The nearest cpp-repl.toml in start or a directory above it."""
    for d in [start, *start.parents]:
        candidate = d / PROJECT_FILE
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Libraries named with -l.
# ---------------------------------------------------------------------------

def _usual_library_dirs():
    """Where the linker looks when no -L directory has a library."""
    arch = sysconfig.get_config_var('MULTIARCH')
    dirs = [f'/usr/local/lib/{arch}', f'/lib/{arch}', f'/usr/lib/{arch}'] if arch else []
    return [Path(d) for d in dirs + ['/usr/local/lib', '/lib64', '/usr/lib64', '/lib', '/usr/lib']]


@functools.lru_cache(maxsize=None)
def _loader_cache():
    """{name: path} from the dynamic loader's cache (ldconfig -p), for this
    machine's word size: the libraries of directories that ld.so.conf adds."""
    tool = shutil.which('ldconfig') or next((p for p in ('/sbin/ldconfig', '/usr/sbin/ldconfig')
                                             if os.path.exists(p)), None)
    if not tool:
        return {}
    try:
        out = subprocess.run([tool, '-p'], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    wide, found = sys.maxsize > 2 ** 32, {}
    for name, tags, path in re.findall(r'^\s+lib(\S+?)\.so[\d.]* \(([^)]*)\) => (\S+)$', out, re.M):
        if wide == ('64' in tags):
            found.setdefault(name, Path(path))
    return found


def _is_elf(path):
    try:
        with open(path, 'rb') as f:
            return f.read(4) == b'\x7fELF'
    except OSError:
        return False


def _script_members(path):
    """What a linker script names in INPUT() and GROUP()."""
    try:
        text = re.sub(r'/\*.*?\*/', ' ', Path(path).read_text(errors='replace'), flags=re.S)
    except OSError:
        return []
    members = []
    for body in re.findall(r'\b(?:INPUT|GROUP)\s*\(((?:[^()]|\([^()]*\))*)\)', text):
        members += re.split(r'[\s,]+', re.sub(r'AS_NEEDED\s*\(([^()]*)\)', r'\1', body).strip())
    return [m for m in members if m]


def _shared_object(path, dirs):
    """path, or for a linker script such as glibc's libm.so, the first shared
    object it names: clang-repl loads shared objects only."""
    if _is_elf(path):
        return path
    for member in _script_members(path):
        if member.startswith('-l'):
            return find_library(member[2:], dirs)
        for candidate in [Path(member)] if member.startswith('/') else [path.parent / member,
                                                                         *(d / member for d in dirs)]:
            if candidate.suffix != '.a' and '.so' in candidate.name and _is_elf(candidate):
                return candidate
    return None


def find_library(name, dirs=()):
    """The shared library that -l name stands for, as the linker finds it:
    lib<name>.so in dirs (the -L directories), then $LIBRARY_PATH and the
    usual directories, then the loader's cache; or else lib<name>.so.N."""
    search = [Path(d) for d in dirs]
    search += [Path(d) for d in os.environ.get('LIBRARY_PATH', '').split(os.pathsep) if d]
    search += _usual_library_dirs()
    static = None
    for d in search:
        dev = d / f'lib{name}.so'
        found = _shared_object(dev, search) if dev.exists() else None
        if not found:
            versions = sorted(d.glob(f'lib{name}.so.*'), reverse=True,
                              key=lambda p: [int(n) for n in re.findall(r'\d+', p.name[len(name) + 6:])])
            found = next((v for v in versions if _is_elf(v)), None)
        if found:
            return found
        if not static and (d / f'lib{name}.a').exists():
            static = d / f'lib{name}.a'
    if name in _loader_cache():
        return _loader_cache()[name]
    if static:
        raise ConfigError(f'-l{name}: there is only {static}, a static archive, and clang-repl loads shared '
                          'libraries')
    raise ConfigError(f'no library for -l{name}: no lib{name}.so in the -L directories, $LIBRARY_PATH, the '
                      "usual places or the loader's cache. Add -L DIR, or give its path with --lib")


# ---------------------------------------------------------------------------
# The clang-repl binary.
# ---------------------------------------------------------------------------

def find_clang_repl(explicit=None):
    """The clang-repl to run: the one asked for, then $CPP_REPL_CLANG_REPL,
    then clang-repl on PATH, then the newest clang-repl-N on PATH or in
    /usr/lib/llvm-N/bin."""
    for candidate in (explicit, os.environ.get('CPP_REPL_CLANG_REPL')):
        if candidate:
            found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
            if not found:
                raise ConfigError(f'no clang-repl at {candidate}')
            return found
    found = shutil.which('clang-repl')
    if found:
        return found
    best = None
    for directory in (Path(d) for d in os.environ.get('PATH', '').split(os.pathsep) if d):
        if not directory.is_dir():
            continue
        for p in directory.glob('clang-repl-*'):
            m = re.fullmatch(r'clang-repl-(\d+)', p.name)
            if m and os.access(p, os.X_OK) and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), str(p))
    for p in Path('/usr/lib').glob('llvm-*/bin/clang-repl'):
        m = re.fullmatch(r'llvm-(\d+)', p.parent.parent.name)
        if m and os.access(p, os.X_OK) and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), str(p))
    if best:
        return best[1]
    raise ConfigError('no clang-repl found: install clang-tools-N (apt.llvm.org has the newest), '
                      'set CPP_REPL_CLANG_REPL, or run cpp-repl --docker')


def clang_repl_version(binary):
    """((major, minor, patch), text) from `clang-repl --version`."""
    try:
        out = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ConfigError(f'cannot run {binary}: {e}')
    m = re.search(r'LLVM version (\d+)\.(\d+)\.(\d+)', out)
    if not m:
        return (0, 0, 0), (out.strip().splitlines() or ['?'])[0]
    version = tuple(int(g) for g in m.groups())
    return version, '.'.join(map(str, version))


def sibling_clang(binary):
    """The clang++ that comes with a clang-repl, if any."""
    real = Path(binary).resolve()
    for name in ('clang++', 'clang'):
        candidate = real.parent / name
        if candidate.is_file():
            return str(candidate)
    m = re.fullmatch(r'clang-repl(-\d+)?', Path(binary).name)
    if m:
        return shutil.which('clang++' + (m.group(1) or ''))
    return None


# ---------------------------------------------------------------------------
# The configuration of one session.
# ---------------------------------------------------------------------------

def state_dir():
    base = os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state'
    return Path(base) / 'cpp-repl'


def _prune(directory, days, ended=lambda p: None):
    """Remove the files and directories in directory older than days, and
    those that ended(p) says are done with; ended(p) False keeps p."""
    cutoff = time.time() - days * 86400
    if not directory.is_dir():
        return
    for p in directory.iterdir():
        try:
            done = ended(p)
            if done or (done is None and p.lstat().st_mtime < cutoff):
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        except OSError:
            pass


def session_dir_name():
    """The name of a session's directory of inputs: this machine, this
    process and the time, so that another session can tell when it ended."""
    return f'{os.uname().nodename}-{os.getpid()}-{time.time_ns()}'


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def session_ended(p):
    """For a session directory on this machine, whether its process has
    gone; None for any other, such as a container's, whose process cannot
    be seen from here."""
    parts = p.name.rsplit('-', 2)
    if len(parts) != 3 or parts[0] != os.uname().nodename or not parts[1].isdigit():
        return None
    return not _alive(int(parts[1]))


@dataclass
class Options:
    """What the command line asks for."""
    files: list = field(default_factory=list)
    profiles: list = field(default_factory=list)
    configs: list = field(default_factory=list)
    no_config: bool = False
    compile_commands: str | None = None
    flags: list = field(default_factory=list)
    libs: list = field(default_factory=list)
    native: bool = False
    no_std: bool = False
    clang_repl: str | None = None
    colour: bool = False
    cwd: Path | None = None
    diagnostics: str = 'full'   # or 'brief': see diagnostics.py
    stop_on_error: bool = False
    timeout: float | None = None   # seconds an input may take, compiling included
    link_names: list = field(default_factory=list)   # -l NAME
    link_dirs: list = field(default_factory=list)    # -L DIR
    quiet: bool = False             # leave out the echo of each input run from a file
    json: bool = False              # a JSON object per input in place of the transcript


class Config:
    """Everything one session needs: the backend's command line, environment
    and first inputs, the prompt, and what the profiles add. With backend
    False, no clang-repl is looked for (cpp-repl --docker, --cflags); with
    links False, no -l library either (cpp-repl --docker, whose image
    looks for them)."""

    def __init__(self, options, backend=True, links=True):
        self.options = options
        # Paths on the command line are relative to where cpp-repl runs, like
        # the files, -c, --lib and --compile-commands; --cwd is the session's
        # own directory.
        self.here = Path.cwd()
        cwd = Path(options.cwd or self.here)
        self.notes = []

        # cpp-repl.toml files and profiles.
        tomls = []
        if not options.no_config:
            project = find_project_file(cwd)
            if project:
                tomls.append(read_toml(project))
        configs, names = list(options.configs), []
        for p in options.profiles:
            if p.endswith('.toml') or os.sep in p:
                configs.append(p)
            elif p not in names:
                names.append(p)
        tomls += [read_toml(c) for c in configs]
        for t in tomls:
            names = [n for n in t.profiles if n not in names] + names
        self.tomls = tomls

        compile_commands = options.compile_commands
        if compile_commands is None:
            compile_commands = next((t.compile_commands for t in reversed(tomls) if t.compile_commands), None)
        self.compile_commands = compile_commands_file(compile_commands) if compile_commands else None

        ctx = SimpleNamespace(compile_commands=self.compile_commands, env=os.environ, cwd=cwd)
        self.profiles = [load_profile(n, ctx) for n in names]

        layers = [parse_flags(p.flags) for p in self.profiles]
        if self.compile_commands:
            entry = compile_commands_entry(self.compile_commands, options.files)
            layers.append(compile_command_flags(entry))
        layers += [parse_flags(t.flags, t.base) for t in tomls]
        cli = list(options.flags) + (['-march=native'] if options.native else [])
        layers.append(parse_flags(cli, self.here))
        flags, std, opt = merge_flags(layers)
        self.std, note = floor_std(std or DEFAULT_STD)
        if note:
            self.notes.append(note)
        self.opt = opt
        self.opt_level = {None: 0, '-O': 1, '-O0': 0, '-O1': 1, '-O2': 2, '-O3': 3,
                          '-Ofast': 3, '-Os': 2, '-Oz': 2, '-Og': 1}.get(opt, -1)
        self.flags = flags
        self.compiler_flags = ([self.std] + ([opt] if opt else []) + flags
                               + ['-iquote', str(PRELUDE_DIR), '-DCPP_REPL=1'])

        self.preludes = [CORE_PRELUDE] + ([] if options.no_std else [STD_PRELUDE])
        for p in self.profiles + tomls:
            self.preludes += [Path(h) for h in p.prelude]
        for h in self.preludes:
            if not h.is_file():
                raise ConfigError(f'no prelude header {h}')
        libs = [Path(lib) for p in self.profiles + tomls for lib in p.libs]
        libs += [Path(lib).expanduser().resolve() for lib in options.libs]
        self.link_dirs = [(self.here / Path(d).expanduser()).resolve() for d in options.link_dirs]
        if links:
            libs += [find_library(name, self.link_dirs) for name in options.link_names]
        self.libs = list(dict.fromkeys(libs))
        for lib in self.libs:
            if not lib.is_file():
                raise ConfigError(f'no library {lib}')

        self.prompt = next((p.prompt for p in reversed(self.profiles) if p.prompt), PROMPT)
        self.more = '.' * (len(self.prompt) - 2) + '> '
        self.mounts = [Path(m) for p in self.profiles + tomls for m in p.mounts]

        # clang-repl keeps its line history in $HOME, and libraries such as
        # USD write the stack of a fatal error to $TMPDIR: both go under the
        # state directory.
        self.state = state_dir()
        self.home, self.tmp, self.inputs = (self.state / d for d in ('home', 'tmp', 'inputs'))
        self.history = self.state / 'history'
        self.clang_repl = self.version = self.version_text = self.argv = None
        if backend:
            for d in (self.home, self.tmp, self.inputs):
                d.mkdir(parents=True, exist_ok=True)
            _prune(self.tmp, days=7)
            # A live session replays its inputs from here, however old it is.
            _prune(self.inputs, days=7, ended=session_ended)
            self.clang_repl = find_clang_repl(options.clang_repl)
            self.version, self.version_text = clang_repl_version(self.clang_repl)
            brief = BRIEF_FLAGS if options.diagnostics == 'brief' else ()
            self.argv = ([self.clang_repl] + ['--Xcc=' + a for a in (*brief, *self.compiler_flags)]
                         + [a for h in self.preludes for a in ('--Xcc=-include', '--Xcc=' + str(h))]
                         + (['--Xcc=-fcolor-diagnostics'] if options.colour else []))
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(self.tmp))
        self.env.pop('LD_PRELOAD', None)

        # The first inputs of every backend. Core dumps go off before any
        # library loads, since a crash in a library's static initialiser
        # would otherwise leave a crash report (apport's, on Ubuntu).
        self.prologue = ['prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);'] if sys.platform.startswith('linux') else []
        self.prologue += ['setvbuf(stdout, nullptr, _IONBF, 0);']   # keep stdout in step with stderr
        self.prologue += [f'%lib {lib}' for lib in self.libs]

    # -- what profiles add -------------------------------------------------

    @property
    def blocks(self):
        return [b for p in self.profiles for b in p.blocks]

    @property
    def diagnostics(self):
        return [d for p in self.profiles for d in p.diagnostics]

    @property
    def help(self):
        return ''.join(p.help for p in self.profiles)

    @property
    def completion_dirs(self):
        return [Path(d) for p in self.profiles for d in p.completion]

    # -- descriptions --------------------------------------------------------

    def command(self):
        """A cpp-repl command line with this session's configuration, without
        its files. Its paths are absolute, so it works from any directory."""
        o = self.options

        def absolute(p):
            return str((self.here / Path(p).expanduser()).resolve())

        args = ['cpp-repl']
        for p in o.profiles:
            args += ['-p', absolute(p) if p.endswith('.toml') or os.sep in p else p]
        for c in o.configs:
            args += ['-c', absolute(c)]
        if o.compile_commands:
            args += ['--compile-commands', str(self.compile_commands)]
        if o.no_std:
            args.append('--no-std')
        if o.native:
            args.append('--native')
        args += [a for _, flag in parse_flags(o.flags, self.here) for a in flag]
        for lib in o.libs:
            args += ['--lib', absolute(lib)]
        for d in self.link_dirs:
            args += ['-L', str(d)]
        for name in o.link_names:
            args += ['-l', name]
        return shlex.join(args)

    def describe(self):
        """The startup line: backend, standard, optimisation, profiles."""
        parts = [f'clang-repl {self.version_text}', self.std.removeprefix('-std='),
                 self.opt or '-O0']
        parts += [p.banner or p.name for p in self.profiles]
        return ', '.join(parts)

    def cflags(self):
        """Flags to build a %save-cpp file with clang++ or g++."""
        flags = [self.std] + ([self.opt] if self.opt else []) + self.flags + ['-I', str(PRELUDE_DIR)]
        for h in self.preludes:
            flags += ['-I', str(h.parent)]
        return _dedupe(flags)

    def link_flags(self):
        """Flags to link a %save-cpp file against the session's libraries."""
        out = []
        for lib in self.libs:
            out += [f'-Wl,-rpath,{lib.parent}', str(lib)]
        return out


def _dedupe(flags):
    """flags without a repeated -I DIR or flag."""
    out, seen, i = [], set(), 0
    while i < len(flags):
        if flags[i] in _INCLUDE_FLAGS and i + 1 < len(flags):
            item = (flags[i], flags[i + 1])
            i += 2
        else:
            item = (flags[i],)
            i += 1
        if item not in seen:
            seen.add(item)
            out += item
    return out
