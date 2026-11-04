"""Settings > Inbox > Connect Google: a person connects their own Google account, read only, with no admin.

The person asks to connect (`POST /api/v2/mail/google/connect`) and gets Google's sign-in address for Tico's
own OAuth client (TICO_GOOGLE_OAUTH_CLIENT_ID/SECRET, run by whoever runs this server; nobody makes a Google
Cloud project, a service account or domain-wide delegation). Google sends their browser back to
`/api/v2/mail/google/callback`, which checks the state belongs to the same person and keeps the one-time code.
The computer hosting the person's message bot claims it (a disconnect goes to the computer holding the grant), asks this
server to trade it for the grant, confirms the account that signed in is the mailbox asked for and keeps the grant
(connectors/mail/oauth.py `finish`). With no bot computer, a publisher takes a first connect, as before. A reconnect that
moves the grant does not revoke the old one at Google: that would end the new grant too (one client, one person). The
old holder's refresh token is inert, since only this server can add the secret and it refreshes only the current grant.

The OAuth client secret never leaves this server. It is one secret for every team on Tico's public app, so no
computer holds it: the code exchange (`/exchange`) and every later refresh (`/token`) go through here. The server
adds the secret, calls Google and answers the computer that holds the grant; it stores no token, and the refresh
token it relays on the exchange and receives on a refresh is never written down or logged. It keeps only the
grant's SHA-256 fingerprint (`grant_hash`), which can refresh nothing: a refresh must match a mailbox this
computer holds and the grant this server issued for it, so the secret is never added to a token from anywhere
else. A refreshed token carries only the scopes of one service that its grant was given (`grant_scopes`,
refresh_scopes).

Only the person connects or disconnects their own mailbox: the mailbox their message bot reads, else their email.
Not a bot, not BotOps or the Assistant acting for them, not an admin for someone else. Disconnect is queued the
same way; the computer revokes the grant at Google and forgets it.

The scopes depend on the Google app that signs the person in. Tico's shared app (the server's environment) asks for
read only (READ_SCOPES). The company's own app, set in Settings > Google, also asks for the act scopes (ACT_SCOPES:
gmail.modify, which labels, archives and drafts mail, and calendar.events), since an Internal app needs no Google review
for them. The grant keeps only the scopes Google gave the exchange (`grant_scopes`, never more than the app may grant),
and a refresh asks Google for no scope the grant lacks: a read-only grant stays read only until its person connects
again. A Workspace whose admin blocks Tico is named as that ("admin_policy"), never answered with a broader request or
a service account.
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

from . import google_rights
from . import models as M
from . import routines
from .google_workspace import acts as acts_allowed, oauth_client, permissions
from .store import H, P, Problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS google_connects(
 person_id TEXT PRIMARY KEY, mailbox TEXT NOT NULL, status TEXT NOT NULL, oauth_state TEXT, verifier TEXT, code TEXT,
 kind TEXT, message TEXT, hint TEXT, can TEXT, runner_id TEXT, holder TEXT, client_id TEXT, started_at REAL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS google_connects_oauth_state ON google_connects(oauth_state) WHERE oauth_state IS NOT NULL;
CREATE TABLE IF NOT EXISTS google_mail_holders(runner_id TEXT PRIMARY KEY, holds INTEGER NOT NULL, seen_at REAL NOT NULL);
"""
# A grant's refresh token is kept only as its SHA-256 fingerprint, never the token: the one being connected
# (`pending_hash`, from the exchange) and the one connected (`grant_hash`, once the computer says connected). The scopes
# Google gave the same exchange are kept the same way (`pending_scopes`, then `grant_scopes`), as a JSON list. A NULL
# `grant_scopes` is a grant from before they were recorded: read only, as it was made.
COLUMNS = {"pending_hash": "TEXT", "grant_hash": "TEXT", "pending_scopes": "TEXT", "grant_scopes": "TEXT"}


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
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
G = "https://www.googleapis.com/auth/"
# The same list as connectors/mail/oauth.py IDENTITY_SCOPES + READ_SCOPES (backend/tests/test_google_connect.py).
READ_SCOPES = ["openid", "email", G + "gmail.readonly", G + "calendar.events.readonly", G + "calendar.freebusy",
               G + "calendar.calendarlist.readonly"]
