"""Checks of the benchmark's own arithmetic and bookkeeping.

    python3 bench/test_bench.py        (or: python3 -m pytest bench/test_bench.py)

Nothing here runs a benchmark arm; the harness itself is exercised by
`bench/run.py --quick`.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arms  # noqa: E402
import report  # noqa: E402
import run  # noqa: E402
import sysinfo  # noqa: E402


def brute_checksum(n):
    a = [[((31 * i + 17 * j) % 97) / 97 for j in range(n)] for i in range(n)]
    b = [[((7 * i + 13 * j) % 89) / 89 for j in range(n)] for i in range(n)]
    return sum(a[i][k] * b[k][j] for i in range(n) for j in range(n) for k in range(n))


class Checksum(unittest.TestCase):
    def test_matches_brute_force(self):
        self.assertEqual(run.expected_checksum(1), 0.0)   # A(0, 0) is 0
        for n in (2, 5, 33, 97, 120):
            with self.subTest(n=n):
                self.assertAlmostEqual(run.expected_checksum(n) / brute_checksum(n), 1.0, places=12)

    def test_matches_the_cpp_arm(self):
        # bench/build/matmul-eigen-native printed this sum(C) at n = 64 and 256.
        self.assertAlmostEqual(run.expected_checksum(64), 64108.3584, places=3)
        self.assertAlmostEqual(run.expected_checksum(256), 4104360.13, places=1)


class Sizes(unittest.TestCase):
    def test_list_and_range(self):
        self.assertEqual(run.parse_sizes('256, 1024'), [256, 1024])
        self.assertEqual(run.parse_sizes('100:600'), [128, 256, 384, 512])
        self.assertIsNone(run.parse_sizes(''))

    def test_cpu_lists(self):
        self.assertEqual(sysinfo.parse_cpu_list('0-3,8'), [0, 1, 2, 3, 8])
        self.assertEqual(sysinfo.format_cpu_list([8, 0, 1, 2, 3]), '0-3,8')
        self.assertEqual(sysinfo.format_cpu_list([]), '')


class Arms(unittest.TestCase):
    def test_groups(self):
        self.assertEqual([a.name for a in arms.select('core')],
                         ['numpy', 'eigen-native', 'eigen-repl', 'eigen-repl-aot', 'eigen-repl-default'])
        self.assertEqual(len(arms.select('all')), 13)
        self.assertEqual([a.name for a in arms.select('mt', mt_threads=8)][0], 'numpy@8')
        with self.assertRaises(ValueError):
            arms.select('core,nonsense')

    def test_aot_libraries(self):
        by_name = {a.name: a for a in arms.all_arms()}
        self.assertEqual(by_name['eigen-repl-aot'].aot_lib, 'libbench_kernel.so')
        self.assertEqual(by_name['eigen-repl-aot-omp'].aot_lib, 'libbench_kernel_omp.so')
        self.assertIsNone(by_name['eigen-repl'].aot_lib)


class Verdicts(unittest.TestCase):
    def test_judge(self):
        self.assertEqual(report.judge((1.0, 0.97, 1.02), 0.95, 1.05), 'pass')
        self.assertEqual(report.judge((0.9, 0.85, 0.93), 0.95, 1.05), 'fail')
        self.assertEqual(report.judge((0.96, 0.93, 0.99), 0.95, 1.05), 'inconclusive')
        self.assertEqual(report.judge(None), 'no data')

    def test_combine(self):
        self.assertEqual(report.combine(['pass', 'pass']), 'pass')
        self.assertEqual(report.combine(['pass', 'inconclusive']), 'inconclusive')
        self.assertEqual(report.combine(['pass', 'fail', 'inconclusive']), 'fail')
        self.assertEqual(report.combine(['no data']), 'no data')


class Latency(unittest.TestCase):
    def test_repl_cells(self):
        launch = 100.0
        records = [
            {'type': 'stamp', 'name': 'start', 't': 101.0},
            {'type': 'env', '_arrived': 101.1},
            {'type': 'stamp', 'name': 'headers', 't': 103.0},
            {'type': 'stamp', 'name': 'control', 't': 103.05},
            {'type': 'stamp', 'name': 'kernel', 't': 105.05},
            {'type': 'result', 'n': 256, 't_first_done': 105.5},
            {'type': 'stamp', 'name': 'warm', 't': 107.0},
            {'type': 'cell', 'name': 'rerun', 'run': 0.03},
            {'type': 'stamp', 'name': 'rerun', 't': 107.13},
            {'type': 'cell', 'name': 'new-instantiation', 'run': 0.03},
            {'type': 'stamp', 'name': 'new-instantiation', 't': 109.13},
        ]
        data = {'runs': [{'phase': 'latency', 'arm': 'eigen-repl', 'kind': 'repl', 'round': 1,
                          'outcome': 'ok', 'launch': launch, 'records': records}]}
        row = report.latency_rows(data)[0]
        self.assertAlmostEqual(row['process_start'], 1.0)
        self.assertAlmostEqual(row['imports'], 2.0)
        self.assertAlmostEqual(row['control'], 0.05)
        self.assertAlmostEqual(row['compile'], 1.95)
        self.assertAlmostEqual(row['first_product'], 5.5)
        self.assertAlmostEqual(row['rerun'], 0.05)
        self.assertAlmostEqual(row['new_expression'], 1.92)


class Outcomes(unittest.TestCase):
    def test_a_warm_session_that_never_ran(self):
        # matmul.cpp's main() ended in `return 0;` from 47c0ac7 on, and
        # cpp-repl ran nothing after it: the latency runs had no warm session.
        arm = {a.name: a for a in arms.all_arms()}['eigen-repl']
        records = [{'type': 'env', 'eigen_threads': 1, 'optimize': True}, {'type': 'done'}]
        log = 'BENCH {"type":"done"}\nc++> return 0;\n[main returned 0: bench/cpp/session.repl not run]\n'
        ran = {'records': records, 'timed_out': False, 'exit': 0}
        self.assertEqual(run.classify(arm, ran, log), 'ok')
        self.assertEqual(run.classify(arm, ran, log, session=True), 'missing-session')
        self.assertEqual(run.failure_detail(ran, log), '[main returned 0: bench/cpp/session.repl not run]')
        records.append({'type': 'stamp', 'name': run.SESSION_LAST, 't': 1.0})
        self.assertEqual(run.classify(arm, ran, '', session=True), 'ok')


if __name__ == '__main__':
    unittest.main()
