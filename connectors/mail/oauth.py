"""One person's own Google account, connected with OAuth: no service account, no domain-wide delegation.

A person signs in to Google and grants this computer access to their own mailbox and calendar. Google
issues a refresh token that can act only as that person; it cannot reach anyone else in the Workspace.
The grant lives next to the service-account key (`google-oauth.json` beside `google-sa.json`, mode
0600), so on a Linux runner it sits in the supervisor's state directory where bots cannot read it, and a
bot's turn still gets only a short-lived access token for one of the mailboxes the hub named for it
(runner/credential_socket.py).

The file holds the OAuth client (a "Desktop app" client from the owner's Google Cloud project) and one
grant per mailbox, keyed by the address Google confirmed at sign-in. A mailbox with a grant is always
served from it: a revoked or expired grant is an error that says how to reconnect, never a quiet switch
to the service account.

Reading starts with read-only access. Each write capability adds only its own Google scope, chosen at
connect time. Nothing here prints or returns a refresh token, an access token or the client secret.
Only the standard library is used, so the CLI can connect before the Google client libraries exist.
"""

import base64, hashlib, json, os, secrets, stat, tempfile, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import Failure, Refused

GRANTS_ENV = "GOOGLE_OAUTH_GRANTS"
FILE = "google-oauth.json"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
TOKENINFO_URI = "https://oauth2.googleapis.com/tokeninfo"
REDIRECT_URI = "http://127.0.0.1:8765/"

G = "https://www.googleapis.com/auth/"
IDENTITY_SCOPES = ["openid", "email"]
READ_SCOPES = [G + "gmail.readonly", G + "calendar.events.readonly", G + "calendar.freebusy",
               G + "calendar.calendarlist.readonly"]
# Each write capability adds only the scope that operation needs. gmail.modify and gmail.compose both let
# Google send mail; Tico's send gate (policy, lint, review, approval) still decides whether anything is sent.
CAPABILITIES = {
    "labels": [G + "gmail.modify"],
    "drafts": [G + "gmail.compose"],
    "calendar-write": [G + "calendar.events"],
}
# The scopes a token for one service may carry, broadest first; a token is minted with the granted subset.
SERVICE_SCOPES = {
    "gmail": [G + "gmail.modify", G + "gmail.compose", G + "gmail.readonly"],
    "calendar": [G + "calendar.events", G + "calendar.events.readonly", G + "calendar.freebusy",
                 G + "calendar.calendarlist.readonly"],
}
WRITE_SCOPES = {"gmail": {G + "gmail.modify", G + "gmail.compose"}, "calendar": {G + "calendar.events"}}
# The granted scopes that let each capability's operations through (gmail.modify also covers drafts).
NEEDS = {"labels": {G + "gmail.modify"}, "drafts": {G + "gmail.modify", G + "gmail.compose"},
         "calendar-write": {G + "calendar.events"}}


def grants_path(key_path=None):
    """GOOGLE_OAUTH_GRANTS, else google-oauth.json beside the service-account key (protected on a runner)."""
    named = os.environ.get(GRANTS_ENV)
    if named:
        return Path(named).expanduser()
    if key_path is None:
        from .auth import key_path as default_key        # noqa: PLC0415
        key_path = default_key()
    return Path(key_path).expanduser().with_name(FILE)


def connect_command(mailbox):
    return (f"scripts/mail.sh google connect --mailbox {mailbox} (on a Docker runner: docker exec -it -u ticorun "
            f"'<container-name>' scripts/mail.sh google connect --mailbox {mailbox}); docs/mail.md, Connect one Google account")


def load(path=None):
    """The grants file as a dict, or {} when there is none. Refuses a file others can read."""
    p = Path(path) if path else grants_path()
    try:
        info = p.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(info.st_mode):
        raise Failure(f"{p} is not a regular file", f"Remove it and connect again: {connect_command('<address>')}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise Failure(f"{p} is mode {stat.S_IMODE(info.st_mode):o}; Google grants must be private", f"chmod 600 {p}")
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        raise Failure(f"{p} is not valid JSON: {e}", f"Remove it and connect again: {connect_command('<address>')}")
    return data if isinstance(data, dict) else {}


def save(data, path=None):
    """Write the grants file atomically, mode 0600, in a 0700 directory created if missing."""
    p = Path(path) if path else grants_path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".google-oauth-", dir=p.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, p)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def grant(mailbox, path=None):
    """The stored grant for `mailbox` (lowercased), or None. A refresh token works only with the client that
    obtained it, so each grant keeps its own client id and secret."""
    data = load(path)
    found = (data.get("grants") or {}).get(str(mailbox or "").strip().lower())
    if not isinstance(found, dict) or not found.get("refresh_token"):
        return None
    return dict(found, token_uri=found.get("token_uri") or TOKEN_URI)


