"""The stall verdicts a quiet task's last read left: the table, and the class the General Manager shows for a task.

Decide used to read a quiet task once per stand and record what it was (waiting on someone, finished but unmarked,
stuck, or unsure). Nothing reads tasks that way now, and no verdict is written: the never-stuck check says what a
quiet task waits on.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS stall_triage(
 task_id TEXT PRIMARY KEY, stand TEXT NOT NULL, class TEXT NOT NULL, confidence REAL, outcome TEXT NOT NULL,
 detail TEXT, judged_at TEXT NOT NULL, woke_at TEXT);
"""

STUCK, UNSURE = "stuck", "unsure"


def verdict_of(c, task_id):
    row = c.execute("SELECT * FROM stall_triage WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def class_of(c, row):
    """None: a verdict was kept only while the task stood as it did when it was read, and the engine's tasks are never
    read that way (the never-stuck check says what a quiet task waits on). Kept until the General Manager stops asking."""
    return None
