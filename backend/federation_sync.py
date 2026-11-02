"""Shared threads between installs: what the envelopes mean. backend/federation_relay.py moves the bytes; this module decides
what they do, inside the caller's transaction.

A thread is shared by its home, the install it was first made on. The home numbers the thread's stream (`home_seq`, one
per item) and is the membership authority: only it adds or removes an install. A replica (another install in the thread)
posts to the home, which numbers each message and relays every item to every active member.

Items carry their author's signature, so a member can check a message the home relayed from a third install against
that install's key from HQ. The outer envelope from the home is checked against the pinned peer key.

A member gets a message only when it was in the thread when the message was made: the core sequence of the message (or of
the message it replies to) must be past the sequence the member joined at. A member the home numbers an item for that it
may not have gets a skip in its place, so its stream stays unbroken.

What a replica sends is built when it is sent, from the message as it is then: an edit or delete after staging is what
goes out, and a message deleted before it ever went out is never sent. Restricted messages (task or meeting provenance)
are never sent. The org rules and the injection screen run before the write (the relay's preflight); a verdict that
could not be checked defers the envelope, so nothing is written or acked for it.

Outcomes of `receive` are plain words: applied, duplicate, gap, or the reason an envelope was refused. A refusal is
recorded (so a replay of it is refused too) and never raises, so a catch-up request queued with it is kept. `Deferred`
is the one outcome that writes nothing: the envelope is tried again on a later pull.
"""
import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from . import federation as F
from . import federation_envelope as E
from . import federation_threads as T
from . import threads as CORE
from . import hubdb as H
from . import org_rule_check as OR
from . import screening
from .store import Problem

CATCH_UP_EVERY = 300        # seconds between catch-up requests for one thread
CATCH_UP_BATCH = 200        # items one page of a catch-up queues; the next page goes when this one has left
CATCH_UP_QUIET = 86_400     # seconds a shared thread stays quiet before its replica asks whether it missed the end
MESSAGE_KINDS = ("say", "ask", "answer")          # what a remote author may write (core: say, ask or answer)
RETRY_FIRST = 15            # seconds before a failed send is tried again; doubled per attempt
BACKOFF_MAX = 300
ROSTER_NAME = re.compile(r"(human|bot):[^@\s]{1,200}(@gi_[0-9a-f]{24})?")
REASON_NOTES = {"rules": "Tico held this message, so it did not reach the thread.",
                "screening": "Tico held this message, so it did not reach the thread.",
                "namespace": "Tico did not take this message: its author is not from the install that sent it.",
                "origin": "Tico did not take this message: its id is already used by another message.",
                "unknown_message": "Tico did not take this edit: the other company has no message it changes.",
                "unknown_parent": "Tico did not take this message: the message it replies to is not shared with this install.",
                "unknown_thread": "Tico did not take this message: the thread is not shared with this install."}


class Deferred(Exception):
    """Not decided yet: the org rules or the injection screen could not check a carried text, the verdict moved, or the
    peer is paused. Nothing is written or acked for the envelope, and the pull stops there until a later try."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def stamp():
    return H.now()


# ----------------------------------------------------------------------------- rows
def thread_local(c, thread_id):
    row = c.execute("SELECT * FROM federation_threads WHERE thread_id=?", (thread_id,)).fetchone()
    return dict(row) if row else None


def thread_of_home(c, home, origin_id):
    row = c.execute("SELECT * FROM federation_threads WHERE home_install=? AND origin_id=?", (home, origin_id)).fetchone()
    return dict(row) if row else None


def member_of(c, thread_id, install_id):
    row = c.execute("SELECT * FROM federation_members WHERE thread_id=? AND install=?", (thread_id, install_id)).fetchone()
    return dict(row) if row else None


def members_of(c, thread_id):
    return [dict(r) for r in c.execute("SELECT * FROM federation_members WHERE thread_id=? ORDER BY install", (thread_id,))]


def message_row(c, origin_id):
    row = c.execute("SELECT * FROM federation_messages WHERE origin_id=?", (origin_id,)).fetchone()
    return dict(row) if row else None


def queued(c, kind, *, delivery_id=None):
    return c.execute("SELECT 1 FROM federation_outbox_items WHERE kind=? AND delivery_id=?", (kind, delivery_id)).fetchone() is not None


def valid_text(value, limit):
    return isinstance(value, str) and len(value) <= limit


# ----------------------------------------------------------------------------- outbox
def queue(c, *, recipient, kind, item_id=None, delivery_id=None, payload=None, thread_origin=""):
    """A row to send to `recipient`. `thread_origin` is the root's origin id: the lane the row waits in."""
    c.execute("INSERT OR IGNORE INTO federation_outbox_items(id,recipient,kind,delivery_id,item_id,payload,attempts,next_attempt_at,"
              "last_error,created_at,thread_origin) VALUES(?,?,?,?,?,?,0,0,'',?,?)",
              (H.new_id(), recipient, kind, delivery_id, item_id, payload, stamp(), thread_origin))


def enqueue(c, thread, item):
    """The item to every active member. A member the item is not for gets a skip in its place (see envelope)."""
    for member in members_of(c, thread["thread_id"]):
        if member["status"] == "active":
            queue(c, recipient=member["install"], kind="item", item_id=item["id"], thread_origin=thread["origin_id"])


def anchor_seq(c, origin_id):
    """The core sequence that decides whether a member may have a message: its parent's when it replies in a sub-thread,
    else its own. It is fixed when the message is made, so a member who joined after that never gets it or its edits."""
    row = c.execute("SELECT COALESCE(pm.seq, m.seq) FROM federation_messages fm JOIN messages m ON m.id=fm.message_id "
                    "JOIN conversations s ON s.id=m.conversation_id LEFT JOIN messages pm ON pm.id=s.parent_message_id "
                    "WHERE fm.origin_id=?", (origin_id,)).fetchone()
    return row[0] if row else None


def eligible(c, member, origin_id):
    anchor = anchor_seq(c, origin_id)
    return anchor is not None and anchor > member["joined_message_seq"]


def request_catch_up(c, now, thread, after):
    """Asks the home for the stream after `after`, once per CATCH_UP_EVERY for the thread."""
    last = thread["catch_up_requested_at"]
    if last is not None and now - last < CATCH_UP_EVERY:
        return
    c.execute("UPDATE federation_threads SET catch_up_requested_at=?, updated_at=? WHERE id=?", (now, stamp(), thread["id"]))
    payload = E.payload_bytes({"type": "catch_up", "thread": thread["origin_id"], "after": int(after)})
    queue(c, recipient=thread["home_install"], kind="catch_up", payload=payload, thread_origin=thread["origin_id"])


# ----------------------------------------------------------------------------- the home's numbering
def insert_item(c, thread, *, kind, origin, revision, author, envelope_id, sent_at, key_id, signature, body):
    """One numbered item of the home's stream. The number is the thread's next one; the unique index keeps it single."""
    seq = c.execute("SELECT high_seq FROM federation_threads WHERE id=?", (thread["id"],)).fetchone()[0] + 1
    item_id = H.new_id()
    c.execute("INSERT INTO federation_items(id,thread_id,home_seq,kind,origin_id,revision,author,envelope_id,sent_at,key_id,"
              "signature,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (item_id, thread["thread_id"], seq, kind, origin, revision, author, envelope_id, int(sent_at), key_id,
               signature, body, stamp()))
    c.execute("UPDATE federation_threads SET high_seq=?, updated_at=? WHERE id=?", (seq, stamp(), thread["id"]))
    return {"id": item_id, "thread_id": thread["thread_id"], "home_seq": seq, "kind": kind, "origin_id": origin,
            "revision": revision}


