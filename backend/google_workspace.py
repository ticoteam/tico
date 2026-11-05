"""Settings > Inboxes, the Workspace connection: one service-account key for the whole Workspace, kept on this server.

An admin makes the key once (scripts/google-setup.sh, or a key file uploaded in Settings > Inboxes). The key is sealed
(vault.cipher, context `google-workspace:<id>`) and never leaves the server. A computer asks for a token for one
mailbox and one service; the server signs a JWT with the key, impersonating that mailbox, and answers with the access
token only (`mint`). The key covers mailboxes on the Workspace's domain (`covers`).

Who may use a mailbox is `authority`: `personal` when the person's own Connect Google grant is live (it always wins),
`workspace` when the active key covers it, otherwise `legacy`, which is what Tico did before. A computer's share of
the work (`owned_mailboxes`) follows the same split: a workspace mailbox follows its inbox bot, or gets one eligible
online sync computer when it has no host; a personal one belongs to the computer that takes its grant.

Nothing here logs a token or a key. Google's refusals reach a caller as a kind, never as Google's text.
"""

import asyncio
import collections
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from fastapi.responses import Response
from pydantic import Field

from . import models as M
from . import routines
from .store import H, Problem

WORKSPACE_SCOPES = {"gmail": ["https://www.googleapis.com/auth/gmail.modify"],
                    "calendar": ["https://www.googleapis.com/auth/calendar"]}
