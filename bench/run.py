#!/usr/bin/env python3
"""Run the matmul benchmark: NumPy, Eigen in cpp-repl, and native Eigen.

    python bench/run.py                    every arm, 3 rounds (about 30 minutes)
    python bench/run.py --quick            1 round, n = 256 and 1024 (about 3 minutes)
    python bench/run.py --arms core --dry-run
    bench/docker.sh --quick                the same inside the bench image

bench/arms.py lists the arms and bench/README.md describes the protocol. Each
arm prints "BENCH {json}" records (bench/cpp/bench.hpp,
bench/py/numpy_matmul.py). This script builds the native programs and the
precompiled kernels, launches each arm pinned to fixed CPUs, samples clock
and temperature while it runs, checks every result, and writes one JSON file
to bench/results/, which bench/report.py turns into tables and plots. It
reads system settings and changes none.
"""

import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CPP = HERE / 'cpp'
BUILD = HERE / 'build'
RESULTS = HERE / 'results'

sys.path.insert(0, str(HERE))
import arms as arms_mod  # noqa: E402
import sysinfo  # noqa: E402

# Eigen allocates its GEMM workspace on every call: about 0.5 MB at n = 256,
# which sits at glibc's initial mmap threshold, so a fresh process maps,
# faults in and unmaps it each call. A long-lived process such as clang-repl
# has already raised its threshold by freeing larger blocks, and does not.
# Every arm runs with the threshold at 32 MB, so the arms differ in code, not
# in allocator state; --no-malloc-tuning shows the difference.
MALLOC_TUNABLES = 'glibc.malloc.mmap_threshold=33554432:glibc.malloc.trim_threshold=67108864'

SIZES = [32, 64, 128, 256, 384, 512, 768, 1024, 1536, 2048, 3072]
MT_EXTRA = [4096]
QUICK_SIZES = [256, 1024]
LATENCY_N = 256

# A run's log, scanned to name what went wrong.
_LINK = re.compile(r'Symbols not found|JIT session error|Failed to materialize symbols'
                   r'|Duplicate definition of symbol')
_CRASH = re.compile(r'clang-repl crashed|Segmentation fault|core dumped|Aborted')
_ERROR = re.compile(r'(?:^|\s)(?:fatal )?error[:\]]|error: ', re.I)


def note(text):
    print(f'bench: {text}', flush=True)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def newest(stem):
    """The newest `stem-N` on PATH, else plain `stem`, as a path; or None."""
    best = None
    for d in os.environ.get('PATH', '').split(os.pathsep):
        p = Path(d)
        if not p.is_dir():
            continue
        for f in p.glob(stem + '-*'):
            m = re.fullmatch(re.escape(stem) + r'-(\d+)', f.name)
            if m and os.access(f, os.X_OK) and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), str(f))
    return best[1] if best else shutil.which(stem)


def find_eigen(explicit):
    for d in (explicit, os.environ.get('EIGEN_ROOT'), '/usr/include/eigen3', '/usr/local/include/eigen3'):
        if d and (Path(d) / 'Eigen' / 'Core').is_file():
            return str(Path(d).resolve())
    return None


