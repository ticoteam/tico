"""The recorder's speech and assist relay (backend/recorder_relay.py): who may call it, what it asks the Gateway, and what
the recorder gets back. Tico HQ is a stub here: nothing reaches the network."""

import asyncio
import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import InvalidStatus
from websockets.http11 import Headers, Response

from clients import gateway as G
from backend import gateway_connection, providers, recorder_relay
from backend.store import H, digest
from backend.tests.test_api import api, headers  # noqa: F401

HUMAN, BOT = "ana-test", "synthetic-recorder-bot-agent"
OFF = {"transcribe": False, "assist": False, "stream": False}
AUDIO = {"transcribe": {"available": True, "model": "tico-transcribe-1.0"}, "stream": {"available": True}}


@pytest.fixture
def relay(api, monkeypatch):
    """The install has a Gateway key; HQ's calls go to `calls` through `answers`, which a test sets."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
            ("ops", "hermes", digest(BOT), H.now(), "human:ana"),
        )
    settings = api.app.state.store.settings
    monkeypatch.setattr(settings, "gateway_key", "synthetic-gateway-key", raising=False)
    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)
    monkeypatch.setattr(recorder_relay, "_services", {})
    calls, answers = [], {}

    def fake_relay(method, url, body=None, headers=None, timeout=G.TIMEOUT):
        calls.append({"method": method, "url": url, "body": body, "headers": headers})
        return answers.get(url.rsplit("/v1/gateway", 1)[-1], (200, json.dumps(AUDIO).encode()))

    monkeypatch.setattr(G, "relay", fake_relay)
    return type("Relay", (), {"api": api, "calls": calls, "answers": answers, "settings": settings})


def test_a_company_human_reaches_the_recorder_and_a_bot_or_no_one_is_refused(relay):
    api = relay.api
    assert api.get("/api/v2/recorder/services", headers=headers(BOT)).status_code == 403
    assert api.get("/api/v2/recorder/services").status_code in (401, 403)
    chat = {"messages": [{"role": "user", "content": "hi"}]}
    assert api.post("/api/v2/recorder/chat/completions", headers=headers(BOT), json=chat).status_code == 403
    assert relay.calls == []  # a refused caller costs the Gateway nothing
    assert api.get("/api/v2/recorder/services", headers=headers(HUMAN)).status_code == 200


def test_without_a_gateway_key_nothing_is_offered_and_nothing_is_asked(relay, monkeypatch):
    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: False)
    monkeypatch.setattr(relay.settings, "gateway_key", "", raising=False)
    assert relay.api.get("/api/v2/recorder/services", headers=headers(HUMAN)).json() == OFF
    assert relay.calls == []
    refused = relay.api.post(
        "/api/v2/recorder/chat/completions",
        headers=headers(HUMAN),
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "gateway_not_connected"


def test_services_reads_the_gateways_audio_answer_and_keeps_it_for_five_minutes(relay, monkeypatch):
    clock = {"at": 1000.0}
    monkeypatch.setattr(recorder_relay, "_now", lambda: clock["at"])
    first = relay.api.get("/api/v2/recorder/services", headers=headers(HUMAN)).json()
    again = relay.api.get("/api/v2/recorder/services", headers=headers(HUMAN)).json()
    assert first == again == {"transcribe": True, "assist": True, "stream": True}
    probes = [call for call in relay.calls if call["url"].endswith("/audio")]
    assert len(probes) == 1 and probes[0]["method"] == "GET"
    assert probes[0]["headers"]["Authorization"] == "Bearer synthetic-gateway-key"
    clock["at"] += recorder_relay.SERVICES_TTL + 1
    relay.answers["/audio"] = (
        200,
        json.dumps({"transcribe": {"available": True}, "stream": {"available": False}}).encode(),
    )
    later = relay.api.get("/api/v2/recorder/services", headers=headers(HUMAN)).json()
    assert later == {"transcribe": True, "assist": True, "stream": False}


@pytest.mark.parametrize("answer", [(503, b"{}"), (0, b""), (200, b"not json")])
def test_a_gateway_error_turns_the_services_off_and_never_a_server_error(relay, answer):
    relay.answers["/audio"] = answer
    response = relay.api.get("/api/v2/recorder/services", headers=headers(HUMAN))
    assert response.status_code == 200
    assert response.json() == OFF


def test_chat_runs_on_the_install_meeting_model_and_keeps_only_the_recorder_fields(relay):
    relay.answers["/chat/completions"] = (200, json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
    sent = {
        "model": "gpt-4o",
        "stream": True,
        "tools": [{"name": "x"}],
        "messages": [{"role": "user", "content": "Q"}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    response = relay.api.post("/api/v2/recorder/chat/completions", headers=headers(HUMAN), json=sent)
    assert response.status_code == 200
    assert response.json() == {"choices": [{"message": {"content": "{}"}}]}
    forwarded = json.loads(relay.calls[-1]["body"])
    assert forwarded == {
        "model": providers.MEETING_MODEL,
        "stream": False,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": "Q"}],
        "response_format": {"type": "json_object"},
    }
    assert relay.calls[-1]["url"].endswith("/v1/gateway/chat/completions")


def test_chat_passes_the_gateways_error_on_with_its_status(relay):
    relay.answers["/chat/completions"] = (429, b'{"error":{"code":"out_of_credit"}}')
    response = relay.api.post(
        "/api/v2/recorder/chat/completions",
        headers=headers(HUMAN),
        json={"messages": [{"role": "user", "content": "Q"}]},
    )
    assert response.status_code == 429
    assert response.json() == {"error": {"code": "out_of_credit"}}


def test_chat_needs_messages_and_refuses_a_body_over_the_size_limit(relay):
    empty = relay.api.post("/api/v2/recorder/chat/completions", headers=headers(HUMAN), json={"messages": []})
    assert empty.status_code == 422
    huge = {"messages": [{"role": "user", "content": "x" * (2_100_000)}]}
    assert relay.api.post("/api/v2/recorder/chat/completions", headers=headers(HUMAN), json=huge).status_code == 413


def test_transcription_passes_the_body_through_and_the_gateways_answer_back(relay):
    body = {"model": "tico-transcribe-1.0", "input_audio": {"data": "UklGRg==", "format": "wav"}, "language": "en"}
    relay.answers["/audio/transcriptions"] = (200, b'{"text":"hello there","words":[]}')
    response = relay.api.post("/api/v2/recorder/audio/transcriptions", headers=headers(HUMAN), json=body)
    assert response.status_code == 200 and response.json() == {"text": "hello there", "words": []}
    assert json.loads(relay.calls[-1]["body"]) == body


def test_transcription_passes_an_error_and_its_status_on(relay):
    relay.answers["/audio/transcriptions"] = (402, b'{"error":{"message":"Out of credit"}}')
    response = relay.api.post("/api/v2/recorder/audio/transcriptions", headers=headers(HUMAN), json={"model": "x"})
    assert response.status_code == 402
    assert response.json() == {"error": {"message": "Out of credit"}}


def test_transcription_refuses_audio_over_25_mb_before_it_reaches_the_gateway(relay):
    too_big = b"{" + b" " * (25 * 1024 * 1024) + b"}"
    response = relay.api.post(
        "/api/v2/recorder/audio/transcriptions",
        headers={**headers(HUMAN), "Content-Type": "application/json"},
        content=too_big,
    )
    assert response.status_code == 413
    assert not any(call["url"].endswith("/audio/transcriptions") for call in relay.calls)


class FakeStream:
    """The Gateway's end of the live stream: `incoming` arrive first, then it stays open until closed."""

    def __init__(self, incoming=(), close_code=1000, ends=False):
        self.incoming, self.sent, self.closed = list(incoming), [], asyncio.Event()
        self.close_code, self.close_reason, self.ends = close_code, "", ends

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed.set()

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        for message in self.incoming:
            yield message
        if not self.ends:
            await self.closed.wait()