def sign_item(c, vault, thread, *, kind, payload, origin, revision, now):
    """A home-authored item: this install signs the payload as its author, to itself as the home."""
    home = F.install(c)
    current = F.key(c)
    envelope_id, sent_at = E.new_envelope_id(), int(now)
    body = E.payload_bytes(payload)
    signature = E.sign(F.private(c, vault, current), home, home, envelope_id, sent_at, body)
    return insert_item(c, thread, kind=kind, origin=origin, revision=revision, author=home, envelope_id=envelope_id,
                       sent_at=sent_at, key_id=current["key_id"], signature=signature, body=body)


def message_payload(thread_origin, origin, revision, actor, message_kind, body, deleted, created, parent="", first=False):
    """`first`: this is the first its author sends of the message, at whatever revision it has by then. Without it, a
    revision past 0 is an edit, and an edit of a message the receiver does not know is never made into one."""
    return {"type": "message", "thread": thread_origin, "origin_id": origin, "revision": int(revision), "actor": actor,
            "message_kind": message_kind, "body": "" if deleted else (body or ""), "deleted": bool(deleted),
            "created": created or "", "parent": parent or "", **({"first": True} if first else {})}


def membership_payload(thread, change, install, members, title=""):
    return {"type": "membership", "thread": thread["origin_id"], "change": change, "install": install, "title": title,
            "members": members}


def refresh_roster(c, vault, thread, now):
    """The home's roster goes out as a numbered membership item when it differs from the last one sent, so every member's
    guest rows follow the home's member list. True when an item was sent."""
    roster = T.roster(c, thread["thread_id"])
    last = c.execute("SELECT payload FROM federation_items WHERE thread_id=? AND kind='membership' "
                     "ORDER BY home_seq DESC LIMIT 1", (thread["thread_id"],)).fetchone()
    seen = (E.parse_payload(bytes(last[0])) or {}).get("members") if last else None
    if seen == roster:
        return False
    item = sign_item(c, vault, thread, kind="membership", now=now, origin=None, revision=0,
                     payload={**membership_payload(thread, "roster", "", roster), "company": F.company(c)})
    enqueue(c, thread, item)
    return True


def sync_rosters(c, vault, now):
    me = F.install(c)
    for row in c.execute("SELECT * FROM federation_threads WHERE home_install=? AND removed_at_seq IS NULL", (me,)).fetchall():
        refresh_roster(c, vault, dict(row), now)


def home_message(c, vault, thread, row, now):
    """The home's item for one of its messages, or a revision of it, numbered and relayed to the active members. None when
    it is not the home's to send: another install's, restricted, already sent at this revision, or deleted before the
    thread's members ever had it. A revision the members never had the first of goes out whole (its current text)."""
    home = F.install(c)
    origin, revision = row["origin_id"], row["revision"]
    existing = c.execute("SELECT * FROM federation_items WHERE origin_id=? AND revision=?", (origin, revision)).fetchone()
    if existing:
        return dict(existing)
    if withheld(c, row["message_id"]):
        return None
    actor = E.qualify(row["actor"], home) if row["actor"] else ""
    if E.install_of(actor) != home:                  # a message numbered by another install is never signed here
        return None
    known = message_row(c, origin)
    if known is not None and revision <= (known["sent_revision"] or 0):
        return None
    if known is None and row["deleted"]:
        return None
    payload = message_payload(thread["origin_id"], origin, revision, actor, row["message_kind"],
                              "" if row["deleted"] else row["body"],
                              row["deleted"], row["created"], parent=row.get("parent_message") or "")
    item = sign_item(c, vault, thread, kind="message", payload=payload, origin=origin, revision=revision, now=now)
    if known is None:
        c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,sent_revision,"
                  "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (H.new_id(), origin, row["message_id"], thread["thread_id"], actor, revision, item["home_seq"], revision,
                   stamp(), stamp()))
    else:
        c.execute("UPDATE federation_messages SET revision=?, sent_revision=?, updated_at=? WHERE origin_id=?",
                  (revision, revision, stamp(), origin))
    enqueue(c, thread, item)
    return item


def withheld(c, message_id):
    """Whether a message must stay in this install. `restricted` only asks for the per-message check (a reply, a file, a
    ref that might name a task), so most flagged messages are fine to send: what is withheld is what a reader with no
    standing could not read (the rule the thread's history is shown by), or any message of a thread tied to a private
    task. Only the message's text and its author leave, so the check covers what does."""
    row = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    if row is None:
        return True
    message = dict(row)
    if message["deleted_at"] or not message["restricted"]:
        return False       # a tombstone carries no text, so it leaves whatever the message's access now is
    conv = c.execute("SELECT * FROM conversations WHERE id=?", (message["conversation_id"],)).fetchone()
    if conv is not None and CORE.private_task_thread(c, dict(conv)):
        return True
    return CORE._narrower_than_thread(c, message)


def home_withheld(c, origin_id):
    """Whether a message the home numbered must be skipped now: its privacy changed after it was staged (a task went
    private). Checked when the item is sent, so a home reply is held to the same rule a replica's is."""
    known = message_row(c, origin_id)
    return known is None or withheld(c, known["message_id"])


def _local(actor, me):
    """A person or bot of this install. The keeper is the system, not a person: its notes are never federated."""
    return bool(actor) and not actor.startswith("keeper") and E.install_of(E.qualify(actor, me)) == me


def subtrees(c, me, limit):
    """Sub-thread messages become outside deliveries: core writes those only for a root's own messages. A home sends its
    people's sub-thread messages to its active members; a replica sends its people's to the home. New messages go out past
    the thread's watermark, and a message already sent goes out again when it is edited or deleted."""
    for row in c.execute("SELECT * FROM federation_threads WHERE removed_at_seq IS NULL").fetchall():
        thread = dict(row)
        root = thread["thread_id"]
        if thread["home_install"] == me:
            peers = [f"peer:{m['install']}" for m in members_of(c, root) if m["status"] == "active"]
        else:
            peers = [f"peer:{thread['home_install']}"]
        top = thread["subtree_seq"]
        for message in T.outside_subtree(c, root, thread["subtree_seq"], limit):
            top = max(top, message["seq"])
            if withheld(c, message["id"]) or message["deleted_at"] or not _local(message["from_actor"], me):
                continue
            for recipient in peers:
                T.queue_outside(c, message, recipient, message["conversation_id"], root)
        if top != thread["subtree_seq"]:
            c.execute("UPDATE federation_threads SET subtree_seq=?, updated_at=? WHERE id=?", (top, stamp(), thread["id"]))
        for message in T.outside_edited(c, root, limit):
            if withheld(c, message["id"]) or not _local(message["from_actor"], me):
                continue
            for recipient in peers:
                T.queue_outside(c, message, recipient, message["conversation_id"], root)


def stage(c, vault, now, limit=100):
    """Core's waiting deliveries to peer installs, turned into this install's outbox. The home numbers a message for its
    members; a replica queues its own posts for the home. Anything not to be sent is settled here, so core stops asking."""
    me = F.install(c)
    if not me or F.key(c) is None:
        return 0
    sync_rosters(c, vault, now)
    subtrees(c, me, limit)
    continue_catch_ups(c)
    repair_gaps(c, me, now)
    staged = 0
    for row in T.due(c, limit):
        delivery = row["id"]
        target = (row["recipient"] or "").removeprefix("peer:")
        thread = thread_local(c, row["thread_id"]) if row["thread_id"] else None
        if thread is None or row["message_kind"] not in MESSAGE_KINDS or withheld(c, row["message_id"]) \
                or not _local(row["actor"], me):
            T.settle(c, delivery, "moot")
            continue
        if thread["home_install"] == me:
            member = member_of(c, thread["thread_id"], target)
            if target == me or member is None or member["status"] != "active":
                T.settle(c, delivery, "moot")
                continue
            item = home_message(c, vault, thread, row, now)
            if item is None:
                T.settle(c, delivery, "moot")
                continue
            # Numbered and on its way, not there yet: `sent` or `refused` settles the delivery when HQ answers.
            queue(c, recipient=target, kind="item", item_id=item["id"], thread_origin=thread["origin_id"])
            staged += 1
            continue
        if row["origin_install"]:
            T.settle(c, delivery, "moot")                 # it came from another install: the home has it already
            continue
        if queued(c, "delivery", delivery_id=delivery):
            continue
        actor = E.qualify(row["actor"], me) if row["actor"] else ""
        if target != thread["home_install"] or E.install_of(actor) != me:
            T.settle(c, delivery, "moot")                 # not this install's message, or not the home's to receive
            continue
        origin, revision = row["origin_id"], row["revision"]
        known = message_row(c, origin)
        if known is not None and known["sent_revision"] is not None and revision <= known["sent_revision"]:
            T.settle(c, delivery, "moot")
            continue
        if known is None:
            c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,sent_revision,"
                      "created_at,updated_at) VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                      (H.new_id(), origin, row["message_id"], thread["thread_id"], actor, revision, stamp(), stamp()))
        queue(c, recipient=thread["home_install"], kind="delivery", delivery_id=delivery, thread_origin=thread["origin_id"])
        staged += 1
    return staged


