"""The preludes as a precompiled header, built once per configuration.

Parsing the prelude headers is most of a session's start-up: about two
seconds for the standard library's, five with OpenUSD's. A precompiled header
of them loads in a few hundredths of a second, through -include-pch, and
clang-repl emits from it whatever an input uses, as from headers parsed at
the start. (clang-repl 18 did not: an input that used an inline function or
an inline variable of the PCH failed to link. cpp-repl needs 23 or newer.)

The PCH lives under <state>/pch/<key>/, and the header it was built from
beside it as <key>.h, keyed by what it depends on: the clang-repl, the flags,
the preludes' contents and the libraries. The PCH records that header's path,
so the header stays where it was built; the PCH is built in a directory of
its own and renamed into place. Clang checks the headers the PCH recorded by
size and mtime, and by content when the mtime changed, and says so when one
changed: the session then rebuilds once (session.Session._start).
"""

import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

from .config import _alive, _prune, sibling_clang

# dir: <state>/pch/<key>. header: <state>/pch/<key>.h, the preludes included
# one by one. pch: the precompiled header in dir.
PCH = namedtuple('PCH', 'dir header pch')

BUILD_TIMEOUT = 600   # seconds the build may take
KEEP_DAYS = 30        # an entry unused for this long goes
FAILED_FOR = 86400    # seconds a failed build is remembered, so that a start does not pay for it again
_BUILDING = re.compile(r'\.building-(\d+)$')
# What clang says when a PCH cannot be used: a header changed since it was
# built, the file is missing or corrupt, or an option differs from its.
_BLAMED = re.compile(r'precompiled header|PCH file|AST file')


class PCHError(Exception):
    """The PCH cannot be built, or was not: the message says why.
    remembered: the failure is one recorded earlier, not new."""

    def __init__(self, message, remembered=False):
        super().__init__(message)
        self.remembered = remembered


def remember_failure(cfg, reason):
    """Record that cfg's PCH failed, so that the starts of the next day go
    without it at once (prepare)."""
    try:
        cache = _cache(cfg)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f'{key(cfg)}.failed').write_text(f'{reason}\n')
    except OSError:
        pass


def _flags(cfg):
    """The session's compiler flags without the working directory's -iquote:
    it differs from session to session, and the preludes do not use it."""
    flags = list(cfg.compiler_flags)
    pair = ['-iquote', str(cfg.cwd)]
    for i in range(len(flags) - 1):
        if flags[i:i + 2] == pair:
            del flags[i:i + 2]
            break
    return flags


def key(cfg):
    """What the entry depends on, hashed: the clang-repl (path, version, size
    and mtime), the flags, the preludes' contents and the libraries."""
    binary = Path(cfg.clang_repl).resolve()
    try:
        st = binary.stat()
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = None
    parts = (str(binary), cfg.version_text, stamp, _flags(cfg),
             [(str(h), hashlib.sha256(h.read_bytes()).hexdigest()) for h in cfg.preludes],
             [str(lib) for lib in cfg.libs])
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


def _cache(cfg):
    return cfg.state / 'pch'


def _entry(cache, k):
    return PCH(cache / k, cache / f'{k}.h', cache / k / 'preludes.pch')


def _abandoned(p):
    """For a build directory, whether the process building it has gone; None
    for anything else, which goes by age."""
    m = _BUILDING.search(p.name)
    return not _alive(int(m.group(1))) if m else None


def _mtime(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def cached(cfg):
    """The complete entry for cfg, touched so that it stays; None when there
    is none."""
    p = _entry(_cache(cfg), key(cfg))
    if not (p.header.is_file() and p.pch.is_file()):
        return None
    for f in (p.dir, p.header):
        try:
            os.utime(f)
        except OSError:
            pass
    return p


def build(cfg, replacing=None):
    """Build the entry for cfg in a directory of its own, renamed into place
    once the PCH is there. One session at a time builds an entry; another
    waiting for it takes the result. With replacing, the mtime_ns of a PCH
    found out of date, the entry is built again unless another session has
    replaced it meanwhile. Raises PCHError."""
    clang = sibling_clang(cfg.clang_repl)
    if not clang:
        raise PCHError('no clang++ beside clang-repl')
    cache = _cache(cfg)
    cache.mkdir(parents=True, exist_ok=True)
    k = key(cfg)
    with open(cache / f'{k}.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = cached(cfg)
        if p and (replacing is None or _mtime(p.pch) != replacing):
            return p
        if sys.stderr.isatty():
            sys.stderr.write('cpp-repl: precompiling the preludes, once for this configuration (a few seconds, '
                             'about ten with OpenUSD)...\n')
        p = _entry(cache, k)
        work = cache / f'{k}.building-{os.getpid()}'
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        try:
            # The header keeps its path: the PCH records it, and clang looks
            # for it at every load.
            p.header.write_text(''.join(f'#include "{h}"\n' for h in cfg.preludes))
            # The language options clang-repl compiles with, which clang
            # checks against the PCH's: incremental extensions, and
            # position-independent code that is not PIE (clang++'s own
            # default, on Ubuntu, is PIE).
            argv = [clang, *_flags(cfg), '-Xclang', '-fincremental-extensions', '-fPIC',
                    '-fpch-validate-input-files-content', '-x', 'c++-header', str(p.header), '-o',
                    str(work / p.pch.name)]
            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=BUILD_TIMEOUT,
                                      stdin=subprocess.DEVNULL, env=cfg.env)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise PCHError(f'precompiling the preludes: {e}')
            if done.returncode:
                lines = (done.stderr + done.stdout).strip().splitlines() or ['?']
                first = next((line for line in lines if 'error' in line), lines[0])
                raise PCHError(f'precompiling the preludes failed: {first.strip()}')
            shutil.rmtree(p.dir, ignore_errors=True)
            os.rename(work, p.dir)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
    return p


def prepare(cfg, rebuild=False):
    """The entry for cfg: the cached one, else built now; with rebuild,
    built anew. Raises PCHError when there is none to be had: the build
    failed, now or within the last day (the reason is kept beside the
    cache, in <key>.failed, and the configuration changing starts afresh)."""
    cache = _cache(cfg)
    _prune(cache, days=KEEP_DAYS, ended=_abandoned)
    failed = cache / f'{key(cfg)}.failed'
    if rebuild:
        p = cached(cfg)
        replacing = _mtime(p.pch) if p else None
    else:
        # A failure recorded goes first: a PCH that built may still be one
        # clang-repl does not start with.
        try:
            if time.time() - failed.stat().st_mtime < FAILED_FOR:
                raise PCHError(failed.read_text().strip(), remembered=True)
        except OSError:
            pass
        p = cached(cfg)
        if p:
            return p
        replacing = None
    try:
        p = build(cfg, replacing)
    except PCHError as e:
        remember_failure(cfg, e)
        raise
    try:
        failed.unlink()
    except OSError:
        pass
    return p


def blamed(output, p):
    """Whether a start-up's output faults the PCH: clang's word on a
    precompiled header (a header changed since it was built, the file missing
    or corrupt, an option that differs), or an error naming its file."""
    if _BLAMED.search(output):
        return True
    return any(str(p.pch) in line for line in output.splitlines() if 'error' in line)
