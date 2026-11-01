"""Shared threads between installs: what the envelopes mean. backend/federation_relay.py moves the bytes; this module decides
what they do, inside the caller's transaction.

A thread is shared by its home, the install it was first made on. The home numbers the thread's stream (`home_seq`, one
per item) and is the membership authority: only it adds or removes an install. A replica (another install in the thread)
posts to the home, which numbers each message and relays every item to every active member.

Items carry their author's signature, so a member can check a message the home relayed from a third install against
that install's key from HQ. The outer envelope from the home is checked against the pinned peer key.

Outcomes of `receive` are plain words: applied, duplicate, gap, or the reason an envelope was refused. A refusal is
recorded (so a replay of it is refused too) and never raises, so a catch-up request queued with it is kept.
"""
import base64

from . import federation as F
from . import federation_envelope as E
from . import federation_threads as T
from . import hubdb as H
from .store import Problem

CATCH_UP_EVERY = 300        # seconds between catch-up requests for one thread
CATCH_UP_BATCH = 200        # items one catch-up answer queues
MESSAGE_KINDS = ("say", "ask", "answer", "notice")
RETRY_FIRST = 15            # seconds before a failed send is tried again; doubled per attempt
BACKOFF_MAX = 300


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
    return c.execute("SELECT 1 FROM federation_outbox WHERE kind=? AND delivery_id=?", (kind, delivery_id)).fetchone() is not None


# ----------------------------------------------------------------------------- outbox
def queue(c, *, recipient, kind, item_id=None, delivery_id=None, payload=None):
    c.execute("INSERT OR IGNORE INTO federation_outbox(id,recipient,kind,delivery_id,item_id,payload,attempts,next_attempt_at,"
              "last_error,created_at) VALUES(?,?,?,?,?,?,0,0,'',?)",
              (H.new_id(), recipient, kind, delivery_id, item_id, payload, stamp()))


def enqueue(c, thread, item):
    """The item to every active member it is for. A message goes only to the members who were in the thread when it was
    numbered: a member who joined later never had it, so never gets its edits either."""
    origin_seq = message_row(c, item["origin_id"])["home_seq"] if item["kind"] == "message" else None
    for member in members_of(c, thread["thread_id"]):
        if member["status"] != "active":
            continue
        if item["kind"] == "message" and (origin_seq is None or origin_seq <= member["joined_at_seq"]):
            continue
        queue(c, recipient=member["install"], kind="item", item_id=item["id"])


def request_catch_up(c, now, thread, after):
    """Asks the home for the stream after `after`, once per CATCH_UP_EVERY for the thread."""
    last = thread["catch_up_requested_at"]
    if last is not None and now - last < CATCH_UP_EVERY:
        return
    c.execute("UPDATE federation_threads SET catch_up_requested_at=?, updated_at=? WHERE id=?", (now, stamp(), thread["id"]))
    payload = E.payload_bytes({"type": "catch_up", "thread": thread["origin_id"], "after": int(after)})
    queue(c, recipient=thread["home_install"], kind="catch_up", payload=payload)


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


def message_payload(thread_origin, origin, revision, actor, message_kind, body, deleted, created):
    return {"type": "message", "thread": thread_origin, "origin_id": origin, "revision": int(revision), "actor": actor,
            "message_kind": message_kind, "body": "" if deleted else (body or ""), "deleted": bool(deleted),
            "created": created or ""}


def home_message(c, vault, thread, row, now):
    """The home's item for one of its messages (or a revision of it). None when the revision is for a message the
    thread's members never had: one posted before the thread was shared with anyone."""
    home = F.install(c)
    origin, revision = row["origin_id"], row["revision"]
    existing = c.execute("SELECT * FROM federation_items WHERE origin_id=? AND revision=?", (origin, revision)).fetchone()
    if existing:
        return dict(existing)
    actor = E.qualify(row["actor"], home) if row["actor"] else ""
    if E.install_of(actor) != home:                  # a message numbered by another install is never signed here
        return None
    known = message_row(c, origin)
    if (revision == 0 and known is not None) or (revision > 0 and (known is None or known["home_seq"] is None)):
        return None
    payload = message_payload(thread["origin_id"], origin, revision, actor, row["message_kind"], row["body"],
                              row["deleted"], row["created"])
    item = sign_item(c, vault, thread, kind="message", payload=payload, origin=origin, revision=revision, now=now)
    if revision == 0:
        c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,created_at,"
                  "updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
                  (H.new_id(), origin, row["message_id"], thread["thread_id"], actor, item["home_seq"], stamp(), stamp()))
    else:
        c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
    enqueue(c, thread, item)
    return item


