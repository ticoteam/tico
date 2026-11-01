"""The one door from federation into core's threads (backend/threads.py, owned by threads/core). Nothing else in federation
names core.

A carried message is written by the keeper with `as_actor`: the real remote author (`human:ana@<install>`), which core
checks is in the signer's namespace and is a guest member of the thread (spec 7.2). `origin` keeps the origin id,
revision and install. Core's C1 for a remote author is a thread_members row on the root for the author or for
`peer:<install>`.

The home is the membership authority. Its roster goes to every replica: its own people as `name`, and the guests it
carries for other installs as `name@<install>`. A replica mirrors the whole roster as guest rows, so a message from a
third install finds its author on every replica. Local readers are the people an admin adds on a replica (core's
add_member), and only a replica of a members thread takes them.

A replicated message from a guest never wakes a bot: a queued run for a bot whose name carries an install is cancelled
(local bot names never contain `@`).
"""
import json

from . import federation as F
from . import hubdb as H
from . import org_rule_check as OR
from . import threads as CORE
from .store import Problem


def create_thread(c, principal, title):
    """A new thread in `members` audience. Returns its view (with `id`)."""
    return CORE.create(c, principal, title=title, audience="members")


def add_outside(c, thread_id, install):
    """Link a peer install as an outside destination of the thread: core writes a delivery per message to it."""
    CORE.add_outside_delivery(c, thread_id, f"peer:{install}")


def remove_outside(c, thread_id, install):
    """Unlink a peer install: its deliveries go moot, so nothing more is sent or accepted for it as a link."""
    CORE.remove_outside_member(c, "keeper", thread_id, f"peer:{install}")


def roster(c, thread_id):
    """The members this thread lists, as the home mirrors them: its local people and bots (`human:x`, `bot:x`) and the
    guests it carries for other installs (`human:x@<install>`). Peer links are not members."""
    return sorted(r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (thread_id,))
                  if str(r[0]).startswith(("human:", "bot:")))


def set_guests(c, thread_id, home, entries):
    """Mirror the home's roster on this replica. A name without an install is the home's guest (`name@home`); a name with
    one is a guest of that install. This install's own people are not guests here (they are local rows already). Every
    guest row on the thread is replaced by the roster."""
    me = F.install(c)
    wanted = {entry if "@" in entry else f"{entry}@{home}" for entry in entries
              if not (me and entry.endswith(f"@{me}"))}
    have = {r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (thread_id,))
            if "@" in str(r[0])}
    for actor in sorted(wanted - have):
        CORE.add_member(c, "keeper", thread_id, actor)
    for actor in sorted(have - wanted):
        CORE.remove_member(c, "keeper", thread_id, actor)
    return len(wanted ^ have)


def ensure_guest(c, thread_id, actor):
    """On the home: a remote author who posts here is a guest row of the thread, so the roster carries it to the other
    replicas. True when the row was added (the roster then changed)."""
    found = c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor=?", (thread_id, actor)).fetchone()
    if found is None:
        CORE.add_member(c, "keeper", thread_id, actor)
        return True
    return False


def can_read(c, thread_id, actor):
    """Whether a local person may read a thread here, by core's own audience rule."""
    return CORE.audience_has(c, thread_id, actor)


def add_reader(c, thread_id, actor):
    """A local person the accepting admin adds to a shared copy on this install. Only a replica of a `members` thread
    takes readers: never the home's own thread, a DM, a local thread, or one this install has been removed from."""
    row = c.execute("SELECT home_install, removed_at_seq FROM federation_threads WHERE thread_id=?", (thread_id,)).fetchone()
    if row is None or row[0] == F.install(c) or row[1] is not None or audience(c, thread_id) != "members":
        raise Problem("not_replica", "Readers are added to a shared copy of a thread on this install", 409)
    return CORE.add_member(c, "keeper", thread_id, actor)