# ----------------------------------------------------------------------------- membership, from the home
def share(c, vault, now, thread_id, install_id):
    """The home adds an install to one of its threads. The install gets the stream from this item on, and the messages
    made from now on (the messages before it joined are never sent to it, nor their edits)."""
    me = F.install(c)
    if not me:
        raise Problem("not_set_up", "Connect the Tico Gateway first: federation needs this install's id", 409)
    if not T.exists(c, thread_id):
        raise Problem("not_found", "No such thread", 404)
    if T.is_sub_thread(c, thread_id):
        raise Problem("sub_thread", "Share the thread itself: its sub-threads go with it", 422)
    if T.audience(c, thread_id) != "members":
        raise Problem("audience", "Only a thread whose audience is members can be shared with another install", 422)
    if F.active(c, install_id) is None:
        raise Problem("not_peered", "Make the peering with that install first: it must be active", 409)
    if T.slack_linked(c, thread_id):
        raise Problem("slack_linked", "This thread is linked to a Slack channel. Unlink it first: another company's words "
                      "must not reach your workspace's channel", 409)
    top = c.execute("SELECT COALESCE(MAX(seq),0) FROM messages").fetchone()[0]
    row = thread_local(c, thread_id)
    if row is None:
        c.execute("INSERT INTO federation_threads(id,thread_id,home_install,origin_id,applied_through,high_seq,removed_at_seq,"
                  "catch_up_requested_at,subtree_seq,created_at,updated_at) VALUES(?,?,?,?,0,0,NULL,NULL,?,?,?)",
                  (H.new_id(), thread_id, me, thread_id, top, stamp(), stamp()))
        row = thread_local(c, thread_id)
    elif row["home_install"] != me:
        raise Problem("not_home", "This thread's home is another install, so only that install can add to it", 409)
    member = member_of(c, thread_id, install_id)
    if member and member["status"] == "active":
        raise Problem("already_shared", "That install is already in the thread", 409)
    item = sign_item(c, vault, row, kind="membership", now=now, origin=None, revision=0,
                     payload={"type": "membership", "thread": thread_id, "change": "added", "install": install_id,
                              "title": T.title(c, thread_id), "members": T.roster(c, thread_id),
                              "company": F.company(c)})
    if member:
        c.execute("UPDATE federation_members SET status='active', joined_at_seq=?, joined_message_seq=?, removed_at_seq=NULL, "
                  "updated_at=? WHERE id=?", (item["home_seq"], top, stamp(), member["id"]))
    else:
        c.execute("INSERT INTO federation_members(id,thread_id,install,status,joined_at_seq,joined_message_seq,removed_at_seq,"
                  "created_at,updated_at) VALUES(?,?,?,'active',?,?,NULL,?,?)",
                  (H.new_id(), thread_id, install_id, item["home_seq"], top, stamp(), stamp()))
    T.add_outside(c, thread_id, install_id)
    enqueue(c, thread_local(c, thread_id), item)
    return {"thread_id": thread_id, "install": install_id, "joined_at_seq": item["home_seq"]}


def unshare(c, vault, now, thread_id, install_id):
    """The home removes an install. It gets the removal as its last item; nothing after that number is sent to it."""
    me = F.install(c)
    row = thread_local(c, thread_id)
    if row is None or row["home_install"] != me:
        raise Problem("not_home", "Only the thread's home can remove an install from it", 409)
    member = member_of(c, thread_id, install_id)
    if member is None or member["status"] != "active":
        raise Problem("not_found", "That install is not in the thread", 404)
    item = sign_item(c, vault, row, kind="membership", now=now, origin=None, revision=0,
                     payload={"type": "membership", "thread": thread_id, "change": "removed", "install": install_id,
                              "title": "", "members": T.roster(c, thread_id),
                              "company": F.company(c)})
    c.execute("UPDATE federation_members SET status='removed', removed_at_seq=?, updated_at=? WHERE id=?",
              (item["home_seq"], stamp(), member["id"]))
    T.remove_outside(c, thread_id, install_id)
    enqueue(c, thread_local(c, thread_id), item)
    queue(c, recipient=install_id, kind="item", item_id=item["id"], thread_origin=row["origin_id"])
    return {"thread_id": thread_id, "install": install_id, "removed_at_seq": item["home_seq"]}


# ----------------------------------------------------------------------------- inbound
def author_key(c, me, sender, peer, author, key_id, fetched):
    """The raw public key that signed an item's author signature, or None: this install's own (old keys stay in the
    table), the pinned key of the sender, or HQ's key for the author (the directory, or `fetched` for this envelope)."""
    if author == me:
        row = c.execute("SELECT public_key FROM federation_keys WHERE key_id=?", (key_id,)).fetchone()
        return base64.b64decode(row[0]) if row else None
    if author == sender:
        return base64.b64decode(peer["peer_public_key"]) if peer and peer["peer_key_id"] == key_id else None
    row = c.execute("SELECT public_key FROM federation_directory_entries WHERE install=? AND key_id=?", (author, key_id)).fetchone()
    if row:
        return base64.b64decode(row[0])
    found = fetched.get(author)
    return base64.b64decode(found["public_key"]) if found and found["key_id"] == key_id else None


def screen_key(install, actor, origin_id, revision, text):
    """The key a carried text's preflight verdicts are stored under, so the write finds the ones for its own text."""
    return (install, actor, origin_id, revision, hashlib.sha256(text.encode()).hexdigest())


def receive(c, now, sender, envelope_id, headers, body, received_at, fetched, gate=None, vault=None):
    """One envelope the HQ pull handed over, inside the caller's transaction. `fetched` holds keys the caller looked up
    outside it, {install: {key_id, public_key}}, which are kept in the directory. `gate(actor, text, install, origin_id,
    revision, thread_id)` returns the (org verdict, injection verdict) the relay's preflight made for a carried text (None
    checks nothing); it raises Deferred for a text it has no verdict for. `vault` signs the roster items a message needs.
    Returns the outcome."""
    me = F.install(c)
    if not me:
        return "not_set_up"
    if c.execute("SELECT 1 FROM federation_receipts WHERE peer_install=? AND envelope_id=?", (sender, envelope_id)).fetchone():
        return "replay"
    for install, found in fetched.items():
        c.execute("INSERT OR IGNORE INTO federation_directory_entries(install,key_id,public_key,fetched_at) VALUES(?,?,?,?)",
                  (install, found["key_id"], found["public_key"], stamp()))
    c.execute("SAVEPOINT federation_envelope")
    try:
        outcome = _receive(c, now, me, sender, envelope_id, headers, body, received_at, fetched, gate, vault)
    except Problem:                     # core refused a write: nothing from this envelope is kept, and it is recorded invalid
        c.execute("ROLLBACK TO federation_envelope")
        outcome = "invalid"
    except Exception:                   # deferred or unexpected: nothing from this envelope is kept, and it is not recorded
        c.execute("ROLLBACK TO federation_envelope")
        c.execute("RELEASE federation_envelope")
        raise
    c.execute("RELEASE federation_envelope")
    c.execute("INSERT INTO federation_receipts(id,peer_install,envelope_id,outcome,received_at) VALUES(?,?,?,?,?)",
              (H.new_id(), sender, envelope_id, outcome, stamp()))
    return outcome


