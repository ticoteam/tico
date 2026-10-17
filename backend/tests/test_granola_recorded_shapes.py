"""Granola's answers as they really arrive, replayed through the real client: every answer gzipped, notes and
transcripts after a line of provider text, and throttling in each of its three forms. The fixtures in
backend/tests/fixtures/granola/ are rebuilt from the two Granola incidents and Granola's docs, not yet a live
recording; scripts/granola_live_check.py checks Granola still answers this way, and its --record output replays here."""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from backend.tests.test_granola_mcp import BASE, Provider, api, headers  # noqa: F401
from scripts import granola_live_check

FIXTURE = Path(__file__).parent / "fixtures" / "granola" / "mcp_shapes.json"
EXCHANGES = json.loads(FIXTURE.read_text())["exchanges"]
IDS = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"]


def with_id(message, request_id):
    return json.loads(json.dumps(message).replace('"<request id>"', json.dumps(request_id)))


def body_of(exchange, request_id=None):
    """The bytes Granola sends: compressed when it was, an event stream when it was."""
    if "events" in exchange:
        body = "".join("event: message\ndata: " + json.dumps(with_id(e, request_id)) + "\n\n"
                       for e in exchange["events"]).encode()
    elif "json" in exchange:
        body = json.dumps(with_id(exchange["json"], request_id)).encode()
    else:
        body = b""
    if httpx.Headers(exchange["headers"]).get("content-encoding") == "gzip":   # a recording's names are lowercase
        body = gzip.compress(body)
    return body


def served(name, request_id=None, exchanges=EXCHANGES):
    exchange = exchanges[name]
    return httpx.Response(exchange["status"], headers=exchange["headers"], content=body_of(exchange, request_id))


class Recorded(Provider):
    """Answers only with recorded shapes; `throttles` puts recorded rate limits ahead of a tool's answer."""

    def __init__(self, api, notes="get_meetings", exchanges=EXCHANGES):
        self.notes, self.throttles, self.notes_calls, self.tools = notes, {}, [], []
        self.exchanges = exchanges
        super().__init__(api)
        self.now = datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp()

    def handle(self, request):
        path = request.url.path
        self.calls.append((path, request))
        oauth = {"/oauth2/register": "register", "/oauth2/device_authorization": "device_authorization",
                 "/oauth2/token": "token", "/.well-known/oauth-authorization-server": "authorization_server"}
        if path in oauth:
            return served(oauth[path], exchanges=self.exchanges)
        if path == "/oauth2/revoke":
            return httpx.Response(200)
        assert path == "/mcp"
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return served("initialize", body["id"], exchanges=self.exchanges)
        assert request.headers["Mcp-Session-Id"] == "session-placeholder"
        if body["method"] == "notifications/initialized":
            return served("initialized", exchanges=self.exchanges)
        if body["method"] == "tools/list":
            return served("tools_list", body["id"], exchanges=self.exchanges)
        name = body["params"]["name"]
        self.tools.append(name)
        if name == "get_meetings":
            self.notes_calls.append(body["params"]["arguments"]["meeting_ids"])
        if self.throttles.get(name):
            return served(self.throttles[name].pop(0), body["id"], exchanges=self.exchanges)
        shape = {"list_meetings": "list_meetings", "get_meetings": self.notes,
                 "get_meeting_transcript": "transcript"}[name]
        return served(shape, body["id"], exchanges=self.exchanges)


def imported(api):
    with api.app.state.store.read() as c:
        return {row["external_id"]: row["notes"] for row in c.execute(
            "SELECT r.external_id,m.notes FROM meetings m JOIN recording_source_refs r ON r.meeting_id=m.id "
            "WHERE r.source='granola' ORDER BY r.external_id")}


def test_the_fixtures_match_what_the_live_check_expects():
    assert granola_live_check.check_fixture(FIXTURE) == []


@pytest.mark.parametrize("notes", ["get_meetings", "get_meetings_event_stream"])
def test_connect_and_sync_read_compressed_answers_with_provider_text_once(api, notes):
    provider = Recorded(api, notes)
    status = provider.connect()
    assert status["connected"] and not status.get("last_error"), status    # v0.3.4: gzip read twice = "unreachable"
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert not status["last_error"], status["last_error"]                     # v0.3.7: intro text = bad_response
    assert status["imported_count"] == 2 and status["skipped"] == 0
    notes_by_id = imported(api)
    assert notes_by_id == {"human:ana:" + IDS[0]: "## Decisions\n- Budget < 15% & plan approved",
                           "human:ana:" + IDS[1]: "## Notes\n- Shared placeholder"}
    assert "Private placeholder" not in json.dumps(notes_by_id)
    assert provider.notes_calls == [IDS]


@pytest.mark.parametrize("throttle", granola_live_check.RATE_LIMITS)
def test_each_recorded_rate_limit_is_waited_out_without_skipping_a_meeting(api, throttle):
    provider = Recorded(api)
    provider.connect()
    provider.throttles["get_meetings"] = [throttle, throttle]   # v0.3.7: a throttled batch skipped its meetings
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert not status["last_error"], status["last_error"]
    assert status["imported_count"] == 2 and status["skipped"] == 0
    assert set(imported(api)) == {"human:ana:" + i for i in IDS}
    assert provider.notes_calls == [IDS, IDS, IDS]              # the same batch again, never split or dropped
    assert [s for s in provider.sleeps if s >= 5][-2:] == [20, 60]   # Retry-After 5 is under the spaced waits


