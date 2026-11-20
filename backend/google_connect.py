"""Tools > Settings > Email > Connect Google: a person connects their own Google account, with no admin.

The person asks to connect (`POST /api/v2/mail/google/connect`) and gets Google's sign-in address for the server's OAuth
client (Tico's shared app, TICO_GOOGLE_OAUTH_CLIENT_ID/SECRET, or the company's own app from Tools > Settings > Email;
nobody makes a Google Cloud project, a service account or domain-wide delegation). Google sends their browser back to
`/api/v2/mail/google/callback`, which checks the state belongs to the same person and trades the one-time code for the
grant right there, with PKCE. The server confirms the account that signed in is the mailbox asked for (the ID token
Google returned with the grant), and keeps the refresh token sealed with the vault (`grant_sealed`, see
backend/google_tokens.py, the one door every Gmail and Calendar call takes). No computer ever holds the grant or a
token from it.

The OAuth client secret never leaves this server, and neither does the refresh token: it is never in a response, a log,
an event or `tico sql` (google_connects is not readable there). A reconnect that moves to a new grant does not revoke
the old one at Google: one client and one person share one grant there, so that would end the new grant too.

Only the person connects or disconnects their own mailbox: the mailbox their message bot reads, else their email.
Not a bot, not BotOps or the Assistant acting for them, not an admin for someone else. Disconnect forgets the grant at
once and revokes it at Google from the server; when Google cannot be reached the person is told to remove Tico at
myaccount.google.com/permissions.

The scopes depend on the Google app that signs the person in. Tico's shared app (the server's environment) asks for
read only (READ_SCOPES). The company's own app, set in Tools > Settings > Email, also asks for the act scopes (ACT_SCOPES:
gmail.modify, which labels, archives, drafts and sends mail, and calendar.events), since an Internal app needs no Google
review for them. The grant keeps only the scopes Google gave the exchange (`grant_scopes`, never more than the app may
grant), and a refresh asks Google for no scope the grant lacks: a read-only grant stays read only until its person
connects again. An answer wider than the app may grant is revoked and nothing is kept. A Workspace whose admin blocks
Tico is named as that ("admin_policy"), never answered with a broader request or a service account.
"""

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
import urllib.parse

from fastapi import Request
from fastapi.responses import RedirectResponse

from . import models as M
from . import outbound_http, routines
from .google_workspace import acts as acts_allowed, oauth_client, permissions
from .store import H, P, Problem

log = logging.getLogger(__name__)

# Each connect or disconnect is one operation (`operation_id`, new at each start): the callback's exchange writes its grant
# only while its operation is still the row's, so a connect replaced or disconnected meanwhile keeps nothing. The grant
# connected is `grant_sealed` (its refresh token, sealed under google_tokens.context(person, grant_operation_id)), the
# scopes Google gave it (`grant_scopes`, a JSON list; NULL is read only) and the client that issued it (`client_id`).
# A reconnect leaves them in place until its own grant replaces them, so a sign-in that fails keeps the earlier grant.
# `answered` is unused: migration 20261112160353 adds it whenever it runs, so the shape keeps it (see the migration
# 20261116145802_google_grants_on_server).


AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
G = "https://www.googleapis.com/auth/"
READ_SCOPES = [
    "openid",
    "email",
    G + "gmail.readonly",
    G + "calendar.events.readonly",
    G + "calendar.freebusy",
    G + "calendar.calendarlist.readonly",
]
# The act scopes, asked only with the company's own app: gmail.modify labels, archives, drafts and sends mail, and
# calendar.events writes calendar events. Tico's shared app never asks for them.
ACT_SCOPES = [G + "gmail.modify", G + "calendar.events"]
# What one service's refreshed token may carry: its read-only scopes, and its act scope when its grant has it.
SERVICE_SCOPES = {
    "gmail": {G + "gmail.readonly"},
    "calendar": {G + "calendar.events.readonly", G + "calendar.freebusy", G + "calendar.calendarlist.readonly"},
}
SERVICE_ACT_SCOPES = {"gmail": {G + "gmail.modify"}, "calendar": {G + "calendar.events"}}
READ_SERVICE_SCOPES = sorted(set().union(*SERVICE_SCOPES.values()))  # a grant from before scopes were recorded
# Google names the identity scopes in an answer by their full address, whatever was asked (`email` comes back as
# userinfo.email). They are not mail or calendar access, so an answer carrying them is not wider than the app's grant.
IDENTITY_NAMES = {G + "userinfo.email", G + "userinfo.profile"}
APPS = {"settings": "own", "env": "shared"}  # the Google app a connect signs in with, as the person is told it
GOOGLE_SECONDS = 20
CALLBACK = "/api/v2/mail/google/callback"
INBOX = "/#/tools/settings/email"  # Tools > Settings > Email, which reads the outcome itself
SIGN_IN_SECONDS = 600  # Google's codes last about ten minutes; a sign-in left open longer starts again
FINISH_SECONDS = 120  # an exchange whose request never finished (the server stopped meanwhile)
# What Google may put in the callback's `error`, as the kind the person is shown.
CALLBACK_ERRORS = {
    "admin_policy_enforced": "admin_policy",
    "org_internal": "app_unavailable",
    "access_denied": "declined",
    "invalid_scope": "app_config",
    "invalid_client": "app_config",
    "unauthorized_client": "app_config",
    "redirect_uri_mismatch": "app_config",
}
# What Google's token endpoint may answer, as the kind a refusal carries.
TOKEN_ERRORS = {
    "admin_policy_enforced": "admin_policy",
    "org_internal": "app_unavailable",
    "invalid_grant": "signin",
    "invalid_client": "app_config",
    "unauthorized_client": "app_config",
    "invalid_scope": "app_config",
    "redirect_uri_mismatch": "app_config",
}


def policy_message(mailbox):
    domain = mailbox.rpartition("@")[2]
    return (
        f"The Google Workspace for {domain} has a policy that blocks Tico, so Google refused the sign-in. "
        "This is the Workspace administrator's setting, not something Tico or you can change, and Tico did not "
        "ask for more access to get around it.",
        f"Ask the administrator for {domain} to allow Tico in the Admin console (Security > API controls > "
        "App access control), or to trust it for your account. Then connect again.",
    )