def _receive(c, now, me, sender, envelope_id, headers, body, received_at, fetched, gate, vault):
    peer = F.peering(c, sender)
    if peer is None or peer["status"] == "revoked":
        return "no_peering"
    if peer["status"] != "active":
        return "not_active"
    if peer["paused_at"]:
        raise Deferred("paused")                        # held here until the peer is resumed
    if headers.get("x-tico-key-id") != peer["peer_key_id"]:
        return "key_mismatch"
    sent = int(headers.get("x-tico-sent-at") or 0)
    if abs(int(received_at) - sent) > E.SKEW:
        return "stale"
    signature = headers.get("x-tico-signature") or ""
    if not E.verify(base64.b64decode(peer["peer_public_key"]), signature, E.signed_text(sender, me, envelope_id, sent, body)):
        return "bad_signature"
    payload = E.parse_payload(body)
    if payload is None:
        return "invalid"
    kind = payload.get("type")
    if kind == "message":
        return on_message(c, now, me, sender, payload, envelope_id, sent, signature, body, gate, vault)
    if kind == "catch_up":
        return on_catch_up(c, me, sender, payload)
    if kind == "item":
        if payload.get("skip") is True:
            return on_skip(c, now, sender, payload)
        return on_item(c, now, me, sender, peer, payload, fetched, gate)
    if kind == "refusal":
        return on_refusal(c, me, sender, payload)
    return "invalid"


def checked_message(payload):
    """The message fields of a payload, or None when one is missing or the wrong kind. A message is never deleted at
    revision 0 (a delete is an edit)."""
    origin, revision, actor = payload.get("origin_id"), payload.get("revision"), payload.get("actor")
    text, kind, deleted = payload.get("body", ""), payload.get("message_kind"), payload.get("deleted", False)
    parent = payload.get("parent") or ""
    if not (valid_text(origin, 200) and origin and type(revision) is int and 0 <= revision <= 1_000_000
            and valid_text(actor, 300) and valid_text(text, E.MAX_BODY) and kind in MESSAGE_KINDS
            and type(deleted) is bool and valid_text(payload.get("thread"), 200) and valid_text(parent, 200)
            and not (deleted and revision == 0)):
        return None
    return {"origin_id": origin, "revision": revision, "actor": actor, "body": text, "message_kind": kind,
            "deleted": deleted, "parent": parent, "first": payload.get("first") is True}


def parent_local(c, thread, parent_origin):
    """The local id of the message a carried reply hangs from: "" for a root message, None when the parent is not shared
    here (so the reply is refused)."""
    if not parent_origin:
        return ""
    row = message_row(c, parent_origin)
    if row is None or row["thread_id"] != thread["thread_id"] or not row["message_id"]:
        return None
    return row["message_id"]


def record_refused(c, known, origin, root, actor, revision):
    """A refused first message is kept with no local message, at the revision refused: a repeat of it is refused the same
    way, without another check. A later revision is a new text, so it is checked afresh, and a reviewer can release a hold."""
    if known is not None:
        c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
        return
    c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,sent_revision,"
              "created_at,updated_at) VALUES(?,?,'',?,?,?,NULL,NULL,?,?)", (H.new_id(), origin, root, actor, revision,
                                                                           stamp(), stamp()))


def record_posted(c, known, origin, message_id, root, actor, revision, seq):
    """A first message now has its local message: a new row, or the refused row it replaces."""
    if known is not None:
        c.execute("UPDATE federation_messages SET message_id=?, revision=?, home_seq=?, updated_at=? WHERE origin_id=?",
                  (message_id, revision, seq, stamp(), origin))
        return
    c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,sent_revision,"
              "created_at,updated_at) VALUES(?,?,?,?,?,?,?,NULL,?,?)",
              (H.new_id(), origin, message_id, root, actor, revision, seq, stamp(), stamp()))


def on_message(c, now, me, sender, payload, envelope_id, sent, signature, body, gate=None, vault=None):
    """A replica's post, sent to the home: checked, numbered, stored, posted into the home's thread and relayed. A refusal
    (rules, screening, namespace, origin, unknown thread or parent) goes back to the sender as a signed receipt."""
    thread = thread_of_home(c, me, payload.get("thread"))
    if thread is None:
        return refuse(c, sender, payload, "unknown_thread")
    member = member_of(c, thread["thread_id"], sender)
    if member is None or member["status"] != "active":
        return "not_member"
    fields = checked_message(payload)
    if fields is None:
        return "invalid"
    if E.install_of(fields["actor"]) != sender:
        return refuse(c, sender, payload, "namespace")
    origin, revision, actor, root = fields["origin_id"], fields["revision"], fields["actor"], thread["thread_id"]
    peer = F.peering(c, sender)
    known = message_row(c, origin)
    same = known is not None and known["author"] == actor and known["thread_id"] == root
    if revision == 0 and known is not None:
        return "duplicate" if same else refuse(c, sender, payload, "origin")
    if known is not None and not same:                 # an edit by someone who did not write the message
        return "unknown_message"
    if known is None and revision > 0 and fields["deleted"]:
        return "unknown_message"
    if known is None and revision > 0 and not fields["first"]:
        return refuse(c, sender, payload, "unknown_message")     # an edit is never made into a message
    fresh = known is None or known["message_id"] == ""           # no local message yet: this would be the first
    if known is not None and revision <= known["revision"]:
        return refuse(c, sender, payload, "rules") if fresh else "duplicate"
    OR.supersede_federated(c, origin, revision)
    parent = parent_local(c, thread, fields["parent"]) if fresh else ""
    if parent is None:
        return refuse(c, sender, payload, "unknown_parent")
    if fresh and fields["deleted"]:                              # taken back before it was ever taken here
        record_refused(c, known, origin, root, actor, revision)
        return "applied"
    replay = {"sender": sender, "actor": actor, "kind": fields["message_kind"], "parent": fields["parent"],
              "envelope_id": envelope_id, "sent_at": sent, "key_id": peer["peer_key_id"], "signature": signature,
              "payload_b64": base64.b64encode(bytes(body)).decode("ascii")}
    text, reason = ("", None) if fields["deleted"] else screen(c, gate, actor, fields["body"], sender, origin, revision, root,
                                                               replay)
    if reason:
        if fresh:
            record_refused(c, known, origin, root, actor, revision)
        return refuse(c, sender, payload, reason)
    if fresh:
        if T.ensure_guest(c, root, actor) and vault is not None:
            refresh_roster(c, vault, thread, now)         # the roster carries the new guest before the message does
        view = T.post_inbound(c, root, text, kind=fields["message_kind"],
                              origin={"origin_id": origin, "revision": revision, "install": sender, "actor": actor},
                              parent_message=parent or None)
        item = insert_item(c, thread, kind="message", origin=origin, revision=revision, author=sender, envelope_id=envelope_id,
                           sent_at=sent, key_id=peer["peer_key_id"], signature=signature, body=body)
        record_posted(c, known, origin, view["id"], root, actor, revision, item["home_seq"])
    else:
        view = T.apply_revision(c, {"origin_id": origin, "revision": revision, "install": sender, "actor": actor},
                                body=text, deleted=fields["deleted"])
        if view is None:
            return "unknown_message"
        item = insert_item(c, thread, kind="message", origin=origin, revision=revision, author=sender,
                           envelope_id=envelope_id, sent_at=sent, key_id=peer["peer_key_id"], signature=signature, body=body)
        c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
    enqueue(c, thread, item)
    return "applied"


