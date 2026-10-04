"""Live meetings use disposable SQLite fixtures and synthetic text only."""

import asyncio
import time

import pytest
from starlette.requests import Request

from backend import live_meetings as live_meetings_module
from backend.auth import Identity
from backend.store import H, digest, encode
from backend.tests.test_api import api, get, headers, post, restrict  # noqa: F401


@pytest.fixture
def live(api):
    settings = api.app.state.store.settings
    settings.test_identities.update({
        "ops-live": Identity("bot:ops", "bot", agent="hermes"),
        "finance-live": Identity("bot:finance", "bot", agent="hermes"),
    })
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?)",
                  ("ops", "hermes", digest("synthetic-ops-agent"), H.now(), "human:ana"))
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created,created_by) VALUES(?,?,?,?,?)",
                  ("finance", "hermes", digest("synthetic-finance-agent"), H.now(), "human:ana"))
    return api


def connect(api, *, title="Replay", client_id=None, fallback=30):
    body = {"title": title, "fallback_window_seconds": fallback}
    if client_id:
        body["client_id"] = client_id
    return post(api, "live-meetings", body)


def attach(api, mid, *bots, token="ana-test"):
    return post(api, f"live-meetings/{mid}/bots", {"bots": list(bots)}, token=token)


def chunk(api, mid, seq, start, end, text="Transcript", *, token="ana-test", expected=200):
    response = api.post("/api/v2/live-meetings/" + mid + "/chunks", json={"chunks": [
        {"seq": seq, "speaker": "Ana", "start_ms": start, "end_ms": end, "text": text}]} ,
        headers=headers(token))
    assert response.status_code == expected, response.text
    return response.json() if expected == 200 else response


def test_connect_is_explicit_team_visible_and_second_human_must_join_to_chat(live):
    meeting = connect(live, client_id="recorder-session-1")
    same = connect(live, client_id="recorder-session-1")
    assert same["id"] == meeting["id"]
    assert same["state"] == "live"
    assert get(live, "live-meetings/" + meeting["id"], token="ben-test")["id"] == meeting["id"]
    denied = live.post("/api/v2/live-meetings/" + meeting["id"] + "/chat", json={"text": "Hi"},
                       headers=headers("ben-test"))
    assert denied.status_code == 403
    post(live, f"live-meetings/{meeting['id']}/join", {}, token="ben-test")
    sent = post(live, f"live-meetings/{meeting['id']}/chat", {"text": "Hi", "transcript_seq": None},
                token="ben-test")
    assert sent["message"]["actor"] == "human:ben"


def test_chunk_sequence_idempotency_conflict_gap_and_lifecycle(live):
    meeting = connect(live)
    mid = meeting["id"]
    first = chunk(live, mid, 1, 0, 1000, "Hello")
    event_id = first["event_id"]
    duplicate = chunk(live, mid, 1, 0, 1000, "Hello")
    assert duplicate["accepted"] == [1]
    assert duplicate["event_id"] == event_id
    chunk(live, mid, 1, 0, 1000, "Changed", expected=409)
    chunk(live, mid, 3, 2000, 3000, expected=409)
    paused = post(live, f"live-meetings/{mid}/control", {"action": "pause"})
    assert paused["state"] == "paused"
    chunk(live, mid, 2, 1000, 2000, expected=409)
    resumed = post(live, f"live-meetings/{mid}/control", {"action": "resume"})
    assert resumed["state"] == "live"
    ended = post(live, f"live-meetings/{mid}/control", {"action": "end"})
    assert ended["state"] == "ended"
    assert mid not in {row["id"] for row in get(live, "live-meetings")["meetings"]}
    chunk(live, mid, 2, 1000, 2000, expected=409)


