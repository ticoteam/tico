"""Readers: outside people who may read a company's private Company page. A reader is an (issuer, subject) identity with a
verified email, in a set of audiences, and nothing else: not on the roster, no seat, never a `human:` or `bot:` actor.
Nothing here reads or writes `humans` except to find who a reader's message goes to.

The rules that matter:
- Everything is checked on every request against the current rows, so removing a reader or changing an audience takes
  effect on the next request. Sessions are stored as hashes and carry no audiences of their own.
- A first sign-in links to a reader row by email. After that the account wins: a changed email keeps the account's access
  and can't move it to whoever holds the old address. An unknown person gets no row.
- A reader only ever gets what `stats.project` and `editions.stream` give for one audience, through
  `release.view_model`, which copies named fields, so a field those modules grow later still can't reach a reader.
- A reader's message is outside text: fenced, screened, capped, and it reaches a person's queue with no bot woken.
"""
import base64
import csv
import hashlib
import hmac
import io
import logging
import re
import secrets
import threading

from .. import hubdb as H
from ..store import Problem, digest

log = logging.getLogger("tico.company_page.readers")

SESSION_DAYS = 30
INVITE_DAYS, INVITE_DAYS_MAX = 14, 90
READERS_MAX = 10000
AUDIENCES_PER_READER = 50
CSV_BYTES_MAX, CSV_ROWS_MAX = 262144, 2000
CSV_INVALID_SHOWN = 50
MESSAGE_MAX, MESSAGES_PER_DAY = 4000, 10
ASK_ID_MAX = 100
PAGE_SIZE = 10
NOTICE_ATTEMPTS = 3
RANK = {"board": 0, "investors": 1, "custom": 2, "public": 3}
EMAIL = re.compile(r"[^@\s<>\"',;:()\[\]\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                   r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")

# Set by reader_app.install: what `reader_view` and `notify_edition`, which take only a connection, need of the install.
_state = {"store": None, "settings": None, "oidc": None}


class InviteRefused(Exception):
    """An invite that can't be used. The reason is for the log; every refusal reads the same to the person."""


# ---------------------------------------------------------------------------------------------- reading the state
def transparency_of(c):
    """closed, private or open. No row, or anything else in it, is closed."""
    row = c.execute("SELECT transparency FROM company_page_settings WHERE id='company'").fetchone()
    return row["transparency"] if row and row["transparency"] in ("private", "open") else "closed"


def availability(settings):
    """Whether private audiences can work on this install: (True, "") or (False, why)."""
    if not getattr(settings, "reader_origin", ""):
        return False, "No reader address is set (TICO_READER_ORIGIN), so private audiences are off"
    from .reader_app import sign_in_configured
    if not sign_in_configured(settings):
        return False, "No sign-in provider is set up (OIDC), so private audiences are off"
    return True, ""


def require_available(settings):
    ok, why = availability(settings)
    if not ok:
        raise Problem("private_audiences_off", why, 422)


def clean_email(value):
    email = str(value or "").strip().lower()
    if len(email) > 320 or not EMAIL.fullmatch(email):
        raise Problem("email", "Enter a valid email address", 422)
    return email


def get(c, reader_id):
    """A reader still on the list, or None."""
    row = c.execute("SELECT * FROM readers WHERE id=? AND removed_at IS NULL", (reader_id,)).fetchone()
    return dict(row) if row else None


def _require(c, reader_id):
    row = get(c, reader_id)
    if not row:
        raise Problem("not_found", "Reader not found", 404)
    return row


def _audience_ids(c, ids):
    """Audiences a reader can be put in: they exist and are lists. Public isn't a list: anyone gets it, and only while the
    company is Open."""
    ids = list(dict.fromkeys(str(i) for i in ids or ()))
    if len(ids) > AUDIENCES_PER_READER:
        raise Problem("audiences", "Too many audiences", 422)
    for audience_id in ids:
        row = c.execute("SELECT kind FROM audiences WHERE id=?", (audience_id,)).fetchone()
        if not row:
            raise Problem("audience", "Audience not found", 404)
        if row["kind"] == "public":
            raise Problem("audience", "The Public audience has no reader list", 422)
    return ids


