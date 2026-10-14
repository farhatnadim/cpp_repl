#!/usr/bin/env python3
"""Turn a bench/run.py result file into tables, plots and hypothesis verdicts.

    python bench/report.py bench/results/<run>.json

Writes next to the JSON:
  <run>.md           tables, hypothesis verdicts, environment, failed checks
  <run>.csv          one row per arm, phase, round and size
  <run>-gflops.png   GFLOP/s against n, one panel per thread count, with the peak
  <run>-ratio.png    each arm's speed relative to the native arm it is compared with

Only the JSON is read, so a report can be rebuilt at any time. Statistics:
the headline is the median time per call over every sample of every round;
the spread is the interquartile range of those samples and the range of the
per-round medians; ratios between arms are paired within a round.
"""

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ONE_THREAD = ['eigen-native', 'numpy', 'eigen-repl', 'eigen-repl-aot', 'eigen-repl-default']
DIAG = ['naive-native', 'naive-repl', 'eigen-repl-generic', 'eigen-native-gcc']

# Fixed colour per entity, in the reference palette's validated order
# (dataviz skill, references/palette.md): the colour follows the arm across
# both panels and both plots, and a marker shape repeats the identity for
# readers who cannot tell the colours apart.
COLOURS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
MARKERS = ['o', 's', '^', 'D', 'v']
INK = {'primary': '#0b0b0b', 'secondary': '#52514e', 'muted': '#898781',
       'grid': '#e1e0d9', 'axis': '#c3c2b7', 'surface': '#fcfcfb', 'band': '#f0efec'}

LABELS = {
    'eigen-native': 'Eigen, native', 'numpy': 'NumPy', 'eigen-repl': 'Eigen in cpp-repl',
    'eigen-repl-aot': 'cpp-repl + compiled kernel', 'eigen-repl-default': 'cpp-repl, no flags',
    'eigen-native-omp': 'Eigen, native, OpenMP', 'eigen-repl-omp': 'Eigen in cpp-repl, OpenMP',
    'eigen-repl-aot-omp': 'cpp-repl + compiled OpenMP kernel',
    'naive-native': 'plain loops, native', 'naive-repl': 'plain loops in cpp-repl',
    'eigen-repl-generic': 'cpp-repl without -march=native', 'eigen-native-gcc': 'Eigen, native, g++',
}


def label(arm):
    if arm.startswith('numpy@'):
        return f"NumPy, {arm.split('@')[1]} threads"
    return LABELS.get(arm, arm)


def family(arm):
    """The one-thread arm whose colour an arm shares."""
    if arm.startswith('numpy'):
        return 'numpy'
    return arm.removesuffix('-omp')


# ---------------------------------------------------------------------------
# Reading results
# ---------------------------------------------------------------------------

class Stat:
    """One arm at one size over every round."""

    def __init__(self, arm, n, threads):
        self.arm, self.n, self.threads = arm, n, threads
        self.samples = []        # per-call seconds, every sample of every round
        self.rounds = {}         # round -> median per-call seconds
        self.first_calls = []

    def add(self, rnd, result):
        self.samples.extend(result['samples'])
        self.rounds[rnd] = statistics.median(result['samples'])
        self.first_calls.append(result['first_call'])

    @property
    def median(self):
        return statistics.median(self.samples)

    @property
    def quartiles(self):
        if len(self.samples) < 2:
            return self.samples[0], self.samples[0]
        q = statistics.quantiles(self.samples, n=4, method='inclusive')
        return q[0], q[2]

    def gflops(self, seconds=None):
        return 2.0 * self.n ** 3 / (seconds if seconds is not None else self.median) * 1e-9

    @property
    def round_gflops(self):
        return [self.gflops(t) for t in self.rounds.values()]


