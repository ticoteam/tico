"""Read-only pulls of the company directory: Google Workspace and Microsoft Entra ID.

Each fetch returns every in-scope person as a record the sync engine (directory.py) compares
with the roster:

  {"email", "name", "title", "manager" (an email), "active", "external_id"}

Suspended, archived or disabled accounts come back with `active: False`, so the engine can mark
them as left. A failed or partial listing raises: the engine never sees half a directory, or it
would take the missing half for leavers.

Credentials are read-only by construction (Directory `*.readonly` scopes, Graph `*.Read.All`).
Tests replace TRANSPORT with an httpx.MockTransport, so nothing reaches the network.
"""

import base64
import re
import time
from urllib.parse import urlsplit

import httpx
import jwt

from .store import Problem

TRANSPORT = None

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_API = "https://admin.googleapis.com/admin/directory/v1"
GOOGLE_USER_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
GOOGLE_GROUP_SCOPE = "https://www.googleapis.com/auth/admin.directory.group.member.readonly"
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_HOST = "graph.microsoft.com"
MAX_PAGES = 200  # 500 people a page: a runaway pager stops long before this matters
PHOTO_MAX_BYTES = 2_000_000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _client():
    return httpx.Client(timeout=20, transport=TRANSPORT)


def _fail(source, response):
    # Vendor error bodies name the missing scope or consent, which is what the admin needs.
    try:
        body = response.json()
        detail = body.get("error_description") or (body.get("error") or {}).get("message") or body.get("error") or ""
        detail = detail if isinstance(detail, str) else str(detail)
    except Exception:
        detail = ""
    raise Problem(
        "directory_" + source, f"{source.title()} refused the request ({response.status_code}). " + detail[:300], 502
    )


def _email(value):
    value = str(value or "").strip().lower()
    return value if _EMAIL.match(value) else ""


def _in_domains(email, domains):
    return not domains or email.rsplit("@", 1)[1] in domains


# ---------------------------------------------------------------------------- Google Workspace
def _google_token(http, creds, scopes):
    info = creds["service_account"]
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": info["client_email"],
            "sub": creds["admin_email"],
            "scope": " ".join(scopes),
            "aud": info.get("token_uri") or GOOGLE_TOKEN_URL,
            "iat": now,
            "exp": now + 3000,
        },
        info["private_key"],
        algorithm="RS256",
    )
    response = http.post(
        info.get("token_uri") or GOOGLE_TOKEN_URL,
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
    )
    if response.status_code != 200:
        _fail("google", response)
    return response.json()["access_token"]


def _google_pages(http, token, path, params, key):
    page, out = "", []
    for _ in range(MAX_PAGES):
        query = {**params, **({"pageToken": page} if page else {})}
        response = http.get(GOOGLE_API + path, params=query, headers={"Authorization": "Bearer " + token})
        if response.status_code != 200:
            _fail("google", response)
        body = response.json()
        out += body.get(key) or []
        page = body.get("nextPageToken") or ""
        if not page:
            return out
    raise Problem("directory_google", "Google returned too many pages", 502)


def _in_ou(path, units):
    path = str(path or "/")
    return any(u == "/" or path == u or path.startswith(u + "/") for u in units)


def google_fetch(creds, flt):
    groups = [g for g in flt.get("groups") or [] if g]
    units = [("/" + u.strip().strip("/")) for u in flt.get("org_units") or [] if u.strip()]
    domains = [d.lower() for d in flt.get("domains") or [] if d]
    with _client() as http:
        token = _google_token(http, creds, [GOOGLE_USER_SCOPE, *([GOOGLE_GROUP_SCOPE] if groups else [])])
        users = _google_pages(
            http,
            token,
            "/users",
            {
                "customer": "my_customer",
                "maxResults": 500,
                "viewType": "admin_view",
                "fields": "nextPageToken,users(id,primaryEmail,name(fullName),suspended,archived,orgUnitPath,"
                "organizations(title,primary),relations(type,value))",
            },
            "users",
        )
        members = set()
        for group in groups:
            for m in _google_pages(
                http,
                token,
                "/groups/" + group.replace("/", "%2F") + "/members",
                {"maxResults": 200, "includeDerivedMembership": "true"},
                "members",
            ):
                if str(m.get("type") or "USER").upper() == "USER":
                    members.add(_email(m.get("email")))
    scoped = bool(groups or units)
    out = []
    for user in users:
        email = _email(user.get("primaryEmail"))
        if not email or not _in_domains(email, domains):
            continue
        if scoped and email not in members and not _in_ou(user.get("orgUnitPath"), units):
            continue
        orgs = user.get("organizations") or []
        org = next((o for o in orgs if o.get("primary")), orgs[0] if orgs else {})
        manager = next((r.get("value") for r in user.get("relations") or [] if r.get("type") == "manager"), "")
        out.append(
            {
                "email": email,
                "name": (user.get("name") or {}).get("fullName") or "",
                "title": org.get("title") or "",
                "manager": _email(manager),
                "active": not (user.get("suspended") or user.get("archived")),
                "external_id": str(user.get("id") or ""),
            }
        )
    return out