def audiences_of(c, reader_id):
    """The enabled audiences a reader on the list is in, highest first: [{id, name, kind}]."""
    rows = c.execute("SELECT a.id,a.name,a.kind FROM audience_readers ar JOIN audiences a ON a.id=ar.audience_id "
                     "JOIN readers r ON r.id=ar.reader_id WHERE ar.reader_id=? AND r.removed_at IS NULL AND a.enabled=1",
                     (reader_id,)).fetchall()
    return sorted((dict(r) for r in rows), key=lambda a: (RANK.get(a["kind"], len(RANK)), a["name"], a["id"]))


def highest_audience(c, reader_id):
    """The audience this reader's page is for right now, or None. Closed gives nobody anything, and a Public audience
    counts only while the company is Open."""
    transparency = transparency_of(c)
    if transparency == "closed":
        return None
    for audience in audiences_of(c, reader_id):
        if audience["kind"] != "public" or transparency == "open":
            return audience
    return None


# ---------------------------------------------------------------------------------------------- the list
def add(c, actor, email, audience_ids=(), name=None):
    """Put an address on the list, or add audiences to one already there. No roster, no account, no mail."""
    email, name = clean_email(email), str(name or "").strip()[:200] or None
    ids = _audience_ids(c, audience_ids)
    row = c.execute("SELECT * FROM readers WHERE lower(email)=? AND removed_at IS NULL", (email,)).fetchone()
    added = not row
    if row:
        reader_id = row["id"]
    else:
        if c.execute("SELECT count(*) FROM readers WHERE removed_at IS NULL").fetchone()[0] >= READERS_MAX:
            raise Problem("too_many", f"A company has at most {READERS_MAX} readers", 422)
        reader_id = H.new_id()
        c.execute("INSERT INTO readers(id,email,name,added_by,created_at) VALUES(?,?,?,?,?)",
                  (reader_id, email, name, actor, H.now()))
    _link(c, reader_id, ids)
    if added or ids:
        H.event(c, actor, "reader.added" if added else "reader.audiences", reader_id, {"audiences": ids})
    return {"id": reader_id, "added": added}


def _link(c, reader_id, ids):
    for audience_id in ids:
        c.execute("INSERT OR IGNORE INTO audience_readers(id,audience_id,reader_id,created_at) VALUES(?,?,?,?)",
                  (H.new_id(), audience_id, reader_id, H.now()))


def update(c, actor, reader_id, audience_ids=None, notify=None, name=None):
    """Change a reader. `audience_ids` replaces the whole set, so taking someone out of one audience is a way out that
    applies on their next request."""
    _require(c, reader_id)
    if audience_ids is not None:
        ids = _audience_ids(c, audience_ids)
        c.execute("DELETE FROM audience_readers WHERE reader_id=?", (reader_id,))
        _link(c, reader_id, ids)
        H.event(c, actor, "reader.audiences", reader_id, {"audiences": ids})
    if notify is not None:
        set_notify(c, reader_id, notify)
    if name is not None:
        c.execute("UPDATE readers SET name=? WHERE id=?", (str(name).strip()[:200] or None, reader_id))
    return view_of(c, get(c, reader_id))


def set_notify(c, reader_id, on):
    c.execute("UPDATE readers SET notify=? WHERE id=?", (int(bool(on)), reader_id))
    if not on:
        _cancel_notices(c, reader_id)


def remove(c, actor, reader_id):
    """Off the list, in one step: every session, audience link and waiting notice goes with the row's removal, so the
    next request of any browser they hold has nothing left to find."""
    _require(c, reader_id)
    now = H.now()
    c.execute("UPDATE readers SET removed_at=? WHERE id=?", (now, reader_id))
    c.execute("UPDATE reader_sessions SET revoked_at=? WHERE reader_id=? AND revoked_at IS NULL", (now, reader_id))
    c.execute("DELETE FROM audience_readers WHERE reader_id=?", (reader_id,))
    _cancel_notices(c, reader_id)
    H.event(c, actor, "reader.removed", reader_id, {})
    return {"removed": True}


