"""Letting Tico support in: a person opens a time-limited session from one of their support tickets (docs/support.md,
"Letting Tico support in"), and the Tico team, or its Support Agent, uses this app through Tico HQ until it ends.

An install is behind NAT or on loopback, so nothing dials in: while a session is live this server long-polls HQ
(`POST <TICO_HQ_URL>/v1/support/<ticket>/access/<session>/pull`, docs/tico-hq.md, with the ticket's secret), runs
each request it is handed against itself in-process with the session's own token, and posts the answer back. Every
request is logged on the session, and the person who started it reads the log under the ticket. A session's id is the one
HQ gave it, and its events name the ticket's HQ id (`TK-...`) too, so what this install keeps matches HQ and admin.

Two tiers. `app` is the Support role: an admin's reads, and writes only on the short setup list below (`APP_WRITES`).
`full` is the owner's reach in the app. Neither tier makes or sees tokens and keys, reveals a credential's value,
hands the install to someone else, or touches these sessions (`ALWAYS_REFUSED`); `gate` decides it on every request,
in Auth.authenticate. The session token (`tico_sa_...`) lives in this process's memory only and its digest in the row;
a restart mints a fresh one. A session ends when the person stops it, when it expires (checked against the time on
every request, so no timer is needed), when HQ ends it, or when the feature is turned off.

Off: `TICO_SUPPORT=off`, demo and rehearsal mode (support.off_reason), the team rule `support_access`, and, until the
security review is done, unless `TICO_SUPPORT_ACCESS=on`.
"""
import asyncio
import base64
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import Field

from . import support, team_rules
from .auth import Identity
from .models import Contract
from .names import SUPPORT_ACTOR
from .store import H, Problem, digest

log = logging.getLogger("tico.support_access")

SCHEMA = """
CREATE TABLE IF NOT EXISTS support_access(
 id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, tier TEXT NOT NULL, computer_id TEXT, started_by TEXT NOT NULL,
 started_at TEXT NOT NULL, expires_at TEXT NOT NULL, ended_at TEXT, ended_by TEXT, end_reason TEXT, token_hash TEXT,
 hq_session TEXT);
CREATE INDEX IF NOT EXISTS support_access_live ON support_access(ended_at, expires_at);
CREATE TABLE IF NOT EXISTS support_access_log(
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL, by TEXT NOT NULL, method TEXT, path TEXT,
 status INTEGER);
CREATE INDEX IF NOT EXISTS support_access_log_session ON support_access_log(session_id, id);
"""
TIERS = ("app", "full")
TOKEN_PREFIX = "tico_sa_"
LABEL = "Tico support"
DEFAULT_HOURS, MAX_HOURS = 24, 720
PULL_WAIT = 30               # seconds HQ may hold a pull open before answering with nothing
DISPATCH_WAIT = 25           # one request run against this app; HQ gives up on it after 30
MAX_BODY = 2 * 1024 * 1024   # bytes of one answer carried back
MAX_BATCH = 20               # requests taken from one pull
LOGGED = 500
LISTED = 50                  # sessions listed under a person's requests
WORKER = True                # tests drive `pull_once` themselves
HQ_SESSION = re.compile(r"sa_[0-9a-f]{24}")
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
API_PATH = re.compile(r"/api/v2/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*")

# Refused to both tiers: sessions themselves, signing in and out, anything that makes or shows a token, key or
# credential value, and handing the install to someone else.
ALWAYS_REFUSED = re.compile(r"/api/v2/(?:"
                            r"support/access(?:/.*)?|logout|local-signin|oauth/.*|mcp/oauth.*"
                            r"|me/tokens(?:/.*)?|access/tokens(?:/.*)?|service-keys(?:/.*)?|enrollments"
                            r"|agents/pairings(?:/.*)?|directory/scim-token|github/token"
                            r"|bots/[^/]+/(?:agent-credential(?:/revoke)?|repository-read-token)"
                            r"|access/owner(?:/email)?"
                            r"|credentials/[^/]+/reveal|credential-runtime|runner-[a-z-]*credential[a-z-]*(?:/.*)?"
                            r")")
