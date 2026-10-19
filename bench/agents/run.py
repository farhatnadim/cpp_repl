#!/usr/bin/env python3
"""Run a tier of the agents benchmark through Harbor: Claude Code solving C++
tasks with cpp-repl as an MCP tool (mcp), as a skill (skill), or unmentioned
(control).

    bench/agents/run.py --tier 0                   # oracle and nop, then one trial per arm
    bench/agents/run.py --tier 1                   # the two Terminal-Bench 4.0 C++ tasks, 10 attempts per arm
    bench/agents/run.py --tier 1 --arm mcp         # one arm of it
    bench/agents/run.py --tier 1 --dry-run         # the job configurations and commands, nothing run
    bench/agents/run.py --tier 0 --install-only    # each arm's container set up and checked, no agent run

A tier is one or more Harbor jobs. A job holds its arms as Harbor agents, and
Harbor orders trials attempt by task by agent, so the arms run interleaved,
in the same hours, on the same machine. Each job lands in
bench/agents/jobs/NAME/ (ignored by git), beside NAME.config.json, the
configuration it ran, and NAME.run.json, what it ran with: the wheel and its
sha256, the commit, Harbor's and Claude Code's versions. report.py reads them.

Agent arms need a credential for Claude Code inside the containers:
ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
JOBS = HERE / 'jobs'
DIST = HERE / 'dist'
AGENT = 'harbor_agent:ClaudeCodeCppRepl'   # Harbor imports it with bench/agents on PYTHONPATH
MCP_CONFIG = HERE / 'cpp-repl.mcp.json'
SKILL = REPO / 'skills' / 'run-cpp'
HARBOR_VERSION = '0.24.0'          # a change of either version is a new campaign
CLAUDE_CODE_VERSION = '2.1.303'
MODEL = 'anthropic/claude-sonnet-5-5'
TB4 = 'terminal-bench/terminal-bench@4.0.0'
TB4_CPP = ('terminal-bench/ks-solver-cpp', 'terminal-bench/mvcc-lsm-compaction')
CONCURRENT = 4   # ks-solver-cpp takes 4 CPUs and 8 GB: four at once fit 32 cores and 62 GB with room

ARMS = {
    'oracle': "the task's reference solution: the task works on this machine (reward 1)",
    'nop': 'nothing at all: the verifier fails a task left as it is (reward 0)',
    'control': 'Claude Code; cpp-repl installed off PATH and never mentioned',
    'mcp': "Claude Code with cpp-repl --mcp, through Harbor's MCP configuration",
    'skill': 'Claude Code with the run-cpp skill, and cpp-repl on PATH',
}
AGENT_ARMS = ('control', 'mcp', 'skill')


@dataclass(frozen=True)
class Job:
    name: str
    dataset: str          # NAME@VERSION
    tasks: tuple          # task names, or glob patterns
    arms: tuple
    attempts: int = 1
    effort: str | None = None     # Claude Code's --effort; None leaves its default
    budget: float | None = None   # dollars a trial may spend: --max-budget-usd, the same in every arm


TIERS = {
    '0': (Job('sanity', TB4, TB4_CPP, ('oracle', 'nop')),
          Job('smoke', TB4, TB4_CPP[:1], AGENT_ARMS, effort='low', budget=5)),
    '1': (Job('tb4-cpp', TB4, TB4_CPP, AGENT_ARMS, attempts=10, budget=25),),
    '2': (Job('polyglot-cpp', 'aider-polyglot@1.0', ('polyglot_cpp_*',), AGENT_ARMS, attempts=3, budget=5),
          Job('tb2-cpp', 'terminal-bench@2.0',
              ('custom-memory-heap-crash', 'fix-code-vulnerability', 'polyglot-rust-c'), AGENT_ARMS,
              attempts=3, budget=25)),
}


def mcp_servers():
    """cpp-repl.mcp.json's servers, as a Harbor agent configuration holds them."""
    servers = json.loads(MCP_CONFIG.read_text())['mcpServers']
    return [{'name': name, 'transport': s.get('type', 'stdio'), 'command': s['command'], 'args': s.get('args', [])}
            for name, s in servers.items()]


def agent_config(arm, job, model, wheel):
    if arm in ('oracle', 'nop'):
        return {'name': arm}
    kwargs = {'version': CLAUDE_CODE_VERSION, 'cpp_repl_wheel': str(wheel)}
    if job.effort:
        kwargs['reasoning_effort'] = job.effort
    if job.budget:
        kwargs['max_budget_usd'] = str(job.budget)
    config = {'import_path': AGENT, 'model_name': model, 'kwargs': kwargs}
    if arm == 'mcp':
        config['mcp_servers'] = mcp_servers()
    elif arm == 'skill':
        config['skills'] = [str(SKILL)]
        kwargs['expose_cpp_repl'] = True
    return config


def dataset_config(dataset, tasks):
    name, _, version = dataset.partition('@')
    if '/' in name:   # a Harbor Hub package, by ref
        return {'name': name, 'ref': version, 'task_names': list(tasks)}
    return {'name': name, 'version': version, 'task_names': list(tasks)}