def conversation_of(c, message_id):
    """The conversation a local message is in, or None."""
    row = c.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()
    return row[0] if row else None


def post_inbound(c, thread_id, body, *, kind, origin, parent_message=None):
    """A carried message, as its remote author. `origin` is {"origin_id", "revision", "install", "actor"}. `thread_id`
    is the root's local id; `parent_message` is the local id of the message a sub-thread hangs from, or None. A repeat
    (origin_id, revision) returns the existing message with `duplicate: True` and writes nothing."""
    target = conversation_of(c, parent_message) if parent_message else thread_id
    view = CORE.post(c, "keeper", target, body, kind=kind, via="peer", origin=origin, parent_message_id=parent_message,
                     as_actor=origin["actor"])
    hold_guest_wakes(c)
    return view


def apply_revision(c, origin, *, body, deleted):
    """A carried edit or delete. None when nothing was applied (no such message, or an older or repeated revision)."""
    view = CORE.apply_revision(c, origin, body=body, deleted=deleted)
    hold_guest_wakes(c)
    return view


def hold_guest_wakes(c):
    """A remote guest never wakes a bot here: a run queued for a bot whose name carries an install is cancelled. Local
    bots (names without `@`) keep the normal wake rule."""
    c.execute("UPDATE jobs SET state='cancelled' WHERE state='queued' AND instr(bot, '@') > 0")


def post_notice(c, thread_id, text, to_actor):
    """A quiet note from the keeper to one local person in the thread, like a held message's note."""
    return CORE.post(c, "keeper", thread_id, text, kind="notice", to_actor=to_actor)


def checked_inbound(store, judge, actor, text, install, origin_id, thread_id):
    """The org-rule verdict for a message carried in from another install, held as its remote author (`inbound`). The
    rules apply to remote actors as guests: nothing here is a roster person. Called outside any write transaction: the
    judge is a model call."""
    return OR.inbound(store, judge, actor, "federated_message", text, (), source="federation",
                      authenticated=f"federation:{install}", origin=f"{install}:{origin_id}",
                      place=lambda c, principal, recipients: OR.place_for(c, principal, recipients, conversation_id=thread_id))


def _shape(c, r):
    """One waiting outside delivery as federation reads it. The message's state is read now, not when the delivery was
    queued: an edit, a delete or a restriction made after staging is what gets sent (or not sent)."""
    found = c.execute("SELECT m.kind, m.refs_json, m.restricted, m.revision AS message_revision, m.created, c.root_id, "
                      "pm.origin_id AS parent_origin, pm.id AS parent_id FROM messages m "
                      "JOIN conversations c ON c.id=m.conversation_id "
                      "LEFT JOIN messages pm ON pm.id=c.parent_message_id WHERE m.id=?", (r["message_id"],)).fetchone()
    refs = _refs(found["refs_json"] if found else None)
    return {"id": r["id"], "recipient": r["recipient"],
            "thread_id": (found["root_id"] if found else None) or r.get("thread_id"),
            "message_id": r["message_id"], "origin_id": r.get("origin_id") or r["message_id"],
            "revision": int(found["message_revision"] if found else (r.get("revision") or 0)),
            "actor": r.get("from_actor") or "",
            "message_kind": (found["kind"] if found else None) or "say",
            "body": r.get("body") or "", "deleted": r.get("deleted_at") is not None,
            "created": (found["created"] if found else None) or r.get("created_at") or "",
            "origin_install": str((refs.get("origin") or {}).get("install") or ""),
            "restricted": bool(found["restricted"]) if found else True,
            "parent_message": ((found["parent_origin"] or found["parent_id"]) if found else None) or ""}


def due(c, limit):
    """Outbound deliveries to peer installs that are waiting, oldest first, as dicts with the keys federation reads: id,
    recipient, thread_id (the shared root), message_id, origin_id, revision, actor, message_kind, body, deleted, created,
    origin_install, restricted, parent_message (the origin id of the message a sub-thread hangs from, or '')."""
    return [_shape(c, dict(row)) for row in CORE.outside_due(c, "peer:", limit)]