def on_catch_up(c, me, sender, payload):
    """A member asks for the stream after a number: queued again, from the member's join on. A removed member gets the
    items up to its removal and no more. Items the member may not have are sent as skips, so the stream has no holes."""
    thread = thread_of_home(c, me, payload.get("thread"))
    if thread is None:
        return "unknown_thread"
    member = member_of(c, thread["thread_id"], sender)
    after = payload.get("after")
    if member is None or type(after) is not int or after < 0:
        return "not_member" if member is None else "invalid"
    start = max(after, member["joined_at_seq"] - 1)
    if member["catch_up_after"] is not None:
        start = min(start, member["catch_up_after"])
    catch_up_page(c, thread, {**member, "catch_up_after": start})
    return "applied"


def catch_up_page(c, thread, member):
    """Queues one page of a member's catch-up and stores how far it got, or clears that when the page reaches the end. A
    removed member's pages end at its removal."""
    after = max(member["catch_up_after"], member["joined_at_seq"] - 1)
    bound = member["removed_at_seq"] if member["status"] == "removed" else None
    items = c.execute("SELECT id,home_seq FROM federation_items WHERE thread_id=? AND home_seq>? "
                      "AND (? IS NULL OR home_seq<=?) ORDER BY home_seq LIMIT ?",
                      (thread["thread_id"], after, bound, bound, CATCH_UP_BATCH + 1)).fetchall()
    for item in items[:CATCH_UP_BATCH]:
        queue(c, recipient=member["install"], kind="item", item_id=item["id"], thread_origin=thread["origin_id"])
    reached = items[CATCH_UP_BATCH - 1]["home_seq"] if len(items) > CATCH_UP_BATCH else None
    c.execute("UPDATE federation_members SET catch_up_after=?, updated_at=? WHERE id=?", (reached, stamp(), member["id"]))


def continue_catch_ups(c):
    """The next page for each member still catching up, once its last page has left: the outbox never holds more than a
    page of catch-up for a lane, and paging does not wait for the member to ask again, however far behind it is."""
    rows = c.execute("SELECT m.* FROM federation_members m JOIN federation_threads t ON t.thread_id=m.thread_id "
                     "WHERE m.catch_up_after IS NOT NULL AND NOT EXISTS (SELECT 1 FROM federation_outbox_items o "
                     "WHERE o.recipient=m.install AND o.thread_origin=t.origin_id AND o.kind='item')").fetchall()
    for row in rows:
        catch_up_page(c, thread_local(c, row["thread_id"]), dict(row))


def gap(c, now, thread, seq):
    """An item past the next number: the stream's end is at least `seq`, and the items before it are asked for now. A
    request the throttle holds back goes later, from `repair_gaps`, even when nothing more ever arrives."""
    c.execute("UPDATE federation_threads SET high_seq=max(high_seq,?), updated_at=? WHERE id=?", (seq, stamp(), thread["id"]))
    request_catch_up(c, now, thread, thread["applied_through"])
    return "gap"


