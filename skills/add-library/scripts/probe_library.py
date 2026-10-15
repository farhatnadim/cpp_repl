#!/usr/bin/env python3
"""Find a C++ library and work out what cpp-repl needs to load it.

  probe_library.py env                      the toolchain, Docker and the project
  probe_library.py find NAME [--scan DIR]   CMake packages, pkg-config modules and headers named like NAME
  probe_library.py resolve --cmake PKG [--components a,b] [--targets T,..]
  probe_library.py resolve --pkg-config MODULE[,MODULE] [--keep-libs NAME,..]
  probe_library.py resolve --headers DIR [--lib PATH]... [--define D]...
                                            include directories, defines, shared libraries, and a draft TOML
  probe_library.py image --config TOML [--image IMAGE]
                                            what a Docker image lacks for a profile, and a Dockerfile
  probe_library.py symbol MANGLED (--config TOML | --dir DIR...)
                                            which shared library defines a symbol

Each command prints one JSON object on stdout; diagnostics go to stderr.
Exit status: 0 ok, 3 resolved with problems, 2 not found or bad usage,
4 a tool is missing.
"""

import argparse
import datetime
import json
import os
import re
import shlex
import shutil
from pathlib import Path

from _common import (CHECKOUT, CPP_REPL_BIN, OK, PROBLEMS, SYSTEM_LIBS, TOOL_MISSING, USAGE, apt_owners,
                     canonical, dev_link, defines_cxx_abi, dynamic_section, elf_kind, emit, find_needed,
                     implicit_include_dirs, ldconfig_cache, multiarch, print_file_name,
                     project_root, run, scratch, script_members, state_dir_status, toml_text, toolchain)

CPP_SUFFIXES = {'.cpp', '.cc', '.cxx', '.c++', '.h', '.hh', '.hpp', '.hxx', '.ipp', '.inl', '.cu'}
SKIP_DIRS = {'.git', '.cpp-repl', 'node_modules', '.venv', 'venv', '__pycache__', '.cache'}


