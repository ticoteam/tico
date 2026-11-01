"""How long a thread keeps its messages (docs/threads.md, contract §5). A root may set `conversations.retention_days`;
this worker deletes what is past it, in the root and every sub-thread, in short batches, so no write waits long behind it.

A message past its time is deleted the way its author would delete it (threads._revise): its text, its revisions and
what was copied from it go in the same transaction (mentions, reactions, search, runs not started, carriers and notes,
Slack posts not sent, stored retry answers, a sub-thread's title). The row stays as a tombstone, so a sub-thread under
it and the thread's sequence numbers still hold; its refs keep only its origin. Files are unlinked from it.

The purge is local. An outside destination already holds its own copy, so no tombstone goes out, and a copy not sent yet
never goes. What a bot's run already read lives in that run's transcript, under the transcript's own retention.

Plain code: nothing past its time reads a little and writes nothing. Each batch records one event and one audit row: how
many messages, never which words.
"""
from . import hubdb as H
from . import threads

BATCH = 200               # messages one transaction deletes
MAX_BATCHES = 10          # batches one run takes; the rest wait for the next run
MAX_ROOTS = 200           # roots one run looks at; the next run goes on after the last one

_after = {"root": ""}     # where the last run stopped (in this process: a restart starts over, which only reads)


def _due(c, root_id, cutoff):
    """Up to BATCH message ids of this root's tree past `cutoff` (a carrier has no seq and goes with its message). Each
    thread's index (messages_conversation) is read only as far as the cutoff."""
    return [r[0] for r in c.execute(
        "SELECT m.id FROM conversations s CROSS JOIN messages m ON m.conversation_id=s.id AND m.created<? "
        "WHERE s.root_id=? AND m.deleted_at IS NULL AND m.seq IS NOT NULL LIMIT ?", (cutoff, root_id, BATCH)).fetchall()]


def purge(c, root_id, ids, days):
    """Delete these messages, in the caller's transaction, skipping any that went since they were read. Returns how many."""
    gone = 0
    for message_id in ids:
        message = H.message(c, message_id)
        if not message:
            continue
        threads._revise(c, message, H.KEEPER, deleted=True, purge=True)
        origin = (message.get("refs") or {}).get("origin")
        c.execute("UPDATE messages SET refs_json=? WHERE id=?", (H._dump({"origin": origin} if origin else {}), message_id))
        c.execute("DELETE FROM message_assets WHERE message_id=?", (message_id,))
        gone += 1
    if gone:
        threads._event(c, H.KEEPER, "thread.purged", root_id, {"messages": gone})
        threads._audit(c, H.KEEPER, "thread:" + root_id, None, {"purged": gone, "retention_days": int(days)})
    return gone


def sweep(store, now=None):
    """One run: the roots with a policy, finding each batch on a read connection (the write lock is held only to delete
    it), one transaction per batch. Returns how many messages went."""
    now = now or H.now()
    with store.read() as c:
        roots = c.execute("SELECT id, retention_days FROM conversations WHERE retention_days IS NOT NULL AND id>? "
                          "ORDER BY id LIMIT ?", (_after["root"], MAX_ROOTS)).fetchall()
    _after["root"] = roots[-1]["id"] if len(roots) == MAX_ROOTS else ""
    total = batches = 0
    for root in roots:
        cutoff = H.shift(now, days=-int(root["retention_days"]))
        while batches < MAX_BATCHES:
            with store.read() as c:
                ids = _due(c, root["id"], cutoff)
            if not ids:
                break
            with store.transaction() as c:
                total += purge(c, root["id"], ids, root["retention_days"])
            batches += 1
    return total
