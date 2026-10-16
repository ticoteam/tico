"""Support tickets: what a person writes to the Tico team from their app, and what the team writes back.

    POST   /v1/support                     a person files a ticket, with redacted diagnostics if they chose; the answer is {ticket_id, secret}
    GET    /v1/support/{ticket_id}         that person's app asks for its status and messages (the ticket's secret)
    POST   /v1/support/{ticket_id}/messages   the person adds a message to a ticket that is not closed (the secret)
    DELETE /v1/support/{ticket_id}         the person deletes their ticket (the secret)
    GET    /v1/staff/stats                 exact active installs per release, last 7 days (HQ_STAFF_KEY)
    GET    /v1/staff/tickets               the team lists tickets              (HQ_STAFF_KEY)
    GET    /v1/staff/tickets/{id}          one ticket with its messages and diagnostics (HQ_STAFF_KEY)
    POST   /v1/staff/tickets/{id}/reply    a reply the person's app will fetch (HQ_STAFF_KEY)
    POST   /v1/staff/tickets/{id}/status   open, answered or closed            (HQ_STAFF_KEY)
    POST   /v1/staff/tickets/by-signature/close   one reply on every open ticket with a Health Monitor
        signature, then each closed: what a release that ships its fix calls (HQ_STAFF_KEY)
    DELETE /v1/staff/tickets/{id}          delete a ticket on request          (HQ_STAFF_KEY)
    GET    /v1/staff/tickets?status=held   tickets the judge called spam: kept quiet, never listed elsewhere (HQ_STAFF_KEY)
    POST   /v1/staff/tickets/{id}/verdict  correct a verdict (legit, spam, injection_risk, unchecked); legit releases a held one
    POST   /v1/staff/judge                 {text} -> {verdict, reason}: the same check for GitHub threads (HQ_STAFF_KEY)

Each new ticket is classified before any bot reads it (judge.py): `legit`, `spam`, `injection_risk` or `unchecked`. Only the
verdict and a short reason are stored; a `spam` ticket is held and the queue skips it until a person corrects the verdict.

A ticket is untrusted text from anyone on the internet. It is validated strictly, stored as plain text and never
interpreted, and it is never logged. The address of a request is used for rate limiting in memory (limits.py) and
nowhere else. The ticket's secret is shown once, in the answer to POST; HQ keeps only its SHA-256, so the database
cannot be used to read another person's replies. Nothing deletes a ticket by itself: it is kept until the person who
filed it, or the team on their request, deletes it. PRIVACY.md is the statement of it; docs/support.md is how the team
works the queue.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timezone

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from .limits import Limiter

MAX_MESSAGES = 60                 # a ticket's thread, both sides
MAX_MESSAGE = 4000
MAX_REPLY = 8000
MAX_FOLLOW_UP = 16 * 1024         # bytes of a message added to a ticket; a full message is well under this
MAX_DIAGNOSTICS = 256 * 1024      # bytes of the diagnostics bundle a person may attach (backend/diagnostics.py)
MAX_REQUEST = MAX_FOLLOW_UP + MAX_DIAGNOSTICS + 1024      # a ticket request
DIAGNOSTICS_DEPTH = 6
MAX_STAFF_REQUEST = 48 * 1024
STATUSES = ("open", "answered", "closed")
VERDICTS = ("legit", "spam", "injection_risk", "unchecked")
MAX_JUDGED = 8000                 # characters of a GitHub thread or email sent to the judge
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

TICKET_ID = re.compile(r"TK-[A-Z2-7]{8}")
SIGNATURE = re.compile(r"hm-[0-9a-f]{12}")
SECRET = re.compile(r"[A-Za-z0-9_-]{20,64}")
# The same shapes hq/app.py accepts for a ping.
INSTALL_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?")
EMAIL = re.compile(r"[^@\s<>\",;()\[\]\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,24}")
MOMENT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ‪-‮⁦-⁩]")
FIELDS = ("message", "email", "install_id", "version")
OPTIONAL = ("diagnostics",)       # the person's own choice; staff routes only ever show it
# The desktop app's version, when the person wrote from the app; labelled apart from the server's `version`.
APP_FIELDS = ("app_version",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets(
 ticket_id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
 closed TEXT, status TEXT NOT NULL DEFAULT 'open', body TEXT NOT NULL, email TEXT, install_id TEXT, version TEXT, app_version TEXT,
 email_pending INTEGER NOT NULL DEFAULT 0, diagnostics TEXT, signature TEXT NOT NULL DEFAULT '',
 verdict TEXT NOT NULL DEFAULT 'unchecked', verdict_reason TEXT NOT NULL DEFAULT '', held INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS tickets_created ON tickets(created);
CREATE TABLE IF NOT EXISTS ticket_verdicts(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, at TEXT NOT NULL, old TEXT NOT NULL, new TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS ticket_replies(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, created TEXT NOT NULL, body TEXT NOT NULL,
 author TEXT NOT NULL DEFAULT 'staff', diagnostics TEXT);
CREATE INDEX IF NOT EXISTS ticket_replies_ticket ON ticket_replies(ticket_id, id);
"""