def load(path):
    data = json.loads(Path(path).read_text())
    stats, failures, skips = {}, {}, {}
    for run in data['runs']:
        if run['phase'] != 'throughput':
            continue
        if run['outcome'] != 'ok':
            failures.setdefault(run['arm'], f"{run['outcome']}: {run.get('error') or 'no detail'}")
            continue
        for rec in run['records']:
            if rec.get('type') == 'result':
                key = (run['arm'], rec['n'])
                stats.setdefault(key, Stat(run['arm'], rec['n'], run['threads'])).add(run['round'], rec)
            elif rec.get('type') == 'skip':
                skips.setdefault((run['arm'], rec['n']), rec.get('reason'))
    for arm, why in data['meta'].get('unavailable', {}).items():
        failures.setdefault(arm, f'unavailable: {why}')
    return data, stats, failures, skips


def ratio(stats, arm, base, n):
    """Speed of arm relative to base at size n (above 1: arm is faster), as
    (median, low, high) over rounds run by both, paired within a round. An
    arm run in fewer rounds is paired with base's matching rounds."""
    a, b = stats.get((arm, n)), stats.get((base, n))
    if not a or not b:
        return None
    pairs = [b.rounds[r] / a.rounds[r] for r in a.rounds if r in b.rounds]
    if not pairs:
        return None
    return statistics.median(pairs), min(pairs), max(pairs)


# ---------------------------------------------------------------------------
# Hypotheses
# ---------------------------------------------------------------------------

def judge(r, lo=-math.inf, hi=math.inf):
    """pass when every round's ratio lies in [lo, hi], fail when none does,
    inconclusive when the rounds straddle a threshold."""
    if r is None:
        return 'no data'
    _, low, high = r
    if lo <= low and high <= hi:
        return 'pass'
    if high < lo or low > hi:
        return 'fail'
    return 'inconclusive'


def combine(verdicts):
    vs = [v for v in verdicts if v != 'no data']
    if not vs:
        return 'no data'
    if 'fail' in vs:
        return 'fail'
    return 'pass' if all(v == 'pass' for v in vs) else 'inconclusive'


def fmt_ratio(r):
    if r is None:
        return 'n/a'
    m, lo, hi = r
    return f'{m:.3f}' if math.isclose(lo, hi) else f'{m:.3f} [{lo:.3f}–{hi:.3f}]'


def sizes_of(stats, arm, at_least=0, at_most=math.inf):
    return sorted(n for (a, n) in stats if a == arm and at_least <= n <= at_most)


