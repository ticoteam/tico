"""Which job a newly written message queues: the one routing rule, called by every message writer in its transaction.

A message to a bot starts a run through a job (`jobs`, id = the message's id) unless:
- it is not addressed to a bot, or a bot sent it to itself;
- it is quiet (`refs.quiet`): a note, a held hand-off, a rule's parked news;
- it is live meeting history (`refs.live.kind` transcript, decision or tool): a person's spoken turns, spoken decisions
  and what a live tool did. A live ask does queue: that is how the text Tico is reached;
- its bot is run by an external agent (a Hermes profile and the other EXTERNAL_HARNESSES), which reads its inbox
  itself, unless a registered computer delivers the bot through its platform's desktop app (backend/app_delivery.py):
  then that computer's runner takes the message as a job.

The bot's state is not read: a paused bot's job waits, since runners claim only active bots' work (execution.claim).
A hub database without Store's job queue (no `jobs` table) queues nothing.

Every writer that inserts into `messages` calls `enqueue` right after its INSERT, in the same transaction: hubdb._write_message
(every say, ask, answer, notice, feed and comment), threads._carrier (a thread member's wake) and
external_sync.import_messages (always quiet). Until migration 0052 this rule was the `queue_bot_message` trigger, and the
trigger's SQL and this module agree row for row (tests/test_enqueue.py).
"""
import json
import math

from .harnesses import EXTERNAL_HARNESSES

LIVE_HISTORY = ("transcript", "decision", "tool")


def external_bot_sql(recipient):
    """SQL true when the bot `recipient` (an SQL expression holding `bot:<id>`) is reached only through its inbox."""
    return ("EXISTS (SELECT 1 FROM bot_settings WHERE bot_id=substr(" + recipient + ",5) AND "
            "harness IN (" + ",".join("'" + h + "'" for h in EXTERNAL_HARNESSES) + ") "
            "AND NOT EXISTS (SELECT 1 FROM runners r WHERE r.id=json_extract(app_delivery,'$.computer') "
            "AND r.revoked_at IS NULL))")


def _refs(value):
    if value is None:
        return {}
    refs = json.loads(value) if isinstance(value, (str, bytes)) else value
    return refs if isinstance(refs, dict) else {}


def _sql_zero(value):
    """SQLite's `coalesce(x, 0) = 0` for a JSON value: missing, null, false and numeric zero are zero; text is not.
    NaN is zero too: SQLite's json_extract reads a JSON NaN as NULL."""
    return value is None or (isinstance(value, (int, float)) and (value == 0 or math.isnan(value)))


def wanted(sender, recipient, refs):
    """Whether a message from `sender` to `recipient` with these refs starts a run, before the bot's harness is read."""
    if not isinstance(recipient, str) or recipient[:4].lower() != "bot:" or (sender or "") == recipient:
        return False
    refs = _refs(refs)
    if not _sql_zero(refs.get("quiet")):
        return False
    live = refs.get("live")
    kind = live.get("kind") if isinstance(live, dict) else None
    return not (isinstance(kind, str) and kind in LIVE_HISTORY)


def enqueue(conn, message):
    """Queue the job `message` (a just-inserted row as a dict: id, sender_id, recipient_id, refs, created_at) starts.
    Returns the job id, or None when it starts none."""
    from .hubdb import _has_table
    recipient = message.get("recipient_id")
    if not wanted(message.get("sender_id"), recipient, message.get("refs")) or not _has_table(conn, "jobs"):
        return None
    if conn.execute("SELECT " + external_bot_sql("?"), (recipient,)).fetchone()[0]:
        return None
    conn.execute("INSERT INTO jobs(id,message_id,bot_id,created_at) VALUES(?,?,?,?)",
                 (message["id"], message["id"], recipient[4:], message["created_at"]))
    return message["id"]


def enqueue_stored(conn, message_id):
    """`enqueue` for a message already in `messages`, read back by id: for a writer that inserts rows in bulk."""
    row = conn.execute("SELECT id,sender_id,recipient_id,refs,created_at FROM messages WHERE id=?",
                       (message_id,)).fetchone()
    return enqueue(conn, dict(zip(("id", "sender_id", "recipient_id", "refs", "created_at"), row, strict=True))) if row else None
