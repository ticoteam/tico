"""The hub as an MCP server: `POST /api/v2/mcp`, streamable HTTP, JSON replies.

The tool table is `clients/hubtools.py`; this module only binds it to the running app. Each
`tools/call` becomes ordinary requests against this same application, carrying the caller's
own `Authorization` header through the same middleware and routes a `hub` command would hit.
There is no privileged path: whatever a bot may not do over HTTP it may not do here either.

Stateless on purpose: no session id, no server-initiated stream (GET answers 405). A
notification such as `notifications/initialized` is accepted with 202 and no body.
"""
import asyncio
import base64
import hashlib
import json
import os
import time
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import parse_qsl

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from clients import hubtools
from clients.tico import STREAM_WAIT, MultipartBody, inbound_wait_timeout
from .store import Problem

PATH = "/api/v2/mcp"
INTERNAL_TIMEOUT_S = 60
FUTURE_MARGIN_S = 5
MAX_CONTENT_BYTES = 1024 * 1024
RAW_BLOCK_BYTES = 1024 * 1024


def raw_body(raw):
    """(the request's content, its headers) for `raw` as `Client.request` sends it: a file or a MultipartBody streamed a
    block at a time with its size up front, anything else as bytes. A seekable file is rewound first, so it is sent
    whole, as its size says."""
    content_type = getattr(raw, "content_type", "application/octet-stream")
    if isinstance(raw, MultipartBody):
        return raw_blocks(raw), {"Content-Type": content_type, "Content-Length": str(raw.size)}
    if hasattr(raw, "read"):
        if hasattr(raw, "seek"):
            raw.seek(0)
        return raw_blocks(raw), {"Content-Type": content_type, "Content-Length": str(os.fstat(raw.fileno()).st_size)}
    return bytes(raw), {"Content-Type": content_type}


async def raw_blocks(source):
    """`source` read a block at a time, each read off the event loop, so the body is never held whole."""
    blocks = iter(lambda: source.read(RAW_BLOCK_BYTES), b"") if hasattr(source, "read") else iter(source)
    while (block := await asyncio.to_thread(next, blocks, None)) is not None:
        yield block


def response_content(response):
    data = response.content[:MAX_CONTENT_BYTES]
    content_type = response.headers.get("content-type", "application/octet-stream")
    disposition = Message()
    disposition["content-disposition"] = response.headers.get("content-disposition", "")
    if content_type == "application/octet-stream" and (filename := disposition.get_filename()):
        from clients.bot_files import TYPES
        content_type = TYPES.get(PurePosixPath(filename).suffix.lower(), content_type)
    is_text = content_type.startswith("text/") or content_type.split(";", 1)[0] in (
        "application/json", "application/xml", "application/javascript")
    return {"content_type": content_type, "bytes": len(data),
            "truncated": len(response.content) > MAX_CONTENT_BYTES,
            **({"text": data.decode(response.encoding or "utf-8", errors="replace")} if is_text else
               {"base64": base64.b64encode(data).decode("ascii")})}


class ApiProblem(Exception):
    """What an internal request's error body says, in the shape `hub` prints."""

    def __init__(self, code, detail, status, retryable=False, operation_id=None):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status
        self.retryable, self.operation_id = retryable, operation_id