def norm(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def profile_name(text):
    """A file-name-friendly profile name: yaml-cpp, opencv, glm."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or 'library'


def _key(text):
    """text lower-cased, without punctuation, a leading lib or a trailing
    version number: OpenCV, opencv4 and libopencv all give opencv."""
    key = re.sub(r'^lib(?=.)', '', norm(text))
    return re.sub(r'\d+$', '', key) or key


def similar(name, other):
    """Whether other (a package, module or directory name) names the library
    called name: the same key, a word of it (yaml in yaml-cpp), or a longer
    name that starts with it (opencv_core for opencv)."""
    a, b = _key(name), _key(other)
    if not a or not b:
        return False
    if a == b:
        return True
    words = [re.sub(r'\d+$', '', w) for w in re.split(r'[^a-z0-9]+', other.lower()) if w]
    return a in words or (len(a) >= 4 and b.startswith(a))


# ---------------------------------------------------------------------------
# env
# ---------------------------------------------------------------------------

def cmd_env(args):
    from cpp_repl import __version__
    from cpp_repl.config import ConfigError, builtin_profiles, find_project_file
    cwd = Path.cwd()
    on_path = shutil.which('cpp-repl')
    # Inside Claude Code the plugin's bin/ is on PATH; a terminal has it only
    # when cpp-repl was installed (pipx, pip, a clone's bin/ on PATH).
    plugin_copy = bool(on_path) and Path(on_path).resolve() == CPP_REPL_BIN.resolve()
    out = {'cpp_repl': {'checkout': str(CHECKOUT), 'bin': str(CPP_REPL_BIN), 'version': __version__,
                        'on_path': on_path, 'on_path_is_this_checkout': plugin_copy,
                        'builtin_profiles': builtin_profiles()}}
    try:
        binary, text, major, clangxx = toolchain(args.clang_repl)
        out['clang_repl'] = {'path': binary, 'version': text, 'major': major, 'clangxx': clangxx}
    except ConfigError as e:
        out['clang_repl'] = {'error': str(e)}
    docker = {'available': False, 'images': []}
    if shutil.which('docker'):
        code, stdout, err = run(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'], timeout=30)
        docker = {'available': code == 0, 'images': sorted(i for i in stdout.split() if i.startswith('cpp-repl'))}
        if code != 0:
            docker['error'] = (err.strip().splitlines() or ['docker images failed'])[-1]
    out['docker'] = docker
    out['tools'] = {t: shutil.which(t) for t in ('cmake', 'pkg-config', 'readelf', 'nm', 'c++filt',
                                                 'dpkg-query', 'git')}
    root = project_root(cwd)
    dot = root / '.cpp-repl'
    project_toml = find_project_file(cwd)
    commands = [p for pattern in ('compile_commands.json', '*/compile_commands.json', '*/*/compile_commands.json')
                for p in sorted(root.glob(pattern)) if not set(p.relative_to(root).parts) & SKIP_DIRS]
    out['project'] = {'root': str(root), 'cpp_repl_toml': str(project_toml) if project_toml else None,
                      'dot_dir': str(dot), 'profiles': sorted(str(p) for p in dot.glob('*.toml')),
                      'compile_commands': [str(p) for p in commands]}
    path, writable = state_dir_status()
    out['state_dir'] = {'path': str(path), 'writable': writable}
    notes = []
    major = out['clang_repl'].get('major')
    if major and major < 23:
        notes.append(f"clang-repl {major}'s JIT cannot assemble inline asm (Eigen's dynamic-size products, "
                     'some SIMD code); cpp-repl --docker runs clang-repl 23')
    if major and major >= 23:
        notes.append('clang 23 rejects the TBB 2020 headers that some OpenUSD builds use')
    if 'error' in out['clang_repl'] and not docker['images']:
        notes.append('no clang-repl and no cpp-repl image: install clang-tools-18 (Ubuntu) or a newer one '
                     f'from apt.llvm.org, or build the image with: make -C {CHECKOUT} docker')
    if not writable:
        notes.append(f'{path} cannot be written: run cpp-repl with XDG_STATE_HOME set to a writable directory')
    out['notes'] = notes
    emit(out)


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------

def prefixes(extra):
    """Install prefixes to search, most specific first."""
    found = [Path(p).expanduser() for p in extra]
    found += [Path(p) for p in os.environ.get('CMAKE_PREFIX_PATH', '').split(os.pathsep) if p]
    for name in ('CONDA_PREFIX', 'VCPKG_INSTALLED_DIR'):
        if os.environ.get(name):
            found.append(Path(os.environ[name]))
    found += [Path.home() / '.local', Path('/usr/local'), Path('/usr')]
    opt = Path('/opt')
    if opt.is_dir():
        found += sorted(p for p in opt.iterdir() if p.is_dir())
    seen, out = set(), []
    for p in found:
        if p.is_dir() and p.resolve() not in seen:
            seen.add(p.resolve())
            out.append(p)
    return out


def _config_files(directory):
    out = []
    for d in (directory, directory / 'cmake', directory / 'CMake'):
        if d.is_dir():
            out += [f for f in d.iterdir() if f.is_file() and re.fullmatch(r'.+(Config|-config)\.cmake', f.name)]
    return out


def cmake_packages(name, roots):
    """CMake package config files whose package or directory name is like name."""
    arch = multiarch()
    found, seen = [], set()
    for prefix in roots:
        bases = [prefix / 'lib' / arch / 'cmake', prefix / 'lib' / 'cmake', prefix / 'lib64' / 'cmake',
                 prefix / 'share' / 'cmake', prefix / 'share', prefix / 'lib' / arch, prefix / 'lib', prefix]
        for base in bases:
            if not base.is_dir():
                continue
            try:
                children = [c for c in base.iterdir() if c.is_dir()]
            except OSError:
                continue
            for child in children:
                if not similar(name, child.name):
                    continue
                for config in _config_files(child):
                    package = re.sub(r'(Config|-config)\.cmake$', '', config.name)
                    key = config.resolve()
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(describe_cmake_package(package, config))
    found.sort(key=lambda p: (norm(p['package']) != norm(name), len(p['package'])))
    return found


def describe_cmake_package(package, config):
    directory = config.parent
    text = ''
    for f in sorted(directory.glob('*.cmake')):
        try:
            text += f.read_text(errors='replace') + '\n'
        except OSError:
            pass
    version = None
    m = (re.search(r'PACKAGE_VERSION\s+"?([0-9][\w.]*)', text)
         or re.search(r'_VERSION\s+"?([0-9]+\.[0-9][\w.]*)', text))
    if m:
        version = m.group(1)
    targets = []
    for target, kind in re.findall(r'add_library\(\s*([\w:.+-]+)\s+(SHARED|STATIC|MODULE|UNKNOWN|INTERFACE|OBJECT)'
                                   r'\s+IMPORTED', text):
        if {'name': target, 'type': kind} not in targets:
            targets.append({'name': target, 'type': kind})
    return {'package': package, 'config': str(config), 'version': version, 'imported_targets': targets}


def components_of(package, targets):
    """The components of a package whose imported targets name them:
    opencv_core for OpenCV, Boost::filesystem, Qt6::Widgets. A target named
    after the package itself (spdlog::spdlog, glm::glm) is no component."""
    key, found = _key(package), set()
    for target in targets:
        name = target['name']
        if '::' in name:
            namespace, _, part = name.partition('::')
            if _key(namespace) == key and _key(part) != key and not part.startswith(namespace + '_'):
                found.add(part)
        elif '_' in name:
            prefix, _, part = name.partition('_')
            if _key(prefix) == key and part:
                found.add(part)
    return found


def pkg_config_modules(name):
    if not shutil.which('pkg-config'):
        return []
    code, out, _ = run(['pkg-config', '--list-all'], timeout=30)
    modules = []
    for line in out.splitlines() if code == 0 else []:
        module = line.split(None, 1)[0] if line.strip() else ''
        if module and similar(name, module):
            _, version, _ = run(['pkg-config', '--modversion', module], timeout=10)
            _, cflags, _ = run(['pkg-config', '--cflags-only-I', module], timeout=10)
            modules.append({'module': module, 'version': version.strip() or None,
                            'include_dirs': [f[2:] for f in shlex.split(cflags) if f.startswith('-I')]})
    modules.sort(key=lambda m: (norm(m['module']) != norm(name), len(m['module'])))
    return modules


def header_candidates(name, roots):
    found = []
    for prefix in roots:
        include = prefix / 'include'
        if not include.is_dir():
            continue
        for child in sorted(include.iterdir()):
            stem = child.name.split('.')[0] if child.is_file() else child.name
            if not similar(name, stem):
                continue
            if child.is_dir():
                sample = sorted(p.name for p in child.iterdir() if p.suffix in CPP_SUFFIXES or not p.suffix)[:6]
                found.append({'dir': str(child), 'include_root': str(include), 'sample': sample})
            elif child.suffix in CPP_SUFFIXES or child.suffix == '':
                found.append({'file': str(child), 'include_root': str(include)})
    return found


def project_includes(directory, limit=4000):
    """{include path: count} of the #include lines in a project's sources."""
    counts, seen = {}, 0
    pattern = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(('build', 'cmake-build'))]
        for f in files:
            if Path(f).suffix not in CPP_SUFFIXES:
                continue
            seen += 1
            if seen > limit:
                return counts
            try:
                text = (Path(root) / f).read_text(errors='replace')
            except OSError:
                continue
            for include in pattern.findall(text):
                counts[include] = counts.get(include, 0) + 1
    return counts


