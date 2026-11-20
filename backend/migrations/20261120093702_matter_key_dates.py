"""A matter's key dates (backend/risk.py): several per matter, each with what it is for, a not before, a due by and when
it was done.

- `matter_dates.label`: what the date is for ("Hearing").
- `matter_dates.starts_at`: not before, the earliest its step may be done; never a deadline.
- `matter_dates.due_at`: due by, the deadline. A row has at least one of the two.
- `matter_dates.done_at`: when it was marked done; a done date leaves the calendar.

Each date is a day (YYYY-MM-DD) or a UTC time (YYYY-MM-DDTHH:MM:SSZ), as `matters.starts_at` and `matters.due_at` are.
Each matter's one pair of those columns (20261120060550_matter_dates) becomes its first row here; the columns stay and
mirror that first row. Pending migrations run in any order: on a company where `matters` or its date columns do not
exist yet there is nothing to copy, and the table is still made. Running it again changes nothing.
"""

import uuid


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS matter_dates ("
        "id TEXT PRIMARY KEY, "
        "task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, "
        "label TEXT NOT NULL DEFAULT '', "
        "starts_at TEXT, due_at TEXT, done_at TEXT, "
        "created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_by_id TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "CHECK (starts_at IS NOT NULL OR due_at IS NOT NULL))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS matter_dates_task_id ON matter_dates(task_id)")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='matters'").fetchone():
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(matters)")}
    if not {"starts_at", "due_at", "dates_label"} <= columns:
        return
    rows = conn.execute(
        "SELECT m.task_id, m.dates_label, m.starts_at, m.due_at, m.updated_by_id, m.updated_at FROM matters m "
        "WHERE (m.starts_at IS NOT NULL OR m.due_at IS NOT NULL) "
        "AND NOT EXISTS (SELECT 1 FROM matter_dates d WHERE d.task_id = m.task_id) ORDER BY m.task_id"
    ).fetchall()
    for task_id, label, starts_at, due_at, actor, at in rows:
        conn.execute(
            "INSERT INTO matter_dates(id, task_id, label, starts_at, due_at, created_by_id, created_at, updated_by_id, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), task_id, label or "", starts_at, due_at, actor, at, actor, at),
        )
