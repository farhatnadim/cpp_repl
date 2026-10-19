#!/usr/bin/env python3
"""The agents benchmark's report: for each task and arm, how often Claude Code
solved the task, at what cost and in what time, and which tools it ran.

    bench/agents/report.py                          # every job under bench/agents/jobs
    bench/agents/report.py JOB_DIR ...              # these jobs
    bench/agents/report.py --out bench/agents/results/NAME   # NAME.md and NAME.csv

A trial is a directory of a Harbor job holding result.json. Its arm comes
from the agent it ran: oracle and nop by name, mcp when the agent had MCP
servers, skill when it had skills, control otherwise. A trial solved the
task when its reward is 1; a trial that raised counts as unsolved, as
Terminal-Bench's leaderboard counts it, and is listed. The tools come from
the agent's stream (agent/claude-code.txt): calls to the cpp-repl MCP tools,
Bash commands that run cpp-repl, and Bash commands that compile or build
(g++, clang++, c++, gcc, clang, make, cmake).
"""

import argparse
import csv
import io
import json
import math
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOBS = HERE / 'jobs'
ARM_ORDER = ('oracle', 'nop', 'control', 'mcp', 'skill')
CPP_REPL = re.compile(r'(^|[\s/;&|(])cpp-repl\b')
BUILD = re.compile(r'(^|[\s/;&|(])(g\+\+|clang\+\+|c\+\+|gcc|clang|make|cmake)(-\d+)?(\s|$)')


# -- statistics ------------------------------------------------------------------

def wilson(k, n, z=1.96):
    """The Wilson score interval of k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe(k1, n1, k2, n2):
    """p1 - p2, and Newcombe's hybrid score interval for it."""
    p1, p2 = k1 / n1, k2 / n2
    (l1, u1), (l2, u2) = wilson(k1, n1), wilson(k2, n2)
    d = p1 - p2
    return d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def fisher(k1, n1, k2, n2):
    """The two-sided p-value of Fisher's exact test for k1 of n1 against k2 of n2."""
    total, n = k1 + k2, n1 + n2

    def p(x):
        return math.comb(total, x) * math.comb(n - total, n1 - x) / math.comb(n, n1)

    observed = p(k1)
    lo, hi = max(0, n1 - (n - total)), min(total, n1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= observed * (1 + 1e-9)))


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


# -- reading trials ----------------------------------------------------------------

def arm_of(agent):
    """The arm of a trial, from the agent configuration it ran."""
    name = agent.get('name') or ''
    if name in ('oracle', 'nop'):
        return name
    if agent.get('mcp_servers'):
        return 'mcp'
    if agent.get('skills'):
        return 'skill'
    return 'control'


def seconds(timing):
    if not timing or not timing.get('started_at') or not timing.get('finished_at'):
        return None
    start, end = (datetime.fromisoformat(timing[k].replace('Z', '+00:00')) for k in ('started_at', 'finished_at'))
    return (end - start).total_seconds()


def tools(stream):
    """What the agent ran, from its stream-json log."""
    counts = {'mcp_calls': 0, 'repl_commands': 0, 'build_commands': 0, 'skill_runs': 0}
    stop = turns = None
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'result':
            stop, turns = event.get('subtype'), event.get('num_turns')
        if event.get('type') != 'assistant':
            continue
        for block in (event.get('message') or {}).get('content') or []:
            if not isinstance(block, dict) or block.get('type') != 'tool_use':
                continue
            name, given = block.get('name', ''), block.get('input') or {}
            if name.startswith('mcp__cpp-repl__') or name.startswith('mcp__plugin_cpp-repl_cpp-repl__'):
                counts['mcp_calls'] += 1
            elif name == 'Bash':
                command = str(given.get('command', ''))
                counts['repl_commands'] += bool(CPP_REPL.search(command))
                counts['build_commands'] += bool(BUILD.search(command))
            elif name == 'Skill' and 'run-cpp' in str(given.get('skill', '')):
                counts['skill_runs'] += 1
    return counts, stop, turns


def read_trial(directory):
    """One trial's row, or None when the directory holds no trial."""
    path = directory / 'result.json'
    if not path.is_file():
        return None
    r = json.loads(path.read_text())
    if 'task_name' not in r:
        return None
    rewards = (r.get('verifier_result') or {}).get('rewards') or {}
    reward = rewards.get('reward')
    agent = (r.get('config') or {}).get('agent') or {}
    context = r.get('agent_result') or {}
    exception = r.get('exception_info') or {}
    stream = directory / 'agent' / 'claude-code.txt'
    counts, stop, turns = tools(stream.read_text(errors='replace')) if stream.is_file() else ({}, None, None)
    return {
        'job': directory.parent.name, 'trial': directory.name, 'task': r['task_name'], 'arm': arm_of(agent),
        'reward': reward, 'solved': reward is not None and float(reward) >= 1.0,
        'error': exception.get('exception_type'),
        'cost_usd': context.get('cost_usd'), 'input_tokens': context.get('n_input_tokens'),
        'cache_tokens': context.get('n_cache_tokens'), 'output_tokens': context.get('n_output_tokens'),
        'agent_seconds': seconds(r.get('agent_execution')), 'turns': turns, 'stop': stop, **counts,
    }


def read_jobs(directories):
    rows = []
    for job in directories:
        for trial in sorted(p for p in job.iterdir() if p.is_dir()):
            row = read_trial(trial)
            if row:
                rows.append(row)
    return rows


# -- the report ----------------------------------------------------------------------