def _cancel_notices(c, reader_id):
    c.execute("UPDATE reader_notices SET status='cancelled',updated_at=? WHERE reader_id=? AND status='pending'",
              (H.now(), reader_id))


def view_of(c, row):
    return {"id": row["id"], "email": row["email"], "name": row["name"], "notify": bool(row["notify"]),
            "signed_in": bool(row["idp_sub"]), "added_by": row["added_by"], "created_at": row["created_at"],
            "audiences": [{"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in
                          (dict(r) for r in c.execute(
                              "SELECT a.id,a.name,a.kind FROM audience_readers ar JOIN audiences a ON a.id=ar.audience_id "
                              "WHERE ar.reader_id=? ORDER BY a.name,a.id", (row["id"],)))]}


def list_rows(c, limit=100, offset=0):
    """The list as an iterator, in the order it was added: the route pages and byte-caps it."""
    rows = c.execute("SELECT * FROM readers WHERE removed_at IS NULL ORDER BY created_at,id LIMIT ? OFFSET ?",
                     (limit + 1, offset))
    return (view_of(c, dict(r)) for r in rows.fetchall())


def import_csv(c, actor, text, audience_ids=()):
    """Add every address in a CSV: an `email` column (or the first column), and a `name` column if there is one. A bad
    line is reported by its number and the rest still go in."""
    if len(str(text).encode()) > CSV_BYTES_MAX:
        raise Problem("too_large", "A reader CSV is at most 256 KB", 413)
    ids = _audience_ids(c, audience_ids)
    added = existing = invalid_count = 0
    invalid = []
    def bad(line, reason):
        nonlocal invalid_count
        invalid_count += 1
        if len(invalid) < CSV_INVALID_SHOWN:
            invalid.append({"line": line, "reason": reason})
    reader = csv.reader(io.StringIO(str(text), newline=""))
    email_at, name_at, line = 0, None, 0
    try:
        for line, row in enumerate(reader, 1):
            if line > CSV_ROWS_MAX:
                raise Problem("too_large", f"A reader CSV is at most {CSV_ROWS_MAX} lines", 413)
            cells = [cell.strip() for cell in row]
            if not any(cells):
                continue
            lowered = [cell.lower() for cell in cells]
            if line == 1 and "email" in lowered:
                email_at, name_at = lowered.index("email"), lowered.index("name") if "name" in lowered else None
                continue
            if email_at >= len(cells):
                bad(line, "No email")
                continue
            name = cells[name_at] if name_at is not None and name_at < len(cells) else None
            try:
                made = add(c, actor, cells[email_at], ids, name)
            except Problem as exc:
                if exc.code == "too_many":
                    raise
                bad(line, "Not a valid email")
                continue
            added, existing = added + bool(made["added"]), existing + (not made["added"])
    except csv.Error:
        bad(line + 1, "Can't read this line")
    return {"added": added, "existing": existing, "invalid_count": invalid_count, "errors": invalid}


# ---------------------------------------------------------------------------------------------- sign-in
def _by_account(c, issuer, sub, email):
    """The reader this account already is. The account wins over any address: its address follows the account when the new
    one is free, and otherwise stays as it was, so a changed email never takes over another reader's row."""
    row = c.execute("SELECT * FROM readers WHERE idp_issuer=? AND idp_sub=? AND removed_at IS NULL",
                    (issuer, sub)).fetchone()
    if not row:
        return None
    if str(row["email"]).lower() != email and not c.execute(
            "SELECT 1 FROM readers WHERE lower(email)=? AND removed_at IS NULL AND id<>?", (email, row["id"])).fetchone():
        c.execute("UPDATE readers SET email=? WHERE id=?", (email, row["id"]))
    return dict(row)


def _by_email(c, issuer, sub, email):
    """A row added by this address that no account holds yet: linked to this account from now on. A row another account
    already holds is not this person's, whatever address they verified today: (None, True)."""
    row = c.execute("SELECT * FROM readers WHERE lower(email)=? AND removed_at IS NULL", (email,)).fetchone()
    if not row:
        return None, False
    if row["idp_sub"]:
        return None, True
    c.execute("UPDATE readers SET idp_issuer=?,idp_sub=? WHERE id=?", (issuer, sub, row["id"]))
    return dict(row), False


def resolve_login(c, issuer, sub, email):
    """The reader a verified sign-in is, or None. It creates nothing: an unknown person simply has no access."""
    email = str(email or "").strip().lower()
    if not (issuer and sub and email):
        return None
    return _by_account(c, issuer, sub, email) or _by_email(c, issuer, sub, email)[0]


def start_session(c, reader_id, previous=""):
    """A new browser session: the token for the cookie, of which only the hash is kept. It says who, never what they may
    read: that is looked up on every request."""
    now = H.now()
    if previous:
        c.execute("UPDATE reader_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                  (now, digest(previous)))
    c.execute("DELETE FROM reader_sessions WHERE expires_at<=?", (now,))
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO reader_sessions(id,reader_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
              (H.new_id(), reader_id, digest(token), now, H.shift(now, days=SESSION_DAYS)))
    return token


def session_reader(c, token):
    """The reader behind a session token, or None: unknown, revoked, expired or removed are all nobody."""
    if not token or len(token) > 200:
        return None
    row = c.execute("SELECT r.* FROM reader_sessions s JOIN readers r ON r.id=s.reader_id "
                    "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND r.removed_at IS NULL",
                    (digest(token), H.now())).fetchone()
    return dict(row) if row else None


def end_session(c, token):
    if token:
        c.execute("UPDATE reader_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                  (H.now(), digest(token)))


# ---------------------------------------------------------------------------------------------- invites
def create_invite(c, actor, audience_id, days=INVITE_DAYS):
    """A single-use link to one audience: (the invite's row as the API shows it, the token). The token isn't stored and
    can't be shown again. Refused while the company is Closed."""
    if transparency_of(c) == "closed":
        raise Problem("closed", "The Company page is Closed: open it before inviting readers", 422)
    _audience_ids(c, [audience_id])
    if not 1 <= int(days) <= INVITE_DAYS_MAX:
        raise Problem("expires", f"An invite lasts 1 to {INVITE_DAYS_MAX} days", 422)
    now, invite_id, token = H.now(), H.new_id(), secrets.token_urlsafe(32)
    c.execute("INSERT INTO reader_invites(id,audience_id,token_hash,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?)",
              (invite_id, audience_id, digest(token), actor, now, H.shift(now, days=int(days))))
    H.event(c, actor, "reader.invited", invite_id, {"audience": audience_id})
    row = c.execute("SELECT * FROM reader_invites WHERE id=?", (invite_id,)).fetchone()
    return invite_view(dict(row)), token


def invite_view(row, now=None):
    now = now or H.now()
    state = ("used" if row["used_at"] else "revoked" if row["revoked_at"] else
             "expired" if row["expires_at"] <= now else "open")
    return {"id": row["id"], "audience_id": row["audience_id"], "state": state, "created_by": row["created_by"],
            "created_at": row["created_at"], "expires_at": row["expires_at"], "used_at": row["used_at"],
            "used_by": row["used_by"], "revoked_at": row["revoked_at"]}


def invite_rows(c, audience_id, limit=100, offset=0):
    rows = c.execute("SELECT * FROM reader_invites WHERE audience_id=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                     (audience_id, limit + 1, offset)).fetchall()
    return (invite_view(dict(r)) for r in rows)


def revoke_invite(c, actor, audience_id, invite_id):
    row = c.execute("SELECT * FROM reader_invites WHERE id=? AND audience_id=?", (invite_id, audience_id)).fetchone()
    if not row:
        raise Problem("not_found", "Invite not found", 404)
    if not row["used_at"] and not row["revoked_at"]:
        c.execute("UPDATE reader_invites SET revoked_at=? WHERE id=?", (H.now(), invite_id))
        H.event(c, actor, "reader.invite_revoked", invite_id, {"audience": audience_id})
    return invite_view(dict(c.execute("SELECT * FROM reader_invites WHERE id=?", (invite_id,)).fetchone()))


def redeem_invite(c, token, issuer, sub, email):
    """Spend an invite on a verified sign-in: the reader, on the list and in the audience, bound to this account. Raises
    InviteRefused, and the caller's transaction then keeps nothing."""
    email = str(email or "").strip().lower()
    if transparency_of(c) == "closed":
        raise InviteRefused("closed")
    row = c.execute("SELECT * FROM reader_invites WHERE token_hash=?", (digest(str(token or "")),)).fetchone()
    if not row:
        raise InviteRefused("unknown")
    now = H.now()
    if row["used_at"] or row["revoked_at"] or row["expires_at"] <= now:
        raise InviteRefused("spent")
    reader = _by_account(c, issuer, sub, email)
    if not reader:
        reader, taken = _by_email(c, issuer, sub, email)
        if taken:
            raise InviteRefused("address_held_by_another_account")
    if not reader:
        if c.execute("SELECT count(*) FROM readers WHERE removed_at IS NULL").fetchone()[0] >= READERS_MAX:
            raise InviteRefused("too_many")
        reader_id = H.new_id()
        c.execute("INSERT INTO readers(id,email,idp_issuer,idp_sub,added_by,created_at) VALUES(?,?,?,?,?,?)",
                  (reader_id, email, issuer, sub, row["created_by"], now))
        reader = get(c, reader_id)
    claimed = c.execute("UPDATE reader_invites SET used_at=?,used_by=? "
                        "WHERE id=? AND used_at IS NULL AND revoked_at IS NULL", (now, reader["id"], row["id"]))
    if claimed.rowcount != 1:
        raise InviteRefused("spent")
    _link(c, reader["id"], [row["audience_id"]])
    H.event(c, H.KEEPER, "reader.invite_used", row["id"], {"audience": row["audience_id"], "reader": reader["id"]})
    return reader


# ---------------------------------------------------------------------------------------------- the page
def edition_ids(c, audience_ids):
    """{anchor: edition id} of the editions published to these audiences now. An anchor outlives a correction and says
    nothing of its source, so it can only be looked up by computing it, and only among what these audiences can read."""
    from . import editions
    if not audience_ids:
        return {}
    rows = c.execute("SELECT id,audience_id,author,period_start,period_end FROM editions WHERE status='published' "
                     "AND audience_id IN (" + ",".join("?" * len(audience_ids)) + ")", tuple(audience_ids)).fetchall()
    return {editions.anchor_of(dict(r)): r["id"] for r in rows}


def readable_edition_id(c, reader_id, anchor):
    """The edition an anchor from this reader's page names, or None: a reader can only name what their audiences hold."""
    return edition_ids(c, [a["id"] for a in audiences_of(c, reader_id)]).get(str(anchor or "")[:200])


def compose(c, reader_id, at, before=None):
    """(view, the ids of the editions in it), or (None, []) when this reader has nothing to read right now. Only their
    highest audience's projection and stream go in, through the one view builder, which copies named fields and no others."""
    audience = highest_audience(c, reader_id)
    if not audience:
        return None, []
    from . import editions, release, stats
    settings = _state["settings"]
    updates = editions.stream(c, [audience["id"]], before=before, limit=PAGE_SIZE, company_name=settings.company_name)
    updates = list(updates)
    # Asked for, not inferred from a full page: the stream also stops early at its byte cap.
    last = updates[-1]["published_at"] if updates else None
    more = bool(last and editions.stream(c, [audience["id"]], before=last, limit=1))
    # A stats board nobody has approved has no stats, whatever the rules would let through.
    rows = stats.project(c, audience["id"], at)["stats"] if stats.board_approved(c, audience["id"]) is True else []
    view = release.view_model(c, audience, rows, updates, last if more else None, "private", settings)
    known = edition_ids(c, [audience["id"]])
    return view, [known.get(u["anchor"]) for u in view["updates"]]


def reader_view(c, reader_id, at, before=None):
    """The view model of this reader's page: their highest audience's editions and stats and nothing else, or None."""
    return compose(c, reader_id, at, before)[0]


def signed_out_view(c):
    """What a signed-out visitor to a private page gets to see: the header, and nothing an audience holds."""
    from . import release
    return release.view_model(c, {"kind": "private", "name": ""}, [], [], None, "private", _state["settings"])


def record_views(c, reader_id, ids):
    """Seen by: one row per reader and edition, the first time they were actually given it."""
    for edition_id in ids:
        if edition_id:
            c.execute("INSERT OR IGNORE INTO edition_views(id,edition_id,reader_id,viewed_at) VALUES(?,?,?,?)",
                      (H.new_id(), edition_id, reader_id, H.now()))


def unseen(c, reader_id, ids):
    """Those not yet recorded, so an ordinary reload takes no write lock."""
    return [e for e in ids if e and not c.execute(
        "SELECT 1 FROM edition_views WHERE edition_id=? AND reader_id=?", (e, reader_id)).fetchone()]


# ---------------------------------------------------------------------------------------------- Reply and I can help
def messages_today(c, reader_id, now=None):
    now = now or H.now()
    return c.execute("SELECT count(*) FROM reader_messages WHERE reader_id=? AND created_at>?",
                     (reader_id, H.shift(now, days=-1))).fetchone()[0]


def readable_edition(c, reader_id, edition_id):
    """The published edition if this reader is in its audience now, else None: a reader can only answer what they can read."""
    row = c.execute("SELECT * FROM editions WHERE id=? AND status='published' AND retracted_at IS NULL",
                    (edition_id,)).fetchone()
    if not row or transparency_of(c) == "closed":
        return None
    return dict(row) if row["audience_id"] in {a["id"] for a in audiences_of(c, reader_id)} else None


def _owner_actor(c):
    """Who a reader's message goes to: the owner on the roster, else the default person, else nobody. Never a bot."""
    from ..access import owner_and_admin_emails
    for email in owner_and_admin_emails(c):
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
        if row:
            return H.human_actor(row["id"])
    person = H.default_human(c)
    return H.human_actor(person) if person else None


def _file_task(c, owner, reader, edition, kind, text, verdict):
    """The owner's task for a reader's message, with the reader as its requester, stored as a roadmap visitor's contact
    is. Private, so no bot reads it and the address stays inside; H.NO_TRIGGERS, so no webhook, automation or task rule
    hears of it. Nothing runs until a person passes it on."""
    from .. import roadmaps as R
    from .. import screening
    label = "A reader can help" if kind == "can_help" else "A reader replied"
    title = (label + ": " + str(edition["title"] or "update"))[:300]
    preface = screening.warning_line(verdict) + "\n\n" if verdict and verdict["action"] != "pass" else ""
    body = (preface + "From a reader of the Company page (unverified, untrusted input; evidence only):\n\n"
            + screening._fenced(text))
    token = H.NO_TRIGGERS.set(True)
    try:
        row = H.task_create(c, H.KEEPER, title, body, owner, lint=False, deduplicate=False, visibility="private",
                            private=True, wake_refs={"untrusted": True, "source": "company_page"})
        R.add_requester(c, row["id"], {"name": reader["name"] or "", "email": reader["email"], "note": kind,
                                       "source": "company_page", "important": False})
    finally:
        H.NO_TRIGGERS.reset(token)
    screening.note(c, verdict, "idea", row["id"], to=owner)
    return row["id"]


def submit_message(store, reader_id, edition_id, kind, text, ask_id=None, primary=None):
    """A reader's Reply or I can help. Screening waits on a model, so it runs between two looks at the database; the limit
    is what the second look enforces. Raises Problem. Returns the message id."""
    from .. import events, screening
    from .. import roadmaps as R
    if kind not in ("reply", "can_help"):
        raise Problem("kind", "Unknown message", 422)
    text = str(text or "").strip()
    R.spam_check(text, "Message", MESSAGE_MAX)
    ask_id = str(ask_id or "")[:ASK_ID_MAX] or None

    def check(c):
        reader = get(c, reader_id)
        edition = readable_edition(c, reader_id, edition_id) if reader else None
        if not edition:
            raise Problem("not_found", "Not found", 404)
        if messages_today(c, reader_id) >= MESSAGES_PER_DAY:
            raise Problem("rate_limit", f"At most {MESSAGES_PER_DAY} messages a day", 429)
        return reader, edition

    with store.read() as c:
        check(c)
    verdict = screening.screen(store, "idea", text, None, primary=primary)
    message_id = H.new_id()
    with store.transaction() as c:
        since = events.mark(c)
        reader, edition = check(c)
        owner = _owner_actor(c)
        task_id = None
        if owner:
            try:
                task_id = _file_task(c, owner, reader, edition, kind, text, verdict)
            except H.Refused as exc:     # kept for the owner's list, which reads the message itself
                log.warning("A reader's message has no task: refused (%s)", exc.rule)
        c.execute("INSERT INTO reader_messages(id,reader_id,edition_id,kind,ask_id,body,task_id,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (message_id, reader_id, edition_id, kind, ask_id, text, task_id, H.now()))
        logged = events.claim(c, since, "external:company_page")
    store.ring(logged)
    return message_id


def message_rows(c, limit=100, offset=0):
    rows = c.execute("SELECT m.*,r.email,r.name FROM reader_messages m JOIN readers r ON r.id=m.reader_id "
                     "ORDER BY m.created_at DESC,m.id LIMIT ? OFFSET ?", (limit + 1, offset)).fetchall()
    return ({"id": r["id"], "kind": r["kind"], "body": r["body"], "ask_id": r["ask_id"], "edition_id": r["edition_id"],
             "task_id": r["task_id"], "created_at": r["created_at"], "untrusted": True,
             "reader": {"id": r["reader_id"], "email": r["email"], "name": r["name"]}} for r in rows)


# ---------------------------------------------------------------------------------------------- notices and unsubscribe
def _secret(settings, purpose):
    from ..oidc import _session_secret
    return hmac.new(_session_secret(settings), b"company-page-reader:" + purpose, hashlib.sha256).digest()


def signed(settings, purpose, value):
    return hmac.new(_secret(settings, purpose), value.encode(), hashlib.sha256).hexdigest()


def unsubscribe_token(settings, reader_id):
    """Good only for turning this reader's notices off."""
    encoded = base64.urlsafe_b64encode(reader_id.encode()).rstrip(b"=").decode()
    return encoded + "." + signed(settings, b"unsubscribe", reader_id)


def unsubscribe_reader(settings, token):
    """The reader id a good unsubscribe token names, or None."""
    encoded, _, mac = str(token or "").partition(".")
    try:
        reader_id = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except ValueError:
        return None
    good = signed(settings, b"unsubscribe", reader_id)
    return reader_id if reader_id and hmac.compare_digest(mac.encode(), good.encode()) else None


def notify_edition(c, edition_id):
    """A link-only notice of a published edition to the readers of its audience with notices on. Inside the caller's
    write it only queues them; the mail goes after the commit, so a slow issuer never holds a write and a rolled-back
    publish sends nothing. {"status": queued | no_edition | closed | unavailable | rehearsal, "count"}."""
    settings, store = _state["settings"], _state["store"]
    if not settings or not availability(settings)[0]:
        return {"status": "unavailable", "count": 0}
    if rehearsing(settings):
        return {"status": "rehearsal", "count": 0}
    if transparency_of(c) == "closed":
        return {"status": "closed", "count": 0}
    edition = c.execute("SELECT audience_id FROM editions WHERE id=? AND status='published' AND retracted_at IS NULL",
                        (edition_id,)).fetchone()
    if not edition:
        return {"status": "no_edition", "count": 0}
    now, count = H.now(), 0
    rows = c.execute("SELECT r.id FROM audience_readers ar JOIN readers r ON r.id=ar.reader_id JOIN audiences a "
                     "ON a.id=ar.audience_id WHERE ar.audience_id=? AND a.enabled=1 AND r.notify=1 AND r.removed_at IS NULL",
                     (edition["audience_id"],)).fetchall()
    for row in rows:
        count += c.execute("INSERT OR IGNORE INTO reader_notices(id,edition_id,reader_id,created_at,updated_at) "
                           "VALUES(?,?,?,?,?)", (H.new_id(), edition_id, row["id"], now, now)).rowcount
    if count and store is not None and hasattr(c, "after_commit"):
        c.after_commit.append(lambda: kick())
    return {"status": "queued", "count": count}


def rehearsing(settings):
    """A rehearsal is a copy of real data, and the addresses in it are real people's: it sends nothing."""
    from ..replication import rehearsal_on
    return bool(getattr(settings, "rehearsal", False)) or rehearsal_on()


_RUNNING, _AGAIN = threading.Lock(), threading.Event()


def kick():
    """Send what is waiting on a thread of its own, unless one is running; a running one sees what was just added."""
    _AGAIN.set()
    if not _RUNNING.acquire(blocking=False):
        return

    def work():
        try:
            while _AGAIN.is_set():
                _AGAIN.clear()
                drain()
        finally:
            _RUNNING.release()
    threading.Thread(target=work, name="tico-reader-notices", daemon=True).start()


def tick(store):
    """For the scheduler: what a restart left waiting goes out."""
    if store is not _state["store"]:
        return
    with store.read() as c:
        if c.execute("SELECT 1 FROM reader_notices WHERE status='pending' LIMIT 1").fetchone():
            kick()


def drain():
    """Send what is waiting. Each is looked at again first: a reader removed, an edition retracted or Closed since it was
    queued sends nothing. An issuer that is down or says "too many" ends the pass with the rest still waiting, for the
    next `tick`. Never logs an address."""
    from ..oidc import SignInError
    store, settings, oidc = _state["store"], _state["settings"], _state["oidc"]
    if store is None or oidc is None or rehearsing(settings):
        return
    while True:
        with store.read() as c:
            waiting = [dict(r) for r in c.execute(
                "SELECT n.id,n.reader_id,n.attempts,r.email,r.notify,r.removed_at,e.status,e.retracted_at "
                "FROM reader_notices n JOIN readers r ON r.id=n.reader_id JOIN editions e ON e.id=n.edition_id "
                "WHERE n.status='pending' ORDER BY n.created_at,n.id LIMIT 200")]
            closed = transparency_of(c) == "closed"
        if not waiting:
            return
        for row in waiting:
            if closed or row["removed_at"] or not row["notify"] or row["status"] != "published" or row["retracted_at"]:
                _notice_done(store, row["id"], "cancelled")
                continue
            try:
                answer = oidc.send_notice(row["email"], settings.company_name, settings.reader_origin + "/",
                                          settings.reader_origin + "/unsubscribe?token="
                                          + unsubscribe_token(settings, row["reader_id"]))
            except SignInError as exc:
                log.warning("Reader notices wait: %s", exc.reason)
                return _notice_retry(store, row)
            status = answer[0] if answer else None
            if answer is None or status == 409:     # this issuer sends no notices, or doesn't know the reader address
                _notice_done(store, row["id"], "unavailable")
            elif status == 202:
                _notice_done(store, row["id"], "sent")
            elif status == 429:                     # not this reader's fault: the install's hourly allowance
                log.info("Reader notices wait: the issuer's limit")
                return
            elif status >= 500:
                log.warning("Reader notices wait: the issuer answered HTTP %s", status)
                return _notice_retry(store, row)
            else:
                log.warning("The issuer refused a reader notice: HTTP %s", status)
                _notice_done(store, row["id"], "failed")


def _notice_done(store, notice_id, status):
    with store.transaction() as c:
        c.execute("UPDATE reader_notices SET status=?,attempts=attempts+1,updated_at=? WHERE id=?",
                  (status, H.now(), notice_id))


def _notice_retry(store, row):
    with store.transaction() as c:
        status = "failed" if row["attempts"] + 1 >= NOTICE_ATTEMPTS else "pending"
        c.execute("UPDATE reader_notices SET status=?,attempts=attempts+1,updated_at=? WHERE id=?",
                  (status, H.now(), row["id"]))