# Pages that open a person's own room or calendar: Tico support has neither, so it gets a plain refusal.
PERSONAL = re.compile(r"/api/v2/(?:tasks/[^/]+/chat|assistant|calendar/upcoming)")
# The one path form the gate reads: no empty, dot or percent-encoded segment, so the gate and the router read the same
# address.
PLAIN_PATH = re.compile(r"/api/v2(?:/[A-Za-z0-9_~@+=:-][A-Za-z0-9._~@+=:-]*)+")
# The app tier reads what an admin reads, except a run's raw provider stream, which may hold what the secret scrub missed,
# and a model sign-in's link and code: whoever enters the code first chooses the account the computer runs on.
APP_READS_REFUSED = re.compile(r"/api/v2/(?:attempts/[^/]+/capture|runners/[^/]+/logins(?:/.*)?)")
# The app tier's only writes: setup. A bot's model, fallback and context size; a computer's runner restart; and asking
# a computer to read its subscriptions' allowance again. No model sign-in (its code would let support choose the
# account a computer runs on), approvals, messages, tasks, runs, publishing, spending, people, access or credentials.
APP_WRITES = tuple((method, re.compile(pattern)) for method, pattern in (
    ("POST", r"/api/v2/bots/[^/]+/(?:model|task-model|fallback|compact-at)"),
    ("POST", r"/api/v2/runners/[^/]+/restart"),
    ("POST", r"/api/v2/subscriptions/refresh"),
))


class AccessStart(Contract):
    ticket_id: str = Field(min_length=1, max_length=64)
    tier: Literal["app", "full"] = "app"
    hours: int = Field(default=DEFAULT_HOURS, ge=1, le=MAX_HOURS)


def flag_on():
    return os.environ.get("TICO_SUPPORT_ACCESS", "").strip().lower() in ("on", "1", "true", "yes")


def off_reason(settings, c=None):
    """Why sessions cannot run here, or "". With a connection, the owner's team rule counts too."""
    reason = support.off_reason(settings)
    if reason:
        return reason
    if not flag_on():
        return "TICO_SUPPORT_ACCESS"
    if c is not None and not team_rules.load(c)["support_access"]:
        return "team_rule"
    return ""


def require_on(settings, c):
    reason = off_reason(settings, c)
    if reason:
        raise Problem("support_access_off", "Letting Tico support in is not available here.", 409,
                      extra={"off_reason": reason})


def live(c, sid=None):
    """The live session (or that one, when it is still live), or None."""
    where, args = "ended_at IS NULL AND expires_at>?", [H.now()]
    if sid is not None:
        where += " AND id=?"
        args.append(sid)
    return c.execute("SELECT * FROM support_access WHERE " + where + " ORDER BY started_at DESC LIMIT 1",
                     args).fetchone()


def seconds_left(row):
    ends = H.parse_ts(row["expires_at"])
    return max(0, int((ends - datetime.now(timezone.utc)).total_seconds())) if ends else 0


