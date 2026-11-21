# pyright: strict
"""Remote MCP servers as Tools (docs/credential-vault.md#mcp-servers): Tools › Add › MCP server.

An MCP tool is an API tool (backend/api_tools.py) with `mcp_url`: the server's address. Its key is reached two ways:
- **Token**: a pasted personal access token or API key, stored as the tool's key in the vault, sent as
  `Authorization: Bearer <token>` or as the whole value of a header the person names (`auth_scheme` `header:<Name>`).
- **OAuth sign-in**: authorization code with PKCE (RFC 7636), the client registered by dynamic client registration
  (RFC 7591) when the server's authorization server offers it, else the client id and secret a person gives. The
  sign-in is a `credentials` row of kind `oauth` on the tool, its access and refresh tokens sealed with the vault's
  cipher; it belongs to the company and records who signed in (`updated_by`). Its tokens are refreshed here only, and
  never leave the server: an OAuth tool is always on the proxy.

`probe` finds out which ways a server offers, on the server and through backend/egress.py's rules (https to a public
address, pinned): an `initialize` without a sign-in; a 401 or 403 whose `WWW-Authenticate: Bearer resource_metadata=`
(RFC 9728), or `/.well-known/oauth-protected-resource`, names an authorization server, whose metadata (RFC 8414, or
OpenID discovery) has the endpoints, means OAuth is offered. A token is always offered on a 401 or 403.

A check is `initialize` and `tools/list` (every page, to LIST_PAGES): Working, Key rejected or Check failed as an API
tool's, and the names it found with the ones marked `readOnlyHint` (`tools.mcp_tools`).

A granted MCP tool reaches a run with no bot.yaml entry: `credential-runtime` adds `mcp_servers` (`runtime_servers`),
one per held key whose tool has an MCP address, {name, tool, url, transport, headers, env, proxied, upstream}. On the proxy the
url is the tool's proxy address with the server's path and the header carries the run's proxy token as `${ENV}`; the
proxy puts the current access token (refreshed when needed) or the stored token in its place. On computers the url is
the server's own and the header's `${ENV}` is the key itself. The runner merges these with bot.yaml's servers, bot.yaml
winning for the same connection (address and variable), any other taking a name of its own (runner-rs/src/mcp.rs
`merge`).

Legacy SSE servers (the transport before streamable HTTP) are not added here: they stay a bot.yaml `tools:` entry.
"""

import base64
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from typing import Any, NamedTuple, cast
from urllib.parse import SplitResult, quote, urlencode, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse
from pydantic import Field, SecretStr

from . import egress, mcp_client, oauth_refresh, outbound_tls
from . import store as _store
from . import tool_access as TA
from . import tool_services as _tool_services
from .models import Contract
from .store import Problem, encode

Json = dict[str, Any]
# Untyped helpers, held as Any so this strict module's own types stay checked.
H: Any = _store.H
TS: Any = _tool_services
_egress: Any = egress  # its check and address_for, untyped


def _api() -> Any:
    from . import api_tools

    return api_tools


def _auth() -> Any:
    from . import auth

    return auth


log = logging.getLogger(__name__)

CALLBACK_PATH = "/api/v2/tool-oauth/callback"
OAUTH = "oauth"  # a sign-in's credential kind
TIMEOUT = 15.0  # seconds for one request to an MCP or OAuth server
MAX_ANSWER = 4 * 1024 * 1024
STATE_TTL = 15 * 60  # a sign-in started and not finished is forgotten after this
REFRESH_EARLY = 60  # an access token this close to its end is refreshed before use
STALE_TRIES = 3  # a refresh whose tokens were dropped (the sign-in changed under it) is read and tried again this often
LIST_PAGES = 10
NAMES_MAX = 500
SIGN_IN_AGAIN = "Sign in again"
HEADER = r"^$|^[A-Za-z0-9][A-Za-z0-9-]{0,59}$"
# Tests set this to an httpx transport: every request here then goes through it, its address still checked and pinned.
TRANSPORT = None


