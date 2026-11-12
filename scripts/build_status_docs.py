#!/usr/bin/env python3
"""Keep docs/status-and-state.md's value lists aligned with backend/status_values.py, the lists the database enforces.

Writes the generated table between the markers. `--check` changes nothing and fails when the table is stale, or when a
column's section (`### table.column`) names a value the list lacks or leaves one out: each value in a section is a
bullet that starts with it (- `value`: what it means), or with a few values that share one meaning (- `a`, `b`: ...).
"""

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'docs/status-and-state.md'

START = '<!-- status-values:start -->'
END = '<!-- status-values:end -->'


def columns():
    """status_values.COLUMNS, loaded from its file: the module imports only the standard library, and importing the
    backend package would reach every backend test in the PR selection."""
    spec = importlib.util.spec_from_file_location('status_values', ROOT / 'backend/status_values.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.COLUMNS


def table():
    rows = [
        '| Column | Enforced by | NULL | Values | Retired spelling (mapped to) |',
        '|---|---|---|---|---|',
    ]
    for col in columns():
        values = ', '.join(f'`{v}`' for v in col.values)
        retired = ', '.join(f'`{old}` (`{new}`)' for old, new in col.retired) or ''
        nulls = 'allowed' if col.nullable else 'refused'
        rows.append(
            f'| [`{col.table}.{col.column}`](#{col.table}{col.column}) | {col.enforce} | {nulls} | {values} | {retired} |'
        )
    return '\n'.join(rows)


def sections(text):
    """{'table.column': the set of values its bullets name}, for each `### table.column` heading."""
    found = {}
    for m in re.finditer(r'^### `?(\w+\.\w+)`?[^\n]*\n(.*?)(?=^#{1,3} |\Z)', text, re.M | re.S):
        named = re.findall(r'^- ((?:`[a-z_]+`, )*`[a-z_]+`)(?: \(|:)', m[2], re.M)
        found[m[1]] = {v for bullet in named for v in re.findall(r'`([a-z_]+)`', bullet)}
    return found


def drift(text):
    """Every way a column's section disagrees with its list, one line each."""
    out, have = [], sections(text)
    for col in columns():
        name = f'{col.table}.{col.column}'
        if name not in have:
            out.append(f'{name}: no "### {name}" section')
            continue
        listed = set(col.values)
        for v in sorted(listed - have[name]):
            out.append(f'{name}: `{v}` is in the list but has no bullet')
        for v in sorted(have[name] - listed):
            out.append(f'{name}: `{v}` has a bullet but is not in the list')
    return out


def render(text):
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + '\n' + table() + '\n' + END + after


def main():
    text = PAGE.read_text()
    current = render(text)
    problems = drift(current)
    if '--check' in sys.argv:
        if text != current:
            problems.insert(0, 'the value table is stale: run python3 scripts/build_status_docs.py')
        return 'docs/status-and-state.md:\n  ' + '\n  '.join(problems) if problems else 0
    PAGE.write_text(current)
    return 'docs/status-and-state.md:\n  ' + '\n  '.join(problems) if problems else 0


if __name__ == '__main__':
    sys.exit(main())
