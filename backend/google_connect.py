"""Settings > Inbox > Connect Google: a person connects their own Google account, read only, with no admin.

The person asks to connect (`POST /api/v2/mail/google/connect`) and gets Google's sign-in address for Tico's
own OAuth client (TICO_GOOGLE_OAUTH_CLIENT_ID/SECRET, run by whoever runs this server; nobody makes a Google
Cloud project, a service account or domain-wide delegation). Google sends their browser back to
`/api/v2/mail/google/callback`, which checks the state belongs to the same person and keeps the one-time code.
The computer that syncs mail (runner/connectors.py) claims it, asks this server to trade it for the grant, confirms
the account that signed in is the mailbox asked for and keeps the grant (connectors/mail/oauth.py `finish`).

The OAuth client secret never leaves this server. It is one secret for every team on Tico's public app, so no
computer holds it: the code exchange (`/exchange`) and every later refresh (`/token`) go through here. The server
adds the secret, calls Google and answers the computer that holds the grant; it stores no token, and the refresh
token it relays on the exchange and receives on a refresh is never written down or logged. It keeps only the
grant's SHA-256 fingerprint (`grant_hash`), which can refresh nothing: a refresh must match a mailbox this
computer holds and the grant this server issued for it, so the secret is never added to a token from anywhere
else. A refreshed token carries only the read-only scopes of one service (SERVICE_SCOPES).

Only the person connects or disconnects their own mailbox: the mailbox their message bot reads, else their email.
Not a bot, not BotOps or the Assistant acting for them, not an admin for someone else. Disconnect is queued the
same way; the computer revokes the grant at Google and forgets it.

The scopes are read only (READ_SCOPES): there is no switch here to widen them. A Workspace whose admin blocks
Tico is named as that ("admin_policy"), never answered with a broader request or a service account.
"""

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import Request
from fastapi.responses import RedirectResponse
from pydantic import Field
from typing import Literal

