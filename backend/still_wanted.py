"""Answers to "Still wanted?" questions put before the nudge was removed.

No question is put to a person now: a bot's quiet task is a line on its roundup (backend/task_roundup.py). A question still on a task from before is answered as it always was. Keep or Close
acts only for the person it was put to, and nobody else who may comment (the bot on the task included) decides it.
"""

from . import hubdb as H

IDLE_DAYS = 7
QUESTION_ID = "still_wanted"
KEEP, CLOSE = "Keep", "Close"


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