MESSAGES = {
    "app_unavailable": (
        "Google says Tico's app is not open to accounts outside its own organization yet.",
        "This is Tico's Google app setting, not yours: tell Tico's support.",
    ),
    "declined": (
        "You didn't approve the access on Google's page, so nothing was connected.",
        "Connect again and choose Allow. Tico asks only to read your mail and calendar.",
    ),
    "app_config": (
        "Tico's Google app is not set up correctly for this server, so Google refused the sign-in.",
        "This is Tico's Google app configuration, not yours: tell Tico's support.",
    ),
    "expired": ("The sign-in took too long and expired before it finished.", "Connect again."),
    "unknown": ("Tico did not hear back from Google in time, so nothing was connected.", "Connect again."),
    "reconnect": (
        "Tico's Google app was changed on this server, so this connection has to be made again.",
        "Connect again.",
    ),
    "identity_mismatch": (
        "Another Google account signed in, not {mailbox}, so nothing was connected.",
        "Sign in to Google as {mailbox} (a private window helps when the browser picks another account).",
    ),
    "no_refresh_token": (
        "Google returned no lasting access, so the connection would end within the hour.",
        "Remove Tico at myaccount.google.com/permissions, then connect again.",
    ),
    "no_gmail": (
        "The Gmail box was unticked on Google's page, so nothing was connected.",
        "Connect again and leave the box to read your email ticked.",
    ),
    "scope": ("Google granted more access than Tico asks for, so nothing was connected.", "Connect again."),
    "revoked": (
        "Google gave Tico more access than it asked for, so Tico ended this connection and revoked it at Google.",
        "Connect Google again.",
    ),
    "code_rejected": ("Google refused the sign-in code.", "Codes work once and expire in minutes: connect again."),
    "network": ("Tico's server could not reach Google to finish the sign-in.", "Try again in a few minutes."),
}
# What a person sees about a connection that ended without them asking, or a disconnect Google did not confirm.
ENDED = {
    "reconnect": (
        "Tico now keeps your Google connection on its server, so it has to be made once more.",
        "Connect Google again.",
    ),
    "not_revoked": (
        "Tico forgot your Google access, but could not reach Google to revoke it.",
        "Remove Tico at myaccount.google.com/permissions to end it at Google too.",
    ),
}
# Offered to a connection made with the company's own app that lacks some of its write access, keyed by what is
# missing: "both", "mail" (labels, archive and drafts) or "calendar" (events). Connecting again is the person's
# choice: nothing is asked for without it.
UPGRADE = {
    "both": (
        "This connection only reads your mail and calendar.",
        "Connect Google again to let Tico also label, archive and draft mail, and add calendar events.",
    ),
    # One missing while the other is not in use either (held but turned off, or never given).
    "mail_only": (
        "This connection only reads your mail.",
        "Connect Google again to let Tico also label, archive and draft mail.",
    ),
    "calendar_only": (
        "This connection only reads your calendar.",
        "Connect Google again to let Tico also add calendar events.",
    ),
    "mail": (
        "This connection can add calendar events, but only reads your mail.",
        "Connect Google again to let Tico also label, archive and draft mail.",
    ),
    "calendar": (
        "This connection can label, archive and draft mail, but only reads your calendar.",
        "Connect Google again to let Tico also add calendar events.",
    ),
}


def app_source(c, settings):
    """The OAuth client a connect signs in with now: 'settings' (the company's own app), 'env' (Tico's shared app, the
    server's environment) or None when there is none."""
    client = oauth_client(c, settings)
    return client[2] if client else None


def grantable(source, may=None):
    """The scopes a sign-in asks Google for with this OAuth client: read only with the shared app, and with the
    company's own app also each act scope What Tico may do allows (Tools > Settings > Email; `may` None is all of them, the
    most this app may ever be given)."""
    if source != "settings":
        return READ_SCOPES
    allowed = acts_allowed(may)
    return READ_SCOPES + [
        scope for scope, service in zip(ACT_SCOPES, ("gmail", "calendar"), strict=True) if allowed[service]
    ]


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
    outbound_http.Failed, carrying Google's refusal when it answered one; ValueError when the answer is not JSON. Tests
    replace this function."""
    reply = outbound_http.send("POST", uri, data=dict(fields), timeout=GOOGLE_SECONDS).check()
    return json.loads(reply.content.decode() or "{}")


def revoke_at_google(token):
    """Ask Google to revoke a grant, best effort: whether Google confirmed it is returned, and the token is never
    logged or stored."""
    if not token:
        return False
    try:
        post_token({"token": token}, REVOKE_URI)
        return True
    except (outbound_http.Failed, ValueError):
        return False


def google_token(client, fields):
    """Google's answer to a code exchange or a refresh with this server's client added. A refusal is a Problem
    naming only Google's reason as a kind (TOKEN_ERRORS); Google's body and the request never reach a log."""
    fields = dict(fields, client_id=client[0], client_secret=client[1])
    try:
        answer = post_token(fields)
    except outbound_http.Failed as e:
        if e.reply is None:
            raise Problem(
                "google_refused",
                f"Tico's server could not reach Google ({type(e.__cause__ or e).__name__})",
                409,
                extra={"kind": "network"},
            ) from None
        try:
            error = str(json.loads(e.reply.content.decode() or "{}").get("error") or "")
        except (ValueError, AttributeError):
            error = ""
        # Google's own outage is a network failure to the person, not a refused grant.
        kind = "network" if e.reply.status >= 500 else TOKEN_ERRORS.get(error.strip().lower(), "error")
        raise Problem(
            "google_refused", f"Google refused it ({error or e.reply.status})", 409, extra={"kind": kind}
        ) from None
    except ValueError as e:
        raise Problem(
            "google_refused",
            f"Tico's server could not reach Google ({type(e).__name__})",
            409,
            extra={"kind": "network"},
        ) from None
    if not isinstance(answer, dict) or not answer.get("access_token"):
        raise Problem("google_refused", "Google returned no access token", 409, extra={"kind": "error"})
    return answer


def signed_in_as(answer):
    """The verified address of the account that signed in, from the ID token Google returned with the grant, or ''.
    The ID token came straight from Google's token endpoint over TLS, so its claims are read without checking its
    signature (OpenID Connect Core 3.1.3.7)."""
    parts = str(answer.get("id_token") or "").split(".")
    if len(parts) != 3:
        return ""
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error):
        return ""
    if not isinstance(claims, dict) or claims.get("email_verified") not in (True, "true"):
        return ""
    return str(claims.get("email") or "").strip().lower()


def refusal(answer, source, mailbox):
    """Why the exchange's grant is not kept, as a kind, or None to keep it."""
    if overbroad(answer, source):
        return "scope"
    if signed_in_as(answer) != mailbox:
        return "identity_mismatch"
    if not answer.get("refresh_token"):
        return "no_refresh_token"
    if not set(granted_scopes(answer, source)) & {G + "gmail.readonly", G + "gmail.modify"}:
        return "no_gmail"
    return None