# The act scopes, asked only with the company's own app: gmail.modify covers the labels, archive and drafts that
# connectors/mail/oauth.py NEEDS gates, and calendar.events the calendar writes. Tico's shared app never asks for them.
ACT_SCOPES = [G + "gmail.modify", G + "calendar.events"]
# What one service's refreshed token may carry: its read-only scopes, and its act scope when its grant has it.
SERVICE_SCOPES = {"gmail": {G + "gmail.readonly"},
                  "calendar": {G + "calendar.events.readonly", G + "calendar.freebusy",
                               G + "calendar.calendarlist.readonly"}}
SERVICE_ACT_SCOPES = {"gmail": {G + "gmail.modify"}, "calendar": {G + "calendar.events"}}
READ_SERVICE_SCOPES = sorted(set().union(*SERVICE_SCOPES.values()))     # a grant from before scopes were recorded
# Google names the identity scopes in an answer by their full address, whatever was asked (`email` comes back as
# userinfo.email). They are not mail or calendar access, so an answer carrying them is not wider than the app's grant.
IDENTITY_NAMES = {G + "userinfo.email", G + "userinfo.profile"}
APPS = {"settings": "own", "env": "shared"}     # the Google app a connect signs in with, as the person is told it
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
    "reconnect": ("Tico's Google app was changed on this server, so this connection has to be made again.",
                  "Connect again."),
}
# Offered to a connection made with the company's own app that lacks some of its write access, keyed by what is
# missing: "both", "mail" (labels, archive and drafts) or "calendar" (events). Connecting again is the person's choice:
# nothing is asked for without it.
UPGRADE = {
    "both": ("This connection only reads your mail and calendar.",
             "Connect Google again to let Tico also label, archive and draft mail, and add calendar events."),
    # One missing while the other is not in use either (held but turned off, or never given).
    "mail_only": ("This connection only reads your mail.",
                  "Connect Google again to let Tico also label, archive and draft mail."),
    "calendar_only": ("This connection only reads your calendar.",
                      "Connect Google again to let Tico also add calendar events."),
    "mail": ("This connection can add calendar events, but only reads your mail.",
             "Connect Google again to let Tico also label, archive and draft mail."),
    "calendar": ("This connection can label, archive and draft mail, but only reads your calendar.",
                 "Connect Google again to let Tico also add calendar events."),
}


class ConnectorResult(M.Contract):
    status: Literal["connected", "failed", "disconnected"]
    kind: str = Field(default="", max_length=40)
    message: str = Field(default="", max_length=500)
    hint: str = Field(default="", max_length=500)
    can: list[str] = Field(default_factory=list, max_length=20)


class ClaimRequest(M.Contract):
    holds_mail: bool = False        # this computer has a Google key or connected accounts (runner_support/connectors.py)


