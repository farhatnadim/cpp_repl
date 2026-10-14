"""The arms of the matmul benchmark: what runs, how it is built, and with what.

Three groups:
  core  one thread: NumPy, native Eigen, Eigen in cpp-repl (tuned, with a
        precompiled kernel, and with no flags at all)
  mt    every physical core: the same comparison with OpenMP and OpenBLAS threads
  diag  arms that explain a gap: plain loops native and in the REPL, the REPL
        without -march=native, and native Eigen built by GCC
"""

from dataclasses import dataclass

# The compile flags every tuned C++ arm uses, and the precompiled kernels too.
FLAGS = {
    'full': ['-std=c++20', '-O3', '-march=native', '-DNDEBUG'],
    'generic': ['-std=c++20', '-O3', '-DNDEBUG'],
    'default': ['-std=c++20'],
}


@dataclass(frozen=True)
class Arm:
    name: str
    group: str            # core, mt or diag
    kind: str             # numpy, native or repl
    description: str
    threads: int = 1
    compiler: str = 'clang'   # native arms: clang or gcc
    flags: str = 'full'       # a key of FLAGS
    kernel: str = 'eigen'     # eigen, naive, or aot (the precompiled kernel)
    openmp: bool = False
    max_n: int = 0            # skip sizes above this, when positive
    rounds: int = 0           # run in this many rounds only, when positive
    baseline: str = ''        # the arm its ratios are taken against
    latency: bool = False     # also part of the cold-start latency pass

    @property
    def aot_lib(self):
        """The precompiled kernel library this arm loads, or None."""
        if self.kernel != 'aot':
            return None
        return 'libbench_kernel_omp.so' if self.openmp else 'libbench_kernel.so'


def all_arms(mt_threads=16):
    mt = mt_threads
    return [
        Arm('numpy', 'core', 'numpy', 'np.matmul with OpenBLAS, one thread',
            baseline='eigen-native', latency=True),
        Arm('eigen-native', 'core', 'native', 'clang++ -O3 -march=native', latency=True),
        Arm('eigen-repl', 'core', 'repl', 'cpp-repl -O3 -march=native',
            baseline='eigen-native', latency=True),
        Arm('eigen-repl-aot', 'core', 'repl',
            'cpp-repl with default flags, calling a kernel compiled -O3 -march=native',
            flags='default', kernel='aot', baseline='eigen-native', latency=True),
        Arm('eigen-repl-default', 'core', 'repl', 'cpp-repl with no flags (-O0)',
            flags='default', max_n=1024, rounds=1, baseline='eigen-native', latency=True),

        Arm(f'numpy@{mt}', 'mt', 'numpy', f'np.matmul with OpenBLAS, {mt} threads',
            threads=mt, baseline='eigen-native-omp'),
        Arm('eigen-native-omp', 'mt', 'native', f'clang++ -O3 -march=native -fopenmp, {mt} threads',
            threads=mt, openmp=True),
        Arm('eigen-repl-omp', 'mt', 'repl', f'cpp-repl -O3 -march=native -fopenmp, {mt} threads',
            threads=mt, openmp=True, baseline='eigen-native-omp'),
        Arm('eigen-repl-aot-omp', 'mt', 'repl',
            f'cpp-repl with default flags, calling a kernel compiled with OpenMP, {mt} threads',
            threads=mt, flags='default', kernel='aot', openmp=True, baseline='eigen-native-omp'),

        Arm('naive-native', 'diag', 'native', 'plain loops, clang++ -O3 -march=native',
            kernel='naive', max_n=1024),
        Arm('naive-repl', 'diag', 'repl', 'plain loops, cpp-repl -O3 -march=native',
            kernel='naive', max_n=1024, baseline='naive-native'),
        Arm('eigen-repl-generic', 'diag', 'repl', 'cpp-repl -O3 without -march=native',
            flags='generic', baseline='eigen-repl'),
        Arm('eigen-native-gcc', 'diag', 'native', 'g++ -O3 -march=native',
            compiler='gcc', baseline='eigen-native'),
    ]


def select(spec, mt_threads=16):
    """The arms named by spec: a comma-separated list of groups (core, mt,
    diag, all) and arm names, in the order of all_arms()."""
    arms = all_arms(mt_threads)
    wanted = {w.strip() for w in spec.split(',') if w.strip()}
    known = {a.name for a in arms} | {'core', 'mt', 'diag', 'all'}
    unknown = wanted - known
    if unknown:
        raise ValueError(f"unknown arm or group: {', '.join(sorted(unknown))}; "
                         f"known: {', '.join(sorted(known))}")
    return [a for a in arms if 'all' in wanted or a.group in wanted or a.name in wanted]
