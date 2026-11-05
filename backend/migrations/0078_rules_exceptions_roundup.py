"""A saved rule on the "exceptions" event becomes a task_roundup rule.

The engineering exceptions are lines on the task roundup now (backend/task_roundup.py), and no event named "exceptions"
is left to wake on. A rule a person saved under it moves to the roundup's event, which is where its lines are listed.
Every other rule keeps its event.
"""


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rules'").fetchone():
        return
    conn.execute("UPDATE rules SET event='task_roundup' WHERE event='exceptions'")