def stage(c, vault, now, limit=100):
    """Core's waiting deliveries to peer installs, turned into this install's outbox. The home numbers a message for its
    members; a replica queues its own posts for the home. Anything not to be sent is settled here, so core stops asking."""
    me = F.install(c)
    if not me or F.key(c) is None:
        return 0
    staged = 0
    for row in T.due(c, limit):
        delivery = row["id"]
        target = (row["recipient"] or "").removeprefix("peer:")
        thread = thread_local(c, row["thread_id"]) if row["thread_id"] else None
        if thread is None or row["message_kind"] not in MESSAGE_KINDS:
            T.settle(c, delivery, "moot")
            continue
        if thread["home_install"] == me:
            member = member_of(c, thread["thread_id"], target)
            if target == me or member is None or member["status"] != "active":
                T.settle(c, delivery, "moot")
                continue
            item = home_message(c, vault, thread, row, now)
            T.settle(c, delivery, "delivered" if item else "moot")
            staged += 1
            continue
        if queued(c, "delivery", delivery_id=delivery):
            continue
        actor = E.qualify(row["actor"], me) if row["actor"] else ""
        if target != thread["home_install"] or E.install_of(actor) != me:
            T.settle(c, delivery, "moot")                 # not this install's message, or not the home's to receive
            continue
        origin, revision = row["origin_id"], row["revision"]
        known = message_row(c, origin)
        if known is not None and revision <= known["revision"]:
            T.settle(c, delivery, "moot")
            continue
        payload = E.payload_bytes(message_payload(thread["origin_id"], origin, revision, actor, row["message_kind"],
                                                  row["body"], row["deleted"], row["created"]))
        if len(payload) > E.MAX_BODY:
            T.settle(c, delivery, "failed", error="too_large")
            continue
        if known is None:
            c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,created_at,"
                      "updated_at) VALUES(?,?,?,?,?,?,NULL,?,?)",
                      (H.new_id(), origin, row["message_id"], thread["thread_id"], actor, revision, stamp(), stamp()))
        else:
            c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
        queue(c, recipient=thread["home_install"], kind="delivery", delivery_id=delivery, payload=payload)
        staged += 1
    return staged


# ----------------------------------------------------------------------------- membership, from the home
def share(c, vault, now, thread_id, install_id):
    """The home adds an install to one of its threads. The install gets the stream from this item on."""
    me = F.install(c)
    if not me:
        raise Problem("not_set_up", "Connect the Tico Gateway first: federation needs this install's id", 409)
    if not T.exists(c, thread_id):
        raise Problem("not_found", "No such thread", 404)
    if T.audience(c, thread_id) != "members":
        raise Problem("audience", "Only a thread whose audience is members can be shared with another install", 422)
    if F.active(c, install_id) is None:
        raise Problem("not_peered", "Make the peering with that install first: it must be active", 409)
    row = thread_local(c, thread_id)
    if row is None:
        c.execute("INSERT INTO federation_threads(id,thread_id,home_install,origin_id,applied_through,high_seq,removed_at_seq,"
                  "catch_up_requested_at,created_at,updated_at) VALUES(?,?,?,?,0,0,NULL,NULL,?,?)",
                  (H.new_id(), thread_id, me, thread_id, stamp(), stamp()))
        row = thread_local(c, thread_id)
    elif row["home_install"] != me:
        raise Problem("not_home", "This thread's home is another install, so only that install can add to it", 409)
    member = member_of(c, thread_id, install_id)
    if member and member["status"] == "active":
        raise Problem("already_shared", "That install is already in the thread", 409)
    item = sign_item(c, vault, row, kind="membership", now=now, origin=None, revision=0,
                     payload={"type": "membership", "thread": thread_id, "change": "added", "install": install_id,
                              "title": T.title(c, thread_id)})
    if member:
        c.execute("UPDATE federation_members SET status='active', joined_at_seq=?, removed_at_seq=NULL, updated_at=? "
                  "WHERE id=?", (item["home_seq"], stamp(), member["id"]))
    else:
        c.execute("INSERT INTO federation_members(id,thread_id,install,status,joined_at_seq,removed_at_seq,created_at,"
                  "updated_at) VALUES(?,?,?,'active',?,NULL,?,?)", (H.new_id(), thread_id, install_id, item["home_seq"],
                                                                  stamp(), stamp()))
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
                              "title": ""})
    c.execute("UPDATE federation_members SET status='removed', removed_at_seq=?, updated_at=? WHERE id=?",
              (item["home_seq"], stamp(), member["id"]))
    enqueue(c, thread_local(c, thread_id), item)
    queue(c, recipient=install_id, kind="item", item_id=item["id"])
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
    row = c.execute("SELECT public_key FROM federation_directory WHERE install=? AND key_id=?", (author, key_id)).fetchone()
    if row:
        return base64.b64decode(row[0])
    found = fetched.get(author)
    return base64.b64decode(found["public_key"]) if found and found["key_id"] == key_id else None