class TokenRequest(M.Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    service: Literal["gmail", "calendar"]
    refresh_token: str = Field(min_length=1, max_length=512)
    scopes: list[str] = Field(min_length=1, max_length=4)


def app_source(c, settings):
    """The OAuth client a connect signs in with now: 'settings' (the company's own app), 'env' (Tico's shared app, the
    server's environment) or None when there is none."""
    client = oauth_client(c, settings)
    return client[2] if client else None


def grantable(source, may=None):
    """The scopes a sign-in asks Google for with this OAuth client: read only with the shared app, and with the
    company's own app also each act scope What Tico may do allows (Settings > Google; `may` None is all of them, the
    most this app may ever be given)."""
    if source != "settings":
        return READ_SCOPES
    allowed = acts_allowed(may)
    return READ_SCOPES + [scope for scope, service in zip(ACT_SCOPES, ("gmail", "calendar")) if allowed[service]]


def overbroad(answer, source):
    """The scopes in the exchange's answer that this app may not grant, empty when there are none. An answer with no
    scope list has none."""
    told = set(str(answer.get("scope") or "").split())
    return told - set(grantable(source)) - IDENTITY_NAMES


def granted_scopes(answer, source):
    """The scopes Google gave in the exchange's answer, never more than `grantable` allows that app. An answer with no
    scope list keeps read only: the grant holds only what Google confirmed. (An answer wider than the app may grant is
    refused before this, see `overbroad`.)"""
    told = str(answer.get("scope") or "").split()
    if not told:
        return READ_SERVICE_SCOPES
    return sorted(set(told) & set(grantable(source)))


def refresh_scopes(service, recorded, may=None):
    """The scopes a refresh for `service` may ask Google for, on a grant holding `recorded` (the JSON list that
    `granted_scopes` stored), and with `may` (What Tico may do) only the act scope it allows. NULL is a grant from
    before scopes were recorded, which was read only. The grant keeps what Google gave: a box turned back on needs no
    new sign-in."""
    given = set(json.loads(recorded)) if recorded is not None else set(READ_SERVICE_SCOPES)
    acts = SERVICE_ACT_SCOPES[service] if acts_allowed(may)[service] else set()
    return given & (SERVICE_SCOPES[service] | acts)


def redirect_uri(settings):
    return settings.public_url.rstrip("/") + CALLBACK


def post_token(fields, uri=TOKEN_URI):
    """Google's answer to a form post to one of its OAuth endpoints: the token endpoint, or revoke (REVOKE_URI). Raises
    urllib.error.HTTPError on a refusal, OSError when Google can't be reached."""
    request = urllib.request.Request(uri, data=urllib.parse.urlencode(fields).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=GOOGLE_SECONDS) as answer:
        return json.loads(answer.read().decode() or "{}")


def revoke_at_google(token):
    """Ask Google to revoke a token this server was just given and must not keep, best effort: whether Google confirmed
    it is returned, and the token is never logged or stored."""
    if not token:
        return False
    try:
        post_token({"token": token}, REVOKE_URI)
        return True
    except (urllib.error.HTTPError, OSError, ValueError):
        return False


def google_token(client, fields):
    """Google's answer to a code exchange or a refresh with this server's client added. A refusal is a Problem
    naming only Google's reason as a kind (TOKEN_ERRORS); Google's body and the request never reach a log."""
    fields = dict(fields, client_id=client[0], client_secret=client[1])
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
    c.execute("UPDATE google_connects SET status='failed',kind='expired',oauth_state=NULL,verifier=NULL,code=NULL,"
              "updated_at=? WHERE status IN ('signing_in','ready') AND started_at<?", (H.now(), now - SIGN_IN_SECONDS))
    c.execute("UPDATE google_connects SET status='failed',kind='unknown',oauth_state=NULL,verifier=NULL,code=NULL,"
              "updated_at=? WHERE status IN ('finishing','revoking') AND started_at<?", (H.now(), now - CLAIM_SECONDS))


def view(row, mailbox, syncing, source, may=None):
    """What the person sees. `source` is the OAuth client a connect would use now (see app_source), `may` What Tico may
    do. A connected grant shows the scopes Google gave it, and `acts` says, per service, whether Tico uses that
    service's write access now (mail: labels, archive and drafts; calendar: events): the grant has it and What Tico may
    do allows it. A connection made with the company's own app that lacks one What Tico may do allows is told what a
    reconnect would add; nothing changes until the person reconnects."""
    status = row["status"] if row else "none"
    wanted = acts_allowed(may)
    wanted = {"mail": wanted["gmail"], "calendar": wanted["calendar"]}
    out = {"configured": source is not None, "app": APPS.get(source), "mailbox": mailbox, "syncing": syncing,
           "status": status, "scopes": grantable(source, may) if source else READ_SCOPES, "can": [],
           "acts": {"mail": False, "calendar": False},
           "updated_at": row["updated_at"] if row else None, "kind": None, "message": None, "hint": None}
    if row and status == "connected":
        given = set(json.loads(row["grant_scopes"]) if row["grant_scopes"] is not None else READ_SERVICE_SCOPES)
        held = {"mail": bool(given & SERVICE_ACT_SCOPES["gmail"]),
                "calendar": bool(given & SERVICE_ACT_SCOPES["calendar"])}
        acts = {name: held[name] and wanted[name] for name in held}
        out.update(can=json.loads(row["can"] or "[]"), scopes=sorted(given), acts=acts)
        missing = [name for name in ("mail", "calendar") if wanted[name] and not held[name]]
        if source == "settings" and missing:
            if len(missing) == 2:
                lacks = "both"
            else:
                other = "calendar" if missing[0] == "mail" else "mail"
                lacks = missing[0] if acts[other] else missing[0] + "_only"
            out.update(message=UPGRADE[lacks][0], hint=UPGRADE[lacks][1])
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


def _credentialed_publishers(c, runner_id):
    """The other publishers that said recently that they hold mail credentials. Only publishers are recorded
    (google_claim), so a non-publisher's grant or answer never holds a first connect back."""
    return [row[0] for row in c.execute(
        "SELECT h.runner_id FROM google_mail_holders h JOIN runners r ON r.id=h.runner_id AND r.revoked_at IS NULL "
        "WHERE h.runner_id<>? AND h.holds=1 AND h.seen_at>?", (runner_id, time.time() - HOLDER_SECONDS)).fetchall()]


def claimable(c, runner_id, holds, named=False):
    """The next connect or disconnect this computer may take, or None (plan v5 V).

    google_rights.takes decides each row. With the person's message bot on a live computer, that computer takes the first
    connect and the reconnect, online or not: a publisher waits for it rather than taking the connect, and a reconnect
    goes there whoever held the earlier grant (the old holder's refreshes are then refused, see google_refresh). A
    disconnect goes to the holder, the only computer that can revoke the grant it holds. With no bot computer, the holder
    keeps its grant; a publisher takes a first connect, or a grant whose holder was removed, unless another publisher has
    said it holds mail credentials recently and takes that row."""
    expire(c)
    rows = c.execute("SELECT * FROM google_connects WHERE status IN ('ready','disconnecting') "
                     "ORDER BY updated_at").fetchall()
    if not rows:
        return None
    hosts, live = google_rights.inbox_hosts(c), google_rights.live_runners(c)
    others = _credentialed_publishers(c, runner_id)
    for row in rows:
        # A publisher without mail credentials defers to another publisher that has them, but only one that takes this
        # row (google_rights.takes): a computer holding some other mailbox never holds this one back.
        elsewhere = any(google_rights.takes(other, True, row, hosts, live) for other in others)
        orphan = named and (holds or not elsewhere)     # a publisher, unless another publisher with credentials takes it
        if google_rights.takes(runner_id, orphan, row, hosts, live):
            return row
    return None


def pending(c, runner_id, named=False):
    """Whether a connect or disconnect waits for this computer, which has no mail credentials yet (otherwise its
    connectors job runs anyway): the job then runs for it (backend/connectors.py)."""
    return claimable(c, runner_id, False, named) is not None


def install(app, store, member, mutate):
    """The four routes a computer takes its part of a connect or disconnect on. `member(c, who)` returns the computer
    and whether it is a publisher; each route then applies the narrow gate of plan v4 O, never publisher() alone."""
    settings = store.settings

    def person_view(c, who):
        pid = require_self(who)
        mailbox, _ = mailbox_for(c, pid)
        syncing = any(b["address"] == mailbox for b in routines.message_bot_mailboxes(c))
        row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (pid,)).fetchone()
        if row and row["mailbox"] != mailbox:
            row = None                  # their mailbox changed since: the old connection says nothing about this one
        return view(row, mailbox, syncing, app_source(c, settings), permissions(c))

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
            client = oauth_client(c, settings)
            if client is None:
                raise Problem("google_app", "This Tico server has no Google app set up, so Connect Google is not "
                                            "available yet. Whoever runs the server sets TICO_GOOGLE_OAUTH_CLIENT_ID "
                                            "and TICO_GOOGLE_OAUTH_CLIENT_SECRET (docs/mail.md).", 409)
            mailbox, _ = mailbox_for(c, pid)
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            query = {"client_id": client[0], "redirect_uri": redirect_uri(settings),
                     "response_type": "code", "scope": " ".join(grantable(client[2], permissions(c))), "access_type": "offline",
                     "prompt": "consent", "login_hint": mailbox, "state": state, "code_challenge": challenge,
                     "code_challenge_method": "S256"}
            now = H.now()
            c.execute("INSERT INTO google_connects(person_id,mailbox,status,oauth_state,verifier,code,kind,message,hint,"
                      "runner_id,started_at,created_at,updated_at) VALUES(?,?,'signing_in',?,?,NULL,NULL,NULL,NULL,NULL,?,?,?) "
                      "ON CONFLICT(person_id) DO UPDATE SET mailbox=excluded.mailbox,status='signing_in',"
                      "oauth_state=excluded.oauth_state,verifier=excluded.verifier,code=NULL,kind=NULL,message=NULL,hint=NULL,"
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
            row = c.execute("SELECT * FROM google_connects WHERE oauth_state=? AND status='signing_in'",
                            (state,)).fetchone() if state else None
            if (not row or who.role not in ("owner", "human") or row["person_id"] != H.actor_id(who.actor)
                    or not secrets.compare_digest(row["oauth_state"], state)):
                return RedirectResponse(INBOX, status_code=302)
            now = H.now()
            if error or not code:
                kind = CALLBACK_ERRORS.get(error, "declined" if error else "error")
                c.execute("UPDATE google_connects SET status='failed',kind=?,oauth_state=NULL,verifier=NULL,code=NULL,"
                          "updated_at=? WHERE person_id=?", (kind, now, row["person_id"]))
                H.event(c, who.actor, "mail.google.connect.failed", row["mailbox"], {"kind": kind})
                return RedirectResponse(INBOX, status_code=302)
            # The state is spent; the code waits for the computer that keeps the grant.
            c.execute("UPDATE google_connects SET status='ready',oauth_state=NULL,code=?,updated_at=? WHERE person_id=?",
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
                      "mailbox=excluded.mailbox,status='disconnecting',oauth_state=NULL,verifier=NULL,code=NULL,kind=NULL,"
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
            runner, named = member(c, request.state.identity)
            holds = bool(body and body.holds_mail)
            # Only a publisher's answer is kept: claimable lets a publisher without credentials defer to one that has them.
            if named:
                c.execute("INSERT INTO google_mail_holders(runner_id,holds,seen_at) VALUES(?,?,?) ON CONFLICT(runner_id) "
                          "DO UPDATE SET holds=excluded.holds,seen_at=excluded.seen_at",
                          (runner["id"], int(holds), time.time()))
            else:
                c.execute("DELETE FROM google_mail_holders WHERE runner_id=?", (runner["id"],))
            # Only recent answers count (claimable), so older ones and revoked computers' go.
            c.execute("DELETE FROM google_mail_holders WHERE seen_at<? OR runner_id IN "
                      "(SELECT id FROM runners WHERE revoked_at IS NOT NULL)", (time.time() - HOLDER_SECONDS,))
            # The narrow gate (plan v4 O): a publisher, or a computer that hosts someone's message bot, or one holding a grant.
            hosted = any(host["runner"] == runner["id"] for host in google_rights.inbox_hosts(c).values())
            if not named and not hosted and not c.execute(
                    "SELECT 1 FROM google_connects WHERE holder=? LIMIT 1", (runner["id"],)).fetchone():
                raise Problem("forbidden", google_rights.FORBIDDEN, 403)
            row = claimable(c, runner["id"], holds, named)
            if not row:
                return {"connect": None}
            action = "connect" if row["status"] == "ready" else "disconnect"
            client = oauth_client(c, settings) if action == "connect" else None
            if action == "connect" and client is None:
                c.execute("UPDATE google_connects SET status='failed',kind='app_config',code=NULL,verifier=NULL,"
                          "updated_at=? WHERE person_id=?", (H.now(), row["person_id"]))
                return {"connect": None}
            c.execute("UPDATE google_connects SET status=?,runner_id=?,started_at=?,updated_at=? WHERE person_id=?",
                      ("finishing" if action == "connect" else "revoking", runner["id"], time.time(), H.now(),
                       row["person_id"]))
            out = {"id": row["person_id"], "action": action, "mailbox": row["mailbox"]}
            if action == "connect":
                out.update(client_id=client[0], broker="server")
            return {"connect": out}

    @app.post("/api/v2/connectors/mail/google/{person_id}/exchange")
    def google_exchange(person_id: str, request: Request, body: M.Empty | None = None):
        """Trade a claimed connect's code for its grant, for the computer that claimed it, once. The code and the
        verifier are cleared before Google is asked; the answer goes to that computer and is not kept here. An answer
        with more scopes than this app may grant is refused: its grant is revoked at Google and nothing is passed on."""
        with store.transaction() as c:
            runner, named = member(c, request.state.identity)
            row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (person_id,)).fetchone()
            if (not row or row["runner_id"] != runner["id"] or row["status"] != "finishing" or not row["code"]
                    or not row["verifier"]):
                raise Problem("state", "No sign-in code waits for this computer to exchange", 409)
            hosts, live = google_rights.inbox_hosts(c), google_rights.live_runners(c)
            if not google_rights.takes(runner["id"], named, row, hosts, live):
                raise Problem("forbidden", google_rights.FORBIDDEN, 403)
            code, verifier = row["code"], row["verifier"]
            c.execute("UPDATE google_connects SET code=NULL,verifier=NULL,updated_at=? WHERE person_id=?",
                      (H.now(), person_id))
        with store.read() as c:
            client = oauth_client(c, settings)
        if client is None:
            raise Problem("google_refused", "This Tico server has no Google app set up", 409,
                          extra={"kind": "app_config"})
        answer = google_token(client, {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                       "redirect_uri": redirect_uri(settings)})
        if overbroad(answer, client[2]):
            # Google gave more access than this app may grant. Nothing is passed on or kept: the grant is revoked at
            # Google (best effort), and the sign-in fails with no token for the computer.
            revoke_at_google(answer.get("refresh_token") or answer.get("access_token"))
            raise Problem("google_refused", "Google granted more access than Tico asks for, so nothing was connected",
                          409, extra={"kind": "scope"})
        if answer.get("refresh_token"):
            # The scopes Google gave this grant, never more than this app may grant (granted_scopes), kept with its
            # fingerprint.
            with store.transaction() as c:
                c.execute("UPDATE google_connects SET pending_hash=?,pending_scopes=? WHERE person_id=? "
                          "AND runner_id=? AND status='finishing'",
                          (fingerprint(answer["refresh_token"]), json.dumps(granted_scopes(answer, client[2])),
                           person_id, runner["id"]))
        return {k: answer[k] for k in ("access_token", "refresh_token", "expires_in", "scope") if k in answer}

    @app.post("/api/v2/connectors/mail/google/token")
    def google_refresh(request: Request, body: TokenRequest):
        """A short-lived access token for a grant this computer holds: one service, read only. The grant must be the
        one this server issued for that mailbox and this computer (its fingerprint); nothing about the request is
        stored."""
        wanted = set(body.scopes)
        if not wanted <= SERVICE_SCOPES[body.service] | SERVICE_ACT_SCOPES[body.service]:
            raise Problem("scope", f"A {body.service} token from Connect Google carries only its {body.service} "
                                   "scopes", 422)
        with store.transaction() as c:
            runner, named = member(c, request.state.identity)
            held = c.execute("SELECT * FROM google_connects WHERE lower(mailbox)=? AND holder=? AND grant_hash=? "
                             "LIMIT 1", (body.mailbox.strip().lower(), runner["id"],
                                         fingerprint(body.refresh_token))).fetchone()
            if not held:
                # Also a grant whose connected result never reached the server: the computer reconnects.
                raise Problem("not_this_grant", "This computer holds no Connect Google grant from this server for that "
                                                "mailbox", 403)
            # The one rule (google_rights.takes): the computer hosting the person's message bot, or, with no host, the holder.
            # A grant that moved away from its bot's computer is refused here, and the answer says to reconnect (the mail CLI
            # stops on it).
            hosts, live = google_rights.inbox_hosts(c), google_rights.live_runners(c)
            if not google_rights.takes(runner["id"], named, held, hosts, live):
                raise Problem("not_this_grant", "This computer no longer runs the message bot for that mailbox, so it does "
                                                "not refresh its Connect Google grant. Reconnect it in Settings > Google.",
                              403)
            # The grant's own scopes bound the refresh: a read-only grant is refused here, with no call to Google.
            if not wanted <= refresh_scopes(body.service, held["grant_scopes"]):
                raise Problem("scope", f"This Connect Google grant does not hold all the {body.service} scopes asked "
                                       "for. Connect Google again in Settings > Google to add them.", 422)
            # What Tico may do narrows the token, not the grant: a write turned off is left out of what Google is
            # asked for, and the computer reads the narrower list back (`scopes`), so it refuses that write itself.
            wanted &= refresh_scopes(body.service, held["grant_scopes"], permissions(c))
            if not wanted:
                raise Problem("scope", f"What Tico may do (Settings > Google) leaves none of the {body.service} "
                                       "scopes asked for", 422)
        with store.read() as c:
            client = oauth_client(c, settings)
        if client is None:
            raise Problem("google_refused", "This Tico server has no Google app set up", 409,
                          extra={"kind": "app_config"})
        answer = google_token(client, {"grant_type": "refresh_token", "refresh_token": body.refresh_token,
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
            runner, named = member(c, request.state.identity)
            row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (person_id,)).fetchone()
            if not row:
                raise Problem("not_found", "No Google connect is waiting for this person", 404)
            if row["runner_id"] != runner["id"] or row["status"] not in ("finishing", "revoking"):
                raise Problem("state", "This Google connect is not this computer's to finish", 409)
            hosts, live = google_rights.inbox_hosts(c), google_rights.live_runners(c)
            if not google_rights.takes(runner["id"], named, row, hosts, live):
                raise Problem("forbidden", google_rights.FORBIDDEN, 403)
            if (row["status"] == "finishing") != (body.status != "disconnected"):
                raise Problem("state", "That result does not answer this request", 409)
            kind = body.kind if body.kind in KINDS else ("error" if body.status == "failed" else None)
            failed = body.status == "failed"
            # The grant is where it was connected, gone once revoked, and unchanged by a failure (a failed reconnect
            # stores nothing, so an earlier grant stays where it was).
            holder = {"connected": runner["id"], "disconnected": None}.get(body.status, row["holder"])
            # Likewise the grant the server will refresh: the new one once connected, none once revoked.
            grant_hash = {"connected": row["pending_hash"], "disconnected": None}.get(body.status, row["grant_hash"])
            # And the scopes that grant holds, from the exchange the same way: what Google gave it, then none once revoked.
            grant_scopes = {"connected": row["pending_scopes"],
                            "disconnected": None}.get(body.status, row["grant_scopes"])
            # The client it is recorded with: this server's now, for a connect that succeeded. A failed reconnect keeps
            # the earlier grant's client, and a revoked grant has none to keep.
            client = oauth_client(c, settings) if body.status == "connected" else None
            client_id = client[0] if client else row["client_id"]
            c.execute("UPDATE google_connects SET status=?,kind=?,message=?,hint=?,can=?,holder=?,grant_hash=?,"
                      "grant_scopes=?,client_id=?,pending_hash=NULL,pending_scopes=NULL,"
                      "oauth_state=NULL,verifier=NULL,code=NULL,updated_at=? WHERE person_id=?",
                      (body.status, kind if failed else None, body.message if failed else None,
                       body.hint if failed else None,
                       json.dumps(sorted(set(body.can))) if body.status == "connected" else None, holder, grant_hash,
                       grant_scopes, client_id, H.now(), person_id))
            H.event(c, "human:" + person_id, "mail.google." + body.status, row["mailbox"],
                    {"kind": kind} if body.status == "failed" else {})
            if body.status == "connected" and row["mailbox"]:
                # Its tasks' reconnect cards end now, not at the connector's next report (backend/reconnects.py).
                from . import reconnects
                reconnects.google_connected(c, row["mailbox"])
            return {"ok": True}