class InProcessApi:
    """`api.get` / `api.post` / `api.patch` / `api.request` for `hubtools`, answered by this application in-process.

    Runs on a worker thread; each call is a coroutine handed to the server's event loop.
    """

    docs_wait_max = 20     # Return recoverable ids well before the HTTP proxy times out.

    def __init__(self, app, loop, authorization, base_url):
        self.app, self.loop, self.base_url = app, loop, base_url
        self.headers = {"Authorization": authorization, "Accept": "application/json",
                        "User-Agent": "Tico-MCP/" + hubtools.SERVER_INFO["version"]}

    def _send(self, method, path, body=None, key=None, query=None, delegate=False, raw=None):
        """One request through the app (its middleware and routes, with the caller's own Authorization), as a response.
        `raw` (bytes, a file or a MultipartBody) is the body as it is, never JSON."""
        if raw is not None and body is not None:
            raise ValueError("Send either a JSON body or opaque bytes, not both")
        path = "/api/v2/" + path.replace("#", "%23")
        timeout = inbound_wait_timeout(method, path, body, INTERNAL_TIMEOUT_S, query=query)
        content, raw_headers = raw_body(raw) if raw is not None else (None, {})
        if raw is not None and hasattr(raw, "read"):
            timeout = max(timeout, STREAM_WAIT)      # a file streamed from disk: stored after its last block
        async def go():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url,
                                         timeout=timeout) as client:
                headers = {**self.headers, **raw_headers}
                if delegate:
                    headers["X-Tico-On-Behalf-Of"] = delegate if isinstance(delegate, str) else "turn"
                if raw is not None or method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                    headers["Idempotency-Key"] = key or hubtools_key()
                return await client.request(method, path, json=body, content=content,
                                            headers=headers, params=query or None)
        future = asyncio.run_coroutine_threadsafe(go(), self.loop)
        try:
            response = future.result(timeout + FUTURE_MARGIN_S)
        except TimeoutError:
            future.cancel()
            raise ApiProblem("unavailable", "Tico did not answer before the request deadline", 504,
                             retryable=True, operation_id=key) from None
        return response

    def _run(self, method, path, body=None, key=None, query=None, delegate=False, raw=None):
        response = self._send(method, path, body, key, query, delegate, raw)
        if response.headers.get("content-disposition"):
            payload = response_content(response)
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = response_content(response)
        if response.status_code >= 400:
            raise problem(payload, response.status_code, key)
        return payload

    def request(self, method, path, body=None, key=None, binary=False, delegate=False, raw=None):
        """`Client.request` (clients/tico.py) for a tool that needs it: a file's bytes (`binary`, hub_file_read), a JSON
        call on a full /api/v2/ path, or an opaque body (`raw`: bytes, a file or a MultipartBody, sent as it is),
        answered through the same app and middleware as every other call here."""
        if not path.startswith("/api/v2/"):
            raise ValueError("Use a versioned Tico API path")
        path, _, query = path[len("/api/v2/"):].partition("?")
        if not binary:
            return self.call(method, path, body, key, dict(parse_qsl(query)), delegate, raw=raw)
        response = self._send(method.upper(), path, body, key, dict(parse_qsl(query)) or None, delegate, raw=raw)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            raise problem(payload, response.status_code, key)
        if response.status_code != 200:
            # A picture, video or audio file sent on a link to Tico file hosting (file_delivery.serve): not text.
            raise ApiProblem("file_integrity", "This file is a picture, video or audio file: it is not read as text",
                             415)
        data = response.content
        if len(data) > 20_000_000 or hashlib.sha256(data).hexdigest() != response.headers.get("X-Content-SHA256"):
            raise ApiProblem("file_integrity", "Tico file response failed its size or integrity check", 502)
        return data

    def get(self, path, /, **query):
        return self._run("GET", path, query={k: v for k, v in query.items() if v is not None})

    def post(self, path, body=None, key=None):
        return self._run("POST", path, body=body if body is not None else {}, key=key)

    def patch(self, path, body=None, key=None):
        return self._run("PATCH", path, body=body if body is not None else {}, key=key)

    def call(self, method, path, body=None, key=None, query=None, delegate=False, raw=None):
        method = method.upper()
        return self._run(method, path.lstrip("/"), body=body if body is not None or method != "GET" else None,
                         key=key, query={k: v for k, v in (query or {}).items() if v is not None}, delegate=delegate,
                         raw=raw)


def problem(payload, status, key):
    """An error response's body as an ApiProblem."""
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    return ApiProblem(error.get("code", "http_error"), error.get("detail") or f"HTTP {status}", status,
                      status == 429 or status >= 500, key)