class Failed(Exception):
    """No usable answer: `reason` is fixed words, never the provider's text or a key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SignInNeeded(Failed):
    def __init__(self) -> None:
        super().__init__(SIGN_IN_AGAIN)


# ---- sending


def send(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Json | None = None,
    data: dict[str, str] | None = None,
) -> httpx.Response:
    """One request to an MCP or OAuth server: only to a public https address, pinned to the address it resolved to, no
    redirect followed, no proxy from the environment, the answer read whole up to MAX_ANSWER. egress.Blocked for an
    address Tico does not send to; Failed when there is no answer."""
    parts: SplitResult = _egress.check(url)
    address: str = _egress.address_for(parts, budget=TIMEOUT)
    pinned = httpx.URL(url).copy_with(host=address)
    host = str(parts.hostname) + (f":{parts.port}" if parts.port else "")
    sent: dict[str, str] = {"User-Agent": "Tico", **(headers or {}), "Host": host}
    try:
        with httpx.Client(
            transport=TRANSPORT,
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            verify=outbound_tls.context(trust_env=False),
        ) as client:
            with client.stream(
                method, pinned, headers=sent, json=json_body, data=data, extensions={"sni_hostname": parts.hostname}
            ) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_ANSWER:
                        raise Failed("answer too large")
                kept = [
                    (k, v)
                    for k, v in response.headers.items()
                    if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
                ]
                return httpx.Response(response.status_code, headers=kept, content=bytes(body))
    except httpx.TimeoutException:
        raise Failed("timed out") from None
    except httpx.HTTPError:
        raise Failed("no answer") from None


def _obj(value: object) -> Json:
    """`value` when it is a JSON object, else {}."""
    return cast(Json, value) if isinstance(value, dict) else {}


def _strs(value: object) -> list[str]:
    """The strings of `value` when it is a JSON list, else []."""
    return [v for v in cast(list[object], value) if isinstance(v, str)] if isinstance(value, list) else []


def _json(response: httpx.Response) -> Json | None:
    try:
        value: object = response.json()
    except ValueError:
        return None
    return cast(Json, value) if isinstance(value, dict) else None


def _get_json(url: str) -> Json | None:
    try:
        response = send("GET", url, headers={"Accept": "application/json"})
    except (egress.Blocked, Failed):
        return None
    return _json(response) if response.status_code == 200 else None


# ---- addresses and discovery


def clean_url(url: str) -> str:
    """The server's address as Tools keeps it: https to a public name, no sign-in, query or fragment. 422 otherwise."""
    url = str(url or "").strip()
    try:
        parts: SplitResult = _egress.check(url)
    except egress.Blocked as exc:
        raise Problem("url", f"Address: {exc.reason}", 422) from None
    if parts.scheme != "https":
        raise Problem("url", "Address: an MCP server in Tools is an https address", 422)
    if parts.query or parts.fragment:
        raise Problem("url", "Address: no query or fragment (a key goes in a header, never in the address)", 422)
    if parts.path.rstrip("/").endswith("/sse"):
        raise Problem(
            "transport",
            "This looks like an older SSE server. Tools connects streamable HTTP servers; add an SSE one in the bot's "
            "bot.yaml tools",
            422,
        )
    return url


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _well_known(url: str, name: str) -> list[str]:
    """RFC 8414 / 9728 places for `name` under `url`: with the path inserted after the well-known part, then at the root."""
    path = urlsplit(url).path.rstrip("/")
    root = origin(url) + "/.well-known/" + name
    return [root + path, root] if path else [root]