def find_libomp(cxx):
    """The libomp.so that cxx links against, if it has one."""
    if not cxx or not shutil.which(cxx):
        return None
    try:
        out = subprocess.run([cxx, '-print-file-name=libomp.so'], capture_output=True, text=True,
                             timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out if out and Path(out).is_absolute() and Path(out).exists() else None


def resolve_tools(args):
    clang_repl = args.clang_repl or os.environ.get('CPP_REPL_CLANG_REPL') or newest('clang-repl')
    llvm = sysinfo.llvm_major(sysinfo.first_line([clang_repl, '--version'])) if clang_repl else None
    cxx = args.cxx
    if not cxx:
        versioned = f'clang++-{llvm}' if llvm else None
        cxx = versioned if versioned and shutil.which(versioned) else newest('clang++')
    tools = {
        'python': args.python or sys.executable,
        'cxx': cxx,
        'gxx': args.gxx or shutil.which('g++'),
        'cpp_repl': args.cpp_repl or str(REPO / 'bin' / 'cpp-repl'),
        'clang_repl': clang_repl,
        'eigen': find_eigen(args.eigen),
        'libomp': args.libomp or find_libomp(cxx),
        'llvm': llvm or sysinfo.llvm_major(sysinfo.first_line([cxx, '--version'])),
    }
    cxx_llvm = sysinfo.llvm_major(sysinfo.first_line([cxx, '--version'])) if cxx else None
    if llvm and cxx_llvm and llvm != cxx_llvm:
        note(f'warning: clang-repl is LLVM {llvm} but {cxx} is LLVM {cxx_llvm}; '
             'the native and REPL arms then use different compilers')
    return tools


def includes(tools):
    return [f'-I{CPP}', f'-I{tools["eigen"]}']


def kernel_defines(arm):
    return {'naive': ['-DBENCH_NAIVE=1'], 'aot': ['-DBENCH_AOT=1']}.get(arm.kernel, [])


def openmp_link(tools):
    """-fopenmp, and a RUNPATH to libomp so the program finds it at run time."""
    flags = ['-fopenmp']
    if tools['libomp']:
        flags.append(f'-Wl,-rpath,{Path(tools["libomp"]).resolve().parent}')
    return flags


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------

def native_binary(arm):
    return BUILD / f'matmul-{arm.name}'


def native_argv(arm, tools, output):
    compiler = tools['gxx'] if arm.compiler == 'gcc' else tools['cxx']
    argv = [compiler] + arms_mod.FLAGS[arm.flags] + includes(tools) + kernel_defines(arm)
    if arm.openmp:
        argv += openmp_link(tools)
    return argv + [str(CPP / 'matmul.cpp'), '-o', str(output)]


def aot_argv(openmp, tools):
    name = 'libbench_kernel_omp.so' if openmp else 'libbench_kernel.so'
    argv = ([tools['cxx']] + arms_mod.FLAGS['full'] + includes(tools)
            + ['-shared', '-fPIC', '-fvisibility=hidden', '-fvisibility-inlines-hidden'])
    if openmp:
        argv += openmp_link(tools)
    return name, argv + [str(CPP / 'kernel_aot.cpp'), '-o', str(BUILD / name)]


def planned_builds(selected, tools):
    """[(name, argv)] for every native program and kernel library the
    selected arms need."""
    builds = []
    for arm in selected:
        if arm.kind == 'native':
            builds.append((native_binary(arm).name, native_argv(arm, tools, native_binary(arm))))
    for openmp in (False, True):
        if any(a.kernel == 'aot' and a.openmp == openmp for a in selected):
            builds.append(aot_argv(openmp, tools))
    return builds


def compile_one(name, argv):
    t = time.monotonic()
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        ok, text = out.returncode == 0, out.stdout + out.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, text = False, str(e)
    return {'name': name, 'argv': argv, 'ok': ok, 'seconds': time.monotonic() - t, 'output': text[-4000:]}


def build_all(builds):
    BUILD.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda b: compile_one(*b), builds))
    for r in results:
        note(f"built {r['name']} in {r['seconds']:.1f} s" if r['ok'] else
             f"build of {r['name']} failed: {first_error(r['output']) or 'see the JSON'}")
    return {r['name']: r for r in results}


def unavailable_reason(arm, tools, built):
    """Why arm cannot run here, or None."""
    if arm.kind == 'numpy':
        return None if tools['python'] else 'no Python'
    if arm.kind == 'native':
        if arm.compiler == 'gcc' and not tools['gxx']:
            return 'no g++'
        if arm.openmp and arm.compiler == 'clang' and not tools['libomp']:
            return 'no libomp for clang'
        b = built.get(native_binary(arm).name)
        return None if b is None or b['ok'] else f"build failed: {first_error(b['output'])}"
    if not tools['clang_repl']:
        return 'no clang-repl'
    if not Path(tools['cpp_repl']).exists() and not shutil.which(tools['cpp_repl']):
        return f"no cpp-repl at {tools['cpp_repl']}"
    if arm.openmp and not tools['libomp']:
        return 'no libomp for clang'
    if arm.aot_lib:
        b = built.get(arm.aot_lib)
        if b is None or not b['ok']:
            return f"{arm.aot_lib} did not build: {first_error(b['output']) if b else 'not built'}"
    return None