def cmd_find(args):
    roots = prefixes(args.prefix)
    out = {'name': args.name, 'cmake': cmake_packages(args.name, roots),
           'pkg_config': pkg_config_modules(args.name), 'headers': header_candidates(args.name, roots)}
    # The top-level names under the library's include directories: what a
    # project's #include lines start with when they use it.
    tops = set()
    for h in out['headers']:
        path = Path(h.get('dir') or h['file'])
        tops.add(path.name)
        if path.is_dir():   # opencv4/ is itself an include root: <opencv2/core.hpp>
            tops |= {p.name for p in path.iterdir() if p.is_dir()}
    for m in out['pkg_config']:
        for d in m['include_dirs']:
            if Path(d).is_dir():
                tops |= {p.name for p in Path(d).iterdir()}
    includes = []
    if args.scan:
        counts = project_includes(Path(args.scan))
        includes = [{'include': i, 'count': c} for i, c in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
                    if i.split('/')[0] in tops]
    out['project_includes'] = includes[:40]
    paths = [p['config'] for p in out['cmake']] + [h.get('dir') or h['file'] for h in out['headers']]
    out['owners'] = apt_owners(paths)
    if out['cmake']:
        best = out['cmake'][0]
        stems = {Path(i['include']).stem.lower() for i in includes}
        components = sorted(c for c in components_of(best['package'], best['imported_targets'])
                            if c.lower() in stems)
        out['suggest'] = {'source': 'cmake', 'package': best['package'], 'components': components,
                          'command': ['resolve', '--cmake', best['package']]
                                     + (['--components', ','.join(components)] if components else [])}
    elif out['pkg_config']:
        out['suggest'] = {'source': 'pkg-config', 'module': out['pkg_config'][0]['module'],
                          'command': ['resolve', '--pkg-config', out['pkg_config'][0]['module']]}
    elif out['headers']:
        h = out['headers'][0]
        out['suggest'] = {'source': 'headers', 'dir': h['include_root'],
                          'command': ['resolve', '--headers', h['include_root']]}
    else:
        out['suggest'] = None
        emit(out, USAGE)
    emit(out)


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------

CMAKELISTS = r'''cmake_minimum_required(VERSION 3.16)
project(cpp_repl_probe CXX)
find_package(@PACKAGE@ REQUIRED @COMPONENTS@)
set(cpp_repl_targets @TARGETS@)
set(cpp_repl_from "--targets")
if(NOT cpp_repl_targets)
  foreach(var @PACKAGE@_LIBRARIES @PACKAGE@_LIBS @UPPER@_LIBRARIES @UPPER@_LIBS)
    if(DEFINED ${var} AND NOT cpp_repl_targets)
      set(cpp_repl_targets ${${var}})
      set(cpp_repl_from "the variable ${var}")
    endif()
  endforeach()
endif()
if(NOT cpp_repl_targets)
  foreach(t @PACKAGE@::@PACKAGE@ @PACKAGE@::@LOWER@ @LOWER@::@LOWER@ @PACKAGE@)
    if(TARGET ${t} AND NOT cpp_repl_targets)
      set(cpp_repl_targets ${t})
      set(cpp_repl_from "the target ${t}")
    endif()
  endforeach()
endif()
if(NOT cpp_repl_targets)
  get_property(cpp_repl_targets DIRECTORY PROPERTY IMPORTED_TARGETS)
  set(cpp_repl_from "every imported target")
endif()
add_executable(cpp_repl_probe probe.cpp)
target_link_libraries(cpp_repl_probe PRIVATE ${cpp_repl_targets})
foreach(var @PACKAGE@_INCLUDE_DIRS @PACKAGE@_INCLUDE_DIR @UPPER@_INCLUDE_DIRS @UPPER@_INCLUDE_DIR)
  if(DEFINED ${var})
    target_include_directories(cpp_repl_probe SYSTEM PRIVATE ${${var}})
  endif()
endforeach()
message(STATUS "cpp_repl_targets=${cpp_repl_targets}")
message(STATUS "cpp_repl_from=${cpp_repl_from}")
message(STATUS "cpp_repl_version=${@PACKAGE@_VERSION}")
'''


