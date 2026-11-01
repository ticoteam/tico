"""The one door from federation into core's threads (backend/threads.py, owned by threads/core). Every call uses the names in
docs/dev/threads-contract.md sections 3, 6 and 11, as core is merged on threads/federation. Nothing else in federation
names core.

Inbound peer writes go through `post` as principal "keeper" with via="peer" and the origin dict. Core runs its C1 checks
only for a person or bot principal, so federation's own gate (an active, pinned peering, a member install, the namespace
check) admits a message. Core keeps the real author in refs.origin.actor; from_actor is "keeper", because core has no
remote-author column. `due()` adds to core's outside deliveries the fields federation reads: the message kind, and the
install a replicated message came from (so a replica never sends a message back to the home it came from).
"""
import json

from . import threads as CORE


def create_thread(c, principal, title):
    """A new thread in `members` audience. Returns its view (with `id`)."""
    return CORE.create(c, principal, title=title, audience="members")


def add_outside(c, thread_id, install):
    """Link a peer install as an outside destination of the thread: core writes a delivery per message to it."""
    CORE.add_outside_delivery(c, thread_id, f"peer:{install}")


def post_inbound(c, thread_id, body, *, kind, origin):
    """A replicated message. `origin` is {"origin_id", "revision", "install", "actor"}. A repeat (origin_id, revision)
    returns the existing message with `duplicate: True` and writes nothing."""
    return CORE.post(c, "keeper", thread_id, body, kind=kind, via="peer", origin=origin)


def apply_revision(c, origin, *, body, deleted):
    """A replicated edit or delete. None when nothing was applied (no such message, or an older or repeated revision)."""
    return CORE.apply_revision(c, origin, body=body, deleted=deleted)


def due(c, limit):
    """Outbound deliveries to peer installs that are waiting, oldest first, as dicts with the keys federation reads: id,
    recipient, thread_id, message_id, origin_id, revision, actor, message_kind, body, deleted, created, origin_install.
    origin_install is the install a replicated message came from, or '' for a message written here."""
    rows = []
    for row in CORE.outside_due(c, "peer:", limit):
        r = dict(row)
        found = c.execute("SELECT kind, refs_json FROM messages WHERE id=?", (r["message_id"],)).fetchone()
        kind = (found[0] if found else None) or "say"
        refs = _refs(found[1] if found else None)
        rows.append({"id": r["id"], "recipient": r["recipient"],
                     "thread_id": r.get("thread_id") or r.get("conversation_id"), "message_id": r["message_id"],
                     "origin_id": r.get("origin_id") or r["message_id"], "revision": int(r.get("revision") or 0),
                     "actor": r.get("from_actor") or "", "message_kind": kind, "body": r.get("body") or "",
                     "deleted": r.get("deleted_at") is not None, "created": r.get("created_at") or "",
                     "origin_install": str((refs.get("origin") or {}).get("install") or "")})
    return rows


def _refs(text):
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def settle(c, delivery_id, state, error=None):
    CORE.settle_delivery(c, delivery_id, state, error=error)


def delivery_revision(c, delivery_id):
    """The revision a core delivery is waiting to send (core bumps it on each edit), or None when there is no delivery."""
    row = c.execute("SELECT revision FROM message_deliveries WHERE id=?", (delivery_id,)).fetchone()
    return int(row[0]) if row else None


def audience(c, thread_id):
    """The thread's audience as core stores it, or '' when there is no such thread (a read, not a write)."""
    row = c.execute("SELECT audience FROM conversations WHERE id=?", (thread_id,)).fetchone()
    return (row[0] or "") if row else ""


def title(c, thread_id):
    row = c.execute("SELECT subject FROM conversations WHERE id=?", (thread_id,)).fetchone()
    return (row[0] or "") if row else ""


def exists(c, thread_id):
    return c.execute("SELECT 1 FROM conversations WHERE id=?", (thread_id,)).fetchone() is not None
