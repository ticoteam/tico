"""Notes imports preserve progress when Granola throttles real MCP response shapes."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

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


def throttled(count=3, *args, **kwargs):
    return [rate_limit(*args, **kwargs) for _ in range(count)]


def test_a_persistent_rate_limit_stops_after_two_spaced_retries_without_skipping_or_advancing_cursor(api, caplog):
    kind = "tool"
    provider = NotesProvider(api)
    provider.connect()
    saved = provider.service.load("human:ana")
    saved[1].update(cursor="2026-10-01T00:00:00+00:00", skipped=7, imported_count=4, last_sync="previous")
    provider.service.save(*saved)
    provider.responses = throttled(3, kind, "Rate limit exceeded fake-provider-sensitive")
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["last_error"] == "rate_limited: get_meetings"
    assert meta["cursor"] == saved[1]["cursor"] and meta["skipped"] == 0
    assert meta["imported_count"] == 4 and meta["last_sync"] == "previous" and meta["state"] == "connected"
    assert [b[0] - a[0] for a, b in zip(provider.notes_calls, provider.notes_calls[1:])] == [20, 60]
    assert "fake-provider-sensitive" not in caplog.text and "fake-provider-sensitive" not in json.dumps(meta)
    provider.sync()
    assert provider.service.load("human:ana")[1]["last_error"] is None


def test_completed_batch_checkpoint_survives_a_rate_limit_and_resumes(api):
    provider = NotesProvider(api, 20)
    provider.connect()
    provider.responses = [None] + throttled()
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["imported_count"] == 10 and meta["cursor"] and not meta.get("last_sync")
    assert meta["last_error"] == "rate_limited: get_meetings" and meta["skipped"] == 0
    assert all(ids == provider.ids[10:] for _, ids in provider.notes_calls[1:])
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["imported_count"] == 20 and meta["last_sync"] and meta["last_error"] is None


def test_a_short_named_wait_is_waited_out_in_the_sync(api):
    provider = NotesProvider(api)
    provider.connect()
    provider.responses = [rate_limit("http", retry_after="20")]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["last_error"] is None and meta["imported_count"] == 2
    assert [b[0] - a[0] for a, b in zip(provider.notes_calls, provider.notes_calls[1:])] == [20]


def test_repeated_throttling_backs_off_and_caps(api):
    from backend import granola_mcp as G
    provider = NotesProvider(api)
    provider.connect()
    delays = []
    for _ in range(9):
        provider.responses = throttled()
        provider.sync()
        meta = provider.service.load("human:ana")[1]
        delays.append(round(meta["retry_after"] - provider.now))
    assert delays == [300, 600, 1200, 2400, 4800, 9600, 19200, G.RATE_LIMIT_MAX, G.RATE_LIMIT_MAX]
    provider.responses = [rate_limit(retry_after=str(G.RATE_LIMIT_MAX * 2))]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert round(meta["retry_after"] - provider.now) == G.RATE_LIMIT_MAX * 2, "a longer wait Granola names is kept"
    provider.responses = [rate_limit(retry_after=str(30 * 86400))]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert round(meta["retry_after"] - provider.now) == G.RETRY_AFTER_MAX, "an absurd named wait cannot park the import"
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["last_error"] is None and meta["failures"] == 0 and "retry_after" not in meta


def test_new_notes_are_fetched_before_imported_ones_are_revisited(api):
    provider = NotesProvider(api, 12)
    provider.connect()
    provider.sync()
    assert provider.service.load("human:ana")[1]["imported_count"] == 12
    new = [str(uuid.UUID(int=100 + i)) for i in range(3)]
    provider.ids = provider.ids + new           # later notes, listed after the overlap window's imported ones
    provider.notes_calls.clear()
    provider.responses = [None] + throttled()
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert set(new) <= set(provider.notes_calls[0][1]), "the first call spends the quota on notes not yet imported"
    assert meta["imported_count"] == 15 and meta["last_error"] == "rate_limited: get_meetings"


class DatedNotes(NotesProvider):
    def __init__(self, api, count):
        self.dates = {}
        super().__init__(api, count)

    def handle(self, request):
        if request.url.path == "/mcp" and json.loads(request.content).get("params", {}).get("name") == "list_meetings":
            return httpx.Response(200, json={"result": {"structuredContent": {
                "meetings": [{"id": nid, "created_at": self.dates[nid]} for nid in self.ids]}}})
        return super().handle(request)


def test_a_throttled_sync_never_checkpoints_past_an_imported_note_not_yet_revisited(api):
    provider = DatedNotes(api, 12)
    start = datetime.fromtimestamp(provider.now, timezone.utc)
    provider.dates.update({nid: (start - timedelta(hours=60 - 5 * i)).isoformat() for i, nid in enumerate(provider.ids)})
    provider.connect()
    provider.sync()
    provider.now += 7 * 86400                    # a week later: three new notes, then Granola throttles
    later = datetime.fromtimestamp(provider.now, timezone.utc)
    new = [str(uuid.UUID(int=100 + i)) for i in range(3)]
    provider.dates.update({nid: (later - timedelta(hours=3 - i)).isoformat() for i, nid in enumerate(new)})
    provider.ids = provider.ids + new
    provider.responses = [None] + throttled()
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["imported_count"] == 15, "the new notes came first"
    # Imported notes were left unrevisited: the next sync's window must still reach the oldest of them.
    assert datetime.fromisoformat(meta["cursor"]) - timedelta(hours=72) <= datetime.fromisoformat(provider.dates[provider.ids[7]])


class ThrottledList(NotesProvider):
    """The first list_meetings call is a 429 naming `wait` seconds."""
    def __init__(self, api, wait):
        self.wait, self.listed = wait, 0
        super().__init__(api)

    def handle(self, request):
        if request.url.path == "/mcp" and json.loads(request.content).get("params", {}).get("name") == "list_meetings":
            self.listed += 1
            if self.listed == 1:
                return httpx.Response(429, json={"error": "slow down"}, headers={"Retry-After": self.wait})
        return super().handle(request)


def test_a_long_named_wait_ends_the_sync_and_a_short_one_is_waited_out(api):
    long = ThrottledList(api, "3600")
    long.connect()
    long.sync()
    meta = long.service.load("human:ana")[1]
    assert long.listed == 1 and max(long.sleeps, default=0) < 60, "an hour is not slept inside the sync"
    assert meta["last_error"] == "rate_limited: list_meetings" and round(meta["retry_after"] - long.now) == 3600
    short = ThrottledList(api, "20")
    short.connect()
    short.sync()
    meta = short.service.load("human:ana")[1]
    assert short.listed == 2 and 20 in short.sleeps and meta["last_error"] is None


def test_a_wait_saved_before_the_cap_cannot_park_the_import_and_sync_now_retries(api):
    from backend import granola_mcp as G
    from backend.auth import Identity
    provider = NotesProvider(api)
    provider.connect()
    who = Identity("human:ana", "human", "ana@acme.example")
    row, meta, secret = provider.service.load("human:ana")
    # Saved by the old code: a 30-day wait from an attempt two days ago.
    meta.update(last_error="rate_limited: get_meetings", last_attempt=provider.now - 2 * 86400,
                retry_after=provider.now + 30 * 86400, failures=4)
    provider.service.save(row, meta, secret)
    status = provider.service.status(who)
    assert status["next_retry"] and status["last_attempt"] and status["failures"] == 4
    assert provider.service.next_retry(meta) == meta["last_attempt"] + G.RETRY_AFTER_MAX < provider.now
    result = api.portal.call(provider.service.trigger, who, G.SCHEDULE)
    assert result["state"] == "syncing", "the scheduled tick retries once the capped wait has passed"

    # Inside a fresh backoff, Sync now still makes a real attempt five minutes after the last one, not sooner.
    api.portal.call(lambda: asyncio.wait_for(asyncio.shield(provider.service.jobs.get("human:ana") or asyncio.sleep(0)), 5))
    row, meta, secret = provider.service.load("human:ana")
    meta.update(last_error="rate_limited: get_meetings", last_attempt=provider.now - 60, retry_after=provider.now + 3600)
    provider.service.save(row, meta, secret)
    assert api.portal.call(lambda: provider.service.trigger(who, now=True))["state"] == "recent"
    meta.update(last_attempt=provider.now - G.RATE_LIMIT_RETRY, last_finished=provider.now - G.RATE_LIMIT_RETRY)
    provider.service.save(row, meta, secret)
    # Opening Meetings asks for a sync too: it keeps to the backoff. Only the button (now) retries.
    assert api.portal.call(provider.service.trigger, who)["state"] == "recent"
    assert api.portal.call(lambda: provider.service.trigger(who, now=True))["state"] == "syncing"


def test_a_throttle_without_a_named_wait_is_retried_after_spaced_waits(api):
    provider = NotesProvider(api)
    provider.connect()
    provider.responses = [rate_limit("http")]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["last_error"] is None and meta["last_error_detail"] is None and meta["imported_count"] == 2
    assert [b[0] - a[0] for a, b in zip(provider.notes_calls, provider.notes_calls[1:])] == [20]


def test_three_throttles_fail_with_a_detail_naming_only_fixed_facts(api):
    provider = NotesProvider(api)
    provider.connect()
    provider.responses = throttled(3, "http")
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["last_error"] == "rate_limited: get_meetings" and len(provider.notes_calls) == 3
    assert meta["last_error_detail"] == {"step": "get_meetings", "http_status": 429, "rpc_code": None, "tool_error": False,
                                         "retry_after": None, "signal": None, "batch": 2}


def test_a_tool_error_throttle_names_its_signal_and_status_shows_no_provider_text(api):
    provider = NotesProvider(api)
    provider.connect()
    provider.responses = throttled(3, "tool", "Rate limit exceeded fake-provider-sensitive")
    provider.sync()
    status = api.get(BASE, headers=headers("ana-test")).json()
    assert status["last_error"] == "rate_limited: get_meetings"
    assert status["last_error_detail"] == {"step": "get_meetings", "http_status": 200, "rpc_code": None, "tool_error": True,
                                           "retry_after": None, "signal": "rate limit", "batch": 2}
    assert status["skip_reasons"] == {}
    assert "fake-provider-sensitive" not in json.dumps(status)
    assert "fake-provider-sensitive" not in json.dumps(provider.service.load("human:ana")[1])


def test_a_systemic_error_stops_the_per_id_fallback_and_counts_the_batch(api):
    provider = NotesProvider(api, 10)
    provider.connect()
    failure = lambda: httpx.Response(200, json={"error": {"code": -32603, "message": "fake-provider-sensitive"}})  # noqa: E731
    provider.responses = [failure(), failure(), failure()]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert [len(ids) for _, ids in provider.notes_calls] == [10, 1, 1], "two alike single failures end the fallback"
    assert meta["skipped"] == 10 and meta["skip_reasons"] == {"provider_error: get_meetings": 10}
    assert meta["last_error"] == "provider_error: get_meetings" and meta["imported_count"] == 0
    assert meta["last_error_detail"] == {"step": "get_meetings", "http_status": 200, "rpc_code": -32603, "tool_error": False,
                                         "retry_after": None, "signal": None, "batch": 1}
    assert "fake-provider-sensitive" not in json.dumps(meta)
    # When a single fetch succeeds, the error is not systemic and the fallback covers the rest of the batch.
    provider.responses = [failure(), failure(), None]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert meta["skipped"] == 1 and meta["skip_reasons"] == {"provider_error: get_meetings": 1}
    assert len(provider.notes_calls) == 3 + 11


def test_the_first_note_fetch_waits_ten_seconds_after_the_previous_mcp_call(api):
    provider = NotesProvider(api)
    provider.connect()
    seen = []

    def handle(request):
        body = json.loads(request.content) if request.url.path == "/mcp" else {}
        seen.append((provider.now, body.get("params", {}).get("name") or body.get("method")))
        return provider.handle(request)
    provider.service.transport = httpx.MockTransport(handle)
    provider.sync()
    names = [name for _, name in seen]
    first = names.index("get_meetings")
    assert names[first - 1] == "list_meetings"
    assert seen[first][0] - seen[first - 1][0] == 10
    assert provider.service.load("human:ana")[1]["imported_count"] == 2


def test_notes_left_untried_by_a_stopped_fallback_are_fetched_by_the_next_sync(api):
    provider = DatedNotes(api, 12)
    start = datetime.fromtimestamp(provider.now, timezone.utc)
    # Twelve notes over nine days: most of the first batch is older than the 72-hour revisit window.
    provider.dates.update({nid: (start - timedelta(hours=216 - 18 * i)).isoformat() for i, nid in enumerate(provider.ids)})
    provider.connect()
    failure = lambda: httpx.Response(200, json={"error": {"code": -32603, "message": "fake"}})  # noqa: E731
    provider.responses = [failure(), failure(), failure()]
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    assert [len(ids) for _, ids in provider.notes_calls] == [10, 1, 1, 2]
    assert meta["skipped"] == 10 and meta["skip_reasons"] == {"provider_error: get_meetings": 10}
    assert meta["imported_count"] == 2 and meta["last_sync"]
    untried = provider.ids[2:10]
    # The cursor stops before the first untried note instead of moving to the end of the sync.
    assert datetime.fromisoformat(meta["cursor"]) <= datetime.fromisoformat(provider.dates[untried[0]])
    provider.now += 3600
    provider.notes_calls.clear()
    provider.sync()
    meta = provider.service.load("human:ana")[1]
    fetched = {nid for _, ids in provider.notes_calls for nid in ids}
    assert set(untried) <= fetched and meta["imported_count"] == 12
    old = [nid for nid in untried if datetime.fromisoformat(provider.dates[nid]) < datetime.fromtimestamp(provider.now, timezone.utc) - timedelta(hours=72)]
    assert old, "notes older than the revisit window are among those imported"
    assert meta["skipped"] == 0 and meta["last_error"] is None
