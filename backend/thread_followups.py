"""Follow-ups on unanswered asks (the thread helper, spec 3.6).

A bot's ask to a person, in a thread, that stays unanswered for AFTER gets one nudge in that same thread: a keeper
notice addressed to the person, quoting the ask. Plain code on the scheduler's fixed cadence (backend/scheduler.py
FIXED, the same shape as the Bookkeeper's watch): no model is called or woken here, and a run with nothing to nudge
writes nothing. A nudge is recorded in `thread_followups` in the same transaction as the post, so one ask gets one
nudge even when runs overlap or a restart replays the sweep.
"""
from datetime import timedelta

from . import hubdb as H

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS thread_followups (message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
    "to_actor TEXT NOT NULL, nudge_message_id TEXT, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS thread_followups_conversation ON thread_followups(conversation_id, created_at)",
]
AFTER = timedelta(hours=24)     # an ask unanswered this long gets its one nudge
NUDGE_LIMIT = 50                # asks nudged per run; the rest wait for the next run
QUOTE_CHARS = 160


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def _quote(body):
    """The ask's first line, shortened, with no @ so the nudge names nobody (a mention would wake its bot)."""
    line = (str(body or "").strip().splitlines() or [""])[0].replace("@", "")
    return line if len(line) <= QUOTE_CHARS else line[:QUOTE_CHARS - 1].rstrip() + "..."


def due(c, at, limit=NUDGE_LIMIT):
    """Asks due a nudge, oldest first. An ask is due when a bot asked a person, it is still open and not expired, the
    person has not answered or written in its thread since, the thread is not a task's (a task's asks sit on its own
    Needs you card), and nothing has nudged it yet."""
    cutoff = H.shift(at, seconds=-int(AFTER.total_seconds()))
    return [dict(r) for r in c.execute(
        "SELECT m.id, m.conversation_id, m.from_actor, m.to_actor, m.body, m.created "
        "FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE m.kind='ask' AND m.from_actor LIKE 'bot:%' AND m.to_actor LIKE 'human:%' "
        "AND m.deleted_at IS NULL AND m.superseded_at IS NULL AND m.answered_by IS NULL "
        "AND (m.expires_at IS NULL OR m.expires_at>?) AND m.created<=? "
        "AND cv.closed_at IS NULL AND cv.archived_at IS NULL AND cv.task_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM thread_followups f WHERE f.message_id=m.id) "
        "AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=m.conversation_id "
        "AND r.from_actor=m.to_actor AND r.created>m.created AND r.deleted_at IS NULL) "
        "ORDER BY m.created LIMIT ?", (at, cutoff, limit)).fetchall()]


def sweep(c, at, limit=NUDGE_LIMIT):
    """Nudge each due ask once, in its thread. Returns the nudges written (a list of {"message", "thread", "to"})."""
    from . import threads
    done = []
    for ask in due(c, at, limit):
        text = f'Still waiting on you: "{_quote(ask["body"])}"'
        posted = threads.post(c, H.KEEPER, ask["conversation_id"], text, kind="notice", refs={"follow_up": ask["id"]},
                              to_actor=ask["to_actor"])
        c.execute("INSERT INTO thread_followups(message_id, conversation_id, to_actor, nudge_message_id, created_at) "
                  "VALUES(?,?,?,?,?)", (ask["id"], ask["conversation_id"], ask["to_actor"], posted["id"], at))
        H.event(c, H.KEEPER, "thread.follow_up", ask["id"], {"thread": ask["conversation_id"], "to": ask["to_actor"]})
        done.append({"message": ask["id"], "thread": ask["conversation_id"], "to": ask["to_actor"]})
    return done