# ---------------------------------------------------------------------------
# Launching an arm
# ---------------------------------------------------------------------------

def arm_argv(arm, tools, session=False):
    if arm.kind == 'numpy':
        return [tools['python'], str(HERE / 'py' / 'numpy_matmul.py')]
    if arm.kind == 'native':
        return [str(native_binary(arm))]
    argv = [tools['cpp_repl'], '--batch', '--no-color', '--clang-repl', tools['clang_repl']]
    argv += arms_mod.FLAGS[arm.flags] + includes(tools) + kernel_defines(arm)
    if arm.aot_lib:
        argv += ['--lib', str(BUILD / arm.aot_lib)]
    elif arm.openmp:
        argv += ['-fopenmp', '--lib', tools['libomp'] or '<libomp.so>']
    argv.append(str(CPP / 'matmul.cpp'))
    if session:
        argv.append(str(CPP / 'session.repl'))
    return argv


def arm_env(arm, sizes, args, min_time):
    env = dict(os.environ)
    for var in ('OMP_PLACES', 'OMP_PROC_BIND', 'BENCH_MAX_N'):
        env.pop(var, None)
    env.update({
        'BENCH_ARM': arm.name,
        'BENCH_SIZES': ','.join(map(str, sizes)),
        'BENCH_THREADS': str(arm.threads),
        'BENCH_MIN_TIME': str(min_time),
        'BENCH_MAX_CALL': str(args.max_call),
        'BENCH_SESSION_N': str(args.session_n),
        'OMP_NUM_THREADS': str(arm.threads),
        'OPENBLAS_NUM_THREADS': str(arm.threads),
        'NO_COLOR': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    if arm.max_n:
        env['BENCH_MAX_N'] = str(arm.max_n)
    if arm.threads > 1:
        env.update(OMP_PLACES='cores', OMP_PROC_BIND='close')
    if args.malloc_tuning:
        env['GLIBC_TUNABLES'] = ':'.join(filter(None, [os.environ.get('GLIBC_TUNABLES'), MALLOC_TUNABLES]))
    return env


def bench_env(env):
    """The part of an arm's environment worth recording."""
    return {k: v for k, v in env.items()
            if (k.startswith(('BENCH_', 'OMP_', 'OPENBLAS_')) or k == 'GLIBC_TUNABLES')
            and k not in ('BENCH_IMAGE_ID', 'BENCH_HOST')}


def descendants(pid):
    """Every process below pid, read from /proc."""
    children = {}
    for stat in Path('/proc').glob('[0-9]*/stat'):
        try:
            text = stat.read_text()
        except OSError:
            continue
        fields = text[text.rfind(')') + 2:].split()
        children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
    out, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            out.append(child)
            todo.append(child)
    return out


def stop(proc):
    """End proc and everything it started. cpp-repl runs clang-repl in a
    session of its own, which a signal to cpp-repl's group would miss."""
    victims = descendants(proc.pid)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for pid in [proc.pid] + victims:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def launch(argv, env, cpus, log_path, timeout):
    """Run argv, copying its output to log_path and collecting its BENCH
    records, each stamped with the time it arrived."""
    if cpus is not None and shutil.which('taskset'):
        argv = ['taskset', '-c', cpus] + argv
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    t_launch = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)

    def pump():
        with open(log_path, 'wb') as log:
            for raw in proc.stdout:
                log.write(raw)
                if raw.startswith(b'BENCH '):
                    try:
                        record = json.loads(raw[6:])
                    except ValueError:
                        continue
                    record['_arrived'] = time.monotonic()
                    records.append(record)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop(proc)
        rc = proc.returncode
    reader.join(timeout=30)
    return {'argv': argv, 'launch': t_launch, 'end': time.monotonic(), 'exit': rc,
            'timed_out': timed_out, 'records': records}


def first_error(text):
    for line in (text or '').splitlines():
        if _LINK.search(line) or _CRASH.search(line) or _ERROR.search(line):
            return line.strip()[:300]
    return None


def failure_detail(run, log_text):
    """One line on what went wrong in a run."""
    refusal = next((r.get('reason') for r in run['records'] if r.get('type') == 'error'), None)
    if refusal:
        return refusal
    if run['timed_out']:
        return 'stopped at the time limit'
    line = first_error(log_text)
    if line:
        return line
    return (f"exit status {run['exit']} after {len(run['records'])} records and no done record"
            if not any(r.get('type') == 'done' for r in run['records']) else f"exit status {run['exit']}")


def classify(arm, run, log_text):
    """'ok', or what went wrong: refused, timeout, link, crash, error,
    missing-done or wrong-config."""
    records = run['records']
    if any(r.get('type') == 'error' for r in records):
        return 'refused'
    if run['timed_out']:
        return 'timeout'
    if _LINK.search(log_text):
        return 'link'
    if (run['exit'] is not None and run['exit'] < 0) or _CRASH.search(log_text):
        return 'crash'
    if not any(r.get('type') == 'done' for r in records):
        return 'error' if first_error(log_text) else 'missing-done'
    if run['exit'] != 0:
        return 'error'
    return 'wrong-config' if config_problem(arm, records) else 'ok'


def config_problem(arm, records):
    """What makes an arm's env record disagree with the arm, or None."""
    env = next((r for r in records if r.get('type') == 'env'), None)
    if env is None:
        return 'no env record'
    if arm.kind == 'numpy':
        threads = (env.get('blas') or {}).get('num_threads')
        return None if threads == arm.threads else f'BLAS runs {threads} threads'
    build = env.get('aot') if arm.kernel == 'aot' else env
    if build is None:
        return 'no build record from the precompiled kernel'
    if arm.openmp and not build.get('openmp'):
        return 'built without OpenMP'
    if build.get('eigen_threads') != arm.threads:
        return f"Eigen uses {build.get('eigen_threads')} threads"
    if arm.flags == 'full' or arm.kernel == 'aot':
        if not build.get('optimize'):
            return '__OPTIMIZE__ is not defined'
    return None


# ---------------------------------------------------------------------------
# Clock, temperature, cool-down
# ---------------------------------------------------------------------------

class Sampler(threading.Thread):
    """Reads the pinned CPUs' clock and the package temperature ten times a
    second while an arm runs."""

    def __init__(self, cpus, sensor, period=0.1):
        super().__init__(daemon=True)
        self.files = [f'/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq' for c in cpus]
        self.sensor, self.period = sensor, period
        self.samples = []
        self.halt = threading.Event()

    def run(self):
        while not self.halt.wait(self.period):
            khz = [k for k in (sysinfo.read_int(f) for f in self.files) if k]
            temp = sysinfo.read_int(self.sensor) if self.sensor else None
            self.samples.append([round(time.monotonic(), 3),
                                 round(sum(khz) / len(khz) / 1000) if khz else None,
                                 temp / 1000 if temp is not None else None])

    def finish(self):
        self.halt.set()
        self.join()
        return self.samples


def temperature(sensor):
    t = sysinfo.read_int(sensor) if sensor else None
    return t / 1000 if t is not None else None


def idle_temperature(sensor, seconds=2.0):
    if not sensor:
        return None
    readings = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        readings.append(temperature(sensor))
        time.sleep(0.2)
    readings = sorted(r for r in readings if r is not None)
    return readings[len(readings) // 2] if readings else None


def cool_down(sensor, idle, margin, limit):
    """Waits until the package is within margin degrees of idle, for at most
    limit seconds; returns the seconds waited."""
    start = time.monotonic()
    if sensor and idle is not None:
        while time.monotonic() - start < limit:
            t = temperature(sensor)
            if t is None or t <= idle + margin:
                break
            time.sleep(0.5)
    return round(time.monotonic() - start, 2)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _periodic_sum(a, c, m, n):
    """sum((a*i + c) mod m for i < n), for a coprime to m: each block of m
    consecutive i takes every residue once."""
    full, rest = divmod(n, m)
    return full * (m * (m - 1) // 2) + sum((a * i + c) % m for i in range(rest))


def expected_checksum(n):
    """sum(C) exactly: sum_k (sum_i A_ik)(sum_j B_kj), with A and B as
    bench.hpp fills them."""
    total = sum(_periodic_sum(31, 17 * k, 97, n) * _periodic_sum(13, 7 * k, 89, n) for k in range(n))
    return float(Fraction(total, 97 * 89))


# Settings that must match between a native arm and the REPL arm compared
# with it. "simd" is left out: Eigen prints the same text for AVX and AVX2.
PARITY_FIELDS = ('eigen', 'vectorize', 'isa', 'ndebug', 'optimize')
PARITY_PAIRS = [('eigen-native', 'eigen-repl'), ('eigen-native', 'eigen-repl-aot'),
                ('eigen-native-omp', 'eigen-repl-omp'), ('eigen-native-omp', 'eigen-repl-aot-omp'),
                ('naive-native', 'naive-repl')]
OPTIMISED_BLAS = ('openblas', 'mkl', 'blis')


def validate(runs, system):
    """Every check the protocol makes. A failure means the run is invalid,
    not that a hypothesis failed."""
    checks = []

    def check(name, ok, detail, arm=None, n=None, rnd=None):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail, 'arm': arm, 'n': n, 'round': rnd})

    cpu = system['cpu']
    peak_per_thread = (cpu['flops_per_cycle'] * cpu['max_mhz'] / 1000) if cpu.get('max_mhz') else None
    expected = {}
    good = [r for r in runs if r['outcome'] == 'ok']
    for run in good:
        for res in (x for x in run['records'] if x.get('type') == 'result'):
            n, where = res['n'], dict(arm=run['arm'], n=res['n'], rnd=run['round'])
            if n not in expected:
                expected[n] = expected_checksum(n)
            fv = res.get('freivalds')
            check('freivalds', fv is not None and fv < 1e-10, f'residual {fv}', **where)
            rel = abs(res['checksum'] - expected[n]) / expected[n] if res.get('checksum') is not None else None
            check('checksum', rel is not None and rel < 1e-10, f'relative error {rel}', **where)
            if peak_per_thread:
                bound = peak_per_thread * run['threads']
                check('below peak', res['gflops'] <= bound * 1.01,
                      f"{res['gflops']:.1f} GFLOP/s against a peak of {bound:.1f}", **where)

    envs = {}
    for run in good:
        env = next((x for x in run['records'] if x.get('type') == 'env'), None)
        if env:
            envs.setdefault(run['arm'], env)
    for native, repl in PARITY_PAIRS:
        if native in envs and repl in envs:
            a = envs[native]
            b = envs[repl].get('aot') if repl.startswith('eigen-repl-aot') else envs[repl]
            diff = {f: (a.get(f), (b or {}).get(f)) for f in PARITY_FIELDS if a.get(f) != (b or {}).get(f)}
            check('same build settings', not diff,
                  'identical' if not diff else f'{native} vs {repl}: {diff}', arm=repl)
    for arm, env in envs.items():
        if 'blas' in env:
            blas = env.get('blas') or {}
            check('optimised BLAS', blas.get('internal_api') in OPTIMISED_BLAS,
                  f"{blas.get('internal_api')} {blas.get('version')} ({blas.get('architecture')})", arm=arm)
    return checks


# ---------------------------------------------------------------------------
# The passes
# ---------------------------------------------------------------------------

class Bench:
    def __init__(self, args, tools, selected, available, system, stem):
        self.args, self.tools, self.system, self.stem = args, tools, system, stem
        self.selected, self.available = selected, available
        self.runs = []
        self.sensor = system.get('temperature_sensor')
        self.idle = idle_temperature(self.sensor) if self.sensor else None

    def cpus(self, arm):
        if not self.args.pin:
            return None
        return self.args.mt_cpus if arm.threads > 1 else str(self.args.cpu)

    def sizes(self, arm):
        return sizes_for(arm, self.args)

    def run_arm(self, arm, rnd, phase, sizes, min_time, session=False, compile_s=None):
        waited = 0.0
        if self.args.cooldown:
            waited = cool_down(self.sensor, self.idle, self.args.cooldown_margin, self.args.cooldown_max)
        argv = arm_argv(arm, self.tools, session=session)
        env = arm_env(arm, sizes, self.args, min_time)
        cpus = self.cpus(arm)
        log = RESULTS / 'logs' / self.stem / f'{phase}-r{rnd}-{arm.name.replace("@", "-")}.log'
        sampler = Sampler(sysinfo.parse_cpu_list(cpus) if cpus else [self.args.cpu], self.sensor)
        sampler.start()
        temp = temperature(self.sensor)
        run = launch(argv, env, cpus, log, self.args.timeout)
        samples = sampler.finish()
        log_text = log.read_text(errors='replace')
        outcome = classify(arm, run, log_text)
        problem = config_problem(arm, run['records']) if outcome == 'wrong-config' else None
        record = {
            'arm': arm.name, 'group': arm.group, 'kind': arm.kind, 'threads': arm.threads,
            'round': rnd, 'phase': phase, 'cpus': cpus, 'env': bench_env(env),
            'cooldown_s': waited, 'temp_start': temp, 'compile_s': compile_s,
            'outcome': outcome, 'error': problem or (None if outcome == 'ok' else failure_detail(run, log_text)),
            'log': str(log.relative_to(HERE)), 'sampler': samples, **run,
        }
        self.runs.append(record)
        best = max((r['gflops'] for r in run['records'] if r.get('type') == 'result'), default=None)
        note(f"{phase} round {rnd}: {arm.name}: {outcome}"
             + (f", best {best:.1f} GFLOP/s" if best else '')
             + (f" ({record['error']})" if record['error'] else ''))
        return record

    def warm_up(self):
        """One untimed launch of a REPL arm, so the first timed one does not
        pay for a cold disk cache."""
        repl = [a for a in self.selected if a.kind == 'repl' and a.name in self.available]
        if repl:
            note('warming the disk cache with one untimed REPL launch')
            arm = repl[0]
            launch(arm_argv(arm, self.tools), arm_env(arm, [32], self.args, 0.05), self.cpus(arm),
                   RESULTS / 'logs' / self.stem / 'warmup.log', self.args.timeout)

    def latency_pass(self, rng):
        arms = [a for a in self.selected if a.latency and a.name in self.available]
        for rnd in range(1, self.args.latency_rounds + 1):
            rng.shuffle(arms)
            for arm in arms:
                compile_s = None
                if arm.kind == 'native':
                    compile_s = self.timed_compile(arm)
                self.run_arm(arm, rnd, 'latency', [LATENCY_N], 0.2,
                             session=arm.kind == 'repl', compile_s=compile_s)

    def timed_compile(self, arm):
        """Compile the arm's program again, pinned like the REPL arms, and
        time it: the native counterpart of a REPL session's cells."""
        out = BUILD / 'latency' / native_binary(arm).name
        out.parent.mkdir(parents=True, exist_ok=True)
        argv = native_argv(arm, self.tools, out)
        cpus = self.cpus(arm)
        if cpus is not None and shutil.which('taskset'):
            argv = ['taskset', '-c', cpus] + argv
        t = time.monotonic()
        subprocess.run(argv, capture_output=True, timeout=600)
        return time.monotonic() - t

    def throughput_pass(self, rng):
        for rnd in range(1, self.args.rounds + 1):
            for threads_one in (True, False):
                arms = [a for a in self.selected if a.name in self.available
                        and (a.threads == 1) == threads_one and (not a.rounds or rnd <= a.rounds)]
                rng.shuffle(arms)
                for arm in arms:
                    self.run_arm(arm, rnd, 'throughput', self.sizes(arm), self.args.min_time)


# ---------------------------------------------------------------------------

def sizes_for(arm, args):
    """The sizes an arm runs in the throughput pass."""
    if args.sizes:
        return args.sizes
    return SIZES + (MT_EXTRA if arm.threads > 1 else [])


def parse_sizes(text):
    """'256,1024' as a list; 'a:b' as the default sizes from a to b."""
    if not text:
        return None
    if ':' in text:
        lo, hi = (int(x) for x in text.split(':'))
        return [n for n in SIZES + MT_EXTRA if lo <= n <= hi]
    return [int(x) for x in text.replace(' ', '').split(',') if x]


def default_harness_cpu(cpu, mt_cpus):
    busy = set(sysinfo.parse_cpu_list(mt_cpus)) | set(sysinfo.siblings(cpu))
    free = [c for c in sysinfo.logical_cpus() if c not in busy]
    return free[-1] if free else None


def main():
    p = argparse.ArgumentParser(
        prog='bench/run.py', description='NumPy vs Eigen in cpp-repl vs native Eigen: matrix multiplication.',
        epilog='bench/report.py turns the JSON this writes into tables and plots.')
    p.add_argument('--arms', default='all', help='groups (core, mt, diag, all) or arm names, comma-separated')
    p.add_argument('--threads', default=None,
                   help='only arms with these thread counts, e.g. 1 or 1,16 (default: all)')
    p.add_argument('--sizes', type=parse_sizes, default=None, help="'256,1024' or 'a:b' (default: the protocol's)")
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--latency-rounds', type=int, default=None, help='default: --rounds')
    p.add_argument('--min-time', type=float, default=1.0, help='seconds of samples per size')
    p.add_argument('--max-call', type=float, default=10.0, help='skip sizes predicted to take longer per call')
    p.add_argument('--session-n', type=int, default=512, help='size of the warm-session cells')
    p.add_argument('--cpu', type=int, default=None, help='CPU for one-thread arms (default 6)')
    p.add_argument('--mt-cpus', default=None, help='CPUs for multi-thread arms (default: one per physical core)')
    p.add_argument('--harness-cpu', type=int, default=None, help='CPU for this script (default: a free one)')
    p.add_argument('--no-pin', dest='pin', action='store_false', help='run without taskset')
    p.add_argument('--no-cooldown', dest='cooldown', action='store_false', help='do not wait for the CPU to cool')
    p.add_argument('--cooldown-margin', type=float, default=5.0, help='degrees above idle to wait for')
    p.add_argument('--cooldown-max', type=float, default=30.0, help='longest wait, in seconds')
    p.add_argument('--timeout', type=float, default=1800.0, help='seconds before an arm is stopped')
    p.add_argument('--no-malloc-tuning', dest='malloc_tuning', action='store_false',
                   help="leave glibc's mmap threshold at its default (see MALLOC_TUNABLES)")
    p.add_argument('--quick', action='store_true', help='1 round, n = 256 and 1024, 0.3 s per size')
    p.add_argument('--latency-only', action='store_true', help='only the cold-start and warm-session pass')
    p.add_argument('--no-latency', dest='latency', action='store_false', help='skip the latency pass')
    p.add_argument('--python', help='Python with NumPy for the NumPy arms (default: this one)')
    p.add_argument('--cxx', help='clang++ for the native arms and kernels (default: the one matching clang-repl)')
    p.add_argument('--gxx', help='g++ for eigen-native-gcc')
    p.add_argument('--cpp-repl', help='cpp-repl to run (default: bin/cpp-repl in this checkout)')
    p.add_argument('--clang-repl', help='clang-repl for cpp-repl (default: the newest on PATH)')
    p.add_argument('--eigen', help='Eigen include directory (default: EIGEN_ROOT, then /usr/include/eigen3)')
    p.add_argument('--libomp', help='libomp.so for the OpenMP arms (default: from clang++)')
    p.add_argument('--seed', type=int, default=0, help='seed for the order of arms within a round')
    p.add_argument('--tag', help='appended to the result file name')
    p.add_argument('--dry-run', action='store_true', help='print what would run, and run nothing')
    args = p.parse_args()

    if args.quick:
        args.rounds = 1
        args.sizes = args.sizes or QUICK_SIZES
        args.min_time = min(args.min_time, 0.3)
        args.cooldown_max = min(args.cooldown_max, 10.0)
    if args.latency_rounds is None:
        args.latency_rounds = args.rounds
    cores = sysinfo.physical_cores()
    if args.cpu is None:
        args.cpu = 6 if 6 in sysinfo.logical_cpus() else sysinfo.logical_cpus()[0]
    if args.mt_cpus is None:
        args.mt_cpus = sysinfo.format_cpu_list(cores)
    mt_threads = len(sysinfo.parse_cpu_list(args.mt_cpus))

    try:
        selected = arms_mod.select(args.arms, mt_threads)
    except ValueError as e:
        p.error(str(e))
    if args.threads:
        wanted = {int(t) for t in args.threads.split(',')}
        selected = [a for a in selected if a.threads in wanted]
    if not selected:
        p.error('no arms selected')

    tools = resolve_tools(args)
    if not tools['eigen'] and any(a.kind != 'numpy' for a in selected):
        p.error('no Eigen found: pass --eigen DIR or set EIGEN_ROOT')
    builds = planned_builds(selected, tools)

    if args.dry_run:
        print(json.dumps({k: v for k, v in tools.items()}, indent=2))
        print('\n# builds')
        for name, argv in builds:
            print(f'{name}: {" ".join(argv)}')
        print('\n# runs')
        for arm in selected:
            env = bench_env(arm_env(arm, sizes_for(arm, args), args, args.min_time))
            cpus = (args.mt_cpus if arm.threads > 1 else str(args.cpu)) if args.pin else None
            print(f'{arm.name} [{arm.group}] cpus={cpus} rounds={arm.rounds or args.rounds}')
            print('  ' + ' '.join(arm_argv(arm, tools, session=False)))
            print('  ' + ' '.join(f'{k}={v}' for k, v in sorted(env.items())))
        return 0

    if not shutil.which('taskset'):
        note('warning: taskset not found; running unpinned')
        args.pin = False
    built = build_all(builds)
    available, reasons = [], {}
    for arm in selected:
        why = unavailable_reason(arm, tools, built)
        if why:
            reasons[arm.name] = why
            note(f'{arm.name}: unavailable ({why})')
        else:
            available.append(arm.name)

    if args.pin:
        harness = args.harness_cpu if args.harness_cpu is not None else default_harness_cpu(args.cpu, args.mt_cpus)
        if harness is not None:
            os.sched_setaffinity(0, {harness})
    pinned = sorted(set(sysinfo.parse_cpu_list(args.mt_cpus)) | {args.cpu})
    system = sysinfo.collect(tools, pinned)
    if system['loadavg'][0] > 1.0:
        note(f"warning: load average {system['loadavg'][0]:.2f}; the machine is not idle")

    host = system['host']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%MZ')
    stem = f"{host}-llvm{tools['llvm'] or 'na'}-{stamp}" + (f'-{args.tag}' if args.tag else '')
    RESULTS.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec='seconds')
    bench = Bench(args, tools, selected, available, system, stem)
    rng = random.Random(args.seed)
    try:
        bench.warm_up()
        if args.latency or args.latency_only:
            bench.latency_pass(rng)
        if not args.latency_only:
            bench.throughput_pass(rng)
    except KeyboardInterrupt:
        note('interrupted; writing what ran so far')

    checks = validate(bench.runs, system)
    result = {
        'meta': {
            'version': 1, 'started': started, 'finished': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'argv': sys.argv, 'args': {k: v for k, v in vars(args).items()}, 'seed': args.seed,
            'tag': args.tag, 'llvm': tools['llvm'], 'mt_threads': mt_threads,
            'arms': [vars(a) | {'aot_lib': a.aot_lib} for a in selected],
            'unavailable': reasons, 'idle_temperature': bench.idle,
        },
        'tools': tools,
        'system': system,
        'builds': list(built.values()),
        'runs': bench.runs,
        'checks': checks,
        'valid': all(c['ok'] for c in checks),
    }
    path = RESULTS / f'{stem}.json'
    path.write_text(json.dumps(result, indent=1, default=str))
    failed = [c for c in checks if not c['ok']]
    note(f'{len(checks) - len(failed)} of {len(checks)} checks passed'
         + ('' if not failed else f"; first failure: {failed[0]['check']} {failed[0]['arm']} "
                                  f"n={failed[0]['n']}: {failed[0]['detail']}"))
    note(f'wrote {path.relative_to(REPO) if REPO in path.parents else path}')
    note(f'next: python bench/report.py {path}')
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
