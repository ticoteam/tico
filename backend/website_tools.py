# pyright: strict
"""Website tools: a site a bot or a person signs in to through a browser, and its accounts.

A website tool is a `tools` row with `kind` 'website', its `sites` (JSON list of hostnames; a subdomain matches; an
`http://host` entry allows that host over plain http) and `central_only` (only the company's browser host opens it).
An account is a vault credential of kind `login` on the tool: its username, and sealed JSON {"password", "totp"?}.
Either may be empty: an account a person signs in to by hand (Google or SSO sign-in, an emailed code, a passkey) has
no password (`has_password` 0, sealed password ''), and fill or copy of it is refused with `no_password`.
A shared account (`holder` NULL) is used by whoever it is granted to; an own account only by its holder (and that
person's Assistant). A shared account may also be granted to a group (`group:<id>`, credential administrators only):
every person and non-archived bot in it and in the groups nested under it uses it, resolved at each use as API tools
resolve Who can see it (teammate_access.group_member_actors), so leaving the group ends it. The pick rule for a caller and a site: their own account on the tool, else the oldest shared
account granted to them, else 403 `no_account`.

The password goes out only in a signed-in person's reveal and in a fill or copy command sent to the computer that types
it (backend/browser_commands.py); the 2FA key never leaves this process: the server makes the current code. Activity is
`tool_calls` rows: the verb, the page (host and path, never a query), where the browser ran, never a value.
"""

import importlib
import ipaddress
import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from urllib.parse import urlsplit

from fastapi import Query, Request
from pydantic import ConfigDict, Field, SecretStr

from . import api_tools as api_tools_module
from . import credentials as credentials_module
from . import totp as T
from .models import ID, AccessAudience, Contract
from .store import H as hubdb, Problem

if TYPE_CHECKING:
    from fastapi import FastAPI

H: Any = hubdb
api_tools: Any = api_tools_module
vaults: Any = credentials_module

