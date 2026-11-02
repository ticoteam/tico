"""Settings > General > Tico company: link this self-run server to its company on app.tico.dev.

An owner or admin, signed in directly (the same rule as Inbox's company link, backend/inbound.py `connection_actor`),
pastes a one-time server code from the portal. Preview asks HQ which company the code is for and spends nothing.
Connect makes a one-time proof, keeps only its digest here for PENDING_S, and sends the proof to HQ with this server's
public address, install id (backend/census.py) and version. HQ fetches `GET <public address>/api/v2/company-connection/
proof`, the one route here with no sign-in (backend/app.py `request_guard`), which answers the digest only while a
connect is pending. On a match HQ answers with the company and a server key (`tsk_...`). A server with no https public
address (the local install, `http://127.0.0.1:8765`) sends `public_url: null`: HQ skips the proof, links it on the code
alone and shows it as a local server, with no address.

The server key is sealed with the credential vault's data key (backend/credentials.py `CredentialCipher.seal`) in
`registry_metadata` KEY, like the gateway key: no read returns it, and no event or log line carries it. With it the
server reads its company and credit (`GET /v1/portal/server`, at most every FRESH_S when Settings asks), says its
version once a day, and within the hour after it changes (`POST /v1/portal/server/heartbeat`, the `company_heartbeat`
system routine, no model turn) and disconnects (`DELETE /v1/portal/server`). HQ refusing the key (401) means the link
was ended on the portal: the key is forgotten here too.

While linked, the server's Tico AI Gateway key comes from its company (`gateway_key`, `POST /v1/portal/server/gateway`):
HQ enrolls the install backend/gateway_connection.py `auto_tick` made and bills its use to the company's credit. It is
asked right after Connect and hourly while there is no key; a pasted or environment key wins. When the link ends, HQ
revokes that key and it is forgotten here too (gateway_connection.py `company_ended`).

HQ is only ever https://api.tico.dev or https://updates.tico.team (`<TICO_HQ_URL>`, backend/census.py `hq_url`); any
other base URL leaves the feature unavailable. HQ's answers are read raw, capped at MAX_ANSWER bytes, with no redirects,
and must have exactly the fields the contract names. Demo and rehearsal servers send HQ nothing (`off`).
"""

import asyncio
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlparse

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, ValidationError

from clients.gateway import user_agent

from .census import hq_url
from .replication import rehearsal_on
from .inbound import connection_json
from .models import Contract
from .store import H, Problem

KEY = "company-server"
CONTEXT = "company-server:key"
PROOF_PATH = "/api/v2/company-connection/proof"
HQ_ALLOWED = ("https://api.tico.dev", "https://updates.tico.team")
PENDING_S = 120          # a proof answers HQ this long after Connect
FRESH_S = 60             # Settings reads HQ's word on the company at most this often
HEARTBEAT_S = 23 * 3600  # the hourly sweep sends the version once this long has passed, or sooner once it changes
WAIT_S = 25              # HQ's whole answer, including its 10-second fetch of the proof
MAX_ANSWER = 4096
TRANSPORT = None         # tests replace it with an httpx.MockTransport: nothing reaches the network
SYSTEM = "system:company"
UNAVAILABLE = "Tico could not confirm the connection. Try again in a minute."
ERRORS = {
    "invalid": (422, "Enter the server code from app.tico.dev."),
    "code_unknown": (404, "That server code is not one Tico made. Check it on app.tico.dev."),
    "code_used": (409, "That server code was already used. Make a new one on app.tico.dev."),
    "install_linked": (409, "This server is already linked to another company."),
    "proof_failed": (422, "Tico could not reach this server at its public address."),
    "rate_limited": (429, "Too many tries. Wait a few minutes and try again."),
}
COMPANY_NAME_MAX = 100
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
WORKSPACE = re.compile(r"[A-Za-z0-9_-]{1,100}")
SERVER_KEY = re.compile(r"tsk_[A-Za-z0-9_-]{16,200}")
GATEWAY_ANSWER = {"key", "key_id", "rotate_after"}
ERROR_CODE = re.compile(r"[a-z][a-z_]{0,39}")

_pending = {"digest": "", "until": 0.0}
_pending_lock = threading.Lock()
_connecting = threading.Lock()
_heartbeat = threading.Lock()


