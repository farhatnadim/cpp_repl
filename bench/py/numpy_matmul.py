"""The NumPy arm: np.matmul(A, B, out=C) under the protocol of bench/cpp/bench.hpp.

bench/run.py runs this with the BENCH_* environment variables the C++ arms
read, and it prints the same "BENCH {json}" records: env, result, skip, done,
and error when it refuses to run. The thread count must be in the
environment before NumPy is imported, because the BLAS library reads it when
it loads.
"""

import json
import math
import os
import sys
import time

THREADS = os.environ.get('BENCH_THREADS', '1')
for _var in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'BLIS_NUM_THREADS'):
    os.environ[_var] = THREADS

import numpy as np  # noqa: E402

try:
    from threadpoolctl import threadpool_info  # noqa: E402
except ImportError:
    threadpool_info = None

# The BLAS libraries worth timing. NumPy linked against netlib's reference
# BLAS (Ubuntu's python3-numpy without libopenblas) shows up as none of them.
OPTIMISED = ('openblas', 'mkl', 'blis')


def emit(kind, **fields):
    print('BENCH ' + json.dumps({'type': kind, **fields}, separators=(',', ':')), flush=True)


def env_list(name, default):
    return [int(s) for s in os.environ.get(name, default).replace(',', ' ').split() if int(s) > 0]


def env_float(name, default):
    return float(os.environ.get(name) or default)


def blas():
    """The BLAS thread pool threadpoolctl finds, or None."""
    if threadpool_info is None:
        return None
    pools = [p for p in threadpool_info() if p.get('user_api') == 'blas']
    return pools[0] if pools else None


def fill(n):
    """The matrices bench.hpp builds: A(i, j) = ((31i + 17j) mod 97) / 97 and
    B(i, j) = ((7i + 13j) mod 89) / 89, row-major here and column-major there,
    which are the same matrices. C is zeroed so its pages exist before the
    first timed call."""
    i = np.arange(n, dtype=np.int64)[:, None]
    j = np.arange(n, dtype=np.int64)[None, :]
    a = ((31 * i + 17 * j) % 97) / 97.0
    b = ((7 * i + 13 * j) % 89) / 89.0
    c = np.empty((n, n))
    c.fill(0.0)
    for m in (a, b, c):
        assert m.dtype == np.float64 and m.flags.c_contiguous
    return a, b, c


def freivalds(a, b, c):
    """|C v - A (B v)| / |A (B v)| for the vector bench.hpp uses."""
    n = a.shape[0]
    v = ((7 * np.arange(n)) % 13 + 1) / 13.0
    abv = a @ (b @ v)
    return float(np.linalg.norm(c @ v - abv) / np.linalg.norm(abv))


def measure(n, cfg):
    a, b, c = fill(n)
    t_start = time.monotonic()
    np.matmul(a, b, out=c)
    t_first_done = time.monotonic()
    first = t_first_done - t_start
    too_long = first > cfg['max_call']
    calls = 1
    while not too_long and (calls < 2 or time.monotonic() - t_start < cfg['warmup_time']):
        np.matmul(a, b, out=c)
        calls += 1
    per_call = (time.monotonic() - t_start) / calls
    k = max(1, math.ceil(cfg['sample_time'] / per_call))
    samples = []
    sampling = time.monotonic()
    while not too_long and len(samples) < cfg['max_samples']:
        elapsed = time.monotonic() - sampling
        if elapsed >= cfg['max_time']:
            break
        if len(samples) >= cfg['min_samples'] and elapsed >= cfg['min_time']:
            break
        s = time.monotonic()
        for _ in range(k):
            np.matmul(a, b, out=c)
        samples.append((time.monotonic() - s) / k)
    if not samples:
        samples.append(first)
    t_end = time.monotonic()
    median = float(np.median(samples))
    emit('result', arm=cfg['arm'], n=n, threads=cfg['threads'], first_call=first,
         warmup_calls=calls, k=k, samples=samples, median=median,
         gflops=2.0 * n ** 3 / median * 1e-9, checksum=float(c.sum()),
         freivalds=freivalds(a, b, c), too_long=too_long,
         t_start=t_start, t_first_done=t_first_done, t_end=t_end)
    return median


def main():
    cfg = {
        'arm': os.environ.get('BENCH_ARM', 'numpy'),
        'sizes': env_list('BENCH_SIZES', '32,64,128,256,384,512,768,1024,1536,2048,3072'),
        'threads': int(THREADS),
        'min_time': env_float('BENCH_MIN_TIME', 1.0),
        'min_samples': int(env_float('BENCH_MIN_SAMPLES', 5)),
        'max_samples': int(env_float('BENCH_MAX_SAMPLES', 50)),
        'max_time': env_float('BENCH_MAX_TIME', 30.0),
        'sample_time': env_float('BENCH_SAMPLE_TIME', 0.02),
        'warmup_time': env_float('BENCH_WARMUP_TIME', 0.25),
        'max_call': env_float('BENCH_MAX_CALL', 10.0),
        'max_n': int(env_float('BENCH_MAX_N', 0)),
    }
    pool = blas()
    emit('env', arm=cfg['arm'], threads=cfg['threads'], numpy=np.__version__,
         python=sys.version.split()[0], executable=sys.executable, blas=pool)
    if threadpool_info is None:
        emit('error', arm=cfg['arm'], reason='threadpoolctl is not installed, so the BLAS '
             'NumPy uses cannot be checked. Install threadpoolctl next to NumPy.')
        return 2
    if pool is None or pool.get('internal_api') not in OPTIMISED:
        emit('error', arm=cfg['arm'], reason='NumPy is not using an optimised BLAS '
             '(OpenBLAS, MKL or BLIS); with netlib reference BLAS this would time Fortran '
             'loops, not NumPy. Use a NumPy wheel from pip.')
        return 2
    if pool.get('num_threads') != cfg['threads']:
        emit('error', arm=cfg['arm'], reason=f"BLAS runs {pool.get('num_threads')} threads, "
             f"not the {cfg['threads']} requested")
        return 2

    prev_n, prev_t, stop = 0, 0.0, None
    for n in cfg['sizes']:
        predicted = prev_t * (n / prev_n) ** 3 if prev_t > 0 else 0.0
        if not stop and cfg['max_n'] > 0 and n > cfg['max_n']:
            stop = "above the arm's size cap"
        if not stop and predicted > cfg['max_call']:
            stop = 'predicted call time above max_call'
        if stop:
            emit('skip', arm=cfg['arm'], n=n, reason=stop, predicted=predicted)
            continue
        prev_t, prev_n = measure(n, cfg), n
        if prev_t > cfg['max_call']:
            stop = 'a smaller size took longer than max_call'
    emit('done', arm=cfg['arm'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
