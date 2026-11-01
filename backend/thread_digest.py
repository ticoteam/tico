"""Email digests of threads (the thread helper's section 5, docs/thread-helper.md): a person who follows a thread on
digest gets one email an hour that lists the threads with new messages for them. Each line is a thread's title, its
unread count and a link to it. The email carries no message text and no author names, and a title goes only for a
thread the person can still read, judged message by message at the moment of the send (C1).

Unread means what the thread shows: the messages past the person's read mark (thread_followers.last_read_seq) written
by a person or delivered to them, never their own or another bot's post (threads.unread_message_sql). A digest lists a
thread when it holds an unread message the person can read that no digest has listed yet, which
thread_digests records per person and thread as the last sequence number a sent email covered. A refused send records
nothing, so the next run lists the same messages again. A thread beyond MAX_THREADS waits for the next run.

The person's Email digests switch (`email_digest`) holds their digests: nothing is sent while it is off, and what is
unread stays unread until it is on again. The mail is the install's Tico account issuer's (backend/oidc.py `digest`):
it mails only an address on this install's people list and holds each person to one digest an hour. A run with
nothing new sends nothing and writes nothing.
"""
import logging
import re
import unicodedata

from . import hubdb as H
from . import threads

LOG = logging.getLogger("tico.thread_digest")
MAX_THREADS = 20          # the issuer takes at most 20 threads in one email; the rest wait for the next run
TITLE_CHARS = 200         # the issuer's limit on a title
MAX_UNREAD = 999          # the issuer's limit on a count
WINDOW = 1000             # the newest unread messages of a thread that one run reads; a bigger backlog counts as this
FALLBACK_TITLE = "Untitled thread"
THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS thread_digests (thread_id TEXT NOT NULL, actor TEXT NOT NULL, "
    "emailed_seq INTEGER NOT NULL DEFAULT 0, emailed_at TEXT, PRIMARY KEY (thread_id, actor))",
]
# A thread this person follows on digest that has a message past their read mark and past the last digest.
_BEHIND = ("f.actor=? AND f.following=1 AND f.notify='digest' AND conv.archived_at IS NULL "
           "AND conv.last_unread_seq > MAX(f.last_read_seq, COALESCE(td.emailed_seq, 0))")


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def candidates(c):
    """The people with a thread to list: a followed thread on digest that is behind for them."""
    return [r[0] for r in c.execute(
        "SELECT DISTINCT f.actor FROM thread_followers f JOIN conversations conv ON conv.id=f.thread_id "
        "LEFT JOIN thread_digests td ON td.thread_id=f.thread_id AND td.actor=f.actor "
        "WHERE f.actor LIKE 'human:%' AND f.following=1 AND f.notify='digest' AND conv.archived_at IS NULL "
        "AND conv.last_unread_seq > MAX(f.last_read_seq, COALESCE(td.emailed_seq, 0)) ORDER BY f.actor").fetchall()]


