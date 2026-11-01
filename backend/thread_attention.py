"""Needs attention (spec 3.6): the thread items on a person's Needs you list.

Read from the inbox (`message_deliveries`), the thread counters and the writes held for review (`org_rule_holds`), each
row checked against the per-message rule (`threads.readable`) before it is listed. An item clears by what the person
does, not by a click:
- a mention or an ask is open until the person posts in its thread after it (or it is deleted);
- an unread DM is open until the person reads past its last message from a person or one addressed to them;
- a write held for review is open until it is decided: its reviewer releases or keeps it, or the person it is held for
  withdraws it (the hold leaves `held`);
- a capped thread (the loop guard, spec 4.5) is open until its capped deliveries resume;
- a thread with a message another company's Tico has not taken is open until the send goes through, the message is
  edited or deleted, or that company leaves the thread. A paused or revoked peering lists nothing (it is already shown in
  Connections, and its messages wait for it).
A bot's mention of the person and a bot's ask are the thread's questions to the person and count; a bot's post that
names nobody is activity, not attention.

Every item is listed. The caller pages the whole list (views.needs_items), so nothing is cut here.
"""
from . import hubdb as H

ADDRESSED = ("mention", "ask", "assign", "addressed")
# The writes a thread post is checked as (backend/org_rule_check.py), and a message carried in from another install.
HELD_OPERATIONS = ("message", "reply", "federated_message")


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
    for row in _unread_dms(c, viewer, me):
        out.append(_item("thread_dm", row["id"], row["id"], _clip(row["subject"] or "Direct message"),
                         row["created"], row["from_actor"]))
    for row in _holds(c, who):
        title = "A message is held for your review" if row["role"] == "reviewer" else "A message of yours is held"
        out.append(_item("thread_held", row["id"], None, title, row["created"], row["author"]))
    for row in _capped(c):
        root = c.execute("SELECT * FROM conversations WHERE id=?", (row["root_id"],)).fetchone()
        if root and threads.readable(c, viewer, dict(root)) and threads.can_manage(c, viewer, dict(root)):
            out.append(_item("thread_capped", row["root_id"], row["root_id"],
                             _clip(root["subject"] or "Thread") + ": bot replies paused", row["since"]))
    for row in _unsent(c):
        root = c.execute("SELECT * FROM conversations WHERE id=?", (row["root_id"],)).fetchone()
        if root and threads.readable(c, viewer, dict(root)) and threads.can_manage(c, viewer, dict(root)):
            out.append(_item("thread_unsent", row["root_id"] + ":" + row["recipient"], row["root_id"],
                             _clip(root["subject"] or "Thread") + ": not sent to another company", row["since"]))
    return sorted(out, key=lambda it: str(it["created"] or ""))


def _addressed(c, me):
    """Messages a person may have to answer: a person's, a bot's ask, or a message that names this person, that they
    have not answered since in the same thread. A bot's plain post, one that names nobody, is not theirs to answer."""
    return [dict(r) for r in c.execute(
        "SELECT m.id, m.conversation_id, m.from_actor, m.kind, m.body, m.created, cv.subject FROM message_deliveries d "
        "JOIN messages m ON m.id=d.message_id JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE d.recipient=? AND d.reason IN ('mention','ask','assign','addressed') "
        "AND d.state IN ('pending','delivered','read') AND m.from_actor<>? "
        "AND (m.from_actor LIKE 'human:%' OR m.kind='ask' OR d.reason='mention') "
        "AND m.deleted_at IS NULL AND m.superseded_at IS NULL "
        "AND (m.kind<>'ask' OR m.answered_by IS NULL) "
        "AND cv.task_id IS NULL AND cv.closed_at IS NULL AND cv.archived_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.conversation_id=m.conversation_id AND r.from_actor=? "
        "AND r.created>m.created AND r.deleted_at IS NULL) "
        "ORDER BY m.created", (me, me, me)).fetchall()]


def _unread_dms(c, viewer, me):
    """Direct messages with a message past the person's read point that they may read, from a person or addressed to
    them. The item takes that message's sender and time: a later message they cannot read sets neither."""
    from . import threads
    out = []
    for cv in c.execute(
            "SELECT cv.id, cv.subject, f.last_read_seq FROM thread_followers f JOIN conversations cv ON cv.id=f.thread_id "
            "WHERE f.actor=? AND f.following=1 AND f.notify<>'muted' "
            "AND (cv.audience='personal' OR (cv.audience='participants' AND cv.parent_id IS NULL)) "
            "AND cv.closed_at IS NULL AND cv.archived_at IS NULL AND cv.last_unread_seq>f.last_read_seq "
            "ORDER BY cv.last_unread_seq DESC", (me,)).fetchall():
        if not threads.readable(c, viewer, cv["id"]):
            continue
        for m in c.execute(
                "SELECT m.* FROM messages m WHERE m.conversation_id=? AND m.seq>? AND m.from_actor<>? "
                "AND (m.from_actor LIKE 'human:%' OR m.to_actor=?) AND m.deleted_at IS NULL ORDER BY m.seq DESC",
                (cv["id"], cv["last_read_seq"], me, me)):
            if threads.readable(c, viewer, dict(m)):
                out.append({"id": cv["id"], "subject": cv["subject"], "from_actor": m["from_actor"],
                            "created": m["created"]})
                break
    return out


def _holds(c, who):
    """Writes held for review that this person reads: the ones they review, and the ones held for them (the person
    their bot reports up to, org_rule_check.represented). The item clears when the hold is decided."""
    from . import org_rule_check
    return org_rule_check.held_for(c, who, HELD_OPERATIONS)


def _unsent(c):
    """One row per thread and company whose peering is live and whose messages have not gone (`federation_threads.unsent`)."""
    from . import federation_threads
    return federation_threads.unsent(c)


def _capped(c):
    """Threads whose bot wakes the loop guard held back, one row per thread (its oldest held wake)."""
    return [dict(r) for r in c.execute(
        "SELECT d.root_id, MIN(d.created_at) AS since FROM message_deliveries d WHERE d.state='capped' "
        "AND d.root_id IS NOT NULL GROUP BY d.root_id ORDER BY since").fetchall()]
