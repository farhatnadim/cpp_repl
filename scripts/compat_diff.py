#!/usr/bin/env python3
"""What changed between two runs of tests/compat/harness.py: the LLVM major
and each probe's verdict. Dates, timings, exit codes, outputs and the minor
and patch versions are not changes.

    scripts/compat_diff.py OLD.json NEW.json          # - reads stdin
    git show HEAD:tests/compat/results/compat-llvm-snapshot.json \\
      | scripts/compat_diff.py - tests/compat/results/compat-llvm-snapshot.json

Prints a Markdown summary; exits 1 when something changed, 0 when nothing
did. .github/workflows/track.yml files an issue on 1.
"""

import json
import sys
from pathlib import Path


def load(arg):
    return json.loads(sys.stdin.read() if arg == '-' else Path(arg).read_text())


def major(result):
    return result['version'].split('.')[0]


def diff(old, new):
    """The Markdown summary, and whether anything changed."""
    lines = [f"clang-repl {old['version']} probed {old['date']} -> {new['version']} probed {new['date']}"]
    changed = False
    if major(old) != major(new):
        lines += ['', f'**The major moved from {major(old)} to {major(new)}.**']
        changed = True
    rows = []
    for name in dict.fromkeys([*old['probes'], *new['probes']]):
        before = old['probes'].get(name, {}).get('verdict', '-')
        after = new['probes'].get(name, {}).get('verdict', '-')
        if before != after:
            title = (new['probes'].get(name) or old['probes'][name])['title'].replace('|', '\\|')
            rows.append(f'| {title} | {before} | {after} |')
    if rows:
        lines += ['', '| Probe | Recorded | Now |', '| --- | --- | --- |', *rows]
        changed = True
    else:
        lines += ['', "No probe's verdict changed."]
    return '\n'.join(lines) + '\n', changed


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    text, changed = diff(load(argv[0]), load(argv[1]))
    sys.stdout.write(text)
    return 1 if changed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
