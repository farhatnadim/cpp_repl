#!/usr/bin/env python3
"""The README's "Why a front end" table, from the JSON that
tests/compat/harness.py writes for each clang-repl.

    scripts/compat_table.py tests/compat/results/*.json                   # print it
    scripts/compat_table.py --write README.md tests/compat/results/*.json # write it between the README's markers

The README holds the table between `<!-- compat-table -->` and
`<!-- /compat-table -->`; tests/test_compat_scripts.py fails when what is
there differs from what the results render to.
"""

import json
import sys
from pathlib import Path

START, END = '<!-- compat-table -->', '<!-- /compat-table -->'


def version_key(result):
    return tuple(int(p) if p.isdigit() else 0 for p in result['version'].split('.'))


def render(paths):
    """The table and its "Probed with" line, as Markdown, one column per
    result in version order."""
    results = sorted((json.loads(Path(p).read_text()) for p in paths), key=version_key)
    if not results:
        sys.exit('usage: compat_table.py [--write README.md] RESULTS.json...')
    names = list(results[-1]['probes'])
    header = ['Raw clang-repl'] + [r['version'] for r in results] + ['cpp-repl']
    rows = [header, ['---'] * len(header)]
    for name in names:
        first = next(r['probes'][name] for r in results if name in r['probes'])
        row = [first['title']]
        row += [r['probes'].get(name, {}).get('verdict', '-') for r in results]
        row.append(first['cpp_repl'])
        rows.append(row)
    lines = ['| ' + ' | '.join(cell.replace('|', '\\|') for cell in row) + ' |' for row in rows]
    dates = ', '.join(f"{r['version']} on {r['date']}" for r in results)
    return '\n'.join(lines) + f'\n\nProbed with tests/compat/harness.py: {dates}.\n'


def section(text):
    """What a README holds between the markers."""
    start = text.index(START) + len(START) + 1
    return text[start:text.index(END, start)]


def write(readme, table):
    """Put the table between the README's markers."""
    text = Path(readme).read_text()
    start = text.index(START) + len(START) + 1
    end = text.index(END, start)
    Path(readme).write_text(text[:start] + table + text[end:])


def main(argv):
    if argv[:1] == ['--write']:
        write(argv[1], render(argv[2:]))
        return
    sys.stdout.write(render(argv))


if __name__ == '__main__':
    main(sys.argv[1:])