# A token minted while What Tico may do turns a service's writes off. Domain-wide delegation takes only scopes the admin
# authorized by name (gmail.modify does not cover gmail.readonly there), so the authorized list carries these too.
READ_ONLY_SCOPES = {"gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
                    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"]}
# What Tico may do in Google beyond reading (Settings > Inboxes), one JSON row in registry_metadata. Each is on until an
# admin turns it off. Organize mail and Send mail both need gmail.modify (Google has no narrower scope Tico uses), so
# Send mail is also held in Tico: off, no bot sends (backend/mail_settings.py, connectors/mail/policy.py).
PERMISSIONS_KEY = "google_permissions"
PERMISSIONS = ("organize_mail", "send_mail", "change_calendar")
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
_CACHE = collections.OrderedDict()   # (row id, revision, mailbox, service, scopes) -> (answer, expiry in epoch seconds)
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
    return sorted({scope for table in (WORKSPACE_SCOPES, READ_ONLY_SCOPES) for scopes in table.values()
                   for scope in scopes})


def permissions(c):
    """What the company lets Tico do in Google beyond reading: {organize_mail, send_mail, change_calendar}."""
    from .read_cache import metadata
    row = metadata(c, PERMISSIONS_KEY)
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    return {name: stored.get(name) is not False for name in PERMISSIONS}


def acts(may=None):
    """Per service, whether its write scope may be used: gmail.modify for Organize or Send, calendar events for
    Change calendar. `may` None is everything (the app's or the key's whole grant)."""
    may = may or dict.fromkeys(PERMISSIONS, True)
    return {"gmail": bool(may["organize_mail"] or may["send_mail"]), "calendar": bool(may["change_calendar"])}


def workspace_scopes(service, may=None):
    """The scopes a Workspace token for `service` is minted with: the write scope, or read only when it is off."""
    return list(WORKSPACE_SCOPES[service] if acts(may)[service] else READ_ONLY_SCOPES[service])


# The scopes a key from before the read-only ones were authorized for (migration 0081): its row's list is null.
LEGACY_SCOPES = sorted(scope for scopes in WORKSPACE_SCOPES.values() for scope in scopes)


def authorized(row):
    """The scopes `row`'s key was authorized for in Google Admin, as its last check proved them or as its upload
    stored them; the first two for a row from before that was kept."""
    try:
        stored = json.loads((row or {}).get("authorized_scopes") or "null")
    except ValueError:
        stored = None
    return sorted(stored) if isinstance(stored, list) and stored else list(LEGACY_SCOPES)


def minting_scopes(service, may, row):
    """What a token for `service` is minted with on `row`: What Tico may do narrows it only to scopes the key was
    authorized for, since Google refuses any other (unauthorized_client). A key authorized for the write scope only
    keeps minting it; the mail connector holds What Tico may do then (connectors/mail/oauth.py company_allows)."""
    wanted, held = workspace_scopes(service, may), set(authorized(row))
    if set(wanted) <= held or not set(WORKSPACE_SCOPES[service]) <= held:
        return wanted
    return list(WORKSPACE_SCOPES[service])


def narrowable(row):
    """Whether `row`'s key was authorized for every scope Tico shows, so each box of What Tico may do narrows it."""
    return set(scope_urls()) <= set(authorized(row))


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


def workspace_mailboxes(c):
    """Known active Workspace mailboxes that need a sync even without a message bot. Awaiting people stay private."""
    from . import mailbox_access
    row, live = active(c), _live_mailboxes(c)
    if not row:
        return []
    people = {p["id"] for p in routines._roster(c).get("people", []) if not p.get("hidden")}
    return [dict(box) for box in c.execute(
        "SELECT address,person_id,kind FROM mailboxes WHERE status='active' AND provider=? "
        "AND (kind='shared' OR person_id IS NOT NULL) ORDER BY address", (mailbox_access.PROVIDER,))
        if (box["kind"] == "shared" or box["person_id"] in people)
        and _decide(box["address"], row, live) == "workspace"]


def workspace_sync_owners(c):
    """One sync computer per Workspace mailbox. Placement wins; fallback uses an isolated company server or the
    person's own computer, never an unrelated person's private computer. Shared mailboxes fall back to servers only."""
    from . import access, google_rights
    from .store import readiness_document
    row, live = active(c), _live_mailboxes(c)
    if not row:
        return {}
    runners = google_rights.live_runners(c)
    placed = {r["bot"]: r["runner_id"] for r in c.execute("SELECT bot,runner_id FROM assignments")
              if r["runner_id"] in runners}
    hosts = {}
    for box in routines.message_bot_mailboxes(c):
        address = box["address"]
        if box["bot"] in placed and _decide(address, row, live) == "workspace":
            hosts.setdefault(address, set()).add(placed[box["bot"]])
    # A message-bot host stays authoritative while offline, just as personal grants do. Without that placement,
    # only computers currently answering can take the mail. Isolated runners supervise the connectors side job.
    online = {r["id"]: r for r in c.execute(
        "SELECT id,owner_id,readiness,accepts_member_bots FROM runners_with_state "
        "WHERE revoked_at IS NULL AND last_seen_at>?",
        (H.shift(H.now(), seconds=-google_rights.ONLINE_SECONDS),))}
    assistant = getattr(getattr(c, "settings", None), "assistant_bot", None)
    assistant_bot = H.bot(c, assistant) if assistant else None
    assistant_host = placed.get(assistant) if assistant_bot and assistant_bot["state"] != "archived" else None
    emails = set(access.owner_and_admin_emails(c))
    admins = {p["id"] for p in routines._roster(c).get("people", [])
              if not p.get("hidden") and p.get("email", "").lower() in emails}
    # Another member's computer never holds someone's mail, even a server that takes members' bots.
    server = min((rid for rid, r in online.items()
                  if readiness_document(r["readiness"]).get("isolated") is True and r["owner_id"] in admins),
                 default=None)
    personal_hosts = {}
    for rid in sorted(online):
        personal_hosts.setdefault(online[rid]["owner_id"], rid)
    for box in workspace_mailboxes(c):
        if box["address"] not in hosts:
            person = box["person_id"] if box["kind"] != "shared" else None
            helper = assistant_host if assistant_host in online and online[assistant_host]["owner_id"] in admins | {person} else None
            owner = (helper if person else None) or server or personal_hosts.get(person)
            if owner:
                hosts[box["address"]] = {owner}
    return {address: min(candidates) for address, candidates in hosts.items()}


def owned_mailboxes(c, runner_id):
    """The mailboxes this computer may publish, claim, read the calendar of and report on, and nothing else:
    Workspace mailboxes follow their message bot's non-revoked host, as before. Without a host, a known active mailbox
    follows the person's Assistant's online host, else an online isolated server owned by an owner or admin, else the person's own online computer. Shared mailboxes fall back to servers only. Among equals,
    the lowest runner id wins; with no eligible computer, no one syncs. Online means contact within
    google_rights.ONLINE_SECONDS; an offline or revoked fallback owner is replaced on the next check. This is sync
    authority only: it grants no bot access to the mailbox. Personal mailboxes follow the live grant this computer
    takes, through google_rights.takes: the live computer hosting the person's message bot, or, with no host (or while the
    grant is ending), its live holder. Revoked computers take nothing. When the bot moves, its new computer owns the
    grant but cannot refresh it until the person reconnects (the health says so), and the old holder keeps nothing."""
    from . import google_rights
    runners = google_rights.live_runners(c)
    if runner_id not in runners:
        return set()
    grants = c.execute("SELECT * FROM google_connects WHERE grant_hash IS NOT NULL AND status<>'disconnected'").fetchall()
    owned = set()
    if grants:
        hosts = google_rights.inbox_hosts(c)
        for grant in grants:
            if google_rights.takes(runner_id, False, grant, hosts, runners):
                owned.add(str(grant["mailbox"]).lower())
    owned.update(address for address, owner in workspace_sync_owners(c).items() if owner == runner_id)
    from . import mailbox_access
    return owned - mailbox_access.stopped(c)


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


def _exchange(c, row, box, scopes, now):
    """Google's access token for `box` and one service's `scopes`, from a JWT signed with the row's key. Returns
    (answer, epoch)."""
    creds = json.loads(_cipher().unseal(c, _context(row["id"]), row["key_sealed"]))
    assertion = jwt.encode({"iss": creds["client_email"], "sub": box, "scope": " ".join(scopes),
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
              "scopes": list(scopes)}
    return answer, expires


def mint(c, mailbox, service, row=None, scopes=None):
    """An access token for `mailbox` and one service ('gmail' or 'calendar'), as {access_token, expiry, scopes}.

    `row` is the key row to mint with; None means the active key. A refusal raises Problem 'not_connected' (403)
    with the kind in extra. Callers decide who may ask first (`authority`); this only signs. The answers are cached
    per key revision until five minutes before they expire, so a check that starts on a revision never reads
    another revision's token. Reads only: the caller records the outcome in a write transaction (`record_mint`).
    `scopes` names them outright (a check proving what the key was authorized for); None is `minting_scopes`."""
    if service not in WORKSPACE_SCOPES:
        raise Problem("scope", "A token is for gmail or calendar", 422)
    box = _address(mailbox)
    current = dict(row) if row else active(c)
    if not current:
        raise Problem("not_connected", "The Google Workspace connection is not set up", 403,
                      extra={"kind": "not_connected"})
    # Narrowed to What Tico may do. The scopes are in the cache key, so a box turned off takes effect on the next mint.
    scopes = list(scopes) if scopes else minting_scopes(service, permissions(c), current)
    key = (current["id"], current["revision"], box, service, tuple(scopes))
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and hit[1] > now + CACHE_MARGIN:
        return dict(hit[0])
    answer, expires = _exchange(c, current, box, scopes, now)
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
    The server's environment wins; otherwise the client an admin pasted into Settings > Inboxes."""
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
        signer = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
        if not isinstance(signer, rsa.RSAPrivateKey):      # a public key PEM loads, but cannot sign
            raise ValueError("not an RSA private key")
        jwt.encode({"iss": key["client_email"], "aud": TOKEN_URI}, signer, algorithm="RS256")
    except (TypeError, ValueError, UnsupportedAlgorithm, jwt.PyJWTError):
        raise Problem("key", "The key's private_key is not a usable RSA key", 422) from None
    fields = {field: key[field].strip() for field in KEY_FIELDS}
    fields["project_id"] = str(key.get("project_id") or "").strip()[:200]
    return fields


# ----------------------------------------------------------------------------- setup codes and the upload

CODE_SECONDS = 1800             # a setup command's code lasts thirty minutes
USED_CODE_SECONDS = 600         # key/keep still answers for a code used this recently
BAD_CODES = 20                  # this many wrong codes in BAD_WINDOW seconds, server-wide, ...
BAD_WINDOW = 60
LOCKOUT_SECONDS = 60            # ... answer 429 for this long
KEY_BYTES = 16 * 1024           # the largest body a setup upload or key/keep may send (enforced by app.py's guard)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "google-setup.sh"
GOOGLE_PATH = "/api/v2/google"
SETUP_PATH = "/api/v2/google/setup.sh"
CODE_PATH = "/api/v2/google/workspace/setup-code"
KEY_PATH = "/api/v2/google/workspace/key"
KEEP_PATH = "/api/v2/google/workspace/key/keep"
CHECK_PATH = "/api/v2/google/workspace/check"
WORKSPACE_PATH = "/api/v2/google/workspace"
OAUTH_PATH = "/api/v2/google/oauth-client"
PEOPLE_PATH = "/api/v2/google/people"
PERMISSIONS_PATH = "/api/v2/google/permissions"
PEOPLE_MAX = 500                # the team list's rows a page; `more` says when people were left out
PAGE_BYTES = 256 * 1024         # and its bytes a page, as JSON, so long names cannot make one page huge
CELL_MAX = 200                  # the characters of a row's name and of its mailbox; a longer person id is left out
# A page's JSON around its rows, with `next` at its longest (CELL_MAX characters, each escaped as \uXXXX at worst), so
# the whole answer stays inside PAGE_BYTES.
ENVELOPE = len(json.dumps({"people": [], "more": True, "next": "\u0000" * CELL_MAX}).encode())
KEY_KINDS = ("unauthorized_client", "other")   # a refusal about the key itself, not about one mailbox
_BAD = []                       # epoch seconds of recent wrong codes
_LOCKOUT = {"until": 0.0}


def code_hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def setup_code(request):
    """The setup code a request carries as `Authorization: Setup <code>`, or None for any other request."""
    header = request.headers.get("authorization", "")
    return header[len("Setup "):].strip() if header.startswith("Setup ") else None


def exempt(request):
    """The only requests that pass the sign-in guard without a session: the setup script, and the key upload and its
    keep check when they carry a setup code. Exact method and path, nothing broader (backend/app.py)."""
    path, method = request.url.path, request.method
    if path == SETUP_PATH:
        return method == "GET"
    if path in (KEY_PATH, KEEP_PATH):
        return method == "POST" and setup_code(request) is not None
    return False


def _check_locked(now):
    with _LOCK:
        locked = now < _LOCKOUT["until"]
    if locked:
        raise Problem("slow_down", "Too many wrong setup codes. Wait a minute, then run the setup command again.", 429,
                      retryable=True)


def _bad_code(now):
    """Count a wrong code (unknown, used or expired). In memory, so a rolled-back transaction cannot reset it."""
    with _LOCK:
        _BAD.append(now)
        while _BAD and now - _BAD[0] > BAD_WINDOW:
            _BAD.pop(0)
        if len(_BAD) >= BAD_CODES:
            _LOCKOUT["until"] = now + LOCKOUT_SECONDS
            _BAD.clear()


def _newest(c):
    """The newest generation issued, from the counter every setup command and every uploaded key takes its number from
    (`_issue`). Setup codes and uploads share one order, so an older code can never upload over a newer key."""
    return int(c.execute("SELECT issued FROM google_setup_generations WHERE singleton=1").fetchone()[0])


def _issue(c):
    """A new generation, for a setup command or an uploaded key, taken in the caller's transaction. The counter is kept
    apart from the rows it numbers: removing a key or a code does not give its number back, so an older command stays
    superseded after a disconnect."""
    c.execute("UPDATE google_setup_generations SET issued=issued+1 WHERE singleton=1")
    return _newest(c)


def _code_row(c, code):
    """The setup code's row, found by its hash and compared with compare_digest, or None."""
    if not code:
        return None
    digest = code_hash(code)
    found = c.execute("SELECT * FROM google_setup_codes WHERE code_hash=?", (digest,)).fetchone()
    if found is None or not secrets.compare_digest(found["code_hash"], digest):
        return None
    return dict(found)


def _admin_of(c, actor, admins):
    """Whether the person who made a setup command is still a credential administrator."""
    from .credentials import administrator
    return administrator(c, SimpleNamespace(role="human", actor=str(actor or "")), admins)


def _consume_code(c, digest, now):
    """Mark an unused, unexpired code used. True only for the one caller that wins: the update is atomic."""
    cursor = c.execute("UPDATE google_setup_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL AND expires_at>?",
                       (now, digest, now))
    return cursor.rowcount == 1


def _epoch(stamp):
    parsed = H.parse_ts(stamp)
    return parsed.timestamp() if parsed else None


def _parse_upload(raw):
    """The upload's JSON object. Never echoes what was sent."""
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise Problem("key", "The upload is not JSON", 422) from None
    if not isinstance(payload, dict):
        raise Problem("key", "The upload is not a JSON object", 422)
    return payload


def command(settings, code):
    return f"curl -fsSL {settings.runner_url}/api/v2/google/setup.sh | bash -s -- {code}"


def _keep_ids(c):
    """The key ids the server still holds: the active key and any pending one. The setup script deletes the service
    account's other keys (scripts/google-setup.sh), so a key Tico was never given does not outlive the upload."""
    ids = [row[0] for row in c.execute("SELECT key_id FROM google_workspaces WHERE status IN "
                                       "('connected','failing','pending') ORDER BY created_at")]
    return list(dict.fromkeys(ids))


def _store_pending(c, fields, domain, generation, actor):
    """Make this key the pending one. A pending key it replaces is deleted; the active key keeps working until this
    one's check passes (`_settle` promotes it). Returns what the upload is answered with."""
    now = H.now()
    replaced = pending(c)
    if replaced:
        c.execute("DELETE FROM google_workspaces WHERE id=? AND status='pending'", (replaced["id"],))
    active_row = active(c)
    row_id = H.new_id()
    sealed = _cipher().seal(c, _context(row_id), json.dumps(fields, sort_keys=True))
    c.execute("INSERT INTO google_workspaces(id,domain,project_id,client_email,client_id,key_id,key_sealed,revision,"
              "status,checked_at,connected_at,last_error,probe_mailbox,created_by,created_at,updated_at,replaces_id,"
              "generation,mint_failing_since,authorized_scopes) "
              "VALUES(?,?,?,?,?,?,?,1,'pending',NULL,NULL,NULL,NULL,?,?,?,?,?,NULL,?)",
              (row_id, domain, fields["project_id"], fields["client_email"], fields["client_id"],
               fields["private_key_id"], sealed, actor, now, now, active_row["id"] if active_row else None, generation,
               json.dumps(scope_urls())))
    H.event(c, actor, "google.workspace.key", domain, {"generation": generation, "replaced_pending": bool(replaced)})
    return {"client_id": fields["client_id"], "delegation_url": delegation_url(fields["client_id"]),
            "keep_key_ids": _keep_ids(c)}


# ----------------------------------------------------------------------------- checks

def _mailboxes_on(c, domain):
    return sorted({str(box["address"]).lower() for box in routines.message_bot_mailboxes(c)
                   if _domain(box["address"]) == domain})


def _probe_mailbox(c, who, row, requested):
    """The mailbox a check asks Google about: the admin's pick (validated: a message bot mailbox on the domain), else
    the one the last check used, the admin's own when it is covered, else the first covered one."""
    covered = _mailboxes_on(c, row["domain"])
    if requested:
        box = _address(requested)
        if box not in covered:
            raise Problem("mailbox", "Pick a message bot mailbox on " + row["domain"], 422)
        return box
    if row.get("probe_mailbox") in covered:
        return row["probe_mailbox"]
    own = ""
    if who is not None:
        from .google_connect import mailbox_for, require_self
        try:
            own = str(mailbox_for(c, require_self(who))[0]).lower()
        except Problem:
            own = ""
    if own in covered:
        return own
    if covered:
        return covered[0]
    raise Problem("mailbox", "No message bot mailbox is on " + row["domain"] + " yet", 422)


MESSAGES = {"unauthorized_client": "Google has not accepted the authorization yet. Check that it was granted for this "
                                   "key's client ID and the scopes, then check again. Google can take a few minutes.",
            "not_found": "Google has no such mailbox on this domain.",
            "other": "Google could not finish the check. Try again in a few minutes."}


def _probe(store, row, mailbox):
    """Gmail's profile and Calendar's calendar list, read only, with a token minted for `row`. (ok, kind, message,
    scopes): `scopes` is what Google accepted, so the row records what its key was authorized for. Each service asks
    for its write and read-only scope together, then, refused, for the write scope alone, as a key authorized before
    the read-only scopes is."""
    proven = []
    for service, url in PROBES:
        token = None
        for scopes in (WORKSPACE_SCOPES[service] + READ_ONLY_SCOPES[service], WORKSPACE_SCOPES[service]):
            with store.read() as c:
                try:
                    token = mint(c, mailbox, service, row=row, scopes=scopes)["access_token"]
                except Problem as refused:
                    kind = refused.extra.get("kind", "other")
                    if kind == "unauthorized_client" and len(scopes) > len(WORKSPACE_SCOPES[service]):
                        continue
                    return False, kind, MESSAGES.get(kind, MESSAGES["other"]), []
            proven += scopes
            break
        status, _ = google_call(url, token=token)
        if status != 200:
            kind = "not_found" if status == 404 else "other"
            return False, kind, MESSAGES[kind], []
    return True, None, "", sorted(proven)


def _settle(c, row, mailbox, ok, kind, message, actor, scopes=None):
    """Record a check's result for the revision it started on. A result for a revision that has since changed (the key
    was replaced, promoted or removed) is dropped: returns False. A passing pending key becomes the active one, and the
    active key it replaces is deleted first (promotion). A passing check records the scopes it proved."""
    current = c.execute("SELECT * FROM google_workspaces WHERE id=? AND revision=?",
                        (row["id"], row["revision"])).fetchone()
    if current is None:
        return False
    now = H.now()
    was_pending = current["status"] == "pending"
    proven = json.dumps(sorted(scopes)) if ok and scopes else current["authorized_scopes"]
    if ok and was_pending:
        c.execute("DELETE FROM google_workspaces WHERE status IN ('connected','failing') AND id<>?", (row["id"],))
        c.execute("UPDATE google_workspaces SET status='connected',connected_at=?,checked_at=?,last_error=NULL,"
                  "probe_mailbox=?,mint_failing_since=NULL,replaces_id=NULL,authorized_scopes=?,revision=revision+1,"
                  "updated_at=? WHERE id=? AND revision=?", (now, now, mailbox, proven, now, row["id"], row["revision"]))
        forget_tokens()
    elif ok:
        c.execute("UPDATE google_workspaces SET status='connected',checked_at=?,last_error=NULL,probe_mailbox=?,"
                  "mint_failing_since=NULL,authorized_scopes=?,revision=revision+1,updated_at=? "
                  "WHERE id=? AND revision=?", (now, mailbox, proven, now, row["id"], row["revision"]))
    elif was_pending:
        # A pending key that fails its check stays pending: the active key keeps working meanwhile.
        c.execute("UPDATE google_workspaces SET last_error=?,checked_at=?,probe_mailbox=?,revision=revision+1,"
                  "updated_at=? WHERE id=? AND revision=?", (message, now, mailbox, now, row["id"], row["revision"]))
    elif kind in KEY_KINDS:
        c.execute("UPDATE google_workspaces SET status='failing',last_error=?,checked_at=?,probe_mailbox=?,"
                  "mint_failing_since=COALESCE(mint_failing_since,?),revision=revision+1,updated_at=? "
                  "WHERE id=? AND revision=?", (message, now, mailbox, now, now, row["id"], row["revision"]))
    else:
        # One mailbox missing (not_found), or a refusal that is not about the key, does not turn an active key failing:
        # the reason is recorded and the key keeps working (KEY_KINDS).
        c.execute("UPDATE google_workspaces SET last_error=?,checked_at=?,probe_mailbox=?,revision=revision+1,"
                  "updated_at=? WHERE id=? AND revision=?", (message, now, mailbox, now, row["id"], row["revision"]))
    H.event(c, actor, "google.workspace.check", mailbox, {"ok": bool(ok), "kind": kind or ""})
    return True


def run_check(store, row, mailbox, actor):
    """Probe `row` for `mailbox`, then settle the result under the revision it started on. Google is asked outside any
    write transaction. Returns (settled, ok, kind, message)."""
    ok, kind, message, scopes = _probe(store, row, mailbox)
    with store.transaction() as c:
        settled = _settle(c, row, mailbox, ok, kind, message, actor, scopes)
    return settled, ok, kind, message


def tick(store, now=None):
    """Checks a pending key, and a failing one, again every RECHECK_SECONDS for RECHECK_WINDOW after it started (the
    scheduler loop calls this). A key past its window waits for Check."""
    now = time.time() if now is None else now
    plan = []
    with store.read() as c:
        for row in [dict(found) for found in c.execute(
                "SELECT * FROM google_workspaces WHERE status IN ('pending','failing')")]:
            started = _epoch(row["mint_failing_since"] or row["created_at"] if row["status"] == "failing"
                             else row["created_at"])
            last = _epoch(row["checked_at"])
            if started is None or now - started > RECHECK_WINDOW or (last is not None and now - last < RECHECK_SECONDS):
                continue
            try:
                plan.append((row, _probe_mailbox(c, None, row, None)))
            except Problem:
                continue
    for row, mailbox in plan:
        run_check(store, row, mailbox, "system:google")


# ----------------------------------------------------------------------------- what a computer's token leaves behind

def record_mint(c, row, mailbox, service, runner, ok, kind=""):
    """The outcome of a token a computer asked for (`mint`), recorded in a write transaction for the revision it was
    minted with. A success clears a failure clock; a refusal about the key itself starts one, and FAILING_SECONDS of
    them turn a connected key failing. One audit row per (mailbox, service, computer) per hour."""
    box = _address(mailbox)
    now = H.now()
    current = c.execute("SELECT * FROM google_workspaces WHERE id=? AND revision=?",
                        (row["id"], row["revision"])).fetchone()
    if current is not None and current["status"] in ACTIVE:
        if ok and (current["mint_failing_since"] is not None or current["status"] != "connected"):
            c.execute("UPDATE google_workspaces SET status='connected',mint_failing_since=NULL,last_error=NULL,"
                      "updated_at=? WHERE id=? AND revision=?", (now, row["id"], row["revision"]))
        elif not ok and kind in KEY_KINDS:
            since = current["mint_failing_since"] or now
            failing = now >= H.shift(since, seconds=FAILING_SECONDS)
            c.execute("UPDATE google_workspaces SET status=?,mint_failing_since=?,last_error=?,updated_at=? "
                      "WHERE id=? AND revision=?",
                      ("failing" if failing else current["status"], since, MESSAGES.get(kind, MESSAGES["other"]),
                       now, row["id"], row["revision"]))
    actor = "runner:" + runner if runner else "system:google"
    stamp = time.time()
    with _LOCK:
        last = _AUDITED.get((box, service, runner or ""))
        if last is not None and stamp - last < AUDIT_SECONDS:
            return
        _AUDITED[(box, service, runner or "")] = stamp
    H.event(c, actor, "google.workspace.mint", box, {"service": service, "runner": runner or "", "ok": bool(ok),
                                                     "kind": kind or ""})


# ----------------------------------------------------------------------------- the routes

class DomainBody(M.Contract):
    domain: str = Field(default="", max_length=253)


class CheckBody(M.Contract):
    mailbox: str = Field(default="", max_length=320)


class PermissionsBody(M.Contract):
    organize_mail: bool
    send_mail: bool
    change_calendar: bool


class OAuthClientBody(M.Contract):
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(min_length=1, max_length=300)


def _view(row):
    """A key row as Settings > Inboxes shows it: never the key, its sealed text or its key id."""
    if row is None:
        return None
    return {"status": row["status"], "domain": row["domain"], "project_id": row["project_id"],
            "client_id": row["client_id"], "scopes": scope_urls(), "delegation_url": delegation_url(row["client_id"]),
            "connected_at": row["connected_at"], "checked_at": row["checked_at"], "last_error": row["last_error"],
            "probe": {"mailbox": row["probe_mailbox"]} if row["probe_mailbox"] else None,
            "generation": row["generation"], "narrowable": narrowable(row)}


def _pair(c):
    """(workspace, pending): the active key, or the pending one when none is active; and the replacement waiting while
    an active key keeps working."""
    row, waiting = active(c), pending(c)
    if row is None:
        return _view(waiting), None
    return _view(row), _view(waiting)


def _oauth_view(c, settings):
    """The OAuth client's state without its secret, and without unsealing it: the server's environment wins."""
    from .google_connect import redirect_uri
    if settings.google_oauth_client_id and settings.google_oauth_client_secret:
        source, client_id = "env", settings.google_oauth_client_id
    else:
        found = c.execute("SELECT client_id FROM google_oauth_clients WHERE singleton=1").fetchone()
        source, client_id = ("settings", found[0]) if found else (None, None)
    return {"configured": source is not None, "source": source, "redirect_uri": redirect_uri(settings),
            "client_id": client_id}


def _reachable(settings):
    from .config import LOOPBACK
    return (urllib.parse.urlsplit(settings.runner_url).hostname or "") not in LOOPBACK


IN_FLIGHT = ("signing_in", "ready", "finishing", "disconnecting", "revoking")


def _person_status(mailbox, grants, attempt, row):
    """One person's Google state for the team list, by the rule `authority` picks a credential with: any live personal
    grant for the mailbox first, whoever made it; then the Workspace key's domain; then the person's own Connect
    Google attempt for this mailbox, under way or failed. `grants` are the live grants for the mailbox."""
    if grants:
        statuses = {grant["status"] for grant in grants}
        return "connected" if "connected" in statuses else "failing" if "failed" in statuses else "pending"
    if row and _domain(mailbox) == row["domain"]:
        return "workspace"
    status = attempt["status"] if attempt and str(attempt["mailbox"] or "").lower() == mailbox else None
    if status in IN_FLIGHT:
        return "pending"
    return "failing" if status == "failed" else "none"


def team_statuses(c, after=""):
    """Every person on the roster with a mailbox (people, never bots; nobody hidden or gone from the directory), and
    whether their mailbox is connected. A fixed number of statements however long the roster is (the bots, their
    declarations and google_connects each read once), and nothing asked of Google. Sorted by name, PEOPLE_MAX a page:
    `after` is the last person id of the page before (its `next`), and `more` says another page follows."""
    from .views import roster
    people = roster(c)
    boxes = routines.message_bot_mailboxes(c, people)
    own = {}
    for box in boxes:                   # routines.mailbox_of, indexed once: the first bot mailbox a person has
        own.setdefault(box["person_id"], box["address"])
    row = active(c)
    live, attempts = {}, {}
    for grant in c.execute("SELECT person_id,mailbox,status,holder,grant_hash FROM google_connects"):
        if grant["holder"] and grant["grant_hash"] and grant["status"] != "disconnected":
            live.setdefault(str(grant["mailbox"] or "").lower(), []).append(grant)
        attempts[grant["person_id"]] = grant
    out, order = [], {}
    for person in people.get("people") or []:
        order[person["id"]] = (str(person.get("name") or person["id"]).lower(), person["id"])
        if person.get("hidden") or person.get("directory_left"):
            continue
        mailbox = own.get(person["id"]) or str(person.get("email") or "").strip().lower()
        if not routines.MAILBOX.fullmatch(mailbox):
            continue
        if len(str(person["id"])) > CELL_MAX:
            # The id is the page cursor and is never cut, so a row with one this long stays off the list.
            logging.getLogger("tico.google").warning("Team list: left out a person whose id is %d characters",
                                                     len(str(person["id"])))
            continue
        out.append((order[person["id"]], {
            "person_id": person["id"], "name": str(person.get("name") or person["id"])[:CELL_MAX],
            "mailbox": mailbox[:CELL_MAX],
            "status": _person_status(mailbox, live.get(mailbox), attempts.get(person["id"]), row)}))
    out.sort(key=lambda pair: pair[0])
    if after:
        # The cursor is a person id, placed by its name in the whole roster: one hidden or gone since keeps its place.
        if after not in order:
            raise Problem("after", "That person is no longer on the roster; load the list again", 422)
        out = [pair for pair in out if pair[0] > order[after]]
    # A page stops at PEOPLE_MAX rows or at PAGE_BYTES for the whole answer, whichever comes first, and `next` is its
    # last row. A row that could never fit a page alone is left out, so every page holds at least one row.
    page, size, more, budget = [], 0, False, PAGE_BYTES - ENVELOPE
    for _, person in out:
        cost = len(json.dumps(person, ensure_ascii=False).encode()) + 1
        if cost > budget:
            logging.getLogger("tico.google").warning("Team list: left out a row of %d bytes", cost)
            continue
        if len(page) == PEOPLE_MAX or size + cost > budget:
            more = True
            break
        page.append(person)
        size += cost
    return {"people": page, "more": more, "next": page[-1]["person_id"] if more else None}


def install(app, store):
    """Settings > Inboxes' Workspace routes. Installed after install_credentials, so the vault is there to seal with.
    None of them goes through `mutate`: each one checks its own rights (an admin, or a setup code)."""
    from .credentials import administrator, require_admin
    use_vault(app.state.vault)
    settings = store.settings

    def _upload(raw, code, who):
        """The key upload, from a setup command (`code`) or an admin session (`who`). Synchronous: runs in a thread."""
        if code is None:
            with store.transaction() as c:
                require_admin(c, who, settings.credential_admins)
                payload = _parse_upload(raw)
                domain = clean_domain(payload.get("domain"))
                fields = validate_key(payload.get("key"))
                return _store_pending(c, fields, domain, _issue(c), who.actor)
        now = time.time()
        _check_locked(now)
        with store.transaction() as c:
            row = _code_row(c, code)
            stamp = H.now()
            # The code is read before the body is looked at, so a bad body cannot dodge the bad-code count.
            if row is None or row["used_at"] is not None or row["expires_at"] <= stamp:
                _bad_code(now)
                raise Problem("setup_code", "That setup code is not valid. Run the setup command again from "
                                            "Settings > Inboxes.", 401)
            payload = _parse_upload(raw)
            fields = validate_key(payload.get("key"))
            if not _admin_of(c, row["created_by"], settings.credential_admins):
                raise Problem("forbidden", "The person who made this setup command is no longer a credential "
                                           "administrator", 403)
            if row["generation"] < _newest(c):
                raise Problem("superseded", "A newer setup command was made. Run the newest one from Settings > Inboxes.",
                              409)
            if not _consume_code(c, row["code_hash"], stamp):
                _bad_code(now)
                raise Problem("setup_code", "That setup code was just used. Run the setup command again.", 401)
            return _store_pending(c, fields, row["domain"], row["generation"], row["created_by"])

    @app.get(GOOGLE_PATH)
    def workspace_status(request: Request):
        """Settings > Inboxes for whoever is signed in: the Workspace key, a replacement waiting, the OAuth client, and
        whether this person's own mailbox is covered by the key (`me` is null for anyone who is not a person here)."""
        from .google_connect import mailbox_for, require_self
        who = request.state.identity
        with store.read() as c:
            workspace, waiting = _pair(c)
            try:
                mailbox = str(mailbox_for(c, require_self(who))[0]).lower()
                me = {"mailbox": mailbox, "covered_by_workspace": covers(c, mailbox)}
            except Problem:
                me = None
            return {"can_admin": administrator(c, who, settings.credential_admins), "workspace": workspace,
                    "pending": waiting, "oauth": _oauth_view(c, settings), "me": me, "permissions": permissions(c),
                    # `newest` lets the page drop a setup command another admin's newer one superseded.
                    "setup": {"base": settings.runner_url, "reachable": _reachable(settings), "newest": _newest(c)}}

    @app.put(PERMISSIONS_PATH)
    def set_permissions(request: Request, body: PermissionsBody):
        """What Tico may do in Google, for the whole company: an admin acting as themselves (Send mail lets bots
        write to outside parties, so no bot, BotOps or the Assistant sets it, as with a bot's own Mail sending)."""
        from .mail_settings import require_person
        who = request.state.identity
        require_person(who)
        chosen = {name: bool(getattr(body, name)) for name in PERMISSIONS}
        with store.transaction() as c:
            require_admin(c, who, settings.credential_admins)
            c.execute("INSERT INTO registry_metadata(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                      "value=excluded.value", (PERMISSIONS_KEY, json.dumps(chosen)))
            H.event(c, who.actor, "google.permissions.set", "", chosen)
        with store.read() as c:
            return {"permissions": permissions(c)}

    @app.get(PEOPLE_PATH)
    def team_google(request: Request, after: str = ""):
        """Settings > Inboxes' team list, for an admin: each person with a mailbox, and whether it is connected
        (`connected`, `none`, `pending`, `failing`, or `workspace` when the Workspace key covers it). A page at a
        time: `after` is the `next` of the page before."""
        who = request.state.identity
        with store.read() as c:
            if not administrator(c, who, settings.credential_admins):
                raise Problem("forbidden", "Only an admin sees the team's Google connections", 403)
            return team_statuses(c, after.strip())

    @app.post(CODE_PATH)
    def make_setup_code(request: Request, body: DomainBody):
        """A setup command for one Workspace domain: its code lasts thirty minutes and works once."""
        who = request.state.identity
        with store.transaction() as c:
            require_admin(c, who, settings.credential_admins)
            domain = clean_domain(body.domain)
            code = secrets.token_urlsafe(32)
            now = H.now()
            expires = H.shift(now, seconds=CODE_SECONDS)
            generation = _issue(c)
            c.execute("INSERT INTO google_setup_codes(code_hash,domain,created_by,created_at,expires_at,used_at,generation) "
                      "VALUES(?,?,?,?,?,NULL,?)", (code_hash(code), domain, who.actor, now, expires, generation))
            H.event(c, who.actor, "google.workspace.setup_code", domain, {"generation": generation})
        return {"code": code, "command": command(settings, code), "expires_at": expires, "generation": generation}

    @app.get(SETUP_PATH, include_in_schema=False)
    def setup_script():
        """scripts/google-setup.sh with this server's address and the install's label filled in. Public: it carries
        nothing secret, and the code is typed in the command, not in the script."""
        try:
            text = SCRIPT.read_text()
        except OSError:
            raise Problem("not_built", "The setup script is not built on this server yet", 503) from None
        from . import census
        with store.read() as c:
            install_id = census.install_id_if_set(c)
        if not install_id:      # only the first request of an install writes (census makes the id)
            with store.transaction() as c:
                install_id = census.install_id_in(c)
        label = hashlib.sha256(install_id.encode()).hexdigest()[:12]
        text = text.replace("__TICO_BASE__", settings.runner_url).replace("__TICO_INSTALL__", label)
        return Response(content=text, media_type="text/x-shellscript")

    @app.post(KEY_PATH)
    async def upload_key(request: Request):
        """The Workspace's service-account key: from a setup command (`Authorization: Setup <code>`), or an admin's
        session with a `domain`. The key is validated, sealed, and kept pending until its check passes."""
        raw = await request.body()
        code = setup_code(request)
        who = getattr(request.state, "identity", None)
        return await asyncio.to_thread(_upload, raw, code, who)

    @app.post(KEEP_PATH, include_in_schema=False)
    def keep_key(request: Request):
        """After an upload, the setup script asks which key ids to keep. Only for the code that made the upload, within
        USED_CODE_SECONDS of it. `latest` is false once a newer setup command exists: the script then deletes nothing."""
        code = setup_code(request)
        now = time.time()
        _check_locked(now)
        with store.read() as c:
            row = _code_row(c, code)
            if row is None:
                _bad_code(now)
                raise Problem("setup_code", "That setup code is not valid. Run the setup command again from "
                                            "Settings > Inboxes.", 401)
            used = _epoch(row["used_at"]) if row["used_at"] else None
            if used is None or now - used > USED_CODE_SECONDS:
                raise Problem("forbidden", "This setup command has no upload to keep keys for, or its window has passed",
                              403)
            # The same test as the upload: a removed admin's code gets no key ids.
            if not _admin_of(c, row["created_by"], settings.credential_admins):
                raise Problem("forbidden", "The person who made this setup command is no longer a credential "
                                           "administrator", 403)
            return {"latest": row["generation"] >= _newest(c), "keep_key_ids": _keep_ids(c)}

    @app.post(CHECK_PATH)
    def check_key(request: Request, body: CheckBody | None = None):
        """Asks Google whether the pending key (or the active one) works for one message bot mailbox: Gmail's profile
        and Calendar's list, read only. A passing pending key becomes the active one."""
        who = request.state.identity
        requested = body.mailbox if body and body.mailbox else None
        with store.read() as c:
            require_admin(c, who, settings.credential_admins)
            row = pending(c) or active(c)
            if row is None:
                raise Problem("not_connected", "No Google Workspace key is set up yet", 409)
            mailbox = _probe_mailbox(c, who, row, requested)
        settled, ok, kind, message = run_check(store, row, mailbox, who.actor)
        if not settled:
            raise Problem("changed", "The Workspace key changed while it was checked. Check again.", 409)
        with store.read() as c:
            workspace, _ = _pair(c)
        return {"ok": bool(ok), "mailbox": mailbox, "error_kind": kind or None,
                "message": message or "Google accepts this key for this mailbox.", "workspace": workspace}

    @app.delete(WORKSPACE_PATH)
    def disconnect_workspace(request: Request):
        """Removes the Workspace key, active and pending. Personal grants (Connect Google) are not touched."""
        who = request.state.identity
        with store.transaction() as c:
            require_admin(c, who, settings.credential_admins)
            removed = c.execute("DELETE FROM google_workspaces").rowcount
            H.event(c, who.actor, "google.workspace.disconnect", "", {"rows": removed})
        forget_tokens()
        return {"ok": True}

    @app.put(OAUTH_PATH)
    def set_oauth_client(request: Request, body: OAuthClientBody):
        """The OAuth client for Connect Google, when the server's environment has none. A replaced client marks the
        grants made with another client as needing a new sign-in (`reconnect`); rows with no recorded client are left."""
        who = request.state.identity
        if settings.google_oauth_client_id and settings.google_oauth_client_secret:
            raise Problem("env_client", "This server's environment sets the Google app, so it cannot be changed here", 409)
        with store.transaction() as c:
            require_admin(c, who, settings.credential_admins)
            sealed = _cipher().seal(c, OAUTH_CONTEXT, body.client_secret)
            now = H.now()
            c.execute("INSERT INTO google_oauth_clients(singleton,client_id,secret_sealed,revision,updated_by,updated_at) "
                      "VALUES(1,?,?,1,?,?) ON CONFLICT(singleton) DO UPDATE SET client_id=excluded.client_id,"
                      "secret_sealed=excluded.secret_sealed,revision=google_oauth_clients.revision+1,"
                      "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                      (body.client_id, sealed, who.actor, now))
            marked = c.execute("UPDATE google_connects SET status='failed',kind='reconnect',message=NULL,hint=NULL,"
                               "updated_at=? WHERE status IN ('connected','failed') AND grant_hash IS NOT NULL AND "
                               "client_id IS NOT NULL AND client_id<>?", (now, body.client_id)).rowcount
            H.event(c, who.actor, "google.oauth_client.set", body.client_id, {"reconnect": marked})
            return {"oauth": _oauth_view(c, settings)}

    @app.delete(OAUTH_PATH)
    def remove_oauth_client(request: Request):
        who = request.state.identity
        with store.transaction() as c:
            require_admin(c, who, settings.credential_admins)
            c.execute("DELETE FROM google_oauth_clients WHERE singleton=1")
            H.event(c, who.actor, "google.oauth_client.remove", "", {})
            return {"oauth": _oauth_view(c, settings)}
