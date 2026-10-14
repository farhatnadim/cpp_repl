"""What the benchmark ran on: CPU, clocks, memory settings, tools and versions.

Everything here is read, never changed: the harness records the governor,
boost and huge-page settings as it finds them. Files that are missing or
unreadable (inside a container, on another CPU vendor) come back as None.
"""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path

CPU = Path('/sys/devices/system/cpu')


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def read_int(path):
    text = read(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def first_line(argv):
    """The first line a tool prints for argv, or None if it cannot run."""
    if not argv or not shutil.which(argv[0]):
        return None
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else None


def llvm_major(version_line):
    """The major version in a clang or clang-repl --version line."""
    m = re.search(r'(?:LLVM|clang) version (\d+)', version_line or '')
    return int(m.group(1)) if m else None


def parse_cpu_list(text):
    """'0-3,8' as [0, 1, 2, 3, 8]."""
    cpus = []
    for part in (text or '').split(','):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition('-')
        cpus.extend(range(int(lo), int(hi or lo) + 1))
    return cpus


def format_cpu_list(cpus):
    """[0, 1, 2, 3, 8] as '0-3,8'."""
    out, cpus = [], sorted(set(cpus))
    i = 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if i == j else f'{cpus[i]}-{cpus[j]}')
        i = j + 1
    return ','.join(out)


def logical_cpus():
    """Every online CPU, whatever this process's own affinity is."""
    return parse_cpu_list(read(CPU / 'online')) or list(range(os.cpu_count() or 1))


def siblings(cpu):
    """The logical CPUs sharing cpu's physical core, cpu included."""
    return parse_cpu_list(read(CPU / f'cpu{cpu}' / 'topology' / 'thread_siblings_list')) or [cpu]


def physical_cores():
    """One logical CPU per physical core: the lowest-numbered sibling."""
    return sorted({min(siblings(c)) for c in logical_cpus()})


def cpu_flags():
    text = read('/proc/cpuinfo') or ''
    m = re.search(r'^flags\s*:\s*(.*)$', text, re.M)
    return set(m.group(1).split()) if m else set()


def flops_per_cycle(flags):
    """Double-precision FLOP per cycle per core for the widest FMA the CPU
    has, assuming two FMA pipes: 2 pipes x lanes x 2 (multiply and add)."""
    if 'avx512f' in flags:
        return 32
    if 'avx2' in flags and 'fma' in flags:
        return 16
    if 'avx' in flags:
        return 8
    return 4


def max_mhz():
    khz = read_int(CPU / 'cpu0' / 'cpufreq' / 'cpuinfo_max_freq')
    return khz / 1000 if khz else None


def model_name():
    m = re.search(r'^model name\s*:\s*(.*)$', read('/proc/cpuinfo') or '', re.M)
    return m.group(1).strip() if m else platform.processor()


def microcode():
    m = re.search(r'^microcode\s*:\s*(\S+)', read('/proc/cpuinfo') or '', re.M)
    return m.group(1) if m else None


def selected(text):
    """'always [madvise] never' as 'madvise'."""
    m = re.search(r'\[(\w+)\]', text or '')
    return m.group(1) if m else text


def cpufreq(cpus):
    out = {
        'boost': read(CPU / 'cpufreq' / 'boost'),
        'per_cpu': {},
    }
    for c in cpus:
        d = CPU / f'cpu{c}' / 'cpufreq'
        out['per_cpu'][c] = {
            'driver': read(d / 'scaling_driver'),
            'governor': read(d / 'scaling_governor'),
            'epp': read(d / 'energy_performance_preference'),
            'max_khz': read_int(d / 'scaling_max_freq'),
        }
    return out


def preferred_cores():
    """Each CPU's preferred-core rank, higher meaning the scheduler favours it:
    amd-pstate's ranking, else ACPI CPPC's highest performance level."""
    ranks = {}
    for c in logical_cpus():
        rank = read_int(CPU / f'cpu{c}' / 'cpufreq' / 'amd_pstate_prefcore_ranking')
        if rank is None:
            rank = read_int(CPU / f'cpu{c}' / 'acpi_cppc' / 'highest_perf')
        if rank is not None:
            ranks[c] = rank
    return ranks or None


