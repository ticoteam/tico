"""Helpers that other tests share, moved out of test_payload_budgets.py so they import no test file."""

import uuid


def clone(c, table, row, n, **changes):
    """`n` copies of `row` in `table`, each with a fresh id and `changes(i)` applied."""
    cols = list(row)
    rows = []
    for i in range(n):
        copy = dict(row, id=uuid.uuid4().hex)
        copy.update({k: f(i) for k, f in changes.items()})
        rows.append([copy[k] for k in cols])
    c.executemany(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", rows)
