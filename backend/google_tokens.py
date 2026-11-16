# pyright: strict
"""The one door to a Google access token for a mailbox, on the server: every Gmail and Google Calendar call asks here.

`authority` (backend/google_workspace.py) picks the credential, and a person's own grant always wins:
- `personal`: the person's Connect Google grant, kept on this server sealed (`google_connects.grant_sealed`, context
  `google-connect:<person_id>:<grant_operation_id>`, so a ciphertext copied to another row opens nowhere). The refresh
  goes through Google with the OAuth client that issued the grant, and asks only for scopes the grant holds.
- `workspace`: the Workspace key mints a token impersonating the mailbox (`google_workspace.mint`).
- anything else is refused: no credential covers the mailbox.

A write (`write=True`) needs the service's act scope. A read-only grant, or one whose act scope What Tico may do turned
off, is refused here before Google is called. Tokens are cached in memory until five minutes before they expire, keyed by
the grant, so a new grant or a disconnect never reuses an old token. No token or refresh token is logged, stored or put
in an event.
"""

import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, cast

from . import google_connect, google_workspace
from .store import Problem

Json = dict[str, Any]
Conn = sqlite3.Connection

CACHE_MARGIN = 300  # a cached token is used until five minutes before it expires
CACHE_SIZE = 512
SEALED_PREFIX = "google-connect:"
# What a token is for: one service's reads, its writes, or everything What Tico may do allows (a computer's own sync).
SCOPES = ("read", "write", "allowed")
G: str = google_connect.G
SERVICE_SCOPES = google_connect.SERVICE_SCOPES
SERVICE_ACT_SCOPES = google_connect.SERVICE_ACT_SCOPES
IDENTITY_NAMES = google_connect.IDENTITY_NAMES
WORKSPACE_SCOPES = google_workspace.WORKSPACE_SCOPES
PERMISSIONS = cast("tuple[str, ...]", google_workspace.PERMISSIONS)

_LOCK = threading.Lock()
# (person, grant operation, client, service, scopes) -> (answer, expiry in epoch seconds)
_CACHE: OrderedDict[tuple[str, str, str, str, tuple[str, ...]], tuple[Json, float]] = OrderedDict()


# The untyped modules this one leans on, with the shapes it uses. Looked up on each call, so tests can replace them.
def _cipher() -> Any:
    return cast("Callable[[], Any]", google_workspace.cipher)()


def _authority(c: Conn, mailbox: str) -> str:
    return cast("Callable[[Conn, str], str]", google_workspace.authority)(c, mailbox)


def _refresh_scopes(service: str, recorded: str | None, may: Json | None = None) -> set[str]:
    return cast("Callable[..., set[str]]", google_connect.refresh_scopes)(service, recorded, may)


def _permissions(c: Conn) -> Json:
    return cast("Callable[[Conn], Json]", google_workspace.permissions)(c)


def _oauth_client(c: Conn) -> tuple[str, str, str] | None:
    settings = cast("Any", c).settings
    return cast("Callable[[Conn, Any], tuple[str, str, str] | None]", google_workspace.oauth_client)(c, settings)


def _google_token(client: tuple[str, str, str], fields: Json) -> Json:
    return cast("Callable[[tuple[str, str, str], Json], Json]", google_connect.google_token)(client, fields)


def context(person_id: str, operation_id: str) -> str:
    """The seal context of a person's grant: it binds the ciphertext to its row and to the connect that made it."""
    return f"{SEALED_PREFIX}{person_id}:{operation_id}"


def seal(c: Conn, person_id: str, operation_id: str, refresh_token: str) -> str:
    return str(_cipher().seal(c, context(person_id, operation_id), refresh_token))


def unseal(c: Conn, row: Json) -> str:
    """The refresh token of `row`'s grant. Only a sealed value under this row's own context opens: plaintext or a value
    sealed for another row is refused (503 vault_unavailable), never used."""
    from .credentials import SEALED

    stored = str(row.get("grant_sealed") or "")
    if not stored.startswith(cast("str", SEALED)):
        raise Problem("vault_unavailable", "A stored secret could not be decrypted", 503)
    return str(_cipher().unseal(c, context(str(row["person_id"]), str(row["grant_operation_id"])), stored))


def grant_row(c: Conn, mailbox: str) -> Json | None:
    """The live personal grant for `mailbox`, or None."""
    found = c.execute(
        "SELECT * FROM google_connects WHERE lower(mailbox)=? AND grant_sealed IS NOT NULL LIMIT 1",
        (str(mailbox or "").strip().lower(),),
    ).fetchone()
    return dict(found) if found else None


def covered(c: Conn, mailbox: str) -> bool:
    """Whether the server holds a credential for `mailbox` (the Workspace key or the person's own grant): its mail and
    calendar are read live from Google."""
    return _authority(c, mailbox) != "legacy"


def forget() -> None:
    """Drop every cached personal token (tests, and a server that wants a clean slate)."""
    with _LOCK:
        _CACHE.clear()


def token(c: Conn, mailbox: str, service: str, write: bool = False) -> str:
    """An access token for `mailbox` and one service ('gmail' or 'calendar'): read only, or with its act scope when
    `write`. Raises Problem when no credential covers the mailbox or the credential cannot do it."""
    return str(access(c, mailbox, service, "write" if write else "read")["access_token"])