def mailbox_for(c, person_id):
    """The mailbox that is this person's own: their message bot's, else their email."""
    from .views import roster

    people = roster(c)
    person = P.person(person_id, people)
    if not person:
        raise Problem("not_found", "You are not on this team's roster", 404)
    return routines.mailbox_of(person, routines.message_bot_mailboxes(c, people)), person


def new_operation():
    return "gc_" + secrets.token_hex(16)


def require_self(who):
    """The person acting as themselves: never a bot, a computer, or BotOps or the Assistant acting for them."""
    if who.role not in ("owner", "human") or who.via or who.task_actor:
        raise Problem(
            "forbidden",
            "Only the person whose mailbox it is connects or disconnects their Google "
            "account, signed in to Tico themselves",
            403,
        )
    return H.actor_id(who.actor)


def expiring(c):
    """Whether `expire` has anything to end: the status page reads without the write lock when it has not."""
    now = time.time()
    return c.execute(
        "SELECT 1 FROM google_connects WHERE (status='signing_in' AND started_at<?) "
        "OR (status='finishing' AND started_at<?) LIMIT 1",
        (now - SIGN_IN_SECONDS, now - FINISH_SECONDS),
    ).fetchone()


def expire(c):
    """Sign-ins nobody finished, and exchanges whose request never ended, end as failures the person can read. A grant
    connected before stays."""
    now = time.time()
    c.execute(
        "UPDATE google_connects SET status='failed',kind='expired',oauth_state=NULL,verifier=NULL,"
        "updated_at=? WHERE status='signing_in' AND started_at<?",
        (H.now(), now - SIGN_IN_SECONDS),
    )
    c.execute(
        "UPDATE google_connects SET status='failed',kind='unknown',oauth_state=NULL,verifier=NULL,"
        "updated_at=? WHERE status='finishing' AND started_at<?",
        (H.now(), now - FINISH_SECONDS),
    )