def repair_gaps(c, me, now, limit=50):
    """A replica asks again for what it has not got: the rest of a gap it saw, or, on a thread quiet for a day, whatever
    came last and never reached it. The answer to the second is usually nothing. One request waits per thread."""
    quiet = (datetime.now(timezone.utc) - timedelta(seconds=CATCH_UP_QUIET)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    rows = c.execute("SELECT * FROM federation_threads t WHERE home_install<>? AND removed_at_seq IS NULL "
                     "AND (high_seq>applied_through OR updated_at<?) "
                     "AND (catch_up_requested_at IS NULL OR catch_up_requested_at<=?) AND NOT EXISTS (SELECT 1 FROM "
                     "federation_outbox_items o WHERE o.kind='catch_up' AND o.recipient=t.home_install "
                     "AND o.thread_origin=t.origin_id) ORDER BY updated_at LIMIT ?",
                     (me, quiet, now - CATCH_UP_EVERY, limit)).fetchall()
    for row in rows:
        request_catch_up(c, now, dict(row), row["applied_through"])


def step(c, now, thread, seq):
    """Moves a thread's applied stream on by one number: a gap asks for the items before it."""
    if thread["removed_at_seq"] is not None and seq > thread["removed_at_seq"]:
        return "removed"
    if seq <= thread["applied_through"]:
        return "duplicate"
    if seq > thread["applied_through"] + 1:
        return gap(c, now, thread, seq)
    advance(c, thread, seq)
    return "applied"


def on_skip(c, now, sender, payload):
    """The home's word that an item is not for this install (a message it was not in the thread for): the stream moves on."""
    thread = thread_of_home(c, sender, payload.get("thread")) if valid_text(payload.get("thread"), 200) else None
    if thread is None:
        return "unknown_thread"
    seq = payload.get("home_seq")
    if type(seq) is not int or seq < 1:
        return "invalid"
    return step(c, now, thread, seq)


def on_item(c, now, me, sender, peer, wrapper, fetched, gate=None):
    """An item from the home: its author's signature checked, then applied in the home's order."""
    item_type, seq, author = wrapper.get("item_type"), wrapper.get("home_seq"), wrapper.get("author")
    item_envelope, item_sent, key_id = wrapper.get("envelope_id"), wrapper.get("sent_at"), wrapper.get("key_id")
    signature, encoded, origin_thread = wrapper.get("signature"), wrapper.get("payload_b64"), wrapper.get("thread")
    if not (item_type in ("message", "membership") and type(seq) is int and seq >= 1 and E.INSTALL.fullmatch(str(author))
            and E.ENVELOPE_ID.fullmatch(str(item_envelope)) and type(item_sent) is int and E.KEY_ID.fullmatch(str(key_id))
            and isinstance(signature, str) and isinstance(encoded, str) and valid_text(origin_thread, 200)):
        return "invalid"
    try:
        inner = base64.b64decode(encoded, validate=True)
    except ValueError:
        return "invalid"
    if len(inner) > E.MAX_BODY:
        return "invalid"
    raw = author_key(c, me, sender, peer, author, key_id, fetched)
    if raw is None:
        return "unknown_author"
    if not E.verify(raw, signature, E.signed_text(author, sender, item_envelope, item_sent, inner)):
        return "bad_signature"
    payload = E.parse_payload(inner)
    if payload is None or payload.get("type") != item_type:
        return "invalid"
    thread = thread_of_home(c, sender, origin_thread)
    if item_type == "membership":
        change = payload.get("change")
        if author != sender or change not in ("added", "removed", "roster"):
            return "namespace"
        if change != "roster" and not E.INSTALL.fullmatch(str(payload.get("install"))):
            return "namespace"
        return on_membership(c, now, me, sender, thread, seq, payload)
    if E.install_of(payload.get("actor")) != author:
        return "namespace"
    if thread is None:
        return "unknown_thread"
    if thread["removed_at_seq"] is not None and seq > thread["removed_at_seq"]:
        return "removed"
    fields = checked_message(payload)
    if fields is None:
        return "invalid"
    fields["first"] = wrapper.get("first") is True      # the home's word: this is the stream's first of this message
    if seq > thread["applied_through"] + 1:
        return gap(c, now, thread, seq)
    outcome = apply_message(c, me, thread, author, seq, fields, gate, sender)
    if outcome == "unknown_message":
        request_catch_up(c, now, thread, 0)             # the message's create comes back with the next catch-up
    if seq > thread["applied_through"]:
        advance(c, thread, seq)
    return outcome


def advance(c, thread, seq):
    c.execute("UPDATE federation_threads SET applied_through=?, high_seq=max(high_seq,?), updated_at=? WHERE id=?",
              (seq, seq, stamp(), thread["id"]))


def apply_message(c, me, thread, author, seq, fields, gate=None, sender=None):
    """One message from the stream into the local thread, through core's door and the org rules (for a message another
    install wrote). A message refused here is recorded with no local message, so a catch-up never screens that text again.
    An edit of a message not known here is never made into one: the catch-up brings the message first. Works for a number
    already passed too, so a message a replica lost comes back on a catch-up."""
    origin, revision, actor, root = fields["origin_id"], fields["revision"], fields["actor"], thread["thread_id"]
    known = message_row(c, origin)
    carried = author != me
    same = known is not None and known["author"] == actor and known["thread_id"] == root
    if same and not carried and known["home_seq"] is None and revision <= known["revision"]:
        c.execute("UPDATE federation_messages SET home_seq=?, updated_at=? WHERE origin_id=?", (seq, stamp(), origin))
        return "applied"                                  # this install's own message, back from the home with its number
    if revision == 0 and known is not None:
        return "duplicate" if same else "origin"
    if revision > 0:
        if known is not None and not same:
            return "unknown_message"
        if known is None and (fields["deleted"] or not fields["first"]):
            return "unknown_message"
        if known is not None and revision <= known["revision"]:
            return "rules" if known["message_id"] == "" else "duplicate"   # refused when it came: nothing to edit
    OR.supersede_federated(c, origin, revision)
    fresh = known is None or known["message_id"] == ""   # no local message yet: this would be the first
    replay = {"sender": sender, "actor": actor, "kind": fields["message_kind"], "parent": fields["parent"], "seq": seq}
    if fresh:
        parent = parent_local(c, thread, fields["parent"])
        if parent is None:
            refuse(c, sender, {"thread": thread["origin_id"], "origin_id": origin, "revision": revision}, "unknown_parent")
            return "unknown_parent"
        if fields["deleted"]:                            # taken back before it was ever taken here
            record_refused(c, known, origin, root, actor, revision)
            return "applied"
        text, reason = (fields["body"], None) if not carried else screen(c, gate, actor, fields["body"], author, origin,
                                                                         revision, root, replay)
        if reason:
            record_refused(c, known, origin, root, actor, revision)
            refuse(c, sender, {"thread": thread["origin_id"], "origin_id": origin, "revision": revision}, reason)
            return reason
        view = T.post_inbound(c, root, text, kind=fields["message_kind"],
                              origin={"origin_id": origin, "revision": revision, "install": author, "actor": actor},
                              parent_message=parent or None)
        record_posted(c, known, origin, view["id"], root, actor, revision, seq)
        return "applied"
    if carried and not fields["deleted"]:
        text, reason = screen(c, gate, actor, fields["body"], author, origin, revision, root, replay)
    else:
        text, reason = fields["body"], None
    if reason:
        refuse(c, sender, {"thread": thread["origin_id"], "origin_id": origin, "revision": revision}, reason)
        return reason
    if carried:
        view = T.apply_revision(c, {"origin_id": origin, "revision": revision, "install": author, "actor": actor},
                                body=text, deleted=fields["deleted"])
        if view is None:
            return "unknown_message"
    c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
    return "applied"


def on_membership(c, now, me, sender, thread, seq, payload):
    change = payload.get("change")
    F.remember_company(c, sender, payload.get("company"))
    if change == "roster":
        if thread is None:
            return "unknown_thread"
        return on_roster(c, now, sender, thread, seq, payload)
    target = payload.get("install")
    members = roster_of(payload)
    if members is None:
        return "invalid"
    if target == me and change == "added":
        if thread is None:
            view = T.create_thread(c, "keeper", title=str(payload.get("title") or "")[:200])
            T.add_outside(c, view["id"], sender)
            T.set_guests(c, view["id"], sender, members)
            top = c.execute("SELECT COALESCE(MAX(seq),0) FROM messages").fetchone()[0]
            c.execute("INSERT INTO federation_threads(id,thread_id,home_install,origin_id,applied_through,high_seq,removed_at_seq,"
                      "catch_up_requested_at,subtree_seq,created_at,updated_at) VALUES(?,?,?,?,?,?,NULL,NULL,?,?,?)",
                      (H.new_id(), view["id"], sender, payload["thread"], seq, seq, top, stamp(), stamp()))
            accepted_by = (F.peering(c, sender) or {}).get("approved_by") or ""
            if accepted_by and "@" not in accepted_by:          # the local admin who accepted the peering reads it
                T.add_reader(c, view["id"], accepted_by)
            return "applied"
        if thread["removed_at_seq"] is None and seq <= thread["applied_through"]:
            return "duplicate"
        T.add_outside(c, thread["thread_id"], sender)
        T.set_guests(c, thread["thread_id"], sender, members)
        c.execute("UPDATE federation_threads SET removed_at_seq=NULL, applied_through=max(applied_through,?), "
                  "high_seq=max(high_seq,?), updated_at=? WHERE id=?", (seq, seq, stamp(), thread["id"]))
        return "applied"
    if thread is None:
        return "unknown_thread"
    if target == me and change == "removed":
        if seq > thread["applied_through"] + 1:          # the items before the removal come first
            return gap(c, now, thread, seq)
        c.execute("UPDATE federation_threads SET removed_at_seq=?, applied_through=max(applied_through,?), "
                  "high_seq=max(high_seq,?), updated_at=? WHERE id=?", (seq, seq, seq, stamp(), thread["id"]))
        T.remove_outside(c, thread["thread_id"], sender)
        return "applied"
    if seq <= thread["applied_through"]:
        return "duplicate"
    if seq > thread["applied_through"] + 1:
        return gap(c, now, thread, seq)
    T.set_guests(c, thread["thread_id"], sender, members)
    advance(c, thread, seq)
    return "applied"


# ----------------------------------------------------------------------------- screening, refusals, rosters
def screen(c, gate, actor, text, install, origin_id, revision, thread_id, replay=None):
    """(text to write, refusal reason or None) for one carried text. The checks ran before this write, outside it (the
    relay's preflight): the injection screen's `hold` refuses and its `warn` puts a line before the text, and the org
    rules decide here (a Block writes nothing and leaves a hold a reviewer can release, a warning is written with its
    line). The injection screen goes first, so a hold only ever carries a text that passed it. `replay` is what `release`
    needs of the envelope, kept on the hold. A verdict that could not be checked, or that moved, defers the envelope."""
    if gate is None:
        return text, None
    org, injection = gate(actor, text, install, origin_id, revision, thread_id)
    if injection and injection.get("action") == "hold":
        return None, "screening"
    warning = screening.warning_line(injection) if injection and injection.get("action") == "warn" else ""
    if OR.holds(org) and OR.federated_hold(c, origin_id, revision):
        return None, "rules"                              # a catch-up brought it again: the first hold stands
    outcome = OR.apply(c, org, text, {**(replay or {}), "install": install, "origin_id": origin_id, "revision": revision,
                                      "thread_id": thread_id, "warning": warning})
    if outcome and "moved" in outcome:
        raise Deferred("moved")
    if outcome and "held" in outcome:
        return None, "rules"
    text = OR.warned_text(outcome, text)
    return (warning + "\n" + text if warning else text), None


def stays_held(why):
    return Problem("changed", why + " It stays held.", 409)


def release(c, vault, now, bot, hold, replay, gate=None):
    """Send anyway for a message another install sent: the write the check stopped, run now as its recorded author (a
    guest here, `bot`), in this install's thread. The hold's reviewer and nonce are the check, so nothing is screened
    again; the envelope's own checks are, as they stand now. The home relays the author's signed envelope as it came, so
    every other member can still check it against the author's key. A refusal keeps the hold, its nonce unspent."""
    origin, revision, actor, root = replay["origin_id"], int(replay["revision"]), replay["actor"], replay["thread_id"]
    sender, author = replay["sender"], replay["install"]
    thread, known = thread_local(c, root), message_row(c, origin)
    if bot.actor != actor or thread is None or thread["removed_at_seq"] is not None:
        raise stays_held("This thread is no longer shared with that company.")
    if F.active(c, sender) is None:
        raise stays_held("The connection with that company is paused or ended.")
    if known is None or known["author"] != actor or known["thread_id"] != root:
        raise stays_held("This message changed.")
    fresh = known["message_id"] == ""
    if revision != known["revision"] if fresh else revision <= known["revision"]:
        raise stays_held("A newer version of this message came.")
    home = thread["home_install"] == F.install(c)
    if home:
        member = member_of(c, root, sender)
        if member is None or member["status"] != "active":
            raise stays_held("This thread is no longer shared with that company.")
        if F.peering(c, sender)["peer_key_id"] != replay["key_id"]:
            raise stays_held("That company's key changed, so its envelope can no longer be checked.")
    stored = json.loads(hold["payload_json"] or "{}").get("text") or ""
    text = OR.warned_text(gate, stored)
    text = replay["warning"] + "\n" + text if replay.get("warning") else text
    carried = {"origin_id": origin, "revision": revision, "install": author, "actor": actor}
    if fresh:
        parent = parent_local(c, thread, replay.get("parent") or "")
        if parent is None:
            raise stays_held("The message it replies to is not in this thread.")
        if home and T.ensure_guest(c, root, actor) and vault is not None:
            refresh_roster(c, vault, thread, now)         # the roster carries the new guest before the message does
        view = T.post_inbound(c, root, text, kind=replay["kind"], origin=carried, parent_message=parent or None)
    else:
        view = T.apply_revision(c, carried, body=text, deleted=False)
        if view is None:
            raise stays_held("This message changed.")
    item = None
    if home:
        item = insert_item(c, thread, kind="message", origin=origin, revision=revision, author=author,
                           envelope_id=replay["envelope_id"], sent_at=replay["sent_at"], key_id=replay["key_id"],
                           signature=replay["signature"], body=base64.b64decode(replay["payload_b64"]))
    if fresh:
        record_posted(c, known, origin, view["id"], root, actor, revision, item["home_seq"] if item else replay.get("seq"))
    else:
        c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
    if item:
        enqueue(c, thread, item)
    return {"id": view["id"], "thread_id": root}


def refusal_bytes(thread, origin, revision, reason):
    return E.payload_bytes({"type": "refusal", "thread": str(thread or "")[:200], "origin_id": origin,
                            "revision": int(revision) if type(revision) is int else 0, "reason": reason})


def refuse(c, to, payload, reason):
    """A signed refusal back to the install that sent a message this install will not take: it names the message (origin
    id and revision) and why. The sender posts a quiet note to the message's author. Returns the reason."""
    origin = payload.get("origin_id")
    if isinstance(origin, str) and origin:
        queue(c, recipient=to, kind="refusal", thread_origin=str(payload.get("thread") or "")[:200],
              payload=refusal_bytes(payload.get("thread"), origin, payload.get("revision"),
                                                                    reason))
    return reason


def on_refusal(c, me, sender, payload):
    """A receiver refused a message this install sent, or relayed. The author's own install posts the quiet note to the
    local author, unless that person can no longer read the thread (then nothing is posted and the outcome is
    `refused`); a refusal from another install is passed on to the install the message came from."""
    origin = payload.get("origin_id")
    if not isinstance(origin, str) or not origin:
        return "invalid"
    row = message_row(c, origin)
    if row is None or row["message_id"] == "":
        return "unknown_message"
    reason = str(payload.get("reason") or "")
    author_install = E.install_of(row["author"])
    if author_install == me:
        author = row["author"].rsplit("@", 1)[0]
        if not T.can_read(c, row["thread_id"], author):      # they can no longer read the thread: nobody to tell
            return "refused"
        T.post_notice(c, row["thread_id"], REASON_NOTES.get(reason, REASON_NOTES["rules"]), author)
        return "applied"
    if author_install and author_install != sender:
        queue(c, recipient=author_install, kind="refusal", thread_origin=str(payload.get("thread") or "")[:200],
              payload=refusal_bytes(payload.get("thread"), origin, payload.get("revision"), reason))
        return "relayed"
    return "unknown_message"


def roster_of(payload):
    """The members a membership item carries (`human:x`, `bot:x`, or a guest `human:x@gi_...`), or None when the list is
    missing or has a name that is not one."""
    members = payload.get("members")
    if not isinstance(members, list) or any(not isinstance(m, str) or len(m) > 300 or not ROSTER_NAME.fullmatch(m)
                                            for m in members):
        return None
    return members


def on_roster(c, now, sender, thread, seq, payload):
    """The home's roster, numbered like every item: this replica's guest rows for the home match it."""
    members = roster_of(payload)
    if members is None:
        return "invalid"
    if thread["removed_at_seq"] is not None and seq > thread["removed_at_seq"]:
        return "removed"
    if seq <= thread["applied_through"]:
        return "duplicate"
    if seq > thread["applied_through"] + 1:
        return gap(c, now, thread, seq)
    T.set_guests(c, thread["thread_id"], sender, members)
    advance(c, thread, seq)
    return "applied"


# ----------------------------------------------------------------------------- held inbound envelopes
def lane_of(body):
    """The root an envelope's payload names (its origin id), or '' when it names none. A lane is per peer and per root:
    a root's numbers and edits keep their order, and another root or peer never waits for them."""
    payload = E.parse_payload(body)
    value = payload.get("thread") if payload else None
    return value if valid_text(value, 200) else ""


def lane_waiting(c, peer, lane):
    """Whether this peer's envelopes for this root are held here: a later one waits behind them."""
    return c.execute("SELECT 1 FROM federation_deferrals WHERE peer_install=? AND thread_origin=? LIMIT 1",
                     (peer, lane)).fetchone() is not None


def hold(c, now, peer, lane, envelope_id, headers, body, received_at, reason):
    """Keeps an envelope this install cannot apply yet, in arrival order. It is acked to HQ once it is held here, so the
    mailbox moves on. The lane waits for it; `held_heads` tries it again when it is due."""
    c.execute("INSERT OR IGNORE INTO federation_deferrals(id,peer_install,thread_origin,envelope_id,headers,body,"
              "received_at,reason,attempts,next_attempt_at,created_at) VALUES(?,?,?,?,?,?,?,?,0,?,?)",
              (H.new_id(), peer, lane, envelope_id, json.dumps(headers, sort_keys=True, separators=(",", ":")), body,
               received_at, reason, now, stamp()))


def held_heads(c, now, limit=50):
    """Each lane's earliest held envelope, when it is due. Only that one is tried next: a lane stops at its first envelope
    that still cannot be applied, while the other lanes go on."""
    return [dict(r) for r in c.execute(
        "SELECT h.* FROM federation_deferrals h WHERE h.next_attempt_at<=? AND h.rowid=(SELECT MIN(i.rowid) FROM "
        "federation_deferrals i WHERE i.peer_install=h.peer_install AND i.thread_origin=h.thread_origin) "
        "ORDER BY h.rowid LIMIT ?", (now, limit)).fetchall()]


def held_again(c, held, reason, now):
    attempts = held["attempts"] + 1
    wait = min(BACKOFF_MAX, RETRY_FIRST * 2 ** min(attempts, 8))
    c.execute("UPDATE federation_deferrals SET attempts=?, reason=?, next_attempt_at=? WHERE id=?",
              (attempts, reason, now + wait, held["id"]))


def held_done(c, held_id):
    c.execute("DELETE FROM federation_deferrals WHERE id=?", (held_id,))


# ----------------------------------------------------------------------------- outbound, to HQ
def due_outbox(c, now, limit=50):
    """The first waiting row of each (recipient, root) lane, when it is due. A later row of a lane waits behind it, so a
    root's stream reaches each peer in order, while other lanes are not held back."""
    return [dict(r) for r in c.execute(
        "SELECT o.* FROM federation_outbox_items o WHERE o.next_attempt_at<=? AND o.rowid=(SELECT MIN(i.rowid) FROM "
        "federation_outbox_items i WHERE i.recipient=o.recipient AND i.thread_origin=o.thread_origin) "
        "ORDER BY o.rowid LIMIT ?", (now, limit)).fetchall()]


def staged_payload(c, row, me):
    """A delivery's payload, read now: the message as it is at send time, so an edit or delete made after it was staged is
    what goes out. (bytes, origin, revision), or None when nothing is to be sent: the delivery is no longer pending, the
    message is restricted or not this install's to send, it was deleted before it ever went out, or this revision went out
    already. A payload too large is settled as failed."""
    cur = T.current(c, row["delivery_id"])
    if cur is None or not cur["thread_id"]:
        return None
    thread = thread_local(c, cur["thread_id"])
    if (thread is None or thread["home_install"] == me or withheld(c, cur["message_id"]) or cur["origin_install"]
            or cur["message_kind"] not in MESSAGE_KINDS):
        return None
    actor = E.qualify(cur["actor"], me) if cur["actor"] else ""
    if E.install_of(actor) != me or cur["recipient"] != f"peer:{thread['home_install']}":
        return None
    origin, revision = cur["origin_id"], cur["revision"]
    known = message_row(c, origin)
    sent_revision = known["sent_revision"] if known is not None else None
    if cur["deleted"] and sent_revision is None:
        return None                                      # it never went out: there is nothing to take back
    if sent_revision is not None and revision <= sent_revision:
        return None
    body = E.payload_bytes(message_payload(thread["origin_id"], origin, revision, actor, cur["message_kind"],
                                           "" if cur["deleted"] else cur["body"],
                                           cur["deleted"], cur["created"], parent=cur["parent_message"],
                                           first=sent_revision is None))
    if len(body) > E.MAX_BODY:
        T.settle(c, row["delivery_id"], "failed", error="too_large")
        return None
    return body, origin, revision


def envelope(c, vault, row, now):
    """What to post for one outbox row, signed now by this install, or None when it is no longer to be sent."""
    me = F.install(c)
    recipient = row["recipient"]
    origin = revision = None
    skip = False
    if row["kind"] == "item":
        item = c.execute("SELECT * FROM federation_items WHERE id=?", (row["item_id"],)).fetchone()
        if item is None:
            return None
        member = member_of(c, item["thread_id"], recipient)
        if member is None or (member["status"] == "removed" and (member["removed_at_seq"] is None
                                                               or item["home_seq"] > member["removed_at_seq"])):
            return None
        thread = thread_local(c, item["thread_id"])
        if item["kind"] == "message" and (not eligible(c, member, item["origin_id"])
                                          or home_withheld(c, item["origin_id"])):
            skip = True
            body = E.payload_bytes({"type": "item", "thread": thread["origin_id"], "home_seq": item["home_seq"], "skip": True})
        else:
            first = item["kind"] == "message" and item["home_seq"] == c.execute(
                "SELECT MIN(home_seq) FROM federation_items WHERE origin_id=?", (item["origin_id"],)).fetchone()[0]
            body = E.payload_bytes({"type": "item", "thread": thread["origin_id"], "home_seq": item["home_seq"],
                                    "item_type": item["kind"], "author": item["author"], "envelope_id": item["envelope_id"],
                                    "sent_at": item["sent_at"], "key_id": item["key_id"], "signature": item["signature"],
                                    "first": first, "payload_b64": base64.b64encode(bytes(item["payload"])).decode("ascii")})
    elif row["kind"] == "delivery":
        built = staged_payload(c, row, me)
        if built is None:
            return None
        body, origin, revision = built
    else:
        body = bytes(row["payload"])
    current = F.key(c)
    envelope_id, sent_at = E.new_envelope_id(), int(now)
    signature = E.sign(F.private(c, vault, current), me, recipient, envelope_id, sent_at, body)
    return {"to": recipient, "body": body, "envelope_id": envelope_id, "sent_at": sent_at, "key_id": current["key_id"],
            "signature": signature, "origin": origin, "revision": revision, "skip": skip}


def delivery_of(c, row):
    """The message delivery an outbox row carries, still waiting: its own, or the one for an item's message to this
    recipient. None for a row that carries no message (membership, catch-up, refusal)."""
    if row["kind"] == "delivery":
        return row["delivery_id"]
    return T.item_delivery(c, row["item_id"], row["recipient"]) if row["kind"] == "item" else None


def sent(c, row, outbound):
    """HQ took the row. A delivery's revision is sent (the outbound's), and the delivery is settled delivered only while it
    is still that revision. An edit made during the send leaves it pending, and the next stage queues the new one. An item
    settles the delivery of its message to this recipient the same way; a skip was not for it, so that delivery goes moot."""
    if row["kind"] == "delivery" and outbound.get("origin"):
        c.execute("UPDATE federation_messages SET sent_revision=?, revision=max(revision,?), updated_at=? WHERE origin_id=? "
                  "AND (sent_revision IS NULL OR sent_revision<?)",
                  (outbound["revision"], outbound["revision"], stamp(), outbound["origin"], outbound["revision"]))
        waiting = T.current(c, row["delivery_id"])
        if waiting is not None and waiting["revision"] == outbound["revision"]:
            T.settle(c, row["delivery_id"], "delivered")
    elif row["kind"] == "item":
        delivery = delivery_of(c, row)
        if delivery:
            T.settle(c, delivery, "moot" if outbound.get("skip") else "delivered")
    c.execute("DELETE FROM federation_outbox_items WHERE id=?", (row["id"],))


def dropped(c, row):
    """A row no longer to be sent: a delivery still waiting goes moot, and the row goes."""
    delivery = delivery_of(c, row)
    if delivery:
        T.moot_pending(c, delivery)
    c.execute("DELETE FROM federation_outbox_items WHERE id=?", (row["id"],))


def refused(c, row, code):
    """HQ or the peer will never take this row as it is: settled as failed (a delivery, which Needs you then lists:
    `T.unsent`) and dropped."""
    delivery = delivery_of(c, row)
    if delivery:
        T.settle(c, delivery, "failed", error=code)
    c.execute("DELETE FROM federation_outbox_items WHERE id=?", (row["id"],))


def retry(c, row, code, now):
    """A send that may go later: tried again after the backoff. The delivery keeps the count, unless the peering is merely
    paused, which is no failure."""
    attempts = row["attempts"] + 1
    wait = min(BACKOFF_MAX, RETRY_FIRST * 2 ** min(attempts, 8))
    c.execute("UPDATE federation_outbox_items SET attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
              (attempts, now + wait, code, row["id"]))
    delivery = delivery_of(c, row) if code != "paused" else None
    if delivery:
        T.note_failure(c, delivery, code)


def carried_texts(c, me, sender, body):
    """The carried texts an envelope from `sender` brings that a write would screen: each as the write will see it. A text
    already applied (a repeat) is not screened again."""
    payload = E.parse_payload(body)
    if not payload:
        return []
    out = []
    if payload.get("type") == "message":
        fields = checked_message(payload)
        thread = thread_of_home(c, me, payload.get("thread")) if fields else None
        if fields and thread and not fields["deleted"] and E.install_of(fields["actor"]) == sender:
            out.append((sender, fields, thread))
    elif payload.get("type") == "item" and payload.get("skip") is not True and payload.get("item_type") == "message":
        author = payload.get("author")
        thread = thread_of_home(c, sender, payload.get("thread")) if valid_text(payload.get("thread"), 200) else None
        try:
            inner = E.parse_payload(base64.b64decode(payload.get("payload_b64") or "", validate=True))
        except (TypeError, ValueError):
            inner = None
        fields = checked_message(inner) if inner else None
        if fields:
            fields["first"] = payload.get("first") is True
        if (isinstance(author, str) and author != me and thread and fields and not fields["deleted"]
                and E.install_of(fields["actor"]) == author):
            out.append((author, fields, thread))
    found = []
    for install, fields, thread in out:
        known = message_row(c, fields["origin_id"])
        if known is None and fields["revision"] > 0 and not fields["first"]:
            continue                                     # an edit of a message not known here: not written, not checked
        if known is not None and (known["author"] != fields["actor"] or known["thread_id"] != thread["thread_id"]
                                  or fields["revision"] <= known["revision"]):
            continue
        found.append({"install": install, "actor": fields["actor"], "origin_id": fields["origin_id"],
                      "revision": fields["revision"], "text": fields["body"], "thread_id": thread["thread_id"]})
    return found
