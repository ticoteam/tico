"""Settings > Google, the Workspace connection: one service-account key for the whole Workspace, kept on this server.

An admin makes the key once (scripts/google-setup.sh, or a key file uploaded in Settings > Google). The key is sealed
(vault.cipher, context `google-workspace:<id>`) and never leaves the server. A computer asks for a token for one
mailbox and one service; the server signs a JWT with the key, impersonating that mailbox, and answers with the access
token only (`mint`). The key covers the message bots' mailboxes on the Workspace's domain (`covers`).

Who may use a mailbox is `authority`: `personal` when the person's own Connect Google grant is live (it always wins),
`workspace` when the active key covers it, otherwise `legacy`, which is what Tico did before. A computer's share of
the work (`owned_mailboxes`) follows the same split: a workspace mailbox belongs to the computer that hosts its inbox
bot, a personal one to the computer that holds its grant.

Nothing here logs a token or a key. Google's refusals reach a caller as a kind, never as Google's text.
"""

import collections
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import jwt

from . import routines
from .store import Problem

WORKSPACE_SCOPES = {"gmail": ["https://www.googleapis.com/auth/gmail.modify"],
                    "calendar": ["https://www.googleapis.com/auth/calendar"]}
TOKEN_URI = "https://oauth2.googleapis.com/token"
DELEGATION_URL = "https://admin.google.com/ac/owl/domainwidedelegation"
OAUTH_CONTEXT = "google-oauth-client"
GOOGLE_SECONDS = 20
JWT_SECONDS = 3000              # Google takes a JWT for at most an hour
CACHE_MARGIN = 300              # a cached token is used until five minutes before it expires
CACHE_SIZE = 512
FAILING_SECONDS = 900           # token mints refused this long turn a connected key failing
RECHECK_SECONDS = 900           # a pending or failing key is checked again this often ...
RECHECK_WINDOW = 86400          # ... for this long
AUDIT_SECONDS = 3600            # one mint audit row per (mailbox, service, computer) per hour
ACTIVE = ("connected", "failing")
PROBES = (("gmail", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
          ("calendar", "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1"))
# What Google's token endpoint may answer, as the kind the caller sees (a refusal names no more than this).
TOKEN_KINDS = {"unauthorized_client": "unauthorized_client"}
DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
KEY_FIELDS = ("client_email", "client_id", "private_key_id", "private_key")

_VAULT = None
_LOCK = threading.Lock()
_CACHE = collections.OrderedDict()   # (row id, revision, mailbox, service) -> (answer, expiry in epoch seconds)
_AUDITED = {}                         # (mailbox, service, computer) -> epoch seconds of its last audit row


def use_vault(vault):
    """The server's vault (credentials.Vault), which seals the key and the OAuth secret. Set when the routes install."""
    global _VAULT
    _VAULT = vault


def _cipher():
    if _VAULT is None:
        raise Problem("vault_unavailable", "The server's secret store is not ready yet", 503)
    return _VAULT.cipher


def scope_urls():
    """Every scope the Workspace grants, sorted: the link's clientScopeToAdd and the copy text, byte for byte."""
    return sorted({scope for scopes in WORKSPACE_SCOPES.values() for scope in scopes})


def delegation_scopes():
    return ",".join(scope_urls())


def delegation_url(client_id):
    return DELEGATION_URL + "?" + urllib.parse.urlencode({"clientIdToAdd": client_id,
                                                           "clientScopeToAdd": delegation_scopes(),
                                                           "overwriteClientId": "true"})


def clean_domain(value):
    domain = str(value or "").strip().lower()
    if not DOMAIN.fullmatch(domain):
        raise Problem("domain", "Name the Workspace's domain, like acme.example", 422)
    return domain


def _domain(mailbox):
    return str(mailbox or "").strip().lower().rpartition("@")[2]


def _address(mailbox):
    box = str(mailbox or "").strip().lower()
    if "@" not in box:
        raise Problem("mailbox", "Name a mailbox address", 422)
    return box


def _context(row_id):
    return "google-workspace:" + str(row_id)


def active(c):
    """The Workspace's active key row (connected or failing), or None. Its one such row is the install's."""
    found = c.execute("SELECT * FROM google_workspaces WHERE status IN ('connected','failing') "
                      "ORDER BY connected_at DESC LIMIT 1").fetchone()
    return dict(found) if found else None


def pending(c):
    """The one pending key (a first key, or a replacement while the active one keeps working), or None."""
    found = c.execute("SELECT * FROM google_workspaces WHERE status='pending' ORDER BY created_at DESC LIMIT 1").fetchone()
    return dict(found) if found else None


def connected(c):
    row = active(c)
    return bool(row) and row["status"] == "connected"


def covers(c, mailbox):
    """Whether the active key's Workspace owns this mailbox's domain (a connected or failing key)."""
    row = active(c)
    return bool(row) and _domain(mailbox) == row["domain"]


def _live(c, box):
    return c.execute("SELECT 1 FROM google_connects WHERE lower(mailbox)=? AND holder IS NOT NULL AND "
                     "grant_hash IS NOT NULL AND status<>'disconnected' LIMIT 1", (box,)).fetchone() is not None


def _live_mailboxes(c):
    return {row[0] for row in c.execute("SELECT lower(mailbox) FROM google_connects WHERE holder IS NOT NULL AND "
                                        "grant_hash IS NOT NULL AND status<>'disconnected'")}


def _decide(box, row, live):
    if box in live:
        return "personal"
    if row and row["status"] in ACTIVE and _domain(box) == row["domain"]:
        return "workspace"
    return "legacy"


def authority(c, mailbox):
    """Which credential owns this mailbox: 'personal' (a live Connect Google grant, read only or refused included),
    'workspace' (the active key covers its domain, connected or failing), or 'legacy' (today's rules)."""
    box = str(mailbox or "").strip().lower()
    return _decide(box, active(c), {box} if _live(c, box) else set())


def owned_mailboxes(c, runner_id):
    """The mailboxes this computer may publish, claim, read the calendar of and report on, and nothing else:
    the workspace mailboxes whose inbox bot runs here, and the personal mailboxes whose live grant it holds."""
    row = active(c)
    live = _live_mailboxes(c)
    owned = {r[0] for r in c.execute("SELECT lower(mailbox) FROM google_connects WHERE holder=? AND "
                                     "grant_hash IS NOT NULL AND status<>'disconnected'", (runner_id,))}
    if row:
        hosted = {r[0] for r in c.execute("SELECT bot FROM assignments WHERE runner_id=?", (runner_id,))}
        for box in routines.message_bot_mailboxes(c):
            address = box["address"]
            if box["bot"] in hosted and _decide(address, row, live) == "workspace":
                owned.add(address)
    return owned


def _fail(status, body):
    """The kind of a token refusal: Google's own words when they name one, else 'other'. Never Google's text."""
    if not status:
        return "other"
    error = str(body.get("error") or "")
    description = str(body.get("error_description") or "").lower()
    if error == "invalid_grant" and ("user" in description or "email" in description):
        return "not_found"
    return TOKEN_KINDS.get(error, "other")


def google_call(url, *, data=None, token=None):
    """(HTTP status, JSON object) from Google: a form POST when `data` is given, else a GET with the bearer `token`.
    A refusal comes back as its status; an unreachable Google as status 0. Tests replace this function."""
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=GOOGLE_SECONDS) as answer:
            status, raw = answer.status, answer.read()
    except urllib.error.HTTPError as refused:
        try:
            status, raw = refused.code, refused.read()
        except OSError:
            status, raw = refused.code, b""
    except (OSError, ValueError):
        return 0, {}
    try:
        parsed = json.loads(raw.decode() or "{}")
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _exchange(c, row, box, service, now):
    """Google's access token for `box` and one service, from a JWT signed with the row's key. Returns (answer, epoch)."""
    creds = json.loads(_cipher().unseal(c, _context(row["id"]), row["key_sealed"]))
    assertion = jwt.encode({"iss": creds["client_email"], "sub": box, "scope": " ".join(WORKSPACE_SCOPES[service]),
                            "aud": TOKEN_URI, "iat": int(now), "exp": int(now) + JWT_SECONDS},
                           creds["private_key"], algorithm="RS256")
    status, body = google_call(TOKEN_URI, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                                "assertion": assertion})
    if status != 200 or not body.get("access_token"):
        raise Problem("not_connected", "Google did not give a token for " + box, 403,
                      extra={"kind": _fail(status, body)})
    lifetime = int(body.get("expires_in") or 3600)
    expires = now + lifetime
    answer = {"access_token": body["access_token"],
              "expiry": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)),
              "scopes": list(WORKSPACE_SCOPES[service])}
    return answer, expires