KIND = "website"
LOGIN = "login"
HTTP = "http://"
MAX_SITES = 50
# Activity verbs (`tool_calls.method`): the contract's, and the browser commands' download and upload.
VERBS = ("open", "fill", "copy", "code", "needs_you", "control", "sign_in", "download", "upload")
RESULTS = ("sent", "refused", "failed", "use")
WHERE = ("central", "own")
RECENT = 5
ACTIVITY_MAX = 200
SCAN_MAX = 2000  # Activity rows read for one page
PAGE_MAX = 300
# Secret reads per account (password and code, from fill, copy, code and a person's reveal): at most this many an hour.
SECRET_RATE = 20
SECRET_WINDOW = 3600.0
TONES = {"ok": "ok", "waiting": "wait", "fail": "fail", "neutral": "idle"}
UNKNOWN_SESSION: dict[str, Any] = {"state": "unknown", "where": None, "computer": None, "by": None}
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(r"(?=.{1,253}$)" + _HOST_LABEL + r"(?:\." + _HOST_LABEL + r")*")
# Characters a browser reads differently from urlsplit (a backslash is a slash to Chrome), and spaces or controls.
_UNSAFE = re.compile(r"[\\\s\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DEFAULT_PORTS = {"": 443, "https": 443, "http": 80}

# What install() was given, for the helpers builder B calls with a connection alone: the settings the credential
# administrators are read from (at each call: they can change), and auth.
_installed: dict[str, Any] = {"settings": None, "auth": None}


class Picked(TypedDict):
    tool_id: str
    account_id: str
    name: str
    sites: list[str]
    central_only: bool
    username: str
    has_password: bool
    has_totp: bool
    holder: str | None


# ---- sites


def _is_host(host: str) -> bool:
    return bool(_HOST.fullmatch(host))


def _ip_like(host: str) -> bool:
    """An address a browser reads as an IP (IPv6, dotted digits, or a last label like `1` or `0x7f`)."""
    last = host.rsplit(".", 1)[-1]
    return ":" in host or last.isdigit() or last.startswith("0x")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _split(text: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) of an address or a bare host, read the way a browser would, or None when a browser and
    urlsplit could disagree: a backslash, a space or control character, a user@ in the authority, a host that is not a
    hostname or an IP. The host is lowercase ASCII (punycode) without a trailing dot; a default port is None."""
    if not text or _UNSAFE.search(text):
        return None
    try:
        parts = urlsplit(text if _SCHEME.match(text) else "//" + text)
        port = parts.port
    except ValueError:
        return None
    if "@" in parts.netloc:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    host = host[:-1] if host.endswith(".") else host
    try:
        host = host.encode("idna").decode("ascii").lower() if not host.isascii() else host
    except UnicodeError:
        return None
    if not host or not (_is_ip(host) if _ip_like(host) else _is_host(host)):
        return None
    if _is_ip(host):
        host = str(ipaddress.ip_address(host))
    if port is not None and port == _DEFAULT_PORTS.get(scheme):
        port = None
    return scheme, host, port


def clean_sites(values: Iterable[object] | None) -> list[str]:
    """Sites as stored: lowercase hostnames (punycode), a port only when it is not the default, no path, in order, once
    each. A full address is taken for its host; one written `http://host` keeps that scheme (the browser may then fill
    on its plain-http pages). A single word (`com`, `localhost`) is refused, as is anything that is not a hostname or
    an IP, without echoing it."""
    out: list[str] = []
    for value in values or ():
        text = str(value or "").strip()
        if not text:
            continue
        found = _split(text)
        if found is None or found[0] not in ("", "http", "https"):
            raise Problem("sites", "Enter a full hostname, such as acme.example.", 422)
        scheme, host, port = found
        if not _is_ip(host) and "." not in host:
            raise Problem("sites", "Enter a full hostname, such as acme.example, rather than one word.", 422)
        entry = (f"[{host}]" if ":" in host else host) + (f":{port}" if port is not None else "")
        entry = HTTP + entry if scheme == "http" else entry
        if entry not in out:
            out.append(entry)
    if not out:
        raise Problem("sites", "Add the site's full address, such as acme.example.", 422)
    if len(out) > MAX_SITES:
        raise Problem("sites", f"Keep at most {MAX_SITES} sites on this website tool.", 422)
    return out


def sites_of(tool: Any) -> list[str]:
    try:
        found = json.loads(tool["sites"] or "[]")
    except (ValueError, TypeError, IndexError, KeyError):
        return []
    items = cast(list[object], found) if isinstance(found, list) else []
    return [s for s in items if isinstance(s, str)]


def _host_of(site: str) -> str:
    return site.removeprefix(HTTP)


def site_matches(sites: list[str], url: str) -> bool:
    """Whether `url` (an address, or a bare hostname) is on one of `sites`: the host itself or a subdomain of one, on
    the same port (a site without one: the default port only). An IP matches only a site that is that IP. An http://
    address matches only a site written `http://host`; any scheme but http and https matches nothing, and so does an
    address a browser could read differently (see `_split`)."""
    found = _split(str(url or "").strip())
    if found is None:
        return False
    scheme, host, port = found
    if scheme not in ("", "http", "https"):
        return False
    for site in sites or ():
        own = _split(site)
        if (
            own is None
            or own[0] not in ("", "http", "https")
            or (scheme == "http" and own[0] != "http")
            or port != own[2]
        ):
            continue
        own_host = own[1]
        if not _is_ip(own_host) and "." not in own_host:
            continue  # a single word would match a whole top-level domain
        if host == own_host:
            return True
        if not _ip_like(host) and not _ip_like(own_host) and host.endswith("." + own_host):
            return True
    return False


def page_of(url: str) -> str:
    """What Activity keeps of a page: host and path, never the query, the fragment or any user:password@."""
    text = str(url or "").strip()
    try:
        parts = urlsplit(text if "://" in text else "//" + text)
        page = (parts.hostname or "") + (parts.path or "")
    except ValueError:
        page = text.split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1]
    return page[:PAGE_MAX]


# ---- who may do what


def _admins(admins: Iterable[str] | None = None) -> Iterable[str]:
    if admins is not None:
        return admins
    settings = _installed["settings"]
    return tuple(cast(Iterable[str], settings.credential_admins)) if settings is not None else ()


def _auth(auth: Any = None) -> Any:
    return auth if auth is not None else _installed["auth"]


def _admin(c: Any, who: Any, admins: Iterable[str] | None = None) -> bool:
    return bool(vaults.administrator(c, who, _admins(admins))) and not getattr(who, "support", None)


def _require_admin(c: Any, who: Any, admins: Iterable[str]) -> None:
    if not _admin(c, who, admins):
        raise Problem("forbidden", "Ask a credential administrator to make this change in Tools.", 403)


def _person(who: Any) -> bool:
    return getattr(who, "role", "") in ("owner", "human") and not getattr(who, "support", None)


def _tool(c: Any, tool_id: str) -> Any:
    row = c.execute("SELECT * FROM tools WHERE id=? AND kind=?", (str(tool_id or ""), KIND)).fetchone()
    if row is None:
        raise Problem(
            "not_found", "This website tool is unavailable; choose one from Tools or ask an admin for access.", 404
        )
    return row


def _readable(c: Any, who: Any, tool_id: str, admins: Iterable[str] | None = None, auth: Any = None) -> Any:
    """The website tool's row, or a 404 when it does not exist or `who` may not see it."""
    try:
        return api_tools._readable(c, who, tool_id, list(_admins(admins)), _auth(auth), kind=KIND)
    except Problem as refused:
        if refused.code != "not_found":
            raise
        raise Problem(
            "not_found", "This website tool is unavailable; choose one from Tools or ask an admin for access.", 404
        ) from None


def _accounts(c: Any, tool_id: str) -> list[Any]:
    return c.execute(
        "SELECT * FROM credentials WHERE tool_id=? AND kind=? ORDER BY created_at, id", (tool_id, LOGIN)
    ).fetchall()


def _account(c: Any, tool_id: str, account_id: str) -> Any:
    row = c.execute(
        "SELECT * FROM credentials WHERE id=? AND tool_id=? AND kind=?", (str(account_id or ""), tool_id, LOGIN)
    ).fetchone()
    if row is None:
        raise Problem(
            "not_found", "This account is unavailable; choose an account in Tools or ask an admin for access.", 404
        )
    return row


GROUP = "group:"


def _org_groups(c: Any) -> dict[str, Any]:
    views: Any = importlib.import_module(".views", __package__)
    return cast(dict[str, Any], views.roster(c, shared=True).get("org_groups") or {})


def _group_name(groups: dict[str, Any], gid: str) -> str:
    found = cast(dict[str, Any], groups.get(gid) or {})
    return str(found.get("name") or gid)


def group_members(c: Any, gid: str) -> frozenset[str]:
    """The people and non-archived bots in group `gid` and the groups nested under it, as actors; read once per
    snapshot (a request's `_choose` asks for each account and each group)."""
    from . import read_cache

    values = read_cache.cache(c)
    key = ("website_group_members", gid)
    if values is not None and key in values:
        return cast(frozenset[str], values[key])
    teammates: Any = importlib.import_module(".teammate_access", __package__)
    found = frozenset(cast(Iterable[str], teammates.group_member_actors(c, gid)))
    if values is not None:
        values[key] = found
    return found


def _group_grants(c: Any, account: Any) -> list[str]:
    """The ids of the groups a shared account is granted to, oldest first, that still exist."""
    if account["holder"]:
        return []
    groups = _org_groups(c)
    out: list[str] = []
    for grant in c.execute(
        "SELECT subject FROM credential_grants WHERE credential_id=? AND subject LIKE 'group:%' AND revoked_at IS NULL "
        "AND parent_id IS NULL ORDER BY created_at, id",
        (account["id"],),
    ).fetchall():
        gid = str(grant["subject"])[len(GROUP) :]
        if gid in groups and gid not in out:
            out.append(gid)
    return out


def _grant(c: Any, account: Any, actor: str) -> Any:
    """The grant `actor` uses a shared account through: its own effective grant, else the first group grant whose
    members (nested groups included) take in `actor`, else None. Only a person or a bot is ever in a group."""
    direct = vaults.effective_grant(c, account["id"], actor)
    if direct is not None or not str(actor or "").startswith(("human:", "bot:")):
        return direct
    for gid in _group_grants(c, account):
        if actor in group_members(c, gid):
            return vaults.effective_grant(c, account["id"], GROUP + gid)
    return None


def can_use(c: Any, actor: str, account: Any) -> bool:
    """`actor` uses the account itself: it holds it (own), or holds an effective grant on it, directly or through a
    group (shared)."""
    if account["holder"]:
        return account["holder"] == actor
    return _grant(c, account, actor) is not None


def _users(c: Any, account: Any) -> list[str]:
    """Subjects that use the account: its holder, or each person and bot with an effective grant."""
    if account["holder"]:
        return [account["holder"]]
    out: list[str] = []
    for grant in c.execute(
        "SELECT subject FROM credential_grants WHERE credential_id=? AND revoked_at IS NULL ORDER BY created_at, id",
        (account["id"],),
    ).fetchall():
        subject = str(grant["subject"])
        if (
            subject.startswith(("human:", "bot:"))
            and subject not in out
            and vaults.effective_grant(c, account["id"], subject) is not None
        ):
            out.append(subject)
    return out


def _manages(c: Any, who: Any, subject: str, auth: Any = None) -> bool:
    """`subject` is the person themselves or a bot they own or manage."""
    if not _person(who):
        return False
    if subject == who.actor:
        return True
    authority = _auth(auth)
    return (
        subject.startswith("bot:")
        and authority is not None
        and bool(authority.bot_manager(c, who, H.actor_id(subject)))
    )


def can_manage(c: Any, who: Any, account: Any, admins: Iterable[str] | None = None, auth: Any = None) -> bool:
    """A credential administrator, or (an own account) its holder or a person who manages the bot that holds it."""
    if _admin(c, who, admins):
        return True
    return bool(account["holder"]) and _manages(c, who, account["holder"], auth)


def _bot_user_managed(c: Any, who: Any, account: Any, auth: Any = None) -> bool:
    """`who` manages a bot that uses the account, directly or through a granted group."""
    users = set(_users(c, account))
    for gid in _group_grants(c, account):
        users |= group_members(c, gid)
    return any(s.startswith("bot:") and _manages(c, who, s, auth) for s in sorted(users))


# ---- the interface backend/browser_commands.py calls


def _picked(tool: Any, account: Any) -> Picked:
    return {
        "tool_id": str(tool["id"]),
        "account_id": str(account["id"]),
        "name": str(tool["name"]),
        "sites": sites_of(tool),
        "central_only": bool(tool["central_only"]),
        "username": str(account["username"] or ""),
        "has_password": bool(account["has_password"]),
        "has_totp": bool(account["has_totp"]),
        "holder": account["holder"] or None,
    }


def _choose(c: Any, actor: str, accounts: list[Any]) -> Any:
    """The pick rule over one tool's accounts: `actor`'s own, else the oldest shared one granted to it, else None."""
    own = [a for a in accounts if a["holder"] == actor]
    if own:
        return own[0]
    for account in accounts:  # oldest first
        if not account["holder"] and _grant(c, account, actor) is not None:
            return account
    return None


def _candidates(c: Any, who: Any, site: str) -> list[Any]:
    """The website tools `site` names that `who` may see: by id, else by name (any case), else by host or address."""
    text = str(site or "").strip()
    if not text:
        return []
    tools = [
        t
        for t in c.execute("SELECT * FROM tools WHERE kind=? ORDER BY created_at, id", (KIND,)).fetchall()
        if api_tools.visible(c, who, t, list(_admins()), _auth())
    ]
    rules: list[Callable[[Any], bool]] = [
        lambda t: t["id"] == text,
        lambda t: str(t["name"]).strip().lower() == text.lower(),
        lambda t: site_matches(sites_of(t), text),
    ]
    for rule in rules:
        found = [t for t in tools if rule(t)]
        if found:
            return found
    return []


def pick(conn: Any, who: Any, site: str) -> Picked:
    """The account `who` (a bot's run or a person) uses on `site` (a host, an address, a tool id or a tool name).
    404 `no_such_site` when no website tool they may see is named; 403 `no_account` when none has an account for them.
    """
    tools = _candidates(conn, who, site)
    if not tools:
        raise Problem(
            "no_such_site", "No website tool matches that site; check its name or ask an admin to add it in Tools.", 404
        )
    for tool in tools:
        account = _choose(conn, who.actor, _accounts(conn, tool["id"]))
        if account is not None:
            return _picked(tool, account)
    raise Problem(
        "no_account",
        f"You have no account on {tools[0]['name']}; ask an admin to share one in Tools > {tools[0]['name']}.",
        403,
    )


def authorize(conn: Any, who: Any, tool_id: str, account_id: str) -> Picked:
    """Recheck the exact account a command uses; a changed pick never substitutes another login mid-command."""
    tool = _readable(conn, who, tool_id)
    account = _account(conn, tool_id, account_id)
    if not can_use(conn, who.actor, account):
        raise Problem(
            "no_account", "You no longer have access to this account; ask an admin to share it in Tools.", 403
        )
    return _picked(tool, account)


def _close(account_id: str) -> None:
    module = _browser()
    if module is not None:
        module.close_account(account_id)


def picked_account(
    conn: Any, who: Any, tool_id: str, account_id: str, admins: Iterable[str] | None = None, auth: Any = None
) -> Picked:
    """One account for a person's view of its browser: a person who uses it, a credential administrator, or one who
    manages its holder. One who only manages a bot the account is granted to gets it only while a "needs you" ask for
    this account (`browser:<tool>/<account>`) waits on them. Else 404, as for none."""
    if not _person(who):
        raise Problem(
            "forbidden", "Open the browser yourself while signed in to Tico, or ask a person to open it.", 403
        )
    tool = _readable(conn, who, tool_id, admins, auth)
    account = _account(conn, tool_id, account_id)
    if not (
        can_use(conn, who.actor, account)
        or can_manage(conn, who, account, admins, auth)
        or (_bot_user_managed(conn, who, account, auth) and _asked(conn, who.actor, tool["id"], account["id"]))
    ):
        raise Problem(
            "not_found", "This account is unavailable; choose an account in Tools or ask an admin for access.", 404
        )
    return _picked(tool, account)


def _asked(conn: Any, person: str, tool_id: str, account_id: str) -> bool:
    """An open "needs you" ask for this account's browser waits on `person` (backend/browser_commands.py open_ask)."""
    module = _browser()
    found = getattr(module, "open_ask", None) if module is not None else None
    return bool(found and found(conn, f"browser:{tool_id}/{account_id}", person=person))


_spent: dict[str, deque[float]] = {}
_spent_lock = threading.Lock()


def spend(account_id: str, now: float | None = None) -> float:
    """Count one secret read of the account; 429 `slow_down` past SECRET_RATE in an hour. In memory: a restart
    forgets, which only ever allows more."""
    at = time.monotonic() if now is None else now
    with _spent_lock:
        times = _spent.setdefault(account_id, deque())
        while times and at - times[0] >= SECRET_WINDOW:
            times.popleft()
        if len(times) >= SECRET_RATE:
            raise Problem(
                "slow_down",
                f"This account reached its limit of {SECRET_RATE} secret reads this hour; wait an hour and try again.",
                429,
                retryable=True,
            )
        times.append(at)
    return at


def finish_spend(account_id: str, reserved: float, success: bool) -> None:
    """Finish a fill's reservation: only a successful report starts its hourly usage window."""
    with _spent_lock:
        times = _spent.get(account_id)
        if times is not None and reserved in times:
            times.remove(reserved)
            if success:
                times.append(time.monotonic())


def _login_row(c: Any, account_id: str) -> Any:
    row = c.execute("SELECT * FROM credentials WHERE id=? AND kind=?", (str(account_id or ""), LOGIN)).fetchone()
    if row is None:
        raise Problem(
            "not_found", "This account is unavailable; choose an account in Tools or ask an admin for access.", 404
        )
    return row


def _totp_of(c: Any, vault: Any, row: Any) -> T.Totp:
    stored = cast(dict[str, str], vault.open_login(c, row))["totp"]
    found = T.parse_totp(stored) if stored else None
    if found is None:
        raise Problem(
            "no_2fa",
            "This account has no 2FA key; add one with Replace login or ask a person to complete sign-in.",
            409,
        )
    return found


def require_value(picked: Picked, field: str) -> None:
    """409 `no_username` or `no_password` when the account stores no such value: a person signs in to it by hand, so
    the bot asks one through the live view instead."""
    missing = (field == "username" and not picked["username"]) or (field == "password" and not picked["has_password"])
    if not missing:
        return
    sites = picked["sites"]
    site = _host_of(sites[0]) if sites else picked["name"]
    raise Problem(
        "no_" + field,
        f"This account has no {field}; a person signs in by hand, so run "
        f'tico browser needs-you {site} "Sign in to {site}" and end the turn.',
        409,
    )


def secret(
    conn: Any, vault: Any, account_id: str, field: Literal["username", "password", "code"], *, charge: bool = True
) -> str:
    """The value a fill or copy sends: the username, the password, or the current 2FA code (409 `no_2fa` when the
    account has none, `no_username` or `no_password` likewise). A password or a code counts toward the account's hourly
    limit (429 `slow_down`). The caller checked who may use it (`pick`, `picked_account`). Never logged."""
    row = _login_row(conn, account_id)
    if field in ("username", "password"):
        tool = conn.execute("SELECT * FROM tools WHERE id=?", (row["tool_id"],)).fetchone()
        if tool is None:
            raise Problem(
                "not_found", "This account is unavailable; choose an account in Tools or ask an admin for access.", 404
            )
        require_value(_picked(tool, row), field)
    if field == "username":
        return str(row["username"] or "")
    if field == "password":
        password = cast(dict[str, str], vault.open_login(conn, row))["password"]
        if charge:
            spend(row["id"])
        return password
    if field == "code":
        found = _totp_of(conn, vault, row)
        if charge:
            spend(row["id"])
        return T.code(found)
    raise Problem("field", "Choose username, password or code for field.", 422)


def expires_in(conn: Any, vault: Any, account_id: str) -> int:
    """Seconds the account's current 2FA code has left (409 `no_2fa` when none). Not a secret read."""
    return T.expires_in(_totp_of(conn, vault, _login_row(conn, account_id)))


def log(conn: Any, picked: Picked, who: Any, verb: str, page: str, where: str, result: str = "use") -> None:
    """One Activity row: who, the verb, the page (host and path only), where the browser ran. Never a value."""
    if verb not in VERBS or where not in WHERE or result not in RESULTS:
        raise ValueError("website activity: unknown verb, where or result")
    actor = str(who.actor)
    conn.execute(
        "INSERT INTO tool_calls(id,tool_id,credential_id,actor,bot,access_level,method,path,status_code,result,detail,"
        "duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            H.new_id(),
            picked["tool_id"],
            picked["account_id"],
            actor,
            actor[4:] if actor.startswith("bot:") else "",
            "own" if picked["holder"] else "shared",
            verb,
            page_of(page),
            None,
            result,
            where,
            None,
            H.now(),
        ),
    )


