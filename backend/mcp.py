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
import json
import time
from email.message import Message
from pathlib import PurePosixPath

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from clients import hubtools
from .store import Problem

PATH = "/api/v2/mcp"
INTERNAL_TIMEOUT_S = 60
MAX_CONTENT_BYTES = 1024 * 1024


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
    """`api.get` / `api.post` / `api.patch` for `hubtools`, answered by this application in-process.

    Runs on a worker thread; each call is a coroutine handed to the server's event loop.
    """

    docs_wait_max = 20     # Return recoverable ids well before the HTTP proxy times out.

    def __init__(self, app, loop, authorization, base_url):
        self.app, self.loop, self.base_url = app, loop, base_url
        self.headers = {"Authorization": authorization, "Accept": "application/json",
                        "User-Agent": "Tico-MCP/" + hubtools.SERVER_INFO["version"]}

    def _run(self, method, path, body=None, key=None, query=None, delegate=False):
        async def go():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url,
                                         timeout=INTERNAL_TIMEOUT_S) as client:
                headers = dict(self.headers)
                if delegate:
                    headers["X-Tico-On-Behalf-Of"] = delegate if isinstance(delegate, str) else "turn"
                if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                    headers["Idempotency-Key"] = key or hubtools_key()
                # A task number ("tasks/#18945") is in the path, not a fragment.
                return await client.request(method, "/api/v2/" + path.replace("#", "%23"), json=body,
                                            headers=headers, params=query or None)
        response = asyncio.run_coroutine_threadsafe(go(), self.loop).result(INTERNAL_TIMEOUT_S + 5)
        if response.headers.get("content-disposition"):
            payload = response_content(response)
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = response_content(response)
        if response.status_code >= 400:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if not isinstance(error, dict):
                error = {}
            raise ApiProblem(error.get("code", "http_error"), error.get("detail") or f"HTTP {response.status_code}",
                             response.status_code, response.status_code == 429 or response.status_code >= 500,
                             key)
        return payload

    def get(self, path, **query):
        return self._run("GET", path, query={k: v for k, v in query.items() if v is not None})

    def post(self, path, body=None, key=None):
        return self._run("POST", path, body=body if body is not None else {}, key=key)

    def patch(self, path, body=None, key=None):
        return self._run("PATCH", path, body=body if body is not None else {}, key=key)

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        method = method.upper()
        return self._run(method, path.lstrip("/"), body=body if body is not None or method != "GET" else None,
                         key=key, query={k: v for k, v in (query or {}).items() if v is not None}, delegate=delegate)


def caller_kind(auth, who):
    """What kind of caller this is, for the tools it is offered (`hubtools.KINDS`): a human as owner, admin or member,
    a bot, BotOps, an external agent run by a bot (a Hermes profile), or the Assistant working in a human's private room.
    The checks are the server's own: `auth.bot_admin`, `Identity.agent`, `Identity.via`."""
    from . import hubdb as H
    if who.via == "assistant":
        return "assistant"
    if who.role == "owner":
        return "owner"
    if who.role == "human":
        return "admin" if auth.bot_admin(who) else "member"
    if who.role == "bot":
        return "agent" if who.agent else "botops" if who.actor == H.bot_actor(H.FLEET_MAINTAINER) else "bot"
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
            if state != "active":
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
        if H.is_bot(who.actor) and H.TITLE_LINT != "off" and general:
            # plain-English titles: recorded on the task this week, refused once TICO_TITLE_LINT=refuse
            problems += [f"{p} (title lint, {H.TITLE_LINT})" for p in H.lint_title(title)]
        dup = H._one(c, "SELECT id FROM tasks WHERE requester=? AND owner=? AND title=? "
                        f"AND status IN ({','.join('?' * len(H.ACTIVE_STATUSES))})",
                     (who.actor, target, title, *H.ACTIVE_STATUSES))
        if dup and H.task_private_readable(c, who.actor, H.task(c, dup["id"])) and (
                not who.task_actor or H.task_private_readable(c, who.task_actor, H.task(c, dup["id"]))):
            problems.append(f"{dup['id']} already asks {H.actor_id(target)} for this")
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
