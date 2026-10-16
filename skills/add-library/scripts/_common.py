"""What the add-library scripts share: the cpp_repl checkout they ship in,
commands run with deadlines, ELF checks, Debian package owners, and TOML text.

The scripts live in skills/add-library/scripts/ of a cpp_repl checkout (or of
a plugin's copy of one). They import cpp_repl from that checkout's src/, so
that flags, profiles and Docker mounts follow the same rules as cpp-repl.
"""

import contextlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

if sys.version_info < (3, 11):
    sys.stdout.write(json.dumps({'error': 'these scripts need Python 3.11 or newer; this is '
                                          + platform.python_version()}) + '\n')
    sys.exit(4)

CHECKOUT = Path(__file__).resolve().parents[3]
CPP_REPL_BIN = CHECKOUT / 'bin' / 'cpp-repl'
if not (CHECKOUT / 'src' / 'cpp_repl' / '__init__.py').is_file():
    sys.stdout.write(json.dumps({'error': f'no cpp_repl checkout at {CHECKOUT}: the scripts run from '
                                          'skills/add-library/scripts/ inside one'}) + '\n')
    sys.exit(4)
sys.path.insert(0, str(CHECKOUT / 'src'))

# Exit statuses shared by the scripts.
OK, FAILED, USAGE, PROBLEMS, TOOL_MISSING, TIMEOUT = 0, 1, 2, 3, 4, 124

# Libraries every process has already: never listed in a profile.
SYSTEM_LIBS = {'c', 'm', 'dl', 'pthread', 'rt', 'util', 'gcc_s', 'gcc', 'stdc++', 'atomic', 'resolv'}


def emit(obj, code=OK):
    """Print obj as JSON on stdout and exit with code."""
    sys.stdout.write(json.dumps(obj, indent=2, default=str) + '\n')
    sys.exit(code)


def note(text):
    """A diagnostic line on stderr."""
    sys.stderr.write(text.rstrip('\n') + '\n')


def run(args, timeout=120, **kwargs):
    """(returncode, stdout, stderr) of a command. The return code is None when
    the command is missing or passes its deadline."""
    try:
        p = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, errors='replace', **kwargs)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return None, '', str(e)
    except subprocess.TimeoutExpired:
        return None, '', f'{Path(str(args[0])).name} passed its {timeout} s deadline'


def multiarch():
    """The Debian multiarch triplet, such as x86_64-linux-gnu."""
    return sysconfig.get_config_var('MULTIARCH') or f'{platform.machine()}-linux-gnu'


def project_root(start):
    """The top of the git repository holding start, else start."""
    code, out, _ = run(['git', '-C', start, 'rev-parse', '--show-toplevel'], timeout=10)
    return Path(out.strip()) if code == 0 and out.strip() else Path(start).resolve()


def state_dir_status():
    """(cpp-repl's state directory, whether it can be written). It is
    written to find out: a sandbox can refuse what the permission bits allow."""
    from cpp_repl.config import state_dir
    path = state_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f'.probe-{os.getpid()}'
        probe.write_text('')
        probe.unlink()
        return path, True
    except OSError:
        return path, False


def ignore_in_dot_dir(root, entry):
    """Add entry to the project's .cpp-repl/.gitignore, so that a scratch
    directory stays out of git when the profile is committed."""
    ignore = Path(root) / '.cpp-repl' / '.gitignore'
    try:
        lines = ignore.read_text().splitlines() if ignore.is_file() else []
        if entry not in lines:
            ignore.parent.mkdir(parents=True, exist_ok=True)
            ignore.write_text('\n'.join(lines + [entry]) + '\n')
    except OSError:
        pass


def make_temp(prefix):
    """A new temporary directory: in $TMPDIR, or in the project's .cpp-repl/.tmp
    when a sandbox lets the scripts write only inside the project."""
    try:
        return Path(tempfile.mkdtemp(prefix=prefix))
    except OSError:
        root = project_root(Path.cwd())
        base = root / '.cpp-repl' / '.tmp'
        base.mkdir(parents=True, exist_ok=True)
        ignore_in_dot_dir(root, '.tmp/')
        return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


@contextlib.contextmanager
def scratch(prefix):
    """make_temp() as a context manager that removes the directory."""
    path = make_temp(prefix)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# The compiler next to clang-repl.
# ---------------------------------------------------------------------------

def toolchain(explicit=None, old_ok=False):
    """(clang-repl path, version text, major, clang++ path) for the clang-repl
    cpp-repl would run; raises cpp_repl's ConfigError when there is none, and,
    unless old_ok, when it is older than cpp-repl runs, in cpp-repl's words."""
    from cpp_repl.config import MIN_VERSION, ConfigError, clang_repl_version, find_clang_repl, sibling_clang
    binary = find_clang_repl(explicit)
    version, text = clang_repl_version(binary)
    if not old_ok and (0, 0, 0) < version < MIN_VERSION:
        raise ConfigError(too_old(binary, text))
    return binary, text, version[0], sibling_clang(binary)