class ServerConnection(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    code: str = Field(strict=True, min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")
    preview: bool = Field(strict=True)


def proof_digest():
    """The pending proof's sha256 hex, or '' when no connect is waiting on HQ."""
    with _pending_lock:
        return _pending["digest"] if time.monotonic() < _pending["until"] else ""


def _hold(digest):
    with _pending_lock:
        _pending.update(digest=digest, until=time.monotonic() + PENDING_S if digest else 0.0)


def _row(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def _write(c, record):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(record, sort_keys=True)))


def _forget(c, actor, action, sealed):
    """Forget the link, but only while it still holds `sealed`, the key the caller read before asking HQ: a late 401
    about an old key never removes a connection made since."""
    record = _row(c)
    if not record.get("key") or record["key"] != sealed:
        return
    c.execute("DELETE FROM registry_metadata WHERE key=?", (KEY,))
    H.event(c, actor, action, record.get("workspace_id", ""), {"company_id": record.get("company", {}).get("id", "")})
    from . import gateway_connection
    gateway_connection.company_ended(c)        # HQ revoked the Gateway key the link gave


def linked(c, settings=None):
    """Whether this server is linked to its company and may ask HQ (not a demo or rehearsal)."""
    return bool(_row(c).get("key")) and not off(settings)


def view(c):
    """What Settings shows: the linked company, or `company: null`. Never the key."""
    record = _row(c)
    if not record.get("key"):
        return {"company": None}
    return {k: record.get(k) for k in ("company", "workspace_id", "portal_url", "credit", "checked", "heartbeat",
                                       "connected_at")}


def manager(auth, who):
    """An owner or admin may see the link; connecting and disconnecting also need a direct sign-in (`connection_actor`)."""
    auth.domain(who)
    if who.role not in ("owner", "human") or who.support or auth.company_role(who) not in ("owner", "admin"):
        raise Problem("forbidden", "Only an owner or an admin sees the Tico company link", 403)


def off(settings):
    """Demo and rehearsal (a copy of real data) send nothing to HQ: not a connect, a read, a heartbeat or a disconnect."""
    return bool(getattr(settings, "demo", False) or getattr(settings, "rehearsal", False) or rehearsal_on())


def _base():
    base = hq_url().rstrip("/")
    if base not in HQ_ALLOWED:
        raise ValueError("hq_url")
    return base


def _call(settings, method, path, body=None, key=""):
    """(status, JSON or None) from HQ. Raises ValueError for anything that is not a small, plain JSON answer, and when
    the whole exchange takes longer than WAIT_S: the timeout cancels a read that is still waiting, not only the next one.
    Called on a worker thread (a sync route, `asyncio.to_thread` or the heartbeat's own), never on the event loop."""
    if off(settings):
        raise ValueError("off")
    return asyncio.run(_exchange(method, path, body, key))


async def _exchange(method, path, body, key):
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        async with asyncio.timeout(WAIT_S):
            async with httpx.AsyncClient(transport=TRANSPORT, timeout=WAIT_S, trust_env=False,
                                         follow_redirects=False) as client:
                async with client.stream(method, _base() + path, json=body, headers=headers) as response:
                    if 300 <= response.status_code < 400:
                        raise ValueError("redirect")
                    if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                        raise ValueError("encoding")
                    chunks, total = [], 0
                    async for chunk in response.aiter_raw():
                        total += len(chunk)
                        if total > MAX_ANSWER:
                            raise ValueError("answer")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    return response.status_code, (connection_json(raw) if raw.strip() else None)
    except (httpx.HTTPError, httpx.StreamError, TimeoutError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("unreachable") from exc


def _text(value, most):
    return isinstance(value, str) and 1 <= len(value) <= most and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _company(value):
    if not isinstance(value, dict) or set(value) != {"id", "name", "slug"}:
        raise ValueError("company")
    if not _text(value["id"], 64) or not _text(value["name"], COMPANY_NAME_MAX) or not isinstance(value["slug"], str) \
            or not SLUG.fullmatch(value["slug"]):
        raise ValueError("company")
    uuid.UUID(value["id"])
    return {"id": value["id"], "name": value["name"], "slug": value["slug"]}


def _portal_url(value):
    parsed = urlparse(value) if _text(value, 2048) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or "@" in parsed.netloc or " " in value:
        raise ValueError("portal_url")
    return value


def _workspace(value):
    if not isinstance(value, str) or not WORKSPACE.fullmatch(value):
        raise ValueError("workspace_id")
    return value


def _credit(value):
    if not isinstance(value, dict) or set(value) != {"balance_cents"}:
        raise ValueError("credit")
    cents = value["balance_cents"]
    if cents is not None and type(cents) is not int:
        raise ValueError("credit")
    return {"balance_cents": cents}


def _refusal(status, result):
    code = result.get("error") if isinstance(result, dict) else None
    if isinstance(code, str) and code in ERRORS and ERRORS[code][0] == status:
        return Problem(code, ERRORS[code][1], status)
    return None


def public_url(settings):
    """This server's address as HQ must reach it, or None when it has none: only an https address can be proved, so
    a local install (http on loopback or a private address) links without one."""
    url = str(settings.public_url or "").rstrip("/")
    return url if urlparse(url).scheme == "https" else None


def connect(store, vault, settings, who, body, install_id):
    """Preview (company only, nothing spent) or connect (prove, then keep the server key). `install_id` is from
    backend/inbound.py `connection_actor`, which also checked the caller."""
    if off(settings):
        raise Problem("company_connection_unavailable", UNAVAILABLE, 503, retryable=True)
    address = public_url(settings)
    from .releases import version
    if not _connecting.acquire(blocking=False):
        raise Problem("company_connection_busy", "Another connect is in progress. Try again in a minute.", 409)
    try:
        proof = secrets.token_urlsafe(32)
        if not body.preview:
            _hold(hashlib.sha256(proof.encode()).hexdigest())
        try:
            answered, result = _call(settings, "POST", "/v1/portal/server-connection", {
                "code": body.code, "public_url": address, "install_id": install_id, "version": version(),
                "proof": proof, "preview": body.preview})
        finally:
            _hold("")
    except ValueError:
        raise Problem("company_connection_unavailable", UNAVAILABLE, 503, retryable=True) from None
    finally:
        _connecting.release()
    if refused := _refusal(answered, result):
        raise refused
    try:
        if body.preview:
            if answered != 200 or not isinstance(result, dict) or set(result) != {"company"}:
                raise ValueError("answer")
            return {"company": _company(result["company"]), "preview": True}
        if answered != 201 or not isinstance(result, dict) or \
                set(result) != {"company", "workspace_id", "server_key", "portal_url"}:
            raise ValueError("answer")
        company, workspace = _company(result["company"]), _workspace(result["workspace_id"])
        portal = _portal_url(result["portal_url"])
        if not isinstance(result["server_key"], str) or not SERVER_KEY.fullmatch(result["server_key"]):
            raise ValueError("server_key")
    except (ValueError, KeyError, TypeError):
        raise Problem("company_connection_unavailable", UNAVAILABLE, 503, retryable=True) from None
    with store.transaction() as c:
        at = H.now()
        _write(c, {"key": vault.cipher.seal(c, CONTEXT, result["server_key"]), "company": company,
                   "workspace_id": workspace, "portal_url": portal, "credit": None, "checked": "", "heartbeat": at,
                   "version": version(), "connected_at": at, "connected_by": who.actor})
        H.event(c, who.actor, "company_server.connected", workspace, {"company_id": company["id"], "name": company["name"]})
    from . import gateway_connection
    gateway_connection.check_soon(store, vault, settings)   # its Gateway key, if it has none, on its own thread
    return status(store, vault)          # unchecked: reads the company's credit now


def _key(store, vault):
    with store.read() as c:
        record = _row(c)
        return (vault.cipher.unseal(c, CONTEXT, record["key"]) if record.get("key") else ""), record


def status(store, vault, now=None):
    """The linked company, refreshed from HQ when the last read is older than FRESH_S. HQ down keeps the last word."""
    key, record = _key(store, vault)
    if key and not off(store.settings) and (now or time.time()) - _seconds(record.get("checked")) >= FRESH_S:
        try:
            code, result = _call(store.settings, "GET", "/v1/portal/server", key=key)
            if code == 401:
                with store.transaction() as c:
                    _forget(c, SYSTEM, "company_server.revoked", record["key"])
            elif code == 200:
                if not isinstance(result, dict) or set(result) != {"company", "workspace_id", "portal_url", "credit"}:
                    raise ValueError("answer")
                fresh = {"company": _company(result["company"]), "workspace_id": _workspace(result["workspace_id"]),
                         "portal_url": _portal_url(result["portal_url"]), "credit": _credit(result["credit"])}
                with store.transaction() as c:
                    if _row(c).get("key") == record["key"]:
                        _write(c, {**_row(c), **fresh, "checked": H.now()})
        except (ValueError, KeyError, TypeError):
            pass
    with store.read() as c:
        return view(c)


def disconnect(store, vault, who):
    """End the link on HQ, then forget the key. A key HQ already refuses is forgotten as well."""
    if off(store.settings):
        raise Problem("company_connection_unavailable", "A rehearsal or demo server cannot change the Tico company link.", 409)
    key, record = _key(store, vault)
    if key:
        try:
            code, _answer = _call(store.settings, "DELETE", "/v1/portal/server", key=key)
        except ValueError:
            raise Problem("company_connection_unavailable", UNAVAILABLE, 503, retryable=True) from None
        if code not in (204, 401):
            raise Problem("company_connection_unavailable", UNAVAILABLE, 503, retryable=True)
        with store.transaction() as c:
            _forget(c, who.actor, "company_server.disconnected", record["key"])
    with store.read() as c:
        return view(c)


def gateway_key(store, vault, install, secret):
    """This server's own Gateway key from its company, for gateway_connection.py `auto_tick`: (status, {key, key_id,
    rotate_after} or {}, error code), as clients/gateway.py `enroll` answers. Status 0: HQ not reached, or an answer
    not of that shape. A 401 forgets the link, as every other call does. Never logs the key or the secret."""
    key, record = _key(store, vault)
    if not key:
        return 401, {}, "unauthorized"
    try:
        code, result = _call(store.settings, "POST", "/v1/portal/server/gateway", {"install": install, "secret": secret},
                             key=key)
    except ValueError:
        return 0, {}, ""
    if code == 401:
        with store.transaction() as c:
            _forget(c, SYSTEM, "company_server.revoked", record["key"])
        return code, {}, "unauthorized"
    if code == 201:
        if isinstance(result, dict) and set(result) == GATEWAY_ANSWER and all(_text(result[k], 200) for k in GATEWAY_ANSWER):
            return code, result, ""
        return 0, {}, ""
    error = result.get("error") if isinstance(result, dict) else None
    return code, {}, error if isinstance(error, str) and ERROR_CODE.fullmatch(error) else ""


def _seconds(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def heartbeat_due(store, now=None):
    """A day since the last heartbeat, or the running version is not the one HQ last heard (an update, or a record from
    before `version` was kept): app.tico.dev and admin see the new version within the hour, not the next day."""
    from .releases import version
    with store.read() as c:
        record = _row(c)
    return bool(record.get("key")) and not off(store.settings) and (
        record.get("version") != version() or (now or time.time()) - _seconds(record.get("heartbeat")) >= HEARTBEAT_S)


def heartbeat(store, vault):
    """Tell HQ this server's version. 'sent', 'gone' (HQ refused the key, now forgotten) or 'failed'."""
    from .releases import version
    if not _heartbeat.acquire(blocking=False):
        return "failed"
    try:
        key, record = _key(store, vault)
        if not key:
            return "gone"
        running = version()
        try:
            code, _answer = _call(store.settings, "POST", "/v1/portal/server/heartbeat", {"version": running}, key=key)
        except ValueError:
            return "failed"
        with store.transaction() as c:
            if _row(c).get("key") != record["key"]:
                return "gone"
            if code == 401:
                _forget(c, SYSTEM, "company_server.revoked", record["key"])
                return "gone"
            if code != 204:
                return "failed"
            _write(c, {**_row(c), "heartbeat": H.now(), "version": running})
            return "sent"
    finally:
        _heartbeat.release()


def heartbeat_later(store):
    """`heartbeat` on its own thread, for the hourly sweep: a tick never waits on HQ."""
    from .credentials import Vault

    def run():
        try:
            heartbeat(store, Vault(store))
        except Exception:
            pass                       # the next hour tries again
    thread = threading.Thread(target=run, name="company-heartbeat", daemon=True)
    thread.start()
    return thread


def install(app, store, settings, auth):
    def actor(who):
        return app.state.inbound.connection_actor(who, what="this server", code="company_connection")

    @app.get(PROOF_PATH)
    async def company_connection_proof():
        """HQ's check that it is talking to the server at this address. No sign-in: it answers only a digest, and only
        while a connect is pending."""
        digest = proof_digest()
        if not digest:
            raise Problem("not_found", "Not found", 404)
        return JSONResponse({"proof_sha256": digest}, headers={"Cache-Control": "no-store"})

    @app.post("/api/v2/company/server-connection")
    async def company_server_connect(request: Request):
        # Authenticate before parsing, as Inbox does: validation errors can reveal the code.
        who = request.state.identity
        proof = actor(who)
        try:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise ValueError()
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 2048:
                    raise ValueError()
                raw.extend(chunk)
            body = ServerConnection.model_validate(connection_json(raw))
        except (ValueError, TypeError, ValidationError, RecursionError):
            raise Problem("invalid", ERRORS["invalid"][1], 422) from None
        return await asyncio.to_thread(connect, store, app.state.vault, settings, who, body, proof["reported_install_id"])

    @app.get("/api/v2/company/server-connection")
    def company_server_view(request: Request):
        manager(auth, request.state.identity)
        return status(store, app.state.vault)

    @app.delete("/api/v2/company/server-connection")
    def company_server_disconnect(request: Request):
        who = request.state.identity
        actor(who)
        return disconnect(store, app.state.vault, who)
