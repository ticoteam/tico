"""Bot-to-bot messages wait for the recipient's next run unless the sender flags them as needing action now
(token saving, Dev 149).

A plain message one bot sends another, a direct message or a task comment, starts no run. H.say writes it to
its room quietly (`refs.quiet`, so store.QUEUE_TRIGGER queues no job) and marks it `refs.can_wait`; `hold` then
leaves a quiet note linked to it, which the recipient's next run carries whatever starts it (execution.claim),
in the order sent, and that run marks the message read. Nothing is dropped: a note whose carrying run fails
waits for the next one (H.NOTE_WAITING_SQL), and the message stays in its room for that room's next turn.

It wakes the recipient at once, as before, when (WAKES):
- the sender flags it: `needs_action` (`hub message send --needs-action`, `hub task comment --needs-action`);
- it is not a plain message: an ask, an answer, a steer;
- it carries anything but a plain message's references (an approval, questions, a command, a batch, ...);
- it answers the recipient's own ask, a message the recipient flagged needs-action, or one it waits on (wait_s);
- it is a gate verdict (a `VERDICT: approve|changes PR #<n> head <sha>` line, Review 1/2, QA, Security).
Fail open: anything this cannot read (references that are not an object, a flag that is not true or false)
wakes. A person's message, the keeper's notices (assignments, gate requests, approvals, a blocker done) and
everything on token saving 2/9's always-wake list never reach this rule: only bot-to-bot messages do.
"""

import re

# Plain says and a bot's own notices can wait; every other kind wakes.
KINDS = ("say", "notice")
# The references a plain message carries. Anything else marks a message that is more than words.
PLAIN_REFS = frozenset({"task", "comment", "attachments", "files", "unsolicited", "turn_id", "warnings", "via"})
VERDICT = re.compile(r"^\s*VERDICT:\s*(approve|changes)\s+PR\s+#\d+\s+head\s+[0-9a-f]{40}\s*$", re.I | re.M)
WAKES = ("flagged needs-action", "an ask, answer or steer", "references beyond a plain message",
         "a reply the recipient waits on", "a gate verdict")
FLAG = "needs_action"
MARK = "can_wait"
RULE = "bot_to_bot"


def can_wait(conn, actor, target, kind, refs, body, in_reply_to=None):
    """Whether a message from bot `actor` to bot `target` waits for the target's next run. False (wake) on
    anything it cannot read."""
    try:
        if not (str(actor).startswith("bot:") and str(target).startswith("bot:")) or kind not in KINDS:
            return False
        if not isinstance(refs, dict) or refs.get("quiet"):
            return False
        flag = refs.get(FLAG, False)
        if flag is not False:
            return False
        if set(refs) - PLAIN_REFS - {FLAG}:
            return False
        if VERDICT.search(str(body or "")):
            return False
        if in_reply_to:
            parent = conn.execute("SELECT from_actor,kind,refs_json,wait_s FROM messages WHERE id=?",
                                  (in_reply_to,)).fetchone()
            if not parent:
                return False
            if parent["from_actor"] == target and (parent["kind"] == "ask" or parent["wait_s"]
                                                   or '"' + FLAG + '": true' in str(parent["refs_json"] or "")):
                return False
        return True
    except Exception:  # noqa: BLE001 - fail open: a message this cannot read wakes its bot
        return False


def hold(conn, msg, task_row=None):
    """The quiet note that takes a waiting message to the recipient's next run, whatever starts it."""
    from . import hubdb as H
    where = f"on task {task_row['title']} ({task_row['id']})" if task_row else "in a direct message"
    text = str(msg["body"] or "")
    if len(text) > H.NOTE_MAX - 400:
        text = text[:H.NOTE_MAX - 400] + "... (the rest is in the message)"
    body = (f"Message from {H.actor_id(msg['from_actor'])} {where}, which could wait for this run "
            f"(message {msg['id']}). Act on it if it asks for something.\n\n{text}")
    row = {"id": H.new_id(), "from_actor": msg["from_actor"], "to_actor": msg["to_actor"], "body": body,
           "created": msg["created"], "message_id": msg["id"]}
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created,message_id) VALUES "
                 "(:id,:from_actor,:to_actor,:body,:created,:message_id)", row)
    H.event(conn, msg["from_actor"], "message.can_wait", msg["id"], {"to": msg["to_actor"], "note": row["id"]})
    return row


def carried(conn, notes, conversation_id, at):
    """Mark the waiting messages these carried notes stand for read. A message in the run's own room is in the
    room history the run already reads, so its note is not repeated: the returned list leaves it out."""
    out = []
    for item in notes:
        mid = item.get("message_id")
        if mid:
            conn.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?), read_at=coalesce(read_at,?) "
                         "WHERE id=?", (at, at, mid))
            row = conn.execute("SELECT conversation_id FROM messages WHERE id=?", (mid,)).fetchone()
            if row and row["conversation_id"] == conversation_id:
                continue
        out.append(item)
    return out