from . import models as M
from . import routines
from .store import H, P, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS google_connects(
 person_id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, status TEXT NOT NULL, state TEXT, verifier TEXT, code TEXT,
 kind TEXT, message TEXT, hint TEXT, can TEXT, runner_id TEXT, holder TEXT, started_at REAL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS google_connects_state ON google_connects(state) WHERE state IS NOT NULL;
CREATE TABLE IF NOT EXISTS google_mail_holders(runner_id TEXT PRIMARY KEY, holds INTEGER NOT NULL, seen REAL NOT NULL);
"""
# The SHA-256 of a grant's refresh token: the one being connected (`pending_hash`, from the exchange) and the one
# connected (`grant_hash`, once the computer says connected). A fingerprint, never the token.
COLUMNS = {"pending_hash": "TEXT", "grant_hash": "TEXT"}


def ensure_schema(c):
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(google_connects)")}
    for name, declaration in COLUMNS.items():
        if name not in have:
            c.execute(f"ALTER TABLE google_connects ADD COLUMN {name} {declaration}")


def fingerprint(refresh_token):
    return hashlib.sha256(str(refresh_token).encode()).hexdigest()

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
G = "https://www.googleapis.com/auth/"
# The same list as connectors/mail/oauth.py IDENTITY_SCOPES + READ_SCOPES (backend/tests/test_google_connect.py).
READ_SCOPES = ["openid", "email", G + "gmail.readonly", G + "calendar.events.readonly", G + "calendar.freebusy",
               G + "calendar.calendarlist.readonly"]
# What a refreshed token may carry: one service's read-only scopes, never both and never a write.
SERVICE_SCOPES = {"gmail": {G + "gmail.readonly"},
                  "calendar": {G + "calendar.events.readonly", G + "calendar.freebusy",
                               G + "calendar.calendarlist.readonly"}}
GOOGLE_SECONDS = 20
CALLBACK = "/api/v2/mail/google/callback"
INBOX = "/#/settings/inbox"    # Settings > Inbox (ui/app/router.js), which reads the outcome itself
SIGN_IN_SECONDS = 600           # Google's codes last about ten minutes; a sign-in left open longer starts again
CLAIM_SECONDS = 300             # a computer that claimed and never answered
HOLDER_SECONDS = 600            # a computer that said it holds mail credentials, this recently
STALLED_SECONDS = 900           # a disconnect its computer has not taken this long: that computer is offline
QUEUED = ("ready", "disconnecting")
# What Google may put in the callback's `error`, as the kind the person is shown.
CALLBACK_ERRORS = {"admin_policy_enforced": "admin_policy", "org_internal": "app_unavailable",
                   "access_denied": "declined", "invalid_scope": "app_config", "invalid_client": "app_config",
                   "unauthorized_client": "app_config", "redirect_uri_mismatch": "app_config"}
# What Google's token endpoint may answer, as the kind the computer reports (connectors/mail/oauth.py REASONS).
TOKEN_ERRORS = {"admin_policy_enforced": "admin_policy", "org_internal": "app_unavailable", "invalid_grant": "signin",
                "invalid_client": "app_config", "unauthorized_client": "app_config", "invalid_scope": "app_config",
                "redirect_uri_mismatch": "app_config"}
KINDS = ("admin_policy", "app_unavailable", "declined", "app_config", "expired", "identity_mismatch",
         "no_refresh_token", "no_gmail", "code_rejected", "network", "signin", "unknown", "not_here", "error")


def policy_message(mailbox):
    domain = mailbox.rpartition("@")[2]
    return (f"The Google Workspace for {domain} has a policy that blocks Tico, so Google refused the sign-in. "
            "This is the Workspace administrator's setting, not something Tico or you can change, and Tico did not "
            "ask for more access to get around it.",
            f"Ask the administrator for {domain} to allow Tico in the Admin console (Security > API controls > "
            "App access control), or to trust it for your account. Then connect again.")


MESSAGES = {
    "app_unavailable": ("Google says Tico's app is not open to accounts outside its own organization yet.",
                        "This is Tico's Google app setting, not yours: tell Tico's support."),
    "declined": ("You didn't approve the access on Google's page, so nothing was connected.",
                 "Connect again and choose Allow. Tico asks only to read your mail and calendar."),
    "app_config": ("Tico's Google app is not set up correctly for this server, so Google refused the sign-in.",
                   "This is Tico's Google app configuration, not yours: tell Tico's support."),
    "expired": ("The sign-in took too long and expired before it finished.", "Connect again."),
    "not_here": ("The computer that answered doesn't hold your Google access, so nothing was disconnected.",
                 "Try again in a few minutes. You can also remove Tico at myaccount.google.com/permissions."),
    "unknown": ("The computer that syncs your mail did not say whether the connection finished.",
                "Connect again. If it keeps happening, check that computer is online."),
}


class ConnectorResult(M.Contract):
    status: Literal["connected", "failed", "disconnected"]
    kind: str = Field(default="", max_length=40)
    message: str = Field(default="", max_length=500)
    hint: str = Field(default="", max_length=500)
    can: list[str] = Field(default_factory=list, max_length=20)


class ClaimRequest(M.Contract):
    holds_mail: bool = False        # this computer has a Google key or connected accounts (runner/connectors.py)


class TokenRequest(M.Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    service: Literal["gmail", "calendar"]
    refresh_token: str = Field(min_length=1, max_length=512)
    scopes: list[str] = Field(min_length=1, max_length=4)


def configured(settings):
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def redirect_uri(settings):
    return settings.public_url.rstrip("/") + CALLBACK


def post_token(fields):
    """Google's token endpoint answer for `fields`. Raises urllib.error.HTTPError on a refusal, OSError when
    Google can't be reached."""
    request = urllib.request.Request(TOKEN_URI, data=urllib.parse.urlencode(fields).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=GOOGLE_SECONDS) as answer:
        return json.loads(answer.read().decode() or "{}")


def google_token(settings, fields):
    """Google's answer to a code exchange or a refresh with this server's client added. A refusal is a Problem
    naming only Google's reason as a kind (TOKEN_ERRORS); Google's body and the request never reach a log."""
    fields = dict(fields, client_id=settings.google_oauth_client_id,
                  client_secret=settings.google_oauth_client_secret)
    try:
        answer = post_token(fields)
    except urllib.error.HTTPError as e:
        try:
            error = str(json.loads(e.read().decode() or "{}").get("error") or "")
        except (OSError, ValueError, AttributeError):
            error = ""
        # Google's own outage is a network failure to the person, not a refused grant.
        kind = "network" if e.code >= 500 else TOKEN_ERRORS.get(error.strip().lower(), "error")
        raise Problem("google_refused", f"Google refused it ({error or e.code})", 409, extra={"kind": kind})
    except (OSError, ValueError) as e:
        raise Problem("google_refused", f"Tico's server could not reach Google ({type(e).__name__})", 409,
                      extra={"kind": "network"})
    if not isinstance(answer, dict) or not answer.get("access_token"):
        raise Problem("google_refused", "Google returned no access token", 409, extra={"kind": "error"})
    return answer


def mailbox_for(c, person_id):
    """The mailbox that is this person's own: their message bot's, else their email."""
    from .views import roster
    people = roster(c)
    person = P.person(person_id, people)
    if not person:
        raise Problem("not_found", "You are not on this team's roster", 404)
    return routines.mailbox_of(person, routines.message_bot_mailboxes(c, people)), person


def require_self(who):
    """The person acting as themselves: never a bot, a computer, or BotOps or the Assistant acting for them."""
    if who.role not in ("owner", "human") or who.via or who.task_actor:
        raise Problem("forbidden", "Only the person whose mailbox it is connects or disconnects their Google "
                                   "account, signed in to Tico themselves", 403)
    return H.actor_id(who.actor)


def expiring(c):
    """Whether `expire` has anything to end: the status page reads without the write lock when it has not."""
    now = time.time()
    return c.execute("SELECT 1 FROM google_connects WHERE (status IN ('signing_in','ready') AND started_at<?) "
                     "OR (status IN ('finishing','revoking') AND started_at<?) LIMIT 1",
                     (now - SIGN_IN_SECONDS, now - CLAIM_SECONDS)).fetchone()


def expire(c):
    """Sign-ins nobody finished, and claims a computer never answered, end as failures the person can read."""
    now = time.time()
    c.execute("UPDATE google_connects SET status='failed',kind='expired',state=NULL,verifier=NULL,code=NULL,"
              "updated_at=? WHERE status IN ('signing_in','ready') AND started_at<?", (H.now(), now - SIGN_IN_SECONDS))
    c.execute("UPDATE google_connects SET status='failed',kind='unknown',state=NULL,verifier=NULL,code=NULL,"
              "updated_at=? WHERE status IN ('finishing','revoking') AND started_at<?", (H.now(), now - CLAIM_SECONDS))


def view(row, settings, mailbox, syncing):
    status = row["status"] if row else "none"
    out = {"configured": configured(settings), "mailbox": mailbox, "syncing": syncing, "status": status,
           "scopes": READ_SCOPES, "can": [], "updated_at": row["updated_at"] if row else None,
           "kind": None, "message": None, "hint": None}
    if row and status == "connected":
        out["can"] = json.loads(row["can"] or "[]")
    if row and status == "failed":
        kind = row["kind"] or "error"
        message, hint = (policy_message(mailbox) if kind == "admin_policy" else MESSAGES.get(kind)
                         or (row["message"] or "Connecting failed.", row["hint"] or "Connect again."))
        out.update(kind=kind, message=message, hint=hint)
    if row and status == "disconnecting" and (row["started_at"] or 0) < time.time() - STALLED_SECONDS:
        # It stays queued, so the computer still revokes when it is back; the person can revoke at Google now.
        out.update(stalled=True, message="The computer that holds your Google access hasn't come online, so Tico "
                                         "hasn't revoked it yet.",
                   hint="Remove Tico now at myaccount.google.com/permissions. The computer finishes the disconnect "
                        "when it is back online.")
    return out


def claimable(c, runner_id, holds):
    """The next connect or disconnect this computer may take, or None.

    A person's row belongs to the computer that holds their grant (`holder`): a reconnect or a disconnect goes only
    there, so a revoke never runs where there is nothing to revoke while the grant stays live elsewhere. A row with no
    live holder (a first connect) goes to a computer that already holds mail credentials, so mail stays on one
    computer; any of the publishing computers takes it only when none has said so recently."""
    expire(c)
    elsewhere = c.execute(
        "SELECT 1 FROM google_mail_holders h JOIN runners r ON r.id=h.runner_id AND r.revoked_at IS NULL "
        "WHERE h.runner_id<>? AND h.holds=1 AND h.seen>? UNION ALL SELECT 1 FROM google_connects g "
        "JOIN runners r ON r.id=g.holder AND r.revoked_at IS NULL WHERE g.holder<>? LIMIT 1",
        (runner_id, time.time() - HOLDER_SECONDS, runner_id)).fetchone()
    return c.execute(
        "SELECT g.* FROM google_connects g LEFT JOIN runners r ON r.id=g.holder AND r.revoked_at IS NULL "
        "WHERE g.status IN ('ready','disconnecting') AND (g.holder=? OR (r.id IS NULL AND ?)) "
        "ORDER BY g.updated_at LIMIT 1", (runner_id, 1 if holds or not elsewhere else 0)).fetchone()


def pending(c, runner_id):
    """Whether a connect or disconnect waits for this computer, which has no mail credentials yet (otherwise its
    connectors job runs anyway): the job then runs for it (backend/connectors.py)."""
    return claimable(c, runner_id, False) is not None


def install(app, store, publisher, mutate):
    settings = store.settings

    def person_view(c, who):
        pid = require_self(who)
        mailbox, _ = mailbox_for(c, pid)
        syncing = any(b["address"] == mailbox for b in routines.message_bot_mailboxes(c))
        row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (pid,)).fetchone()
        if row and row["mailbox"] != mailbox:
            row = None                  # their mailbox changed since: the old connection says nothing about this one
        return view(row, settings, mailbox, syncing)

    @app.get("/api/v2/mail/google")
    def google_status(request: Request):
        """Your own Google connection: the mailbox it is for, its state, and why it failed."""
        with store.read() as c:
            if not expiring(c):
                return person_view(c, request.state.identity)
        with store.transaction() as c:
            expire(c)
            return person_view(c, request.state.identity)

    @app.post("/api/v2/mail/google/connect")
    def google_connect(request: Request, body: M.Empty):
        def work(c):
            who = request.state.identity
            pid = require_self(who)
            if not configured(settings):
                raise Problem("google_app", "This Tico server has no Google app set up, so Connect Google is not "
                                            "available yet. Whoever runs the server sets TICO_GOOGLE_OAUTH_CLIENT_ID "
                                            "and TICO_GOOGLE_OAUTH_CLIENT_SECRET (docs/mail.md).", 409)
            mailbox, _ = mailbox_for(c, pid)
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            query = {"client_id": settings.google_oauth_client_id, "redirect_uri": redirect_uri(settings),
                     "response_type": "code", "scope": " ".join(READ_SCOPES), "access_type": "offline",
                     "prompt": "consent", "login_hint": mailbox, "state": state, "code_challenge": challenge,
                     "code_challenge_method": "S256"}
            now = H.now()
            c.execute("INSERT INTO google_connects(person_id,mailbox,status,state,verifier,code,kind,message,hint,"
                      "runner_id,started_at,created_at,updated_at) VALUES(?,?,'signing_in',?,?,NULL,NULL,NULL,NULL,NULL,?,?,?) "
                      "ON CONFLICT(person_id) DO UPDATE SET mailbox=excluded.mailbox,status='signing_in',"
                      "state=excluded.state,verifier=excluded.verifier,code=NULL,kind=NULL,message=NULL,hint=NULL,"
                      "can=NULL,"
                      "runner_id=NULL,started_at=excluded.started_at,updated_at=excluded.updated_at",
                      (pid, mailbox, state, verifier, time.time(), now, now))
            H.event(c, who.actor, "mail.google.connect.start", mailbox, {})
            return {"auth_url": AUTH_URI + "?" + urllib.parse.urlencode(query), "mailbox": mailbox}
        return mutate(request, body, work)

    @app.get(CALLBACK, include_in_schema=False)
    def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        """Google sends the browser back here. The state must be one this same person started, still open."""
        who = request.state.identity
        with store.transaction() as c:
            expire(c)
            row = c.execute("SELECT * FROM google_connects WHERE state=? AND status='signing_in'",
                            (state,)).fetchone() if state else None
            if (not row or who.role not in ("owner", "human") or row["person_id"] != H.actor_id(who.actor)
                    or not secrets.compare_digest(row["state"], state)):
                return RedirectResponse(INBOX, status_code=302)
            now = H.now()
            if error or not code:
                kind = CALLBACK_ERRORS.get(error, "declined" if error else "error")
                c.execute("UPDATE google_connects SET status='failed',kind=?,state=NULL,verifier=NULL,code=NULL,"
                          "updated_at=? WHERE person_id=?", (kind, now, row["person_id"]))
                H.event(c, who.actor, "mail.google.connect.failed", row["mailbox"], {"kind": kind})
                return RedirectResponse(INBOX, status_code=302)
            # The state is spent; the code waits for the computer that keeps the grant.
            c.execute("UPDATE google_connects SET status='ready',state=NULL,code=?,updated_at=? WHERE person_id=?",
                      (code[:2000], now, row["person_id"]))
        return RedirectResponse(INBOX, status_code=302)

    @app.post("/api/v2/mail/google/disconnect")
    def google_disconnect(request: Request, body: M.Empty):
        def work(c):
            who = request.state.identity
            pid = require_self(who)
            mailbox, _ = mailbox_for(c, pid)
            now = H.now()
            c.execute("INSERT INTO google_connects(person_id,mailbox,status,started_at,created_at,updated_at) "
                      "VALUES(?,?,'disconnecting',?,?,?) ON CONFLICT(person_id) DO UPDATE SET "
                      "mailbox=excluded.mailbox,status='disconnecting',state=NULL,verifier=NULL,code=NULL,kind=NULL,"
                      "message=NULL,hint=NULL,can=NULL,runner_id=NULL,started_at=excluded.started_at,updated_at=excluded.updated_at",
                      (pid, mailbox, time.time(), now, now))
            H.event(c, who.actor, "mail.google.disconnect", mailbox, {})
            return person_view(c, who)
        return mutate(request, body, work)

    @app.post("/api/v2/connectors/mail/google/claim")
    def google_claim(request: Request, body: ClaimRequest | None = None):
        """The next connect or disconnect for the computer that syncs mail. A connect carries no code and no
        secret: the computer asks `/exchange` for the grant, and the code is spent there."""
        with store.transaction() as c:
            runner = publisher(c, request.state.identity)
            holds = bool(body and body.holds_mail)
            c.execute("INSERT INTO google_mail_holders(runner_id,holds,seen) VALUES(?,?,?) ON CONFLICT(runner_id) "
                      "DO UPDATE SET holds=excluded.holds,seen=excluded.seen", (runner["id"], int(holds), time.time()))
            # Only recent answers count (claimable), so older ones and revoked computers' go.
            c.execute("DELETE FROM google_mail_holders WHERE seen<? OR runner_id IN "
                      "(SELECT id FROM runners WHERE revoked_at IS NOT NULL)", (time.time() - HOLDER_SECONDS,))
            row = claimable(c, runner["id"], holds)
            if not row:
                return {"connect": None}
            action = "connect" if row["status"] == "ready" else "disconnect"
            if action == "connect" and not configured(settings):
                c.execute("UPDATE google_connects SET status='failed',kind='app_config',code=NULL,verifier=NULL,"
                          "updated_at=? WHERE person_id=?", (H.now(), row["person_id"]))
                return {"connect": None}
            c.execute("UPDATE google_connects SET status=?,runner_id=?,started_at=?,updated_at=? WHERE person_id=?",
                      ("finishing" if action == "connect" else "revoking", runner["id"], time.time(), H.now(),
                       row["person_id"]))
            out = {"id": row["person_id"], "action": action, "mailbox": row["mailbox"]}
            if action == "connect":
                out.update(client_id=settings.google_oauth_client_id, broker="server")
            return {"connect": out}

    @app.post("/api/v2/connectors/mail/google/{person_id}/exchange")
    def google_exchange(person_id: str, request: Request, body: M.Empty | None = None):
        """Trade a claimed connect's code for its grant, for the computer that claimed it, once. The code and the
        verifier are cleared before Google is asked; the answer goes to that computer and is not kept here."""
        with store.transaction() as c:
            runner = publisher(c, request.state.identity)
            row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (person_id,)).fetchone()
            if (not row or row["runner_id"] != runner["id"] or row["status"] != "finishing" or not row["code"]
                    or not row["verifier"]):
                raise Problem("state", "No sign-in code waits for this computer to exchange", 409)
            code, verifier = row["code"], row["verifier"]
            c.execute("UPDATE google_connects SET code=NULL,verifier=NULL,updated_at=? WHERE person_id=?",
                      (H.now(), person_id))
        if not configured(settings):
            raise Problem("google_refused", "This Tico server has no Google app set up", 409,
                          extra={"kind": "app_config"})
        answer = google_token(settings, {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                         "redirect_uri": redirect_uri(settings)})
        if answer.get("refresh_token"):
            with store.transaction() as c:
                c.execute("UPDATE google_connects SET pending_hash=? WHERE person_id=? AND runner_id=? AND "
                          "status='finishing'", (fingerprint(answer["refresh_token"]), person_id, runner["id"]))
        return {k: answer[k] for k in ("access_token", "refresh_token", "expires_in", "scope") if k in answer}

    @app.post("/api/v2/connectors/mail/google/token")
    def google_refresh(request: Request, body: TokenRequest):
        """A short-lived access token for a grant this computer holds: one service, read only. The grant must be the
        one this server issued for that mailbox and this computer (its fingerprint); nothing about the request is
        stored."""
        wanted = set(body.scopes)
        if not wanted <= SERVICE_SCOPES[body.service]:
            raise Problem("scope", f"A {body.service} token from Connect Google carries only its read-only scopes", 422)
        with store.transaction() as c:
            runner = publisher(c, request.state.identity)
            held = c.execute("SELECT 1 FROM google_connects WHERE lower(mailbox)=? AND holder=? AND grant_hash=? "
                             "LIMIT 1", (body.mailbox.strip().lower(), runner["id"],
                                         fingerprint(body.refresh_token))).fetchone()
            if not held:
                # Also a grant whose connected result never reached the server: the computer reconnects.
                raise Problem("not_this_grant", "This computer holds no Connect Google grant from this server for that "
                                                "mailbox", 403)
        if not configured(settings):
            raise Problem("google_refused", "This Tico server has no Google app set up", 409,
                          extra={"kind": "app_config"})
        answer = google_token(settings, {"grant_type": "refresh_token", "refresh_token": body.refresh_token,
                                         "scope": " ".join(sorted(wanted))})
        returned = set(str(answer.get("scope") or "").split())
        if returned - wanted:
            raise Problem("google_refused", "Google answered with more access than was asked for", 409,
                          extra={"kind": "error"})
        granted = sorted(returned) or sorted(wanted)
        expires = int(answer.get("expires_in") or 3600)
        return {"access_token": answer["access_token"], "expires_in": expires,
                "expiry": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + expires)), "scopes": granted}

    @app.post("/api/v2/connectors/mail/google/{person_id}/result")
    def google_result(person_id: str, request: Request, body: ConnectorResult):
        with store.transaction() as c:
            runner = publisher(c, request.state.identity)
            row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (person_id,)).fetchone()
            if not row:
                raise Problem("not_found", "No Google connect is waiting for this person", 404)
            if row["runner_id"] != runner["id"] or row["status"] not in ("finishing", "revoking"):
                raise Problem("state", "This Google connect is not this computer's to finish", 409)
            if (row["status"] == "finishing") != (body.status != "disconnected"):
                raise Problem("state", "That result does not answer this request", 409)
            kind = body.kind if body.kind in KINDS else ("error" if body.status == "failed" else None)
            failed = body.status == "failed"
            # The grant is where it was connected, gone once revoked, and unchanged by a failure (a failed reconnect
            # stores nothing, so an earlier grant stays where it was).
            holder = {"connected": runner["id"], "disconnected": None}.get(body.status, row["holder"])
            # Likewise the grant the server will refresh: the new one once connected, none once revoked.
            grant_hash = {"connected": row["pending_hash"], "disconnected": None}.get(body.status, row["grant_hash"])
            c.execute("UPDATE google_connects SET status=?,kind=?,message=?,hint=?,can=?,holder=?,grant_hash=?,"
                      "pending_hash=NULL,state=NULL,verifier=NULL,code=NULL,updated_at=? WHERE person_id=?",
                      (body.status, kind if failed else None, body.message if failed else None,
                       body.hint if failed else None,
                       json.dumps(sorted(set(body.can))) if body.status == "connected" else None, holder, grant_hash,
                       H.now(), person_id))
            H.event(c, "human:" + person_id, "mail.google." + body.status, row["mailbox"],
                    {"kind": kind} if body.status == "failed" else {})
            if body.status == "connected" and row["mailbox"]:
                # Its tasks' reconnect cards end now, not at the connector's next report (backend/reconnects.py).
                from . import reconnects
                reconnects.google_connected(c, row["mailbox"])
            return {"ok": True}