def receive(c, now, sender, envelope_id, headers, body, received_at, fetched):
    """One envelope the HQ pull handed over, inside the caller's transaction. `fetched` holds keys the caller looked up
    outside it, {install: {key_id, public_key}}, which are kept in the directory. Returns the outcome."""
    me = F.install(c)
    if not me:
        return "not_set_up"
    if c.execute("SELECT 1 FROM federation_receipts WHERE peer_install=? AND envelope_id=?", (sender, envelope_id)).fetchone():
        return "replay"
    for install, found in fetched.items():
        c.execute("INSERT OR IGNORE INTO federation_directory(install,key_id,public_key,fetched_at) VALUES(?,?,?,?)",
                  (install, found["key_id"], found["public_key"], stamp()))
    outcome = _receive(c, now, me, sender, envelope_id, headers, body, received_at, fetched)
    c.execute("INSERT INTO federation_receipts(id,peer_install,envelope_id,outcome,received_at) VALUES(?,?,?,?,?)",
              (H.new_id(), sender, envelope_id, outcome, stamp()))
    return outcome


def _receive(c, now, me, sender, envelope_id, headers, body, received_at, fetched):
    peer = F.peering(c, sender)
    if peer is None or peer["status"] == "revoked":
        return "no_peering"
    if peer["status"] != "active":
        return "not_active"
    if peer["paused_at"]:
        return "paused"
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
        return on_message(c, now, me, sender, payload, envelope_id, sent, signature, body)
    if kind == "catch_up":
        return on_catch_up(c, me, sender, payload)
    if kind == "item":
        return on_item(c, now, me, sender, peer, payload, fetched)
    return "invalid"


def valid_text(value, limit):
    return isinstance(value, str) and len(value) <= limit


def checked_message(payload):
    """The message fields of a payload, or None when one is missing or the wrong kind."""
    origin, revision, actor = payload.get("origin_id"), payload.get("revision"), payload.get("actor")
    text, kind, deleted = payload.get("body", ""), payload.get("message_kind"), payload.get("deleted", False)
    if not (valid_text(origin, 200) and origin and type(revision) is int and 0 <= revision <= 1_000_000
            and valid_text(actor, 300) and valid_text(text, E.MAX_BODY) and kind in MESSAGE_KINDS
            and type(deleted) is bool and valid_text(payload.get("thread"), 200)):
        return None
    return {"origin_id": origin, "revision": revision, "actor": actor, "body": text, "message_kind": kind,
            "deleted": deleted}


def on_message(c, now, me, sender, payload, envelope_id, sent, signature, body):
    """A replica's post, sent to the home: numbered, stored, posted into the home's thread and relayed."""
    thread = thread_of_home(c, me, payload.get("thread"))
    if thread is None:
        return "unknown_thread"
    member = member_of(c, thread["thread_id"], sender)
    if member is None or member["status"] != "active":
        return "not_member"
    fields = checked_message(payload)
    if fields is None:
        return "invalid"
    if E.install_of(fields["actor"]) != sender:
        return "namespace"
    origin, revision, actor = fields["origin_id"], fields["revision"], fields["actor"]
    peer = F.peering(c, sender)
    known = message_row(c, origin)
    if revision == 0:
        if known is not None:
            return "duplicate"
        item = insert_item(c, thread, kind="message", origin=origin, revision=0, author=sender, envelope_id=envelope_id,
                           sent_at=sent, key_id=peer["peer_key_id"], signature=signature, body=body)
        view = T.post_inbound(c, thread["thread_id"], fields["body"], kind=fields["message_kind"],
                              origin={"origin_id": origin, "revision": 0, "install": sender, "actor": actor})
        c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,created_at,"
                  "updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
                  (H.new_id(), origin, view["id"], thread["thread_id"], actor, item["home_seq"], stamp(), stamp()))
    else:
        if known is None or known["author"] != actor:
            return "unknown_message"
        if revision <= known["revision"]:
            return "duplicate"
        view = T.apply_revision(c, {"origin_id": origin, "revision": revision, "install": sender, "actor": actor},
                                body=fields["body"], deleted=fields["deleted"])
        if view is None:
            return "unknown_message"
        item = insert_item(c, thread, kind="message", origin=origin, revision=revision, author=sender,
                           envelope_id=envelope_id, sent_at=sent, key_id=peer["peer_key_id"], signature=signature, body=body)
        c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
    enqueue(c, thread, item)
    return "applied"