def summary(rows):
    """One line per task and arm, then one per arm over every task."""
    groups = {}
    for row in rows:
        groups.setdefault((row['task'], row['arm']), []).append(row)
        groups.setdefault(('all tasks', row['arm']), []).append(row)
    lines = []
    tasks = sorted({row['task'] for row in rows}) + (['all tasks'] if len({row['task'] for row in rows}) > 1 else [])
    for task in tasks:
        for arm in sorted({a for t, a in groups if t == task}, key=lambda a: (ARM_ORDER + (a,)).index(a)):
            group = groups[(task, arm)]
            n, k = len(group), sum(row['solved'] for row in group)
            low, high = wilson(k, n)
            lines.append({
                'task': task, 'arm': arm, 'trials': n, 'solved': k, 'rate': k / n, 'low': low, 'high': high,
                'errored': sum(bool(row['error']) for row in group),
                'budget_stops': sum(row['stop'] == 'error_max_budget_usd' for row in group),
                'cost_usd': median(row['cost_usd'] for row in group),
                'agent_minutes': (lambda s: s / 60 if s is not None else None)(
                    median(row['agent_seconds'] for row in group)),
                'input_tokens': median(row['input_tokens'] for row in group),
                'output_tokens': median(row['output_tokens'] for row in group),
                'mcp_calls': median(row.get('mcp_calls') for row in group),
                'repl_commands': median(row.get('repl_commands') for row in group),
                'build_commands': median(row.get('build_commands') for row in group),
            })
    return lines


def comparisons(rows):
    """Each treatment against control, per task and over every task."""
    out = []
    for task in sorted({row['task'] for row in rows}) + ['all tasks']:
        group = [row for row in rows if task == 'all tasks' or row['task'] == task]
        control = [row for row in group if row['arm'] == 'control']
        for arm in ('mcp', 'skill'):
            treated = [row for row in group if row['arm'] == arm]
            if not control or not treated:
                continue
            k1, n1, k2, n2 = sum(r['solved'] for r in treated), len(treated), sum(r['solved'] for r in control), len(control)
            d, low, high = newcombe(k1, n1, k2, n2)
            out.append({'task': task, 'arm': arm, 'difference': d, 'low': low, 'high': high,
                        'p': fisher(k1, n1, k2, n2), 'treated': f'{k1}/{n1}', 'control': f'{k2}/{n2}'})
    return out


def _f(value, digits=2):
    if value is None:
        return '–'
    return f'{value:.{digits}f}' if isinstance(value, float) and not float(value).is_integer() else f'{value:g}'


def markdown(rows, sources):
    lines = ['# bench/agents report', '', f'Jobs: {", ".join(sources)}.', '',
             'Solved: reward 1, with a 95% Wilson interval. Errored trials count as unsolved. '
             'Cost, minutes, tokens and tool counts are medians per trial; minutes are the agent\'s.', '',
             '| Task | Arm | Trials | Solved | Rate [95% CI] | Errored | Budget stops | Cost $ | Minutes | '
             'Input tok | Output tok | MCP calls | cpp-repl cmds | Build cmds |',
             '|---|---|--:|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|']
    for s in summary(rows):
        lines.append(f"| {s['task']} | {s['arm']} | {s['trials']} | {s['solved']} | "
                     f"{s['rate']:.0%} [{s['low']:.0%}, {s['high']:.0%}] | {s['errored']} | {s['budget_stops']} | "
                     f"{_f(s['cost_usd'])} | {_f(s['agent_minutes'], 1)} | {_f(s['input_tokens'])} | "
                     f"{_f(s['output_tokens'])} | {_f(s['mcp_calls'])} | {_f(s['repl_commands'])} | "
                     f"{_f(s['build_commands'])} |")
    compared = comparisons(rows)
    if compared:
        lines += ['', '## Against control', '',
                  'The difference in solve rate, treatment minus control, with Newcombe\'s 95% interval, and '
                  'the two-sided p-value of Fisher\'s exact test.', '',
                  '| Task | Arm | Treated | Control | Difference [95% CI] | p |', '|---|---|--:|--:|---|--:|']
        for c in compared:
            lines.append(f"| {c['task']} | {c['arm']} | {c['treated']} | {c['control']} | "
                         f"{c['difference']:+.0%} [{c['low']:+.0%}, {c['high']:+.0%}] | {c['p']:.3f} |")
    errored = [row for row in rows if row['error']]
    if errored:
        lines += ['', '## Errored trials', '', '| Trial | Task | Arm | Exception |', '|---|---|---|---|']
        lines += [f"| {r['job']}/{r['trial']} | {r['task']} | {r['arm']} | {r['error']} |" for r in errored]
    return '\n'.join(lines) + '\n'


def table(rows):
    buffer = io.StringIO()
    fields = ['job', 'trial', 'task', 'arm', 'reward', 'solved', 'error', 'cost_usd', 'input_tokens', 'cache_tokens',
              'output_tokens', 'agent_seconds', 'turns', 'stop', 'mcp_calls', 'repl_commands', 'build_commands',
              'skill_runs']
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('jobs', nargs='*', type=Path, help=f'job directories (default: every one in {JOBS})')
    parser.add_argument('--out', type=Path, help='write OUT.md and OUT.csv instead of printing the table')
    args = parser.parse_args(argv)
    jobs = args.jobs or sorted(p for p in JOBS.iterdir() if p.is_dir()) if (args.jobs or JOBS.is_dir()) else []
    rows = read_jobs(jobs)
    if not rows:
        sys.stderr.write('report.py: no trials found\n')
        return 1
    text = markdown(rows, [job.name for job in jobs])
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix('.md').write_text(text)
        args.out.with_suffix('.csv').write_text(table(rows))
        print(f'report.py: {args.out.with_suffix(".md")} and {args.out.with_suffix(".csv")}')
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
