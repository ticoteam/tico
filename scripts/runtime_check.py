#!/usr/bin/env python3
"""Checks this interpreter can run Tico: the Python the dependency locks were compiled for, and a SQLite new enough
for the schema with the extensions it uses.

    python scripts/runtime_check.py

The Dockerfile runs it while building the images, so an image on the wrong Python or an older SQLite fails at build
time, not in the release smoke or on a customer's server. scripts/tests/test_runtime_check.py runs it on the test
interpreter.
"""
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'backend' / 'requirements.txt'
# The oldest SQLite the schema runs on: ALTER TABLE ... DROP COLUMN (backend/task_relations.py) needs 3.35.0, and
# UPDATE ... FROM needs 3.33.0. Raise it when the code starts using something newer.
MIN_SQLITE = (3, 35, 0)


def locked_python(lock=LOCK):
    """The Python minor version (3, 12) the lock's `uv pip compile --python-version` header names."""
    match = re.search(r'--python-version (\d+)\.(\d+)\b', Path(lock).read_text())
    if not match:
        raise ValueError(f'{lock} does not say which Python it was compiled for (--python-version)')
    return int(match.group(1)), int(match.group(2))


def problems(version_info=sys.version_info, connect=sqlite3.connect, lock=LOCK):
    """What is wrong with this runtime, as sentences; empty when it can run Tico."""
    found = []
    want = locked_python(lock)
    if tuple(version_info[:2]) != want:
        found.append(f'Python {version_info[0]}.{version_info[1]}, but the locks in {Path(lock).name} are compiled for '
                     f'{want[0]}.{want[1]}')
    db = connect(':memory:')
    try:
        version = db.execute('SELECT sqlite_version()').fetchone()[0]
        if tuple(int(part) for part in version.split('.')[:3]) < MIN_SQLITE:
            found.append(f'SQLite {version}, older than {".".join(map(str, MIN_SQLITE))}')
        for name, sql in (('FTS5', "CREATE VIRTUAL TABLE fts_probe USING fts5(body, tokenize='porter unicode61')"),
                          ('JSON functions', "SELECT value FROM json_each(json_set('{}', '$.a', 1))")):
            try:
                db.execute(sql).fetchall()
            except sqlite3.Error as error:
                found.append(f'SQLite {version} lacks {name} ({error})')
    finally:
        db.close()
    return found


def main():
    found = problems()
    for problem in found:
        print(f'runtime check: {problem}', file=sys.stderr)
    if not found:
        print(f'runtime check: Python {sys.version.split()[0]}, SQLite {sqlite3.sqlite_version} ok')
    return 1 if found else 0


if __name__ == '__main__':
    raise SystemExit(main())