def test_meeting_only_bot_read_and_parallel_turns_with_transcript_link(live):
    meeting = connect(live)
    mid = meeting["id"]
    denied = get(live, f"live-meetings/{mid}", token="finance-live", expected=403)
    assert denied["error"]["code"] == "forbidden"
    attach(live, mid, "ops", "finance")
    assert get(live, f"live-meetings/{mid}", token="ops-live")["id"] == mid
    bot_view = get(live, f"live-meetings/{mid}", token="ops-live")
    assert all(not member["email"] for member in bot_view["humans"])
    assert get(live, f"live-meetings/{mid}/turns", token="finance-live")["turns"] == []
    chunk(live, mid, 1, 0, 1000, "Ops and Finance, please review this")
    ops_turn = get(live, f"live-meetings/{mid}/turns", token="ops-live")["turns"][0]
    finance_turn = get(live, f"live-meetings/{mid}/turns", token="finance-live")["turns"][0]
    assert ops_turn["id"] != finance_turn["id"]
    assert {turn["id"] for turn in get(live, f"live-meetings/{mid}")["router"]["turns"]} == {
        ops_turn["id"], finance_turn["id"]}
    pending_reply = live.post("/api/v2/live-meetings/" + mid + "/chat",
                              json={"text": "Too early", "turn_id": ops_turn["id"]},
                              headers=headers("ops-live"))
    assert pending_reply.status_code == 409
    turns = (("ops-live", ops_turn), ("finance-live", finance_turn))
    for token, turn in turns:
        post(live, f"live-meetings/{mid}/turns/{turn['id']}/claim", {}, token=token)
    replies = [post(live, f"live-meetings/{mid}/chat", {
        "text": "Reviewed", "turn_id": turn["id"], "transcript_seq": 1,
    }, token=token) for token, turn in turns]
    assert all(reply["message"]["transcript_seq"] == 1 for reply in replies)
    assert all(reply["message"]["at_ms"] == 1000 for reply in replies)
    assert {reply["message"]["role"] for reply in replies} == {"bot"}
    with live.app.state.store.read() as c:
        meeting_bots = c.execute("SELECT bot FROM live_meeting_bots WHERE meeting_id=?", (mid,)).fetchall()
        assert {row[0] for row in meeting_bots} == {"ops", "finance"}


def test_router_windows_cap_cooldown_and_hidden_pass_trace(live):
    calls = []

    def choose(state, questions, label):
        calls.append((state["window_index"], label))
        return {"model": "synthetic-decider", "ms": 1,
                "answers": {key: {"noul": 0.95, "confidence": 0.95} for key in questions}}

    live.app.state.live_meeting_decider = choose
    meeting = connect(live, fallback=30)
    assert meeting["window_ms"] == 30_000
    mid = meeting["id"]
    attach(live, mid, "ops", "finance", "inbox", "doc-updater")
    chunk(live, mid, 1, 0, 30_000, "A full first window", token="ana-test")
    detail = get(live, f"live-meetings/{mid}")
    assert len(calls) == 1
    trace = detail["router"]["windows"][0]
    assert trace["outcome"] == "route"
    assert len(trace["trace"]["selected"]) == 3
    assert {row["reason"] for row in trace["trace"]["skipped"]} == {"cap"}
    assert len(detail["router"]["turns"]) == 3
    assert len(get(live, f"live-meetings/{mid}/turns", token="finance-live")["turns"]) == 1
    # No reply turns enter visible chat until the selected bot claims and answers.
    assert detail["chat"] == []

    pass_app = live
    pass_app.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.1, "confidence": 0.9} for key in questions}}
    passed = connect(live, title="PASS", fallback=45)
    assert passed["window_ms"] == 45_000  # without Jev, use the configured 30–45 second fallback
    attach(live, passed["id"], "ops")
    chunk(live, passed["id"], 1, 0, 45_000, "Nothing needs a reply")
    result = get(live, f"live-meetings/{passed['id']}")
    assert result["window_ms"] == 45_000
    assert result["router"]["windows"][0]["outcome"] == "pass"
    assert result["router"]["windows"][0]["trace"]["answers"]["ops"] == 0.1
    assert result["chat"] == []


