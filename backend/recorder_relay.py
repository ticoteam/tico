"""The Tico Recorder's speech and assist, through the install's Tico AI Gateway with the install's gateway key.

The recorder holds no key of its own: it signs in as a person and these routes relay to the Gateway that
backend/gateway_connection.py holds. Only a company human may use them; a bot or an anonymous caller is refused.

    GET  /api/v2/recorder/services               which of transcribe, assist and stream this install can use
    POST /api/v2/recorder/audio/transcriptions   the Gateway's speech-to-text body and answer, unchanged (25 MB)
    POST /api/v2/recorder/chat/completions       the Gateway's chat, not streamed, on the install's meeting model
    WS   /api/v2/recorder/stream?<query>         the Gateway's live speech stream, frames relayed unchanged

A Gateway error is passed on with its status and body, so the recorder can show it. Nothing here returns the key.
"""

import asyncio
import json
import threading
import time

from fastapi import Request, WebSocket
from fastapi.responses import Response
from websockets.asyncio.client import connect as connect_upstream
from websockets.exceptions import InvalidStatus, WebSocketException

from clients import gateway as G

from . import gateway_connection, providers, views
from .store import Problem

TRANSCRIBE_LIMIT = 25 * 1024 * 1024
# What a chat request may carry on to the Gateway; its other fields are the install's to choose, not the recorder's.
CHAT_FIELDS = ("messages", "response_format", "temperature", "max_tokens")
SERVICES_TTL = 300
PROBE_TIMEOUT = 8
TRANSCRIBE_TIMEOUT = 120
CHAT_TIMEOUT = 60
QUERY_LIMIT = 2048
CLOSE_CODES = (1000, 1001, 1008, 1011, 1013)
OFF = {"transcribe": False, "assist": False, "stream": False}

_services, _services_lock = {}, threading.Lock()
_now = time.monotonic     # tests move it


def _gateway(store, vault, settings):
    """(key, base, fingerprint) of the install's Gateway connection, read once."""
    with store.read() as c:
        return gateway_connection.target(c, vault, settings)


def _meeting_model(store, settings):
    with store.read() as c:
        return chat_model(c, settings)


def _check(settings, key):
    """The Problem a relay refuses with before it reaches the Gateway, or None."""
    if settings.rehearsal:
        return Problem("rehearsal", "The recorder's speech is off in rehearsal mode; nothing is sent to the Gateway", 503)
    if not key:
        return Problem("gateway_not_connected", "The Tico AI Gateway is not connected; the owner saves a gateway key in "
                       "Settings > Computers", 409)
    return None


def _probe(base, key):
    """What the Gateway says it serves: `transcribe` and `stream` from its `GET <base>/audio`. Any failure is all off."""
    status, body = G.relay("GET", base + "/audio", headers={"Authorization": "Bearer " + key}, timeout=PROBE_TIMEOUT)
    try:
        payload = json.loads(body) if status == 200 else None
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return dict(OFF)
    return {"transcribe": _available(payload, "transcribe"), "assist": True, "stream": _available(payload, "stream")}


def _available(payload, name):
    part = payload.get(name)
    return isinstance(part, dict) and part.get("available") is True


def services(store, vault, settings):
    """`{"transcribe","assist","stream"}`: assist needs a Gateway connection, transcribe and stream what the Gateway says
    (its answer kept for SERVICES_TTL seconds, per key and address). Off with no connection, and off on any Gateway error."""
    key, base, fingerprint = _gateway(store, vault, settings)
    if not key or settings.rehearsal:
        return dict(OFF)
    now = _now()
    with _services_lock:
        hit = _services.get((base, fingerprint))
    if hit and hit[0] > now:
        return dict(hit[1])
    answer = _probe(base, key)
    with _services_lock:
        _services[(base, fingerprint)] = (now + SERVICES_TTL, answer)
    return dict(answer)


def chat_model(c, settings):
    """The model a recorder's chat runs on: the install's meeting model when it is a Tico model, else the default one."""
    meeting = providers.load(c, settings).get("meeting_model") or ""
    return meeting if gateway_connection.is_tico(meeting) else providers.MEETING_MODEL


def stream_url(base, query):
    """The Gateway's live speech stream address for `query` (the same query as xAI's): wss, or ws to this computer."""
    base = G.safe_base(base)
    scheme = "wss" if base.startswith("https://") else "ws"
    return scheme + base[base.index("://"):] + "/audio/stream" + ("?" + query if query else "")


def _passed_on(status, body):
    """The Gateway's answer as the recorder gets it: its status and body, or 503 when the Gateway did not answer."""
    if status == 0:
        raise Problem("gateway_unreachable", "The Tico AI Gateway did not answer; try again", 503, retryable=True)
    return Response(content=body, status_code=status, media_type="application/json")


