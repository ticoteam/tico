"""Person-owned Granola OAuth and remote MCP. No provider payload reaches diagnostics.

Tokens and pending device codes are encrypted with the credential vault's cipher in a
separate store without reveal, grant, runner or SQL access. HTTP work runs outside transactions.
"""
import asyncio
import base64
import json
import logging
import math
import re
import time
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

import httpx
from fastapi import Request

from .auth import Identity, validate_identity
from .imports import MeetingImport, segments_of
from .store import H, Problem, encode

MCP = "https://mcp.granola.ai/mcp"
AUTH = "https://mcp-auth.granola.ai"
SCOPES = "openid profile email offline_access mcp"
SCHEDULE = 25 * 60
RATE_LIMIT_RETRY = 5 * 60
RETRY_AFTER_MAX = 24 * 3600        # a provider-named wait longer than this is treated as this
RATE_LIMIT_MAX = 6 * 3600          # repeated throttling backs off to this, unless Granola asks for longer
RETRY_IN_SYNC = 60                 # a get_meetings throttle is waited out in the sync only when Granola says it is this short
GET_MEETINGS_RETRIES = (20, 60)    # spaced waits for a get_meetings throttle with no named wait, or a short one
FIRST_GET_MEETINGS_GAP = 10        # the first get_meetings waits this long after the sync's previous MCP call
HOLD_MAX = 3                       # syncs a checkpoint is held for untried notes before they are given up
REVISIT = 24 * 3600                # an imported note is re-read for a late summary only while its meeting is this recent
DEBOUNCE = 120
GET_MEETINGS_INTERVAL = 6
SIGNALS = (("rate limit", r"rate[\s_-]*limit"), ("slow down", r"slow[\s_-]+down"), ("too many requests", r"too many requests"))
log = logging.getLogger(__name__)


class GranolaError(Exception):
    def __init__(self, code="provider_error", step=None, retry_after=None, http_status=None, rpc_code=None,
                 tool_error=False, signal=None):
        self.code = code
        self.step = step
        self.retry_after = retry_after
        # Fixed facts about Granola's answer, safe to store: never its text.
        self.http_status, self.rpc_code, self.tool_error, self.signal = http_status, rpc_code, tool_error, signal
        self.batch = None
        super().__init__(code)

    def detail(self, step):
        return {"step": self.step or step, "http_status": self.http_status, "rpc_code": self.rpc_code,
                "tool_error": self.tool_error,
                "retry_after": round(self.retry_after) if self.retry_after is not None else None,
                "signal": self.signal, "batch": self.batch}