def test_named_chat_bypasses_decision_and_repeat_finalize_keeps_external_id(live):
    calls = []
    live.app.state.live_meeting_decider = lambda *args: calls.append(args) or {}
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    chunk(live, mid, 1, 0, 500, "Ops, please take a look")
    detail = get(live, f"live-meetings/{mid}")
    assert detail["router"]["turns"][0]["bot"] == "ops"
    assert detail["router"]["windows"] == []
    assert detail["router"]["bypasses"][0]["source_key"] == "chunk:1"
    assert calls == []

    chat_meeting = connect(live, title="Named chat")
    attach(live, chat_meeting["id"], "ops")
    post(live, f"live-meetings/{chat_meeting['id']}/chat", {"text": "Ops, can you review this?"})
    chat_detail = get(live, f"live-meetings/{chat_meeting['id']}")
    assert chat_detail["router"]["turns"][0]["source_key"].startswith("chat:")
    assert chat_detail["router"]["bypasses"][0]["source_key"].startswith("chat:")
    assert chat_detail["router"]["windows"] == []
    assert calls == []
    post(live, f"live-meetings/{mid}/control", {"action": "end"})
    one = post(live, f"live-meetings/{mid}/finalize", {})
    two = post(live, f"live-meetings/{mid}/finalize", {})
    assert one["meeting_id"] == two["meeting_id"]
    assert one["event_id"] == two["event_id"]
    with live.app.state.store.read() as c:
        refs = c.execute("SELECT count(*) FROM recording_source_refs WHERE source='tico-live' AND external_id=?",
                         ("human:ana:" + mid,)).fetchone()[0]
        finalized_events = c.execute("SELECT count(*) FROM live_meeting_events WHERE meeting_id=? AND type='meeting.finalized'",
                                     (mid,)).fetchone()[0]
    assert refs == 1
    assert finalized_events == 1


def test_rehearsal_mode_keeps_live_router_a_hidden_pass(live):
    calls = []
    live.app.state.store.settings.rehearsal = True
    live.app.state.live_meeting_decider = lambda *args: calls.append(args)
    meeting = connect(live, title="Rehearsal", fallback=30)
    attach(live, meeting["id"], "ops")
    chunk(live, meeting["id"], 1, 0, 30_000, "Synthetic text only")
    detail = get(live, f"live-meetings/{meeting['id']}")
    assert detail["router"]["windows"][0]["outcome"] == "pass"
    assert detail["router"]["windows"][0]["trace"]["reason"] == "rehearsal_mode"
    assert detail["chat"] == []
    assert calls == []


def test_router_busy_skip_then_sixty_second_reply_cooldown(live):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.9} for key in questions}}
    meeting = connect(live, fallback=30)
    mid = meeting["id"]
    attach(live, mid, "ops")
    chunk(live, mid, 1, 0, 30_000, "First window")
    first = get(live, f"live-meetings/{mid}")
    turn_id = first["router"]["turns"][0]["id"]
    # Catching up while the first turn is pending records a busy skip.
    chunk(live, mid, 2, 30_000, 60_000, "Busy window")
    busy = get(live, f"live-meetings/{mid}")["router"]["windows"][1]
    assert busy["trace"]["skipped"] == [{"bot": "ops", "reason": "busy"}]
    post(live, f"live-meetings/{mid}/turns/{turn_id}/claim", {}, token="ops-live")
    post(live, f"live-meetings/{mid}/chat", {"text": "Done", "turn_id": turn_id, "at_ms": 60_000},
         token="ops-live")
    post(live, f"live-meetings/{mid}/chat", {"text": "Ops, another thought", "at_ms": 120_000})
    assert len(get(live, f"live-meetings/{mid}")["router"]["turns"]) == 1
    # One half-minute later cooldown suppresses a route; at exactly one minute it expires.
    chunk(live, mid, 3, 60_000, 90_000, "Cooldown window")
    detail = get(live, f"live-meetings/{mid}")
    assert detail["router"]["windows"][2]["trace"]["skipped"] == [{"bot": "ops", "reason": "cooldown"}]
    chunk(live, mid, 4, 90_000, 120_000, "Cooldown expired")
    detail = get(live, f"live-meetings/{mid}")
    assert detail["router"]["windows"][3]["outcome"] == "route"
    assert len([turn for turn in detail["router"]["turns"] if turn["status"] == "pending"]) == 1