class Resolution:
    """What a library needs, gathered from CMake, pkg-config or by hand."""

    def __init__(self, clangxx, root):
        self.clangxx, self.root = clangxx, root
        self.implicit = implicit_include_dirs(clangxx)
        self.includes, self.defines, self.flags, self.std = [], [], [], None
        self.libs, self.dropped, self.problems = [], [], []
        self.search_dirs = []

    def problem(self, item, kind, fix):
        entry = {'item': item, 'kind': kind, 'fix': fix}
        if entry not in self.problems:
            self.problems.append(entry)

    def drop(self, item, why):
        entry = {'item': item, 'why': why}
        if entry not in self.dropped:
            self.dropped.append(entry)

    # -- compile side --------------------------------------------------------

    def include(self, path, system=True):
        path = os.path.normpath(path)
        if path in self.implicit:
            self.drop(path, 'the compiler searches it already; listing it breaks #include_next')
        elif not Path(path).is_dir():
            self.drop(path, 'not a directory here')
        elif path not in [i['path'] for i in self.includes]:
            self.includes.append({'path': path, 'system': system})

    def define(self, d):
        if d and d not in self.defines:
            self.defines.append(d)

    def compile_flag(self, flag, value=None):
        """One compile option from a package."""
        if flag.startswith('-std='):
            self.std = flag[len('-std='):]
        elif flag in ('-I', '-isystem') and value:
            self.include(value, system=flag == '-isystem')
        elif flag.startswith('-I') and len(flag) > 2:
            self.include(flag[2:], system=False)
        elif flag.startswith('-isystem') and len(flag) > len('-isystem'):
            self.include(flag[len('-isystem'):])
        elif flag in ('-D',) and value:
            self.define(value)
        elif flag.startswith('-D'):
            self.define(flag[2:])
        elif flag.startswith('-fopenmp'):
            self.problem(flag, 'openmp', 'add "-fopenmp" to flags and the libomp.so that matches the clang-repl '
                                         '(libomp-N-dev) to libs, or drop OpenMP from the package options')
        elif flag in ('-include', '-imacros') and value:
            self.problem(f'{flag} {value}', 'unsupported-flag', f'#include "{value}" at the top of the prelude header')
        elif flag.startswith(('-Xclang', '-stdlib=', '-x')):
            self.problem(flag, 'unsupported-flag', 'cpp-repl does not pass this flag on; check whether the '
                                                   'library needs it (-stdlib=libc++ means a libc++ build)')
        elif flag.startswith('-Werror'):
            self.drop(flag, 'warnings stay warnings in the REPL')
        elif flag == '-pthread' or flag.startswith(('-m', '-f', '-W')):
            if flag not in self.flags:
                self.flags.append(flag)
        else:
            self.drop(flag, 'not a compile option a REPL session needs')

    # -- link side -----------------------------------------------------------

    def library_path(self, d):
        if d and d not in self.search_dirs:
            self.search_dirs.append(d)

    def library_name(self, name, origin):
        """-lNAME: resolved to the shared library file the linker would use."""
        if name in SYSTEM_LIBS:
            self.drop(f'-l{name}', 'every process has it already')
            return
        if name.startswith(':'):
            candidates = [Path(d) / name[1:] for d in self.search_dirs]
        else:
            candidates = [Path(d) / f'lib{name}.so' for d in self.search_dirs]
        for c in candidates:
            if c.exists():
                self.library_file(str(c), origin)
                return
        found = print_file_name(self.clangxx, f'lib{name}.so')
        if not found:
            cache = ldconfig_cache()
            found = cache.get(f'lib{name}.so') or next(
                (path for file, path in cache.items() if file.startswith(f'lib{name}.so.')), None)
        if found:
            self.library_file(found, origin)
            return
        static = print_file_name(self.clangxx, f'lib{name}.a')
        if static:
            self.problem(static, 'static', STATIC_FIX)
        else:
            self.problem(f'-l{name}', 'unresolved-l', 'no lib' + name + '.so found: pass --prefix for its '
                                                       'install, or name the file with --lib')

    def library_file(self, path, origin, depth=0):
        path = canonical(path)
        kind = elf_kind(path)
        if kind is None:
            self.problem(path, 'missing', 'the file does not exist or cannot be read')
        elif kind == 'shared':
            link = dev_link(path)
            if link not in [lib['path'] for lib in self.libs]:
                self.libs.append({'path': link, 'from': origin})
        elif kind == 'archive':
            self.problem(path, 'static', STATIC_FIX)
        elif kind == 'script' and depth < 3:
            self.drop(path, 'a linker script; its members are listed instead')
            base = Path(path).parent
            for member in script_members(path):
                if member.startswith('-l'):
                    self.library_name(member[2:], origin)
                elif member.endswith(('.a', '.o')) or 'ld-linux' in member or 'libc.so' in member:
                    self.drop(member, 'part of the C runtime, or static')
                else:
                    p = Path(member) if Path(member).is_absolute() else base / member
                    self.library_file(str(p), origin, depth + 1)
        elif kind == 'object':
            self.problem(path, 'object', 'an object file cannot be loaded: link it into a shared library first')
        else:
            self.problem(path, 'unknown-file', 'not a shared library')

    def link_item(self, item, origin):
        """One item from a link line."""
        if item.startswith('-l'):
            self.library_name(item[2:], origin)
        elif item.startswith('-L'):
            self.library_path(item[2:])
        elif item.startswith('-Wl,'):
            if '-Bstatic' in item:
                self.problem(item, 'static', 'the package links this library statically: ' + STATIC_FIX)
            else:
                self.drop(item, 'a linker option; cpp-repl loads libraries by path')
        elif item in ('-pthread', '-lpthread') or item.startswith('-rdynamic'):
            self.drop(item, 'every process has it already')
        elif item.startswith('-fopenmp'):
            self.compile_flag(item)
        elif item in ('-framework',) or item.startswith('-F'):
            self.problem(item, 'framework', 'frameworks are macOS only')
        elif os.path.isabs(item):
            self.library_file(item, origin)
        elif item.startswith('-'):
            self.drop(item, 'a linker option')
        else:
            self.problem(item, 'unknown-file', 'a link item cpp-repl cannot place')

    # -- after ---------------------------------------------------------------

    def finish(self):
        """Missing dependencies and ABI checks, and the libraries in load
        order: a library's dependencies before it."""
        self.libs.reverse()
        for lib in self.libs:
            info = dynamic_section(lib['path'])
            lib['soname'] = info['soname']
            lib['missing_needed'] = [n for n in info['needed'] if not find_needed(n, info['runpath'])]
            for name in lib['missing_needed']:
                self.problem(f'{name} (needed by {Path(lib["path"]).name})', 'missing-needed',
                             'the loader cannot find it: list its absolute path in libs before this library')
            abi = defines_cxx_abi(lib['path'])
            if abi:
                self.problem(lib['path'], 'libcxx', 'built against libc++; cpp-repl uses libstdc++, so this '
                                                    'library cannot be mixed in')