def caller_kind(auth, who):
    """What kind of caller this is, for the tools it is offered (`hubtools.KINDS`): a human as owner, admin or member,
    a bot, BotOps, an external agent run by a bot (a Hermes profile), or the Assistant working in a human's private room.
    Counsel is `counsel`: its own tool list (`hubtools.COUNSEL_TOOLS`). The checks are the server's own: `auth.bot_admin`,
    `Identity.agent`, `Identity.via`."""
    from . import hubdb as H
    if who.via == "assistant":
        return "assistant"
    if who.role == "owner":
        return "owner"
    if who.role == "human":
        return "admin" if auth.bot_admin(who) else "member"
    if who.role == "bot":
        return "agent" if who.agent else "botops" if who.actor == H.bot_actor(H.FLEET_MAINTAINER) else \
            "counsel" if who.actor == "bot:" + H.COUNSEL else "bot"
    return None


def hubtools_key():
    import uuid
    return str(uuid.uuid4())


def install_mcp(app, settings):
    base_url = settings.public_url or "http://127.0.0.1"

    @app.get(PATH)
    def mcp_stream(request: Request):
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.post(PATH)
    async def mcp(request: Request):
        try:
            message = json.loads(await request.body() or b"null")
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
        if not isinstance(message, dict):
            return JSONResponse({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32600, "message": "Send one JSON-RPC message"}},
                                status_code=400)
        api = InProcessApi(app, asyncio.get_running_loop(), request.headers.get("authorization", ""), base_url)
        who = getattr(request.state, "identity", None)
        kind = caller_kind(app.state.auth, who) if who else None
        protocol = hubtools.Protocol(api, api_error=ApiProblem, kind=kind)
        started = time.monotonic()
        reply = await asyncio.to_thread(protocol.handle, message)
        if reply is None:
            return Response(status_code=202)
        if message.get("method") == "tools/call":
            await asyncio.to_thread(record_call, app, request, message, reply, time.monotonic() - started)
        return JSONResponse(reply)


def record_call(app, request, message, reply, seconds):
    """One `mcp.call` event per tool call: which tool, whose, how long on the server and how big the
    answer was (timing and stats let a connected assistant keep improving). The
    arguments and the answer are never stored, only their sizes. A failure to record never fails the
    call."""
    from . import hubdb as H
    try:
        who = getattr(request.state, "identity", None)
        if not who:
            return
        params = message.get("params") or {}
        result = reply.get("result") or {}
        error = reply.get("error") or (result.get("isError") and (result.get("content") or [{}])[0].get("text", "")[:200])
        detail = {"tool": str(params.get("name") or "")[:80], "ms": round(seconds * 1000),
                  "bytes": len(json.dumps(result)), "args_bytes": len(json.dumps(params.get("arguments") or {})),
                  "via": getattr(who, "token_label", "") or getattr(who, "agent", "") or "",
                  "error": bool(error), "error_text": str(error)[:200] if error else ""}
        with app.state.store.transaction() as c:
            H.event(c, who.actor, "mcp.call", detail["tool"], detail)
    except Exception:
        pass