def hypotheses(data, stats, failures, latency):
    out = []

    def per_size(arm, base, lo, hi, at_least=0, at_most=math.inf):
        rows = [(n, ratio(stats, arm, base, n)) for n in sizes_of(stats, arm, at_least, at_most)]
        verdict = combine(judge(r, lo, hi) for _, r in rows)
        detail = ', '.join(f'n={n}: {fmt_ratio(r)}' for n, r in rows) or failures.get(arm, 'no data')
        return verdict, detail

    out.append(('H0', 'the configuration is as intended (every validity check passes)',
                'pass' if data['valid'] else 'fail',
                f"{sum(c['ok'] for c in data['checks'])} of {len(data['checks'])} checks passed"))
    out.append(('H1', 'Eigen in cpp-repl runs at 0.95–1.05 of native for n ≥ 256',
                *per_size('eigen-repl', 'eigen-native', 0.95, 1.05, at_least=256)))
    r = ratio(stats, 'eigen-repl-default', 'eigen-native', 256)
    out.append(('H2', 'cpp-repl with no flags is at least 20× slower than native at n = 256',
                judge(r, hi=1 / 20), f'speed ratio {fmt_ratio(r)}' if r else failures.get('eigen-repl-default', 'no data')))
    out.append(('H3', 'cpp-repl calling the compiled kernel runs at 0.97–1.03 of native for n ≥ 128',
                *per_size('eigen-repl-aot', 'eigen-native', 0.97, 1.03, at_least=128)))
    out.append(('H4', 'cpp-repl without -march=native reaches at most 0.6 of cpp-repl with it, for n ≥ 512',
                *per_size('eigen-repl-generic', 'eigen-repl', -math.inf, 0.6, at_least=512)))
    big = per_size('numpy', 'eigen-native', 1.0, math.inf, at_least=512)
    small = per_size('numpy', 'eigen-native', -math.inf, 1.0, at_most=64)
    cross = next((n for n in sizes_of(stats, 'numpy')
                  if (ratio(stats, 'numpy', 'eigen-native', n) or (0,))[0] >= 1.0), None)
    out.append(('H5', 'NumPy is at least as fast as native Eigen for n ≥ 512, and Eigen is ahead at n ≤ 64',
                combine([big[0], small[0]]),
                f'n ≥ 512: {big[1]}; n ≤ 64: {small[1]}; '
                + (f'NumPy first catches up at n = {cross}' if cross else 'NumPy never catches up')))
    mt = data['meta'].get('mt_threads', 16)
    omp_failed = failures.get('eigen-repl-omp')
    if omp_failed:
        h6 = ('pass', f'OpenMP in the JIT failed with a recorded reason: {omp_failed}')
    else:
        h6 = per_size('eigen-repl-omp', 'eigen-native-omp', 0.95, 1.05)
    aot = per_size('eigen-repl-aot-omp', 'eigen-native-omp', 0.95, 1.05)
    out.append(('H6', f'with {mt} threads, OpenMP in the JIT runs at 0.95–1.05 of native or fails with a '
                      f'recorded reason; the compiled OpenMP kernel runs at 0.95–1.05',
                combine([h6[0], aot[0]]), f'JIT: {h6[1]}; compiled kernel: {aot[1]}'))
    rerun = [row['rerun'] for row in latency if row['arm'] == 'eigen-repl' and row.get('rerun') is not None]
    if rerun:
        h7 = 'pass' if max(rerun) < 0.2 else 'fail' if min(rerun) >= 0.2 else 'inconclusive'
        detail = f"overhead {statistics.median(rerun):.3f} s [{min(rerun):.3f}–{max(rerun):.3f}] over {len(rerun)} rounds"
    else:
        h7, detail = 'no data', 'no warm-session data for eigen-repl'
    out.append(('H7', 'in a warm session, re-running the compiled kernel costs under 0.2 s beyond the run itself',
                h7, detail))
    return out


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------

def latency_rows(data):
    """One row per latency run: what each part of a cold start cost, and
    for the REPL arms the cost of two warm cells beyond their own run time."""
    rows = []
    for run in data['runs']:
        if run['phase'] != 'latency':
            continue
        recs = run['records']
        stamps = {r['name']: r['t'] for r in recs if r.get('type') == 'stamp'}
        cells = {r['name']: r['run'] for r in recs if r.get('type') == 'cell'}
        result = next((r for r in recs if r.get('type') == 'result'), None)
        env = next((r for r in recs if r.get('type') == 'env'), None)
        launch = run['launch']
        row = {'arm': run['arm'], 'round': run['round'], 'outcome': run['outcome']}
        if result:
            row['first_product'] = result['t_first_done'] - launch + (run.get('compile_s') or 0.0)
        if run['kind'] == 'numpy' and env:
            row['imports'] = env['_arrived'] - launch
        if run['kind'] == 'native':
            row['compile'] = run.get('compile_s')
            if 'start' in stamps:
                row['process_start'] = stamps['start'] - launch
        if run['kind'] == 'repl' and {'start', 'headers', 'control', 'kernel'} <= stamps.keys():
            control = stamps['control'] - stamps['headers']
            row.update(process_start=stamps['start'] - launch, imports=stamps['headers'] - stamps['start'],
                       control=control, compile=(stamps['kernel'] - stamps['control']) - control)
            if {'warm', 'rerun'} <= stamps.keys() and 'rerun' in cells:
                row['rerun'] = (stamps['rerun'] - stamps['warm']) - cells['rerun'] - control
            if {'rerun', 'new-instantiation'} <= stamps.keys() and 'new-instantiation' in cells:
                row['new_expression'] = (stamps['new-instantiation'] - stamps['rerun']) - cells['new-instantiation'] - control
        rows.append(row)
    return rows