@pytest.mark.parametrize("name,change,drift", [
    ("get_meetings", lambda e: e["json"]["result"]["content"][0].update(text="Your notes are being generated."),
     "get_meetings: Tico's parser reads no meetings from the result"),
    ("rate_limit_tool_error", lambda e: e["json"]["result"]["content"][0].update(text="Quota reached, try later"),
     "rate_limit_tool_error: Tico does not read it as a rate limit"),
    ("tools_list", lambda e: e["json"]["result"]["tools"].pop(1), "tools_list: no get_meetings tool"),
    ("initialize", lambda e: e["headers"].pop("Mcp-Session-Id"), "initialize: no Mcp-Session-Id header"),
])
def test_the_live_check_names_a_shape_change(name, change, drift):
    exchange = json.loads(json.dumps(EXCHANGES[name]))
    change(exchange)
    assert granola_live_check.shape_problems(name, exchange) == [drift]


def test_the_live_check_skips_without_a_credential(monkeypatch, capsys):
    monkeypatch.delenv("GRANOLA_MCP_TOKEN", raising=False)
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    assert granola_live_check.main([]) == 0
    assert capsys.readouterr().out.startswith("SKIP")


def test_a_recording_keeps_the_shape_and_drops_people_and_tokens():
    real = {"token": {"status": 200, "headers": {}, "json": {"access_token": "live-access", "refresh_token": "live-refresh",
                                                             "expires_in": 3600}},
            "initialize": {"status": 200, "headers": {"Mcp-Session-Id": "live-session"}, "json": {"result": {}}},
            "get_meetings": {"status": 200, "headers": {}, "json": {"result": {"content": [{"type": "text", "text": (
                'Here are the notes:\n<meetings_data count="1"><meeting id="8f14e45f-ceea-467f-a0e6-7a1f2b3c4d5e" '
                'title="Board prep with Dana"><known_participants>Dana Reyes from Initech <dana@initech.example>'
                '</known_participants><summary>Raise at $12M</summary></meeting></meetings_data>')}]}}}}
    scrubbed = granola_live_check.scrub(real)
    text = json.dumps(scrubbed)
    for secret in ("live-access", "live-refresh", "live-session", "Dana", "Initech", "dana@", "$12M", "Board prep",
                   "8f14e45f"):
        assert secret not in text
    assert scrubbed["token"]["json"]["expires_in"] == 3600
    assert granola_live_check.shape_problems("get_meetings", scrubbed["get_meetings"]) == []


def live_granola(notes="get_meetings", throttle=None, public=None):
    """A fake Granola for the live check: recorded MCP answers, or a throttle for every notes request."""
    def handle(request):
        if request.url.host == "public-api.granola.ai":
            return public or httpx.Response(200, json={"notes": [{"id": "not_ABCDEFGHIJKLMN"}], "hasMore": False})
        body = json.loads(request.content)
        method, name = body["method"], body.get("params", {}).get("name")
        shape = {"initialize": "initialize", "notifications/initialized": "initialized", "tools/list": "tools_list"}.get(
            method) or (throttle or notes if name == "get_meetings" else name)
        return served(shape, body.get("id"))
    return httpx.MockTransport(handle)


@pytest.fixture
def live(monkeypatch):
    sleeps = []
    monkeypatch.setattr(granola_live_check, "SLEEP", sleeps.append)
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    monkeypatch.setenv("GRANOLA_MCP_TOKEN", "fake-access-sensitive")
    return sleeps


def test_the_live_check_says_ok_only_when_every_probe_answered(live, monkeypatch, capsys):
    monkeypatch.setattr(granola_live_check, "TRANSPORT", live_granola())
    assert granola_live_check.main([]) == 0
    assert capsys.readouterr().out.strip().endswith("OK: Granola's answers match the fixtures' shapes")


@pytest.mark.parametrize("throttle", granola_live_check.RATE_LIMITS)
def test_a_throttled_notes_probe_is_incomplete_never_ok(live, monkeypatch, capsys, throttle):
    monkeypatch.setattr(granola_live_check, "TRANSPORT", live_granola(throttle=throttle))
    assert granola_live_check.main([]) == 2
    out = capsys.readouterr().out
    assert "INCOMPLETE get_meetings: rate-limited twice" in out and "OK:" not in out and "DRIFT" not in out
    assert live == [5 if throttle == "rate_limit_http_429" else 20]       # one bounded wait, Retry-After when named


def test_a_throttled_public_api_is_incomplete_never_ok(live, monkeypatch, capsys):
    monkeypatch.delenv("GRANOLA_MCP_TOKEN")
    monkeypatch.setenv("GRANOLA_API_KEY", "fake-key-sensitive")
    monkeypatch.setattr(granola_live_check, "TRANSPORT", live_granola(public=httpx.Response(429, headers={"Retry-After": "600"})))
    assert granola_live_check.main([]) == 2
    out = capsys.readouterr().out
    assert "INCOMPLETE public API notes" in out and "OK:" not in out
    assert live == [60]


@pytest.mark.parametrize("notes", ["get_meetings", "get_meetings_event_stream"])
def test_a_new_recording_replays_compressed_through_the_real_client(api, live, monkeypatch, tmp_path, notes):
    monkeypatch.setattr(granola_live_check, "TRANSPORT", live_granola(notes))
    assert granola_live_check.main(["--record", str(tmp_path)]) == 0
    recording = json.loads((tmp_path / "mcp_shapes.json").read_text())["exchanges"]
    assert recording["get_meetings"]["headers"]["content-encoding"] == "gzip"            # httpx's lowercase names
    assert body_of(recording["get_meetings"])[:2] == b"\x1f\x8b"                       # replayed compressed
    assert "fake-access-sensitive" not in json.dumps(recording)
    provider = Recorded(api, exchanges={**EXCHANGES, **recording})
    provider.connect()
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert not status["last_error"] and status["imported_count"] == 2 and status["skipped"] == 0
    assert set(imported(api).values()) == {"Scrubbed summary"}
