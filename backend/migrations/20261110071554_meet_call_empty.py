"""Google Meet calls (backend/calls.py, docs/meet.md): `meet_empty_at`, the first successful status read that showed
nobody in a Meet someone had been seen in. The call ends `meet_ended` once reads have shown nobody for a minute from
then, so a stretch when Google did not answer never counts as a minute of nobody. A read that shows anyone clears it.

`calls` comes from 0047, so it is there on every file; the guard only keeps a rerun from adding the column twice.
"""


def up(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "calls" not in tables:
        return
    have = {row[1] for row in conn.execute("PRAGMA table_info(calls)")}
    if "meet_empty_at" not in have:
        conn.execute("ALTER TABLE calls ADD COLUMN meet_empty_at TEXT")