def median_cell(rows, arm, key, unit='s'):
    values = [r[key] for r in rows if r['arm'] == arm and r.get(key) is not None]
    if not values:
        return '—'
    m = statistics.median(values)
    text = f'{m:.3f} {unit}' if m < 10 else f'{m:.1f} {unit}'
    return text if len(values) == 1 else f'{text} [{min(values):.3f}–{max(values):.3f}]'


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def cell(stats, failures, skips, arm, n):
    s = stats.get((arm, n))
    if s:
        return f'{s.gflops():.1f}'
    if (arm, n) in skips:
        return 'skipped'
    if arm in failures:
        return 'n/a'
    return '—'


def table(header, rows, numeric=True):
    """A markdown table; with numeric, every column but the first is right-aligned."""
    rule = ['---:' if numeric and i else '---' for i in range(len(header))]
    out = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join(rule) + '|']
    out += ['| ' + ' | '.join(str(c) for c in row) + ' |' for row in rows]
    return '\n'.join(out)


def mean_mhz(data, threads_one):
    values = []
    for run in data['runs']:
        if run['phase'] == 'throughput' and (run['threads'] == 1) == threads_one:
            values += [s[1] for s in run.get('sampler') or [] if s[1]]
    return statistics.mean(values) if values else None


def peak(data, threads, mhz):
    cpu = data['system']['cpu']
    if not mhz or not cpu.get('flops_per_cycle'):
        return None
    return cpu['flops_per_cycle'] * mhz / 1000 * threads