def mint(c, mailbox, service, row=None):
    """An access token for `mailbox` and one service ('gmail' or 'calendar'), as {access_token, expiry, scopes}.

    `row` is the key row to mint with; None means the active key. A refusal raises Problem 'not_connected' (403)
    with the kind in extra. Callers decide who may ask first (`authority`); this only signs. The answers are cached
    per key revision until five minutes before they expire, so a check that starts on a revision never reads
    another revision's token. Reads only: the caller records the outcome in a write transaction (`record_mint`)."""
    if service not in WORKSPACE_SCOPES:
        raise Problem("scope", "A token is for gmail or calendar", 422)
    box = _address(mailbox)
    current = dict(row) if row else active(c)
    if not current:
        raise Problem("not_connected", "The Google Workspace connection is not set up", 403,
                      extra={"kind": "not_connected"})
    key = (current["id"], current["revision"], box, service)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and hit[1] > now + CACHE_MARGIN:
        return dict(hit[0])
    answer, expires = _exchange(c, current, box, service, now)
    with _LOCK:
        _CACHE[key] = (answer, expires)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return dict(answer)


def forget_tokens():
    """Drop every cached token: the key was replaced or removed, or the Workspace was disconnected."""
    with _LOCK:
        _CACHE.clear()


def oauth_client(c, settings):
    """Tico's Google OAuth client for Connect Google: (client_id, client_secret, 'env' | 'settings'), or None.
    The server's environment wins; otherwise the client an admin pasted into Settings > Google."""
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        return settings.google_oauth_client_id, settings.google_oauth_client_secret, "env"
    found = c.execute("SELECT client_id, secret_sealed FROM google_oauth_clients WHERE singleton=1").fetchone()
    if not found:
        return None
    secret = _cipher().unseal(c, OAUTH_CONTEXT, found["secret_sealed"])
    return found["client_id"], secret, "settings"


def validate_key(key):
    """The fields this server needs from an uploaded service-account key, or a 422 that names only the field.
    The private key is proved by signing a JWT with it, so a key that cannot sign never becomes pending."""
    if not isinstance(key, dict):
        raise Problem("key", "The key is not a JSON object", 422)
    if key.get("type") != "service_account":
        raise Problem("key", "The key is not a service account key", 422)
    for field in KEY_FIELDS:
        if not isinstance(key.get(field), str) or not key[field].strip():
            raise Problem("key", "The key has no " + field, 422)
    if "@" not in key["client_email"]:
        raise Problem("key", "The key's client_email is not an address", 422)
    if key.get("token_uri") != TOKEN_URI:
        raise Problem("key", "The key's token_uri is not Google's token endpoint", 422)
    try:
        jwt.encode({"iss": key["client_email"], "aud": TOKEN_URI}, key["private_key"], algorithm="RS256")
    except Exception:
        raise Problem("key", "The key's private_key is not a usable RSA key", 422) from None
    return {field: key[field].strip() for field in KEY_FIELDS}