def job_config(job, arms, model, wheel, name, jobs_dir=JOBS, concurrent=CONCURRENT, install_only=False):
    return {
        'job_name': name,
        'install_only': install_only,
        'jobs_dir': str(jobs_dir),
        'n_attempts': job.attempts,
        'n_concurrent_trials': concurrent,
        # Docker enforces limits and reserves nothing: the concurrency keeps
        # the limits within the machine, so that each trial gets them.
        'environment': {'type': 'docker', 'cpu_enforcement_policy': 'limit', 'memory_enforcement_policy': 'limit'},
        'agents': [agent_config(arm, job, model, wheel) for arm in arms],
        'datasets': [dataset_config(job.dataset, job.tasks)],
    }


def wheel_problem(wheel, arms):
    """Why the wheel cannot serve these arms, or None."""
    if not any(arm in AGENT_ARMS for arm in arms):
        return None
    if wheel is None or not Path(wheel).is_file():
        return (f'no cpp-repl wheel: build one into {DIST.relative_to(REPO)} with '
                f'`uv build --wheel --out-dir {DIST.relative_to(REPO)}`, or pass --wheel')
    if 'mcp' in arms:
        with zipfile.ZipFile(wheel) as z:
            if 'cpp_repl/mcp.py' not in z.namelist():
                return f'{wheel} has no cpp-repl --mcp: build the wheel from a checkout that has it'
    return None


def newest_wheel():
    wheels = sorted(DIST.glob('cpp_repl-*.whl'), key=lambda p: p.stat().st_mtime)
    return wheels[-1] if wheels else None


def provenance(job, arms, model, wheel, config_path):
    run = {'job': job.name, 'arms': list(arms), 'model': model, 'dataset': job.dataset, 'tasks': list(job.tasks),
           'attempts': job.attempts, 'effort': job.effort, 'budget_usd': job.budget,
           'harbor': HARBOR_VERSION, 'claude_code': CLAUDE_CODE_VERSION, 'config': config_path.name}
    commit = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True)
    run['commit'] = commit.stdout.strip() or None
    if wheel is not None and any(arm in AGENT_ARMS for arm in arms):
        run['wheel'] = Path(wheel).name
        run['wheel_sha256'] = hashlib.sha256(Path(wheel).read_bytes()).hexdigest()
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tier', required=True, choices=sorted(TIERS))
    parser.add_argument('--arm', action='append', choices=sorted(ARMS), help='run only these arms; repeatable')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--wheel', type=Path, help=f'the cpp-repl wheel (default: the newest in {DIST})')
    parser.add_argument('--concurrent', type=int, default=CONCURRENT, help='trials at once (default 4)')
    parser.add_argument('--attempts', type=int, help="attempts per task and arm, in place of the tier's")
    parser.add_argument('--jobs-dir', type=Path, default=JOBS)
    parser.add_argument('--dry-run', action='store_true', help='print the configurations and commands')
    parser.add_argument('--install-only', action='store_true',
                        help="set up each arm's container and run its checks, then stop: no model call, no verifier")
    args = parser.parse_args(argv)

    wheel = (args.wheel or newest_wheel())
    wheel = wheel.resolve() if wheel else None
    stamp = datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')
    plans = []
    for job in TIERS[args.tier]:
        arms = tuple(a for a in job.arms if not args.arm or a in args.arm)
        if not arms:
            continue
        if args.attempts:
            job = Job(**{**job.__dict__, 'attempts': args.attempts})
        name = f't{args.tier}-{job.name}{"-install" if args.install_only else ""}_{args.model.split("/")[-1]}_{stamp}'
        plans.append((job, arms, name))
    if not plans:
        parser.error(f'tier {args.tier} has none of the arms {args.arm}')

    problems = {wheel_problem(wheel, arms) for _, arms, _ in plans} - {None}
    if problems and not args.dry_run:
        for p in sorted(problems):
            sys.stderr.write(f'run.py: {p}\n')
        return 2
    needs_model = any(a in AGENT_ARMS for _, arms, _ in plans for a in arms)
    if needs_model and not (args.dry_run or args.install_only) and not (os.environ.get('ANTHROPIC_API_KEY')
                                                   or os.environ.get('CLAUDE_CODE_OAUTH_TOKEN')):
        sys.stderr.write('run.py: the agent arms need a credential for Claude Code in the containers: '
                         'ANTHROPIC_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`\n')
        return 2
    if not args.dry_run and shutil.which('harbor') is None:
        sys.stderr.write(f'run.py: no harbor on PATH: uv tool install harbor=={HARBOR_VERSION}\n')
        return 2

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(HERE), os.environ.get('PYTHONPATH')])))
    status = 0
    args.jobs_dir.mkdir(parents=True, exist_ok=True)
    for job, arms, name in plans:
        config = job_config(job, arms, args.model, wheel, name, args.jobs_dir.resolve(), args.concurrent,
                            args.install_only)
        config_path = args.jobs_dir / f'{name}.config.json'
        command = ['harbor', 'run', '-c', str(config_path), '-q']
        if args.dry_run:
            print(f'# {name}: {", ".join(arms)}; {job.attempts} attempt(s) of {len(job.tasks)} task(s)')
            print(json.dumps(config, indent=2))
            print('PYTHONPATH=' + str(HERE), ' '.join(command), '\n')
            continue
        config_path.write_text(json.dumps(config, indent=2) + '\n')
        (args.jobs_dir / f'{name}.run.json').write_text(
            json.dumps(provenance(job, arms, args.model, wheel, config_path), indent=2) + '\n')
        print(f'run.py: {name}: {", ".join(arms)}', flush=True)
        done = subprocess.run(command, env=env)
        status = status or done.returncode
    return status


if __name__ == '__main__':
    sys.exit(main())