class GranolaMCP:
    def __init__(self, app):
        self.app, self.store = app, app.state.store
        self.transport = None                 # MockTransport in tests; never an API setting
        self.clock = time.time
        self.sleep = asyncio.sleep
        self.locks, self.jobs = weakref.WeakValueDictionary(), {}
        self.connection_locks = weakref.WeakValueDictionary()
        self.changing_connections = set()
        self.registration_lock = asyncio.Lock()
        self.pace_lock = asyncio.Lock()
        self.oauth_pace_lock = asyncio.Lock()
        self.next_call = 0
        self.next_oauth_call = 0
        self.starts = {}
        self.revocations = set()

    async def pace(self, meeting_meta=None):
        async with self.pace_lock:
            next_call = max(self.next_call, meeting_meta.get("next_meetings_call", 0)) if meeting_meta is not None else self.next_call
            wait = next_call - self.clock()
            if wait > 0:
                await self.sleep(wait)
            self.next_call = self.clock() + 1.05
            if meeting_meta is not None:
                meeting_meta["next_meetings_call"] = self.clock() + GET_MEETINGS_INTERVAL

    @staticmethod
    def seconds(value, default):
        try:
            return max(1, int(value))
        except (ValueError, TypeError, OverflowError):
            return default

    @staticmethod
    def verification(url):
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            return (parsed.scheme == "https" and (host == "granola.ai" or host.endswith(".granola.ai"))
                    and not parsed.username and not parsed.password)
        except (ValueError, TypeError):
            return False

    def metadata(self, actor):
        with self.store.read() as c:
            row = c.execute("SELECT metadata_json FROM granola_connections WHERE actor=?", (actor,)).fetchone()
        return json.loads(row[0]) if row else {}

    def delete(self, actor):
        with self.store.transaction() as c:
            c.execute("DELETE FROM granola_connections WHERE actor=?", (actor,))

    def eligible(self, who):
        with self.store.read() as c:
            try:
                validate_identity(c, who)
                return True
            except Problem:
                return False

    def lock(self, actor):
        return self.locks.setdefault(actor, asyncio.Lock())

    def load(self, actor):
        with self.store.transaction() as c:
            row = c.execute("SELECT * FROM granola_connections WHERE actor=?", (actor,)).fetchone()
            if not row:
                return None
            return dict(row), json.loads(row["metadata_json"]), json.loads(self.app.state.vault.cipher.decrypt(c, row))

    def save(self, row, meta, secret):
        with self.store.transaction() as c:
            ciphertext, nonce = self.app.state.vault.cipher.encrypt(c, row["id"], encode(secret))
            # A disconnect/reconnect invalidates every in-flight operation from the previous connection.
            c.execute("UPDATE granola_connections SET ciphertext=?,nonce=?,metadata_json=? WHERE actor=? AND id=?",
                      (ciphertext, nonce, encode(meta), row["actor"], row["id"]))

    async def http(self, method, url, meeting_meta=None, **kwargs):
        if url.startswith(AUTH + "/"):
            # OAuth has its own small queue, so sign-in never waits behind background MCP traffic.
            async with self.oauth_pace_lock:
                wait = self.next_oauth_call - self.clock()
                if wait > 0:
                    await self.sleep(wait)
                self.next_oauth_call = self.clock() + 1.05
        elif meeting_meta is None:
            await self.pace()
        else:
            await self.pace(meeting_meta)
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=45, follow_redirects=False) as client:
                async with client.stream(method, url, **kwargs) as response:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > 20_000_000:
                            raise GranolaError("bad_response")
                    # aiter_bytes() already decoded gzip/br: drop the encoding headers or the rebuilt response
                    # decodes the body a second time and fails (Granola gzips every answer).
                    headers = [(k, v) for k, v in response.headers.items()
                               if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                    return httpx.Response(response.status_code, headers=headers, content=bytes(payload),
                                          request=response.request)
        except httpx.HTTPError:
            raise GranolaError("unreachable") from None

    @staticmethod
    def payload(response):
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise GranolaError("bad_response") from None

    async def client_id(self, rejected=False):
        async with self.registration_lock:
            def read_client():
                with self.store.read() as c:
                    return c.execute("SELECT value_json FROM registry_metadata WHERE key='granola-mcp-client'").fetchone()
            row = await asyncio.to_thread(read_client)
            if row and not rejected:
                return json.loads(row[0])["client_id"]
            response = await self.http("POST", AUTH + "/oauth2/register", json={
                "client_name": "Tico", "token_endpoint_auth_method": "none",
                "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"],
                # Granola rejects a registration without redirect_uris, even for a device-code client that never
                # redirects ("redirect_uris must be an array"); a loopback address is never used.
                "redirect_uris": ["http://127.0.0.1/callback"]})
            if response.status_code >= 400:
                raise GranolaError()
            client_id = self.payload(response).get("client_id")
            if not isinstance(client_id, str) or not client_id:
                raise GranolaError("bad_response")
            def save_client():
                with self.store.transaction() as c:
                    c.execute("INSERT INTO registry_metadata VALUES('granola-mcp-client',?) ON CONFLICT(key) "
                              "DO UPDATE SET value_json=excluded.value_json", (encode({"client_id": client_id}),))
            await asyncio.to_thread(save_client)
            return client_id

    async def connect(self, who):
        async with self.connection_locks.setdefault(who.actor, asyncio.Lock()):
            self.changing_connections.add(who.actor)
            try:
                await self.cancel_sync(who.actor)
                return await self.start_signin(who)
            finally:
                self.changing_connections.discard(who.actor)

    async def cancel_sync(self, actor):
        task = self.jobs.get(actor)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def start_signin(self, who):
        async with self.lock(who.actor):
            client_id = await self.client_id()
            for attempt in range(2):
                response = await self.http("POST", AUTH + "/oauth2/device_authorization", data={
                    "client_id": client_id, "scope": SCOPES, "resource": MCP})
                value = self.payload(response)
                if value.get("error") in ("invalid_client", "unauthorized_client") and attempt == 0:
                    client_id = await self.client_id(rejected=True)
                    continue
                if response.status_code >= 400 or value.get("error"):
                    raise GranolaError()
                break
            if not all(value.get(k) for k in ("device_code", "user_code", "verification_uri", "expires_in")):
                raise GranolaError("bad_response")
            now = self.clock()
            if not all(self.verification(value[k]) for k in ("verification_uri", "verification_uri_complete") if k in value):
                raise GranolaError("bad_response")
            interval = self.seconds(value.get("interval"), 5)
            previous = await asyncio.to_thread(self.load, who.actor)
            meta = {"state": "pending", "expires": now + self.seconds(value["expires_in"], 600), "interval": interval,
                    "next_poll": now + interval, "needs_signin": False, "last_sync": None,
                    "imported_count": 0, "plan_hint": None}
            secret = {"device_code": value["device_code"], "client_id": client_id}
            if previous:
                old_secret = previous[2].get("previous_secret", previous[2])
                if old_secret.get("refresh_token"):
                    secret["previous_secret"] = old_secret
            if previous:
                active_meta = previous[1].get("previous_meta", previous[1])
                if active_meta.get("state") == "connected":
                    meta["previous_meta"] = active_meta
            def store_connection():
                with self.store.transaction() as c:
                    cid = "granola:" + uuid.uuid4().hex
                    ciphertext, nonce = self.app.state.vault.cipher.encrypt(c, cid, encode(secret))
                    c.execute("INSERT INTO granola_connections VALUES(?,?,?,?,?,?) ON CONFLICT(actor) DO UPDATE SET "
                              "id=excluded.id,email=excluded.email,ciphertext=excluded.ciphertext,nonce=excluded.nonce,"
                              "metadata_json=excluded.metadata_json",
                              (who.actor, cid, who.email, ciphertext, nonce, encode(meta)))
            await asyncio.to_thread(store_connection)
            return {k: value[k] for k in ("user_code", "verification_uri", "verification_uri_complete", "expires_in")
                    if k in value} | {"interval": interval, "expires_in": self.seconds(value["expires_in"], 600)}

    def next_retry(self, meta):
        """When a backed-off connection may sync again. A wait saved before the 24 h cap existed (or any longer one)
        is held to the cap from the last attempt, so an old huge Retry-After cannot park an import."""
        at = meta.get("retry_after") or 0
        return min(at, (meta.get("last_attempt") or self.clock()) + RETRY_AFTER_MAX) if at else 0

    @staticmethod
    def stamp(at):
        return datetime.fromtimestamp(at, timezone.utc).isoformat() if at else None

    def status(self, who):
        # Reading status does not decrypt a token.
        with self.store.read() as c:
            row = c.execute("SELECT email,metadata_json FROM granola_connections WHERE actor=?", (who.actor,)).fetchone()
            api_key = c.execute("SELECT enabled FROM meeting_importers WHERE source='granola'").fetchone()
        meta = json.loads(row["metadata_json"]) if row else {}
        return {"mode": "account" if row else "api_key" if api_key and api_key[0] else "off",
                "connected": meta.get("state") == "connected" or bool(meta.get("previous_meta")), "email": meta.get("email"),
                "plan_hint": meta.get("plan_hint"), "last_sync": meta.get("last_sync"),
                "last_error": meta.get("last_error"), "imported_count": meta.get("imported_count", 0),
                "needs_signin": meta.get("needs_signin", False), "syncing": who.actor in self.jobs,
                "skipped": meta.get("skipped", 0), "skip_reasons": meta.get("skip_reasons", {}),
                "last_error_detail": meta.get("last_error_detail"), "last_attempt": self.stamp(meta.get("last_attempt")),
                "next_retry": self.stamp(self.next_retry(meta)), "failures": meta.get("failures", 0)}

    def failed_signin(self, row, meta, secret):
        meta.update(state="needs_signin", needs_signin=True, last_error="Granola needs sign-in again")
        # Do not keep a rejected access token or device code.
        self.save(row, meta, {"client_id": secret.get("client_id", "")})

    @staticmethod
    def account_email(value):
        """Display-only claims received from the trusted token endpoint; never used for rights."""
        from runner.importers.base import email_of
        claims = [value, value.get("userinfo")]
        token = value.get("id_token")
        if isinstance(token, str) and len(token) < 100_000:
            try:
                part = token.split(".")[1]
                claims.append(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))))
            except (ValueError, IndexError, UnicodeError):
                pass
        for claim in claims:
            if isinstance(claim, dict) and claim.get("email_verified") is not False:
                email = email_of(claim.get("email"))
                if email:
                    return email
        return None

    async def poll(self, who):
        meta = await asyncio.to_thread(self.metadata, who.actor)
        if meta.get("state") != "pending":
            return {"state": meta.get("state", "off"), **await asyncio.to_thread(self.status, who)}
        async with self.lock(who.actor):
            saved = await asyncio.to_thread(self.load, who.actor)
            if not saved:
                return {"state": "off", **await asyncio.to_thread(self.status, who)}
            row, meta, secret = saved
            revoke_after_save = None
            if meta["state"] != "pending":
                return {"state": meta["state"], **await asyncio.to_thread(self.status, who)}
            now = self.clock()
            if now >= meta["expires"]:
                meta.update(state="expired", last_error="Sign-in expired")
                await asyncio.to_thread(self.save, row, meta, secret if meta.get("previous_meta") else {})
            elif now >= meta["next_poll"]:
                response = await self.http("POST", AUTH + "/oauth2/token", data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": secret["client_id"],
                    "device_code": secret["device_code"], "resource": MCP})
                value = self.payload(response)
                error = value.get("error")
                if error in ("authorization_pending", "slow_down"):
                    if error == "slow_down":
                        meta["interval"] += 5
                    meta["next_poll"] = self.clock() + meta["interval"]
                elif error in ("expired_token", "access_denied"):
                    meta.update(state="expired" if error == "expired_token" else "denied",
                                last_error="Sign-in expired" if error == "expired_token" else "Sign-in declined")
                    secret = secret if meta.get("previous_meta") else {}
                elif error in ("invalid_client", "unauthorized_client"):
                    await self.client_id(rejected=True)
                    if meta.get("previous_meta"):
                        meta, secret = meta["previous_meta"], secret["previous_secret"]
                        await asyncio.to_thread(self.save, row, meta, secret)
                        return {"state": meta["state"], **await asyncio.to_thread(self.status, who)}
                    await asyncio.to_thread(self.failed_signin, row, meta, secret)
                    return {"state": "needs_signin", **await asyncio.to_thread(self.status, who)}
                elif response.status_code >= 400 or error:
                    raise GranolaError()
                else:
                    if not value.get("access_token") or not value.get("refresh_token"):
                        raise GranolaError("bad_response")
                    old_secret = secret
                    secret = {"client_id": secret["client_id"], "access_token": value["access_token"],
                              "refresh_token": value["refresh_token"],
                              "expiry": self.clock() + self.seconds(value.get("expires_in"), 3600)}
                    revoke_after_save = old_secret.get("previous_secret", {})
                    meta.pop("previous_meta", None)
                    meta.update(state="connected", needs_signin=False, last_error=None, plan_hint="free",
                                email=self.account_email(value))
                await asyncio.to_thread(self.save, row, meta, secret)
                if revoke_after_save:
                    self.queue_revoke(revoke_after_save)
            if meta["state"] in ("expired", "denied") and meta.get("previous_meta"):
                meta = meta["previous_meta"]
                secret = secret["previous_secret"]
                await asyncio.to_thread(self.save, row, meta, secret)
            return {"state": meta["state"], **await asyncio.to_thread(self.status, who)}

    async def refresh(self, row, meta, secret):
        for attempt in range(2):
            response = await self.http("POST", AUTH + "/oauth2/token", data={
                "grant_type": "refresh_token", "client_id": secret["client_id"],
                "refresh_token": secret["refresh_token"], "resource": MCP})
            if response.status_code == 429:
                raise GranolaError("rate_limited", retry_after=self.retry_delay(response))
            if response.status_code >= 500:
                raise GranolaError("unreachable")
            value = self.payload(response)
            error = value.get("error")
            if response.status_code in (400, 401) and error in ("invalid_grant", "invalid_client", "unauthorized_client"):
                if error != "invalid_grant" and attempt == 0:
                    secret["client_id"] = await self.client_id(rejected=True)
                    continue
                client_id = secret.get("client_id", "")
                secret.clear()
                secret["client_id"] = client_id
                await asyncio.to_thread(self.failed_signin, row, meta, secret)
                raise GranolaError("needs_signin")
            if response.status_code >= 400 or error or not value.get("access_token"):
                raise GranolaError("bad_response")
            secret.update(access_token=value["access_token"], refresh_token=value.get("refresh_token") or secret["refresh_token"],
                          expiry=self.clock() + self.seconds(value.get("expires_in"), 3600))
            await asyncio.to_thread(self.save, row, meta, secret)
            return

    def queue_revoke(self, secret):
        secret = secret.get("previous_secret", secret)
        if secret.get("refresh_token"):
            task = asyncio.create_task(self.revoke(dict(secret)))
            self.revocations.add(task)
            task.add_done_callback(self.revocations.discard)

    async def revoke(self, secret):
        try:
            response = await self.http("GET", AUTH + "/.well-known/oauth-authorization-server")
            endpoint = self.payload(response).get("revocation_endpoint", "")
            if endpoint.startswith(AUTH + "/"):
                await self.http("POST", endpoint, data={"token": secret["refresh_token"],
                                "client_id": secret["client_id"], "token_type_hint": "refresh_token"})
        except Exception:
            pass

    async def disconnect(self, who):
        async with self.connection_locks.setdefault(who.actor, asyncio.Lock()):
            self.changing_connections.add(who.actor)
            try:
                await self.cancel_sync(who.actor)
                return await self.remove_connection(who)
            finally:
                self.changing_connections.discard(who.actor)

    async def remove_connection(self, who):
        async with self.lock(who.actor):
            try:
                saved = await asyncio.to_thread(self.load, who.actor)
            except (Problem, ValueError):
                saved = None
            await asyncio.to_thread(self.delete, who.actor)
            self.starts.pop(who.actor, None)
            if saved:
                self.queue_revoke(saved[2])
        return {"ok": True}

    @staticmethod
    def signal(value):
        """Which fixed throttle phrase the provider's text contains, or None. Only that label is ever kept."""
        text = encode(value)
        return next((name for name, pattern in SIGNALS if re.search(pattern, text, re.I)), None)

    @staticmethod
    def rate_limited(value):
        # Provider text is used only for classification, never diagnostics or stored metadata.
        return GranolaMCP.signal(value) is not None

    def retry_delay(self, response):
        value = response.headers.get("Retry-After")
        try:
            delay = float(value)
        except (TypeError, ValueError):
            try:
                delay = parsedate_to_datetime(value).timestamp() - self.clock()
            except (TypeError, ValueError, OverflowError):
                return None
        return min(max(0, delay), RETRY_AFTER_MAX) if math.isfinite(delay) else None

    async def rpc(self, row, meta, secret, session, method, params=None, notification=False):
        if secret.get("expiry", 0) <= self.clock() + 30:
            await self.refresh_for_sync(row, meta, secret)
        for attempt in range(4):
            headers = {"Authorization": "Bearer " + secret["access_token"],
                       "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": session.get("version", "2025-03-26")}
            if session.get("id"):
                headers["Mcp-Session-Id"] = session["id"]
            body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            if not notification:
                body["id"] = uuid.uuid4().hex
            meetings = method == "tools/call" and (params or {}).get("name") == "get_meetings"
            response = await self.http("POST", MCP, headers=headers, json=body, meeting_meta=meta if meetings else None)
            session["last_call"] = self.clock()
            status = response.status_code
            if response.status_code == 401 and not session.get("refreshed"):
                session["refreshed"] = True
                await self.refresh_for_sync(row, meta, secret)
                continue
            if response.status_code == 429:
                delay = self.retry_delay(response)
                if meetings or attempt == 3 or (delay is not None and delay > RETRY_IN_SYNC):
                    raise GranolaError("rate_limited", retry_after=delay, http_status=status)
                await self.sleep(delay if delay is not None else 2 ** (attempt + 1))
                continue
            if response.status_code >= 400 and self.rate_limited(response.text):
                raise GranolaError("rate_limited", retry_after=self.retry_delay(response), http_status=status,
                                   signal=self.signal(response.text))
            if response.status_code == 403:
                raise GranolaError("forbidden", http_status=status)
            if response.status_code >= 400:
                raise GranolaError("provider_error", http_status=status)
            if response.headers.get("Mcp-Session-Id"):
                session["id"] = response.headers["Mcp-Session-Id"]
            if notification:
                return {}
            if "text/event-stream" in response.headers.get("content-type", ""):
                value = None
                for event in response.text.replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
                    if data:
                        try:
                            candidate = json.loads(data)
                            if candidate.get("id") == body["id"]:
                                value = candidate
                                break
                        except (ValueError, AttributeError):
                            pass
                if not isinstance(value, dict):
                    raise GranolaError("bad_response", http_status=status)
            else:
                value = self.payload(response)
            if value.get("error"):
                error = value["error"]
                code = error.get("code") if isinstance(error, dict) else None
                code = code if isinstance(code, int) and not isinstance(code, bool) else None
                if self.rate_limited(error):
                    raise GranolaError("rate_limited", retry_after=self.retry_delay(response), http_status=status,
                                       rpc_code=code, signal=self.signal(error))
                raise GranolaError("forbidden" if code in (403, -32003) else "provider_error", http_status=status, rpc_code=code)
            result = value.get("result", {})
            if not isinstance(result, dict):
                raise GranolaError("bad_response", http_status=status)
            if result.get("isError"):
                # Read only to classify; never save/return/log the tool's free-form error message.
                if self.rate_limited(result):
                    raise GranolaError("rate_limited", retry_after=self.retry_delay(response), http_status=status,
                                       tool_error=True, signal=self.signal(result))
                if (params or {}).get("name") == "get_account_info":
                    # A completed optional-tool refusal is different from a transient HTTP/network failure.
                    raise GranolaError("feature_unavailable", http_status=status, tool_error=True)
                message = encode(result).lower()
                if (params or {}).get("name") == "get_meeting_transcript" and any(word in message for word in
                        ("paid", "upgrade", "business", "enterprise")):
                    raise GranolaError("transcripts_unavailable", http_status=status, tool_error=True)
                raise GranolaError("forbidden" if any(word in message for word in
                                   ("permission", "paid", "upgrade", "forbidden", "not authorized", "access denied"))
                                   else "provider_error", http_status=status, tool_error=True)
            return result
        raise GranolaError("rate_limited", retry_after=self.retry_delay(response), http_status=response.status_code)

    async def refresh_for_sync(self, row, meta, secret):
        try:
            await self.refresh(row, meta, secret)
        except GranolaError as exc:
            exc.step = "refresh_token"
            raise

    @staticmethod
    def content(result, allow_text=False):
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result["structuredContent"]
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block.get("text", ""))
                except ValueError:
                    raw = block.get("text", "")
                    parsed = GranolaMCP.xml_content(raw)
                    if parsed is not None:
                        return parsed
                    if allow_text:
                        return {"transcript": raw}
        raise GranolaError("bad_response")

    @staticmethod
    def xml_content(raw):
        """Granola's XML-like text contains bare emails and markdown, so never parse it as XML."""
        if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
            raise GranolaError("bad_response")
        start = re.search(r"<(meetings_data|meetings|notes|meeting|note|transcript)\b", raw)
        if start is None:
            return None
        raw = raw[start.start():]

        def attributes(value):
            return {m[1]: unescape(m[3]) for m in re.finditer(
                r'''([\w:-]+)\s*=\s*(["'])(.*?)\2''', value, re.S)}

        def field(body, key):
            # Only direct fields count. Skip whole unknown/private sections without altering
            # markup inside a shared summary (which may itself contain literal tag examples).
            position = 0
            while match := re.search(r"<([\w:-]+)\b[^>]*>", body[position:]):
                start = position + match.end()
                if match[0].rstrip().endswith("/>"):
                    if match[1] == key:
                        return ""
                    position = start
                    continue
                close = re.search(r"</" + re.escape(match[1]) + r"\s*>", body[start:])
                if close is None:
                    return None
                if match[1] == key:
                    return body[start:start + close.start()].strip()
                position = start + close.end()
            return None

        # Bound each body by the next meeting opener: a broken row cannot swallow a healthy one.
        # Hide opaque sections while locating rows, keeping offsets into the original text.
        scan = re.sub(r"<(summary_markdown|summary_text|ai_summary|enhanced_notes|summary|private_notes|raw_notes|note_taker_notes)\b[^>]*>.*?</\1\s*>",
                      lambda match: " " * len(match[0]), raw, flags=re.S)
        nodes = list(re.finditer(r'''<(meeting|note)\b((?:[^<>"']|"[^"]*"|'[^']*')*?)(/?)>''', scan, re.S))
        out = []
        for index, node in enumerate(nodes):
            end = nodes[index + 1].start() if index + 1 < len(nodes) else len(raw)
            close = re.search(r"</" + node[1] + r"\s*>", scan[node.end():end])
            if not node[3] and close is None:
                out.append({})
                continue
            body = "" if node[3] else raw[node.end():node.end() + close.start()]
            attrs = attributes(node[2])
            row = {"id": attrs.get("id") or attrs.get("meeting_id")}
            for key in ("id", "meeting_id", "note_id", "title", "date", "created_at", "start_time", "summary_markdown",
                        "summary_text", "ai_summary", "enhanced_notes", "summary", "web_url"):
                if key in attrs:
                    row[key] = attrs[key]
                value = field(body, key)
                if value is not None:
                    row[key] = value if key in ("summary_markdown", "summary_text", "ai_summary", "enhanced_notes", "summary") else unescape(value)
            row["attendees"] = []
            for attendee in re.finditer(r"<attendee\b([^>]*?)(?:/\s*>|>(.*?)</attendee\s*>)", field(body, "attendees") or "", re.S):
                attrs, inner = attributes(attendee[1]), attendee[2] or ""
                row["attendees"].append({"name": attrs.get("name") or unescape(field(inner, "name") or re.sub(r"<[^>]*>", "", inner).strip()),
                                         "email": attrs.get("email") or unescape(field(inner, "email") or "")})
            for line in (field(body, "known_participants") or "").splitlines():
                line = unescape(line).strip()
                if not line:
                    continue
                email = re.search(r"<([^<>\s]+@[^<>\s]+)>", line)
                name = line[:email.start()].strip() if email else line
                name = re.sub(r"\s*\(note creator\)", "", name, flags=re.I)
                name = re.split(r"(?:^|\s+)from\s+", name, maxsplit=1, flags=re.I)[0].strip()
                row["attendees"].append({"name": name, "email": email[1] if email else ""})
            out.append(row)
        if out or re.search(r"<(meetings_data|meetings|notes)\b", raw):
            wrapper = re.match(r"\s*<(meetings_data|meetings|notes)\b[^>]*>(.*)</\1\s*>\s*$", raw, re.S)
            return {"meetings": out, "next_cursor": unescape(field(wrapper[2] if wrapper else raw, "next_cursor") or "") or None}
        transcript = field(raw, "transcript")
        if transcript is not None:
            return {"transcript": transcript}
        return None

    @staticmethod
    def meeting_date(value):
        """Provider dates without a timezone (including English month names) are treated as UTC."""
        from runner.importers.base import moment
        parsed = moment(value)
        if parsed is None and isinstance(value, str):
            for format in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
                try:
                    return datetime.strptime(value.strip(), format).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
        return parsed

    @staticmethod
    def arguments(tool, values):
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        out = {}
        for key, value in values.items():
            if key not in properties:
                continue
            spec = properties[key]
            if spec.get("format") == "date" and isinstance(value, str):
                value = value[:10]
            if spec.get("enum") and value not in spec["enum"]:
                if key in ("date_range", "time_range") and "custom" in spec["enum"]:
                    value = "custom"
                elif key in ("date_range", "time_range") and "last_30_days" in spec["enum"]:
                    value = "last_30_days"
                else:
                    continue
            if spec.get("type") == "object" and isinstance(value, dict) and spec.get("properties"):
                value = {k: v for k, v in value.items() if k in spec["properties"]}
            out[key] = value
        # Exact schemas are discovered at runtime; unsupported required fields fail safely.
        if any(k not in out for k in schema.get("required", [])):
            raise GranolaError("unsupported_schema")
        return out

    async def call(self, row, meta, secret, session, tool, values):
        params = {"name": tool["name"], "arguments": self.arguments(tool, values)}
        meetings = tool["name"] == "get_meetings"
        for attempt in range(len(GET_MEETINGS_RETRIES) + 1):
            try:
                return self.content(await self.rpc(row, meta, secret, session, "tools/call", params),
                                    allow_text=tool["name"] == "get_meeting_transcript")
            except GranolaError as exc:
                if meetings:
                    exc.batch = len(values.get("meeting_ids") or values.get("ids") or values.get("note_ids") or [])
                # A get_meetings throttle with no named wait, or a short one, is retried after spaced waits.
                # A longer named wait fails the sync, and the next one backs off.
                if (not meetings or exc.code != "rate_limited" or attempt == len(GET_MEETINGS_RETRIES)
                        or (exc.retry_after is not None and exc.retry_after > RETRY_IN_SYNC)):
                    raise
                await self.sleep(max(exc.retry_after or 0, GET_MEETINGS_RETRIES[attempt]))

    @staticmethod
    def account_details(result):
        """Keep only display email and a recognized plan from optional account information."""
        from runner.importers.base import email_of
        try:
            value = GranolaMCP.content(result)
        except GranolaError:
            value = "\n".join(block.get("text", "") for block in result.get("content", [])
                              if block.get("type") == "text")
        details, pending = {}, [value]
        for _ in range(100):
            if not pending:
                break
            part = pending.pop()
            if isinstance(part, dict):
                email = GranolaMCP.account_email(part)
                if email:
                    details["email"] = email
                for key in ("plan", "plan_name", "tier", "subscription_plan"):
                    plan = part.get(key)
                    if isinstance(plan, dict):
                        plan = plan.get("name") or plan.get("tier")
                    if isinstance(plan, str):
                        plan = plan.lower().strip()
                        if plan in ("free", "basic"):
                            details["account_plan_hint"] = "free"
                        elif plan in ("paid", "pro", "business", "enterprise"):
                            details["account_plan_hint"] = "paid"
                pending.extend(v for v in part.values() if isinstance(v, (dict, list)))
            elif isinstance(part, list):
                pending.extend(part[:100])
            elif isinstance(part, str):
                email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", part)
                if email and email_of(email[0]):
                    details["email"] = email_of(email[0])
                plan = re.search(r"\b(?:plan|tier)\s*[:=]\s*(free|basic|paid|pro|business|enterprise)\b", part, re.I)
                if plan:
                    details["account_plan_hint"] = "free" if plan[1].lower() in ("free", "basic") else "paid"
        return details

    @staticmethod
    def rows(value):
        if isinstance(value, list):
            return [r if isinstance(r, dict) else {} for r in value]
        if isinstance(value, dict):
            for key in ("meetings", "notes", "results"):
                if isinstance(value.get(key), list):
                    return [r if isinstance(r, dict) else {} for r in value[key]]
            if value.get("id") or value.get("meeting_id"):
                return [value]
        raise GranolaError("bad_response")

    @staticmethod
    def item(note, transcript=""):
        # Do not use private_notes, raw_notes or note-taker notes. Only shared/AI summaries.
        summary = note.get("summary_markdown") or note.get("summary_text") or note.get("ai_summary") or note.get("enhanced_notes") or note.get("summary") or ""
        if isinstance(summary, dict):
            summary = summary.get("markdown") or summary.get("text") or ""
        notes = note.get("notes")
        if not summary and isinstance(notes, dict):
            summary = notes.get("summary") or notes.get("ai_summary") or ""
        external_id = note.get("note_id") or note.get("id") or note.get("meeting_id")
        if not external_id:
            raise GranolaError("bad_response")
        from runner.importers.base import people, text, https_url
        attendees = note.get("attendees") or note.get("participants") or []
        participants = people(*[(a.get("name", ""), a.get("email", "")) if isinstance(a, dict) else str(a)
                                for a in attendees])
        started = GranolaMCP.meeting_date(note.get("created_at") or note.get("date") or note.get("start_time"))
        return MeetingImport(source="granola", external_id=str(external_id), private=True,
                             title=text(note.get("title"), 300) or "Granola meeting",
                             started_at=started.isoformat() if started else None, participants=participants,
                             notes=str(summary).strip()[:200_000], transcript=transcript,
                             media_url=https_url(note.get("web_url"))) if summary or transcript else None

    def imported_ids(self, actor, ids):
        """Which of these Granola notes this person already has as a meeting: {id: whether it has a transcript}."""
        keys = [f"{actor}:{nid}" for nid in ids]
        found = {}
        with self.store.read() as c:
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                found.update((r[0], bool(r[1])) for r in c.execute(
                    "SELECT r.external_id,m.transcript_original!='' FROM recording_source_refs r "
                    "LEFT JOIN meetings m ON m.id=r.meeting_id WHERE r.source='granola' AND r.resource_type='meeting' "
                    "AND r.external_id IN (%s)" % ",".join("?" * len(chunk)), chunk))
        return {key[len(actor) + 1:]: value for key, value in found.items()}

    async def sync(self, actor):
        async with self.lock(actor):
            if actor in self.changing_connections:
                return
            who = Identity(actor, "human")
            if not await asyncio.to_thread(self.eligible, who):
                try:
                    saved = await asyncio.to_thread(self.load, actor)
                except Problem:
                    saved = None
                await asyncio.to_thread(self.delete, actor)
                if saved:
                    self.queue_revoke(saved[2])
                return
            saved = await asyncio.to_thread(self.load, actor)
            if not saved:
                return
            row, meta, secret = saved
            if meta["state"] != "connected":
                return
            who = Identity(actor, "human", row["email"])
            meta.update(last_attempt=self.clock(), skipped=0, skip_reasons={})
            await asyncio.to_thread(self.save, row, meta, secret)
            step = "initialize"
            skipped_error = skipped_detail = None

            def skip(reason, count=1, error=None):
                nonlocal skipped_detail
                meta["skipped"] += count
                meta["skip_reasons"][reason] = meta["skip_reasons"].get(reason, 0) + count
                skipped_detail = (error or GranolaError()).detail(reason.split(": ")[-1])
                return reason
            try:
                meta["transcripts_unavailable"] = False
                meta["plan_hint"] = "paid" if meta.get("transcript_succeeded") else meta.get("account_plan_hint", "free")
                transcript_errors = 0
                session = {}
                initialized = await self.rpc(row, meta, secret, session, "initialize", {
                    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "Tico", "version": "0.3.2"}})
                session["version"] = initialized.get("protocolVersion", "2025-03-26")
                step = "notifications/initialized"
                await self.rpc(row, meta, secret, session, "notifications/initialized", notification=True)
                tools, cursor = {}, None
                step = "tools/list"
                for _ in range(20):
                    page = await self.rpc(row, meta, secret, session, "tools/list", {"cursor": cursor} if cursor else {})
                    tools.update({t["name"]: t for t in page.get("tools", []) if isinstance(t, dict) and "name" in t})
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                else:
                    raise GranolaError("import_limit")
                if not all(name in tools for name in ("list_meetings", "get_meetings")):
                    raise GranolaError("feature_unavailable")
                if "get_account_info" in tools and not meta.get("account_info_checked"):
                    step = "get_account_info"
                    try:
                        result = await self.rpc(row, meta, secret, session, "tools/call", {
                            "name": "get_account_info", "arguments": self.arguments(tools["get_account_info"], {})})
                        meta.update(self.account_details(result))
                        meta["account_info_checked"] = True
                        meta["plan_hint"] = "paid" if meta.get("transcript_succeeded") else meta.get("account_plan_hint", "free")
                    except GranolaError as exc:
                        if exc.code in ("needs_signin", "rate_limited"):
                            raise
                        if exc.code in ("forbidden", "feature_unavailable", "bad_response"):
                            meta["account_info_checked"] = True
                    except (TypeError, ValueError, AttributeError):
                        meta["account_info_checked"] = True
                until = datetime.fromtimestamp(self.clock(), timezone.utc)
                since = meta.get("cursor") or (until - timedelta(days=30)).isoformat()
                if meta.get("cursor"):
                    # Revisit recent notes because summaries arrive after a meeting finishes.
                    since = (datetime.fromisoformat(since) - timedelta(hours=72)).isoformat()
                if meta["plan_hint"] == "free":
                    since = max(datetime.fromisoformat(since), until - timedelta(days=30)).isoformat()
                cursor, listed = None, []
                ranges = (tools["list_meetings"].get("inputSchema") or {}).get("properties", {}).get("time_range", {}).get("enum", [])
                time_range = "last_30_days" if not meta.get("cursor") and meta["plan_hint"] == "free" and "last_30_days" in ranges else "custom"
                step = "list_meetings"
                for _ in range(100):
                    value = await self.call(row, meta, secret, session, tools["list_meetings"], {
                        "since": since, "start_date": since, "after": since, "created_after": since,
                        "end_date": until.isoformat(), "until": until.isoformat(), "before": until.isoformat(),
                        "time_range": time_range, "custom_start": since, "custom_end": until.isoformat(),
                        "date_range": {"start": since, "end": until.isoformat(), "start_date": since,
                                       "end_date": until.isoformat(), "from": since, "to": until.isoformat()},
                        "involvement": {"captured_by_me": True}, "captured_by_me": True,
                        "cursor": cursor or "", "page_size": 50, "limit": 50})
                    listed.extend(self.rows(value))
                    cursor = (value.get("next_cursor") or value.get("nextCursor") or value.get("cursor")) if isinstance(value, dict) else None
                    if not cursor:
                        break
                else:
                    raise GranolaError("import_limit")
                ids, dates = [], {}
                for entry in listed:
                    try:
                        date = self.meeting_date(entry.get("created_at") or entry.get("date") or entry.get("start_time"))
                        if date and not datetime.fromisoformat(since) <= date <= until:
                            continue
                        nid = entry.get("id") or entry.get("meeting_id") or entry.get("note_id")
                        if not nid:
                            raise ValueError()
                        ids.append(str(nid))
                        dates[str(nid)] = date or datetime.fromisoformat(since)
                    except Exception:
                        skipped_error = skip("bad_response: list_meetings")
                ids = sorted(dict.fromkeys(ids), key=dates.get)
                # Notes not imported yet go first: re-reading the overlap window's imported notes (for a
                # regenerated summary) must not spend a tight quota before new meetings arrive.
                imported = await asyncio.to_thread(self.imported_ids, actor, ids)
                by_date, done, position, untried = list(ids), set(), 0, set()
                # An imported note is re-read for a late summary only while its meeting (listed start) is under a day
                # old; older ones count as done.
                settled = {nid for nid in imported if dates[nid] < until - timedelta(seconds=REVISIT)}
                done.update(settled)
                ids = [nid for nid in ids if nid not in imported] + [nid for nid in ids if nid in imported and nid not in settled]
                if len(ids) > 5000:
                    raise GranolaError("import_limit")
                id_properties = (tools["get_meetings"].get("inputSchema") or {}).get("properties", {})
                batch_size = max(1, min([10] + [v["maxItems"] for k, v in id_properties.items()
                                                if k in ("meeting_ids", "ids", "note_ids") and isinstance(v.get("maxItems"), int)]))
                if ids:
                    # Keep the first note fetch out of the burst of setup and list calls.
                    wait = session.get("last_call", 0) + FIRST_GET_MEETINGS_GAP - self.clock()
                    if wait > 0:
                        await self.sleep(wait)
                for offset in range(0, len(ids), batch_size):
                    batch = ids[offset:offset + batch_size]
                    left, failed = [], set()           # left: ids of this batch without an answer, held for a later sync
                    step = "get_meetings"
                    try:
                        value = await self.call(row, meta, secret, session, tools["get_meetings"],
                                                {"meeting_ids": batch, "ids": batch, "note_ids": batch})
                        notes = self.rows(value)
                    except GranolaError as exc:
                        if exc.code in ("unreachable", "rate_limited", "needs_signin"):
                            raise
                        # A bad ID must not hide the other meetings in its batch.
                        notes, first_codes = [], []
                        for index, nid in enumerate(batch):
                            try:
                                value = await self.call(row, meta, secret, session, tools["get_meetings"],
                                                        {"meeting_ids": [nid], "ids": [nid], "note_ids": [nid]})
                                notes.extend(self.rows(value))
                            except GranolaError as exc:
                                if exc.code in ("unreachable", "rate_limited", "needs_signin"):
                                    raise
                                skipped_error = skip(exc.code + ": get_meetings", error=exc)
                                failed.add(nid)
                                if index < 2:
                                    first_codes.append(exc.code)
                                # The first two single fetches failing alike means a systemic error, not a bad ID:
                                # the rest of the batch is counted as skipped without spending more calls.
                                if index == 1 and first_codes == [exc.code, exc.code] and len(batch) > 2:
                                    skip(skipped_error, len(batch) - 2, exc)
                                    left = batch[2:]
                                    break
                    returned = [n.get("id") or n.get("meeting_id") or n.get("note_id") for n in notes]
                    missing = [nid for nid in batch if nid not in {str(r) for r in returned if r}
                               and nid not in failed and nid not in left]
                    # A row without an id is skipped below as bad_response and stands for one missing id: count each once.
                    unexplained = len(missing) - sum(1 for r in returned if not r)
                    if unexplained > 0:
                        skipped_error = skip("missing: get_meetings", unexplained)
                    left += missing
                    for note in notes:
                        transcript = ""
                        nid = note.get("id") or note.get("meeting_id") or note.get("note_id")
                        if not nid:
                            skipped_error = skip("bad_response: get_meetings")
                            continue
                        # A re-import keeps a stored transcript and ignores a new one, so it is not fetched again.
                        if ("get_meeting_transcript" in tools and not meta.get("transcripts_unavailable")
                                and not imported.get(str(nid))):
                            step = "get_meeting_transcript"
                            try:
                                data = await self.call(row, meta, secret, session, tools["get_meeting_transcript"],
                                                       {"meeting_id": nid, "id": nid, "note_id": nid})
                                transcript = data.get("transcript", "") if isinstance(data, dict) else data if isinstance(data, (list, str)) else ""
                                if not segments_of(MeetingImport(transcript=transcript)):
                                    raise GranolaError("bad_response")
                                transcript_errors = 0
                                meta.update(transcript_succeeded=True, plan_hint="paid")
                            except Exception as exc:
                                if isinstance(exc, GranolaError) and exc.code in ("needs_signin", "rate_limited", "unreachable"):
                                    raise
                                transcript = ""
                                # Missing/malformed transcripts are not evidence of a free account.
                                if isinstance(exc, GranolaError) and exc.code == "transcripts_unavailable":
                                    meta["transcripts_unavailable"] = True
                                    meta.pop("transcript_succeeded", None)
                                    meta["plan_hint"] = "free"
                                else:
                                    transcript_errors += 1
                                    if transcript_errors >= 3:
                                        meta["transcripts_unavailable"] = True
                        step = "import_meeting"
                        try:
                            item = self.item(note, transcript)
                            if item:
                                def file_item():
                                    with self.store.transaction() as c:
                                        validate_identity(c, who)
                                        result = self.app.state.import_meeting(c, who, item, [], fill_empty=True)
                                        if not result["existing"]:
                                            meta["imported_count"] = meta.get("imported_count", 0) + 1
                                # A worker transaction must finish before cancellation releases the person lock.
                                worker = asyncio.create_task(asyncio.to_thread(file_item))
                                try:
                                    await asyncio.shield(worker)
                                except asyncio.CancelledError:
                                    await worker
                                    raise
                        except Exception:
                            skipped_error = skip("bad_response: import_meeting")
                        await asyncio.to_thread(self.save, row, meta, secret)
                    # New notes go first, so the checkpoint is the last note in list-date order with every
                    # earlier one processed: it never passes an imported note not yet revisited.
                    # Ids left untried stay out of `done`, so the checkpoint stops before them and a later sync lists them again.
                    done.update(nid for nid in batch if nid not in left)
                    untried.update(left)
                    while position < len(by_date) and by_date[position] in done:
                        position += 1
                    checkpoint = dates[by_date[position - 1]] if position else None
                    if checkpoint and (not meta.get("cursor") or checkpoint > datetime.fromisoformat(meta["cursor"])):
                        meta["cursor"] = checkpoint.isoformat()
                    await asyncio.to_thread(self.save, row, meta, secret)
                if meta.get("state") == "needs_signin":
                    return
                meta["failures"] = 0
                meta.pop("retry_after", None)
                meta["held_syncs"] = meta.get("held_syncs", 0) + 1 if untried else 0
                if meta["held_syncs"] >= HOLD_MAX:
                    # Already counted as skipped; recorded under a fixed key once the checkpoint stops waiting for them.
                    meta["skip_reasons"]["held: gave up after %d syncs" % HOLD_MAX] = len(untried)
                    meta["held_syncs"], untried = 0, set()
                meta.update(last_sync=H.now(), last_finished=self.clock(),
                            cursor=meta.get("cursor") if untried else until.isoformat(), last_error=skipped_error, last_error_detail=skipped_detail if skipped_error else None)
                if skipped_error:
                    log.warning("%s", skipped_error)
            except GranolaError as exc:
                failure = exc.code + ": " + (exc.step or step)
                log.warning("%s", failure)
                if exc.code == "needs_signin":
                    return
                meta["last_error"] = failure
                meta["last_error_detail"] = exc.detail(step)
                limited = exc.code == "rate_limited"
                meta["failures"] = min(meta.get("failures", 0) + 1, 8 if limited else 4)
                # Throttling backs off too: retrying every few minutes keeps a sliding quota exhausted.
                delay = (max(exc.retry_after or 0, min(RATE_LIMIT_MAX, RATE_LIMIT_RETRY * 2 ** (meta["failures"] - 1)))
                         if limited else SCHEDULE * 2 ** (min(meta["failures"], 4) - 1))
                meta["retry_after"] = self.clock() + delay
            except Exception:
                # In particular never persist validation errors containing provider data.
                meta["last_error"] = "sync_error: " + step
                meta["last_error_detail"] = GranolaError().detail(step)
                log.warning("%s", meta["last_error"])
            await asyncio.to_thread(self.save, row, meta, secret)

    async def trigger(self, who, interval=DEBOUNCE, now=False):
        if who.actor in self.changing_connections:
            return {"state": "off", "last_sync": None}
        if not await asyncio.to_thread(self.eligible, who):
            await self.disconnect(who)
            return {"state": "off", "last_sync": None}
        status = await asyncio.to_thread(self.status, who)
        if who.actor in self.jobs:
            return {"state": "syncing", "last_sync": status["last_sync"]}
        meta = await asyncio.to_thread(self.metadata, who.actor)
        if meta.get("previous_meta") and self.clock() >= meta.get("expires", 0):
            await self.poll(who)
            meta = await asyncio.to_thread(self.metadata, who.actor)
        if not meta or meta["state"] != "connected":
            return {"state": "needs_signin" if status["needs_signin"] else "off", "last_sync": status["last_sync"]}
        throttled = (meta.get("last_error") or "").startswith("rate_limited:")
        retry_at = self.next_retry(meta) if interval == SCHEDULE or throttled else 0
        # A person pressing Sync now (`now`, the button only) gets a real attempt once five minutes have passed since
        # the last one, even inside a backoff. Opening Meetings also asks for a sync; that one keeps to the backoff.
        if retry_at and now and self.clock() - meta.get("last_attempt", 0) >= RATE_LIMIT_RETRY:
            retry_at = 0
        if retry_at and self.clock() < retry_at:
            return {"state": "recent", "last_sync": status["last_sync"]}
        if not retry_at and self.clock() - max(meta.get("last_attempt", 0), meta.get("last_finished", 0)) < interval:
            return {"state": "recent", "last_sync": status["last_sync"]}
        if who.actor in self.jobs:
            return {"state": "syncing", "last_sync": status["last_sync"]}
        if who.actor in self.changing_connections:
            return {"state": "off", "last_sync": status["last_sync"]}
        def record_failure():
            with self.store.transaction() as c:
                row = c.execute("SELECT metadata_json FROM granola_connections WHERE actor=?", (who.actor,)).fetchone()
                if row:
                    meta = json.loads(row[0])
                    meta.update(last_attempt=self.clock(), last_error="credential_error: load_connection")
                    c.execute("UPDATE granola_connections SET metadata_json=? WHERE actor=?", (encode(meta), who.actor))
        async def run():
            try:
                await self.sync(who.actor)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("credential_error: load_connection")
                await asyncio.to_thread(record_failure)
        task = asyncio.create_task(run())
        self.jobs[who.actor] = task
        def finished(task):
            if self.jobs.get(who.actor) is task:
                self.jobs.pop(who.actor, None)
        task.add_done_callback(finished)
        return {"state": "syncing", "last_sync": status["last_sync"]}

    async def tick(self):
        def connections():
            with self.store.read() as c:
                return [dict(r) for r in c.execute("SELECT actor,email FROM granola_connections")]
        rows = await asyncio.to_thread(connections)
        active = {row["actor"] for row in rows}
        self.starts = {actor: value for actor, value in self.starts.items() if actor in active}
        for index, row in enumerate(rows):
            try:
                start = self.starts.setdefault(row["actor"], self.clock() + index * 5)
                if self.clock() >= start:
                    await self.trigger(Identity(row["actor"], "human", row["email"]), SCHEDULE)
            except Exception:
                continue

    async def loop(self, stop):
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except TimeoutError:
                pass
        await self.close()

    async def close(self):
        for task in list(self.jobs.values()) + list(self.revocations):
            task.cancel()
        await asyncio.gather(*list(self.jobs.values()), *list(self.revocations), return_exceptions=True)


def install_granola(app):
    service = app.state.granola = GranolaMCP(app)

    def person(request):
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("forbidden", "Open Meetings in your browser to connect your own Granola account", 403)
        return who

    async def safe(operation):
        try:
            return await operation
        except GranolaError as exc:
            raise Problem("granola_unavailable", "Granola: " + exc.code, 503) from None

    @app.get("/api/v2/meetings/granola")
    def status(request: Request):
        return service.status(person(request))

    @app.post("/api/v2/meetings/granola/connect")
    async def connect(request: Request):
        return await safe(service.connect(person(request)))

    @app.get("/api/v2/meetings/granola/connect/status")
    async def poll(request: Request):
        return await safe(service.poll(person(request)))

    @app.delete("/api/v2/meetings/granola/connect")
    async def disconnect(request: Request):
        return await safe(service.disconnect(person(request)))

    @app.post("/api/v2/meetings/granola/sync")
    async def sync(request: Request, now: bool = False):
        """`now`: the person pressed Sync now, so a backoff yields after five minutes since the last attempt."""
        return await service.trigger(person(request), now=now)
