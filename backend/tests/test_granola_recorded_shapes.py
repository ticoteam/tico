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

from backend.granola_mcp import GranolaMCP
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


S = "zq%02d"   # unique made-up markers: none may reach a recording


def meetings_answer(text=None, structured=None, blocks=1):
    result = {"structuredContent": structured} if structured is not None else {
        "content": [{"type": "text", "text": text}] + [{"type": "text", "text": S % 90 + " extra"}] * (blocks - 1)}
    return {"status": 200, "headers": {"content-type": "application/json; charset=" + S % 91, "x-trace": S % 92},
            "json": {"jsonrpc": "2.0", "id": S % 93, "result": result}}


XML_FORMS = {
    "title element, aliases, attendee forms, private notes, prose around": (
        f'{S % 1} intro\n<meetings_data count="2"><meeting id="{S % 2}" date="{S % 3}"><title>{S % 4}</title>'
        f'<attendees><attendee name="{S % 5}" email="{S % 6}@x.example"/><attendee><name>{S % 7}</name>'
        f'<email>{S % 8}@x.example</email></attendee><attendee>{S % 9}</attendee></attendees>'
        f'<enhanced_notes>{S % 10} < 15% & {S % 11}@x.example</enhanced_notes><private_notes>{S % 12}</private_notes>'
        f'<raw_notes>{S % 13}</raw_notes><note_taker_notes>{S % 14}</note_taker_notes><{S % 15}>{S % 16}</{S % 15}>'
        f"</meeting><meeting id='{S % 17}' title='{S % 18}'><ai_summary>{S % 19}</ai_summary></meeting>"
        f'<next_cursor>{S % 20}</next_cursor></meetings_data>\n{S % 21} trailer', 3, 2),
    "single-quoted titles, known_participants, each summary alias": (
        "<meetings>" + "".join(f"<meeting id='{S % (30 + n)}' title='{S % (40 + n)}'><known_participants>\n"
                               f"{S % (50 + n)} (note creator) from {S % (60 + n)} <{S % (70 + n)}@x.example>\n"
                               f"</known_participants><{tag}>{S % (80 + n)}</{tag}></meeting>"
                               for n, tag in enumerate(granola_live_check.SUMMARIES)) + "</meetings>", 1, 5),
    "referral example": (f'<meetings><meeting id="00000000-0000-4000-8000-000000000001"><title>{S % 22}</title>'
                         f'<enhanced_notes>{S % 23}</enhanced_notes><attendees><attendee name="{S % 24}" /></attendees>'
                         "</meeting></meetings>", 1, 1),
}


@pytest.mark.parametrize("raw,people,count", XML_FORMS.values(), ids=list(XML_FORMS))
def test_a_recorded_xml_answer_keeps_its_shape_and_nothing_granola_wrote(raw, people, count):
    original = GranolaMCP.content(meetings_answer(raw)["json"]["result"])["meetings"]
    made = granola_live_check.scrub({"get_meetings": meetings_answer(raw, blocks=2)})
    assert "zq" not in json.dumps(made)
    rows = GranolaMCP.content(made["get_meetings"]["json"]["result"])["meetings"]
    assert len(rows) == len(original) == count
    assert [bool(GranolaMCP.item(r)) for r in rows] == [bool(GranolaMCP.item(r)) for r in original]
    assert len(rows[0]["attendees"]) == people
    assert made["get_meetings"]["headers"] == {"content-type": "application/json"}