def on_catch_up(c, me, sender, payload):
    """A member asks for the stream after a number: queued again, for the items it may have. A removed member gets the
    items up to its removal and no more."""
    thread = thread_of_home(c, me, payload.get("thread"))
    if thread is None:
        return "unknown_thread"
    member = member_of(c, thread["thread_id"], sender)
    after = payload.get("after")
    if member is None or type(after) is not int or after < 0:
        return "not_member" if member is None else "invalid"
    bound = member["removed_at_seq"] if member["status"] == "removed" else None
    items = c.execute("SELECT * FROM federation_items WHERE thread_id=? AND home_seq>? ORDER BY home_seq LIMIT ?",
                      (thread["thread_id"], after, CATCH_UP_BATCH)).fetchall()
    for item in items:
        if bound is not None and item["home_seq"] > bound:
            break
        if item["kind"] == "message":
            origin_seq = message_row(c, item["origin_id"])
            if origin_seq is None or origin_seq["home_seq"] is None or origin_seq["home_seq"] <= member["joined_at_seq"]:
                continue
        queue(c, recipient=sender, kind="item", item_id=item["id"])
    return "applied"


def on_item(c, now, me, sender, peer, wrapper, fetched):
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
        if author != sender or payload.get("change") not in ("added", "removed") or not E.INSTALL.fullmatch(str(payload.get("install"))):
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
    if seq > thread["applied_through"] + 1:
        request_catch_up(c, now, thread, thread["applied_through"])
        return "gap"
    outcome = apply_message(c, now, me, thread, author, seq, fields)
    if outcome == "unknown_message":
        request_catch_up(c, now, thread, 0)             # the message's create comes back with the next catch-up
    if seq > thread["applied_through"]:
        advance(c, thread, seq)
    return outcome


def advance(c, thread, seq):
    c.execute("UPDATE federation_threads SET applied_through=?, high_seq=max(high_seq,?), updated_at=? WHERE id=?",
              (seq, seq, stamp(), thread["id"]))


def apply_message(c, now, me, thread, author, seq, fields):
    """One message from the stream into the local thread, through core's door. Works for a number already passed too,
    so a message a replica lost comes back on a catch-up."""
    origin, revision, actor = fields["origin_id"], fields["revision"], fields["actor"]
    known = message_row(c, origin)
    if revision == 0:
        if known is None:
            view = T.post_inbound(c, thread["thread_id"], fields["body"], kind=fields["message_kind"],
                                  origin={"origin_id": origin, "revision": 0, "install": author, "actor": actor})
            c.execute("INSERT INTO federation_messages(id,origin_id,message_id,thread_id,author,revision,home_seq,created_at,"
                      "updated_at) VALUES(?,?,?,?,?,0,?,?,?)",
                      (H.new_id(), origin, view["id"], thread["thread_id"], actor, seq, stamp(), stamp()))
            return "applied"
        if author == me and known["author"] == actor and known["home_seq"] is None:
            c.execute("UPDATE federation_messages SET home_seq=?, updated_at=? WHERE origin_id=?", (seq, stamp(), origin))
            return "applied"
        return "duplicate"
    if known is None or known["author"] != actor:
        return "unknown_message"
    if revision <= known["revision"]:
        return "duplicate"
    if author != me:
        view = T.apply_revision(c, {"origin_id": origin, "revision": revision, "install": author, "actor": actor},
                                body=fields["body"], deleted=fields["deleted"])
        if view is None:
            return "unknown_message"
    c.execute("UPDATE federation_messages SET revision=?, updated_at=? WHERE origin_id=?", (revision, stamp(), origin))
    return "applied"