def hq_ticket(c, ticket_id):
    """The ticket's id at HQ (`TK-...`), or "" once it is gone here."""
    row = c.execute("SELECT hq_id FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
    return row["hq_id"] if row else ""


def view(c, row):
    """`id` is HQ's session id (a session from before it was kept as the id has HQ's in `hq_session`); `ticket_id` is
    the ticket here and `hq_ticket` the same ticket at HQ."""
    return {"id": row["id"], "hq_session": row["hq_session"], "ticket_id": row["ticket_id"],
            "hq_ticket": hq_ticket(c, row["ticket_id"]), "tier": row["tier"], "started_by": row["started_by"],
            "started_at": row["started_at"], "expires_at": row["expires_at"], "seconds_left": seconds_left(row),
            "ended_at": row["ended_at"], "end_reason": row["end_reason"]}


def hq_stamp(ts):
    """HQ's time format: to the second."""
    return H.parse_ts(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------- the identity
def identity(c, token, settings):
    """The support identity for a session token: only a live session, with the feature on. Else 401."""
    row = c.execute("SELECT * FROM support_access WHERE token_hash=?", (digest(token),)).fetchone()
    if not row or row["ended_at"] or row["expires_at"] <= H.now() or off_reason(settings, c):
        raise Problem("identity", "Invalid credential", 401)
    return Identity(SUPPORT_ACTOR, "owner" if row["tier"] == "full" else "human", via_token=True,
                    token_label=LABEL, token_id=row["id"], support=row["tier"])


def check_live(c, who):
    """validate_identity for a support identity: its session still live, under a write's lock too."""
    if not flag_on() or not live(c, who.token_id) or not team_rules.load(c)["support_access"]:
        raise Problem("identity", "Invalid credential", 401)


def gate(who, path, method):
    """What a support identity may reach: refused (403) here before any route runs."""
    method = (method or "").upper()
    path = path[:-1] if path.endswith("/") else path
    if not PLAIN_PATH.fullmatch(path) or ALWAYS_REFUSED.fullmatch(path) or PERSONAL.fullmatch(path):
        raise Problem("support_access", "Tico support cannot do this.", 403)
    if who.support == "full":
        return
    if method in ("GET", "HEAD"):
        if APP_READS_REFUSED.fullmatch(path):
            raise Problem("support_access", "Tico support cannot do this.", 403)
        return
    if not any(method == allowed and pattern.fullmatch(path) for allowed, pattern in APP_WRITES):
        raise Problem("support_access", "Tico support (app access) can read and change setup only. Ask for full "
                      "access, or do this yourself.", 403)


# ---------------------------------------------------------------------- the link to HQ
_tokens = {}                 # session id -> its token: in this process's memory only
_runtime = {"app": None, "loop": None, "worker": None}
_worker_lock = threading.Lock()


def _mint(store, sid):
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with store.transaction() as c:
        c.execute("UPDATE support_access SET token_hash=? WHERE id=? AND ended_at IS NULL", (digest(token), sid))
    _tokens[sid] = token
    return token


def end(store, row, by, reason):
    """End a session now, and forget its token. Returns whether this call ended it."""
    _tokens.pop(row["id"], None)
    with store.transaction() as c:
        done = c.execute("UPDATE support_access SET ended_at=?, ended_by=?, end_reason=?, token_hash=NULL "
                         "WHERE id=? AND ended_at IS NULL", (H.now(), by or None, reason, row["id"])).rowcount
        if done:
            H.event(c, by or "system", "support_access.ended" if reason != "stopped" else "support_access.stopped",
                    row["id"], {"reason": reason, "ticket_id": row["ticket_id"], "hq_ticket": hq_ticket(c, row["ticket_id"]),
                                "tier": row["tier"]})
    return bool(done)


def end_all(c, actor, reason):
    """End every live session in this transaction (the owner turned the team rule off). The link worker sees no live
    session on its next round and stops; HQ ends its side at the session's time."""
    rows = c.execute("SELECT id, ticket_id FROM support_access WHERE ended_at IS NULL").fetchall()
    for row in rows:
        _tokens.pop(row["id"], None)
        c.execute("UPDATE support_access SET ended_at=?, ended_by=?, end_reason=?, token_hash=NULL WHERE id=?",
                  (H.now(), actor, reason, row["id"]))
        H.event(c, actor, "support_access.ended", row["id"],
                {"reason": reason, "ticket_id": row["ticket_id"], "hq_ticket": hq_ticket(c, row["ticket_id"])})
    return len(rows)


def _ticket(store, row):
    with store.read() as c:
        return c.execute("SELECT * FROM support_tickets WHERE id=?", (row["ticket_id"],)).fetchone()


def _tell_hq_ended(app, row):
    """Best effort: HQ also ends it on its own clock and on the next pull."""
    ticket = _ticket(app.state.store, row)
    if not ticket or not row["hq_session"]:
        return
    try:
        support._ask_hq("POST", f"/v1/support/{ticket['hq_id']}/access/{row['hq_session']}/end", body={},
                        secret=app.state.support.secret(ticket))
    except Problem:
        pass


def _dispatch(app, token, request):
    """Run one request HQ carried against this app, as the session. Returns (status, content type, body)."""
    method = str(request.get("method") or "").upper()
    path, query = str(request.get("path") or ""), str(request.get("query") or "")
    if (method not in METHODS or not API_PATH.fullmatch(path) or any(p in (".", "..") for p in path.split("/"))
            or len(query) > 4000 or "#" in query):
        return 400, "application/json", b'{"error":{"code":"support_access","detail":"Only /api/v2 requests are carried."}}'
    try:
        body = base64.b64decode(str(request.get("body_b64") or ""), validate=True)
    except ValueError:
        return 400, "application/json", b'{"error":{"code":"support_access","detail":"The body is not base64."}}'
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "tico-support-access"}
    if method not in ("GET", "HEAD"):
        headers["Idempotency-Key"] = "support-" + str(request.get("rid") or secrets.token_hex(8))[:80]
    content_type = str(request.get("content_type") or "")[:200]
    if content_type:
        headers["Content-Type"] = content_type

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await asyncio.wait_for(client.request(method, path + ("?" + query if query else ""),
                                                         content=body or None, headers=headers), DISPATCH_WAIT)
    loop = _runtime["loop"]
    try:
        response = asyncio.run_coroutine_threadsafe(go(), loop).result(DISPATCH_WAIT + 5)
    except Exception as exc:
        log.warning("A support request did not finish: %s", type(exc).__name__)
        return 504, "application/json", b'{"error":{"code":"timeout","detail":"The request did not finish in time."}}'
    content = response.content
    if len(content) > MAX_BODY:
        return 413, "application/json", b'{"error":{"code":"too_large","detail":"The answer is over 2 MB."}}'
    return response.status_code, response.headers.get("content-type", "")[:200], content


def pull_once(app, wait=PULL_WAIT):
    """One round with HQ: take what it queued, run each and send its answer back. Returns whether the session is still
    live (False: the worker stops)."""
    store, settings = app.state.store, app.state.store.settings
    with store.read() as c:
        row = c.execute("SELECT * FROM support_access WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
        reason = off_reason(settings, c) if row else ""
    if not row:
        return False
    if row["expires_at"] <= H.now() or reason:
        # Support itself off (TICO_SUPPORT, demo, rehearsal) sends nothing at all; HQ ends its side at the session's time.
        if end(store, row, "", "expired" if not reason else "turned_off") and not support.off_reason(settings):
            _tell_hq_ended(app, row)
        return False
    ticket = _ticket(store, row)
    if not ticket or not row["hq_session"]:
        end(store, row, "", "ticket_gone")
        return False
    token = _tokens.get(row["id"]) or _mint(store, row["id"])
    base = f"/v1/support/{ticket['hq_id']}/access/{row['hq_session']}"
    secret = app.state.support.secret(ticket)
    answer = support._ask_hq("POST", base + "/pull", body={}, secret=secret, wait=wait + 10)
    if answer.status_code in (404, 409, 410):
        end(store, row, "", "ended_by_hq")
        return False
    data = support._answer(answer, (200,))
    if data.get("ended") is True:
        end(store, row, "", "ended_by_hq")
        return False
    # Each answer goes back as soon as its request finishes: one slow request then holds up no answer already made,
    # and no reply body is larger than one capped response.
    for request in (data.get("requests") if isinstance(data.get("requests"), list) else [])[:MAX_BATCH]:
        if not isinstance(request, dict):
            continue
        with store.read() as c:
            still = live(c, row["id"])
        by = "support-agent" if request.get("by") == "support-agent" else "staff"
        if still:
            status, content_type, content = _dispatch(app, token, request)
        else:
            status, content_type, content = 410, "application/json", b'{"error":{"code":"ended","detail":"The session ended."}}'
        with store.transaction() as c:
            c.execute("INSERT INTO support_access_log(session_id,created_at,by,method,path,status) VALUES(?,?,?,?,?,?)",
                      (row["id"], H.now(), by, str(request.get("method") or "")[:10].upper(),
                       str(request.get("path") or "")[:500], status))
        reply = {"rid": str(request.get("rid") or "")[:80], "status": status, "content_type": content_type,
                 "body_b64": base64.b64encode(content).decode()}
        support._answer(support._ask_hq("POST", base + "/reply", body={"replies": [reply]}, secret=secret), (200,))
    with store.read() as c:
        return bool(live(c, row["id"]))


def _work(app):
    delay = 1
    while True:
        try:
            if not pull_once(app):
                return
            delay = 1
        except Exception as exc:          # HQ down or refusing: wait longer each time, up to five minutes
            log.warning("Support access link to HQ failed: %s", type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, 300)


def wake(app):
    """Start the link worker unless it is running."""
    if not WORKER or _runtime["loop"] is None:
        return
    with _worker_lock:
        running = _runtime["worker"]
        if running and running.is_alive():
            return
        thread = threading.Thread(target=_work, args=(app,), name="tico-support-access", daemon=True)
        _runtime["worker"] = thread
        thread.start()


def started(app, loop):
    """On server start: remember the app and its loop for the worker, and pick a live session back up with a fresh
    token (the old one was in the last process's memory)."""
    _runtime["app"], _runtime["loop"] = app, loop
    store = app.state.store
    with store.read() as c:
        row = c.execute("SELECT * FROM support_access WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
        reason = off_reason(store.settings, c) if row else ""
    if not row:
        return
    if row["expires_at"] <= H.now() or reason:
        # A session never outlives the switch: turning the feature back on later does not revive it.
        end(store, row, "", "expired" if not reason else "turned_off")
        return
    _mint(store, row["id"])
    wake(app)


# ---------------------------------------------------------------------- the routes
class Access:
    def __init__(self, app, store, settings, auth):
        self.app, self.store, self.settings, self.auth = app, store, settings, auth
        self.starting = threading.Lock()     # one live session per install, even when two people start at once

    def may_manage(self, who):
        return self.auth.company_role(who) in ("owner", "admin")

    def status(self, who):
        if who.role not in ("owner", "human"):
            raise Problem("forbidden", "Only a person can see this.", 403)
        with self.store.read() as c:
            reason = off_reason(self.settings, c)
            row = None if reason else live(c)
            last = mine = None
            if not reason and self.may_manage(who):
                last = c.execute("SELECT * FROM support_access ORDER BY started_at DESC LIMIT 1").fetchone()
            # The sessions started from this person's own requests: each request lists its own sessions' logs.
            if not reason:
                mine = c.execute("SELECT a.* FROM support_access a JOIN support_tickets t ON t.id=a.ticket_id "
                                 "WHERE t.actor=? ORDER BY a.started_at DESC LIMIT ?", (who.actor, LISTED)).fetchall()
            person = not who.via_token and not who.via
            return {"available": not reason, "off_reason": reason, "active": view(c, row) if row else None,
                    "last": view(c, last) if last else None, "sessions": [view(c, x) for x in mine or ()],
                    "can_start": bool(not reason and person and self.may_manage(who)),
                    "can_full": bool(not reason and person and who.role == "owner")}

    def start(self, who, body):
        support.require_person(who)
        role = self.auth.company_role(who)
        if role not in ("owner", "admin"):
            raise Problem("forbidden", "Only the owner or an admin can let Tico support in.", 403)
        if body.tier == "full" and role != "owner":
            raise Problem("forbidden", "Only the owner can give Tico support full access.", 403)
        with self.starting:
            with self.store.read() as c:
                require_on(self.settings, c)
                ticket = c.execute("SELECT * FROM support_tickets WHERE id=? AND actor=?",
                                   (body.ticket_id, who.actor)).fetchone()
                if not ticket:
                    raise Problem("not_found", "Ticket not found", 404)
                if ticket["status"] not in ("open", "answered"):
                    raise Problem("support_closed", "This request is closed. Start a new one first.", 409)
                if live(c):
                    raise Problem("support_access_live", "Tico support already has access. Stop it first.", 409)
            now = H.now()
            expires = H.shift(now, hours=body.hours)
            answer = support._ask_hq("POST", f"/v1/support/{ticket['hq_id']}/access",
                                     body={"tier": body.tier, "expires_at": hq_stamp(expires)},
                                     secret=self.app.state.support.secret(ticket))
            session = str(support._answer(answer, (200, 201)).get("session") or "")
            if not HQ_SESSION.fullmatch(session):
                raise Problem("support_unreachable", "Could not reach Tico support. Try again in a moment.", 502,
                              retryable=True)
            # HQ's session id is the id here too, so this install's log and events match HQ and admin.
            sid = session
            token = TOKEN_PREFIX + secrets.token_urlsafe(32)
            with self.store.transaction() as c:
                if c.execute("SELECT 1 FROM support_access WHERE id=?", (sid,)).fetchone():
                    raise Problem("support_unreachable", "Could not reach Tico support. Try again in a moment.", 502,
                                  retryable=True)
                c.execute("INSERT INTO support_access(id,ticket_id,tier,started_by,started_at,expires_at,token_hash,"
                          "hq_session) VALUES(?,?,?,?,?,?,?,?)",
                          (sid, ticket["id"], body.tier, who.actor, now, expires, digest(token), session))
                H.event(c, who.actor, "support_access.started", sid,
                        {"ticket_id": ticket["id"], "hq_ticket": ticket["hq_id"], "tier": body.tier, "hours": body.hours})
                made = view(c, c.execute("SELECT * FROM support_access WHERE id=?", (sid,)).fetchone())
            _tokens[sid] = token
        wake(self.app)
        return made

    def _session(self, who, sid):
        support.require_person(who)
        with self.store.read() as c:
            row = c.execute("SELECT * FROM support_access WHERE id=?", (sid,)).fetchone()
        if not row:
            raise Problem("not_found", "Session not found", 404)
        if row["started_by"] != who.actor and not self.may_manage(who):
            raise Problem("forbidden", "Only whoever started it, the owner or an admin can see or stop this.", 403)
        return row

    def stop(self, who, sid):
        with self.store.read() as c:
            require_on(self.settings, c)
        row = self._session(who, sid)
        if end(self.store, row, who.actor, "stopped"):
            _tell_hq_ended(self.app, row)
        with self.store.read() as c:
            return view(c, c.execute("SELECT * FROM support_access WHERE id=?", (sid,)).fetchone())

    def log(self, who, sid):
        with self.store.read() as c:
            require_on(self.settings, c)
        row = self._session(who, sid)
        with self.store.read() as c:
            rows = c.execute("SELECT created_at,by,method,path,status FROM support_access_log WHERE session_id=? "
                             "ORDER BY id DESC LIMIT ?", (sid, LOGGED)).fetchall()
            return {"session": view(c, row), "log": [dict(r) for r in rows]}


def install(app, store, settings, auth):
    from fastapi import Request

    access = Access(app, store, settings, auth)

    @app.get("/api/v2/support/access")
    def support_access_status(request: Request):
        """Whether Tico support has access now (the banner every person sees), the button under a request, and the
        sessions started from this person's requests (each request's log)."""
        return access.status(request.state.identity)

    @app.post("/api/v2/support/access")
    def support_access_start(request: Request, body: AccessStart):
        return access.start(request.state.identity, body)

    @app.post("/api/v2/support/access/{sid}/stop")
    def support_access_stop(request: Request, sid: str):
        return access.stop(request.state.identity, sid)

    @app.get("/api/v2/support/access/{sid}/log")
    def support_access_log(request: Request, sid: str):
        return access.log(request.state.identity, sid)

    return access
