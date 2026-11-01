"""Follow-ups on unanswered asks (the thread helper, spec 3.6).

A bot's ask to a person, in a thread, that stays unanswered for AFTER gets one nudge in that same thread: a keeper
notice addressed to the person, quoting the ask. Plain code on the scheduler's fixed cadence (backend/scheduler.py
FIXED, the same shape as the Bookkeeper's watch): no model is called or woken here, and a run with nothing to nudge
writes nothing. A nudge is recorded in `thread_followups` in the same transaction as the post, so one ask gets one
nudge even when runs overlap or a restart replays the sweep.

An ask whose recipient can no longer read it is retired with no nudge, and so is one whose nudge fails: the row stays,
so later runs do not reconsider it. Each ask runs in its own savepoint, so a failure rolls back only that ask.
"""
import logging
from datetime import timedelta

from . import hubdb as H

LOG = logging.getLogger("tico.thread_followups")
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


def _nudge(c, ask):
    """The nudge for one ask, or None when its recipient cannot read the ask now. The nudge takes the ask's restriction
    and migrated audience, so a reader who cannot see the ask cannot see the nudge either; a restricted or migrated ask
    is nudged without its quote."""
    from . import threads
    reader = threads.actor_viewer(c, ask["to_actor"])
    if reader is None or not threads.readable(c, reader, ask["id"]):
        return None
    source = c.execute("SELECT restricted, legacy_audience FROM messages WHERE id=?", (ask["id"],)).fetchone()
    plain = not source["restricted"] and not source["legacy_audience"]
    text = f'Still waiting on you: "{_quote(ask["body"])}"' if plain else "Still waiting on you."
    posted = threads.post(c, H.KEEPER, ask["conversation_id"], text, kind="notice", refs={"follow_up": ask["id"]},
                          to_actor=ask["to_actor"])
    c.execute("UPDATE messages SET restricted=?, legacy_audience=? WHERE id=?",
              (source["restricted"], source["legacy_audience"], posted["id"]))
    return posted


def sweep(c, at, limit=NUDGE_LIMIT):
    """Nudge each due ask once, in its thread. Returns the nudges written (a list of {"message", "thread", "to"})."""
    done = []
    for ask in due(c, at, limit):
        c.execute("SAVEPOINT thread_followup")
        try:
            posted = _nudge(c, ask)
            c.execute("INSERT INTO thread_followups(message_id, conversation_id, to_actor, nudge_message_id, created_at) "
                      "VALUES(?,?,?,?,?)",
                      (ask["id"], ask["conversation_id"], ask["to_actor"], posted["id"] if posted else None, at))
            c.execute("RELEASE thread_followup")
        except Exception:
            c.execute("ROLLBACK TO thread_followup")
            c.execute("RELEASE thread_followup")
            LOG.warning("follow-up for an ask failed; it is retired without a nudge", exc_info=True)
            c.execute("INSERT INTO thread_followups(message_id, conversation_id, to_actor, nudge_message_id, created_at) "
                      "VALUES(?,?,?,NULL,?)", (ask["id"], ask["conversation_id"], ask["to_actor"], at))
            continue
        if posted:
            H.event(c, H.KEEPER, "thread.follow_up", ask["id"], {"thread": ask["conversation_id"], "to": ask["to_actor"]})
            done.append({"message": ask["id"], "thread": ask["conversation_id"], "to": ask["to_actor"]})
    return done