def view(row, mailbox, syncing, source, may=None):
    """What the person sees. `source` is the OAuth client a connect would use now (see app_source), `may` What Tico may
    do. A connected grant shows the scopes Google gave it, and `acts` says, per service, whether Tico uses that
    service's write access now (mail: labels, archive and drafts; calendar: events): the grant has it and What
    Tico may do allows it. A connection made with the company's own app that lacks one What Tico may do allows is told
    what a reconnect would add; nothing changes until the person reconnects."""
    status = row["status"] if row else "none"
    wanted = acts_allowed(may)
    wanted = {"mail": wanted["gmail"], "calendar": wanted["calendar"]}
    out = {
        "configured": source is not None,
        "app": APPS.get(source),
        "mailbox": mailbox,
        "syncing": syncing,
        "status": status,
        "scopes": grantable(source, may) if source else READ_SCOPES,
        "can": [],
        "acts": {"mail": False, "calendar": False},
        "updated_at": row["updated_at"] if row else None,
        "kind": None,
        "message": None,
        "hint": None,
    }
    if row and status == "connected":
        given = set(json.loads(row["grant_scopes"]) if row["grant_scopes"] is not None else READ_SERVICE_SCOPES)
        held = {
            "mail": bool(given & SERVICE_ACT_SCOPES["gmail"]),
            "calendar": bool(given & SERVICE_ACT_SCOPES["calendar"]),
        }
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
        message, hint = (
            policy_message(mailbox)
            if kind == "admin_policy"
            else MESSAGES.get(kind) or (row["message"] or "Connecting failed.", row["hint"] or "Connect again.")
        )
        out.update(kind=kind, message=message.format(mailbox=mailbox), hint=hint.format(mailbox=mailbox))
    if row and status == "disconnected" and row["kind"] in ENDED:
        out.update(kind=row["kind"], message=ENDED[row["kind"]][0], hint=ENDED[row["kind"]][1])
    return out