# ---- builder B's in-memory session facts (absent until backend/browser_commands.py lands)


def _browser() -> Any:
    try:
        return importlib.import_module(".browser_commands", __package__)
    except ModuleNotFoundError as exc:
        if not str(exc.name or "").endswith("browser_commands"):
            raise
        return None


def _session(c: Any, account_id: str) -> dict[str, Any]:
    module = _browser()
    view = getattr(module, "session_view", None) if module is not None else None
    found = view(account_id, c) if view is not None else None
    return dict(cast(dict[str, Any], found)) if isinstance(found, dict) else dict(UNKNOWN_SESSION)


def _host(c: Any) -> dict[str, Any] | None:
    module = _browser()
    view = getattr(module, "host_view", None) if module is not None else None
    found = view(c) if view is not None else None
    return dict(cast(dict[str, Any], found)) if isinstance(found, dict) else None


# ---- views


def _initials(name: str) -> str:
    return str(api_tools._initials(name))


def _who(subject: str, name: str | None) -> dict[str, Any]:
    label = name or H.actor_id(subject)
    return {
        "subject": subject,
        "kind": "bot" if subject.startswith("bot:") else "person",
        "id": H.actor_id(subject),
        "name": label,
        "initials": _initials(label),
    }


def _names(c: Any, subjects: Iterable[str]) -> dict[str, str]:
    names: Any = importlib.import_module(".names", __package__)
    return cast(dict[str, str], names.lookup(c, set(subjects)))


