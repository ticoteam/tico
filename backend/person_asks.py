"""Bot tasks set waiting on a person before asks became subtasks (hubdb._ask_in_subtask) take the new shape.

Such a task was itself the person's card: its title, its body and the bot's notes. Each one whose waiting note is a
plain ask (hubdb.lint_person_ask) and that has no open question to the person is updated as its bot would update it
now, so the ask becomes one small subtask the person owns and the task stays the bot's, blocked on it. One with no
plain ask is left to backend/restate_asks.py, and the bot's restated ask takes this shape when it is set. Run by the
scheduler, never on a read; a task already moved is no longer waiting on the person, so it is never moved twice.
"""

from . import hubdb as H
from .batch_work import isolated


def waiting_on_people(conn):
    """Bot tasks waiting on a person with a plain ask and no open question to them."""
    out = []
    for row in H._rows(conn.execute(
            "SELECT * FROM tasks WHERE status='waiting' AND waiting_on LIKE 'human:%' AND owner LIKE 'bot:%' "
            "ORDER BY created_at")):
        if H.lint_person_ask(row.get("note")):
            continue
        if any(a["to_actor"] == row["waiting_on"] for a in H.open_task_asks(conn, row)):
            continue
        out.append(row)
    return out


def migrate(conn):
    """Move each such task to the subtask shape. Returns the task ids moved."""
    moved = []
    for row in waiting_on_people(conn):
        with isolated(conn, "person_asks", row["id"]):
            H.task_update(conn, row["owner"], row["id"], status="waiting", waiting_on=row["waiting_on"],
                          note=row["note"], quiet=True)
            moved.append(row["id"])
    return moved