def current(c, delivery_id):
    """A pending peer delivery as due() shapes it, read now, or None when it is no longer waiting."""
    row = c.execute("SELECT d.id, d.recipient, d.message_id, m.origin_id, m.body, m.from_actor, m.deleted_at "
                    "FROM message_deliveries d JOIN messages m ON m.id=d.message_id "
                    "WHERE d.id=? AND d.state='pending'", (delivery_id,)).fetchone()
    return _shape(c, dict(row)) if row else None


def _refs(text):
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def outside_subtree(c, root_id, after, limit):
    """Messages in the sub-threads of a root, numbered after `after`, oldest first. Core writes outside deliveries for a
    root's own messages only, so a shared thread's sub-thread messages are queued from here."""
    return [dict(r) for r in c.execute(
        "SELECT m.id, m.seq, m.conversation_id, m.from_actor, m.revision, m.created, m.restricted, m.deleted_at "
        "FROM messages m JOIN conversations s ON s.id=m.conversation_id "
        "WHERE s.root_id=? AND s.id<>? AND m.seq>? ORDER BY m.seq LIMIT ?", (root_id, root_id, after, limit)).fetchall()]


def outside_edited(c, root_id, limit):
    """Federated messages in a shared thread whose latest revision is past the one sent: an edit or a delete made after it
    went out. Sub-threads and the root's own messages alike, so an edit never depends on core queueing it again."""
    return [dict(r) for r in c.execute(
        "SELECT m.id, m.seq, m.conversation_id, m.from_actor, m.revision, m.created, m.restricted, m.deleted_at "
        "FROM federation_messages fm JOIN messages m ON m.id=fm.message_id "
        "WHERE fm.thread_id=? AND fm.message_id<>'' AND fm.sent_revision IS NOT NULL AND m.revision>fm.sent_revision "
        "ORDER BY m.seq LIMIT ?", (root_id, limit)).fetchall()]


def queue_outside(c, message, recipient, conversation_id, root_id):
    """One outside delivery for a message core did not queue (a sub-thread's, or an edit of one already sent). A delivery
    already at this revision or past it is left alone; an older one is brought up to the message's revision and waits."""
    revision = int(message.get("revision") or 0)
    found = c.execute("SELECT id, revision FROM message_deliveries WHERE message_id=? AND recipient=?",
                      (message["id"], recipient)).fetchone()
    if found is None:
        CORE._insert_delivery(c, message, recipient, {"id": conversation_id}, {"id": root_id}, "none", "outside")
        return
    if int(found["revision"] or 0) >= revision:
        return
    c.execute("UPDATE message_deliveries SET state='pending', revision=?, next_attempt_at=NULL, updated_at=? WHERE id=?",
              (revision, H.now(), found["id"]))


def moot_pending(c, delivery_id):
    """A delivery still waiting goes moot (nothing will be sent for it); one already sent or settled is left alone."""
    c.execute("UPDATE message_deliveries SET state='moot', updated_at=? WHERE id=? AND state='pending'", (H.now(), delivery_id))


def settle(c, delivery_id, state, error=None):
    CORE.settle_delivery(c, delivery_id, state, error=error)


def audience(c, thread_id):
    """The thread's audience as core stores it, or '' when there is no such thread (a read, not a write)."""
    row = c.execute("SELECT audience FROM conversations WHERE id=?", (thread_id,)).fetchone()
    return (row[0] or "") if row else ""


def title(c, thread_id):
    row = c.execute("SELECT subject FROM conversations WHERE id=?", (thread_id,)).fetchone()
    return (row[0] or "") if row else ""


def exists(c, thread_id):
    return c.execute("SELECT 1 FROM conversations WHERE id=?", (thread_id,)).fetchone() is not None