def temperature_sensor():
    """The file holding the CPU package temperature in millidegrees: AMD's
    k10temp Tctl, or Intel's coretemp package sensor."""
    for hwmon in sorted(Path('/sys/class/hwmon').glob('hwmon*')):
        name = read(hwmon / 'name')
        if name not in ('k10temp', 'coretemp', 'zenpower'):
            continue
        for label in sorted(hwmon.glob('temp*_label')):
            if read(label) in ('Tctl', 'Tdie', 'Package id 0'):
                path = label.with_name(label.name.replace('_label', '_input'))
                if path.exists():
                    return str(path)
    return None


def eigen_version(include_dir):
    """The version of the Eigen in include_dir. Eigen 5 spells it in
    EIGEN_VERSION_STRING and keeps the three old macros at 3.5.0."""
    root = Path(include_dir) / 'Eigen'
    text = ''.join(read(p) or '' for p in (root / 'Version', root / 'src' / 'Core' / 'util' / 'Macros.h'))
    m = re.search(r'#define EIGEN_VERSION_STRING\s+"([^"]+)"', text)
    if m:
        return m.group(1)
    parts = dict(re.findall(r'#define EIGEN_(WORLD|MAJOR|MINOR)_VERSION (\d+)', text))
    if parts:
        return f"{parts.get('WORLD')}.{parts.get('MAJOR')}.{parts.get('MINOR')}"
    return None


_PY_PROBE = r'''
import json, sys
info = {'python': sys.version.split()[0], 'executable': sys.executable}
try:
    import numpy
    info['numpy'] = numpy.__version__
except ImportError:
    info['numpy'] = None
try:
    from threadpoolctl import threadpool_info
    info['threadpools'] = threadpool_info()
except ImportError:
    info['threadpools'] = None
print(json.dumps(info))
'''


def python_info(python):
    """The NumPy and BLAS behind the Python that runs the NumPy arm."""
    if not python:
        return None
    try:
        out = subprocess.run([python, '-c', _PY_PROBE], capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def collect(tools, cpus):
    """The record bench/run.py stores with every result file. tools maps a
    name to an argv prefix or a path; cpus are the CPUs the arms are pinned to."""
    flags = cpu_flags()
    lscpu = None
    if shutil.which('lscpu'):
        try:
            lscpu = json.loads(subprocess.run(['lscpu', '-J'], capture_output=True, text=True,
                                              timeout=30).stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            lscpu = None
    return {
        'host': os.environ.get('BENCH_HOST') or socket.gethostname(),
        'kernel': platform.release(),
        'in_docker': Path('/.dockerenv').exists(),
        'docker_image': os.environ.get('BENCH_IMAGE_ID'),
        'cpu': {
            'model': model_name(),
            'logical': len(logical_cpus()),
            'physical': len(physical_cores()),
            'physical_cores': format_cpu_list(physical_cores()),
            'flags': sorted(f for f in flags if f in ('avx', 'avx2', 'fma', 'avx512f', 'avx512vl')),
            'flops_per_cycle': flops_per_cycle(flags),
            'max_mhz': max_mhz(),
            'microcode': microcode(),
            'preferred_cores': preferred_cores(),
        },
        'cpufreq': cpufreq(cpus),
        'thp': {
            'enabled': selected(read('/sys/kernel/mm/transparent_hugepage/enabled')),
            'defrag': selected(read('/sys/kernel/mm/transparent_hugepage/defrag')),
        },
        'loadavg': os.getloadavg(),
        'eigen': {'path': tools.get('eigen'), 'version': eigen_version(tools['eigen']) if tools.get('eigen') else None},
        'temperature_sensor': temperature_sensor(),
        'tools': {name: first_line([path, '--version']) if path else None
                  for name, path in tools.items() if name in ('cxx', 'gxx', 'clang_repl')},
        'python': python_info(tools.get('python')),
        'lscpu': lscpu,
    }
