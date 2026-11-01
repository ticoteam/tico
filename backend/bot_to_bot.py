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

# Plain says and a bot's own notices can wait; every other kind wakes.
KINDS = ("say", "notice")
# The references a plain message carries. Anything else marks a message that is more than words.
PLAIN_REFS = frozenset({"task", "comment", "attachments", "files", "unsolicited", "turn_id", "warnings", "via", "rule"})
WAKES = ("flagged needs-action", "an ask, answer or steer", "references beyond a plain message",
         "a reply the recipient waits on", "a gate verdict")
FLAG = "needs_action"
MARK = "can_wait"
RULE = "bot_to_bot"
BACKLOG = "held-messages"


def can_wait(conn, actor, target, kind, refs, body, in_reply_to=None):
    """Whether a message from bot `actor` to bot `target` waits for the target's next run. False (wake) on
    anything it cannot read."""
    try:
        if not (str(actor).startswith("bot:") and str(target).startswith("bot:")) or kind not in KINDS:
            return False
        if not isinstance(refs, dict) or refs.get("quiet"):
            return False
        from . import wake_rules
        # The flag, the reply the recipient waits on and the verdict lines are on the always-wake list.
        if wake_rules.always_wakes(conn, actor, target, kind, refs, body, in_reply_to=in_reply_to):
            return False
        return not set(refs) - PLAIN_REFS - {FLAG}
    except Exception:  # noqa: BLE001 - fail open: a message this cannot read wakes its bot
        return False


def held_reason(msg, conn=None):
    """What the sender reads when its message was held for the recipient's next run (Dev 328), else None. On
    Oct 21 Support's escalations to DevOps were held like this and read as delivered: say it plainly. With `conn`
    the recipient is named by its slug, else by its id."""
    refs = msg.get("refs") if isinstance(msg.get("refs"), dict) else {}
    if refs.get(MARK) != RULE:
        return None
    from . import hubdb as H
    bot = H.actor_id(msg.get("recipient_id") or "")
    if conn is not None:
        bot = (H.bot(conn, bot) or {}).get("slug", bot)
    return (f"held for {bot}'s next run; nothing runs it now. Add --needs-action (needs_action: true) to wake "
            f"{bot} now, or ask with `hub question ask`.")


def hold(conn, msg, task_row=None):
    """The quiet note that takes a waiting message to the recipient's next run, whatever starts it."""
    from . import hubdb as H
    where = f"on task {task_row['title']} ({task_row['id']})" if task_row else "in a direct message"
    text = str(msg["body"] or "")
    if len(text) > H.NOTE_MAX - 400:
        text = text[:H.NOTE_MAX - 400] + "... (the rest is in the message)"
    sender = H.actor_id(msg["from_actor"])
    if H.is_bot(msg["from_actor"]):
        sender = (H.bot(conn, sender) or {}).get("slug", sender)
    body = (f"Message from {sender} {where}, which could wait for this run "
            f"(message {msg['id']}). Act on it if it asks for something.\n\n{text}")
    row = {"id": H.new_id(), "from_actor": msg["from_actor"], "to_actor": msg["to_actor"], "body": body,
           "created_at": msg["created_at"], "message_id": msg["id"]}
    conn.execute("INSERT INTO notes(id,from_actor,to_actor,body,created_at,message_id) VALUES "
                 "(:id,:from_actor,:to_actor,:body,:created_at,:message_id)", row)
    H.event(conn, msg["from_actor"], "message.can_wait", msg["id"], {"to": msg["to_actor"], "note": row["id"]})
    return row


def carried(conn, notes, at):
    """Mark the waiting messages these carried notes stand for read. Every note goes into the prompt, one in the
    run's own room too: a fresh session reads no room history and a resumed one only a recent page, so the room
    alone never proves the run saw it."""
    for item in notes:
        if item.get("message_id"):
            conn.execute("UPDATE messages SET delivered_at=coalesce(delivered_at,?), read_at=coalesce(read_at,?) "
                         "WHERE id=?", (at, at, item["message_id"]))
    return notes


def continue_backlog(conn, bot):
    """More held messages wait than one run carries (H.notes_waiting's limit): one keeper notice starts the run
    that carries the next ones, so none is left without a run to deliver it. Called after this run's notes are
    marked carried; at most one such notice waits at a time."""
    from . import hubdb as H
    left = conn.execute(f"SELECT 1 FROM notes n WHERE n.to_actor=? AND n.message_id IS NOT NULL AND "
                        f"{H.NOTE_WAITING_SQL} LIMIT 1", ("bot:" + bot,)).fetchone()
    if not left:
        return None
    pending = conn.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=? "
                           "AND j.state IN ('queued','leased') AND json_extract(m.refs,'$.wake')=?",
                           (bot, BACKLOG)).fetchone()
    if pending:
        return None
    return H.say(conn, H.KEEPER, "bot:" + bot, "More bot messages waited than one run carries; this run "
                 "carries the next ones.", kind="notice", refs={"wake": BACKLOG, "trigger": "other"})
