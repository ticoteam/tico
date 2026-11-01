"""The one door from federation into core's threads (backend/threads.py, owned by threads/core). Nothing else in federation
names core.

A carried message is written by the keeper with `as_actor`: the real remote author (`human:ana@<install>`), which core
checks is in the signer's namespace and is a guest member of the thread (spec 7.2). `origin` keeps the origin id,
revision and install. Core's C1 for a remote author is a thread_members row for the author or for `peer:<install>`.

The home's roster is mirrored here as guest rows (`<actor>@<home install>`), through core's add_member and remove_member,
so a replica shows who is in the thread. Local readers are the people an admin adds on this install (core's add_member).
A replicated message from a guest never wakes a bot: a queued run for a bot whose name carries an install is cancelled
(local bot names never contain `@`).
"""
import json

from . import org_rule_check as OR
from . import threads as CORE


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
    """The home's local members, the list a replica mirrors: people and bots on this install, sorted."""
    return sorted(r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (thread_id,))
                  if str(r[0]).startswith(("human:", "bot:")) and "@" not in str(r[0]))


def set_guests(c, thread_id, install, actors):
    """Mirror the home's roster on this replica: each actor as `actor@install`, and no other guest of that install."""
    wanted = {f"{actor}@{install}" for actor in actors}
    have = {r[0] for r in c.execute("SELECT actor FROM thread_members WHERE thread_id=?", (thread_id,))
            if str(r[0]).endswith("@" + install)}
    for actor in sorted(wanted - have):
        CORE.add_member(c, "keeper", thread_id, actor)
    for actor in sorted(have - wanted):
        CORE.remove_member(c, "keeper", thread_id, actor)
    return len(wanted ^ have)


def add_reader(c, thread_id, actor):
    """A local person the accepting admin adds to a thread on this install. Core checks the person exists."""
    return CORE.add_member(c, "keeper", thread_id, actor)




def post_inbound(c, thread_id, body, *, kind, origin):
    """A carried message, as its remote author. `origin` is {"origin_id", "revision", "install", "actor"}. A repeat
    (origin_id, revision) returns the existing message with `duplicate: True` and writes nothing."""
    view = CORE.post(c, "keeper", thread_id, body, kind=kind, via="peer", origin=origin, as_actor=origin["actor"])
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
    rules apply to remote actors as guests: nothing here is a roster person."""
    return OR.inbound(store, judge, actor, "federated_message", text, (), source="federation",
                      authenticated=f"federation:{install}", origin=f"{install}:{origin_id}",
                      place=lambda c, principal, recipients: OR.place_for(c, principal, recipients, conversation_id=thread_id))


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