def google_photo(creds, email):
    with _client() as http:
        token = _google_token(http, creds, [GOOGLE_USER_SCOPE])
        response = http.get(
            GOOGLE_API + "/users/" + email + "/photos/thumbnail", headers={"Authorization": "Bearer " + token}
        )
    if response.status_code != 200:
        return None
    data = response.json().get("photoData") or ""
    if not data:
        return None
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return (raw, response.json().get("mimeType") or "image/jpeg") if len(raw) <= PHOTO_MAX_BYTES else None


# ------------------------------------------------------------------------------- Microsoft Entra
def _graph_token(http, creds):
    response = http.post(
        f"https://login.microsoftonline.com/{creds['tenant']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    if response.status_code != 200:
        _fail("entra", response)
    return response.json()["access_token"]


def _graph_pages(http, token, url, params=None, headers=None):
    out = []
    for _ in range(MAX_PAGES):
        response = http.get(url, params=params, headers={"Authorization": "Bearer " + token, **(headers or {})})
        if response.status_code != 200:
            _fail("entra", response)
        body = response.json()
        out += body.get("value") or []
        url, params = body.get("@odata.nextLink") or "", None
        if not url:
            return out
        # A next link is followed only on Graph itself, so a hostile response cannot aim the token elsewhere.
        if urlsplit(url).hostname != GRAPH_HOST:
            raise Problem("directory_entra", "Graph returned an unexpected page link", 502)
    raise Problem("directory_entra", "Graph returned too many pages", 502)


def entra_fetch(creds, flt):
    groups = [g for g in flt.get("groups") or [] if g]
    domains = [d.lower() for d in flt.get("domains") or [] if d]
    with _client() as http:
        token = _graph_token(http, creds)
        users = _graph_pages(
            http,
            token,
            GRAPH + "/users",
            {
                "$select": "id,displayName,mail,userPrincipalName,jobTitle,accountEnabled,userType",
                "$expand": "manager($select=id,mail,userPrincipalName)",
                "$top": 999,
            },
        )
        members = set()
        for group in groups:
            # The cast to user needs the eventual-consistency header and $count (Graph docs).
            for m in _graph_pages(
                http,
                token,
                f"{GRAPH}/groups/{group}/members/microsoft.graph.user",
                {"$select": "id", "$count": "true", "$top": 999},
                {"ConsistencyLevel": "eventual"},
            ):
                members.add(m.get("id"))
    out = []
    for user in users:
        if str(user.get("userType") or "Member") != "Member":
            continue  # guests are other companies' people
        email = _email(user.get("mail")) or _email(user.get("userPrincipalName"))
        if not email or not _in_domains(email, domains):
            continue
        if groups and user.get("id") not in members:
            continue
        boss = user.get("manager") or {}
        out.append(
            {
                "email": email,
                "name": user.get("displayName") or "",
                "title": user.get("jobTitle") or "",
                "manager": _email(boss.get("mail")) or _email(boss.get("userPrincipalName")),
                "active": user.get("accountEnabled") is not False,
                "external_id": str(user.get("id") or ""),
            }
        )
    return out


def entra_photo(creds, record):
    with _client() as http:
        token = _graph_token(http, creds)
        response = http.get(
            f"{GRAPH}/users/{record['external_id']}/photo/$value", headers={"Authorization": "Bearer " + token}
        )
    if response.status_code != 200 or len(response.content) > PHOTO_MAX_BYTES:
        return None
    return response.content, response.headers.get("content-type", "image/jpeg")


FETCH = {"google": google_fetch, "entra": entra_fetch}


def photo(source, creds, record):
    try:
        return google_photo(creds, record["email"]) if source == "google" else entra_photo(creds, record)
    except Exception:
        return None