def test_chunk_correction_versions_are_idempotent_and_final_transcript_uses_latest(live):
    meeting = connect(live)
    mid = meeting["id"]
    chunk(live, mid, 1, 0, 1000, "Initial text")
    revision = {"revision": 2, "speaker": "Ana", "start_ms": 0, "end_ms": 1000, "text": "Corrected text"}
    first = post(live, f"live-meetings/{mid}/chunks/1/corrections", revision)
    again = post(live, f"live-meetings/{mid}/chunks/1/corrections", revision)
    assert first["revision"] == again["revision"] == 2
    assert again["replayed"] is True
    conflict = live.post("/api/v2/live-meetings/" + mid + "/chunks/1/corrections",
                         json={**revision, "text": "Different correction"}, headers=headers())
    assert conflict.status_code == 409
    gap = live.post("/api/v2/live-meetings/" + mid + "/chunks/1/corrections",
                    json={**revision, "revision": 4}, headers=headers())
    assert gap.status_code == 409
    # A delayed retry of the original chunk acknowledges its stored version without undoing correction 2.
    assert chunk(live, mid, 1, 0, 1000, "Initial text")["accepted"] == [1]
    post(live, f"live-meetings/{mid}/control", {"action": "end"})
    finalized = post(live, f"live-meetings/{mid}/finalize", {})
    with live.app.state.store.read() as c:
        row = c.execute("SELECT transcript_original FROM meetings WHERE id=?", (finalized["meeting_id"],)).fetchone()
        versions = c.execute("SELECT revision FROM live_meeting_chunk_versions WHERE meeting_id=? AND seq=1 ORDER BY revision",
                             (mid,)).fetchall()
    assert "Corrected text" in row["transcript_original"]
    assert [version[0] for version in versions] == [1, 2]


def test_orgchart_attachment_requires_visible_active_bot_and_control_is_connector_only(live):
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    denied = live.post("/api/v2/live-meetings/" + mid + "/control", json={"action": "pause"},
                       headers=headers("ben-test"))
    assert denied.status_code == 403
    hidden = live.post("/api/v2/live-meetings/" + mid + "/bots", json={"bots": ["unknown"]},
                       headers=headers("ana-test"))
    assert hidden.status_code == 404


def test_joined_human_can_attach_visible_bots_but_nonmember_cannot(live):
    meeting = connect(live, title="Participant attachment")
    mid = meeting["id"]
    denied = live.post("/api/v2/live-meetings/" + mid + "/bots", json={"bots": ["ops"]},
                       headers=headers("ben-test"))
    assert denied.status_code == 403
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test")
    attached = attach(live, mid, "ops", token="ben-test")
    assert attached["added"] == ["ops"]
    detail = get(live, f"live-meetings/{mid}")
    assert detail["bots"][0]["joined_by"] == "human:ben"
    control = live.post("/api/v2/live-meetings/" + mid + "/control", json={"action": "pause"},
                        headers=headers("ben-test"))
    assert control.status_code == 403
    with live.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    private_meeting = connect(live, title="Visible roster")
    post(live, f"live-meetings/{private_meeting['id']}/join", {}, token="cara-test")
    candidates = get(live, "live-meetings/bot-candidates", token="cara-test")
    assert "ops" not in {bot["slug"] for bot in candidates["bots"]}
    hidden_bot = live.post("/api/v2/live-meetings/" + private_meeting["id"] + "/bots",
                           json={"bots": ["ops"]}, headers=headers("cara-test"))
    assert hidden_bot.status_code == 403