def too_old(binary, text):
    """What cpp-repl says of a clang-repl older than it runs (config.py)."""
    from cpp_repl.config import MIN_VERSION
    major = MIN_VERSION[0]
    return (f'{binary} is clang-repl {text}, and cpp-repl needs {major} or newer: install clang-{major} and '
            f'clang-tools-{major} from apt.llvm.org, or run cpp-repl --docker')


def implicit_include_dirs(clangxx):
    """The directories clang++ searches by itself for <...> includes."""
    dirs = {'/usr/include', '/usr/local/include'}
    if clangxx:
        code, _, err = run([clangxx, '-E', '-x', 'c++', '-v', '/dev/null'], timeout=30)
        inside = False
        for line in err.splitlines():
            if line.startswith('#include <...> search starts here'):
                inside = True
            elif line.startswith('End of search list'):
                inside = False
            elif inside and line.strip():
                dirs.add(os.path.normpath(line.strip()))
    return dirs


def print_file_name(clangxx, name):
    """The path clang++ would link for a library file name, or None."""
    if not clangxx:
        return None
    code, out, _ = run([clangxx, f'-print-file-name={name}'], timeout=20)
    path = out.strip()
    return path if code == 0 and os.path.isabs(path) and os.path.exists(path) else None


# ---------------------------------------------------------------------------
# Shared libraries.
# ---------------------------------------------------------------------------

def elf_kind(path):
    """What a library file is: 'shared', 'executable', 'object', 'archive',
    'script' (a linker script), 'other', or None when it cannot be read."""
    try:
        with open(path, 'rb') as f:
            head = f.read(4096)
    except OSError:
        return None
    if head.startswith(b'!<arch>'):
        return 'archive'
    if head.startswith(b'\x7fELF'):
        e_type = int.from_bytes(head[16:18], 'little' if head[5] == 1 else 'big')
        return {1: 'object', 2: 'executable', 3: 'shared'}.get(e_type, 'other')
    if re.search(rb'\b(INPUT|GROUP)\s*\(', head):
        return 'script'
    return 'other'


def script_members(path):
    """The files and -l names a linker script names in INPUT() and GROUP()."""
    text = Path(path).read_text(errors='replace')
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    members = []
    for body in re.findall(r'\b(?:INPUT|GROUP)\s*\(((?:[^()]|\([^()]*\))*)\)', text):
        body = re.sub(r'AS_NEEDED\s*\(([^()]*)\)', r'\1', body)
        members += [m for m in re.split(r'[\s,]+', body) if m]
    return members


def dynamic_section(path):
    """{'soname', 'needed', 'runpath'} from readelf -d, with $ORIGIN expanded."""
    code, out, _ = run(['readelf', '-d', '-W', path], timeout=30)
    info = {'soname': None, 'needed': [], 'runpath': []}
    if code != 0:
        return info
    origin = str(Path(path).resolve().parent)
    for kind, value in re.findall(r'\((NEEDED|SONAME|RUNPATH|RPATH)\)\s+[^[]*\[([^\]]*)\]', out):
        if kind == 'NEEDED':
            info['needed'].append(value)
        elif kind == 'SONAME':
            info['soname'] = value
        else:
            info['runpath'] += [p.replace('$ORIGIN', origin).replace('${ORIGIN}', origin)
                                for p in value.split(':') if p]
    return info


_LDCONFIG = None


def ldconfig_cache():
    """{file name: path} of the dynamic loader's cache for this machine's
    architecture (ldconfig -p)."""
    global _LDCONFIG
    if _LDCONFIG is None:
        _LDCONFIG = {}
        tag = {'x86_64': 'x86-64', 'aarch64': 'AArch64'}.get(platform.machine())
        for binary in ('ldconfig', '/sbin/ldconfig', '/usr/sbin/ldconfig'):
            code, out, _ = run([binary, '-p'], timeout=30)
            if code == 0:
                for name, flags, path in re.findall(r'^\s*(\S+)\s+\(([^)]*)\)\s+=>\s+(\S+)', out, re.M):
                    if tag is None or tag in flags:
                        _LDCONFIG.setdefault(name, path)
                break
    return _LDCONFIG


def find_needed(name, runpath=()):
    """Where the loader finds a DT_NEEDED name, or None."""
    for d in list(runpath) + ['/lib/' + multiarch(), '/usr/lib/' + multiarch(), '/lib64', '/usr/lib64',
                              '/lib', '/usr/lib']:
        candidate = Path(d) / name
        if candidate.exists():
            return str(candidate)
    return ldconfig_cache().get(name)


