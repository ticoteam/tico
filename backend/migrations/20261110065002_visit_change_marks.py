"""Where a visit last changed, by order (backend/task_wakes.py, docs/dev/pipelines-v3-build.md).

The never-stuck check counts the runs and reads the wakes "since the visit last changed". The clock cannot say which
side of a change a run is on: it can go back. So each visit keeps the newest `attempts` row and the newest `messages`
row there were when it last changed (`last_change_attempt`, `last_change_message`, rowids). A run or a wake that came
after the change has a larger rowid, whatever the clock said. Rows in those tables are never deleted, so rowids only
grow.

The marks follow `last_change_at`: one trigger writes them when a visit opens, the other whenever its last change is
written. An open visit gets the marks of now. On a new file `attempts` and `messages` do not exist yet when the
migrations run; a trigger reads them only when it fires, and there is no visit to mark.
"""

MARKS = (
    "last_change_attempt = (SELECT COALESCE(MAX(rowid), 0) FROM attempts), "
    "last_change_message = (SELECT COALESCE(MAX(rowid), 0) FROM messages)"
)
TRIGGERS = (
    f"CREATE TRIGGER IF NOT EXISTS visits_change_marks_on_open AFTER INSERT ON visits BEGIN "
    f"UPDATE visits SET {MARKS} WHERE id = NEW.id; END",
    f"CREATE TRIGGER IF NOT EXISTS visits_change_marks_on_change AFTER UPDATE OF last_change_at ON visits BEGIN "
    f"UPDATE visits SET {MARKS} WHERE id = NEW.id; END",
)


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(visits)")}
    for name in ("last_change_attempt", "last_change_message"):
        if name not in columns:
            conn.execute(f"ALTER TABLE visits ADD COLUMN {name} INTEGER")
    for sql in TRIGGERS:
        conn.execute(sql)
    if _has_table(conn, "attempts") and _has_table(conn, "messages"):
        conn.execute(f"UPDATE visits SET {MARKS} WHERE left_at IS NULL")