def read_client(path):
    """The OAuth client from the JSON Google offers to download for a Desktop app (or a Web client)."""
    try:
        raw = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as e:
        raise Failure(f"could not read the OAuth client file {path}: {e}",
                      "Download it from Google Cloud console > APIs & Services > Credentials > your OAuth client.")
    if raw.get("type") == "service_account":
        raise Failure(f"{path} is a service-account key, not an OAuth client",
                      "Create an OAuth client id of type Desktop app; this flow never uses a service account.")
    body = raw.get("installed") or raw.get("web") or raw
    if not body.get("client_id") or not body.get("client_secret"):
        raise Failure(f"{path} has no client_id and client_secret",
                      "Download the OAuth client JSON (Desktop app) from Google Cloud console > Credentials.")
    return {"client_id": body["client_id"], "client_secret": body["client_secret"],
            "token_uri": body.get("token_uri") or TOKEN_URI}


def scopes_for(capabilities):
    unknown = sorted(set(capabilities) - set(CAPABILITIES))
    if unknown:
        raise Failure(f"unknown capability {', '.join(unknown)}", f"Choose from {', '.join(sorted(CAPABILITIES))}.")
    wanted = IDENTITY_SCOPES + READ_SCOPES + [s for c in sorted(set(capabilities)) for s in CAPABILITIES[c]]
    return list(dict.fromkeys(wanted))


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorization(client, mailbox, capabilities, redirect_uri=REDIRECT_URI):
    """The sign-in URL for `mailbox`, plus the state and PKCE verifier the answer is checked against."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce()
    query = {"client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
             "scope": " ".join(scopes_for(capabilities)), "access_type": "offline", "prompt": "consent",
             "login_hint": mailbox, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    return {"url": AUTH_URI + "?" + urllib.parse.urlencode(query), "state": state, "verifier": verifier,
            "redirect_uri": redirect_uri}


def code_from(answer, state):
    """The authorization code from what the person pasted: the whole address Google sent them to."""
    text = str(answer or "").strip()
    if not text:
        raise Failure("nothing was pasted", "Paste the full address from the browser after approving.")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query)
    if query.get("error"):
        raise Failure(f"Google did not grant access: {query['error'][0]}",
                      "Approve the access on Google's page, or ask the Workspace admin to allow this app "
                      "(Admin console > Security > API controls > App access control).")
    if query.get("state", [""])[0] != state:
        raise Failure("that address is not the answer to this sign-in (state does not match)",
                      "Start again and paste the address from the browser tab this sign-in opened.")
    code = query.get("code", [""])[0]
    if not code:
        raise Failure("that address has no authorization code", "Paste the full address, starting with http://127.0.0.1.")
    return code


def _post(url, fields, opener=None):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _json((opener or urllib.request.urlopen)(request, timeout=30))


def _get(url, opener=None):
    return _json((opener or urllib.request.urlopen)(urllib.request.Request(url), timeout=30))


def _json(response):
    with response:
        return json.loads(response.read().decode() or "{}")


def _google_error(exc):
    try:
        body = json.loads(exc.read().decode() or "{}")
    except Exception:
        body = {}
    return str(body.get("error") or getattr(exc, "code", "") or type(exc).__name__)


def exchange(client, code, verifier, redirect_uri=REDIRECT_URI, opener=None):
    """Trade the code for tokens. Returns Google's answer; the caller keeps only the refresh token."""
    try:
        return _post(client.get("token_uri") or TOKEN_URI,
                     {"code": code, "client_id": client["client_id"], "client_secret": client["client_secret"],
                      "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier},
                     opener)
    except urllib.error.HTTPError as e:
        raise Failure(f"Google refused the authorization code: {_google_error(e)}",
                      "Codes work once and expire in minutes: start the sign-in again.")
    except (OSError, ValueError) as e:
        raise Failure(f"could not reach Google to finish the sign-in: {type(e).__name__}", "Check this computer's network.")


def confirmed_identity(access_token, opener=None):
    """(email, granted scopes) for the token, as Google reports them."""
    try:
        info = _get(TOKENINFO_URI + "?" + urllib.parse.urlencode({"access_token": access_token}), opener)
    except (OSError, ValueError) as e:
        raise Failure(f"could not confirm which Google account signed in: {type(e).__name__}", "Try the sign-in again.")
    verified = str(info.get("email_verified", "")).lower() == "true"
    email = str(info.get("email") or "").strip().lower()
    if not email or not verified:
        raise Failure("Google did not confirm the signed-in account's address", "Try the sign-in again.")
    return email, sorted(str(info.get("scope") or "").split())


def connect(mailbox, client, code, verifier, capabilities=(), path=None, redirect_uri=REDIRECT_URI, opener=None,
            now=None):
    """Finish a sign-in: exchange the code, require that Google confirms `mailbox` itself signed in, store the grant.

    Refuses another account's grant (a person cannot connect someone else's mailbox, and a grant never
    reaches beyond the account that made it). Returns the public description of the stored grant."""
    mailbox = str(mailbox or "").strip().lower()
    tokens = exchange(client, code, verifier, redirect_uri, opener)
    refresh, access = tokens.get("refresh_token"), tokens.get("access_token")
    if not access:
        raise Failure("Google returned no access token", "Start the sign-in again.")
    email, granted = confirmed_identity(access, opener)
    if email != mailbox:
        _revoke_token(refresh or access, opener)
        raise Failure(f"the account that signed in is {email}, not {mailbox}; nothing was stored",
                      f"Sign in to Google as {mailbox} (use a private window if the browser picks another account).")
    if not refresh:
        raise Failure("Google returned no refresh token, so access would end within the hour",
                      "Remove this app at myaccount.google.com/permissions, then connect again.")
    if not set(granted) & set(SERVICE_SCOPES["gmail"]):
        _revoke_token(refresh, opener)
        raise Failure("the grant has no Gmail access (the Gmail box was unticked on Google's page); nothing was stored",
                      "Connect again and leave the read your email box ticked.")
    data = load(path)
    data["client"] = public = {"client_id": client["client_id"], "client_secret": client["client_secret"],
                               "token_uri": client.get("token_uri") or TOKEN_URI}
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    data.setdefault("grants", {})[mailbox] = dict(public, refresh_token=refresh, scopes=granted, email=email,
                                                  capabilities=sorted(set(capabilities)), connected_at=stamp)
    save(data, path)
    return describe(mailbox, data["grants"][mailbox])


def describe(mailbox, found):
    """What a person may see about a grant: never a token."""
    scopes = set(found.get("scopes") or [])
    can = ["read mail"] if scopes & set(SERVICE_SCOPES["gmail"]) else []
    if G + "gmail.modify" in scopes:
        can.append("label and archive mail")
    if scopes & WRITE_SCOPES["gmail"]:
        can.append("write drafts")
    if scopes & set(SERVICE_SCOPES["calendar"]):
        can.append("read calendar")
    if G + "calendar.events" in scopes:
        can.append("add calendar events")
    return {"mailbox": mailbox, "source": "google account (oauth)", "can": can,
            "scopes": sorted(s.replace(G, "") for s in scopes), "connected_at": found.get("connected_at", "")}


def status(path=None):
    data = load(path)
    return [describe(m, g) for m, g in sorted((data.get("grants") or {}).items()) if isinstance(g, dict)]


def _revoke_token(token, opener=None):
    if not token:
        return False
    try:
        _post(REVOKE_URI, {"token": token}, opener)
        return True
    except (OSError, ValueError):
        return False


def revoke(mailbox, path=None, opener=None):
    """Revoke the grant at Google and forget it here. The local copy is removed even when Google cannot be
    reached, so this computer stops using it either way. Returns whether Google confirmed the revocation."""
    mailbox = str(mailbox or "").strip().lower()
    data = load(path)
    found = (data.get("grants") or {}).pop(mailbox, None)
    if not found:
        raise Failure(f"{mailbox} has no Google account connected on this computer", "Nothing to revoke.")
    save(data, path)
    return _revoke_token(found.get("refresh_token"), opener)


def service_scopes(found, service, mailbox):
    """The granted scopes a token for `service` carries. Raises when the grant has none for it."""
    granted = set(found.get("scopes") or [])
    chosen = [s for s in SERVICE_SCOPES[service] if s in granted]
    if not chosen:
        what = "Gmail" if service == "gmail" else "Google Calendar"
        raise Failure(f"{mailbox}'s connected Google account did not grant {what} access",
                      f"Connect again and leave the {what} boxes ticked: {connect_command(mailbox)}")
    return chosen


def require(granted, capability, mailbox, what):
    """Refuse a write the connected account did not grant, before anything is sent to Google.

    `granted` is None for the service account (its scopes are the Workspace admin's delegation) and the
    list of granted scopes for a connected account. Tico's own verbs (a bot's `read` covers labelling and
    archiving) never widen what Google was asked for; this never asks Google for more either."""
    if granted is None or set(granted) & NEEDS[capability]:
        return
    raise Refused(f"{mailbox} is connected to Tico read-only at Google, so Tico will not {what}; nothing was sent "
                  "to Google",
                  f"Reading still works. Only if {mailbox} wants Tico to {what}, they connect again adding "
                  f"--allow {capability}: {connect_command(mailbox)}")


def refresh_failed_hint(mailbox):
    return ("The grant was revoked at Google (myaccount.google.com/permissions), expired, or the Workspace admin "
            f"blocked the app. Reconnect: {connect_command(mailbox)}. Tico does not fall back to a service account.")