def utcnow():
    return datetime.now(timezone.utc)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def signature_of(message):
    """The Health Monitor signature a ticket carries (`Signature: hm-...` in its body), or "": the same problem has
    the same signature on every install, so the team groups and closes tickets by it."""
    found = SIGNATURE.search(str(message or ""))
    return found.group(0) if found else ""


class Invalid(Exception):
    """A request refused; `field` names the one thing wrong, never its value."""

    def __init__(self, field):
        super().__init__(field)
        self.field = field


def text(value, field, limit):
    """Plain text of 1..limit characters: a string, no control characters, no blank message."""
    if not isinstance(value, str) or CONTROL.search(value):
        raise Invalid(field)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip() or len(value) > limit:
        raise Invalid(field)
    return value.strip()


def _plain(value, depth=0):
    """Whether `value` is JSON made only of strings, numbers, booleans, null, lists and objects, not deeply nested,
    with no control character in any string."""
    if depth > DIAGNOSTICS_DEPTH:
        return False
    if isinstance(value, str):
        return not CONTROL.search(value)
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_plain(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and not CONTROL.search(k) and _plain(v, depth + 1) for k, v in value.items())
    return False


def parse_diagnostics(data):
    """The compact JSON of the attached diagnostics, None when there are none, or Invalid. HQ checks the shape and the
    size; what may be in it is the sending Tico's allowlist and redactor (backend/diagnostics.py)."""
    value = data.get("diagnostics") if isinstance(data, dict) else None
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("format") != 1 or not _plain(value):
        raise Invalid("diagnostics")
    packed = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    if len(packed.encode()) > MAX_DIAGNOSTICS:
        raise Invalid("diagnostics")
    return packed


def parse_ticket(data):
    """(message, email, install_id, version, app_version) from a decoded request, or Invalid. Fields beyond these (and the
    optional diagnostics, which `parse_diagnostics` reads) are refused."""
    if not isinstance(data, dict) or set(data) - set(FIELDS) - set(APP_FIELDS) - set(OPTIONAL):
        raise Invalid("fields")
    message = text(data.get("message"), "message", MAX_MESSAGE)
    values = {}
    for name, pattern in (("email", EMAIL), ("install_id", INSTALL_ID), ("version", VERSION), ("app_version", VERSION)):
        value = data.get(name)
        if value in (None, ""):
            values[name] = None
        elif not isinstance(value, str) or (name == "email" and len(value) > 254) or not pattern.fullmatch(value):
            raise Invalid(name)
        else:
            values[name] = value
    return message, values["email"], values["install_id"], values["version"], values["app_version"]


