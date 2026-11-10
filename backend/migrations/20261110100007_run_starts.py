"""The order runs started in (backend/task_wakes.py `started`, `runs_since`; docs/pipelines-v3.md, the 7 runs).

A visit allows 7 bot runs between two changes, and a run is charged to the change that was current when it STARTED.
The attempts row is written at the claim, which can come before a change while the start comes after it: by the
attempt's rowid that run would belong to the old change and an eighth run could start. So every start takes the next
row of `run_starts` (AUTOINCREMENT: ids only grow, whatever the clock says), and each visit keeps the newest one there
was when it last changed (`last_change_start`), beside `last_change_attempt` and `last_change_message`. A run whose
start row is newer than the visit's mark started after the change.

The visit triggers of 20261110065002_visit_change_marks are made again with the third mark. Open visits get the mark of
now. Runs started before this file have no start row and are read by their attempt's rowid, as before.
"""

MARKS = (
    "last_change_attempt = (SELECT COALESCE(MAX(rowid), 0) FROM attempts), "
    "last_change_message = (SELECT COALESCE(MAX(rowid), 0) FROM messages), "
    "last_change_start = (SELECT COALESCE(MAX(id), 0) FROM run_starts)"
)
TRIGGERS = (
    ("visits_change_marks_on_open", "AFTER INSERT ON visits"),
    ("visits_change_marks_on_change", "AFTER UPDATE OF last_change_at ON visits"),
)


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_starts(id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL UNIQUE)"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(visits)")}
    if "last_change_start" not in columns:
        conn.execute("ALTER TABLE visits ADD COLUMN last_change_start INTEGER")
    for name, when in TRIGGERS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(f"CREATE TRIGGER {name} {when} BEGIN UPDATE visits SET {MARKS} WHERE id = NEW.id; END")
    if _has_table(conn, "attempts") and _has_table(conn, "messages"):
        conn.execute(f"UPDATE visits SET {MARKS} WHERE left_at IS NULL")