def install_recorder_relay(app, store, auth, settings):
    @app.get("/api/v2/recorder/services")
    def recorder_services(request: Request):
        views.human_only(request.state.identity)
        return services(store, app.state.vault, settings)

    @app.post("/api/v2/recorder/audio/transcriptions")
    async def recorder_transcriptions(request: Request):
        views.human_only(request.state.identity)
        body = await request.body()
        if len(body) > TRANSCRIBE_LIMIT:
            raise Problem("too_large", "The audio exceeds the 25 MB limit; send shorter clips", 413)
        key, base, _ = await asyncio.to_thread(_gateway, store, app.state.vault, settings)
        refused = _check(settings, key)
        if refused:
            raise refused
        status, answer = await asyncio.to_thread(G.relay, "POST", base + "/audio/transcriptions", body,
                                                 {"Authorization": "Bearer " + key}, TRANSCRIBE_TIMEOUT)
        return _passed_on(status, answer)

    @app.post("/api/v2/recorder/chat/completions")
    async def recorder_chat(request: Request):
        views.human_only(request.state.identity)
        try:
            payload = json.loads(await request.body() or b"{}")
        except ValueError:
            raise Problem("bad_request", "The chat request is not JSON", 422) from None
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list) or not messages:
            raise Problem("messages", "Send the chat messages", 422)
        key, base, _ = await asyncio.to_thread(_gateway, store, app.state.vault, settings)
        refused = _check(settings, key)
        if refused:
            raise refused
        model = await asyncio.to_thread(_meeting_model, store, settings)
        body = {**{name: payload[name] for name in CHAT_FIELDS if name in payload}, "model": model, "stream": False}
        status, answer = await asyncio.to_thread(G.relay, "POST", base + "/chat/completions",
                                                 json.dumps(body).encode(), {"Authorization": "Bearer " + key},
                                                 CHAT_TIMEOUT)
        return _passed_on(status, answer)

    @app.websocket("/api/v2/recorder/stream")
    async def recorder_stream(websocket: WebSocket):
        try:
            who = await asyncio.to_thread(auth.authenticate, websocket.headers, websocket.url.path, "GET")
            views.human_only(who)
            key, base, _ = await asyncio.to_thread(_gateway, store, app.state.vault, settings)
            refused = _check(settings, key)
            if refused:
                raise refused
            address = stream_url(base, websocket.url.query[:QUERY_LIMIT])
        except Problem as exc:
            # Refused before the handshake: the recorder's socket never opens.
            await websocket.close(code=1008, reason=exc.code)
            return
        except ValueError:
            # The Gateway URL is not one the key may be sent to (G.safe_base).
            await websocket.close(code=1008, reason="gateway_url_invalid")
            return
        await websocket.accept()
        try:
            upstream = await connect_upstream(address, additional_headers={"Authorization": "Bearer " + key},
                                              user_agent_header=G.user_agent(), open_timeout=10)
        except InvalidStatus as exc:
            # The Gateway refuses the stream (503 stream_unavailable when it has no live speech service).
            reason = "stream_unavailable" if exc.response.status_code == 503 else f"stream_refused_{exc.response.status_code}"
            await websocket.close(code=1011, reason=reason)
            return
        except (WebSocketException, OSError, TimeoutError):
            await websocket.close(code=1011, reason="stream_unreachable")
            return
        await _relay(websocket, upstream)


async def _relay(websocket, upstream):
    """Frames both ways, unchanged, until either side closes. The other side then closes with the Gateway's outcome."""
    async def from_recorder():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await upstream.send(message["text"])
            elif message.get("bytes") is not None:
                await upstream.send(message["bytes"])

    async def from_gateway():
        async for message in upstream:
            if isinstance(message, str):
                await websocket.send_text(message)
            else:
                await websocket.send_bytes(message)

    recorder, gateway = asyncio.ensure_future(from_recorder()), asyncio.ensure_future(from_gateway())
    try:
        await asyncio.wait({recorder, gateway}, return_when=asyncio.FIRST_COMPLETED)
        gateway_first = gateway.done()
    finally:
        recorder.cancel()
        gateway.cancel()
        await asyncio.gather(recorder, gateway, return_exceptions=True)
        await upstream.close()
    if not gateway_first:
        return                                   # the recorder left: there is no one to send a close frame to
    try:
        # A close code a close frame may carry; an abnormal one (1006) is reported as a server error.
        code = upstream.close_code if upstream.close_code in CLOSE_CODES else 1011
        await websocket.close(code=code, reason=upstream.close_reason or "")
    except (RuntimeError, OSError):
        # The recorder has gone already.
        pass