STATIC_FIX = ('cpp-repl loads shared libraries only: install or build the shared variant '
              '(BUILD_SHARED_LIBS=ON, a *-dynamic vcpkg triplet), use a header-only mode if the library has '
              'one, or wrap a PIC archive in a shared library')


def resolve_cmake(args, res, clangxx):
    if not shutil.which('cmake'):
        emit({'error': 'cmake is not installed'}, TOOL_MISSING)
    package = args.cmake
    components = [c for c in (args.components or '').split(',') if c]
    targets = [t for t in (args.targets or '').split(',') if t]
    text = (CMAKELISTS.replace('@PACKAGE@', package).replace('@UPPER@', package.upper())
            .replace('@LOWER@', package.lower())
            .replace('@COMPONENTS@', ('COMPONENTS ' + ' '.join(components)) if components else '')
            .replace('@TARGETS@', ' '.join(targets)))
    with scratch('cpp-repl-probe-') as tmp:
        src, build = tmp / 'src', tmp / 'build'
        src.mkdir()
        (src / 'CMakeLists.txt').write_text(text)
        (src / 'probe.cpp').write_text('int main() { return 0; }\n')
        query = build / '.cmake' / 'api' / 'v1' / 'query'
        query.mkdir(parents=True)
        (query / 'codemodel-v2').write_text('')
        cmd = ['cmake', '-S', src, '-B', build, '-G', 'Unix Makefiles', '-DCMAKE_BUILD_TYPE=Release',
               '-DCMAKE_CXX_FLAGS_RELEASE=', '-DBUILD_SHARED_LIBS=ON', f'-D{package}_USE_STATIC_LIBS=OFF',
               f'-D{package.upper()}_USE_STATIC_LIBS=OFF']
        if clangxx:
            cmd.append(f'-DCMAKE_CXX_COMPILER={clangxx}')
        prefix_path = [str(Path(p).expanduser()) for p in args.prefix]
        if prefix_path:
            cmd.append('-DCMAKE_PREFIX_PATH=' + ';'.join(prefix_path))
        cmd += [f'-D{d}' for d in args.cmake_define]
        code, out, err = run(cmd, timeout=300)
        if code != 0:
            lines = (out + err).strip().splitlines()
            start = next((i for i, line in enumerate(lines) if line.startswith('CMake Error')), max(0, len(lines) - 25))
            error = {'error': f'cmake could not find or configure the package {package}',
                     'cmake_output': lines[start:start + 25]}
            if components:
                error['hint'] = (f'resolve was given components ({", ".join(components)}): if {package} has no '
                                 'components, or names them differently, resolve without --components')
            emit(error, USAGE)
        info = dict(re.findall(r'-- cpp_repl_(\w+)=(.*)', out))
        target = read_file_api(build)
        cache_text = (build / 'CMakeCache.txt').read_text(errors='replace')
    for group in target.get('compileGroups', []):
        for inc in group.get('includes', []):
            res.include(inc['path'], system=True)
        for d in group.get('defines', []):
            res.define(d['define'])
        for fragment in group.get('compileCommandFragments', []):
            items = shlex.split(fragment['fragment'])
            i = 0
            while i < len(items):
                flag = items[i]
                if flag in ('-I', '-isystem', '-D', '-include', '-imacros') and i + 1 < len(items):
                    res.compile_flag(flag, items[i + 1])
                    i += 2
                    continue
                res.compile_flag(flag)
                i += 1
    origin = f'CMake package {package}'
    for fragment in target.get('link', {}).get('commandFragments', []):
        role = fragment.get('role')
        for item in shlex.split(fragment['fragment']):
            if role == 'libraryPath':
                res.library_path(item[2:] if item.startswith('-L') else item)
            elif role == 'libraries':
                res.link_item(item, origin)
            elif role == 'frameworkPath':
                res.problem(item, 'framework', 'frameworks are macOS only')
            elif item.startswith('-fopenmp'):
                res.compile_flag(item)
    config = re.search(rf'^{re.escape(package)}_DIR:PATH=(.*)$', cache_text, re.M)
    config_file = None
    if config:
        config_file = next((str(f) for f in _config_files(Path(config.group(1).strip()))), None)
    return {'kind': 'cmake', 'package': package, 'components': components,
            'targets': info.get('targets', '').split(';') if info.get('targets') else [],
            'targets_from': info.get('from'), 'version': info.get('version') or None, 'config': config_file}