def _group_who(gid: str, name: str, members: int) -> dict[str, Any]:
    return {
        "subject": GROUP + gid,
        "kind": "group",
        "id": gid,
        "name": name,
        "initials": _initials(name),
        "members": members,
    }


def _by_name(row: dict[str, Any]) -> tuple[int, str, str]:
    return ({"group": 0, "person": 1}.get(str(row["kind"]), 2), str(row["name"]).lower(), str(row["subject"]))


def _access(c: Any, account: Any, users: list[str], label: dict[str, str], groups: bool) -> list[dict[str, Any]]:
    """One row per grant: the granted groups (to an administrator: `groups`) with their member count, then people,
    then bots, each by name. A person or bot granted directly who is also in a granted group carries `via`, the names
    of those groups."""
    rows = [_who(s, label.get(s)) for s in users]
    if groups:
        names = _org_groups(c)
        granted: list[tuple[str, frozenset[str]]] = []
        for gid in _group_grants(c, account):
            name = _group_name(names, gid)
            members = group_members(c, gid)
            granted.append((name, members))
            rows.append(_group_who(gid, name, len(members)))
        for row in rows:
            via = sorted((n for n, m in granted if row["subject"] in m), key=str.lower)
            if via:
                row["via"] = via
    return sorted(rows, key=_by_name)