class Tickets:
    """The tickets table and the limits in front of it. Shares the installs database's connection and lock."""

    def __init__(self, db, now=utcnow, clock=time.monotonic):
        self.db, self.now = db, now
        with db.lock:
            db.conn.execute("PRAGMA secure_delete=ON")       # a deleted ticket's text is overwritten, not just unlinked
            db.conn.executescript(SCHEMA)
            # A database from before diagnostics gets the column once; running this again changes nothing.
            have = {row[1] for row in db.conn.execute("PRAGMA table_info(tickets)")}
            if "diagnostics" not in have:
                db.conn.execute("ALTER TABLE tickets ADD COLUMN diagnostics TEXT")
            if "signature" not in have:
                db.conn.execute("ALTER TABLE tickets ADD COLUMN signature TEXT NOT NULL DEFAULT ''")
                for row in db.conn.execute("SELECT ticket_id, body FROM tickets WHERE signature=''"):
                    found = signature_of(row["body"])
                    if found:
                        db.conn.execute("UPDATE tickets SET signature=? WHERE ticket_id=?", (found, row["ticket_id"]))
            for column, spec in (("verdict", "TEXT NOT NULL DEFAULT 'unchecked'"), ("verdict_reason", "TEXT NOT NULL DEFAULT ''"),
                                 ("held", "INTEGER NOT NULL DEFAULT 0"), ("app_version", "TEXT")):
                if column not in have:
                    db.conn.execute(f"ALTER TABLE tickets ADD COLUMN {column} {spec}")
            reply_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(ticket_replies)")}
            if "diagnostics" not in reply_columns:
                db.conn.execute("ALTER TABLE ticket_replies ADD COLUMN diagnostics TEXT")
        # In memory, as the request limit is: a salted hash of the address, or of the install id, forgotten on restart.
        self.per_address = Limiter(limit=5, window=3600, clock=clock)         # tickets an hour from one address
        self.per_install = Limiter(limit=10, window=86400, clock=clock)       # tickets a day from one install id
        self.overall = Limiter(limit=1000, window=86400, clock=clock)         # tickets a day in all: a flood cap
        self.reads = Limiter(limit=600, window=3600, clock=clock)             # a person's app asking for replies
        self.follow_ups = Limiter(limit=30, window=3600, clock=clock)         # messages added to tickets, per address
        self.staff = Limiter(limit=1200, window=3600, clock=clock)
        self.judged = Limiter(limit=5000, window=86400, clock=clock)          # judge calls a day, all callers: a spend cap
        self.refused = Limiter(limit=20, window=3600, clock=clock)            # wrong staff keys, per address

    # ------------------------------------------------------------------ people
    def create(self, message, email, install_id, version, diagnostics=None, verdict="unchecked", reason="", app_version=None):
        """Store a ticket. Returns (ticket_id, secret); the secret is not kept, only its hash. `diagnostics` is the
        attached bundle as compact JSON; it is kept with the ticket until the ticket is deleted."""
        secret = secrets.token_urlsafe(24)
        moment = stamp(self.now())
        with self.db.lock:
            while True:
                ticket_id = "TK-" + "".join(secrets.choice(ALPHABET) for _ in range(8))
                if not self.db.conn.execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone():
                    break
            self.db.conn.execute(
                "INSERT INTO tickets(ticket_id,key_hash,created,updated,status,body,email,install_id,version,app_version,"
                "diagnostics,signature,verdict,verdict_reason,held) VALUES(?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?)",
                (ticket_id, digest(secret), moment, moment, message, email, install_id, version, app_version, diagnostics,
                 signature_of(message), verdict, reason[:200], 1 if verdict == "spam" else 0))
        return ticket_id, secret

    def _owned(self, ticket_id, secret):
        """The ticket row when `secret` is its secret, else None: the same for an unknown ticket and a wrong secret.
        Caller holds the lock."""
        row = self.db.conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        expected = row["key_hash"] if row else digest("no such ticket")
        return row if hmac.compare_digest(digest(secret), expected) and row else None

    def for_person(self, ticket_id, secret):
        """What the person's app may see: the status and the messages. None for an unknown ticket or a wrong secret."""
        with self.db.lock:
            row = self._owned(ticket_id, secret)
            if not row:
                return None
            messages = self._messages(ticket_id)
        return {"ticket_id": ticket_id, "status": row["status"], "created": row["created"],
                "updated": row["updated"], "messages": messages}

    def add_message(self, ticket_id, secret, body, diagnostics=None):
        """The person writes again. The ticket reopens if it was answered. Returns the new state, None for an unknown
        ticket or wrong secret, False for a closed or full one."""
        moment = stamp(self.now())
        with self.db.lock:
            row = self._owned(ticket_id, secret)
            if not row:
                return None
            count = self.db.conn.execute("SELECT count(*) FROM ticket_replies WHERE ticket_id=?", (ticket_id,)).fetchone()[0]
            if row["status"] == "closed" or count >= MAX_MESSAGES:
                return False
            message = self.db.conn.execute("INSERT INTO ticket_replies(ticket_id,created,body,author,diagnostics) VALUES(?,?,?,'person',?)",
                                           (ticket_id, moment, body, diagnostics)).lastrowid
            self.db.conn.execute("UPDATE tickets SET status='open', updated=? WHERE ticket_id=?", (moment, ticket_id))
        return {"ticket_id": ticket_id, "status": "open", "message_id": message}

    def delete(self, ticket_id, secret=None):
        """Remove a ticket and its messages, overwritten in the file. With `secret`, only if it is the person's own.
        Returns whether there was one."""
        with self.db.lock:
            if secret is not None and not self._owned(ticket_id, secret):
                return False
            self.db.conn.execute("DELETE FROM ticket_replies WHERE ticket_id=?", (ticket_id,))
            found = self.db.conn.execute("DELETE FROM tickets WHERE ticket_id=?", (ticket_id,)).rowcount
            if found:
                self.db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return bool(found)

    # ------------------------------------------------------------------ staff
    def _messages(self, ticket_id, full=False):
        out = []
        for r in self.db.conn.execute("SELECT * FROM ticket_replies WHERE ticket_id=? ORDER BY id", (ticket_id,)):
            message = {"id": r["id"], "created": r["created"], "from": r["author"], "body": r["body"]}
            if r["diagnostics"]:
                message["has_diagnostics"] = True
                if full:
                    message["diagnostics"] = json.loads(r["diagnostics"])
            out.append(message)
        return out

    def _staff_view(self, row, full=False):
        """A ticket for the staff routes. The diagnostics bundle is large: a listing says whether there is one, and
        `show` (`full`) carries it."""
        view = {"ticket_id": row["ticket_id"], "status": row["status"], "created": row["created"],
                "updated": row["updated"], "body": row["body"], "email": row["email"], "version": row["version"],
                "app_version": row["app_version"], "install_id": row["install_id"], "email_pending": bool(row["email_pending"]),
                "signature": row["signature"] or signature_of(row["body"]),
                "has_diagnostics": bool(row["diagnostics"]), "messages": self._messages(row["ticket_id"], full=full),
                "verdict": row["verdict"], "verdict_reason": row["verdict_reason"], "held": bool(row["held"])}
        if full and row["diagnostics"]:
            view["diagnostics"] = json.loads(row["diagnostics"])
        return view

    def listing(self, status="", since="", limit=50, signature=""):
        """Oldest activity first, so a caller that remembers the last `updated` it saw misses nothing. A ticket appears
        again whenever its person writes or the team replies. With `signature`, only tickets carrying that Health
        Monitor signature, so the team works one signature's tickets together."""
        where, args = [], []
        # A ticket held as spam is in the held list and nowhere else, so the queue and the bot never see it.
        where.append("held=1" if status == "held" else "held=0")
        if status and status not in ("all", "held"):
            where.append("status=?")
            args.append(status)
        if since:
            where.append("updated>?")
            args.append(since)
        if signature:
            where.append("signature=?")
            args.append(signature)
        sql = "SELECT * FROM tickets" + (" WHERE " + " AND ".join(where) if where else "")
        with self.db.lock:
            rows = self.db.conn.execute(sql + " ORDER BY updated, ticket_id LIMIT ?", (*args, limit)).fetchall()
            return [self._staff_view(r) for r in rows]

    def correct(self, ticket_id, verdict, note=""):
        """A person's verdict replaces the judge's, and is recorded (old, new, when) for tuning it. `legit` or anything but
        `spam` releases a held ticket: it is new work again. Returns the ticket's verdict, or None for an unknown ticket."""
        moment = stamp(self.now())
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not row:
                return None
            held = 1 if verdict == "spam" else 0
            self.db.conn.execute("INSERT INTO ticket_verdicts(ticket_id,at,old,new,note) VALUES(?,?,?,?,?)",
                                 (ticket_id, moment, row["verdict"], verdict, note[:200]))
            self.db.conn.execute("UPDATE tickets SET verdict=?, verdict_reason=?, held=?, updated=? WHERE ticket_id=?",
                                 (verdict, ("corrected by staff: " + note)[:200] if note else "corrected by staff", held, moment, ticket_id))
        return {"ticket_id": ticket_id, "verdict": verdict, "held": bool(held), "was": row["verdict"]}

    def show(self, ticket_id):
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            return self._staff_view(row, full=True) if row else None

    def reply(self, ticket_id, body):
        """Record a reply for the install to fetch. A ticket with an email address is marked "email pending": HQ sends
        no mail, the Support bot drafts it. Returns the ticket's new state, None for an unknown ticket, and False for
        a closed one."""
        moment = stamp(self.now())
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not row:
                return None
            if row["status"] == "closed":
                return False
            reply = self.db.conn.execute("INSERT INTO ticket_replies(ticket_id,created,body,author) VALUES(?,?,?,'staff')",
                                         (ticket_id, moment, body)).lastrowid
            pending = 1 if row["email"] else 0
            self.db.conn.execute("UPDATE tickets SET status='answered', updated=?, email_pending=? WHERE ticket_id=?",
                                 (moment, pending or row["email_pending"], ticket_id))
        return {"ticket_id": ticket_id, "status": "answered", "reply_id": reply, "email_pending": bool(pending)}

    def set_status(self, ticket_id, status=None, email_sent=False):
        moment = stamp(self.now())
        with self.db.lock:
            row = self.db.conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not row:
                return None
            status = status or row["status"]
            closed = (row["closed"] or moment) if status == "closed" else None
            pending = 0 if email_sent else row["email_pending"]
            self.db.conn.execute("UPDATE tickets SET status=?, closed=?, email_pending=?, updated=? WHERE ticket_id=?",
                                 (status, closed, pending, moment, ticket_id))
        return {"ticket_id": ticket_id, "status": status, "email_pending": bool(pending)}

    def close_signature(self, signature, body):
        """When a release ships the fix for one Health Monitor signature: the same reply on every open or answered
        ticket carrying it, then each closed. A ticket with an email address keeps it marked email pending: HQ sends
        no mail, the Support bot drafts it. Returns each ticket closed, and how many already were."""
        body = text(body, "body", MAX_REPLY)
        moment = stamp(self.now())
        with self.db.lock:
            rows = self.db.conn.execute("SELECT * FROM tickets WHERE signature=? AND held=0", (signature,)).fetchall()
            open_rows = [r for r in rows if r["status"] in ("open", "answered")]
            closed, skipped = [], 0
            for row in open_rows:
                reply_id = self.db.conn.execute(
                    "INSERT INTO ticket_replies(ticket_id,created,body,author) VALUES(?,?,?,'staff')",
                    (row["ticket_id"], moment, body)).lastrowid
                self.db.conn.execute("UPDATE tickets SET status='closed', closed=?, email_pending=?, updated=? "
                                     "WHERE ticket_id=?",
                                     (row["closed"] or moment, 1 if row["email"] else row["email_pending"], moment,
                                      row["ticket_id"]))
                closed.append({"ticket_id": row["ticket_id"], "reply_id": reply_id,
                               "email_pending": bool(row["email"] or row["email_pending"])})
            skipped = sum(1 for r in rows if r["status"] == "closed")
        return {"signature": signature, "closed": closed, "already_closed": skipped}