def markdown(data, stats, failures, skips, verdicts, latency, stem):
    meta, system, tools = data['meta'], data['system'], data['tools']
    mt = meta.get('mt_threads', 16)
    lines = [f"# Matmul benchmark: {stem}", '',
             f"Run {meta['started']} to {meta['finished']} on {system['cpu']['model']} "
             f"({system['host']}), LLVM {meta.get('llvm')}. Values are GFLOP/s (2n³ per call over the "
             f"median time per call); higher is better. Ratios are paired within a round, with the range "
             f"over rounds in brackets.", '']

    def ratio_col(arm, base, n):
        return fmt_ratio(ratio(stats, arm, base, n))

    one = [a for a in ONE_THREAD if any(k[0] == a for k in stats) or a in failures]
    if one:
        ns = sorted({n for (a, n) in stats if a in ONE_THREAD})
        header = ['n'] + [label(a) for a in one] + ['REPL / native']
        rows = [[n] + [cell(stats, failures, skips, a, n) for a in one] + [ratio_col('eigen-repl', 'eigen-native', n)]
                for n in ns]
        lines += ['## One thread', '', table(header, rows), '']
    mt_arms = [f'numpy@{mt}', 'eigen-native-omp', 'eigen-repl-omp', 'eigen-repl-aot-omp']
    mt_arms = [a for a in mt_arms if any(k[0] == a for k in stats) or a in failures]
    if mt_arms:
        ns = sorted({n for (a, n) in stats if a in mt_arms})
        header = ['n'] + [label(a) for a in mt_arms] + ['REPL / native', 'NumPy speed-up', 'Eigen native speed-up']

        def speedup(arm, one_arm, n):
            a, b = stats.get((arm, n)), stats.get((one_arm, n))
            return f'{b.median / a.median:.1f}×' if a and b else '—'
        rows = [[n] + [cell(stats, failures, skips, a, n) for a in mt_arms]
                + [ratio_col('eigen-repl-omp', 'eigen-native-omp', n), speedup(f'numpy@{mt}', 'numpy', n),
                   speedup('eigen-native-omp', 'eigen-native', n)] for n in ns]
        lines += [f'## {mt} threads', '', table(header, rows), '']
    if latency:
        arms = []
        for row in latency:
            if row['arm'] not in arms:
                arms.append(row['arm'])
        rows = [[label(a), median_cell(latency, a, 'process_start'), median_cell(latency, a, 'imports'),
                 median_cell(latency, a, 'compile'), median_cell(latency, a, 'first_product'),
                 median_cell(latency, a, 'rerun'), median_cell(latency, a, 'new_expression')] for a in arms]
        lines += ['## Latency', '',
                  'Cold start, from launch: process start-up, then headers (REPL) or `import numpy`, then compiling '
                  'the kernel (for native: the whole program, before launch), then the first product at n = 256. '
                  'Warm session (REPL only): what re-running the compiled kernel, and typing a new Eigen expression '
                  '(`A.transpose() * B`), cost beyond their own run time.', '',
                  table(['arm', 'start-up', 'headers / import', 'kernel compile', 'first product', 'warm re-run',
                         'new expression'], rows), '']
    diag = [a for a in DIAG if any(k[0] == a for k in stats) or a in failures]
    if diag:
        ns = sorted({n for (a, n) in stats if a in diag})
        header = (['n'] + [label(a) for a in diag]
                  + ['naive REPL / native', 'generic / tuned REPL', 'g++ / clang++'])
        rows = [[n] + [cell(stats, failures, skips, a, n) for a in diag]
                + [ratio_col('naive-repl', 'naive-native', n), ratio_col('eigen-repl-generic', 'eigen-repl', n),
                   ratio_col('eigen-native-gcc', 'eigen-native', n)] for n in ns]
        lines += ['## Diagnostics', '', table(header, rows), '']
    lines += ['## Hypotheses', '', 'Written down before the first run (bench/README.md). Each is judged on the '
              'per-round values: pass when every round meets it, fail when none does, inconclusive otherwise.', '',
              table(['', 'hypothesis', 'verdict', 'evidence'], [[h, text, v, d] for h, text, v, d in verdicts],
                    numeric=False), '']
    if failures:
        lines += ['## Arms that did not run cleanly', '']
        lines += [f'- `{a}`: {why}' for a, why in sorted(failures.items())]
        lines.append('')
    bad = [c for c in data['checks'] if not c['ok']]
    if bad:
        lines += ['## Failed checks', '']
        lines += [f"- {c['check']}: `{c['arm']}` n={c['n']} round {c['round']}: {c['detail']}" for c in bad[:40]]
        lines.append('')
    one_mhz, mt_mhz = mean_mhz(data, True), mean_mhz(data, False)
    py = system.get('python') or {}
    blas = next((p for p in py.get('threadpools') or [] if p.get('user_api') == 'blas'), {})
    temps = [s[2] for run in data['runs'] for s in run.get('sampler') or [] if s[2] is not None]
    env_rows = [
        ['CPU', f"{system['cpu']['model']}: {system['cpu']['physical']} cores, {system['cpu']['logical']} threads, "
                f"{system['cpu']['flops_per_cycle']} FLOP/cycle/core, max {system['cpu']['max_mhz']:.0f} MHz"],
        ['mean clock while timing', f"1 thread {one_mhz:.0f} MHz" if one_mhz else '—'],
        ['', f"{mt} threads {mt_mhz:.0f} MHz" if mt_mhz else '—'],
        ['temperature', f'{min(temps):.0f}–{max(temps):.0f} °C' if temps else '—'],
        ['clang++', system['tools'].get('cxx') or '—'],
        ['clang-repl', system['tools'].get('clang_repl') or '—'],
        ['g++', system['tools'].get('gxx') or '—'],
        ['Eigen', f"{(system.get('eigen') or {}).get('version')} ({tools.get('eigen')})"],
        ['NumPy', f"{py.get('numpy')} with {blas.get('internal_api')} {blas.get('version')} "
                  f"({blas.get('architecture')})" if py else '—'],
        ['huge pages', str(system['thp'])],
        ['frequency policy', policy(system, meta['args'].get('cpu'))],
        ['load average at start', ', '.join(f'{x:.2f}' for x in system['loadavg'])],
        ['container', system.get('docker_image') or ('yes' if system.get('in_docker') else 'no')],
    ]
    lines += ['## Environment', '', table(['', ''], env_rows, numeric=False), '']
    return '\n'.join(lines)


def policy(system, cpu):
    """Driver, governor, energy preference and boost of the one-thread CPU."""
    per_cpu = system['cpufreq']['per_cpu']
    f = per_cpu.get(str(cpu)) or next(iter(per_cpu.values()), {})
    return (f"{f.get('driver')}, governor {f.get('governor')}, preference {f.get('epp')}, "
            f"boost {system['cpufreq'].get('boost')}")