def read_file_api(build):
    """The probe target's object from CMake's File API reply."""
    reply = build / '.cmake' / 'api' / 'v1' / 'reply'
    index = sorted(reply.glob('index-*.json'))
    if not index:
        emit({'error': 'CMake wrote no File API reply: CMake 3.16 or newer is needed'}, TOOL_MISSING)
    data = json.loads(index[-1].read_text())
    codemodel = next(r for k, r in data['reply'].items() if k.startswith('codemodel-v2'))
    model = json.loads((reply / codemodel['jsonFile']).read_text())
    for target in model['configurations'][0]['targets']:
        if target['name'] == 'cpp_repl_probe':
            return json.loads((reply / target['jsonFile']).read_text())
    emit({'error': 'the CMake File API reply has no probe target'}, TOOL_MISSING)


def resolve_pkg_config(args, res):
    if not shutil.which('pkg-config'):
        emit({'error': 'pkg-config is not installed'}, TOOL_MISSING)
    modules = [m for m in args.pkg_config.split(',') if m]
    code, cflags, err = run(['pkg-config', '--cflags', *modules], timeout=30)
    if code != 0:
        emit({'error': f'pkg-config does not know {", ".join(modules)}', 'output': err.strip()}, USAGE)
    _, libs, _ = run(['pkg-config', '--libs', *modules], timeout=30)
    _, version, _ = run(['pkg-config', '--modversion', modules[0]], timeout=30)
    items = shlex.split(cflags)
    i = 0
    while i < len(items):
        if items[i] in ('-I', '-isystem', '-D', '-include') and i + 1 < len(items):
            res.compile_flag(items[i], items[i + 1])
            i += 2
            continue
        res.compile_flag(items[i])
        i += 1
    keep = {k for k in (args.keep_libs or '').split(',') if k}
    origin = f'pkg-config {",".join(modules)}'
    for item in shlex.split(libs):
        if keep and item.startswith('-l') and item[2:] not in keep:
            res.drop(item, 'left out by --keep-libs')
            continue
        res.link_item(item, origin)
    return {'kind': 'pkg-config', 'modules': modules, 'version': version.strip() or None}


def resolve_headers(args, res):
    for d in args.headers:
        res.include(str(Path(d).expanduser().resolve()), system=True)
    for d in args.define:
        res.define(d)
    for lib in args.lib:
        res.link_item(str(Path(lib).expanduser().resolve()), 'by hand')
    return {'kind': 'headers', 'dirs': args.headers, 'version': None}


def in_project(path, root, dot):
    """path relative to the .cpp-repl directory when it lies inside the
    project, else None. Paths in a TOML are relative to the TOML."""
    try:
        if Path(path).resolve().is_relative_to(root.resolve()):
            return os.path.relpath(Path(path).resolve(), dot)
    except (OSError, ValueError):
        pass
    return None


def cmd_resolve(args):
    from cpp_repl.config import ConfigError
    if sum(bool(x) for x in (args.cmake, args.pkg_config, args.headers)) != 1:
        emit({'error': 'give one of --cmake PKG, --pkg-config MODULE or --headers DIR'}, USAGE)
    try:
        clangxx = toolchain(args.clang_repl)[3]
    except ConfigError:
        clangxx = shutil.which('clang++') or shutil.which('c++')
    root = project_root(Path.cwd())
    res = Resolution(clangxx, root)
    if args.cmake:
        source = resolve_cmake(args, res, clangxx)
    elif args.pkg_config:
        source = resolve_pkg_config(args, res)
    else:
        source = resolve_headers(args, res)
    source['compiler'] = clangxx
    res.finish()

    if args.name:
        name = profile_name(args.name)
    elif args.cmake:
        name = profile_name(args.cmake)
    elif args.pkg_config:
        name = profile_name(args.pkg_config.split(',')[0])
    else:
        name = profile_name(Path(args.headers[0]).name)
    dot = root / '.cpp-repl'
    flags, include = [], []
    for inc in res.includes:
        rel = in_project(inc['path'], root, dot)
        if rel:
            include.append(rel)   # the project's own headers, relative to the TOML
        else:
            flags += ['-isystem', inc['path']]
    flags += res.flags
    libs = [in_project(lib['path'], root, dot) or lib['path'] for lib in res.libs]
    data = {'include': include, 'flags': flags, 'defines': res.defines, 'libs': libs,
            'prelude': [f'{name}_repl.h']}
    if res.std:
        m = re.fullmatch(r'(?:c|gnu)\+\+(\w+)', res.std)
        if m and m.group(1) in ('23', '2b', '26', '2c'):
            data['std'] = res.std
    # The Debian packages that hold what the profile reads, for a Docker
    # image: the libraries' owners, include directories that one package
    # owns (/usr/include/opencv4 belongs to every OpenCV module), and for a
    # header-only CMake package the owner of its config file.
    owners = apt_owners([lib['path'] for lib in res.libs])
    packages = {p for found in owners.values() for p in found}
    for found in apt_owners([i['path'] for i in res.includes]).values():
        if len(found) == 1:
            packages |= set(found)
    if not res.libs and source.get('config'):
        packages |= {p for found in apt_owners([source['config']]).values() for p in found}
    packages = sorted(packages)
    version = source.get('version')
    where = {'cmake': f'the CMake package {args.cmake}', 'pkg-config': f'pkg-config {args.pkg_config}',
             'headers': 'headers given by hand'}[source['kind']]
    comments = [f'{name}{" " + version if version else ""} for cpp-repl, from {where}; '
                f'written by probe_library.py on {datetime.date.today()}.',
                f'Start a session with: cpp-repl -c .cpp-repl/{name}.toml']
    out = {'name': name, 'source': source, 'include': res.includes, 'defines': res.defines, 'flags': res.flags,
           'std': res.std, 'libs': res.libs, 'header_only': not res.libs, 'dropped': res.dropped,
           'problems': res.problems, 'packages': packages, 'toml_path': str(dot / f'{name}.toml')}
    if args.toml:
        out['toml'] = toml_text(data, comments)
    emit(out, PROBLEMS if res.problems else OK)