# ---------------------------------------------------------------------- the routes
def no_store(body, status=200):
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def refuse(error, status, **extra):
    return no_store({"error": error, **extra}, status)


async def read_json(request, limit):
    """The request's JSON, or a (status, error) pair. The body is read only up to `limit` bytes."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return 413, "too_large"
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            return 413, "too_large"
    request.state.body_size = len(raw)
    try:
        return 200, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return 422, "invalid"


def install(app, tickets, address, staff_key="", judge=None):
    """Add the routes. `address(request)` is who is asking, for the rate limits only. `judge(text) -> (verdict, reason)`
    classifies what arrives (judge.py); it never raises, and without one every verdict is `unchecked`. Returns the
    staff key check, for the other staff routes (notices.py)."""
    key_digest = digest(staff_key) if staff_key else ""
    judge = judge or (lambda text: ("unchecked", "no judge"))

    @app.post("/v1/support")
    async def file_ticket(request: Request):
        who = address(request)
        if not tickets.per_address.allow(who) or not tickets.overall.allow("all"):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "3600"})
        status, data = await read_json(request, MAX_REQUEST)
        if status != 200:
            return refuse(data, status)
        if request.state.body_size > MAX_FOLLOW_UP and not (isinstance(data, dict) and "diagnostics" in data):
            return refuse("too_large", 413)          # only the diagnostics may make a request this big
        try:
            message, email, install_id, version, app_version = parse_ticket(data)
            diagnostics = parse_diagnostics(data)
        except Invalid as bad:
            return refuse("invalid", 422, field=bad.field)
        if install_id and not tickets.per_install.allow(install_id):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "86400"})
        verdict, reason = await run_in_threadpool(judge, message)
        ticket_id, secret = tickets.create(message, email, install_id, version, diagnostics,
                                           verdict if verdict in VERDICTS else "unchecked", reason, app_version)
        return no_store({"ticket_id": ticket_id, "secret": secret, "status": "open"}, 201)

    @app.get("/v1/support/{ticket_id}")
    def ticket_for_person(request: Request, ticket_id: str):
        if not tickets.reads.allow(address(request)):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "3600"})
        secret = request.headers.get("x-ticket-secret") or request.query_params.get("secret") or ""
        found = None
        if TICKET_ID.fullmatch(ticket_id) and SECRET.fullmatch(secret):
            found = tickets.for_person(ticket_id, secret)
        return no_store(found) if found else refuse("not_found", 404)

    def secret_of(request, ticket_id):
        secret = request.headers.get("x-ticket-secret") or request.query_params.get("secret") or ""
        return secret if TICKET_ID.fullmatch(ticket_id) and SECRET.fullmatch(secret) else ""

    @app.post("/v1/support/{ticket_id}/messages")
    async def person_writes(request: Request, ticket_id: str):
        if not tickets.follow_ups.allow(address(request)):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "3600"})
        status, data = await read_json(request, MAX_REQUEST)
        if status != 200:
            return refuse(data, status)
        if request.state.body_size > MAX_FOLLOW_UP and not (isinstance(data, dict) and "diagnostics" in data):
            return refuse("too_large", 413)
        try:
            if not isinstance(data, dict) or "message" not in data or set(data) - {"message", "diagnostics"}:
                raise Invalid("fields")
            body = text(data["message"], "message", MAX_MESSAGE)
            diagnostics = parse_diagnostics(data)
        except Invalid as bad:
            return refuse("invalid", 422, field=bad.field)
        secret = secret_of(request, ticket_id)
        done = tickets.add_message(ticket_id, secret, body, diagnostics) if secret else None
        if done is None:
            return refuse("not_found", 404)
        return no_store(done, 201) if done else refuse("closed", 409)

    @app.delete("/v1/support/{ticket_id}")
    def person_deletes(request: Request, ticket_id: str):
        if not tickets.reads.allow(address(request)):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "3600"})
        secret = secret_of(request, ticket_id)
        return no_store({"deleted": True}) if secret and tickets.delete(ticket_id, secret) else refuse("not_found", 404)

    def staff_only(request):
        """None when the caller holds the staff key, else the refusal. With no key configured the routes do not exist."""
        if not key_digest:
            return refuse("not_found", 404)
        who = address(request)
        if not tickets.staff.allow(who):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "3600"})
        header = request.headers.get("authorization", "")
        given = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if given and hmac.compare_digest(digest(given), key_digest):
            return None
        if not tickets.refused.allow(who):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "3600"})
        return refuse("unauthorized", 401)

    def known(ticket_id):
        return TICKET_ID.fullmatch(ticket_id)

    @app.get("/v1/staff/stats")
    def staff_stats(request: Request):
        denied = staff_only(request)
        if denied:
            return denied
        return no_store({"by_version": tickets.db.release_counts(), "window_days": 7})

    @app.get("/v1/staff/tickets")
    def staff_list(request: Request):
        denied = staff_only(request)
        if denied:
            return denied
        params = request.query_params
        status, since = params.get("status", ""), params.get("since", "")
        limit = params.get("limit", "50")
        signature = params.get("signature", "")
        if (status and status not in STATUSES + ("all", "held")) or (since and not MOMENT.fullmatch(since)) \
                or not limit.isdigit() or not 1 <= int(limit) <= 200 \
                or (signature and not SIGNATURE.fullmatch(signature)):
            return refuse("invalid", 422)
        return no_store({"tickets": tickets.listing(status, since, int(limit), signature)})

    @app.get("/v1/staff/tickets/{ticket_id}")
    def staff_show(request: Request, ticket_id: str):
        denied = staff_only(request)
        if denied:
            return denied
        found = tickets.show(ticket_id) if known(ticket_id) else None
        return no_store(found) if found else refuse("not_found", 404)

    @app.delete("/v1/staff/tickets/{ticket_id}")
    def staff_delete(request: Request, ticket_id: str):
        denied = staff_only(request)
        if denied:
            return denied
        return no_store({"deleted": True}) if known(ticket_id) and tickets.delete(ticket_id) else refuse("not_found", 404)

    @app.post("/v1/staff/tickets/{ticket_id}/reply")
    async def staff_reply(request: Request, ticket_id: str):
        denied = staff_only(request)
        if denied:
            return denied
        status, data = await read_json(request, MAX_STAFF_REQUEST)
        if status != 200:
            return refuse(data, status)
        try:
            if not isinstance(data, dict) or set(data) != {"body"}:
                raise Invalid("fields")
            body = text(data["body"], "body", MAX_REPLY)
        except Invalid as bad:
            return refuse("invalid", 422, field=bad.field)
        done = tickets.reply(ticket_id, body) if known(ticket_id) else None
        if done is None:
            return refuse("not_found", 404)
        return no_store(done) if done else refuse("closed", 409)

    @app.post("/v1/staff/tickets/by-signature/close")
    async def staff_close_signature(request: Request):
        """A release shipped the fix for one Health Monitor signature: reply the same fixed-in text to every open or
        answered ticket carrying it, and close each. Running it again closes nothing new."""
        denied = staff_only(request)
        if denied:
            return denied
        status, data = await read_json(request, MAX_STAFF_REQUEST)
        if status != 200:
            return refuse(data, status)
        try:
            if not isinstance(data, dict) or set(data) != {"signature", "body"}:
                raise Invalid("fields")
            if not isinstance(data["signature"], str) or not SIGNATURE.fullmatch(data["signature"]):
                raise Invalid("signature")
            done = tickets.close_signature(data["signature"], data["body"])
        except Invalid as bad:
            return refuse("invalid", 422, field=bad.field)
        return no_store(done)

    @app.post("/v1/staff/tickets/{ticket_id}/status")
    async def staff_status(request: Request, ticket_id: str):
        denied = staff_only(request)
        if denied:
            return denied
        status, data = await read_json(request, MAX_STAFF_REQUEST)
        if status != 200:
            return refuse(data, status)
        if not isinstance(data, dict) or not data or set(data) - {"status", "email_sent"} \
                or data.get("status", "open") not in STATUSES or not isinstance(data.get("email_sent", False), bool):
            return refuse("invalid", 422)
        done = tickets.set_status(ticket_id, data.get("status"), data.get("email_sent") is True) \
            if known(ticket_id) else None
        return no_store(done) if done else refuse("not_found", 404)

    @app.post("/v1/staff/tickets/{ticket_id}/verdict")
    async def staff_verdict(request: Request, ticket_id: str):
        denied = staff_only(request)
        if denied:
            return denied
        status, data = await read_json(request, MAX_STAFF_REQUEST)
        if status != 200:
            return refuse(data, status)
        if not isinstance(data, dict) or set(data) - {"verdict", "note"} or data.get("verdict") not in VERDICTS \
                or not isinstance(data.get("note", ""), str) or CONTROL.search(data.get("note", "")):
            return refuse("invalid", 422)
        done = tickets.correct(ticket_id, data["verdict"], data.get("note", "").strip()) if known(ticket_id) else None
        return no_store(done) if done else refuse("not_found", 404)

    @app.post("/v1/staff/judge")
    async def staff_judge(request: Request):
        """The same check for text that is not a ticket (a GitHub thread): {text} -> {verdict, reason}. Nothing is kept."""
        denied = staff_only(request)
        if denied:
            return denied
        status, data = await read_json(request, MAX_STAFF_REQUEST)
        if status != 200:
            return refuse(data, status)
        if not isinstance(data, dict) or set(data) != {"text"} or not isinstance(data["text"], str) or not data["text"].strip():
            return refuse("invalid", 422)
        if not tickets.judged.allow("all"):
            return no_store({"verdict": "unchecked", "reason": "the daily judge limit is reached"})
        verdict, reason = await run_in_threadpool(judge, data["text"][:MAX_JUDGED])
        return no_store({"verdict": verdict if verdict in VERDICTS else "unchecked", "reason": reason})

    return staff_only
