#!/usr/bin/env python3
"""The README's "Why a front end" table, from the JSON that
tests/compat/harness.py writes for each clang-repl.

    scripts/compat_table.py tests/compat/results/*.json
"""

import json
import sys
from pathlib import Path


def version_key(result):
    return tuple(int(p) if p.isdigit() else 0 for p in result['version'].split('.'))


def main(paths):
    results = sorted((json.loads(Path(p).read_text()) for p in paths), key=version_key)
    if not results:
        sys.exit('usage: compat_table.py RESULTS.json...')
    names = list(results[-1]['probes'])
    header = ['Raw clang-repl'] + [f"{r['version']}" for r in results] + ['cpp-repl']
    rows = [header, ['---'] * len(header)]
    for name in names:
        first = next(r['probes'][name] for r in results if name in r['probes'])
        row = [first['title']]
        row += [r['probes'].get(name, {}).get('verdict', '-') for r in results]
        row.append(first['cpp_repl'])
        rows.append(row)
    for row in rows:
        print('| ' + ' | '.join(cell.replace('|', '\\|') for cell in row) + ' |')
    dates = ', '.join(f"{r['version']} on {r['date']}" for r in results)
    print(f'\nProbed with tests/compat/harness.py: {dates}.')


if __name__ == '__main__':
    main(sys.argv[1:])
