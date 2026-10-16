#!/usr/bin/env python3
"""Run the eval cases headless, as `claude plugin eval` runs them, without its
sandbox, which needs user namespaces that AppArmor blocks on Ubuntu 24.04
(README, "The sandbox").

    evals/headless.py --case run-snippet --case yaml-cpp
    evals/headless.py                          # every case
    evals/headless.py --no-plugin --case opencv   # the baseline arm

For each case:
1. the plugin is a `git archive HEAD` of this checkout, so that uncommitted
   edits stay out of the run;
2. that copy's evals/<case>/scaffold.sh seeds a temporary workspace;
3. `claude -p` runs prompt.md's prompt there, with the plugin loaded from the
   copy (and an installed cpp-repl plugin turned off), the case's tools and
   timeout, and --budget dollars at most;
4. the computed graders score the run: tool_used against the tool calls,
   regex (contains or not_contains) and file_exists against the workspace,
   and a regex with no target against the final reply. An llm grader is
   printed with the reply, for a judgement by hand.

No run starts once the runs so far cost --total dollars. Results go to
evals/results/headless-<time>.json, which git ignores. PyYAML reads the
cases.
"""

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

CHECKOUT = Path(__file__).resolve().parent.parent


def front_matter(path):
    """(the YAML between the --- lines at the top, the text after them)."""
    text = path.read_text()
    m = re.match(r'^---\n(.*?)\n---\n?(.*)$', text, re.S)
    return (yaml.safe_load(m.group(1)) or {}, m.group(2).strip()) if m else ({}, text.strip())


