"""OpenUSD: the usd libraries loaded, `using namespace pxr`, and how stages,
layers, prims and properties print.

The install is found from the CMakeCache.txt next to the compile_commands.json
in use (its USD_ROOT), then $USD_ROOT, then the usual places. An install is a
directory holding include/pxr/pxr.h.
"""

import hashlib
import os
import re
import shutil
import sysconfig
from pathlib import Path

from ...config import ConfigError, Profile, state_dir

HERE = Path(__file__).resolve().parent

# Loaded into every backend before the first input, dependencies first; the
# ones an install lacks are skipped. The REPL would find the base libraries
# (arch to pcp) through usd's RUNPATH, but a %save-cpp program calls into them
# directly (SdfPath, TfToken, VtValue, ArchGetDemangled, and with Python support
# the python library's converters), and its native link needs each one on the
# command line.
LIBS = ['arch', 'python', 'tf', 'gf', 'js', 'trace', 'work', 'plug', 'vt', 'ts', 'ar', 'kind', 'sdf',
        'sdr', 'pcp', 'usd', 'usdGeom', 'usdShade', 'usdLux', 'usdSkel', 'usdPhysics', 'usdVol',
        'usdMedia', 'usdRender', 'usdUtils', 'usdUI', 'usdProc', 'usdSemantics']

CANDIDATES = ['~/USD', '~/OpenUSD', '~/OpenUSDBuild', '/usr/local/USD', '/opt/USD', '/opt/OpenUSD']

# Header directories whose class and method names Tab offers.
COMPLETION = ['usd/usd', 'usd/usdGeom', 'usd/usdLux', 'usd/usdShade', 'usd/sdf', 'usd/kind',
              'base/gf', 'base/vt', 'base/tf']

HELP = """\
  tree(stage)     the prim hierarchy of a stage or prim
  A stage prints its usda, a prim its type, properties and children, an
  attribute its value and time samples. %flags shows the standard in use:
  the profile asks for C++20, since OpenUSD's headers do not build as C++23
  or later (a std::unique_ptr of an incomplete type, which libstdc++'s
  constexpr unique_ptr rejects).
"""

# OpenUSD's headers as of 26.11 do not compile as C++23 or later with
# libstdc++ 13: std::unique_ptr<UsdPrimDefinition>, the type incomplete, hits
# the "sizeof an incomplete type" assertion of the constexpr unique_ptr. The
# profile's flags come lowest, so -std on the command line still wins.
STD = '-std=gnu++20'
STD_NOTE = f'the usd profile compiles as {STD.removeprefix("-std=")}: OpenUSD\'s headers do not build as C++23 or later'

# USD's own errors and warnings, coloured by severity.
DIAGNOSTICS = [(re.compile(r'(Coding Error|Runtime Error|Fatal Error|FATAL ERROR|Error|Warning|Status):'),
                {'Warning': '33', 'Status': '36'})]


def _is_root(path):
    return path is not None and (Path(path) / 'include' / 'pxr' / 'pxr.h').is_file()


def find_root(ctx):
    """The OpenUSD install for this session."""
    if ctx.compile_commands:
        cache = Path(ctx.compile_commands).parent / 'CMakeCache.txt'
        if cache.is_file():
            m = re.search(r'^USD_ROOT:PATH=(.*)$', cache.read_text(errors='replace'), re.M)
            if m:
                # The build's own OpenUSD, or nothing: another install found
                # below would not match what the build compiles against.
                if not _is_root(m.group(1).strip()):
                    raise ConfigError(f'{cache} has USD_ROOT={m.group(1).strip()}, which holds no '
                                      'include/pxr/pxr.h')
                return Path(m.group(1).strip())
    for name in ('USD_ROOT', 'PXR_ROOT'):
        value = ctx.env.get(name)
        if value:
            if not _is_root(value):
                raise ConfigError(f'${name} is {value}, which holds no include/pxr/pxr.h')
            return Path(value)
    for candidate in CANDIDATES:
        path = Path(candidate).expanduser()
        if _is_root(path):
            return path
    raise ConfigError('the usd profile needs an OpenUSD install: set USD_ROOT to the directory '
                      'holding include/pxr and lib')


def version(root):
    text = (root / 'include' / 'pxr' / 'pxr.h').read_text(errors='replace')
    parts = re.findall(r'#define PXR_(?:MAJOR|MINOR|PATCH)_VERSION (\d+)', text)
    return '.'.join(parts) or '?'


