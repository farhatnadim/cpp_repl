"""scripts/compat_table.py and scripts/compat_diff.py: the README's "Why a
front end" table is what the checked-in probe results render to, and a diff
of two runs reports a moved verdict or major and nothing else."""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import compat_diff  # noqa: E402
import compat_table  # noqa: E402

RESULTS = sorted((ROOT / 'tests' / 'compat' / 'results').glob('*.json'))


def test_readme_table_is_current():
    text = (ROOT / 'README.md').read_text()
    assert compat_table.section(text) == compat_table.render(RESULTS), \
        "README.md's compat table differs from tests/compat/results: make compat-table"


def test_diff_reports_verdicts_and_the_major():
    old = json.loads(RESULTS[-1].read_text())
    name = next(iter(old['probes']))
    text, changed = compat_diff.diff(old, old)
    assert not changed and "No probe's verdict changed." in text

    new = copy.deepcopy(old)
    new['date'] = '2099-01-01'
    new['version'] = old['version'].rsplit('.', 1)[0] + '.99'
    new['probes'][name]['seconds'] = 99.0
    new['probes'][name]['output'] = 'other'
    text, changed = compat_diff.diff(old, new)
    assert not changed, text

    new['probes'][name]['verdict'] = 'something else'
    text, changed = compat_diff.diff(old, new)
    assert changed
    assert text.count('\n| ') == 3   # the header, its rule, one row
    assert f"| {old['probes'][name]['title']} | {old['probes'][name]['verdict']} | something else |" in text

    new['version'] = '99.0.0'
    text, changed = compat_diff.diff(old, new)
    assert changed and 'The major moved' in text