def write_csv(path, data):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['arm', 'group', 'threads', 'phase', 'round', 'outcome', 'n', 'median_s', 'q1_s', 'q3_s', 'min_s',
                    'gflops', 'samples', 'k', 'first_call_s', 'checksum', 'freivalds', 'mean_mhz'])
        for run in data['runs']:
            mhz = [s[1] for s in run.get('sampler') or [] if s[1]]
            for rec in run['records']:
                if rec.get('type') != 'result':
                    continue
                s = sorted(rec['samples'])
                q = statistics.quantiles(s, n=4, method='inclusive') if len(s) > 1 else [s[0], s[0], s[0]]
                w.writerow([run['arm'], run['group'], run['threads'], run['phase'], run['round'], run['outcome'],
                            rec['n'], rec['median'], q[0], q[2], s[0], rec['gflops'], len(s), rec['k'],
                            rec['first_call'], rec['checksum'], rec['freivalds'],
                            round(statistics.mean(mhz)) if mhz else ''])


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def style(ax, xlabel, ylabel, title):
    ax.set_facecolor(INK['surface'])
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(INK['axis'])
        ax.spines[side].set_linewidth(0.8)
    ax.grid(axis='y', color=INK['grid'], linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK['muted'], labelsize=8, length=0)
    ax.set_xlabel(xlabel, color=INK['secondary'], fontsize=9)
    ax.set_ylabel(ylabel, color=INK['secondary'], fontsize=9)
    ax.set_title(title, color=INK['primary'], fontsize=10, loc='left')


def series_style(arm):
    i = ONE_THREAD.index(family(arm)) if family(arm) in ONE_THREAD else len(COLOURS) - 1
    return dict(color=COLOURS[i], marker=MARKERS[i], linewidth=1.5, markersize=6,
                markeredgecolor=INK['surface'], markeredgewidth=1.0)


def log2_axis(ax, ns):
    ax.set_xscale('log', base=2)
    ax.set_xticks(ns)
    ax.set_xticklabels([str(n) for n in ns])
    ax.minorticks_off()


