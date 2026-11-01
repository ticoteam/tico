"""Needs attention (spec 3.6): the thread items on a person's Needs you list.

Read from the inbox (`message_deliveries`) and the thread counters, in SQL, each row checked against the per-message
rule (`threads.readable`) before it is listed. An item clears by what the person does, not by a click:
- a mention or an ask is open until the person posts in its thread after it (or it is deleted);
- an unread DM is open until the person reads past its last message from a person or one addressed to them;
- a message held for the person is open until a reviewer releases it (its delivery leaves `held`);
- a capped thread (the loop guard, spec 4.5) is open until its capped deliveries resume.
Bot posts that name nobody are activity, not attention; a bot's ask counts (it is the thread's question to the person).
"""
from . import hubdb as H

ITEM_LIMIT = 50      # items per list; the rest wait behind them in Needs you's own paging
ADDRESSED = ("mention", "ask", "assign", "addressed")


def _clip(text, n=160):
    text = str(text or "").strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + "..."


def _item(kind, item_id, thread_id, title, created, requester=None, message_id=None, first_line=""):
    return {"kind": kind, "id": item_id, "message_id": message_id, "conversation_id": thread_id, "title": title,
            "first_line": first_line, "created": created, "requester": requester, "origin_actor": requester,
            "from_person": requester if H.is_human(requester) else None, "ask": None, "recommend": None,
            "person_ask": None, "ask_missing": False}


def items(c, auth, who, skip=()):
    """The thread items for `who`, skipping message ids in `skip` (asks Needs you already lists, e.g. loose asks)."""
    from . import threads
    viewer, me = threads.viewer(auth, who), who.actor
    seen, out = set(skip), []
    for row in _addressed(c, me):
        if row["id"] in seen or not threads.readable(c, viewer, row["id"]):
            continue
        seen.add(row["id"])
        kind = "thread_ask" if row["kind"] == "ask" else "thread_mention"
        first = _clip((row["body"] or "").strip().splitlines()[0] if (row["body"] or "").strip() else "")
        out.append(_item(kind, row["id"], row["conversation_id"], first or _clip(row["subject"] or "Thread"),
                         row["created"], row["from_actor"], message_id=row["id"], first_line=first))
    for row in _unread_dms(c, me):
        if threads.readable(c, viewer, row["id"]):
            out.append(_item("thread_dm", row["id"], row["id"], _clip(row["subject"] or "Direct message"),
                             row["created"], row["from_actor"]))
    for row in _held(c, me):
        if threads.readable(c, viewer, row["message_id"]):
            out.append(_item("thread_held", row["id"], row["conversation_id"], "A message to you is held for review",
                             row["created"]))
    for row in _capped(c):
        root = c.execute("SELECT * FROM conversations WHERE id=?", (row["root_id"],)).fetchone()
        if root and threads.readable(c, viewer, dict(root)) and threads.can_manage(c, viewer, dict(root)):
            out.append(_item("thread_capped", row["root_id"], row["root_id"],
                             _clip(root["subject"] or "Thread") + ": bot replies paused", row["since"]))
    return sorted(out, key=lambda it: str(it["created"] or ""))[:ITEM_LIMIT]     # the oldest wait longest


def _addressed(c, me):
    """Messages a person may have to answer: a person's, or a bot's ask, that name this person and that they have not
    answered since in the same thread. Bot replies that name them are not theirs to answer."""
    return [dict(r) for r in c.execute(
        "SELECT m.id, m.conversation_id, m.from_actor, m.kind, m.body, m.created, cv.subject FROM message_deliveries d "
        "JOIN messages m ON m.id=d.message_id JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE d.recipient=? AND d.reason IN ('mention','ask','assign','addressed') "
        "AND d.state IN ('pending','delivered','read') AND m.from_actor<>? "
        "AND (m.from_actor LIKE 'human:%' OR m.kind='ask') AND m.deleted_at IS NULL AND m.superseded_at IS NULL "
        "AND (m.kind<>'ask' OR m.answered_by IS NULL) "
        "AND cv.task_id IS NULL AND cv.closed_at IS NULL AND cv.archived_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=m.conversation_id AND r.from_actor=? "
        "AND r.created>m.created AND r.deleted_at IS NULL) "
        "ORDER BY m.created LIMIT ?", (me, me, me, ITEM_LIMIT * 2)).fetchall()]


def _unread_dms(c, me):
    """Direct messages with a message from a person (or addressed to this person) past the person's read point."""
    out = []
    candidates = c.execute(
        "SELECT cv.id, cv.subject, f.last_read_seq FROM thread_followers f JOIN conversations cv ON cv.id=f.thread_id "
        "WHERE f.actor=? AND f.following=1 AND f.notify<>'muted' AND cv.audience='personal' "
        "AND cv.closed_at IS NULL AND cv.archived_at IS NULL AND cv.last_unread_seq>f.last_read_seq "
        "ORDER BY cv.last_unread_seq DESC LIMIT ?", (me, ITEM_LIMIT * 2)).fetchall()
    for row in candidates:
        hit = c.execute(
            "SELECT m.from_actor, m.created FROM messages m WHERE m.conversation_id=? AND m.seq>? "
            "AND m.from_actor<>? AND (m.from_actor LIKE 'human:%' OR m.to_actor=?) AND m.deleted_at IS NULL "
            "ORDER BY m.seq DESC LIMIT 1", (row["id"], row["last_read_seq"], me, me)).fetchone()
        if hit:
            out.append({"id": row["id"], "subject": row["subject"], "from_actor": hit["from_actor"],
                        "created": hit["created"]})
        if len(out) >= ITEM_LIMIT:
            break
    return out


def _held(c, me):
    return [dict(r) for r in c.execute(
        "SELECT d.id, d.message_id, m.conversation_id, m.created FROM message_deliveries d "
        "JOIN messages m ON m.id=d.message_id WHERE d.recipient=? AND d.state='held' "
        "ORDER BY d.created_at LIMIT ?", (me, ITEM_LIMIT)).fetchall()]


def _capped(c):
    """Threads whose bot wakes the loop guard held back, one row per thread (its oldest held wake)."""
    return [dict(r) for r in c.execute(
        "SELECT d.root_id, MIN(d.created_at) AS since FROM message_deliveries d WHERE d.state='capped' "
        "AND d.root_id IS NOT NULL GROUP BY d.root_id ORDER BY since LIMIT ?", (ITEM_LIMIT,)).fetchall()]
