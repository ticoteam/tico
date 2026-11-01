"""The one door from federation into core's threads (backend/threads.py, owned by threads/core). Every call uses the names
in docs/dev/threads-contract.md sections 3 and 11. `module()` is the only import; a test puts a stand-in in its place
until core's module lands, so the rest of federation never names core directly.

Assumed from the contract, to confirm with core: `threads.post(..., via="peer", origin=...)` takes the principal
"keeper" for an inbound peer write and applies the audience check to `origin["actor"]` (a guest); `outside_due` rows carry
the message's fields with the delivery id and thread id. `due()` reads those rows by name, with the fallbacks noted there.
"""
import importlib


def module():
    return importlib.import_module("backend.threads")


def create_thread(c, principal, title):
    """A new thread in `members` audience. Returns its view (with `id`)."""
    return module().create(c, principal, title=title, audience="members")


def add_outside(c, thread_id, install):
    """Link a peer install as an outside destination of the thread: core writes a delivery per message to it."""
    module().add_outside_delivery(c, thread_id, f"peer:{install}")


def post_inbound(c, thread_id, body, *, kind, origin):
    """A replicated message, through core's write door. A repeat `(origin_id, revision)` returns the existing message."""
    return module().post(c, "keeper", thread_id, body, kind=kind, via="peer", origin=origin)


def apply_revision(c, origin, *, body, deleted):
    """A replicated edit or delete. None when core has no message for the origin id."""
    return module().apply_revision(c, origin, body=body, deleted=deleted)


def due(c, limit):
    """Outbound deliveries to peer installs that are waiting, oldest first, as dicts with the keys federation reads:
    id, recipient, thread_id, message_id, origin_id, revision, actor, message_kind, body, deleted, created."""
    rows = []
    for row in module().outside_due(c, "peer:", limit):
        r = dict(row)
        message_id = r.get("message_id") or r.get("id")
        rows.append({"id": r.get("delivery_id") or r.get("id"), "recipient": r.get("recipient"),
                     "thread_id": r.get("thread_id"), "message_id": message_id,
                     "origin_id": r.get("origin_id") or message_id, "revision": int(r.get("revision") or 0),
                     "actor": r.get("actor") or r.get("from_actor") or "",
                     "message_kind": r.get("message_kind") or "say", "body": r.get("body") or "",
                     "deleted": bool(r.get("deleted")), "created": r.get("created") or ""})
    return rows


def settle(c, delivery_id, state, error=None):
    module().settle_delivery(c, delivery_id, state, error=error)


def audience(c, thread_id):
    """The thread's audience as core stores it, or '' when there is no such thread (a read, not a write)."""
    row = c.execute("SELECT audience FROM conversations WHERE id=?", (thread_id,)).fetchone()
    return (row[0] or "") if row else ""


def title(c, thread_id):
    row = c.execute("SELECT subject FROM conversations WHERE id=?", (thread_id,)).fetchone()
    return (row[0] or "") if row else ""


def exists(c, thread_id):
    return c.execute("SELECT 1 FROM conversations WHERE id=?", (thread_id,)).fetchone() is not None