def plots(data, stats, out_stem):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('report: matplotlib is not installed; skipping the plots', file=sys.stderr)
        return []
    mt = data['meta'].get('mt_threads', 16)
    panels = [('One thread', ONE_THREAD, 1, mean_mhz(data, True)),
              (f'{mt} threads', [f'numpy@{mt}', 'eigen-native-omp', 'eigen-repl-omp', 'eigen-repl-aot-omp'],
               mt, mean_mhz(data, False))]
    panels = [p for p in panels if any((a, n) in stats for a in p[1] for (_, n) in stats)]
    written = []
    if not panels:
        return written

    fig, axes = plt.subplots(1, len(panels), figsize=(6.4 * len(panels), 4.4), squeeze=False)
    fig.patch.set_facecolor(INK['surface'])
    for ax, (title, arms, threads, mhz) in zip(axes[0], panels):
        ns = sorted({n for (a, n) in stats if a in arms})
        for arm in arms:
            pts = [(n, stats[(arm, n)]) for n in ns if (arm, n) in stats]
            if not pts:
                continue
            xs = [n for n, _ in pts]
            ys = [s.gflops() for _, s in pts]
            lo = [s.gflops() - s.gflops(s.quartiles[1]) for _, s in pts]
            hi = [s.gflops(s.quartiles[0]) - s.gflops() for _, s in pts]
            st = series_style(arm)
            ax.errorbar(xs, ys, yerr=[lo, hi], label=label(arm), capsize=0, elinewidth=1.0, **st)
        top = peak(data, threads, mhz)
        if top:
            ax.axhline(top, color=INK['muted'], linewidth=1.0)
            ax.text(ns[0], top, f'  peak at the measured {mhz / 1000:.2f} GHz: {top:.0f} GFLOP/s',
                    color=INK['secondary'], fontsize=8, va='bottom')
            ax.set_ylim(0, top * 1.12)
        else:
            ax.set_ylim(bottom=0)
        log2_axis(ax, ns)
        style(ax, 'n (matrices are n × n doubles)', 'GFLOP/s', title)
        legend = ax.legend(loc='lower right', fontsize=8, frameon=False)
        for text in legend.get_texts():
            text.set_color(INK['primary'])
    fig.tight_layout()
    path = Path(f'{out_stem}-gflops.png')
    fig.savefig(path, dpi=150, facecolor=INK['surface'])
    plt.close(fig)
    written.append(path)

    pairs = [('One thread', 'eigen-native', ['numpy', 'eigen-repl', 'eigen-repl-aot', 'eigen-repl-default']),
             (f'{mt} threads', 'eigen-native-omp', [f'numpy@{mt}', 'eigen-repl-omp', 'eigen-repl-aot-omp'])]
    pairs = [p for p in pairs if any(ratio(stats, a, p[1], n) for a in p[2] for (_, n) in stats)]
    if not pairs:
        return written
    lo_lim, hi_lim = 0.5, 1.5
    fig, axes = plt.subplots(1, len(pairs), figsize=(6.4 * len(pairs), 4.0), squeeze=False)
    fig.patch.set_facecolor(INK['surface'])
    for ax, (title, base, arms) in zip(axes[0], pairs):
        ns = sorted({n for (a, n) in stats if a in arms + [base]})
        ax.axhspan(0.95, 1.05, color=INK['band'], linewidth=0)
        ax.axhline(1.0, color=INK['axis'], linewidth=1.0)
        below = []
        for arm in arms:
            pts = [(n, ratio(stats, arm, base, n)) for n in ns]
            pts = [(n, r) for n, r in pts if r]
            if not pts:
                continue
            st = series_style(arm)
            shown = [(n, r[0]) for n, r in pts if lo_lim <= r[0] <= hi_lim]
            if shown:
                ax.plot([n for n, _ in shown], [v for _, v in shown], label=label(arm), **st)
            off = [(n, r[0]) for n, r in pts if not lo_lim <= r[0] <= hi_lim]
            if off:
                worst = min(v for _, v in off)
                below.append(f'{label(arm)}: {worst:.3g}× to {max(v for _, v in off):.3g}× (off scale)')
                ax.plot([n for n, _ in off], [lo_lim if v < lo_lim else hi_lim for _, v in off],
                        linestyle='none', marker='v', color=st['color'], markersize=6,
                        markeredgecolor=INK['surface'], label=None if shown else label(arm))
        log2_axis(ax, ns)
        ax.set_ylim(lo_lim * 0.97, hi_lim)
        style(ax, 'n', f'speed relative to {label(base)}', f'{title}: ratio to native (band: ±5%)')
        if below:
            ax.text(0.01, 0.02, '\n'.join(below), transform=ax.transAxes, fontsize=7.5,
                    color=INK['secondary'], va='bottom')
        legend = ax.legend(loc='upper right', fontsize=8, frameon=False)
        for text in legend.get_texts():
            text.set_color(INK['primary'])
    fig.tight_layout()
    path = Path(f'{out_stem}-ratio.png')
    fig.savefig(path, dpi=150, facecolor=INK['surface'])
    plt.close(fig)
    written.append(path)
    return written


def main():
    p = argparse.ArgumentParser(prog='bench/report.py', description='Tables and plots from a bench/run.py result.')
    p.add_argument('result', type=Path, help='a JSON file written by bench/run.py')
    p.add_argument('--no-plots', action='store_true')
    args = p.parse_args()

    data, stats, failures, skips = load(args.result)
    latency = latency_rows(data)
    verdicts = hypotheses(data, stats, failures, latency)
    stem = args.result.with_suffix('')
    md = markdown(data, stats, failures, skips, verdicts, latency, stem.name)
    Path(f'{stem}.md').write_text(md + '\n')
    write_csv(f'{stem}.csv', data)
    written = [Path(f'{stem}.md'), Path(f'{stem}.csv')]
    if not args.no_plots:
        written += plots(data, stats, stem)
    for path in written:
        print(f'report: wrote {path}')
    for h, _, verdict, detail in verdicts:
        print(f'report: {h} {verdict}: {detail}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