def _account_view(
    c: Any, who: Any, account: Any, admin: bool, label: dict[str, str], admins: Iterable[str] | None, auth: Any
) -> dict[str, Any] | None:
    """An account as the caller may see it, or None: a non-admin sees only the accounts they use or manage."""
    uses = can_use(c, who.actor, account)
    manages = can_manage(c, who, account, admins, auth)
    if not (admin or uses or manages):
        return None
    holder = account["holder"] or None
    users = _users(c, account)
    if not admin and not holder:
        # A member sees themselves and the grants they made, as on an API tool.
        made = {
            str(r["subject"])
            for r in c.execute(
                "SELECT subject FROM credential_grants WHERE credential_id=? AND granted_by=? AND revoked_at IS NULL",
                (account["id"], who.actor),
            ).fetchall()
        }
        users = [s for s in users if s == who.actor or s in made]
    out: dict[str, Any] = {
        "id": account["id"],
        "kind": "own" if holder else "shared",
        "username": account["username"] or "",
        "has_password": bool(account["has_password"]),
        "has_totp": bool(account["has_totp"]),
        "holder": _who(holder, label.get(holder)) if holder else None,
        # A member's view ("themselves and the grants they made") never names a group or what it covers.
        "access": _access(c, account, users, label, admin and not holder),
        "session": _session(c, account["id"]),
        "can_use": uses,
        "can_manage": manages,
    }
    if _machine(who):
        del out["username"]  # a bot signs in with `tico login fill`; it never needs to read the username
    return out


def _machine(who: Any) -> bool:
    """A bot, a run's token (the Assistant's included), an external agent or a computer: never a signed-in person."""
    return (
        getattr(who, "role", "") not in ("owner", "human")
        or bool(getattr(who, "attempt_id", ""))
        or bool(getattr(who, "agent", ""))
        or bool(getattr(who, "runner_id", ""))
    )


def _accounts_view(c: Any, who: Any, tool: Any, admins: Iterable[str] | None, auth: Any) -> list[dict[str, Any]]:
    admin = _admin(c, who, admins)
    rows = _accounts(c, tool["id"])
    subjects = {s for a in rows for s in _users(c, a)}
    label = _names(c, subjects)
    out: list[dict[str, Any]] = []
    for account in rows:
        view = _account_view(c, who, account, admin, label, admins, auth)
        if view is not None:
            out.append(view)
    return out


def status(accounts: list[dict[str, Any]], host: dict[str, Any] | None) -> dict[str, str]:
    """{label, tone}: No account; the browser host offline; Needs you; In use; Ready."""
    if not accounts:
        return {"label": "No account", "tone": "waiting"}
    if host is not None and not host.get("online"):
        return {"label": f"{host.get('label') or 'The browser host'} is offline", "tone": "fail"}
    states = {str(cast(dict[str, Any], a.get("session") or {}).get("state") or "unknown") for a in accounts}
    if "needs_you" in states:
        return {"label": "Needs you", "tone": "waiting"}
    if "in_use" in states:
        return {"label": "In use", "tone": "ok"}
    return {"label": "Ready", "tone": "neutral"}


def _tool_view(tool: Any) -> dict[str, Any]:
    return {
        "id": tool["id"],
        "name": tool["name"],
        "kind": KIND,
        "sites": sites_of(tool),
        "central_only": bool(tool["central_only"]),
    }