def title_of(subject):
    """A thread's title on one line, as the issuer reads it: whitespace runs collapse to a space and control characters
    go, so the length the issuer checks is the length sent here."""
    kept = "".join(" " if ch.isspace() else ch for ch in str(subject or "")
                   if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cs"))
    text = " ".join(kept.split())[:TITLE_CHARS].strip()
    return text or FALLBACK_TITLE


def _plan(c, actor):
    """What this person's digest says now, or None when it says nothing: their email, the threads to list (most recent
    first, at most MAX_THREADS), each with its title and unread count, and the sequence number each listed thread's
    email would cover. Every message is read through the person's own view (C1) here, at the send."""
    from . import people as P
    from . import views
    from .auth import standing
    pid = H.actor_id(actor)
    if standing(c, pid):                              # left, or signed in off: no digest
        return None
    person = P.person(pid, views.roster(c))
    if not person or person.get("email_digest") is False:
        return None
    email = str((H.human(c, pid) or {}).get("email") or "").strip().lower()
    viewer = threads.actor_viewer(c, actor)
    if not email or viewer is None:
        return None
    listed = []
    behind = c.execute(
        "SELECT f.thread_id, f.last_read_seq, COALESCE(td.emailed_seq, 0) AS emailed, conv.subject "
        "FROM thread_followers f JOIN conversations conv ON conv.id=f.thread_id "
        "LEFT JOIN thread_digests td ON td.thread_id=f.thread_id AND td.actor=f.actor "
        f"WHERE {_BEHIND} ORDER BY conv.last_message_at DESC, f.thread_id", (actor,)).fetchall()
    for row in behind:
        if not THREAD_ID.fullmatch(row["thread_id"] or ""):
            continue
        window = c.execute(f"SELECT m.* FROM messages m WHERE m.conversation_id=? AND m.seq>? AND "
                           f"{threads.unread_message_sql()} ORDER BY m.seq DESC LIMIT ?",
                           (row["thread_id"], row["last_read_seq"], actor, actor, WINDOW)).fetchall()
        readable = [m for m in window if threads.readable(c, viewer, dict(m))]
        if not any(m["seq"] > row["emailed"] for m in readable):
            continue
        listed.append({"thread_id": row["thread_id"], "title": title_of(row["subject"]),
                       "unread": min(len(readable), MAX_UNREAD), "covers": window[0]["seq"]})
    listed = listed[:MAX_THREADS]
    return {"actor": actor, "email": email,
            "items": [{"title": t["title"], "unread": t["unread"], "path": "/#/thread/" + t["thread_id"]}
                      for t in listed],
            "marks": [(t["thread_id"], t["covers"]) for t in listed],
            "unread": sum(t["unread"] for t in listed)}


def _mark_sent(c, actor, marks):
    now = H.now()
    c.executemany("INSERT INTO thread_digests(thread_id, actor, emailed_seq, emailed_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(thread_id, actor) DO UPDATE SET emailed_seq=MAX(emailed_seq, excluded.emailed_seq), "
                  "emailed_at=excluded.emailed_at", [(thread_id, actor, seq, now) for thread_id, seq in marks])


def _send_one(store, send, actor):
    """One person's digest: the plan in one transaction, the send outside it (the issuer waits on mail), then the
    threads it listed recorded as emailed only when the issuer took the email. Returns 1 when an email went."""
    with store.transaction() as c:
        plan = _plan(c, actor)
    if plan is None or not plan["items"]:
        return 0
    answer = send(plan["email"], plan["items"])
    if answer is None:
        return 0
    status, body = answer
    if status != 202 or body.get("status") != "sent":
        LOG.info("Email digest not sent: the issuer answered %s", status)
        return 0
    with store.transaction() as c:
        _mark_sent(c, actor, plan["marks"])
        H.event(c, H.KEEPER, "thread.digest", actor, {"threads": len(plan["items"]), "unread": plan["unread"]})
    return 1


def sweep(store, send):
    """One run: one email to each person with a thread to list. `send` is the issuer call (`sender_for`), or None when
    this install sends no mail, and then nothing is read or written. Returns how many emails went. One person's failure
    is logged by its exception type alone (never an address or a title) and does not stop the others."""
    if send is None:
        return 0
    with store.read() as c:
        people = candidates(c)
    sent = 0
    for actor in people:
        try:
            sent += _send_one(store, send, actor)
        except Exception as exc:  # noqa: BLE001 - a digest that fails for one person must not stop the rest
            LOG.warning("Email digest failed for one person: %s", type(exc).__name__)
    return sent


def sender_for(auth):
    """The install's issuer digest call, or None when it signs in some other way and so sends no mail."""
    proxy = getattr(auth, "proxy", None)
    if getattr(proxy, "name", "") != "oidc" or not hasattr(proxy, "digest"):
        return None
    return proxy.digest
