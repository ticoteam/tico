"""Notes imports preserve progress when Granola throttles real MCP response shapes."""
import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from backend.granola_mcp import GranolaMCP
from backend.tests.test_granola_mcp import BASE, Provider, api, headers  # noqa: F401


PREFIXED_NOTES = '''Here are the shared notes for this meeting:
<meetings_data count="1"><meeting id="shared" title="Team sync">
<known_participants>Ana from Acme <ana@example.com></known_participants>
<summary>## Decisions
Budget < 15% & roadmap approved</summary></meeting></meetings_data>'''


class NotesProvider(Provider):
    def __init__(self, api, count=2):
        self.ids = [str(uuid.UUID(int=i + 1)) for i in range(count)]
        self.notes_calls, self.ranges = [], []
        self.responses = []
        self.account_result = None
        self.account_calls = 0
        self.range_enum = ["custom", "last_30_days"]
        super().__init__(api)
        self.now = datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp()

    def handle(self, request):
        if request.url.path == "/mcp":
            body = json.loads(request.content)
            if body["method"] == "tools/list":
                tools = [
                    {"name": "list_meetings", "inputSchema": {"properties": {
                        "time_range": {"enum": self.range_enum}, "custom_start": {}, "custom_end": {}},
                        "required": ["time_range"]}},
                    {"name": "get_meetings", "inputSchema": {"properties": {
                        "meeting_ids": {"type": "array", "minItems": 1, "maxItems": 10}},
                        "required": ["meeting_ids"], "additionalProperties": False}}]
                if self.account_result is not None:
                    tools.append({"name": "get_account_info", "inputSchema": {"properties": {}}})
                return httpx.Response(200, json={"result": {"tools": tools}})
            name = body.get("params", {}).get("name")
            if name == "list_meetings":
                self.ranges.append(body["params"]["arguments"])
                return httpx.Response(200, json={"result": {"structuredContent": {
                    "meetings": [{"id": nid} for nid in self.ids]}}})
            if name == "get_account_info":
                self.account_calls += 1
                return self.account_result
            if name == "get_meetings":
                args = body["params"]["arguments"]
                assert set(args) == {"meeting_ids"}
                assert 1 <= len(args["meeting_ids"]) <= 10
                self.notes_calls.append((self.now, args["meeting_ids"]))
                if self.responses:
                    response = self.responses.pop(0)
                    if response is not None:
                        return response
                return httpx.Response(200, json={"result": {"structuredContent": {
                    "meetings": [{"id": nid, "summary": "Shared notes"} for nid in args["meeting_ids"]]}}})
        return super().handle(request)


def rate_limit(kind="tool", message="Rate limit exceeded. Please slow down requests.", retry_after=None):
    payload = {"result": {"isError": True, "content": [{"type": "text", "text": message}]}}
    status = 200
    if kind == "rpc":
        payload = {"error": {"code": -32000, "message": message}}
    elif kind in ("http", "http_error"):
        status = 429 if kind == "http" else 503
        payload = {"error": message}
    return httpx.Response(status, json=payload, headers={"Retry-After": retry_after} if retry_after else {})


@pytest.mark.parametrize("raw,expected", [
    (PREFIXED_NOTES, {"meetings": [{"id": "shared", "title": "Team sync",
        "summary": "## Decisions\nBudget < 15% & roadmap approved",
        "attendees": [{"name": "Ana", "email": "ana@example.com"}]}], "next_cursor": None}),
    ('Intro\n<transcript>Ana: Ship it</transcript>', {"transcript": "Ana: Ship it"}),
])
def test_text_before_xml_is_ignored(raw, expected):
    assert GranolaMCP.content({"content": [{"type": "text", "text": raw}]}) == expected
    assert GranolaMCP.xml_content(raw) == expected


def test_rate_limits_wait_and_retry_the_same_batch(api):
    kind, message = "rpc", "Rate limit exceeded"
    provider = NotesProvider(api)
    provider.connect()
    provider.responses = [rate_limit(kind, message)]
    provider.sync()
    status = api.get(BASE, headers=headers()).json()
    assert status["imported_count"] == 2 and status["skipped"] == 0 and status["last_error"] is None
    assert len(provider.notes_calls) == 2
    assert all(ids == provider.ids for _, ids in provider.notes_calls)
    assert provider.notes_calls[1][0] - provider.notes_calls[0][0] == 15


def test_retry_after_is_honored(api):
    provider = NotesProvider(api)
    provider.connect()
    provider.responses = [rate_limit("tool", retry_after="42")]
    provider.sync()
    first, second = [t for t, _ in provider.notes_calls]
    assert second - first == 42


def test_four_rate_limits_stop_without_skipping_or_advancing_cursor(api, caplog):
    kind = "tool"
    provider = NotesProvider(api)
    provider.connect()
    saved = provider.service.load("human:ana")
    saved[1].update(cursor="2026-10-01T00:00:00+00:00", skipped=7, imported_count=4, last_sync="previous")
    provider.service.save(*saved)
    provider.responses = [rate_limit(kind, "Rate limit exceeded fake-provider-sensitive") for _ in range(4)]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["last_error"] == "rate_limited: get_meetings"
    assert meta["cursor"] == saved[1]["cursor"] and meta["skipped"] == 0
    assert meta["imported_count"] == 4 and meta["last_sync"] == "previous" and meta["state"] == "connected"
    assert len(provider.notes_calls) == 4 and all(ids == provider.ids for _, ids in provider.notes_calls)
    assert [b[0] - a[0] for a, b in zip(provider.notes_calls, provider.notes_calls[1:])] == [15, 30, 60]
    assert "fake-provider-sensitive" not in caplog.text and "fake-provider-sensitive" not in json.dumps(meta)
    provider.sync()
    assert provider.service.load("human:ana")[1]["last_error"] is None


def test_completed_batch_checkpoint_survives_a_rate_limit_and_resumes(api):
    provider = NotesProvider(api, 20)
    provider.connect()
    provider.responses = [None] + [rate_limit() for _ in range(4)]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["imported_count"] == 10 and meta["cursor"] and not meta.get("last_sync")
    assert meta["last_error"] == "rate_limited: get_meetings" and meta["skipped"] == 0
    assert all(ids == provider.ids[10:] for _, ids in provider.notes_calls[1:])
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["imported_count"] == 20 and meta["last_sync"] and meta["last_error"] is None