# ---------------------------------------------------------------------------
# image
# ---------------------------------------------------------------------------

def included_headers(cfg):
    """Every header the preludes include, as the host's clang++ -H lists them,
    without the compiler's own resource headers (the image has its own)."""
    from cpp_repl.config import ConfigError
    try:
        clangxx = toolchain()[3]
    except ConfigError:
        clangxx = shutil.which('clang++')
    if not clangxx:
        return []
    _, resource, _ = run([clangxx, '-print-resource-dir'], timeout=20)
    resource = os.path.normpath(resource.strip()) if resource.strip() else None
    with scratch('cpp-repl-image-') as tmp:
        source = tmp / 'preludes.cpp'
        source.write_text(''.join(f'#include "{h}"\n' for h in cfg.preludes))
        _, _, err = run([clangxx, '-fsyntax-only', '-H', *cfg.compiler_flags, source], timeout=300)
    headers = []
    for path in re.findall(r'^\.+ (.+)$', err, re.M):
        path = os.path.normpath(path)
        if resource and path.startswith(resource + os.sep):
            continue
        headers.append(path)
    return list(dict.fromkeys(headers))


def cmd_image(args):
    from cpp_repl.config import Config, ConfigError, Options
    from cpp_repl.docker import DEFAULT_IMAGE, _image_has
    image = args.image or os.environ.get('CPP_REPL_IMAGE') or DEFAULT_IMAGE
    try:
        cfg = Config(Options(cwd=Path.cwd(), configs=[args.config], no_config=True), backend=False)
    except ConfigError as e:
        emit({'error': str(e)}, USAGE)
    paths = []
    flags = cfg.compiler_flags
    for i, a in enumerate(flags[:-1]):
        if a in ('-I', '-isystem', '-iquote', '-idirafter'):
            paths.append(flags[i + 1])
    paths += [str(lib) for lib in cfg.libs] + [str(h) for h in cfg.preludes]
    # The headers the preludes include count too: a header-only library under
    # /usr/include (glm) adds no path to the profile.
    paths += included_headers(cfg)
    # Anything outside the image's own directories is mounted by cpp-repl --docker.
    inside = sorted({p for p in paths if _image_has(Path(p).resolve())})
    if not shutil.which('docker'):
        emit({'error': 'docker is not installed'}, TOOL_MISSING)
    code, out, err = run(['docker', 'run', '--rm', '--entrypoint', 'sh', image, '-c',
                          'for p; do [ -e "$p" ] || echo "$p"; done', 'sh', *inside], timeout=120)
    if code != 0:
        emit({'error': f'cannot run the image {image}', 'output': err.strip().splitlines()[-5:]}, USAGE)
    missing = out.split()
    # Packages from the missing files: a directory such as /usr/include/opencv4
    # belongs to every package that puts files in it.
    files = [m for m in missing if not Path(m).is_dir()]
    owners = apt_owners(files)
    apt = sorted({p for found in owners.values() for p in found})
    unowned = [m for m in files if m not in owners]
    tag = f'cpp-repl-{norm(project_root(Path.cwd()).name) or "project"}:{image.split(":")[-1]}'
    dockerfile = None
    if apt:
        dockerfile = '\n'.join([
            f"# {image} plus the packages this project's cpp-repl profiles read from /usr.",
            f'# Build it before first use: docker build -t {tag} .cpp-repl',
            f'# then run: cpp-repl --docker --image {tag} -c .cpp-repl/<lib>.toml',
            f'FROM {image}',
            'USER root',
            'RUN apt-get update \\',
            ' && apt-get install -y --no-install-recommends \\',
            *[f'      {p} \\' for p in apt],
            ' && rm -rf /var/lib/apt/lists/*', ''])
    emit({'image': image, 'checked': len(inside), 'missing_count': len(missing), 'missing': missing[:20],
          'apt': apt, 'unowned': unowned[:20], 'tag': tag, 'dockerfile': dockerfile},
         PROBLEMS if unowned else OK)


# ---------------------------------------------------------------------------
# symbol
# ---------------------------------------------------------------------------