def task_dry_run(c, auth, who, body):
    """The checks `task_create` would refuse on, without writing or counting a refusal."""
    from . import hubdb as H
    problems, warnings = [], []
    title, text = str(body.title or "").strip(), str(body.body or "")
    typ = H.type_get(c, body.type or H.GENERAL_TYPE)
    general = bool(typ) and typ["id"] == H.GENERAL_TYPE      # the lints shape asks, not a custom type's tickets
    target = H.resolve_actor(c, body.owner)
    if target and H.is_bot(target) and not auth.bot_access(c, who, H.actor_id(target))["see"]:
        target = None       # a bot the caller cannot see is not one they can name
    if not target or target == H.KEEPER:
        problems.append(f"{body.owner} is not a bot or a person on the roster")
    else:
        if H.is_bot(target):
            state = (H.bot(c, H.actor_id(target)) or {}).get("state")
            if state in H.INACTIVE_STATES:
                problems.append(H.target_inactive(c, H.actor_id(target), state))
            elif state != "active":
                problems.append(f"{H.actor_id(target)} is {state}, not active")
            if not auth.bot_access(c, who, H.actor_id(target))["write"]:
                problems.append(f"you can see {H.actor_id(target)} but may not give it work; "
                                "ask the person who runs it for Write access")
            if not auth.bot_contact(c, who, H.actor_id(target)):
                problems.append(f"{H.actor_id(target)} does not take tasks from other bots; ask its "
                                "manager or a person to pass this on")
        if not title:
            problems.append("give it a title that says what you are asking for")
        elif H.is_human(target) and general and H.STYLE_LINT != "off":
            # writing problems: the create is accepted with these as warnings unless TICO_STYLE_LINT=refuse
            (problems if H.STYLE_LINT == "refuse" else warnings).extend(H.lint_human_item(text, title=title))
        if title and H.is_bot(who.actor) and H.is_human(target) and general:
            # the ask that opens a person's task is always refused when it is missing or unclear
            problems += list(filter(None, [H.person_ask_refusal(target, H.person_ask_of_body(text, title), "assign")]))
        if H.is_bot(who.actor) and H.TITLE_LINT != "off" and general:
            # plain-English titles: recorded on the task this week, refused once TICO_TITLE_LINT=refuse
            problems += [f"{p} (title lint, {H.TITLE_LINT})" for p in H.lint_title(title)]
        dup = H._one(c, "SELECT id FROM tasks WHERE requester_id=? AND owner_id=? AND title=? "
                        f"AND status IN ({','.join('?' * len(H.ACTIVE_STATUSES))})",
                     (who.actor, target, title, *H.ACTIVE_STATUSES))
        if dup and H.task_private_readable(c, who.actor, H.task(c, dup["id"])) and (
                not who.task_actor or H.task_private_readable(c, who.task_actor, H.task(c, dup["id"]))):
            problems.append(f"{dup['id']} already asks {H.actor_id(target)} for this")
        # The create's own privacy refusals, word for word (task_privacy.require_create): private work handed to
        # someone who cannot read it, or a private task's guest refused.
        from . import task_privacy as privacy
        from .store import Problem
        try:
            parents = [r.task for r in body.relations if r.kind == "parent"]
            parent = H.task(c, auth.task(c, who, auth.resolve_task(c, who, parents[0]))["id"]) if parents else None
            related = [auth.resolve_task(c, who, r.task) for r in body.relations if r.kind != "parent"]
            privacy.require_create(c, who, body.private, target, parent, title=body.title, body=body.body,
                                   from_private_work=body.from_private, texts=privacy.create_texts(body),
                                   related=related)
        except Problem as exc:
            problems.append(exc.detail)
    if body.roles:
        from . import task_roles as TRo
        try:
            for people in (TRo.clean(body.roles) or {}).values():
                problems += [f"{p} is not a bot or a person on the roster" for p in people if not H.resolve_actor(c, p)]
        except ValueError as exc:
            problems.append(str(exc))
    if not typ:
        problems.append("No such task type")
    elif body.step:
        step = next((s for s in typ["steps"] if s["id"] == body.step or s["name"] == body.step), None)
        if not step:
            problems.append("No such step in this task type")
        elif step["status"] == "ready" and H.is_bot(who.actor):
            problems.append("Ready to ship is set when the pull request merges, not by the bot")
        elif step["status"] == "waiting" and who.actor == target and H.is_bot(target):
            problems.append("A self-requested task needs something to wait on first")
    return {"ok": not problems, "owner": target, "problems": problems, **({"warnings": warnings} if warnings else {})}