def snapshot(into):
    """HEAD of this checkout, unpacked into a directory."""
    archive = subprocess.run(['git', '-C', str(CHECKOUT), 'archive', 'HEAD'], capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', str(into)], input=archive.stdout, check=True)
    return subprocess.run(['git', '-C', str(CHECKOUT), 'rev-parse', '--short', 'HEAD'], capture_output=True,
                          text=True, check=True).stdout.strip()


def run_case(case, plugin_dir, args):
    meta, prompt = front_matter(case / 'prompt.md')
    workspace = Path(tempfile.mkdtemp(prefix=f'eval-{case.name}-'))
    scaffold = subprocess.run(['bash', str(case / 'scaffold.sh')], cwd=workspace, capture_output=True, text=True)
    if scaffold.returncode != 0:
        return {'case': case.name, 'error': f'scaffold.sh failed: {scaffold.stderr.strip()[-500:]}'}
    cmd = ['claude', '-p', '--model', args.model, '--output-format', 'stream-json', '--verbose',
           '--max-budget-usd', str(args.budget), '--no-session-persistence', '--permission-prompts', 'none',
           '--settings', json.dumps({'enabledPlugins': {'cpp-repl@cpp-repl': False}})]
    if plugin_dir:
        cmd += ['--plugin-dir', str(plugin_dir)]
    cmd += ['--allowedTools', *meta.get('allowed_tools', ['Read', 'Glob', 'Grep', 'Skill', 'Bash'])]
    start = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=workspace, input=prompt, capture_output=True, text=True,
                           timeout=meta.get('timeout_seconds', 1800))
        stream, stderr = p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        stream = e.stdout.decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
        stderr = 'timed out'
    events = []
    for line in stream.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    init = next((e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init'), {})
    result = next((e for e in reversed(events) if e.get('type') == 'result'), {})
    calls = [block for e in events if e.get('type') == 'assistant'
             for block in e.get('message', {}).get('content', []) if block.get('type') == 'tool_use']
    reply = result.get('result', '')
    graded = [grade(g, calls, workspace, reply) for g in sorted((case / 'graders').glob('*.md'))]
    computed = [g for g in graded if g['passed'] is not None]
    weight = sum(g['weight'] for g in computed)
    plugins = [p.get('name') for p in init.get('plugins', [])]
    out = {'case': case.name, 'description': meta.get('description'), 'workspace': str(workspace),
           'cost_usd': result.get('total_cost_usd'), 'turns': result.get('num_turns'),
           'seconds': round(time.monotonic() - start, 1), 'plugins': plugins,
           'plugin_errors': init.get('plugin_errors'), 'skills_fired': [c['input'].get('skill') for c in calls
                                                                       if c.get('name') == 'Skill'],
           'commands': [c['input'].get('command') for c in calls if c.get('name') == 'Bash'],
           'permission_denials': result.get('permission_denials'), 'graders': graded,
           'score': round(sum(g['weight'] for g in computed if g['passed']) / weight, 2) if weight else None,
           'reply': reply}
    if plugin_dir and plugins.count('cpp-repl') != 1:
        out['error'] = f'the cpp-repl plugin loaded {plugins.count("cpp-repl")} times: {plugins}'
    if not result:
        out['error'] = f'no result from claude: {stderr.strip()[-500:]}'
    if not args.keep:
        shutil.rmtree(workspace, ignore_errors=True)
    return out


def grade(path, calls, workspace, reply):
    """One grader's verdict: passed is True or False, or None for an llm
    grader, which is printed for a judgement by hand."""
    meta, body = front_matter(path)
    kind, weight = meta.get('type'), meta.get('weight', 1)
    verdict = {'grader': path.stem, 'type': kind, 'weight': weight, 'passed': None}
    if kind == 'tool_used':
        pattern = re.compile(meta.get('input_match', ''))
        n = sum(1 for c in calls if c.get('name') == meta.get('tool')
                and pattern.search(json.dumps(c.get('input', {}))))
        low, high = meta.get('min', 1), meta.get('max')
        verdict.update(passed=n >= low and (high is None or n <= high), count=n)
    elif kind == 'regex':
        target = meta.get('target') or {}
        if target.get('source') == 'file':
            file = workspace / target['path']
            text = file.read_text(errors='replace') if file.is_file() else None
        else:
            text = reply
        found = text is not None and re.search(meta['pattern'], text, re.M) is not None
        verdict['passed'] = (not found and text is not None) if meta.get('match') == 'not_contains' else found
    elif kind == 'file_exists':
        found = any(fnmatch.fnmatch(str(p.relative_to(workspace)), meta['path']) for p in workspace.rglob('*'))
        verdict['passed'] = found == meta.get('exists', True)
    else:
        verdict['judge'] = body
    return verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--case', action='append', default=[], help='a case to run (default: all); repeatable')
    parser.add_argument('--model', default='claude-sonnet-5-5')
    parser.add_argument('--budget', type=float, default=0.6, help='dollars a run may spend (default 0.6)')
    parser.add_argument('--total', type=float, default=5.0, help='no run starts past this many dollars (default 5)')
    parser.add_argument('--no-plugin', action='store_true', help='the baseline arm: no plugin loaded')
    parser.add_argument('--keep', action='store_true', help='keep the workspaces')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cpp-repl-plugin-') as tmp:
        plugin = Path(tmp)
        commit = snapshot(plugin)
        cases = sorted(d for d in (plugin / 'evals').iterdir() if (d / 'prompt.md').is_file())
        if args.case:
            cases = [c for c in cases if c.name in args.case]
            missing = set(args.case) - {c.name for c in cases}
            if missing:
                sys.exit(f'headless.py: no case {", ".join(sorted(missing))} at HEAD')
        results, spent = [], 0.0
        for case in cases:
            if spent >= args.total:
                results.append({'case': case.name, 'error': f'not run: ${spent:.2f} spent of ${args.total:.2f}'})
                continue
            r = run_case(case, None if args.no_plugin else plugin, args)
            spent += r.get('cost_usd') or 0
            results.append(r)
            print(f"{r['case']}: score {r.get('score')}, ${r.get('cost_usd') or 0:.2f}, {r.get('seconds')} s, "
                  f"skills {r.get('skills_fired')}{', ' + r['error'] if r.get('error') else ''}", flush=True)
            for g in r.get('graders', []):
                mark = {True: 'pass', False: 'FAIL', None: 'judge'}[g['passed']]
                print(f"  {mark:5} {g['grader']}" + (f" (count {g['count']})" if 'count' in g else ''))
    out = CHECKOUT / 'evals' / 'results' / f'headless-{time.strftime("%Y-%m-%dT%H-%M-%S")}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'commit': commit, 'model': args.model, 'plugin': not args.no_plugin,
                               'spent_usd': round(spent, 2), 'results': results}, indent=2) + '\n')
    print(f'${spent:.2f} in all; results in {out}')
    return 0 if all(r.get('score') == 1 and not r.get('error') for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