def stream_stub(relay, monkeypatch, stream=None, refuse=None):
    opened = {}

    async def connect(address, additional_headers=None, **options):
        opened.update(address=address, headers=additional_headers)
        if refuse is not None:
            raise InvalidStatus(Response(refuse, "Service Unavailable", Headers(), b""))
        return stream

    monkeypatch.setattr(recorder_relay, "connect_upstream", connect)
    return opened


def _closed_within(stream, seconds):
    deadline = time.monotonic() + seconds
    while not stream.closed.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    return stream.closed.is_set()


def test_the_live_stream_relays_frames_both_ways_and_closes_both_sides(relay, monkeypatch):
    stream = FakeStream(incoming=["partial words", b"\x00\x01"])
    opened = stream_stub(relay, monkeypatch, stream=stream)
    query = "encoding=linear16&sample_rate=16000&interim_results=true"
    with relay.api.websocket_connect("/api/v2/recorder/stream?" + query, headers=headers(HUMAN)) as ws:
        assert ws.receive_text() == "partial words"
        assert ws.receive_bytes() == b"\x00\x01"
        ws.send_bytes(b"audio frame")
        ws.send_text('{"type":"KeepAlive"}')
        # The recorder leaves first. The relay closes the Gateway's side before the test client ends the session
        # (which cancels the app), so wait for that here.
        ws.close()
        assert _closed_within(stream, 5)
    assert stream.sent == [b"audio frame", '{"type":"KeepAlive"}']
    assert stream.closed.is_set()
    assert opened["address"] == "wss://updates.tico.team/v1/gateway/audio/stream?" + query
    assert opened["headers"] == {"Authorization": "Bearer synthetic-gateway-key"}


def test_when_the_gateway_closes_the_stream_the_recorder_is_closed_with_its_code(relay, monkeypatch):
    stream_stub(relay, monkeypatch, stream=FakeStream(incoming=["final words"], close_code=1011, ends=True))
    with relay.api.websocket_connect("/api/v2/recorder/stream?encoding=linear16", headers=headers(HUMAN)) as ws:
        assert ws.receive_text() == "final words"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1011


def test_the_live_stream_says_why_when_the_gateway_refuses_it(relay, monkeypatch):
    stream_stub(relay, monkeypatch, refuse=503)
    with relay.api.websocket_connect("/api/v2/recorder/stream?encoding=linear16", headers=headers(HUMAN)) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1011
    assert closed.value.reason == "stream_unavailable"


def test_a_bot_or_no_one_gets_no_live_stream(relay, monkeypatch):
    opened = stream_stub(relay, monkeypatch, stream=FakeStream())
    for token in (BOT, None):
        extra = {"Authorization": "Bearer " + token} if token else {}
        with pytest.raises(WebSocketDisconnect) as refused:
            with relay.api.websocket_connect("/api/v2/recorder/stream", headers=extra):
                pass
        assert refused.value.code == 1008
    assert opened == {}  # the Gateway is never dialled for them