def access(c: Conn, mailbox: str, service: str, scopes: str = "read") -> Json:
    """{access_token, expiry, scopes} for `mailbox` and `service`. `scopes` is 'read', 'write' or 'allowed' (every
    scope What Tico may do allows on this credential: a computer that syncs and acts for the mailbox)."""
    if service not in SERVICE_SCOPES or scopes not in SCOPES:
        raise Problem("scope", "A Google token is for gmail or calendar, to read, write or as allowed", 422)
    box = str(mailbox or "").strip().lower()
    kind = _authority(c, box)
    if kind == "personal":
        row = grant_row(c, box)
        if row is not None:
            return _personal(c, row, service, scopes)
    if kind == "workspace":
        return _workspace(c, box, service, scopes)
    raise Problem(
        "not_connected",
        "No Google Workspace key or Connect Google grant covers " + box,
        403,
        extra={"kind": "not_connected"},
    )


def _workspace(c: Conn, box: str, service: str, scopes: str) -> Json:
    row = cast("Callable[[Conn], Json | None]", google_workspace.active)(c)
    minting = cast("Callable[[str, Json, Json | None], list[str]]", google_workspace.minting_scopes)
    if scopes == "write":
        wanted: list[str] | None = WORKSPACE_SCOPES[service]
    elif scopes == "read":
        # The read-only scope, unless the key was authorized for the write scope only (Google refuses any other).
        wanted = minting(service, dict.fromkeys(PERMISSIONS, False), row)
    else:
        wanted = None  # minted as What Tico may do allows (google_workspace.minting_scopes)
    mint = cast("Callable[..., Json]", google_workspace.mint)
    return mint(c, box, service, row=row, scopes=wanted)


def _refused_write(row: Json, service: str, scopes: str) -> Problem:
    what = "Gmail" if service == "gmail" else "Google Calendar"
    mailbox = str(row["mailbox"])
    if scopes != "write":
        detail = (
            f"{mailbox}'s Connect Google grant gives Tico no {what} access. Connect Google again and leave its boxes "
            "ticked."
        )
    elif _refresh_scopes(service, row["grant_scopes"]) & SERVICE_ACT_SCOPES[service]:
        detail = (
            f"What Tico may do (Tools > Settings > Email) turns off changes to {what}, so Tico will not change "
            f"{mailbox}; nothing was sent to Google."
        )
    else:
        detail = (
            f"{mailbox} is connected to Tico read only for {what}, so Tico will not change it; nothing was sent to "
            "Google. Connect Google again with the company's own Google app to add this access."
        )
    return Problem("read_only" if scopes == "write" else "scope", detail, 403, extra={"kind": "scope"})


def _personal(c: Conn, row: Json, service: str, scopes: str) -> Json:
    """A token from the person's own grant. The grant's scopes bound every refresh; What Tico may do narrows it."""
    allowed = _refresh_scopes(service, row["grant_scopes"], _permissions(c))
    if scopes == "read":
        wanted = allowed & SERVICE_SCOPES[service]
    elif scopes == "write":
        wanted = allowed & SERVICE_ACT_SCOPES[service]
    else:
        wanted = allowed
    if not wanted:
        raise _refused_write(row, service, scopes)
    mailbox = str(row["mailbox"])
    client = _oauth_client(c)
    if client is None or (row["client_id"] and row["client_id"] != client[0]):
        raise Problem(
            "not_connected",
            f"{mailbox}'s Google connection was made with another Google app. Connect Google again in "
            "Tools > Settings > Email.",
            403,
            extra={"kind": "reconnect"},
        )
    key = (str(row["person_id"]), str(row["grant_operation_id"]), client[0], service, tuple(sorted(wanted)))
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and hit[1] > now + CACHE_MARGIN:
        return dict(hit[0])
    refresh = unseal(c, row)
    try:
        answer = _google_token(
            client, {"grant_type": "refresh_token", "refresh_token": refresh, "scope": " ".join(sorted(wanted))}
        )
    except Problem as refused:
        kind = str(cast("Json", refused.extra).get("kind", "error"))
        raise Problem(
            "not_connected",
            "Tico's server could not reach Google; try again shortly"
            if kind == "network"
            else f"Google would not refresh {mailbox}'s connection ({kind}). Connect Google again in "
            "Tools > Settings > Email.",
            503 if kind == "network" else 403,
            retryable=kind == "network",
            extra={"kind": kind},
        ) from None
    returned = set(str(answer.get("scope") or "").split()) - IDENTITY_NAMES
    if returned - wanted:
        raise Problem(
            "google_refused", "Google answered with more access than was asked for", 403, extra={"kind": "error"}
        )
    lifetime = int(answer.get("expires_in") or 3600)
    out: Json = {
        "access_token": str(answer["access_token"]),
        "expiry": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + lifetime)),
        "scopes": sorted(returned) or sorted(wanted),
    }
    with _LOCK:
        _CACHE[key] = (out, now + lifetime)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return dict(out)


def described(scopes: list[str]) -> list[str]:
    """What a grant holding `scopes` lets Tico do, as the person reads it: never a token."""
    held = set(scopes)
    can = ["read mail"] if held & {G + "gmail.readonly", G + "gmail.modify"} else []
    if G + "gmail.modify" in held:
        can += ["label and archive mail", "write drafts"]
    if held & (SERVICE_SCOPES["calendar"] | SERVICE_ACT_SCOPES["calendar"]):
        can.append("read calendar")
    if G + "calendar.events" in held:
        can.append("add calendar events")
    return can