def python_include(root):
    """The Python headers the install was built against, which its headers
    include when Python support is on; None when it is off."""
    pxr_h = (root / 'include' / 'pxr' / 'pxr.h').read_text(errors='replace')
    if not re.search(r'^#define PXR_PYTHON_SUPPORT_ENABLED', pxr_h, re.M):
        return None
    config = root / 'pxrConfig.cmake'
    if config.is_file():
        m = re.search(r'set\(Python3_INCLUDE_DIR \[\[(.*?)\]\]\)', config.read_text(errors='replace'))
        if m and (Path(m.group(1)) / 'Python.h').is_file():
            return Path(m.group(1))
    include = sysconfig.get_paths().get('include')
    if include and (Path(include) / 'Python.h').is_file():
        return Path(include)
    raise ConfigError(f'OpenUSD at {root} was built with Python support, and no Python.h was found: '
                      'install python3-dev')


def defines(root):
    """The compile definitions the install's CMake targets export."""
    targets = root / 'cmake' / 'pxrTargets.cmake'
    found = []
    if targets.is_file():
        for value in re.findall(r'INTERFACE_COMPILE_DEFINITIONS "([^"]*)"', targets.read_text(errors='replace')):
            found += [d for d in value.split(';') if d and '$<' not in d]
    return list(dict.fromkeys(found + ['TBB_SUPPRESS_DEPRECATED_MESSAGES=1']))


# TBB 2020, which OpenUSD's build_usd.py installs on Linux, declares
# task_group_context::kind_type without a fixed underlying type, then gives a
# static constant of it a value outside the enumeration's range: clang 20 and
# newer reject that, and the error can no longer be turned off. The session
# reads a copy of the TBB headers in which the enumeration is `: unsigned`,
# the type compilers already give it, so its size and the ABI stay the same.
# The whole tbb/ directory is copied, since its headers include one another by
# relative path, each marked a system header, as the install's are; the copy
# comes before every include directory (Profile.overlays), a build's
# -isystem of the install included.
_KIND_TYPE = 'enum kind_type {'
_OVERLAY_FORMAT = 2   # changes when the copy is made differently
TBB_NOTE = ("TBB 2020's headers come from a patched copy: clang 20 and newer reject an enumeration constant "
            'in tbb/task.h')


def tbb_overlay(root):
    """A directory holding a patched copy of the install's tbb/ headers, to
    put ahead of the install's own; None when its TBB needs no patch."""
    tbb = root / 'include' / 'tbb'
    try:
        text = (tbb / 'task.h').read_text()
    except OSError:
        return None
    if _KIND_TYPE not in text:
        return None
    digest = hashlib.sha256(f'{tbb}\n{text}\n{_OVERLAY_FORMAT}'.encode()).hexdigest()[:12]
    overlay = state_dir() / 'overlay' / f'tbb-{digest}'
    if (overlay / 'tbb' / 'task.h').is_file():
        return overlay
    work = overlay.with_name(f'{overlay.name}.building-{os.getpid()}')
    shutil.rmtree(work, ignore_errors=True)
    try:
        shutil.copytree(tbb, work / 'tbb')
        (work / 'tbb' / 'task.h').write_text(text.replace(_KIND_TYPE, 'enum kind_type : unsigned {', 1))
        for header in (f for f in (work / 'tbb').rglob('*') if f.is_file() and f.suffix in ('.h', '')):
            header.write_bytes(b'#pragma GCC system_header\n' + header.read_bytes())
        os.rename(work, overlay)
    except OSError:
        shutil.rmtree(work, ignore_errors=True)
        if not (overlay / 'tbb' / 'task.h').is_file():   # another session made it meanwhile, or nobody did
            raise ConfigError(f'cannot write a patched copy of {tbb} under {overlay.parent}')
    return overlay


def libraries(root):
    lib = root / 'lib'
    monolithic = lib / 'libusd_ms.so'
    if monolithic.is_file():
        return [monolithic]
    for pattern in ('libusd_{}.so', 'lib{}.so'):
        found = [lib / pattern.format(name) for name in LIBS if (lib / pattern.format(name)).is_file()]
        if found:
            return found
    raise ConfigError(f'no OpenUSD libraries in {lib}')


def configure(ctx):
    root = find_root(ctx)
    overlay = tbb_overlay(root)
    flags = [STD, '-isystem', str(root / 'include')]
    python = python_include(root)
    if python:
        flags += ['-isystem', str(python)]
    flags += ['-D' + d for d in defines(root)] + ['-Wno-deprecated']
    include = root / 'include' / 'pxr'
    return Profile(name='usd', flags=flags, libs=libraries(root), prelude=[HERE / 'cpp_repl_usd.h'],
                   prompt='usd> ', blocks=[(re.compile(r'#usda '), 'usd')], diagnostics=DIAGNOSTICS,
                   completion=[include / d for d in COMPLETION], help=HELP,
                   banner=f'OpenUSD {version(root)}', mounts=[root],
                   notes=[STD_NOTE] + ([TBB_NOTE] if overlay else []), std=STD,
                   overlays=[overlay] if overlay else [])
