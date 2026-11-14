"""The release policy's job columns (backend/wake_presets.py): `release`, the policy a job that can wait carries (Due,
the wait, Prefers warm, Run cold or Skip at the deadline, Fresh or Fork); `release_after`, the job a warm window chained
this one after; and `released`, how the release check let it go (warm or cold, how long it waited, or skipped).

`jobs` does not exist on a new file: there backend/store.py's SCHEMA makes it with the columns.
"""

COLUMNS = (
    ("release", "TEXT"),
    ("release_after", "TEXT"),
    ("released", "TEXT"),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if not columns:
        return
    for column, kind in COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {kind}")