def canonical(path):
    """A library path in one spelling: normalised, and under /usr on a merged
    /usr system (/lib/x86_64-linux-gnu/libfmt.so is
    /usr/lib/x86_64-linux-gnu/libfmt.so)."""
    p = os.path.normpath(str(path))
    if p.startswith(('/lib/', '/lib64/')):
        usr = '/usr' + p
        try:
            if os.path.exists(usr) and os.path.samefile(usr, p):
                return usr
        except OSError:
            pass
    return p


def dev_link(path):
    """The lib*.so link beside a versioned library file that points to the
    same file, else the path itself. A profile that names the link survives a
    patch upgrade of the library."""
    p = Path(path)
    m = re.match(r'(lib.+?\.so)(\.[0-9][0-9A-Za-z_]*)+$', p.name)
    if m:
        link = p.with_name(m.group(1))
        try:
            if link.exists() and link.resolve() == p.resolve():
                return str(link)
        except OSError:
            pass
    return str(p)


def defines_cxx_abi(path):
    """'libc++' when a library was built against libc++, else None."""
    code, out, _ = run(['nm', '-D', '--defined-only', path], timeout=60)
    if code == 0 and 'NSt3__1' in out:
        return 'libc++'
    return None


# ---------------------------------------------------------------------------
# Debian packages.
# ---------------------------------------------------------------------------

def _alternate(path):
    """The same path through the other side of a merged /usr (/lib and /usr/lib)."""
    for a, b in (('/usr/lib/', '/lib/'), ('/lib/', '/usr/lib/'), ('/usr/lib64/', '/lib64/'),
                 ('/lib64/', '/usr/lib64/')):
        if path.startswith(a):
            return b + path[len(a):]
    return None


def _dpkg_search(paths):
    """{path: {package, ...}} from dpkg-query -S, a few hundred paths per call."""
    found = {}
    for i in range(0, len(paths), 200):
        chunk = paths[i:i + 200]
        wanted = set(chunk)
        _, out, _ = run(['dpkg-query', '-S', *chunk], timeout=120)
        for line in out.splitlines():
            if line.startswith(('diversion ', 'local diversion')):
                continue
            packages, sep, owned = line.rpartition(': ')
            if sep and owned.strip() in wanted:
                found.setdefault(owned.strip(), set()).update(
                    p.strip().split(':')[0] for p in packages.split(',') if p.strip())
    return found


def apt_owners(paths):
    """{path: [package, ...]} for the paths an installed Debian package owns.
    A path dpkg does not know is tried through the other side of a merged /usr."""
    if not shutil.which('dpkg-query'):
        return {}
    paths = list(dict.fromkeys(str(p) for p in paths))
    owners = _dpkg_search(paths)
    alternates = {a: p for p in paths if p not in owners for a in [_alternate(p)] if a}
    for alternate, found in _dpkg_search(list(alternates)).items():
        owners[alternates[alternate]] = found
    return {path: sorted(found) for path, found in owners.items()}


# ---------------------------------------------------------------------------
# cpp-repl.toml text.
# ---------------------------------------------------------------------------

TOML_KEYS = ('std', 'compile_commands', 'profiles', 'include', 'flags', 'defines', 'libs', 'prelude', 'pch')
_PAIRED = ('-I', '-isystem', '-iquote', '-idirafter', '-D', '-U')


def _flag_items(flags):
    """flags grouped so that a flag and its value share a line."""
    items, i = [], 0
    while i < len(flags):
        if flags[i] in _PAIRED and i + 1 < len(flags):
            items.append(flags[i:i + 2])
            i += 2
        else:
            items.append(flags[i:i + 1])
            i += 1
    return items


def toml_text(data, comments=()):
    """data, a dict of the keys cpp-repl.toml reads, as TOML text with
    comment lines first."""
    lines = [f'# {c}'.rstrip() for c in comments]
    for key in TOML_KEYS:
        value = data.get(key)
        if value in (None, '', []):
            continue
        if isinstance(value, (str, bool)):
            lines.append(f'{key} = {json.dumps(value)}')
            continue
        items = _flag_items(value) if key == 'flags' else [[v] for v in value]
        one_line = f'{key} = [' + ', '.join(json.dumps(v) for v in value) + ']'
        if len(one_line) <= 100 and len(items) <= 2:
            lines.append(one_line)
        else:
            lines.append(f'{key} = [')
            lines += ['  ' + ', '.join(json.dumps(v) for v in item) + ',' for item in items]
            lines.append(']')
    return '\n'.join(lines) + '\n'


def read_toml_data(path):
    """The raw keys of a cpp-repl.toml file."""
    import tomllib
    return tomllib.loads(Path(path).read_text())


def shell_join(args):
    return shlex.join(str(a) for a in args)