def _https(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 1000:
        return False
    try:
        return _egress.check(value).scheme == "https"
    except egress.Blocked:
        return False


def discover(url: str, www_authenticate: str = "") -> Json | None:
    """The server's OAuth setup, or None when it offers none: {issuer, authorization_endpoint, token_endpoint,
    registration_endpoint, scope, resource, auth_methods}."""
    told = mcp_client.challenge(www_authenticate)
    resource: Json | None = None
    for candidate in ([told["resource_metadata"]] if _https(told.get("resource_metadata")) else []) + _well_known(
        url, "oauth-protected-resource"
    ):
        found = _get_json(candidate)
        if found and isinstance(found.get("authorization_servers"), list) and found["authorization_servers"]:
            resource = found
            break
    issuers = [s for s in _strs(resource["authorization_servers"]) if _https(s)][:3] if resource else [origin(url)]
    for issuer in issuers:
        places = _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration")
        places.append(issuer.rstrip("/") + "/.well-known/openid-configuration")
        for place in dict.fromkeys(places):
            meta = _get_json(place)
            if not meta or not _https(meta.get("authorization_endpoint")) or not _https(meta.get("token_endpoint")):
                continue
            methods = _strs(meta.get("code_challenge_methods_supported"))
            if methods and "S256" not in methods:
                continue  # PKCE with S256 is required
            scopes = told.get("scope") or " ".join(_strs((resource or {}).get("scopes_supported")))
            auth_methods = _strs(meta.get("token_endpoint_auth_methods_supported"))
            return {
                "issuer": meta.get("issuer") if isinstance(meta.get("issuer"), str) else issuer,
                "authorization_endpoint": meta["authorization_endpoint"],
                "token_endpoint": meta["token_endpoint"],
                "registration_endpoint": meta["registration_endpoint"]
                if _https(meta.get("registration_endpoint"))
                else "",
                "scope": scopes[:500],
                "resource": (resource or {}).get("resource") if _https((resource or {}).get("resource")) else url,
                "auth_methods": auth_methods[:10],
            }
    return None


def probe(url: str) -> tuple[Json, Json | None]:
    """(what a person is offered, the OAuth setup or None) for a server address: {url, transport, server, open, token,
    oauth, dcr}. Network: call it outside a transaction."""
    url = clean_url(url)
    hello = mcp_client.initialize()
    try:
        first = send("POST", url, headers=mcp_client.headers(), json_body=hello)
    except egress.Blocked as exc:
        raise Problem("url", f"Address: {exc.reason}", 422) from None
    except Failed as exc:
        raise Problem(
            "mcp_unreachable", f"No answer from that address ({exc.reason}). Check it and try again", 422
        ) from None
    found: Json = {
        "url": url,
        "transport": "http",
        "server": "",
        "open": False,
        "token": True,
        "oauth": False,
        "dcr": False,
    }
    if 200 <= first.status_code < 300:
        try:
            answer = mcp_client.message(first.text, first.headers.get("content-type", ""), hello["id"])
        except mcp_client.AnswerError:
            answer = None
        if answer is None or "result" not in answer:
            raise Problem("mcp_answer", "That address answered, but not as an MCP server", 422)
        info = _obj(_obj(answer["result"]).get("serverInfo"))
        found.update(open=True, server=str(info.get("name") or "")[:80])
        return found, None
    if first.status_code not in (401, 403):
        raise Problem("mcp_answer", f"That address answered {first.status_code}, not as an MCP server", 422)
    meta = discover(url, first.headers.get("www-authenticate", ""))
    if meta:
        found.update(oauth=True, dcr=bool(meta["registration_endpoint"]))
    return found, meta


# ---- the tool's sign-in and its tokens


def env_for(tool_id: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", tool_id.upper()).strip("_") + "_MCP_TOKEN"


def callback_url(settings: Any) -> str:
    return settings.public_url.rstrip("/") + CALLBACK_PATH


def _secret_context(tool_id: str) -> str:
    return "tools.mcp_client_secret:" + tool_id


def _meta(tool: Any) -> Json | None:
    try:
        value: object = json.loads(tool["mcp_oauth"] or "null")
    except (TypeError, ValueError):
        return None
    return cast(Json, value) if isinstance(value, dict) else None


def _client_secret(c: Any, vault: Any, tool: Any) -> str:
    sealed = tool["mcp_client_secret"]
    return vault.cipher.unseal(c, _secret_context(tool["id"]), sealed) if sealed else ""


def _token_request(meta: Json, secret: str, data: dict[str, str]) -> httpx.Response:
    """POST to the token endpoint with the client's own authentication: none (PKCE alone), else the client secret as
    HTTP Basic, or in the body when the server takes only that."""
    data = dict(data)
    headers = {"Accept": "application/json"}
    methods = _strs(meta.get("auth_methods"))
    if secret and not ("client_secret_post" in methods and "client_secret_basic" not in methods):
        pair = quote(meta["client_id"], safe="") + ":" + quote(secret, safe="")
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    else:
        data["client_id"] = meta["client_id"]
        if secret:
            data["client_secret"] = secret
    if meta.get("resource"):
        data["resource"] = meta["resource"]
    return send("POST", meta["token_endpoint"], headers=headers, data=data)


def _record_failure(store: Any, tool_id: str, credential_id: str | None, code: int | None, detail: str) -> None:
    with store.transaction() as c:
        c.execute(
            "INSERT INTO tool_checks(id,tool_id,credential_id,ok,status_code,detail,checked_at) VALUES(?,?,?,?,?,?,?)",
            (H.new_id(), tool_id, credential_id, 0, code, detail, H.now()),
        )


class _Turn(NamedTuple):
    """A claim on one sign-in's refresh: its tool, the client secret, the tokens read with the claim and the claim's
    holder."""

    tool: Any
    secret: str
    held: Json
    holder: str


def _fresh(held: Json) -> bool:
    return not held.get("expires_at") or held["expires_at"] > time.time() + REFRESH_EARLY


def _stored_refresh(store: Any, vault: Any, credential_id: str) -> str | None:
    """The refresh token the sign-in stores now, or None when it has none."""
    with store.transaction() as c:
        row = c.execute("SELECT * FROM credentials WHERE id=? AND kind=?", (credential_id, OAUTH)).fetchone()
        if row is None or row["ciphertext"] is None:
            return None
        return json.loads(vault.cipher.decrypt(c, row)).get("refresh_token")


def access_token(store: Any, vault: Any, credential_id: str, force: bool = False) -> str:
    """The sign-in's current access token, refreshed first when it ends within REFRESH_EARLY seconds (or `force`).
    SignInNeeded when the server will not refresh it (and a failed check says Sign in again); Failed when it did not
    answer. Blocking: call it off the event loop.

    Refreshes of one sign-in run one at a time, across processes (backend/oauth_refresh.py). A vendor that rotates
    refresh tokens accepts each once, so the refresh token is read and claimed in one write transaction, sent with no
    transaction open, and the new tokens are stored only while the stored refresh token is still the one sent. A
    caller that finds a refresh in progress waits for it and uses the tokens it stored.
    """
    sign_in = "credential:" + credential_id
    deadline = time.monotonic() + oauth_refresh.WAIT_SECONDS
    for _ in range(STALE_TRIES):
        turn = _take_turn(store, vault, credential_id, sign_in, force, deadline)
        if isinstance(turn, str):
            return turn
        token = _refresh(store, vault, credential_id, sign_in, turn)
        if token is not None:
            return token
    raise Failed("the sign-in changed while it was refreshed; try again")


def _take_turn(store: Any, vault: Any, credential_id: str, sign_in: str, force: bool, deadline: float) -> str | _Turn:
    """The fresh access token when no refresh is needed, else the claim to refresh. Waits while another holder's
    refresh lasts; Failed when it does not end in time."""
    while True:
        with store.transaction() as c:
            row = c.execute("SELECT * FROM credentials WHERE id=? AND kind=?", (credential_id, OAUTH)).fetchone()
            tool = c.execute("SELECT * FROM tools WHERE id=?", ((row or {"tool_id": ""})["tool_id"],)).fetchone()
            if row is None or tool is None or row["ciphertext"] is None:
                raise SignInNeeded()
            held = json.loads(vault.cipher.decrypt(c, row))  # decrypt may reseal an old value in place
            if held.get("access_token") and _fresh(held) and not force:
                return held["access_token"]
            holder = oauth_refresh.take(c, sign_in)
            if holder is not None:
                return _Turn(tool, _client_secret(c, vault, tool), held, holder)
        if time.monotonic() > deadline:
            raise Failed("the sign-in is being refreshed; try again")
        time.sleep(oauth_refresh.POLL_SECONDS)


def _refresh(store: Any, vault: Any, credential_id: str, sign_in: str, turn: _Turn) -> str | None:
    """The new access token, or None when the sign-in changed during the refresh (its tokens are read again)."""
    try:
        return _send_refresh(store, vault, credential_id, turn)
    finally:
        oauth_refresh.release(store, sign_in, turn.holder)


def _send_refresh(store: Any, vault: Any, credential_id: str, turn: _Turn) -> str | None:
    tool, held = turn.tool, turn.held
    meta = _meta(tool)
    sent = held.get("refresh_token")
    if not sent or not meta or not meta.get("client_id"):
        _record_failure(store, tool["id"], credential_id, 401, SIGN_IN_AGAIN)
        raise SignInNeeded()
    response = _token_request(meta, turn.secret, {"grant_type": "refresh_token", "refresh_token": sent})
    if response.status_code in (400, 401, 403):
        if _stored_refresh(store, vault, credential_id) != sent:
            return None  # refused because another refresh has stored a newer token: the stored tokens are read again
        _record_failure(store, tool["id"], credential_id, 401, SIGN_IN_AGAIN)
        raise SignInNeeded()
    if response.status_code >= 300:
        raise Failed(f"the sign-in server answered {response.status_code}")
    try:
        new = mcp_client.tokens(_json(response), time.time(), held)
    except mcp_client.AnswerError:
        raise Failed("the sign-in server's answer had no token") from None
    with store.transaction() as c:
        row = c.execute("SELECT * FROM credentials WHERE id=? AND kind=?", (credential_id, OAUTH)).fetchone()
        if row is None or json.loads(vault.cipher.decrypt(c, row)).get("refresh_token") != sent:
            return None  # the claim lapsed during the refresh and the sign-in has changed: these tokens are dropped
        ciphertext, nonce = vault.cipher.encrypt(c, credential_id, encode(new))
        c.execute(
            "UPDATE credentials SET ciphertext=?, nonce=?, updated_at=? WHERE id=?",
            (ciphertext, nonce, H.now(), credential_id),
        )
    return new["access_token"]


def store_sign_in(c: Any, vault: Any, tool: Any, actor: str, tokens: Json) -> str:
    """Keep a finished sign-in as the tool's Read and write key (kind `oauth`), replacing the tokens of an earlier one so
    its grants stay. The credential id."""
    existing = c.execute("SELECT * FROM credentials WHERE tool_id=? AND kind=?", (tool["id"], OAUTH)).fetchone()
    other = c.execute(
        "SELECT 1 FROM credentials WHERE tool_id=? AND kind<>? AND access_level='read_write'", (tool["id"], OAUTH)
    ).fetchone()
    if other is not None:
        raise Problem(
            "key_level", f"{tool['name']} already has a Read and write key. Delete it in Tools > {tool['name']}", 409
        )
    cid = existing["id"] if existing else H.new_id()
    ciphertext, nonce = vault.cipher.encrypt(c, cid, encode(tokens))
    now = H.now()
    if existing:
        c.execute(
            "UPDATE credentials SET ciphertext=?, nonce=?, revision=revision+1, updated_at=?, updated_by=? WHERE id=?",
            (ciphertext, nonce, now, actor, cid),
        )
    else:
        c.execute(
            "INSERT INTO credentials(id,name,username,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
            "updated_by,created_by,tool_id,access_level) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'read_write')",
            (
                cid,
                f"{tool['name']} sign-in",
                "",
                OAUTH,
                env_for(tool["id"]),
                "",
                ciphertext,
                nonce,
                "tool:" + tool["id"],
                now,
                now,
                actor,
                actor,
                tool["id"],
            ),
        )
    H.event(c, actor, "tool.signed_in", tool["id"], {"credential": cid})
    return cid


# ---- checks


def _rpc(
    tool: Any, token: str, session: dict[str, str], method: str, params: Json | None = None, notification: bool = False
) -> tuple[httpx.Response, Json | None]:
    body = mcp_client.rpc(method, params, notification)
    scheme = tool["auth_scheme"] or "bearer"
    response = send("POST", tool["mcp_url"], headers=mcp_client.headers(token, session, scheme), json_body=body)
    if response.headers.get("mcp-session-id"):
        session["id"] = response.headers["mcp-session-id"][:500]
    if notification or response.status_code >= 300:
        return response, None
    try:
        answer = mcp_client.message(response.text, response.headers.get("content-type", ""), body["id"])
    except mcp_client.AnswerError:
        return response, None
    return response, answer


def list_tools(tool: Any, token: str) -> tuple[tuple[bool, int | None, str], list[Json] | None]:
    """((ok, status_code, detail), [{name, read_only}] or None) for one key: initialize, then every page of tools/list."""
    session: dict[str, str] = {}
    try:
        response, answer = _rpc(tool, token, session, "initialize", mcp_client.initialize("tico-check")["params"])
        code = response.status_code
        if code == 401:  # only a 401 says the key is rejected; a 403 is a check failure (no MCP without scope)
            return (False, code, "Key rejected"), None
        if code >= 300:
            return (False, code, f"Check failed: {code}"), None
        if answer is None or not isinstance(answer.get("result"), dict):
            return (False, code, "Check failed: not an MCP answer"), None
        version = _obj(answer["result"]).get("protocolVersion")
        if isinstance(version, str) and len(version) < 40:
            session["version"] = version
        _rpc(tool, token, session, "notifications/initialized", notification=True)
        names: list[Json] = []
        cursor = ""
        for _ in range(LIST_PAGES):
            response, answer = _rpc(tool, token, session, "tools/list", {"cursor": cursor} if cursor else {})
            result: object = (answer or {}).get("result")
            if response.status_code >= 300 or not isinstance(result, dict):
                return (False, response.status_code, "Check failed: tools/list"), None
            page = cast(Json, result)
            for item in cast(list[object], page.get("tools") or []):
                entry = _obj(item)
                name = entry.get("name")
                if isinstance(name, str) and len(names) < NAMES_MAX:
                    hint = _obj(entry.get("annotations")).get("readOnlyHint")
                    names.append({"name": name[:200], "read_only": hint is True})
            following = page.get("nextCursor")
            if not isinstance(following, str) or not following:
                break
            cursor = following
        return (True, 200, "Working"), names
    except egress.Blocked:
        return (False, None, "Check failed: address refused"), None
    except Failed as exc:
        return (False, None, "Check failed: " + exc.reason), None


def check(store: Any, vault: Any, tool_id: str) -> list[Json]:
    """api_tools.check_tool for an MCP tool: initialize and tools/list with each stored key (a sign-in's access token,
    refreshed when needed); a tool_checks row each, worst first, and the tools it found on the tool."""
    with store.transaction() as c:
        tool = c.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone()
        keys = c.execute(
            "SELECT * FROM credentials WHERE tool_id=? AND ciphertext IS NOT NULL ORDER BY access_level, id", (tool_id,)
        ).fetchall()
        stored = [(k, None if k["kind"] == OAUTH else vault.cipher.decrypt(c, k)) for k in keys]
    if not stored:
        raise Problem(
            "check",
            f"{tool['name']} has no stored key. "
            + ("Sign in" if _meta(tool) else "Add one")
            + f" in Tools > {tool['name']}",
            409,
        )
    results: list[tuple[Any, bool, int | None, str]] = []
    listed: list[Json] | None = None
    for key, secret in stored:
        if secret is None:
            try:
                secret = access_token(store, vault, key["id"])
            except Failed as exc:
                results.append((key, False, 401 if isinstance(exc, SignInNeeded) else None, exc.reason))
                continue
        (ok, code, text), names = list_tools(tool, secret)
        if ok and names is not None and listed is None:
            listed = names
        results.append((key, ok, code, text))
    stored = secret = None  # the values leave scope before anything is written
    rows: list[Json] = []
    with store.transaction() as c:
        for key, ok, code, text in results:
            row: Json = {
                "id": H.new_id(),
                "tool_id": tool_id,
                "credential_id": key["id"],
                "ok": int(ok),
                "status_code": code,
                "detail": text,
                "checked_at": H.now(),
            }
            c.execute(
                "INSERT INTO tool_checks(id,tool_id,credential_id,ok,status_code,detail,checked_at) "
                "VALUES(:id,:tool_id,:credential_id,:ok,:status_code,:detail,:checked_at)",
                row,
            )
            rows.append(row | {"ok": ok})
        if listed is not None:
            found = {
                "names": [t["name"] for t in listed],
                "read_only": [t["name"] for t in listed if t["read_only"]],
                "listed_at": H.now(),
            }
            c.execute("UPDATE tools SET mcp_tools=? WHERE id=?", (encode(found), tool_id))
    rows.sort(key=lambda r: r["ok"])
    return rows


def check_soon(store: Any, vault: Any, tool_id: str) -> threading.Thread:
    """A check on a thread of its own (after a sign-in): the person's page is not held for it."""

    def run() -> None:
        try:
            check(store, vault, tool_id)
        except Exception as exc:  # noqa: BLE001 - a failed first check shows as the tool's status, never an error page
            log.warning("mcp tool check %s failed: %s", tool_id, type(exc).__name__)

    thread = threading.Thread(target=run, name="mcp-tool-check", daemon=True)
    thread.start()
    return thread


# ---- what pages and runs see


def view(c: Any, tool: Any, keys: list[Any]) -> Json | None:
    """The MCP part of a tool's page, or None for an API tool: its address, how it signs in, who signed in, and the
    tools its last check found (names, and how many are read-only). Never a token."""
    if not tool["mcp_url"]:
        return None
    try:
        listed = _obj(json.loads(tool["mcp_tools"] or "null"))
    except ValueError:
        listed = {}
    meta = _meta(tool)
    sign_in = next((k for k in keys if k["kind"] == OAUTH), None)
    names = _strs(listed.get("names"))
    return {
        "url": tool["mcp_url"],
        "transport": tool["mcp_transport"] or "http",
        "auth": "oauth" if meta else "token",
        "header": tool["auth_scheme"][7:] if (tool["auth_scheme"] or "").startswith("header:") else "Authorization",
        "tools": len(names) if listed else None,
        "read_only": len(_strs(listed.get("read_only"))),
        "names": names[:200],
        "listed_at": listed.get("listed_at"),
        "signed_in_by": sign_in["updated_by"] if sign_in else None,
        "signed_in_at": sign_in["updated_at"] if sign_in else None,
        "client": ("registered" if meta.get("registered") else "given") if meta and meta.get("client_id") else None,
    }


def runtime_servers(c: Any, values: list[Json]) -> list[Json]:
    """The MCP servers a run gets for its held keys (`values`, credential-runtime's items): one per key whose tool has an
    MCP address. A proxied key's server is under the tool's proxy address; a computer key's is the server's own, and a
    sign-in never goes to a computer."""
    out: list[Json] = []
    for item in values:
        if not item.get("env"):
            continue
        row = c.execute(
            "SELECT t.*, k.kind AS key_kind FROM credentials k JOIN tools t ON t.id=k.tool_id "
            "WHERE k.id=? AND t.mcp_url<>''",
            (item["id"],),
        ).fetchone()
        if row is None:
            continue
        path = urlsplit(str(row["mcp_url"])).path or "/"
        if item.get("proxy_url"):
            url, proxied = str(item["proxy_url"]).rstrip("/") + path, True
        elif row["key_kind"] == OAUTH:
            continue
        else:
            url, proxied = row["mcp_url"], False
        scheme = row["auth_scheme"] or "bearer"
        header = (
            {scheme[7:]: "${" + item["env"] + "}"}
            if scheme.startswith("header:")
            else {"Authorization": "Bearer ${" + item["env"] + "}"}
        )
        out.append(
            {
                "name": row["id"],
                "tool": row["name"],
                "url": url,
                "transport": row["mcp_transport"] or "http",
                "headers": header,
                "env": item["env"],
                "proxied": proxied,
                # The server's own address, whatever `url` the run reaches it by: what the runner tells connections by.
                "upstream": row["mcp_url"],
            }
        )
    return out


# ---- routes


class McpProbe(Contract):
    url: str = Field(min_length=1, max_length=500)


class McpCreate(Contract):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=500)
    auth: str = Field(pattern=r"^(token|oauth)$")
    # The header the token goes in: empty or Authorization is `Bearer <token>`; any other name takes the token alone.
    header: str = Field(default="", max_length=60, pattern=HEADER)
    token: SecretStr | None = Field(default=None, min_length=1, max_length=100_000)
    # A server with no dynamic registration: the client its owner made for Tico.
    client_id: str = Field(default="", max_length=500)
    client_secret: SecretStr | None = Field(default=None, min_length=1, max_length=2000)


def create(c: Any, who: Any, vault: Any, body: McpCreate, found: Json, meta: Json | None) -> Json:
    """The tool, on the proxy, with its key when it is a token. An OAuth tool's sign-in comes after (`start`)."""
    TA.require(c, who, TA.tool(), action=TA.CREATE, admins=vault.admins, auth=vault.auth)
    name = body.name.strip()
    api_tools = _api()
    tid = api_tools._unique_id(c, TS.slug(name))
    service = TS.match(name=name) or ""
    header = body.header.strip()
    scheme = "bearer" if not header or header.lower() == "authorization" else "header:" + header
    oauth: Json | None = None
    if body.auth == "oauth":
        oauth = dict(meta or {}) | (
            {"client_id": body.client_id.strip(), "registered": False} if body.client_id.strip() else {}
        )
    now = H.now()
    c.execute(
        "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,updated_at,"
        "mcp_url,mcp_transport,mcp_oauth,mcp_client_secret) VALUES(?,?,?,?,?,'','proxy',?,?,?,?,?,?,?)",
        (
            tid,
            name,
            service,
            origin(found["url"]),
            "bearer" if oauth else scheme,
            who.actor,
            now,
            now,
            found["url"],
            found["transport"],
            encode(oauth) if oauth else None,
            vault.cipher.seal(c, _secret_context(tid), body.client_secret.get_secret_value())
            if oauth and body.client_secret is not None
            else None,
        ),
    )
    H.event(c, who.actor, "tool.created", tid, {"service": service, "delivery": "proxy", "mcp": body.auth})
    if body.auth == "token":
        api_tools.add_key(
            c,
            who,
            vault,
            tid,
            api_tools.KeyCreate(name=f"{name} token", value=body.token, env=env_for(tid)),
        )
    return api_tools._tool_view(api_tools.tool_row(c, tid)) | {"mcp": view(c, api_tools.tool_row(c, tid), [])}


def register(store: Any, vault: Any, settings: Any, tool: Any) -> Json:
    """The client Tico signs in to this tool's server as, registered (RFC 7591) on first use and kept on the tool. A
    server with no registration endpoint needs the client id a person gave."""
    meta = _meta(tool) or {}
    if meta.get("client_id"):
        return meta
    if not meta.get("registration_endpoint"):
        raise Problem(
            "client_id",
            f"{tool['name']}'s sign-in server needs a client id. Make an OAuth client for Tico there, with the "
            f"redirect address {callback_url(settings)}, and add {tool['name']} again with its id",
            409,
        )
    try:
        response = send(
            "POST",
            meta["registration_endpoint"],
            headers={"Accept": "application/json"},
            json_body=mcp_client.registration([callback_url(settings)], ["authorization_code", "refresh_token"]),
        )
        if response.status_code >= 300:
            raise Failed(f"registration answered {response.status_code}")
        client_id, secret = mcp_client.registered(_json(response))
    except (Failed, mcp_client.AnswerError, egress.Blocked) as exc:
        reason = getattr(exc, "reason", "") or "its answer had no client id"
        raise Problem("sign_in", f"{tool['name']}'s sign-in server did not register Tico: {reason}", 502) from None
    meta |= {"client_id": client_id, "registered": True}
    with store.transaction() as c:
        c.execute(
            "UPDATE tools SET mcp_oauth=?, mcp_client_secret=?, updated_at=? WHERE id=?",
            (
                encode(meta),
                vault.cipher.seal(c, _secret_context(tool["id"]), secret) if secret else None,
                H.now(),
                tool["id"],
            ),
        )
    return meta


def start(store: Any, vault: Any, settings: Any, who: Any, tool_id: str) -> dict[str, str]:
    """{authorize_url}: where the person signs in. The state and PKCE verifier wait here, sealed, for STATE_TTL."""
    validate_identity: Any = _auth().validate_identity

    api_tools = _api()

    with store.read() as c:
        validate_identity(c, who)
        TA.require(c, who, TA.tool(), action=TA.EDIT, admins=vault.admins, auth=vault.auth)
        tool = api_tools._readable(c, who, tool_id, vault.admins)
    if not tool["mcp_url"] or not _meta(tool):
        raise Problem("sign_in", f"{tool['name']} has no sign-in. Add a token in Tools > {tool['name']} > Keys", 409)
    meta = register(store, vault, settings, tool)
    verifier, challenge = mcp_client.pkce()
    state = secrets.token_urlsafe(32)
    sid = hashlib.sha256(state.encode()).hexdigest()
    with store.transaction() as c:
        c.execute("DELETE FROM tool_oauth_states WHERE created_at<?", (H.shift(H.now(), seconds=-STATE_TTL),))
        c.execute(
            "INSERT INTO tool_oauth_states(id,tool_id,actor,verifier,created_at) VALUES(?,?,?,?,?)",
            (sid, tool_id, who.actor, vault.cipher.seal(c, "tool_oauth_states:" + sid, verifier), H.now()),
        )
    query = {
        "response_type": "code",
        "client_id": meta["client_id"],
        "redirect_uri": callback_url(settings),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": meta.get("resource") or tool["mcp_url"],
    }
    if meta.get("scope"):
        query["scope"] = meta["scope"]
    endpoint = meta["authorization_endpoint"]
    return {"authorize_url": endpoint + ("&" if "?" in endpoint else "?") + urlencode(query)}


def finish(store: Any, vault: Any, settings: Any, code: str, state: str, error: str = "") -> str | None:
    """The sign-in server's return: the state taken (once), the code exchanged for tokens with its verifier, the tokens
    kept on the tool. The tool's id, or None when the state is unknown or too old."""
    sid = hashlib.sha256(str(state or "").encode()).hexdigest()
    with store.transaction() as c:
        row = c.execute("SELECT * FROM tool_oauth_states WHERE id=?", (sid,)).fetchone()
        if row is None:
            return None
        c.execute("DELETE FROM tool_oauth_states WHERE id=?", (sid,))
        if row["created_at"] < H.shift(H.now(), seconds=-STATE_TTL):
            return None
        tool = c.execute("SELECT * FROM tools WHERE id=?", (row["tool_id"],)).fetchone()
        if tool is None:
            return None
        verifier = vault.cipher.unseal(c, "tool_oauth_states:" + sid, row["verifier"])
        secret = _client_secret(c, vault, tool)
    meta = _meta(tool) or {}
    if error or not code or not meta.get("client_id"):
        _record_failure(store, tool["id"], None, None, "Sign-in declined" if error else "Sign-in failed")
        return tool["id"]
    try:
        response = _token_request(
            meta,
            secret,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url(settings),
                "code_verifier": verifier,
            },
        )
        if response.status_code >= 300:
            raise Failed(f"the sign-in server answered {response.status_code}")
        tokens = mcp_client.tokens(_json(response), time.time())
    except (Failed, mcp_client.AnswerError, egress.Blocked):
        _record_failure(store, tool["id"], None, None, "Sign-in failed")
        return tool["id"]
    with store.transaction() as c:
        try:
            store_sign_in(c, vault, tool, row["actor"], tokens)
        except Problem:
            _record_failure(store, tool["id"], None, None, "Sign-in failed: the tool already has a key")
            return tool["id"]
    return tool["id"]


