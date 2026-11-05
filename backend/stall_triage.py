"""The stall verdicts a quiet task's last read left: the table, and the class the Task Manager shows for a task.

Decide used to read a quiet task once per stand and record what it was (waiting on someone, finished but unmarked,
stuck, or unsure). Nothing reads tasks that way now: the task roundup (backend/task_roundup.py) lists quiet work, so no
verdict is written. Existing verdicts stay for the Task Manager, which shows one only while the task still stands as it
did when it was read (class_of).
"""
from .store import H

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
    """The class Decide gave this task at the way it stands now (`unsure` or `stuck`, the two a person may still have to
    act on), or None: no verdict, or the task changed since. For the Task Manager's quiet-task items."""
    try:
        found = verdict_of(c, row["id"])
        if found and found["class"] in (UNSURE, STUCK) and found["stand"] == H._stall_stand(c, row):
            return found["class"]
    except Exception:       # noqa: BLE001 - a missing table or an unreadable task is no class
        pass
    return None
