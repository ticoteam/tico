"""The Recorder's last columns: `calls.recorder_actor`, `calls.recorder_claimed_at` and `humans.record_on_join`.

Migration drop_recorder kept them because an install below v0.6.0 could still add them after it ran. The floor is
v0.6.0 now, which shipped both files that add them, so nothing pending adds them again. No index, trigger, view or
foreign key names them, so each goes with a plain DROP COLUMN. Each step checks first, so a second run changes nothing.
"""

DROPS = (
    ("calls", "recorder_actor"),
    ("calls", "recorder_claimed_at"),
    ("humans", "record_on_join"),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    for table, column in DROPS:
        if column in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