def install(app: Any, store: Any) -> None:
    def vault() -> Any:  # read at each call: a test swaps its cipher after start
        return app.state.vault

    def admin(request: Request) -> Any:
        validate_identity: Any = _auth().validate_identity

        who = request.state.identity
        with store.read() as c:
            validate_identity(c, who)
            TA.require(c, who, TA.tool(), action=TA.EDIT, admins=vault().admins, auth=vault().auth)
        return who

    @app.post("/api/v2/mcp-tools/probe")
    def probe_mcp_tool(request: Request, body: McpProbe) -> Json:
        """What a remote MCP server offers: a token, an OAuth sign-in (with or without dynamic registration), or nothing
        to sign in with. Credential administrators only; the server is asked from here, never from the browser."""
        admin(request)
        found, _meta_ = probe(body.url)
        return found

    @app.post("/api/v2/mcp-tools")
    def create_mcp_tool(request: Request, body: McpCreate) -> Json:
        """Add a remote MCP server as a tool: with a token (checked at once), or ready for its OAuth sign-in."""
        receipt: Any = _api()._Receipt

        who = admin(request)
        with store.read() as c:
            TA.catalog_only(c, who, mcp_url=body.url)  # before the probe reaches the address
        found, meta = probe(body.url)
        if body.auth == "token" and body.token is None:
            raise Problem("token", "Paste the token", 422)
        if body.auth == "oauth" and not meta:
            raise Problem("auth", "This server offers no sign-in. Paste a token instead", 422)
        if body.auth == "oauth" and not (meta or {}).get("registration_endpoint") and not body.client_id.strip():
            raise Problem(
                "client_id",
                "This server's sign-in needs a client id: make an OAuth client for Tico there, with the redirect "
                f"address {callback_url(store.settings)}",
                422,
            )

        def nothing(c: Any, w: Any) -> None:  # create() checks the caller itself
            return None

        def made_by(c: Any) -> Json:
            return create(c, who, vault(), body, found, meta)

        made = vault().change(
            who,
            request.url.path,
            request.headers.get("idempotency-key"),
            receipt(body),
            nothing,
            made_by,
        )
        if body.auth == "token":
            try:
                check(store, vault(), made["id"])
            except Problem:
                pass  # the tool's status says what the check found
        return made

    @app.post("/api/v2/api-tools/{tool_id}/sign-in")
    def start_mcp_sign_in(request: Request, tool_id: str) -> dict[str, str]:
        """Start (or redo) an MCP tool's OAuth sign-in: {authorize_url}, where the person signs in."""
        return start(store, vault(), store.settings, request.state.identity, tool_id)

    @app.get(CALLBACK_PATH, include_in_schema=False)
    def finish_mcp_sign_in(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        """The sign-in server sends the browser back here. The state, made for one sign-in, is the only right to it."""
        tool_id = finish(store, vault(), store.settings, code[:4000], state[:200], error[:200])
        if tool_id is None:
            return RedirectResponse("/#/tools", status_code=302)
        with store.read() as c:
            signed = c.execute(
                "SELECT 1 FROM credentials WHERE tool_id=? AND kind=? AND ciphertext IS NOT NULL", (tool_id, OAUTH)
            ).fetchone()
        if signed:
            check_soon(store, vault(), tool_id)
        return RedirectResponse("/#/tools/api/" + quote(tool_id, safe=""), status_code=302)
