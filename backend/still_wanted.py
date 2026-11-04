"""Still wanted? questions to people about idle tasks, and the answers to the ones already put.

No new question is put to a person: `ask_idle` returns nothing, and migration 0080 withdraws the open ones. A question put
before that is still answered here: `is_ask`, `choice` and `act` read its Keep or Close, and only the person it was put to
decides. `idle_tasks` still names the tasks a week idle, one at a time, for the tests that read them.
"""

import sqlite3

from . import hubdb as H

IDLE_DAYS = 7
QUESTION_ID = "still_wanted"
KEEP, CLOSE = "Keep", "Close"


def _last_activity(row):
    return max(row.get("updated_at") or "", row.get("checkpoint_at") or "")


def _live(conn, task_id):
    """A run on this task is leased or going: it is not idle, whatever its timestamps say."""
    try:
        return conn.execute("SELECT 1 FROM attempts a WHERE a.task_id=? "
                            "AND a.state IN ('leased','running') LIMIT 1", (task_id,)).fetchone() is not None
    except sqlite3.OperationalError:     # a local hub has no attempts table
        return False


def recipient(conn, row):
    """The requester when a person asked for it, else the owner when a person owns it, else nobody."""
    for actor in (row["requester_id"], row["owner_id"]):
        if H.is_human(actor) and H._actor_exists(conn, actor):
            return actor
    return None


def idle_tasks(conn, at=None):
    at = at or H.now()
    cutoff = H.shift(at, days=-IDLE_DAYS)
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    out = []
    for row in H._rows(conn.execute(
            f"SELECT * FROM {H.TASKS} tasks WHERE status IN ({marks}) AND updated_at<? "
            "AND coalesce(checkpoint_at,'')<? ORDER BY updated_at", (*H.ACTIVE_STATUSES, cutoff, cutoff))):
        if H.TS.snoozed(row, at):
            continue                     # set aside until a date: its owner hears when that comes
        if H.steps_only(conn, row):
            continue                     # a ticket resting in a board column; Close is not its way out
        if row.get("stale_asked_at") and row["stale_asked_at"] >= _last_activity(row):
            continue                     # already asked in this idle stretch
        if _live(conn, row["id"]) or H.unanswered_ask(conn, row):
            continue                     # running, or someone already owes an answer on it
        if recipient(conn, row):
            out.append(row)
    return out


def ask_idle(conn, at=None):
    """No question goes to a person about an idle task any more: the maintenance run never messages a person. Returns []."""
    return []


def is_ask(asked):
    refs = (asked or {}).get("refs") or {}
    return bool(asked and asked.get("sender_id") == H.KEEPER and refs.get("still_wanted"))


def choice(asked, answer, actor):
    """Keep, Close or None (dismissed, or answered only in words) for an answer to a still-wanted ask.
    Only the person it was put to decides: anyone else who may comment (the bot that filed the task
    included) answers it as an ordinary answer, with no Keep or Close effect."""
    if not is_ask(asked) or answer.dismiss or not (H.is_human(actor) and actor == asked.get("recipient_id")):
        return None
    labels = answer.answers.get(QUESTION_ID) or []
    return labels[0] if labels and labels[0] in (KEEP, CLOSE) else None


def act(conn, actor, task_id, picked):
    """Do what the person chose. Keep: the answer already marked the task active, so its clock restarts.
    Close: the task closes as theirs, with the reason on it."""
    if picked == CLOSE:
        row = H.task(conn, task_id)
        if row and row["status"] in H.ACTIVE_STATUSES:
            H.task_close(conn, actor, task_id, f"Closed: no longer wanted after {IDLE_DAYS} idle days.")
    elif picked == KEEP:
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.now(), task_id))