def test_pause_keeps_chat_but_suppresses_named_bot_routing(live):
    meeting = connect(live, title="Paused routing")
    mid = meeting["id"]
    attach(live, mid, "ops")
    post(live, f"live-meetings/{mid}/control", {"action": "disconnect"})
    post(live, f"live-meetings/{mid}/chat", {"text": "Ops, please respond"})
    detail = get(live, f"live-meetings/{mid}")
    assert detail["state"] == "paused"
    assert len(detail["chat"]) == 1
    assert detail["router"]["turns"] == []


def test_jev_15_second_windows_replay_thirty_minutes_at_four_x_without_waiting(live):
    calls = []

    def choose(state, questions, label):
        calls.append(state["window_index"])
        return {"model": "synthetic-decider", "ms": 1,
                "answers": {key: {"noul": 0.1, "confidence": 0.9} for key in questions}}

    live.app.state.judge = lambda *args: None  # configured Jev path selects 15-second windows
    live.app.state.live_meeting_decider = choose
    meeting = connect(live, title="30 minute 4x replay", fallback=45)
    mid = meeting["id"]
    assert meeting["window_ms"] == 15_000
    attach(live, mid, "ops")
    chunks = [{"seq": seq, "speaker": "Synthetic", "start_ms": (seq - 1) * 15_000,
               "end_ms": seq * 15_000, "text": f"Synthetic transcript segment {seq}"}
              for seq in range(1, 121)]
    started = time.monotonic()
    response = live.post("/api/v2/live-meetings/" + mid + "/chunks", json={"chunks": chunks},
                         headers=headers("ana-test", key="synthetic-30m-replay"))
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert elapsed < 240, f"synthetic 30-minute replay exceeded focused-test budget at {elapsed:.1f}s"
    detail = get(live, f"live-meetings/{mid}")
    assert len(detail["chunks"]) == 120
    assert len(detail["router"]["windows"]) == 120
    assert calls == list(range(120))
    assert {window["outcome"] for window in detail["router"]["windows"]} == {"pass"}
    assert detail["router"]["turns"] == []
    replay = live.post("/api/v2/live-meetings/" + mid + "/chunks", json={"chunks": chunks},
                       headers=headers("ana-test", key="synthetic-30m-replay-retry"))
    assert replay.status_code == 200, replay.text
    repeated = get(live, f"live-meetings/{mid}")
    assert repeated["event_id"] == detail["event_id"]
    assert len(repeated["router"]["windows"]) == 120
    assert calls == list(range(120))


def test_sse_replays_persisted_events_after_last_event_id(live, monkeypatch):
    meeting = connect(live, title="SSE replay")
    mid = meeting["id"]
    chunk(live, mid, 1, 0, 1000, "Persisted event")
    event_route = next(route for route in live.app.routes
                       if getattr(route, "path", "") == "/api/v2/live-meetings/{rid}/events")
    path = f"/api/v2/live-meetings/{mid}/events"
    request = Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"authorization", b"Bearer ana-test"),
                    (b"last-event-id", str(meeting["event_id"]).encode())],
        "client": ("testclient", 80), "server": ("testserver", 80),
    })
    request.state.identity = Identity("human:ana", "owner", "ana@acme.example")
    disconnected = 0

    async def is_disconnected(_request):
        nonlocal disconnected
        disconnected += 1
        return disconnected > 1

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(Request, "is_disconnected", is_disconnected)
    monkeypatch.setattr(live_meetings_module.asyncio, "sleep", no_sleep)

    async def read_replay():
        response = await event_route.endpoint(request, mid)
        iterator = response.body_iterator
        first = await iterator.__anext__()
        keepalive = await iterator.__anext__()
        try:
            await iterator.__anext__()
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("SSE stream did not stop after the synthetic disconnect")
        return first, keepalive

    first, keepalive = asyncio.run(read_replay())
    assert first.startswith("id: 2\nevent: meeting.chunk\n")
    assert '"text":"Persisted event"' in first
    assert keepalive.startswith(": keep-alive")