def _defined(path, symbol):
    """Whether a shared library defines symbol: a mangled name exactly, or a
    demangled one (cv::GaussianBlur) as the start of a demangled name."""
    if symbol.startswith('_Z') or not ('::' in symbol or '(' in symbol):
        code, out, _ = run(['nm', '-D', '--defined-only', path], timeout=60)
        return code == 0 and any(line.split()[-1].split('@')[0] == symbol
                                 for line in out.splitlines() if line.strip())
    code, out, _ = run(['nm', '-D', '--defined-only', '-C', path], timeout=60)
    if code != 0:
        return False
    for line in out.splitlines():
        parts = line.split(' ', 2)
        if len(parts) == 3 and parts[1] in 'TWVuiBD' and parts[2].startswith(symbol):
            return True
    return False


def cmd_symbol(args):
    from cpp_repl.config import Config, ConfigError, Options
    if not shutil.which('nm'):
        emit({'error': 'nm (binutils) is not installed'}, TOOL_MISSING)
    symbol = args.mangled.strip()
    code, demangled, _ = run(['c++filt', symbol], timeout=10)
    first, dirs = [], [Path(d).expanduser() for d in args.dir]
    pattern = args.glob
    if args.config:
        try:
            cfg = Config(Options(cwd=Path.cwd(), configs=[args.config], no_config=True), backend=False)
        except ConfigError as e:
            emit({'error': str(e)}, USAGE)
        listed = [str(lib) for lib in cfg.libs]
        first += listed
        for lib in listed:   # what the listed libraries load, one level down
            info = dynamic_section(lib)
            first += [p for p in (find_needed(n, info['runpath']) for n in info['needed']) if p]
        if not pattern and listed:
            # Libraries named like the listed ones: libopencv_core.so -> libopencv*.so*
            stems = [re.match(r'(lib[A-Za-z]+)', Path(lib).name) for lib in listed]
            prefix = os.path.commonprefix([m.group(1) for m in stems if m])
            if len(prefix) > 4:
                pattern = f'{prefix}*.so*'
                dirs += sorted({Path(lib).parent for lib in listed})
    second = []
    for d in dirs:
        if d.is_dir():
            second += sorted(str(p) for p in d.glob(pattern or 'lib*.so*'))
    seen, found, limit = set(), [], 800
    for batch in (first, second):
        for path in batch:
            real = os.path.realpath(path)
            if real in seen or elf_kind(real) != 'shared':
                continue
            if len(seen) >= limit:
                break
            seen.add(real)
            if _defined(real, symbol):
                link = dev_link(path)
                found.append({'path': link if Path(link).exists() else path,
                              'soname': dynamic_section(real)['soname']})
        if found:
            break
    owners = apt_owners([f['path'] for f in found])
    for f in found:
        f['apt'] = owners.get(f['path'], [])
    out = {'symbol': symbol, 'demangled': demangled.strip() if code == 0 else None, 'searched': len(seen),
           'found_in': found}
    if not found:
        out['hint'] = ('not in the listed libraries or their dependencies: pass --dir DIR (and --glob '
                       "'libname*.so*') to search a library directory")
    emit(out, OK if found else USAGE)


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('env', help='the toolchain, Docker and the project')
    p.add_argument('--clang-repl', help='the clang-repl to describe')
    p = sub.add_parser('find', help='where a library is installed')
    p.add_argument('name')
    p.add_argument('--prefix', action='append', default=[], help='another install prefix to search')
    p.add_argument('--scan', metavar='DIR', help="read this project's #include lines")
    p = sub.add_parser('resolve', help='what cpp-repl needs to load a library')
    p.add_argument('--cmake', metavar='PACKAGE')
    p.add_argument('--components', help='find_package COMPONENTS, comma-separated')
    p.add_argument('--targets', help='the imported targets to link, comma-separated')
    p.add_argument('--cmake-define', action='append', default=[], metavar='VAR=VALUE')
    p.add_argument('--pkg-config', metavar='MODULE[,MODULE]')
    p.add_argument('--keep-libs', metavar='NAME,..', help='with --pkg-config: the -l names to keep')
    p.add_argument('--headers', action='append', default=[], metavar='DIR')
    p.add_argument('--lib', action='append', default=[], metavar='PATH')
    p.add_argument('--define', action='append', default=[], metavar='NAME[=VALUE]')
    p.add_argument('--prefix', action='append', default=[])
    p.add_argument('--name', help='the profile name (file names in .cpp-repl/)')
    p.add_argument('--toml', action='store_true', help='include a draft TOML')
    p.add_argument('--clang-repl')
    p = sub.add_parser('image', help='what a Docker image lacks for a profile')
    p.add_argument('--config', required=True)
    p.add_argument('--image')
    p = sub.add_parser('symbol', help='which shared library defines a symbol')
    p.add_argument('mangled')
    p.add_argument('--config')
    p.add_argument('--dir', action='append', default=[])
    p.add_argument('--glob', help='file names to search in the directories (default: like the listed libraries)')
    args = parser.parse_args()
    if args.command == 'symbol' and not (args.config or args.dir):
        emit({'error': 'give --config TOML or --dir DIR'}, USAGE)
    {'env': cmd_env, 'find': cmd_find, 'resolve': cmd_resolve, 'image': cmd_image, 'symbol': cmd_symbol}[
        args.command](args)


if __name__ == '__main__':
    main()