def _activity_rows(
    c: Any, who: Any, tool_id: str, admin: bool, auth: Any, cursor: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    at, _, last = str(cursor or "").partition("|")
    sql, args = "SELECT * FROM tool_calls WHERE tool_id=?", [tool_id]
    if at:
        sql, args = sql + " AND (created_at<? OR (created_at=? AND id<?))", args + [at, at, last]
    found: list[Any] = []
    scanned: list[Any] = c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", [*args, SCAN_MAX]).fetchall()
    for row in scanned:
        if api_tools._call_visible(c, who, auth, admin, row):
            found.append(row)
            if len(found) > limit:
                break
    more = len(found) > limit
    found = found[:limit]
    # A full scan with too few rows the caller may see: the next page starts after the last row read.
    last_row = found[-1] if more else scanned[-1] if len(scanned) == SCAN_MAX else None
    subjects = {("bot:" + r["bot"]) if r["bot"] else str(r["actor"]) for r in found}
    label = _names(c, subjects)
    rows: list[dict[str, Any]] = []
    for r in found:
        subject = ("bot:" + r["bot"]) if r["bot"] else str(r["actor"])
        rows.append(
            {
                "id": r["id"],
                "at": r["created_at"],
                "who": _who(subject, label.get(subject)),
                "verb": r["method"],
                "page": r["path"],
                "account": "own" if r["access_level"] == "own" else "shared",
                "where": "central" if r["detail"] == "central" else "own",
                "result": r["result"],
            }
        )
    return rows, (f"{last_row['created_at']}|{last_row['id']}" if last_row is not None else None)


def detail(c: Any, who: Any, tool_id: str, admins: Iterable[str] | None = None, auth: Any = None) -> dict[str, Any]:
    """`GET /website-tools/{id}`: the tool, the accounts the caller may see (never a value), the browser host, the
    newest Activity; to whoever may change it, who can see it."""
    auth = _auth(auth)
    tool = _readable(c, who, tool_id, admins, auth)
    admin = _admin(c, who, admins)
    accounts = _accounts_view(c, who, tool, admins, auth)
    host = _host(c)
    recent, _ = _activity_rows(c, who, tool["id"], admin, auth, "", RECENT)
    out: dict[str, Any] = {
        "tool": _tool_view(tool),
        "status": status(accounts, host),
        "accounts": accounts,
        "host": host,
        "recent": recent,
        "can_manage": admin,
        "private": tool["audience"] is not None,
        "can_set_audience": False,
    }
    if api_tools.may_set_audience(c, who, tool, list(_admins(admins)), auth):
        out.update(
            can_set_audience=True,
            audience=api_tools.audience_of(tool),
            seen_by=api_tools.seen_by(c, tool, auth),
        )
    return out


def activity(
    c: Any,
    who: Any,
    tool_id: str,
    admins: Iterable[str] | None = None,
    auth: Any = None,
    cursor: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    auth = _auth(auth)
    tool = _readable(c, who, tool_id, admins, auth)
    rows, more = _activity_rows(c, who, tool["id"], _admin(c, who, admins), auth, cursor, limit)
    return {"rows": rows, "next": more}


def index_rows(c: Any, who: Any, auth: Any, admins: Iterable[str]) -> list[dict[str, Any]]:
    """Tools rows (backend/api_tools.py `index_rows`) for the website tools the reader may see."""
    admin = _admin(c, who, admins)
    host: dict[str, Any] | None = None
    host_read = False
    out: list[dict[str, Any]] = []
    for tool in c.execute("SELECT * FROM tools WHERE kind=? ORDER BY lower(name), id", (KIND,)).fetchall():
        if not (admin and tool["audience"] is None) and not api_tools.visible(c, who, tool, list(admins), auth):
            continue
        if not host_read:
            host, host_read = _host(c), True
        accounts = _accounts_view(c, who, tool, admins, auth)
        state = status(accounts, host)
        people: dict[str, dict[str, Any]] = {}
        for account in accounts:
            for person in account["access"]:
                people.setdefault(person["subject"], person)
        listed = list(people.values())
        if who.role in ("owner", "human") and not admin and auth is not None:
            bots = sorted({p["id"] for p in listed if p["kind"] == "bot"})
            seen = cast(dict[str, Any], auth.bot_accesses(c, who, bots)) if bots else {}
            listed = [
                p for p in listed if p["kind"] != "bot" or cast(dict[str, Any], seen.get(p["id"]) or {}).get("see")
            ]
        sites = sites_of(tool)
        out.append(
            api_tools._index_row(
                "website:" + tool["id"],
                KIND,
                tool["name"],
                {"tone": TONES[state["tone"]], "text": state["label"]},
                "#/tools/website/" + tool["id"],
                detail=_host_of(sites[0]) if sites else "",
                way="Browser",
                who=[{k: p[k] for k in ("kind", "id", "name", "initials", "members") if k in p} for p in listed],
                who_total=len(listed),
                needs_you=state["tone"] == "fail" or state["label"] == "Needs you",
                private=tool["audience"] is not None,
            )
        )
    return out


# ---- changes


class WebsiteToolCreate(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    sites: list[str] = Field(min_length=1, max_length=MAX_SITES * 2)
    central_only: bool = False


class WebsiteToolUpdate(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    sites: list[str] | None = Field(default=None, min_length=1, max_length=MAX_SITES * 2)
    central_only: bool | None = None
    # Who can see it, as on an API tool: null is Everyone, an audience Only listed. Left out, it stays as it is.
    audience: AccessAudience | None = None


class WebsiteAccountCreate(Contract):
    # Password spaces are significant; SecretStr keeps a value out of repr and logs. Both may be left out (or empty)
    # for a site a person signs in to by hand.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(default="", max_length=250)
    password: SecretStr | None = Field(default=None, max_length=10_000)
    totp: SecretStr | None = Field(default=None, max_length=2_000)
    holder: ID | None = None


class WebsiteAccountUpdate(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str | None = Field(default=None, max_length=250)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=10_000)
    # Left out: the 2FA key stays; null: it is removed; a value: it replaces the key.
    totp: SecretStr | None = Field(default=None, max_length=2_000)


class WebsiteAccountAccess(Contract):
    model_config = ConfigDict(extra="forbid")
    on: bool


def _secret_text(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


def _seal(c: Any, vault: Any, account_id: str, password: str, totp_text: str) -> tuple[Any, Any]:
    plain = json.dumps({"password": password, **({"totp": totp_text} if totp_text else {})})
    return cast(tuple[Any, Any], vault.cipher.encrypt(c, account_id, plain))


def _totp_checked(value: SecretStr | None) -> str:
    """The 2FA setup key as given, once `totp.parse_totp` reads it ('' for none); a refusal never echoes it."""
    text = _secret_text(value).strip()
    if text:
        try:
            T.parse_totp(text)
        except Problem:
            raise Problem("totp", "Enter the site's base32 2FA setup key or its otpauth://totp link.", 422) from None
    return text


def create_tool(c: Any, who: Any, body: WebsiteToolCreate, admins: Iterable[str]) -> dict[str, Any]:
    _require_admin(c, who, admins)
    sites = clean_sites(body.sites)
    name = (body.name or "").strip() or _host_of(sites[0])
    now = H.now()
    tid = str(api_tools._unique_id(c, api_tools._slug(name)))
    c.execute(
        "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at,"
        "kind,sites,central_only) VALUES(?,?,'','','bearer','','computer',?,?,?,?,?,?)",
        (tid, name, who.actor, now, now, KIND, json.dumps(sites), int(body.central_only)),
    )
    H.event(c, who.actor, "tool.created", tid, {"kind": KIND, "sites": sites, "central_only": body.central_only})
    return {"id": tid}


def update_tool(
    c: Any, who: Any, tool_id: str, body: WebsiteToolUpdate, admins: Iterable[str], auth: Any
) -> dict[str, Any]:
    asked = body.model_dump(exclude_none=True, exclude={"audience"})
    audience_set = "audience" in body.model_fields_set
    if asked or not audience_set or getattr(who, "role", "") != "owner":
        _require_admin(c, who, admins)
    tool = _readable(c, who, tool_id, admins, auth)
    if audience_set:
        value = body.audience.model_dump() if body.audience is not None else None
        api_tools.set_audience(c, who, tool, value, list(admins), auth)
    fields: dict[str, Any] = {}
    if body.name is not None and body.name.strip() and body.name.strip() != tool["name"]:
        fields["name"] = body.name.strip()
    if body.sites is not None:
        sites = clean_sites(body.sites)
        if sites != sites_of(tool):
            fields["sites"] = json.dumps(sites)
    if body.central_only is not None and int(body.central_only) != int(tool["central_only"]):
        fields["central_only"] = int(body.central_only)
    if fields:
        c.execute(
            "UPDATE tools SET " + ",".join(f"{k}=?" for k in fields) + ",updated_at=? WHERE id=?",
            (*fields.values(), H.now(), tool["id"]),
        )
        shown = {
            k: (json.loads(v) if k == "sites" else bool(v) if k == "central_only" else v) for k, v in fields.items()
        }
        H.event(c, who.actor, "tool.updated", tool["id"], shown)
        if "sites" in fields or "central_only" in fields:
            for account in _accounts(c, tool["id"]):
                _close(str(account["id"]))
    return _tool_view(_tool(c, tool["id"]))


def _holder(c: Any, value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("bot:"):
        bot = H.resolve_bot(c, text)
        if not bot:
            raise Problem("holder", "Choose a registered person or bot.", 422)
        return "bot:" + str(bot["id"])
    if text.startswith("human:") and c.execute("SELECT 1 FROM humans WHERE id=?", (text[6:],)).fetchone():
        return text
    raise Problem("holder", "Choose a registered person or bot.", 422)


def add_account(
    c: Any, who: Any, vault: Any, tool_id: str, body: WebsiteAccountCreate, admins: Iterable[str], auth: Any
) -> dict[str, Any]:
    """A shared account (credential administrators) or an own one: an administrator's for anyone, a person's for
    themselves or a bot they manage. One own account per holder per tool."""
    tool = _readable(c, who, tool_id, admins, auth)
    holder = _holder(c, body.holder)
    if not (_admin(c, who, admins) or (holder and _manages(c, who, holder, auth))):
        raise Problem(
            "forbidden",
            "Ask a credential administrator to add a shared account, or add your own account or one for a bot you manage.",
            403,
        )
    if (
        holder
        and c.execute(
            "SELECT 1 FROM credentials WHERE tool_id=? AND kind=? AND holder=?", (tool["id"], LOGIN, holder)
        ).fetchone()
    ):
        raise Problem(
            "own_account_exists", "That teammate already has an account here; use Replace login instead.", 409
        )
    password = _secret_text(body.password)
    totp_text = _totp_checked(body.totp)
    aid, now = H.new_id(), H.now()
    ciphertext, nonce = _seal(c, vault, aid, password, totp_text)
    username = body.username.strip()
    c.execute(
        "INSERT INTO credentials(id,name,username,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
        "updated_by,created_by,tool_id,has_totp,holder,has_password) VALUES(?,?,?,?,'','',?,?,?,?,?,?,?,?,?,?,?)",
        (
            aid,
            username or str(tool["name"]),
            username,
            LOGIN,
            ciphertext,
            nonce,
            "website:" + str(tool["id"]),
            now,
            now,
            who.actor,
            who.actor,
            tool["id"],
            int(bool(totp_text)),
            holder,
            int(bool(password)),
        ),
    )
    H.event(c, who.actor, "credential.created", aid, {"kind": LOGIN, "tool": tool["id"], "own": bool(holder)})
    H.event(c, who.actor, "tool.key_added", tool["id"], {"credential": aid, "own": bool(holder)})
    return {"id": aid}


def update_account(
    c: Any,
    who: Any,
    vault: Any,
    tool_id: str,
    account_id: str,
    body: WebsiteAccountUpdate,
    admins: Iterable[str],
    auth: Any,
) -> dict[str, Any]:
    """Replace login: the username, the password (added to an account that had none), the 2FA key (null removes it).
    Whoever may manage the account."""
    tool = _readable(c, who, tool_id, admins, auth)
    account = _account(c, tool["id"], account_id)
    if not can_manage(c, who, account, admins, auth):
        raise Problem("forbidden", "Ask a credential administrator or the account's holder to replace this login.", 403)
    totp_set = "totp" in body.model_fields_set
    changed_secret = body.password is not None or totp_set
    username = account["username"] if body.username is None else body.username.strip()
    ciphertext, nonce, has_totp = account["ciphertext"], account["nonce"], account["has_totp"]
    has_password = 1 if body.password is not None else account["has_password"]
    if changed_secret:
        old = cast(dict[str, str], vault.open_login(c, account))
        password = _secret_text(body.password) if body.password is not None else old["password"]
        totp_text = (_totp_checked(body.totp) if body.totp is not None else "") if totp_set else old["totp"]
        ciphertext, nonce = _seal(c, vault, account["id"], password, totp_text)
        has_totp = int(bool(totp_text))
    c.execute(
        "UPDATE credentials SET username=?,name=?,ciphertext=?,nonce=?,has_totp=?,has_password=?,revision=revision+1,"
        "updated_at=?,updated_by=? WHERE id=?",
        (
            username,
            username or str(tool["name"]),
            ciphertext,
            nonce,
            has_totp,
            has_password,
            H.now(),
            who.actor,
            account["id"],
        ),
    )
    H.event(
        c,
        who.actor,
        "credential.updated",
        account["id"],
        {
            "kind": LOGIN,
            "tool": tool["id"],
            "password_changed": body.password is not None,
            "totp_changed": totp_set,
        },
    )
    return {"id": account["id"]}


def delete_account(c: Any, who: Any, tool_id: str, account_id: str, admins: Iterable[str], auth: Any) -> dict[str, Any]:
    tool = _readable(c, who, tool_id, admins, auth)
    account = _account(c, tool["id"], account_id)
    if not can_manage(c, who, account, admins, auth):
        raise Problem(
            "forbidden", "Ask a credential administrator or the account's holder to remove this account.", 403
        )
    api_tools._drop_key(c, who, account["id"])
    _close(str(account["id"]))
    with _spent_lock:
        _spent.pop(str(account["id"]), None)
    return {"deleted": True, "id": account["id"]}


def set_access(
    c: Any,
    who: Any,
    vault: Any,
    tool_id: str,
    account_id: str,
    subject: str,
    on: bool,
    admins: Iterable[str],
    auth: Any,
) -> dict[str, Any]:
    """Give or take a shared account from a person or a bot, through the vault's grant rules."""
    tool = _readable(c, who, tool_id, admins, auth)
    account = _account(c, tool["id"], account_id)
    if account["holder"]:
        raise Problem(
            "own_account",
            "This account belongs to its holder alone; add a shared account to grant someone else access.",
            409,
        )
    subject = str(api_tools.subject_of(c, subject))
    if not subject.startswith(("human:", "bot:", GROUP)):
        raise Problem("subject", "Choose a registered person, bot or group.", 422)
    if subject.startswith(GROUP):
        # Only a credential administrator gives a group a login or takes it away, either way before anything changes.
        if not _admin(c, who, admins):
            raise Problem("forbidden", "Ask a credential administrator to change access to this account.", 403)
        if subject[len(GROUP) :] not in _org_groups(c):
            raise Problem("subject", "Choose a registered person, bot or group.", 422)
    try:
        if on:
            vault.grant(c, who, account["id"], subject)
        else:
            grants = c.execute(
                "SELECT * FROM credential_grants WHERE credential_id=? AND subject=? AND revoked_at IS NULL",
                (account["id"], subject),
            ).fetchall()
            for grant in grants:
                vault.revoke_authority(c, who, account["id"], grant)
            api_tools._revoke(c, who, account["id"], subject)
            if grants:
                _close(str(account["id"]))
    except Problem as refused:
        code = str(cast(Any, refused).code)
        if code == "forbidden":
            raise Problem(
                "forbidden", "Ask a credential administrator to change access to this account.", 403
            ) from None
        if code == "subject":
            raise Problem("subject", "Choose a registered person, bot or group.", 422) from None
        raise
    # The account's whole list, as the caller sees it, so a dialog repaints member counts and "Also in" from it.
    admin = _admin(c, who, admins)
    view = _account_view(c, who, account, admin, _names(c, _users(c, account)), admins, auth)
    return {
        "subject": subject,
        "on": vaults.effective_grant(c, account["id"], subject) is not None,
        "access": view["access"] if view is not None else [],
    }


def groups_view(c: Any, who: Any, admins: Iterable[str]) -> list[dict[str, Any]]:
    """`GET /website-tools/groups`: each group with its member count (nested groups included), for the Who can use it
    picker. Credential administrators only: they alone grant a login to a group."""
    _require_admin(c, who, admins)
    groups = _org_groups(c)
    rows = [{"id": gid, "name": _group_name(groups, gid), "members": len(group_members(c, gid))} for gid in groups]
    return sorted(rows, key=lambda r: (r["name"].lower(), r["id"]))


def forget_group(c: Any, actor: str, gid: str) -> None:
    """A deleted group's login grants go as a removal does: each revoked with a credential.revoked event, and the
    account's open browser sessions closed (backend/teammate_access.py forget_group calls this)."""
    subject = GROUP + gid
    accounts = [
        str(r["credential_id"])
        for r in c.execute(
            "SELECT DISTINCT g.credential_id FROM credential_grants g JOIN credentials k ON k.id=g.credential_id "
            "WHERE g.subject=? AND g.revoked_at IS NULL AND k.kind=?",
            (subject, LOGIN),
        ).fetchall()
    ]
    who = _Actor(actor)
    for account_id in accounts:
        api_tools._revoke(c, who, account_id, subject)
        _close(account_id)


class _Actor:
    def __init__(self, actor: str) -> None:
        self.actor = actor


def current_code(
    c: Any, who: Any, vault: Any, tool_id: str, account_id: str, admins: Iterable[str], auth: Any
) -> dict[str, Any]:
    """The account's current 2FA code, to a signed-in person who uses it or a credential administrator. Logged as
    `code`, without the code."""
    if not vaults.signed_in_person(who):
        raise Problem("forbidden", "Ask a person who uses this account to open its code while signed in to Tico.", 403)
    tool = _readable(c, who, tool_id, admins, auth)
    account = _account(c, tool["id"], account_id)
    if not (can_use(c, who.actor, account) or _admin(c, who, admins)):
        raise Problem("forbidden", "You do not use this account; ask an admin to share it in Tools.", 403)
    found = _totp_of(c, vault, account)
    spend(account["id"])
    code = T.code(found)
    log(c, _picked(tool, account), who, "code", _host_of(sites_of(tool)[0]) if sites_of(tool) else "", "own")
    return {"code": code, "expires_in": T.expires_in(found)}


# ---- routes


def install(app: "FastAPI", store: Any, auth: Any, settings: Any) -> None:
    """The people routes. The vault is read from app.state at each call (a test swaps its cipher after start)."""
    _installed.update(settings=store.settings, auth=auth)

    def vault() -> Any:
        return app.state.vault

    def admins() -> list[str]:
        return list(vault().admins)

    def change(request: Request, body: Any, authorize: Callable[[Any, Any], None], fn: Callable[[Any], Any]) -> Any:
        return vault().change(
            request.state.identity, request.url.path, request.headers.get("idempotency-key"), body, authorize, fn
        )

    def person(c: Any, w: Any) -> None:
        if not _person(w):
            raise Problem("forbidden", "Ask a person to change this website tool while signed in to Tico.", 403)

    def manager(c: Any, w: Any) -> None:
        _require_admin(c, w, admins())

    def setter(c: Any, w: Any) -> None:
        if w.role != "owner":
            manager(c, w)

    def plain(request: Request, fn: Callable[[Any, Any], Any]) -> Any:
        """A delete, like the vault's: no receipt (a repeat finds nothing and says so)."""
        with store.transaction() as c:
            vaults.validate_identity(c, request.state.identity)
            return fn(c, request.state.identity)

    @app.post("/api/v2/website-tools")
    def create_website_tool(request: Request, body: WebsiteToolCreate) -> Any:
        """Add a website tool (credential administrators): its sites and whether only the browser host opens it."""
        who = request.state.identity
        return change(request, body, manager, lambda c: create_tool(c, who, body, admins()))

    @app.get("/api/v2/website-tools/groups")
    def website_tool_groups(request: Request) -> Any:
        """Each group with its member count, for the Who can use it picker (credential administrators)."""
        with store.read_transaction() as c:
            return groups_view(c, request.state.identity, admins())

    @app.get("/api/v2/website-tools/{tool_id}")
    def get_website_tool(request: Request, tool_id: str) -> Any:
        with store.read_transaction() as c:
            return detail(c, request.state.identity, tool_id, admins(), auth)

    @app.patch("/api/v2/website-tools/{tool_id}")
    def update_website_tool(request: Request, tool_id: str, body: WebsiteToolUpdate) -> Any:
        who = request.state.identity
        return change(request, body, setter, lambda c: update_tool(c, who, tool_id, body, admins(), auth))

    @app.delete("/api/v2/website-tools/{tool_id}")
    def delete_website_tool(request: Request, tool_id: str) -> Any:
        """The tool, its accounts and their grants, and its Activity."""

        def work(c: Any, who: Any) -> Any:
            _require_admin(c, who, admins())
            _readable(c, who, tool_id, admins(), auth)
            accounts = _accounts(c, tool_id)
            answer = api_tools.delete_tool(c, who, tool_id, admins(), kind=KIND)
            for account in accounts:
                _close(str(account["id"]))
            return answer

        return plain(request, work)

    @app.post("/api/v2/website-tools/{tool_id}/accounts")
    def add_website_account(request: Request, tool_id: str, body: WebsiteAccountCreate) -> Any:
        who = request.state.identity
        return change(
            request,
            api_tools._Receipt(body),
            person,
            lambda c: add_account(c, who, vault(), tool_id, body, admins(), auth),
        )

    @app.patch("/api/v2/website-tools/{tool_id}/accounts/{account_id}")
    def update_website_account(request: Request, tool_id: str, account_id: str, body: WebsiteAccountUpdate) -> Any:
        who = request.state.identity
        receipt = api_tools._Receipt(body, totp_set="totp" in body.model_fields_set)
        return change(
            request,
            receipt,
            person,
            lambda c: update_account(c, who, vault(), tool_id, account_id, body, admins(), auth),
        )

    @app.delete("/api/v2/website-tools/{tool_id}/accounts/{account_id}")
    def delete_website_account(request: Request, tool_id: str, account_id: str) -> Any:
        return plain(request, lambda c, who: delete_account(c, who, tool_id, account_id, admins(), auth))

    @app.put("/api/v2/website-tools/{tool_id}/accounts/{account_id}/access/{subject}")
    def set_website_account_access(
        request: Request, tool_id: str, account_id: str, subject: str, body: WebsiteAccountAccess
    ) -> Any:
        who = request.state.identity
        return change(
            request,
            api_tools._Receipt(body, subject=subject),
            person,
            lambda c: set_access(c, who, vault(), tool_id, account_id, subject, body.on, admins(), auth),
        )

    @app.get("/api/v2/website-tools/{tool_id}/accounts/{account_id}/code")
    def website_account_code(request: Request, tool_id: str, account_id: str) -> Any:
        """The current 2FA code, to a signed-in person who uses the account. Logged, without the code."""
        with store.transaction() as c:
            vaults.validate_identity(c, request.state.identity)
            return current_code(c, request.state.identity, vault(), tool_id, account_id, admins(), auth)

    @app.get("/api/v2/website-tools/{tool_id}/activity")
    def website_tool_activity(
        request: Request,
        tool_id: str,
        cursor: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=ACTIVITY_MAX),
    ) -> Any:
        with store.read_transaction() as c:
            return activity(c, request.state.identity, tool_id, admins(), auth, cursor, limit)
