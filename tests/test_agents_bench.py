"""bench/agents: the job configurations run.py hands Harbor, and the report
report.py makes of Harbor's trials. Neither Harbor nor Docker runs here."""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parent.parent / 'bench' / 'agents'


def load(name):
    """bench/agents/NAME.py, under a name of its own: bench/ has a run.py and a report.py too."""
    spec = importlib.util.spec_from_file_location(f'bench_agents_{name}', AGENTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = load('run')
report = load('report')


def wheel(path, mcp=True):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('cpp_repl/__init__.py', "__version__ = '0'\n")
        if mcp:
            z.writestr('cpp_repl/mcp.py', '')
    return path


def test_tier_1_runs_the_three_arms_in_one_job(tmp_path):
    """One job, so that Harbor interleaves the arms' trials; the arms differ
    only in the MCP server, the skill and the link onto PATH."""
    job, = run.TIERS['1']
    config = run.job_config(job, job.arms, run.MODEL, tmp_path / 'w.whl', 'name', tmp_path, 4)
    assert config['n_attempts'] == 10 and config['n_concurrent_trials'] == 4 and config['job_name'] == 'name'
    assert config['environment'] == {'type': 'docker', 'cpu_enforcement_policy': 'limit',
                                     'memory_enforcement_policy': 'limit'}
    assert config['datasets'] == [{'name': 'terminal-bench/terminal-bench', 'ref': '4.0.0',
                                   'task_names': ['terminal-bench/ks-solver-cpp', 'terminal-bench/mvcc-lsm-compaction']}]
    control, mcp, skill = config['agents']
    for agent in config['agents']:
        assert agent['import_path'] == 'harbor_agent:ClaudeCodeCppRepl' and agent['model_name'] == run.MODEL
        assert agent['kwargs']['version'] == run.CLAUDE_CODE_VERSION and agent['kwargs']['max_budget_usd'] == '25'
        assert agent['kwargs']['cpp_repl_wheel'] == str(tmp_path / 'w.whl') and 'reasoning_effort' not in agent['kwargs']
    assert 'mcp_servers' not in control and 'skills' not in control and 'expose_cpp_repl' not in control['kwargs']
    assert mcp['mcp_servers'] == [{'name': 'cpp-repl', 'transport': 'stdio', 'command': '/opt/cpp-repl/bin/cpp-repl',
                                   'args': ['--mcp']}]
    assert skill['skills'] == [str(run.SKILL)] and skill['kwargs']['expose_cpp_repl'] is True
    assert (run.SKILL / 'SKILL.md').is_file()
    assert [report.arm_of(a) for a in config['agents']] == ['control', 'mcp', 'skill']


def test_tier_0_checks_the_tasks_then_each_arm_once(tmp_path):
    sanity, smoke = run.TIERS['0']
    config = run.job_config(sanity, sanity.arms, run.MODEL, None, 'n', tmp_path)
    assert config['agents'] == [{'name': 'oracle'}, {'name': 'nop'}]
    assert [report.arm_of(a) for a in config['agents']] == ['oracle', 'nop']
    assert len(config['datasets'][0]['task_names']) == 2
    config = run.job_config(smoke, smoke.arms, run.MODEL, tmp_path / 'w.whl', 'n', tmp_path)
    assert config['datasets'][0]['task_names'] == ['terminal-bench/ks-solver-cpp'] and config['n_attempts'] == 1
    assert {a['kwargs']['reasoning_effort'] for a in config['agents']} == {'low'}
    assert {a['kwargs']['max_budget_usd'] for a in config['agents']} == {'5'}


def test_tier_2_names_registry_datasets_by_version(tmp_path):
    polyglot, tb2 = run.TIERS['2']
    config = run.job_config(polyglot, polyglot.arms, run.MODEL, tmp_path / 'w.whl', 'n', tmp_path)
    assert config['datasets'] == [{'name': 'aider-polyglot', 'version': '1.0', 'task_names': ['polyglot_cpp_*']}]
    assert run.job_config(tb2, tb2.arms, run.MODEL, None, 'n', tmp_path)['datasets'][0]['version'] == '2.0'


def test_the_wheel_must_serve_the_arms(tmp_path):
    assert run.wheel_problem(None, ('oracle', 'nop')) is None
    assert 'no cpp-repl wheel' in run.wheel_problem(None, ('control',))
    assert run.wheel_problem(wheel(tmp_path / 'a.whl'), ('control', 'mcp')) is None
    old = wheel(tmp_path / 'b.whl', mcp=False)
    assert run.wheel_problem(old, ('control', 'skill')) is None
    assert 'has no cpp-repl --mcp' in run.wheel_problem(old, ('mcp',))


def test_a_dry_run_prints_the_chosen_arms_and_writes_nothing(tmp_path, capsys):
    w = wheel(tmp_path / 'cpp_repl-0-py3-none-any.whl')
    assert run.main(['--tier', '1', '--arm', 'mcp', '--wheel', str(w), '--attempts', '2', '--dry-run',
                     '--jobs-dir', str(tmp_path / 'jobs')]) == 0
    out = capsys.readouterr().out
    first, rest = out.split('\n', 1)
    assert first.startswith('# t1-tb4-cpp_claude-sonnet-5-5_') and first.endswith(': mcp; 2 attempt(s) of 2 task(s)')
    config = json.loads(rest[:rest.index('\n}\n') + 2])
    assert config['n_attempts'] == 2 and [report.arm_of(a) for a in config['agents']] == ['mcp']
    assert 'harbor run -c ' in out and not any((tmp_path / 'jobs').glob('*.json'))


def test_agent_arms_need_a_credential(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN', raising=False)
    w = wheel(tmp_path / 'cpp_repl-0-py3-none-any.whl')
    assert run.main(['--tier', '1', '--wheel', str(w), '--jobs-dir', str(tmp_path / 'jobs')]) == 2
    assert 'need a credential' in capsys.readouterr().err


def test_the_statistics():
    low, high = report.wilson(5, 10)
    assert round(low, 4) == 0.2366 and round(high, 4) == 0.7634
    assert report.wilson(0, 0) == (0.0, 1.0)
    assert report.fisher(5, 10, 5, 10) == pytest.approx(1.0)
    assert report.fisher(10, 10, 0, 10) == pytest.approx(2 / 184756)
    d, low, high = report.newcombe(7, 10, 3, 10)
    assert d == pytest.approx(0.4) and -0.1 < low < 0.4 < high < 0.8


def trial(job, name, task, agent, reward, cost=1.0, error=None, events=()):
    directory = job / name
    (directory / 'agent').mkdir(parents=True)
    (directory / 'result.json').write_text(json.dumps({
        'task_name': task, 'trial_name': name, 'config': {'agent': agent},
        'verifier_result': None if reward is None else {'rewards': {'reward': reward}},
        'agent_result': {'cost_usd': cost, 'n_input_tokens': 1000, 'n_cache_tokens': 800, 'n_output_tokens': 100},
        'exception_info': {'exception_type': error, 'exception_message': ''} if error else None,
        'agent_execution': {'started_at': '2026-10-19T10:00:00Z', 'finished_at': '2026-10-19T10:30:00Z'},
    }))
    (directory / 'agent' / 'claude-code.txt').write_text(''.join(json.dumps(e) + '\n' for e in events) + 'not json\n')


def tool_use(name, **given):
    return {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': name, 'input': given}]}}


def test_the_report_reads_harbors_trials(tmp_path):
    job = tmp_path / 't1-tb4-cpp_model_stamp'
    agent = {'import_path': 'harbor_agent:ClaudeCodeCppRepl', 'mcp_servers': [], 'skills': []}
    mcp_agent = dict(agent, mcp_servers=[{'name': 'cpp-repl'}])
    skill_agent = dict(agent, skills=['/x/run-cpp'])
    build = tool_use('Bash', command='cd /app && g++ -O2 -std=c++17 solution.cpp -o s && ./s')
    trial(job, 'a__1', 'tb/ks', agent, 1, cost=2.0, events=[build, build, {'type': 'result', 'subtype': 'success',
                                                                              'num_turns': 30}])
    trial(job, 'a__2', 'tb/ks', agent, 0, cost=4.0, events=[build, {'type': 'result',
                                                                      'subtype': 'error_max_budget_usd'}])
    trial(job, 'b__1', 'tb/ks', mcp_agent, 1, events=[tool_use('mcp__cpp-repl__run_cpp', code='1'),
                                                      tool_use('mcp__cpp-repl__run_cpp_file', path='a.cpp'), build])
    trial(job, 'b__2', 'tb/ks', mcp_agent, None, error='AgentTimeoutError')
    trial(job, 'c__1', 'tb/ks', skill_agent, 1, events=[tool_use('Skill', skill='run-cpp'),
                                                        tool_use('Bash', command='cpp-repl --batch -q s.cpp'),
                                                        tool_use('Bash', command='make -C /app test')])
    trial(job, 'd__1', 'tb/ks', {'name': 'oracle'}, 1)
    (job / 'result.json').write_text('{"n_total_trials": 6}')   # the job's own result is no trial
    rows = {row['trial']: row for row in report.read_jobs([job])}
    assert set(rows) == {'a__1', 'a__2', 'b__1', 'b__2', 'c__1', 'd__1'}
    assert [rows[t]['arm'] for t in sorted(rows)] == ['control', 'control', 'mcp', 'mcp', 'skill', 'oracle']
    assert rows['a__1']['build_commands'] == 2 and rows['a__1']['turns'] == 30 and rows['a__1']['solved']
    assert rows['a__2']['stop'] == 'error_max_budget_usd' and not rows['a__2']['solved']
    assert rows['b__1']['mcp_calls'] == 2 and rows['b__1']['build_commands'] == 1
    assert rows['b__2']['error'] == 'AgentTimeoutError' and not rows['b__2']['solved']
    assert rows['c__1']['skill_runs'] == 1 and rows['c__1']['repl_commands'] == 1 and rows['c__1']['build_commands'] == 1
    assert rows['a__1']['agent_seconds'] == 1800

    lines = {(s['task'], s['arm']): s for s in report.summary(list(rows.values()))}
    control = lines[('tb/ks', 'control')]
    assert (control['trials'], control['solved'], control['budget_stops'], control['cost_usd']) == (2, 1, 1, 3.0)
    assert control['agent_minutes'] == 30 and lines[('tb/ks', 'mcp')]['errored'] == 1
    compared = {c['arm']: c for c in report.comparisons(list(rows.values())) if c['task'] == 'tb/ks'}
    assert compared['mcp']['treated'] == '1/2' and compared['mcp']['control'] == '1/2'
    assert compared['mcp']['difference'] == 0 and compared['skill']['treated'] == '1/1'

    out = tmp_path / 'results' / 'r'
    assert report.main([str(job), '--out', str(out)]) == 0
    text = out.with_suffix('.md').read_text()
    assert '| tb/ks | control | 2 | 1 | 50% [' in text and '## Against control' in text
    assert '| t1-tb4-cpp_model_stamp/b__2 | tb/ks | mcp | AgentTimeoutError |' in text
    assert out.with_suffix('.csv').read_text().count('\n') == 7
