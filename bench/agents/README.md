# Agents: Claude Code with and without cpp-repl

**The question:** does a coding agent solve C++ tasks more often, faster or
cheaper when cpp-repl is there? And when it is, does the MCP tool do better
than the skill?

The agent is Claude Code, run by [Harbor](https://github.com/harbor-framework/harbor),
the runner of [Terminal-Bench](https://www.tbench.ai). The tasks are
Terminal-Bench 4.0's C++ tasks, each in its own container, graded by the
task's own verifier in a container of its own.

## The arms

Every arm runs the same agent, [`harbor_agent.py`](harbor_agent.py): Harbor's
Claude Code agent, with clang-repl 23 and cpp-repl installed in the
container. The files are the same in every arm; what the agent is told
differs.

| Arm | What the agent has |
|---|---|
| `control` | Claude Code. cpp-repl is installed in `/opt/cpp-repl`, off PATH, and never mentioned. |
| `mcp` | The same, with the MCP server `cpp-repl --mcp` ([`cpp-repl.mcp.json`](cpp-repl.mcp.json)): the tools `run_cpp`, `run_cpp_file`, `save_cpp` and `reset_session`. |
| `skill` | The same, with the plugin's run-cpp skill and `cpp-repl` on PATH: the plugin's way before the MCP server. |
| `oracle`, `nop` | Harbor's own: the task's reference solution, and nothing at all. They check that a task works on this machine (reward 1) and that its verifier fails what is not done (reward 0). |

clang-23 comes from apt.llvm.org in every arm. Its `/usr/bin` links are
removed, so that no arm finds `clang++-23` or `clang-repl-23` on PATH; the
task's own compiler (g++ 13) is what the control arm compiles with.
cpp-repl's precompiled header is built before the agent starts.

## The tiers

| Tier | Tasks | Trials | What it is for |
|---|---|--:|---|
| 0 | `ks-solver-cpp` and `mvcc-lsm-compaction`, oracle and nop; then `ks-solver-cpp` once per arm, at low effort and $5 a trial | 4 + 3 | The pipeline works: images build, rewards are 1 and 0, the MCP server starts in the container, the tools are called. |
| 1 | Terminal-Bench 4.0's two C++ tasks, 10 attempts per arm | 60 | The question as asked. Two tasks make it a case study. |
| 2 | Aider's polyglot benchmark, its 26 C++ exercises, and Terminal-Bench 2.0's three C++ tasks, 3 attempts per arm | 261 | An interval narrow enough to read. |

Terminal-Bench 4.0 (66 tasks, 2026-08-26) has two C++ tasks:
- `ks-solver-cpp` asks for a spectral solver of a PDE on the unit disk in
  `/app/solution.cpp`, which the verifier builds with `g++ -O3 -std=c++17`.
- `mvcc-lsm-compaction` asks for a bug found and fixed in a reduced C++
  storage engine, with a regression test.

Its CUDA task needs an H100, and its C task works on a stripped binary.

## Held constant

- **One job per tier, every arm in it.** Harbor orders a job's trials
  attempt by task by agent, so the arms run interleaved, in the same hours.
  Anthropic measured a 6-point swing on Terminal-Bench 2.0 from machine
  resourcing alone.
- **The machine and its limits.** Harbor's Docker backend enforces each
  task's CPU and memory as limits and reserves nothing. Four trials at once
  keep the limits within the machine's 32 cores and 62 GB.
- **The versions:** Harbor 0.24.0, Claude Code 2.1.303 in the container,
  the model, and the cpp-repl wheel, whose sha256 each job records. A change
  of any of them starts a new campaign.
- **The caps,** the same in every arm: Claude Code's `--max-budget-usd` ($25
  a trial in tier 1, $5 in tiers 0 and 2), and the task's agent timeout
  (8 hours in Terminal-Bench 4.0). A trial stopped by the budget counts as
  unsolved, and the report counts them.

## Running

```
uv tool install harbor==0.24.0
uv build --wheel --out-dir bench/agents/dist     # from a checkout with cpp-repl --mcp (0.3.0 or newer)
export ANTHROPIC_API_KEY=...                     # or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`
make agents-smoke                                # tier 0
make agents-bench TIER=1                         # every arm of tier 1; ARM=mcp for one
make agents-report                               # bench/agents/results/report-<time>.md and .csv
```

- **Credentials.** Claude Code runs inside the containers and needs its own
  credential there. `ANTHROPIC_API_KEY` is billed by the API.
  `CLAUDE_CODE_OAUTH_TOKEN` counts against a Claude subscription's usage.
  Harbor passes either one in, and neither is written to a file.
- **`bench/agents/run.py --tier N --dry-run`** prints each job's
  configuration and the `harbor run` command, and runs nothing.
- **`--install-only`** sets up each arm's container and runs the checks,
  then stops: no model call and no verifier, so no credential is needed.
- **Jobs** land in `bench/agents/jobs/` (ignored by git), each beside
  `NAME.config.json`, what Harbor ran, and `NAME.run.json`, what it ran with.
- **Cost.** Terminal-Bench's leaderboard runs spent about $14 a trial with
  Opus 5.5 at effort max, and $22 with Sonnet 5.5, which used more tokens.
  Tier 0's three agent trials cost at most $15.

## Reading the report

[`report.py`](report.py) gives one row per task and arm:
- the solve rate with a 95% Wilson interval;
- the errored trials and the budget stops;
- the median cost, agent minutes, and input and output tokens;
- how often the agent called the MCP tools, ran cpp-repl through Bash, and
  compiled or built.

A second table gives each treatment against control: the difference in solve
rate, with Newcombe's interval and Fisher's exact p. A trial that raised,
such as an agent timeout, counts as unsolved, as Terminal-Bench's
leaderboard counts it, and is listed.

## Limits

- **The MCP server's configuration is fixed** when Claude Code starts: the
  task's `/app` as its directory, no profile, no flags. Quoted includes
  resolve against `/app`, and `run_cpp_file` takes a source with its own
  includes.
- **Docker limits are not reservations.** Other work on the machine competes
  with the trials. Run nothing else heavy during a tier.
- **Two tasks are not a benchmark.** Tier 1 says what happened on two tasks,
  ten times each. Tier 2 is the one to generalise from.

## Results

Tier 0 on this machine, 2026-10-19 (Harbor 0.24.0, Docker 29, 32 cores):

| Check | ks-solver-cpp | mvcc-lsm-compaction |
|---|---|---|
| oracle | reward 1.0 | reward 1.0 |
| nop | reward 0 | reward 0 |
| each arm's container, `--install-only` | set up and checked, in 1.5 minutes | not run |

In the install-only run, every arm's container installed Claude Code 2.1.303,
clang-23 and the cpp-repl wheel. Then a session printed 6 * 7, and
`cpp-repl --mcp` answered a `run_cpp` call. The agent trials wait for a
credential.
