"""One "Still wanted?" question for a task nobody has touched in a week.

With many tasks open, some stop mattering and nobody says so. Nothing here closes or deletes a task on
its own: an idle task gets one question to the person behind it, Keep or Close, and only their answer
acts. One ask per idle stretch: `stale_asked_at` records it, and any later activity (a comment, an
update, a completed turn's checkpoint) starts a new stretch.
"""

import sqlite3

from . import hubdb as H
from .batch_work import isolated

IDLE_DAYS = 7
QUESTION_ID = "still_wanted"
KEEP, CLOSE = "Keep", "Close"


def _last_activity(row):
    return max(row.get("updated_at") or "", row.get("checkpoint_at") or "")


def _live(conn, task_id):
    """A run on this task is leased or going: it is not idle, whatever its timestamps say."""
    try:
        return conn.execute("SELECT 1 FROM turns t JOIN attempts a ON a.id=t.id WHERE t.task_id=? "
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
            f"SELECT * FROM tasks WHERE status IN ({marks}) AND updated_at<? "
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
    """Put "Still wanted?" to the person behind each idle task. Returns the task ids asked about. Not while the team
    has Stale ticket review on: its suggestions cover a quiet week (backend/stale_review.py)."""
    from . import team_rules
    try:
        if team_rules.load(conn)["stale_review"]:
            return []
    except sqlite3.OperationalError:     # a local hub keeps no team rules
        pass
    asked = []
    for row in idle_tasks(conn, at):
        with isolated(conn, "still_wanted", row["id"]):
            person = recipient(conn, row)
            ask = {"questions": [{"id": QUESTION_ID, "header": "Still wanted?",
                                  "question": f"Still wanted? {row['title']}"[:300],
                                  "options": [{"label": KEEP, "description": "Keep it open"},
                                              {"label": CLOSE, "description": "Close it"}],
                                  "multi": False, "other": False}], "who": person}
            H.task_comment(conn, H.KEEPER, row["id"],
                           f"Nothing has happened on this task for {IDLE_DAYS} days. Still wanted?",
                           wake=False, ask=ask, extra_refs={"still_wanted": True})
            # The ask itself bumps `updated_at`; recording the same moment keeps it inside this stretch.
            conn.execute("UPDATE tasks SET stale_asked_at=updated_at WHERE id=?", (row["id"],))
            H.event(conn, H.KEEPER, "task.still_wanted_ask", row["id"], {"to": person})
            asked.append(row["id"])
    return asked


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