def on_membership(c, now, me, sender, thread, seq, payload):
    target, change = payload["install"], payload["change"]
    if target == me and change == "added":
        if thread is None:
            view = T.create_thread(c, "keeper", title=str(payload.get("title") or "")[:200])
            T.add_outside(c, view["id"], sender)
            c.execute("INSERT INTO federation_threads(id,thread_id,home_install,origin_id,applied_through,high_seq,removed_at_seq,"
                      "catch_up_requested_at,created_at,updated_at) VALUES(?,?,?,?,?,?,NULL,NULL,?,?)",
                      (H.new_id(), view["id"], sender, payload["thread"], seq, seq, stamp(), stamp()))
            return "applied"
        if thread["removed_at_seq"] is None and seq <= thread["applied_through"]:
            return "duplicate"
        c.execute("UPDATE federation_threads SET removed_at_seq=NULL, applied_through=max(applied_through,?), "
                  "high_seq=max(high_seq,?), updated_at=? WHERE id=?", (seq, seq, stamp(), thread["id"]))
        return "applied"
    if thread is None:
        return "unknown_thread"
    if target == me and change == "removed":
        c.execute("UPDATE federation_threads SET removed_at_seq=?, applied_through=max(applied_through,?), "
                  "high_seq=max(high_seq,?), updated_at=? WHERE id=?", (seq, seq, seq, stamp(), thread["id"]))
        return "applied"
    if seq <= thread["applied_through"]:
        return "duplicate"
    if seq > thread["applied_through"] + 1:
        request_catch_up(c, now, thread, thread["applied_through"])
        return "gap"
    advance(c, thread, seq)
    return "applied"


# ----------------------------------------------------------------------------- outbound, to HQ
def due_outbox(c, now, limit=50):
    return [dict(r) for r in c.execute("SELECT * FROM federation_outbox WHERE next_attempt_at<=? ORDER BY created_at, id "
                                       "LIMIT ?", (now, limit)).fetchall()]


def envelope(c, vault, row, now):
    """What to post for one outbox row, signed now by this install, or None when it is no longer to be sent."""
    me = F.install(c)
    recipient = row["recipient"]
    if row["kind"] == "item":
        item = c.execute("SELECT * FROM federation_items WHERE id=?", (row["item_id"],)).fetchone()
        if item is None:
            return None
        member = member_of(c, item["thread_id"], recipient)
        if member is None or (member["status"] == "removed" and (member["removed_at_seq"] is None
                                                               or item["home_seq"] > member["removed_at_seq"])):
            return None
        thread = thread_local(c, item["thread_id"])
        body = E.payload_bytes({"type": "item", "thread": thread["origin_id"], "home_seq": item["home_seq"],
                                "item_type": item["kind"], "author": item["author"], "envelope_id": item["envelope_id"],
                                "sent_at": item["sent_at"], "key_id": item["key_id"], "signature": item["signature"],
                                "payload_b64": base64.b64encode(bytes(item["payload"])).decode("ascii")})
    else:
        body = bytes(row["payload"])
    current = F.key(c)
    envelope_id, sent_at = E.new_envelope_id(), int(now)
    signature = E.sign(F.private(c, vault, current), me, recipient, envelope_id, sent_at, body)
    return {"to": recipient, "body": body, "envelope_id": envelope_id, "sent_at": sent_at, "key_id": current["key_id"],
            "signature": signature}


def sent(c, row):
    if row["kind"] == "delivery":
        T.settle(c, row["delivery_id"], "delivered")
    c.execute("DELETE FROM federation_outbox WHERE id=?", (row["id"],))


def refused(c, row, code):
    """HQ or the peer will never take this row as it is: settled as failed (a delivery) and dropped."""
    if row["kind"] == "delivery":
        T.settle(c, row["delivery_id"], "failed", error=code)
    c.execute("DELETE FROM federation_outbox WHERE id=?", (row["id"],))


def retry(c, row, code, now):
    attempts = row["attempts"] + 1
    wait = min(BACKOFF_MAX, RETRY_FIRST * 2 ** min(attempts, 8))
    c.execute("UPDATE federation_outbox SET attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
              (attempts, now + wait, code, row["id"]))