def install(app, store, mutate):
    """The person's own routes: their status, connect, Google's callback, and disconnect."""
    settings = store.settings
    from . import google_tokens, reconnects

    google_tokens.use_store(store)

    def person_view(c, who):
        pid = require_self(who)
        mailbox, _ = mailbox_for(c, pid)
        syncing = any(b["address"] == mailbox for b in routines.message_bot_mailboxes(c))
        row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (pid,)).fetchone()
        if row and row["mailbox"] != mailbox:
            row = None  # their mailbox changed since: the old connection says nothing about this one
        return view(row, mailbox, syncing, app_source(c, settings), permissions(c))

    def failed(row, kind):
        """The exchange for `row`'s operation failed: it says why, and a grant connected before stays."""
        with store.transaction() as c:
            changed = c.execute(
                "UPDATE google_connects SET status='failed',kind=?,message=NULL,hint=NULL,updated_at=? "
                "WHERE person_id=? AND operation_id=? AND status='finishing'",
                (kind, H.now(), row["person_id"], row["operation_id"]),
            ).rowcount
            if changed:
                H.event(c, "human:" + row["person_id"], "mail.google.connect.failed", row["mailbox"], {"kind": kind})

    def finish(row, client, code, verifier):
        """Trade the code for the grant, check it, and keep it sealed while this operation is still the row's."""
        if client is None:
            return failed(row, "app_config")
        try:
            answer = google_token(
                client,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri(settings),
                },
            )
        except Problem as refused:
            kind = refused.extra.get("kind", "error")
            return failed(row, "code_rejected" if kind == "signin" else kind)
        kind = refusal(answer, client[2], row["mailbox"])
        if kind:
            if kind != "no_refresh_token":
                # Another account's grant, or one wider than asked, is not kept and is ended at Google (best effort).
                revoke_at_google(answer.get("refresh_token") or answer.get("access_token"))
            return failed(row, kind)
        scopes = granted_scopes(answer, client[2])
        with store.transaction() as c:
            sealed = google_tokens.seal(
                c, row["person_id"], row["operation_id"], row["mailbox"], answer["refresh_token"]
            )
            kept = c.execute(
                "UPDATE google_connects SET status='connected',kind=NULL,message=NULL,hint=NULL,can=?,client_id=?,"
                "grant_scopes=?,grant_sealed=?,grant_operation_id=operation_id,updated_at=? "
                "WHERE person_id=? AND operation_id=? AND status='finishing'",
                (
                    json.dumps(google_tokens.described(scopes)),
                    client[0],
                    json.dumps(scopes),
                    sealed,
                    H.now(),
                    row["person_id"],
                    row["operation_id"],
                ),
            ).rowcount
            if not kept:
                now = c.execute("SELECT * FROM google_connects WHERE person_id=?", (row["person_id"],)).fetchone()
            else:
                H.event(c, "human:" + row["person_id"], "mail.google.connected", row["mailbox"], {})
                reconnects.google_connected(c, row["mailbox"])  # its tasks' reconnect cards end now
                return
        # The person disconnected, or connected again, while Google answered: this grant lost and is not kept. It is
        # revoked at Google, unless the row still holds a live grant for the same mailbox from the same app: one
        # client and one person share one grant at Google, so revoking would end that one too.
        log.warning("google connect: operation %s is no longer current", row["operation_id"])
        live = (
            now is not None
            and now["grant_sealed"] is not None
            and now["client_id"] == client[0]
            and str(now["mailbox"]).lower() == str(row["mailbox"]).lower()
        )
        if not live:
            revoke_at_google(answer["refresh_token"])

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
        who = request.state.identity
        ended = {}

        def work(c):
            pid = require_self(who)
            client = oauth_client(c, settings)
            if client is None:
                raise Problem(
                    "google_app",
                    "This Tico server has no Google app set up, so Connect Google is not "
                    "available yet. Whoever runs the server sets TICO_GOOGLE_OAUTH_CLIENT_ID "
                    "and TICO_GOOGLE_OAUTH_CLIENT_SECRET (docs/mail.md).",
                    409,
                )
            mailbox, _ = mailbox_for(c, pid)
            state = secrets.token_urlsafe(24)
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            query = {
                "client_id": client[0],
                "redirect_uri": redirect_uri(settings),
                "response_type": "code",
                "scope": " ".join(grantable(client[2], permissions(c))),
                "access_type": "offline",
                "prompt": "consent",
                "login_hint": mailbox,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
            now = H.now()
            held = c.execute("SELECT * FROM google_connects WHERE person_id=?", (pid,)).fetchone()
            if held and held["grant_sealed"] and str(held["mailbox"]).lower() != mailbox.lower():
                # Their mailbox is another one now: the grant for the old one ends here, so it never serves the new.
                try:
                    ended["refresh"] = google_tokens.unseal(c, dict(held))
                except Problem:
                    pass  # a grant that cannot be opened cannot be revoked; it is forgotten all the same
                c.execute(
                    "UPDATE google_connects SET can=NULL,client_id=NULL,grant_scopes=NULL,grant_sealed=NULL,"
                    "grant_operation_id=NULL WHERE person_id=?",
                    (pid,),
                )
            # A grant connected before for this mailbox stays in use until this sign-in's own grant replaces it.
            c.execute(
                "INSERT INTO google_connects(person_id,mailbox,status,oauth_state,verifier,operation_id,started_at,"
                "created_at,updated_at) VALUES(?,?,'signing_in',?,?,?,?,?,?) "
                "ON CONFLICT(person_id) DO UPDATE SET mailbox=excluded.mailbox,status='signing_in',"
                "oauth_state=excluded.oauth_state,verifier=excluded.verifier,kind=NULL,message=NULL,hint=NULL,"
                "operation_id=excluded.operation_id,started_at=excluded.started_at,updated_at=excluded.updated_at",
                (pid, mailbox, state, verifier, new_operation(), time.time(), now, now),
            )
            H.event(c, who.actor, "mail.google.connect.start", mailbox, {})
            return {"auth_url": AUTH_URI + "?" + urllib.parse.urlencode(query), "mailbox": mailbox}

        answer = mutate(request, body, work)
        if ended.get("refresh"):
            revoke_at_google(ended.pop("refresh"))  # after the write, outside the lock
        return answer

    @app.get(CALLBACK, include_in_schema=False)
    def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        """Google sends the browser back here. The state must be one this same person started, still open; its code is
        traded for the grant at once, outside the write lock."""
        who = request.state.identity
        with store.transaction() as c:
            expire(c)
            row = (
                c.execute(
                    "SELECT * FROM google_connects WHERE oauth_state=? AND status='signing_in'", (state,)
                ).fetchone()
                if state
                else None
            )
            if (
                not row
                or who.role not in ("owner", "human")
                or row["person_id"] != H.actor_id(who.actor)
                or not secrets.compare_digest(row["oauth_state"], state)
            ):
                return RedirectResponse(INBOX, status_code=302)
            now = H.now()
            if error or not code:
                kind = CALLBACK_ERRORS.get(error, "declined" if error else "error")
                c.execute(
                    "UPDATE google_connects SET status='failed',kind=?,oauth_state=NULL,verifier=NULL,"
                    "updated_at=? WHERE person_id=?",
                    (kind, now, row["person_id"]),
                )
                H.event(c, who.actor, "mail.google.connect.failed", row["mailbox"], {"kind": kind})
                return RedirectResponse(INBOX, status_code=302)
            # The state and the verifier are spent here, before Google is asked: a code works once.
            client = oauth_client(c, settings)
            c.execute(
                "UPDATE google_connects SET status='finishing',oauth_state=NULL,verifier=NULL,started_at=?,"
                "updated_at=? WHERE person_id=?",
                (time.time(), now, row["person_id"]),
            )
            row = dict(row)
        finish(row, client, code[:2000], row["verifier"])
        return RedirectResponse(INBOX, status_code=302)

    @app.post("/api/v2/mail/google/disconnect")
    def google_disconnect(request: Request, body: M.Empty):
        """Forget the person's grant now and revoke it at Google. Google is asked after the write, outside the lock;
        the refresh token is held only in this request meanwhile, never in the idempotency store."""
        who = request.state.identity
        held = {}

        def work(c):
            pid = require_self(who)
            mailbox, _ = mailbox_for(c, pid)
            row = c.execute("SELECT * FROM google_connects WHERE person_id=?", (pid,)).fetchone()
            if row and row["grant_sealed"]:
                try:
                    held["refresh"] = google_tokens.unseal(c, dict(row))
                except Problem:
                    pass  # a grant that cannot be opened cannot be revoked; it is forgotten all the same
            operation = new_operation()
            held.update(person=pid, operation=operation)
            now = H.now()
            c.execute(
                "INSERT INTO google_connects(person_id,mailbox,status,operation_id,started_at,created_at,updated_at) "
                "VALUES(?,?,'disconnected',?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET "
                "mailbox=excluded.mailbox,status='disconnected',oauth_state=NULL,verifier=NULL,kind=NULL,"
                "message=NULL,hint=NULL,can=NULL,client_id=NULL,grant_scopes=NULL,grant_sealed=NULL,"
                "grant_operation_id=NULL,operation_id=excluded.operation_id,started_at=excluded.started_at,"
                "updated_at=excluded.updated_at",
                (pid, mailbox, operation, time.time(), now, now),
            )
            H.event(c, who.actor, "mail.google.disconnect", mailbox, {})
            return {"ok": True}

        mutate(request, body, work)
        refresh = held.pop("refresh", None)
        if refresh and not revoke_at_google(refresh):
            with store.transaction() as c:
                c.execute(
                    "UPDATE google_connects SET kind='not_revoked',updated_at=? "
                    "WHERE person_id=? AND operation_id=? AND status='disconnected'",
                    (H.now(), held["person"], held["operation"]),
                )
        with store.read() as c:
            return person_view(c, who)