@pytest.mark.parametrize("as_text", [False, True])
def test_a_recorded_json_answer_keeps_its_shape_and_nothing_granola_wrote(as_text):
    rows = [{"note_id": S % 1, "title": S % 2, "summary": {"markdown": S % 3}, "participants": [S % 4, {"name": S % 5}],
             "web_url": "https://x.example/" + S % 6, S % 7: S % 8, "nested": json.dumps({"title": S % 9})},
            {"meeting_id": S % 10, "ai_summary": 'quote " ' + S % 11, "attendees": [{"name": S % 12, "email": S % 13}]}]
    body = {"meetings": rows, "next_cursor": S % 14}
    answer = meetings_answer(json.dumps(body)) if as_text else meetings_answer(structured=body)
    exchange = {**answer, "events": [answer["json"], {"jsonrpc": "2.0", "method": S % 15, "params": {"x": S % 16}}]}
    del exchange["json"]
    made = granola_live_check.scrub({"get_meetings": exchange})
    assert "zq" not in json.dumps(made)
    first = made["get_meetings"]["events"][0]["result"]
    assert ("content" in first) is as_text and ("structuredContent" in first) is not as_text
    parsed = GranolaMCP.content(first)
    assert parsed["next_cursor"] == "cursor-placeholder" and len(parsed["meetings"]) == 2
    assert all(GranolaMCP.item(r).notes for r in parsed["meetings"])


def test_tool_descriptions_unknown_fields_and_errors_are_made_up_but_still_classified():
    tools = {"status": 200, "headers": {"mcp-session-id": S % 1, "retry-after": S % 2}, "json": {"jsonrpc": "2.0", "id": 1,
             "result": {"tools": [{"name": "get_meetings", "description": S % 3, "inputSchema": {"type": "object",
              "properties": {"meeting_ids": {"type": "array", "maxItems": 10, "description": S % 4},
                             S % 5: {"type": "string", "enum": ["last_30_days", S % 6]}}, "required": ["meeting_ids", S % 5]}},
              {"name": S % 7, "description": S % 8}]}}}
    error = {"status": 200, "headers": {}, "json": {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000,
             "message": f"Rate limit exceeded for {S % 9}", "data": S % 10}}}
    tool_error = {"status": 200, "headers": {}, "json": {"jsonrpc": "2.0", "id": 3, "result": {"isError": True,
                  "content": [{"type": "text", "text": f"{S % 11} slow down"}]}}}
    made = granola_live_check.scrub({"tools_list": tools, "rate_limit_rpc_error": error, "rate_limit_tool_error": tool_error})
    assert "zq" not in json.dumps(made)
    schema = made["tools_list"]["json"]["result"]["tools"][0]["inputSchema"]
    assert schema["properties"]["meeting_ids"] == {"type": "array", "maxItems": 10}
    assert schema["required"] == ["meeting_ids", "param_2"]
    assert granola_live_check.check_fixture_exchanges({k: v for k, v in made.items() if k != "tools_list"}) == []


@pytest.mark.parametrize("answer", [
    meetings_answer(S % 1 + " no meetings here"),
    {"status": 200, "headers": {}, "text": S % 2},
    {"status": 200, "headers": {}, "json": {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x",
     "inputSchema": json.loads('{"items":' * 12 + "{}" + "}" * 12)}]}}},
], ids=["unparseable notes", "not JSON", "schema too deep"])
def test_a_shape_it_cannot_rebuild_writes_nothing_and_names_only_the_category(tmp_path, monkeypatch, capsys, answer):
    name = "tools_list" if "tools" in json.dumps(answer) else "get_meetings"
    (tmp_path / "mcp_shapes.json").write_text("kept")
    monkeypatch.setattr(granola_live_check, "live_mcp", lambda token: ({name: answer}, []))
    monkeypatch.setenv("GRANOLA_MCP_TOKEN", "fake-access-sensitive")
    monkeypatch.delenv("GRANOLA_API_KEY", raising=False)
    assert granola_live_check.main(["--record", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "CANNOT RECORD" in out and "zq" not in out
    assert [p.name for p in tmp_path.iterdir()] == ["mcp_shapes.json"] and (tmp_path / "mcp_shapes.json").read_text() == "kept"


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
    assert set(imported(api).values()) == {granola_live_check.NOTE % 1, granola_live_check.NOTE % 2}
