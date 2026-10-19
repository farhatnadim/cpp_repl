"""The agent every arm of bench/agents runs: Harbor's Claude Code agent, with
clang-repl and cpp-repl installed in the task's container.

    harbor run ... -a harbor_agent:ClaudeCodeCppRepl --ak cpp_repl_wheel=WHEEL [--ak expose_cpp_repl=true]

Harbor imports this module itself (run.py puts bench/agents on PYTHONPATH),
so it runs in Harbor's own Python and imports only Harbor and the standard
library.

Every arm installs the same files, so that the containers differ only in
what the agent is told:
  - clang-N and clang-tools-N from apt.llvm.org, with their /usr/bin links
    removed, so that no arm finds clang++-N or clang-repl-N on PATH; cpp-repl
    finds clang-repl in /usr/lib/llvm-N/bin;
  - the cpp-repl wheel in a virtual environment at /opt/cpp-repl, off PATH;
  - cpp-repl's precompiled header, built once as the agent's user, so that
    the first run in a trial starts in half a second.
Before the agent starts, two checks run in every arm: a session prints
6 * 7, and cpp-repl --mcp answers a run_cpp call. A container where either
fails ends the trial at setup, as an error, before the agent spends anything.
The mcp arm adds Harbor's --mcp-config (cpp-repl.mcp.json), which starts
/opt/cpp-repl/bin/cpp-repl --mcp; the skill arm adds Harbor's --skill with
the run-cpp skill and expose_cpp_repl, which links cpp-repl into
/usr/local/bin.
"""

import shlex
from pathlib import Path
from typing import override

from pydantic import Field

from harbor.agents.installed.claude_code import ClaudeCode, ClaudeCodeOptions
from harbor.environments.base import BaseEnvironment

PREFIX = '/opt/cpp-repl'
UPLOADS = '/installed-agent/cpp-repl'

# Run by cpp-repl's own Python: an MCP client's handshake and one call.
MCP_CHECK = f'''
import json, subprocess
messages = [
    {{"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "clientInfo": {{"name": "setup", "version": "0"}}}}}},
    {{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {{"name": "run_cpp", "arguments": {{"code": "6 * 7"}}}}}},
]
run = subprocess.run(["{PREFIX}/bin/cpp-repl", "--mcp"], input="".join(json.dumps(m) + "\\n" for m in messages),
                     capture_output=True, text=True, timeout=300)
text = json.loads(run.stdout.splitlines()[-1])["result"]["content"][0]["text"]
assert text == "c++> 6 * 7\\n42\\n", (text, run.stderr)
'''


class ClaudeCodeCppReplOptions(ClaudeCodeOptions):
    cpp_repl_wheel: str = Field(description='The cpp-repl wheel: a file on the host, uploaded into the '
                                            'container, or a URL pip installs from.')
    llvm_major: int = Field(default=23, description='The LLVM release whose clang-repl cpp-repl runs.')
    expose_cpp_repl: bool = Field(default=False, description='Link cpp-repl into /usr/local/bin, for the '
                                                             'skill arm.')


def install_script(llvm_major, wheel):
    """The shell script, run as root, that installs clang-repl and cpp-repl
    in an Ubuntu or Debian container."""
    n = int(llvm_major)
    return f'''set -eu
export DEBIAN_FRONTEND=noninteractive
. /etc/os-release
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3 python3-venv
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key -o /etc/apt/keyrings/apt.llvm.org.asc
chmod 0644 /etc/apt/keyrings/apt.llvm.org.asc
echo "deb [signed-by=/etc/apt/keyrings/apt.llvm.org.asc] https://apt.llvm.org/$VERSION_CODENAME/ llvm-toolchain-$VERSION_CODENAME-{n} main" > /etc/apt/sources.list.d/apt.llvm.org.list
apt-get update
apt-get install -y --no-install-recommends clang-{n} clang-tools-{n}
find /usr/bin -maxdepth 1 -lname '../lib/llvm-{n}/*' -delete
python3 -m venv {PREFIX}
{PREFIX}/bin/pip install --no-cache-dir --disable-pip-version-check --quiet {shlex.quote(wheel)}
mkdir -p {UPLOADS}
{{ {PREFIX}/bin/cpp-repl --version; /usr/lib/llvm-{n}/bin/clang-repl --version; }} > {UPLOADS}/versions.txt 2>&1
'''


class ClaudeCodeCppRepl(ClaudeCode):
    """Claude Code, with clang-repl and cpp-repl installed beside it."""

    options_model = ClaudeCodeCppReplOptions
    options: ClaudeCodeCppReplOptions

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        wheel = self.options.cpp_repl_wheel
        if '://' not in wheel:
            local = Path(wheel).expanduser().resolve()
            await self.exec_as_root(environment, command=f'mkdir -p {UPLOADS}')
            await environment.upload_file(local, f'{UPLOADS}/{local.name}')
            wheel = f'{UPLOADS}/{local.name}'
        await self.exec_as_root(environment, command=install_script(self.options.llvm_major, wheel),
                                timeout_sec=1800)
        if self.options.expose_cpp_repl:
            await self.exec_as_root(environment, command=f'ln -sf {PREFIX}/bin/cpp-repl /usr/local/bin/cpp-repl')
        # The precompiled header, built as the user the agent runs as, and the checks.
        await self.exec_as_agent(
            environment, timeout_sec=600,
            command=(f'out=$(printf "6 * 7\\n" | {PREFIX}/bin/cpp-repl --no-config -q) && test "$out" = 42 '
                     f'|| {{ echo "cpp-repl does not run: $out" >&2; exit 1; }}'))
        await self.exec_as_agent(environment, timeout_sec=600,
                                 command=f"{PREFIX}/bin/python - <<'EOF'\n{MCP_CHECK}\nEOF")
