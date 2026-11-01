"""Live meetings use disposable SQLite fixtures and synthetic text only."""

import asyncio
import json
import time

import pytest
from starlette.requests import Request

from backend import live_meetings as live_meetings_module
from backend import gateway_connection, providers, task_models
from backend.auth import Identity
from backend.store import H, digest, encode
from backend.tests.test_api import api, assign, get, headers, post, restrict, runner  # noqa: F401


@pytest.fixture
def live(api):
    settings = api.app.state.store.settings
    settings.test_identities.update({
        "ops-live": Identity("bot:ops", "bot", agent="hermes"),
        "finance-live": Identity("bot:finance", "bot", agent="hermes"),
    })
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  ("ops", "hermes", digest("synthetic-ops-agent"), H.now(), "human:ana"))
        c.execute("INSERT OR REPLACE INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  ("finance", "hermes", digest("synthetic-finance-agent"), H.now(), "human:ana"))
    yield api
    assert settle(api)


def ready(api, r, bots, capabilities=("private_lanes_v1",)):
    """A current runner: it advertises private lanes, so it takes a private meeting's turns (execution.PRIVATE_LANES)."""
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": list(capabilities),
                "readiness": {bot: True for bot in bots}}, token=r["token"])


def settle(api):
    """Wait for the meeting routing a write started off its request (live_meetings.Routing)."""
    return api.app.state.live_meeting_routing.settle(timeout=60)


def connect(api, *, title="Replay", client_id=None, fallback=30, reply_cap=3, cooldown_seconds=60):
    body = {"title": title, "fallback_window_seconds": fallback,
            "reply_cap": reply_cap, "cooldown_seconds": cooldown_seconds}
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
    settle(api)
    return response.json() if expected == 200 else response


def test_connect_is_explicit_team_visible_and_second_human_must_join_to_chat(live):
    meeting = connect(live, client_id="recorder-session-1")
    candidates = get(live, "live-meetings/bot-candidates")
    assert set(candidates) == {"bots"}
    assert all(set(bot) == {"id", "slug", "name", "description", "team"} for bot in candidates["bots"])
    assert any(bot["slug"] == "ops" for bot in candidates["bots"])
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
    assert set(first) == {"id", "state", "seq", "accepted", "event_id"}
    assert first["id"] == mid and first["state"] == "live" and first["seq"] == 1
    event_id = first["event_id"]
    duplicate = chunk(live, mid, 1, 0, 1000, "Hello")
    assert set(duplicate) == set(first)
    assert duplicate["accepted"] == [1]
    assert duplicate["event_id"] == event_id
    conflict = chunk(live, mid, 1, 0, 1000, "Changed", expected=409)
    assert conflict.json()["error"]["code"] == "sequence_conflict"
    gap = chunk(live, mid, 3, 2000, 3000, expected=409)
    assert gap.json()["error"]["code"] == "sequence_gap"
    assert gap.json()["error"]["expected_seq"] == 2
    paused = post(live, f"live-meetings/{mid}/control", {"action": "pause"})
    assert paused["state"] == "paused"
    paused_chunk = chunk(live, mid, 2, 1000, 2000, expected=409)
    assert paused_chunk.json()["error"]["code"] == "meeting_state"
    resumed = post(live, f"live-meetings/{mid}/control", {"action": "resume"})
    assert resumed["state"] == "live"
    ended = post(live, f"live-meetings/{mid}/control", {"action": "end"})
    assert ended["state"] == "ended"
    assert mid not in {row["id"] for row in get(live, "live-meetings")["meetings"]}
    chunk(live, mid, 2, 1000, 2000, expected=409)
    ended_retry = chunk(live, mid, 1, 0, 1000, "Hello")
    assert ended_retry["state"] == "ended" and ended_retry["accepted"] == [1]


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
        direct_jobs = c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
                                "WHERE json_extract(m.refs,'$.live_meeting.meeting_id')=?",
                                (mid,)).fetchone()[0]
        assert direct_jobs == 2
        direct_job_states = c.execute("SELECT DISTINCT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                                      "WHERE json_extract(m.refs,'$.live_meeting.meeting_id')=?",
                                      (mid,)).fetchall()
        assert {row[0] for row in direct_job_states} == {"canceled"}


def test_live_turns_use_concurrent_runner_jobs_and_hidden_pass(live):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops", "finance")
    chunk(live, mid, 1, 0, 30_000, "The rollout needs a reliability review")
    routed = get(live, f"live-meetings/{mid}")
    turns = {turn["bot"]: turn for turn in routed["router"]["turns"]}
    assert set(turns) == {"ops", "finance"}

    machine = runner(live)
    for slug in ("ops", "finance"):
        assign(live, machine, slug)
    ready(live, machine, ["ops", "finance"])
    attempts = {}
    for slug in ("ops", "finance"):
        attempt = post(live, "jobs/claim", {"bot": slug}, token=machine["token"])
        assert attempt["attempt"]["bot"] == slug
        attempts[slug] = attempt["attempt"]
        assert "The rollout needs a reliability review" in attempt["attempt"]["message"]["body"]
        assert turns[slug]["id"] in attempt["attempt"]["message"]["body"]
    assert len({attempt["id"] for attempt in attempts.values()}) == 2
    with live.app.state.store.read() as c:
        assert {row["state"] for row in c.execute("SELECT state FROM attempts WHERE id IN (?,?)",
                                                  tuple(attempt["id"] for attempt in attempts.values()))} == {"leased"}
    for attempt in attempts.values():
        post(live, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-live-turn"},
             token=machine["token"])

    post(live, f"attempts/{attempts['ops']['id']}/complete",
         {"outcome": "completed", "text": "I will check the reliability risks.", "last_seq": 0},
         token=machine["token"])
    post(live, f"attempts/{attempts['finance']['id']}/complete",
         {"outcome": "completed", "text": "PASS", "last_seq": 0}, token=machine["token"])
    final = get(live, f"live-meetings/{mid}")
    assert [message["text"] for message in final["chat"]] == ["I will check the reliability risks."]
    states = {turn["bot"]: (turn["status"], turn["skip_reason"]) for turn in final["router"]["turns"]}
    assert states["ops"] == ("replied", "")
    assert states["finance"] == ("skipped", "bot_pass")
    traces = [event for event in final["router"]["bypasses"]]
    assert traces == []
    with live.app.state.store.read() as c:
        pass_events = [json.loads(row[0]) for row in c.execute(
            "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.router'", (mid,))]
        assert any(item.get("turn_id") == turns["finance"]["id"] and item["outcome"] == "pass"
                   and item["trace"]["reason"] == "bot_pass" for item in pass_events)
        assert c.execute("SELECT count(*) FROM live_meeting_chat WHERE meeting_id=? AND text='PASS'",
                         (mid,)).fetchone()[0] == 0


def test_per_meeting_reply_cap_cooldown_and_validation(live):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live, reply_cap=1, cooldown_seconds=5)
    mid = meeting["id"]
    assert meeting["reply_cap"] == 1 and meeting["cooldown_ms"] == 5000
    attach(live, mid, "ops", "finance")
    chunk(live, mid, 1, 0, 30_000, "A window for the team")
    detail = get(live, f"live-meetings/{mid}")
    assert len([turn for turn in detail["router"]["turns"] if turn["status"] == "pending"]) == 1
    assert detail["router"]["windows"][0]["trace"]["skipped"] == [{"bot": "ops", "reason": "cap"}]
    first = detail["router"]["turns"][0]
    post(live, f"live-meetings/{mid}/turns/{first['id']}/claim", {}, token="finance-live")
    post(live, f"live-meetings/{mid}/chat", {"text": "Reviewed", "turn_id": first["id"]},
         token="finance-live")
    # The cooldown holds back routed turns only: a bot named directly still gets its turn.
    post(live, f"live-meetings/{mid}/chat", {"text": "Finance, any follow-up?"})
    after_cooldown = get(live, f"live-meetings/{mid}")
    assert after_cooldown["router"]["bypasses"][-1]["skipped"] == []
    assert after_cooldown["router"]["bypasses"][-1]["targets"] == ["finance"]
    assert any(turn["bot"] == "finance" and turn["status"] == "pending"
               for turn in after_cooldown["router"]["turns"])
    assert live.post("/api/v2/live-meetings", json={"title": "Invalid cap", "reply_cap": 21},
                     headers=headers()).status_code == 422
    assert live.post("/api/v2/live-meetings", json={"title": "Invalid cooldown", "cooldown_seconds": 3601},
                     headers=headers()).status_code == 422


def test_end_cancels_open_turns_and_records_final_window_pass(live):
    calls = []
    live.app.state.live_meeting_decider = lambda *args: calls.append(args)
    queued = connect(live, title="Open job")
    attach(live, queued["id"], "ops")
    post(live, f"live-meetings/{queued['id']}/chat", {"text": "Ops, please review"})
    assert get(live, f"live-meetings/{queued['id']}")["router"]["turns"][0]["status"] == "pending"
    ended = post(live, f"live-meetings/{queued['id']}/control", {"action": "end"})
    assert ended["router"]["turns"][0]["status"] == "skipped"
    assert ended["router"]["turns"][0]["skip_reason"] == "meeting_ended_before_reply"
    with live.app.state.store.read() as c:
        states = [row[0] for row in c.execute("SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                                               "WHERE json_extract(m.refs,'$.live_meeting.meeting_id')=?",
                                               (queued["id"],))]
        assert states == ["canceled"]

    final_window = connect(live, title="Partial final window")
    attach(live, final_window["id"], "ops")
    chunk(live, final_window["id"], 1, 0, 1000, "Tail text")
    finished = post(live, f"live-meetings/{final_window['id']}/control", {"action": "end"})
    assert finished["router"]["windows"][0]["outcome"] == "pass"
    assert finished["router"]["windows"][0]["trace"]["reason"] == "meeting_ended_final_window"
    assert finished["router"]["turns"] == []
    assert calls == []


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
    first_turn = detail["router"]["turns"][0]
    post(live, f"live-meetings/{mid}/turns/{first_turn['id']}/claim", {}, token="ops-live")
    post(live, f"live-meetings/{mid}/chat", {"text": "PASS", "turn_id": first_turn["id"]}, token="ops-live")

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
    assert set(one) == {"id", "meeting_id", "existing", "changed", "event_id"}
    assert one["id"] == mid and one["meeting_id"]
    assert one["meeting_id"] == two["meeting_id"]
    assert two["existing"] is True and two["changed"] is False
    assert one["event_id"] == two["event_id"]
    with live.app.state.store.read() as c:
        refs = c.execute("SELECT count(*) FROM recording_source_refs WHERE source='tico-live' AND external_id=?",
                         ("human:ana:" + mid,)).fetchone()[0]
        finalized_event = c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id=? "
                                    "AND type='meeting.finalized'", (mid,)).fetchone()
        finalized_events = c.execute("SELECT count(*) FROM live_meeting_events WHERE meeting_id=? "
                                     "AND type='meeting.finalized'", (mid,)).fetchone()[0]
    assert refs == 1
    assert finalized_events == 1
    with live.app.state.store.read() as c:
        imported = c.execute("SELECT review_state FROM meetings WHERE id=?", (one["meeting_id"],)).fetchone()
    assert imported["review_state"] == "pending"
    pending = get(live, "meetings?review=pending")
    assert one["meeting_id"] in {row["id"] for row in pending["meetings"]}
    assert json.loads(finalized_event["payload"]) == {
        "id": mid, "meeting_id": one["meeting_id"], "source": "tico-live", "external_id": mid,
    }


def test_joined_human_can_remove_bot_and_revoke_queued_turn_access(live):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.9} for key in questions}}
    meeting = connect(live, cooldown_seconds=0)
    mid = meeting["id"]
    attach(live, mid, "ops")
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test")
    chunk(live, mid, 1, 0, 30_000, "Queue a bot reply")
    turn = get(live, f"live-meetings/{mid}")["router"]["turns"][0]

    path = f"/api/v2/live-meetings/{mid}/bots/ops"
    removed = live.request("DELETE", path, json={}, headers=headers("ben-test", "remove-before-claim"))
    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] is True
    replay = live.request("DELETE", path, json={}, headers=headers("ben-test", "remove-before-claim"))
    assert replay.status_code == 200 and replay.json() == removed.json()
    detail = get(live, f"live-meetings/{mid}")
    assert detail["bots"] == []
    assert next(row for row in detail["router"]["turns"] if row["id"] == turn["id"])["skip_reason"] == "bot_detached"
    with live.app.state.store.read() as c:
        event = c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id=? "
                          "AND type='meeting.bot_left'", (mid,)).fetchone()
    assert json.loads(event["payload"]) == {
        "bot": "ops", "removed_by": "human:ben", "cancelled_turns": [turn["id"]],
        "reason": "meeting_only_access_revoked"}
    denied = live.get("/api/v2/live-meetings/" + mid, headers=headers("ops-live"))
    assert denied.status_code == 403
    denied_turns = live.get("/api/v2/live-meetings/" + mid + "/turns", headers=headers("ops-live"))
    assert denied_turns.status_code == 403


def test_chunk_correction_versions_are_idempotent_and_final_transcript_uses_latest(live):
    meeting = connect(live)
    mid = meeting["id"]
    chunk(live, mid, 1, 0, 1000, "Initial text")
    revision = {"revision": 2, "speaker": "Ana", "start_ms": 0, "end_ms": 1000, "text": "Corrected text"}
    first = post(live, f"live-meetings/{mid}/chunks/1/corrections", revision)
    again = post(live, f"live-meetings/{mid}/chunks/1/corrections", revision)
    assert set(first) == {"id", "seq", "revision", "replayed", "event_id"}
    assert first["id"] == mid and first["seq"] == 1 and first["replayed"] is False
    assert first["revision"] == again["revision"] == 2
    assert again["replayed"] is True
    conflict = live.post("/api/v2/live-meetings/" + mid + "/chunks/1/corrections",
                         json={**revision, "text": "Different correction"}, headers=headers())
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "revision_conflict"
    gap = live.post("/api/v2/live-meetings/" + mid + "/chunks/1/corrections",
                    json={**revision, "revision": 4}, headers=headers())
    assert gap.status_code == 409
    assert gap.json()["error"]["code"] == "revision_gap"
    assert gap.json()["error"]["expected_revision"] == 3
    with live.app.state.store.read() as c:
        corrected_event = c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id=? "
                                    "AND type='meeting.chunk_corrected'", (mid,)).fetchone()
    assert json.loads(corrected_event["payload"]) == {
        "seq": 1, "revision": 2, "speaker": "Ana", "start_ms": 0, "end_ms": 1000,
        "text": "Corrected text", "corrected_by": "human:ana",
    }
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


def test_empty_correction_clears_a_streamed_chunk_from_the_transcript(live):
    meeting = connect(live)
    mid = meeting["id"]
    chunk(live, mid, 1, 0, 1000, "Kept words")
    chunk(live, mid, 2, 1000, 2000, "Uh")
    cleared = post(live, f"live-meetings/{mid}/chunks/2/corrections",
                   {"revision": 2, "speaker": "", "start_ms": 1000, "end_ms": 2000, "text": "  "})
    assert cleared["revision"] == 2 and cleared["replayed"] is False
    assert [row["seq"] for row in get(live, f"live-meetings/{mid}")["chunks"]] == [1]
    post(live, f"live-meetings/{mid}/control", {"action": "end"})
    finalized = post(live, f"live-meetings/{mid}/finalize", {})
    with live.app.state.store.read() as c:
        row = c.execute("SELECT transcript_original FROM meetings WHERE id=?", (finalized["meeting_id"],)).fetchone()
    assert "Kept words" in row["transcript_original"] and "Uh" not in row["transcript_original"]


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
    assert set(candidates) == {"bots"}
    assert all(set(bot) == {"id", "slug", "name", "description", "team"} for bot in candidates["bots"])
    assert "ops" not in {bot["slug"] for bot in candidates["bots"]}
    hidden_bot = live.post("/api/v2/live-meetings/" + private_meeting["id"] + "/bots",
                           json={"bots": ["ops"]}, headers=headers("cara-test"))
    assert hidden_bot.status_code == 403


def test_attachment_requires_write_and_keeper_delivery_keeps_human_contact_policy(live):
    with live.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["cara"]}, read={"people": ["cara"]},
                 write={"people": ["ana"], "bots": ["finance"]})
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        config["bot_contact"] = "tasks"
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (encode(config),))

    meeting = connect(live, title="Write-authorized contact")
    mid = meeting["id"]
    post(live, f"live-meetings/{mid}/join", {}, token="cara-test")
    candidates = get(live, "live-meetings/bot-candidates", token="cara-test")
    assert "ops" not in {bot["slug"] for bot in candidates["bots"]}
    denied = live.post("/api/v2/live-meetings/" + mid + "/bots", json={"bots": ["ops"]},
                       headers=headers("cara-test"))
    assert denied.status_code == 403
    assert "Write access" in denied.json()["error"]["detail"]

    # The meeting turn is authorized by the recorded human's Write grant. That contact does not
    # let another bot bypass the target's tasks-only contact setting.
    bot_chat = live.post("/api/v2/chat/ops", json={"text": "Can you check this?"},
                         headers=headers("finance-live"))
    assert bot_chat.status_code == 403
    assert bot_chat.json()["error"]["code"] == "bot_contact"

    with live.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["cara"]}, read={"people": ["cara"]},
                 write={"people": ["cara"]})
    candidates = get(live, "live-meetings/bot-candidates", token="cara-test")
    assert "ops" in {bot["slug"] for bot in candidates["bots"]}
    attach(live, mid, "ops", token="cara-test")
    with live.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["cara"]}, read={"people": ["cara"]},
                 write={"people": ["ana"]})
    chunk(live, mid, 1, 0, 1000, "Ops, please check the total")
    detail = get(live, f"live-meetings/{mid}")
    assert detail["router"]["turns"][0]["bot"] == "ops"
    turn_id = detail["router"]["turns"][0]["id"]
    claim = post(live, f"live-meetings/{mid}/turns/{turn_id}/claim", {}, token="ops-live")
    assert claim["status"] == "skipped" and claim["reason"] == "bot_write_access_revoked"
    with live.app.state.store.read() as c:
        job = c.execute("SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                        "WHERE json_extract(m.refs,'$.live_meeting.turn_id')=?", (turn_id,)).fetchone()
    assert job["state"] == "canceled"


@pytest.mark.slow
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
    assert response.status_code == 200, response.text
    assert settle(live)
    elapsed = time.monotonic() - started
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
    assert settle(live)
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

    monkeypatch.setattr(Request, "is_disconnected", is_disconnected)
    monkeypatch.setattr(live_meetings_module, "EVENT_POLL_SECONDS", 0)

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
    payload = json.loads(first.split("data: ", 1)[1])
    assert payload == {"seq": 1, "speaker": "Ana", "start_ms": 0, "end_ms": 1000,
                       "text": "Persisted event", "revision": 1}
    assert keepalive.startswith(": keep-alive")

    # Recorder may resume using the query cursor instead; main-process fetch attaches Bearer auth.
    after_request = Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"after=1", "root_path": "",
        "headers": [(b"authorization", b"Bearer ana-test")],
        "client": ("testclient", 80), "server": ("testserver", 80),
    })
    after_request.state.identity = Identity("human:ana", "owner", "ana@acme.example")
    disconnected = 0

    async def read_after_cursor():
        response = await event_route.endpoint(after_request, mid, after=1)
        iterator = response.body_iterator
        first_event = await iterator.__anext__()
        await iterator.aclose()
        return first_event

    after_first = asyncio.run(read_after_cursor())
    assert after_first == first


def test_private_meeting_is_hidden_until_owner_adds_people_or_opens_it_to_the_team(live):
    meeting = post(live, "live-meetings", {"title": "Private sync", "visibility": "private"})
    mid = meeting["id"]
    assert meeting["visibility"] == "private" and meeting["access"] == []
    assert mid not in {row["id"] for row in get(live, "live-meetings", token="ben-test")["meetings"]}
    for path in ("", "/events"):
        assert live.get(f"/api/v2/live-meetings/{mid}{path}", headers=headers("ben-test")).status_code == 404
    assert live.post(f"/api/v2/live-meetings/{mid}/join", json={}, headers=headers("ben-test")).status_code == 404
    # The owner can always add a bot; the bot reads only this meeting.
    assert attach(live, mid, "ops")["added"] == ["ops"]
    assert get(live, f"live-meetings/{mid}", token="ops-live")["id"] == mid
    # Only the owner changes who can see it.
    denied = live.post(f"/api/v2/live-meetings/{mid}/access", json={"add": ["ben"]}, headers=headers("cara-test"))
    assert denied.status_code == 404
    shared = post(live, f"live-meetings/{mid}/access", {"add": ["ben"]})
    assert [row["actor"] for row in shared["access"]] == ["human:ben"]
    assert mid in {row["id"] for row in get(live, "live-meetings", token="ben-test")["meetings"]}
    assert live.get(f"/api/v2/live-meetings/{mid}", headers=headers("cara-test")).status_code == 404
    post(live, f"live-meetings/{mid}/join", {}, token="ben-test")
    post(live, f"live-meetings/{mid}/access", {"remove": ["ben"]})
    assert live.get(f"/api/v2/live-meetings/{mid}", headers=headers("ben-test")).status_code == 404
    opened = post(live, f"live-meetings/{mid}/access", {"visibility": "team"})
    assert opened["visibility"] == "team"
    post(live, f"live-meetings/{mid}/join", {}, token="cara-test")
    unknown = live.post(f"/api/v2/live-meetings/{mid}/access", json={"add": ["nobody-here"]},
                        headers=headers("ana-test"))
    assert unknown.status_code == 404


def test_a_held_notice_does_not_make_an_idle_bot_miss_its_meeting_turn(live):
    """A wake preset's held notice is queued but starts no run (backend/wake_presets.py): the bot is not busy."""
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    with live.app.state.store.transaction() as c:
        task = H.task_create(c, H.human_actor("ana"), "Tidy the notes", "Please tidy them.", "bot:ops")
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
        H._wake(c, H.task(c, task["id"]), "bot:ops", "Finished: the review", {"wake_batch": "completion"})
        assert c.execute("SELECT held FROM jobs WHERE bot_id='ops' AND state='queued'").fetchone()[0] == 1
    meeting = connect(live)
    attach(live, meeting["id"], "ops")
    chunk(live, meeting["id"], 1, 0, 30_000, "The rollout needs a reliability review")
    turns = {turn["bot"]: turn for turn in get(live, f"live-meetings/{meeting['id']}")["router"]["turns"]}
    assert "ops" in turns and turns["ops"]["skip_reason"] != "busy"


# A live meeting's reply runs on the company's meeting model (or its own, set at connect) at low effort; other turns and a
# company with the Gateway off keep the bot's own model.
MEETING = "tico-meeting-1.0"


def gateway_on(api, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "gateway_key", "synthetic-gateway-key", raising=False)
    monkeypatch.setattr(gateway_connection, "connected", lambda c, settings=None: True)


def set_meeting_model(api, model):
    with api.app.state.store.transaction() as c:
        providers.save(c, "human:ana", {"enabled": [], "meeting_model": model,
                                        "expected_revision": providers.load(c)["revision"]}, H.now())


def test_the_meeting_model_defaults_to_the_gateway_model_only_while_the_gateway_is_on(live, monkeypatch):
    settings = live.app.state.store.settings
    with live.app.state.store.read() as c:
        assert providers.load(c, settings)["meeting_model"] == ""
    gateway_on(live, monkeypatch)
    with live.app.state.store.read() as c:
        assert providers.load(c, settings)["meeting_model"] == providers.MEETING_MODEL
    set_meeting_model(live, "")       # the bot's own model stays chosen even with the Gateway on
    with live.app.state.store.read() as c:
        assert providers.load(c, settings)["meeting_model"] == ""
    with pytest.raises(providers.ProviderError):
        set_meeting_model(live, "no-such-model")


def test_a_meeting_reply_runs_on_the_meeting_model_and_other_turns_keep_the_bots_model(live, monkeypatch):
    settings = live.app.state.store.settings
    gateway_on(live, monkeypatch)
    set_meeting_model(live, MEETING)
    own = {"runtime": "hermes", "harness": "hermes", "model": "bots-own", "reasoning_effort": "high",
           "task_model": {"model": "some-task-model"}}
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,cooldown_ms,reply_cap,threshold,"
                  "created_at,updated_at,started_at,visibility,client,meeting_model) "
                  "VALUES('m-company','human:ana','t','live',30000,60000,3,0.7,?,?,?,'team',NULL,NULL)",
                  (H.now(), H.now(), H.now()))
        turn = live_meetings_module.turn_config(c, settings, own, "ops", "m-company", "")
        assert (turn["runtime"], turn["harness"], turn["model"], turn["reasoning_effort"]) == ("pi", "pi", MEETING, "low")
        assert "task_model" not in turn and own["model"] == "bots-own"
        assert live_meetings_module.turn_config(c, settings, own, "ops", None, "") is own
        assert live_meetings_module.turn_config(c, settings, own, "ops", "missing", "") is own


def test_the_per_meeting_model_wins_and_an_empty_one_keeps_the_bots_model(live, monkeypatch):
    settings = live.app.state.store.settings
    gateway_on(live, monkeypatch)
    set_meeting_model(live, MEETING)
    own = {"runtime": "hermes", "harness": "hermes", "model": "bots-own"}
    post(live, "live-meetings", {"title": "Bad", "meeting_model": "no-such-model"}, expected=422)
    own_meeting = post(live, "live-meetings", {"title": "Own", "meeting_model": ""})
    picked = post(live, "live-meetings", {"title": "Picked", "meeting_model": MEETING})
    with live.app.state.store.transaction() as c:
        stored = c.execute("SELECT meeting_model FROM live_meetings WHERE id=?", (own_meeting["id"],)).fetchone()
        assert stored[0] == ""
        assert live_meetings_module.turn_config(c, settings, own, "ops", own_meeting["id"], "") == {**own, "session": "task"}
        turn = live_meetings_module.turn_config(c, settings, own, "ops", picked["id"], "")
        assert turn["model"] == MEETING and turn["runtime"] == "pi"


def test_a_meeting_model_the_runner_cannot_run_falls_back_to_the_bots_model(live, monkeypatch):
    settings = live.app.state.store.settings
    gateway_on(live, monkeypatch)
    set_meeting_model(live, MEETING)
    own = {"runtime": "hermes", "harness": "hermes", "model": "bots-own"}
    monkeypatch.setattr(task_models, "why_not", lambda *args, **kwargs: "pi is not installed on this computer")
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,cooldown_ms,reply_cap,threshold,"
                  "created_at,updated_at,started_at,visibility,client,meeting_model) "
                  "VALUES('m-fallback','human:ana','t','live',30000,60000,3,0.7,?,?,?,'team',NULL,NULL)",
                  (H.now(), H.now(), H.now()))
        assert live_meetings_module.turn_config(c, settings, own, "ops", "m-fallback", "", turn_id="t-1") == {
            **own, "session": "task"}
        event = c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id='m-fallback' AND type='meeting.model'").fetchone()
        payload = json.loads(event[0])
        assert payload["fallback"] == "bot_model" and payload["turn_id"] == "t-1"
        assert "not installed" in payload["reason"]
        # Claimed again (a retry, a resumed run), the turn falls back the same way: the meeting is not told twice.
        live_meetings_module.turn_config(c, settings, own, "ops", "m-fallback", "", turn_id="t-1")
        live_meetings_module.turn_config(c, settings, own, "ops", "m-fallback", "", turn_id="t-1")
        assert c.execute("SELECT count(*) FROM live_meeting_events WHERE meeting_id='m-fallback' "
                         "AND type='meeting.model'").fetchone()[0] == 1


def test_a_meeting_model_outside_the_catalog_falls_back_and_says_why(live, monkeypatch):
    settings = live.app.state.store.settings
    gateway_on(live, monkeypatch)
    own = {"runtime": "hermes", "harness": "hermes", "model": "bots-own"}
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,cooldown_ms,reply_cap,threshold,"
                  "created_at,updated_at,started_at,visibility,client,meeting_model) "
                  "VALUES('m-gone','human:ana','t','live',30000,60000,3,0.7,?,?,?,'team',NULL,'retired-model')",
                  (H.now(), H.now(), H.now()))
        assert live_meetings_module.turn_config(c, settings, own, "ops", "m-gone", "") == {**own, "session": "task"}
        payload = json.loads(c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id='m-gone' "
                                       "AND type='meeting.model'").fetchone()[0])
        assert "not in the model catalog" in payload["reason"]


def test_live_meetings_are_not_limited_by_the_daily_decision_budget_but_the_monthly_cap_still_stops_them(live, monkeypatch):
    settings = live.app.state.store.settings
    for role in ("owner", "human", "bot"):
        monkeypatch.setattr(settings, f"decisions_daily_calls_{role}", 0, raising=False)
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live)
    attach(live, meeting["id"], "ops")
    chunk(live, meeting["id"], 1, 0, 30_000, "The rollout needs a reliability review")
    routed = get(live, f"live-meetings/{meeting['id']}")
    assert [turn["bot"] for turn in routed["router"]["turns"]] == ["ops"]
    monkeypatch.setattr(live_meetings_module, "over_spend_cap", lambda c, settings: True)
    capped = connect(live, title="Capped")
    attach(live, capped["id"], "ops")
    chunk(live, capped["id"], 1, 0, 30_000, "The rollout needs a reliability review")
    windows = get(live, f"live-meetings/{capped['id']}")["router"]["windows"]
    assert "monthly_decision_spend_cap" in {window["trace"]["reason"] for window in windows}
    assert get(live, f"live-meetings/{capped['id']}")["router"]["turns"] == []


def test_a_routed_meeting_turn_is_claimed_on_the_meeting_model(live, monkeypatch):
    gateway_on(live, monkeypatch)
    set_meeting_model(live, MEETING)
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live)
    attach(live, meeting["id"], "ops")
    chunk(live, meeting["id"], 1, 0, 30_000, "The rollout needs a reliability review")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    with live.app.state.store.read() as c:
        handed = c.execute("SELECT handed_runtime,handed_model FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    assert (handed[0], handed[1]) == ("pi", MEETING)


def claimed_turn(api, machine, bot="ops"):
    """The next meeting turn a runner claims for `bot`, started as a running turn."""
    attempt = post(api, "jobs/claim", {"bot": bot}, token=machine["token"])["attempt"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-" + attempt["id"]}, token=machine["token"])
    return attempt


def thread_of(attempt):
    """The provider thread the runner keys this turn on (runner/state.py and runner-rs session_key, clients/sessions.py)."""
    from clients import sessions
    return sessions.session_key(attempt["config"], attempt["bot"], (attempt.get("task") or {}).get("id"),
                                (attempt.get("conversation") or {}).get("id"))


def test_each_meeting_is_its_own_thread_and_a_meetings_turns_continue_it(live):
    """A private meeting's words never reach another meeting or the bot's chats: each meeting and bot has one
    conversation, and its turns run on that conversation's own thread, whatever the bot's session setting."""
    with live.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.session','bot') WHERE bot='ops'")
    private = post(live, "live-meetings", {"title": "Private A", "visibility": "private", "cooldown_seconds": 0})
    team = post(live, "live-meetings", {"title": "Team B", "cooldown_seconds": 0})
    for meeting in (private, team):
        attach(live, meeting["id"], "ops")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    chunk(live, private["id"], 1, 0, 1000, "Ops, the synthetic codename is confidential")
    first = claimed_turn(live, machine)
    post(live, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "Noted.", "last_seq": 0},
         token=machine["token"])
    chunk(live, private["id"], 2, 1000, 2000, "Ops, what did I just say?")
    second = claimed_turn(live, machine)
    post(live, f"attempts/{second['id']}/complete", {"outcome": "completed", "text": "The codename.", "last_seq": 0},
         token=machine["token"])
    chunk(live, team["id"], 1, 0, 1000, "Ops, what is the codename?")
    other = claimed_turn(live, machine)
    assert first["conversation"]["id"] == second["conversation"]["id"] != other["conversation"]["id"]
    assert thread_of(first) == thread_of(second) == "conversation:" + first["conversation"]["id"]
    assert thread_of(other) == "conversation:" + other["conversation"]["id"] != thread_of(first)
    with live.app.state.store.read() as c:
        own = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        kept = {row[0] for row in c.execute("SELECT conversation_id FROM live_meeting_bots WHERE bot='ops'")}
    from clients import sessions
    assert sessions.session_key(own, "ops", None, "any-direct-chat") == sessions.BOT_THREAD != thread_of(first)
    assert kept == {first["conversation"]["id"], other["conversation"]["id"]}
    assert "confidential" not in other["message"]["body"]


def test_a_named_follow_up_while_the_bot_replies_is_steered_into_that_reply(live):
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    chunk(live, mid, 1, 0, 1000, "Ops, how is the rollout?")
    running = claimed_turn(live, machine)
    chunk(live, mid, 2, 1000, 2000, "Ops, and the rollback plan too")
    detail = get(live, f"live-meetings/{mid}")
    assert detail["router"]["bypasses"][-1]["skipped"] == []
    follow = next(turn for turn in detail["router"]["turns"] if turn["source_key"] == "chunk:2")
    first = next(turn for turn in detail["router"]["turns"] if turn["source_key"] == "chunk:1")
    with live.app.state.store.read() as c:
        deferred = [json.loads(row[0]) for row in c.execute(
            "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.bot_turn_deferred'", (mid,))]
    assert deferred == [{"turn_id": follow["id"], "bot": "ops", "reason": "replying_in_meeting",
                         "behind": first["id"], "source_key": "chunk:2"}]
    # A runtime that steers takes the follow-up into the running turn, as any follow-up in its conversation.
    taken = post(live, f"attempts/{running['id']}/inputs", {}, token=machine["token"])["messages"]
    assert len(taken) == 1 and "rollback plan" in taken[0]["body"]
    post(live, f"attempts/{running['id']}/complete",
         {"outcome": "completed", "text": "On track; rollback is ready.", "last_seq": 0}, token=machine["token"])
    turns = {turn["id"]: turn for turn in get(live, f"live-meetings/{mid}")["router"]["turns"]}
    assert turns[first["id"]]["status"] == "replied"
    assert (turns[follow["id"]]["status"], turns[follow["id"]]["skip_reason"]) == ("skipped", "steered_into_turn")
    assert post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None


def test_a_named_follow_up_a_runtime_cannot_steer_runs_right_after_and_busy_bots_are_queued(live):
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    chunk(live, mid, 1, 0, 1000, "Ops, how is the rollout?")
    running = claimed_turn(live, machine)
    chunk(live, mid, 2, 1000, 2000, "Ops, and the rollback plan too")
    post(live, f"attempts/{running['id']}/complete", {"outcome": "completed", "text": "On track.", "last_seq": 0},
         token=machine["token"])
    after = claimed_turn(live, machine)
    assert "rollback plan" in after["message"]["body"]
    assert after["conversation"]["id"] == running["conversation"]["id"]
    turns = {turn["source_key"]: turn for turn in get(live, f"live-meetings/{mid}")["router"]["turns"]}
    assert turns["chunk:2"]["status"] == "claimed"
    post(live, f"attempts/{after['id']}/complete", {"outcome": "completed", "text": "Rollback is ready.",
                                                    "last_seq": 0}, token=machine["token"])
    # Busy with other work: the named turn is queued behind it, never dropped.
    with live.app.state.store.transaction() as c:
        H.task_create(c, H.human_actor("ana"), "Tidy the notes", "Please tidy them.", "bot:ops")
    chunk(live, mid, 3, 2000, 3000, "Ops, one more thing")
    detail = get(live, f"live-meetings/{mid}")
    assert any(turn["source_key"] == "chunk:3" and turn["status"] == "pending" for turn in detail["router"]["turns"])
    with live.app.state.store.read() as c:
        reasons = [json.loads(row[0])["reason"] for row in c.execute(
            "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.bot_turn_deferred'", (mid,))]
    assert reasons[-1] == "busy"


def test_a_routed_window_still_skips_a_busy_bot_and_one_in_cooldown(live):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live, cooldown_seconds=600)
    mid = meeting["id"]
    attach(live, mid, "ops")

    def skipped_at(seq):
        return next(window["trace"]["skipped"] for window in get(live, f"live-meetings/{mid}")["router"]["windows"]
                    if window["trace"].get("chunk_range") == [seq, seq])

    chunk(live, mid, 1, 0, 30_000, "The rollout needs a reliability review")
    turn = get(live, f"live-meetings/{mid}")["router"]["turns"][0]
    chunk(live, mid, 2, 30_000, 60_000, "More about the rollout")
    assert skipped_at(2) == [{"bot": "ops", "reason": "busy"}]
    post(live, f"live-meetings/{mid}/turns/{turn['id']}/claim", {}, token="ops-live")
    post(live, f"live-meetings/{mid}/chat", {"text": "Reviewed", "turn_id": turn["id"]}, token="ops-live")
    chunk(live, mid, 3, 60_000, 90_000, "Still the rollout")
    assert skipped_at(3) == [{"bot": "ops", "reason": "cooldown"}]


def test_a_chunk_is_acknowledged_before_its_window_decision_comes_back(live):
    import threading
    gate, asked = threading.Event(), threading.Event()

    def slow(state, questions, label):
        asked.set()
        gate.wait(30)
        return {"model": "synthetic-decider", "ms": 1,
                "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}

    live.app.state.live_meeting_decider = slow
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    started = time.monotonic()
    response = live.post(f"/api/v2/live-meetings/{mid}/chunks", headers=headers(), json={"chunks": [
        {"seq": 1, "speaker": "Ana", "start_ms": 0, "end_ms": 30_000, "text": "The rollout needs a review"}]})
    assert response.status_code == 200 and response.json()["accepted"] == [1]
    assert asked.wait(10) and time.monotonic() - started < 10
    assert get(live, f"live-meetings/{mid}")["router"]["windows"][0]["status"] == "deciding"
    # A second chunk while the first window is still deciding neither waits nor starts a second decision of it.
    again = live.post(f"/api/v2/live-meetings/{mid}/chunks", headers=headers(), json={"chunks": [
        {"seq": 2, "speaker": "Ana", "start_ms": 30_000, "end_ms": 31_000, "text": "Anything else"}]})
    assert again.status_code == 200
    gate.set()
    assert settle(live)
    windows = get(live, f"live-meetings/{mid}")["router"]["windows"]
    assert [window["outcome"] for window in windows if window["trace"].get("chunk_range")] == ["route"]
    # A lost routing thread (a restart) is picked up by the scheduler's sweep.
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO live_meeting_chunks(meeting_id,seq,revision,speaker,start_ms,end_ms,text,created_at) "
                  "VALUES(?,3,1,'Ana',40000,61000,'Late words',?)", (mid, H.now()))
        c.execute("UPDATE live_meetings SET seq=3 WHERE id=?", (mid,))
    live.app.state.live_meeting_routing.sweep()
    assert settle(live)
    windows = get(live, f"live-meetings/{mid}")["router"]["windows"]
    assert [window["trace"]["chunk_range"] for window in windows if window["trace"].get("chunk_range")] == [[1, 1], [2, 3]]


def test_a_failed_decision_keeps_its_code_and_never_its_detail(live):
    from clients import judge as J

    def failing(state, questions, label):
        raise J.JudgeError("unavailable", "upstream echoed: synthetic-private-words", status=503, retryable=True)

    live.app.state.live_meeting_decider = failing
    meeting = connect(live)
    attach(live, meeting["id"], "ops")
    chunk(live, meeting["id"], 1, 0, 30_000, "Synthetic words")
    trace = get(live, f"live-meetings/{meeting['id']}")["router"]["windows"][0]["trace"]
    assert (trace["reason"], trace["error"], trace["code"], trace["status"], trace["retryable"]) == (
        "decision_error", "JudgeError", "unavailable", 503, True)
    assert "synthetic-private-words" not in json.dumps({k: v for k, v in trace.items() if k != "decision_input"})


def test_the_meeting_view_says_its_meeting_model(live):
    assert connect(live)["meeting_model"] is None
    own = post(live, "live-meetings", {"title": "Own", "meeting_model": ""})
    assert get(live, f"live-meetings/{own['id']}")["meeting_model"] == ""


def test_a_gateway_meeting_model_a_computer_cannot_run_falls_back_to_mercury_on_openrouter(live, monkeypatch):
    settings = live.app.state.store.settings
    gateway_on(live, monkeypatch)
    own = {"runtime": "hermes", "harness": "hermes", "model": "bots-own"}
    gateway_off = lambda c, settings, bot, choice, *args: (
        "the Tico AI Gateway does not run on this Mac" if choice["model"].startswith("tico-") else "")
    monkeypatch.setattr(task_models, "why_not", gateway_off)

    def meeting(rid):
        c.execute("INSERT INTO live_meetings(id,owner_actor,title,state,window_ms,cooldown_ms,reply_cap,threshold,"
                  "created_at,updated_at,started_at,visibility,client,meeting_model) "
                  "VALUES(?,'human:ana','t','live',30000,60000,3,0.7,?,?,?,'team',NULL,NULL)",
                  (rid, H.now(), H.now(), H.now()))

    def said(rid):
        return json.loads(c.execute("SELECT payload FROM live_meeting_events WHERE meeting_id=? "
                                    "AND type='meeting.model'", (rid,)).fetchone()[0])

    with live.app.state.store.transaction() as c:
        providers.save(c, "human:ana", {"enabled": ["openrouter"], "meeting_model": MEETING,
                                        "expected_revision": providers.load(c, settings)["revision"]}, H.now(), settings)
        meeting("m-mac")
        turn = live_meetings_module.turn_config(c, settings, own, "ops", "m-mac", "", turn_id="t-1")
        assert (turn["runtime"], turn["model"], turn["reasoning_effort"], turn["session"]) == (
            "pi", "mercury-3", "low", "task")
        payload = said("m-mac")
        assert (payload["model"], payload["fallback"], payload["ran"]) == (MEETING, "openrouter", "mercury-3")
        assert payload["steps"] == [{"model": MEETING, "reason": "the Tico AI Gateway does not run on this Mac"}]
    with live.app.state.store.transaction() as c:
        providers.save(c, "human:ana", {"enabled": ["anthropic"],
                                        "expected_revision": providers.load(c, settings)["revision"]}, H.now(), settings)
        meeting("m-no-openrouter")
        assert live_meetings_module.turn_config(c, settings, own, "ops", "m-no-openrouter", "") == {**own, "session": "task"}
        payload = said("m-no-openrouter")
        assert (payload["fallback"], payload["ran"]) == ("bot_model", "bots-own")
        assert [step["model"] for step in payload["steps"]] == [MEETING, "mercury-3"]
        assert payload["steps"][1]["reason"] == "OpenRouter is off for this company"


def handed(api, attempt):
    with api.app.state.store.read() as c:
        return c.execute("SELECT handed_model FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]


def retried(api, mid):
    with api.app.state.store.read() as c:
        return [json.loads(row[0]) for row in c.execute(
            "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.bot_turn_retried' "
            "ORDER BY event_id", (mid,))]


def test_an_empty_reply_on_the_meeting_model_is_asked_again_then_on_the_bots_own_model_and_then_given_up(
        live, monkeypatch):
    gateway_on(live, monkeypatch)
    set_meeting_model(live, MEETING)
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    chunk(live, mid, 1, 0, 1000, "Ops, how is the rollout?")
    runs = []
    for _ in range(3):
        attempt = claimed_turn(live, machine)
        runs.append(attempt)
        post(live, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": " ", "last_seq": 0},
             token=machine["token"])
    assert [handed(live, run) for run in runs[:2]] == [MEETING, MEETING]
    assert handed(live, runs[2]) != MEETING
    assert len({run["conversation"]["id"] for run in runs}) == 1
    assert len({run["message"]["refs"]["live_meeting"]["turn_id"] for run in runs}) == 1
    assert [run["message"]["body"].startswith("Your last reply to this turn was empty") for run in runs] == [
        False, True, True]
    turn_id = runs[0]["message"]["refs"]["live_meeting"]["turn_id"]
    assert retried(live, mid) == [
        {"turn_id": turn_id, "bot": "ops", "reason": "empty_bot_reply", "retry": 1, "model": MEETING},
        {"turn_id": turn_id, "bot": "ops", "reason": "empty_bot_reply", "retry": 2, "model": None}]
    turn = next(t for t in get(live, f"live-meetings/{mid}")["router"]["turns"] if t["id"] == turn_id)
    assert (turn["status"], turn["skip_reason"]) == ("skipped", "empty_bot_reply")
    assert post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None


def test_an_empty_reply_on_the_bots_own_model_is_asked_once_more_and_an_answer_then_lands(live):
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    chunk(live, mid, 1, 0, 1000, "Ops, how is the rollout?")
    first = claimed_turn(live, machine)
    post(live, f"attempts/{first['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0},
         token=machine["token"])
    again = claimed_turn(live, machine)
    post(live, f"attempts/{again['id']}/complete", {"outcome": "completed", "text": "On track.", "last_seq": 0},
         token=machine["token"])
    assert [(row["retry"], row["model"]) for row in retried(live, mid)] == [(1, None)]
    detail = get(live, f"live-meetings/{mid}")
    assert detail["router"]["turns"][0]["status"] == "replied"
    assert [row["text"] for row in detail["chat"] if row["role"] == "bot"] == ["On track."]
    # Its own model gave the empty one, so a second empty one is not asked a third time.
    chunk(live, mid, 2, 1000, 2000, "Ops, and the rollback?")
    for _ in range(2):
        attempt = claimed_turn(live, machine)
        post(live, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0},
             token=machine["token"])
    assert len(retried(live, mid)) == 2
    assert post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None


def test_a_question_followed_by_silence_is_routed_once_the_window_falls_quiet(live, monkeypatch):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    chunk(live, mid, 1, 0, 4000, "Can someone check the rollout?")
    routing = live.app.state.live_meeting_routing
    assert get(live, f"live-meetings/{mid}")["router"]["windows"] == []
    window = meeting["window_ms"] / 1000
    assert window + 1 < routing.timers[mid].interval <= window + 2     # the window and its grace (2 s), from the chunk
    clock = {"at": H.now()}
    monkeypatch.setattr(live_meetings_module, "_now", lambda: clock["at"])
    clock["at"] = H.shift(H.now(), seconds=window + 1)
    routing.later(mid)
    assert settle(live) and get(live, f"live-meetings/{mid}")["router"]["windows"] == []
    clock["at"] = H.shift(H.now(), seconds=window + 3)
    routing.later(mid)
    routing.later(mid)
    assert settle(live)
    windows = get(live, f"live-meetings/{mid}")["router"]["windows"]
    assert [(window["window_index"], window["outcome"]) for window in windows] == [(0, "route")]
    assert [turn["bot"] for turn in get(live, f"live-meetings/{mid}")["router"]["turns"]] == ["ops"]
    routing.later(mid)                                   # routed once: going round again decides nothing new
    assert settle(live) and len(get(live, f"live-meetings/{mid}")["router"]["windows"]) == 1


def test_a_chunk_that_arrives_after_its_window_closed_is_routed_once_by_the_next_open_window(live, monkeypatch):
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    meeting = connect(live)
    mid = meeting["id"]
    attach(live, mid, "ops")
    routing = live.app.state.live_meeting_routing
    window = meeting["window_ms"] / 1000
    clock = {"at": H.now()}
    monkeypatch.setattr(live_meetings_module, "_now", lambda: clock["at"])
    chunk(live, mid, 1, 0, 4000, "Can someone check the rollout?")
    clock["at"] = H.shift(H.now(), seconds=window + 3)
    routing.later(mid)
    assert settle(live)
    assert [(w["window_index"], w["outcome"], w["trace"]["chunk_range"]) for w in
            get(live, f"live-meetings/{mid}")["router"]["windows"]] == [(0, "route", [1, 1])]
    # Seq 2 starts inside window 0, which closed quiet a moment ago: it waits for the next window that is open.
    clock["at"] = H.now()
    chunk(live, mid, 2, 10_000, 11_000, "Late question for ops")
    assert settle(live)
    assert len(get(live, f"live-meetings/{mid}")["router"]["windows"]) == 1
    clock["at"] = H.shift(H.now(), seconds=window + 3)
    routing.later(mid)
    assert settle(live)
    windows = get(live, f"live-meetings/{mid}")["router"]["windows"]
    assert [(w["window_index"], w["start_ms"], w["trace"]["chunk_range"]) for w in windows] == [
        (0, 0, [1, 1]), (1, meeting["window_ms"], [2, 2])]
    # Routed once: going round again decides nothing new, and a correction to that text routes nothing either.
    routing.later(mid)
    assert settle(live) and len(get(live, f"live-meetings/{mid}")["router"]["windows"]) == 2
    later = []
    monkeypatch.setattr(routing, "later", lambda rid, chat_id=None: later.append(rid))
    post(live, f"live-meetings/{mid}/chunks/2/corrections", {
        "revision": 2, "speaker": "Ana", "start_ms": 10_000, "end_ms": 11_000, "text": "Late question for Ops"})
    assert later == []


def test_only_a_private_meetings_turn_tells_the_bot_it_cannot_save_or_share_it(live):
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    prompts = {}
    for visibility in ("private", "team"):
        meeting = post(live, "live-meetings", {"title": visibility, "visibility": visibility, "cooldown_seconds": 0})
        attach(live, meeting["id"], "ops")
        chunk(live, meeting["id"], 1, 0, 1000, "Ops, remember the word Tangerine")
        attempt = claimed_turn(live, machine)
        prompts[visibility] = attempt["message"]["body"]
        post(live, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Noted.", "last_seq": 0},
             token=machine["token"])
    rule = "This meeting is private: you cannot save or share anything from it"
    assert rule in prompts["private"] and rule not in prompts["team"]
    assert "the meeting's owner can share its notes and action items after the meeting" in prompts["private"]


def test_a_private_meetings_turns_run_in_its_own_lane_whose_checkout_goes_when_it_ends(live):
    """The runner keeps a private meeting's checkout apart from the bot's (runner-rs/src/sessions.rs, private lanes):
    the server hands its turns that lane and says when the meeting ended. A team meeting's turns stay in the main lane."""
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"], ["task_lanes_v2", "private_lanes_v1"])
    turns = {}
    for visibility in ("private", "team"):
        meeting = post(live, "live-meetings", {"title": visibility, "visibility": visibility, "cooldown_seconds": 0})
        attach(live, meeting["id"], "ops")
        chunk(live, meeting["id"], 1, 0, 1000, "Ops, the press embargo lifts Nov 17 9am PT. Got it?")
        attempt = claimed_turn(live, machine)
        turns[visibility] = (meeting["id"], attempt)
        assert attempt["message"]["body"] in [m["body"] for m in attempt["history"]], "its own lane reads its room"
        post(live, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Got it.", "last_seq": 0},
             token=machine["token"])
    mid, attempt = turns["private"]
    conv = attempt["conversation"]["id"]
    assert (attempt["lane"], turns["team"][1]["lane"]) == ("private:" + conv, "main")
    mine = {"bot": "ops", "task": "private-" + conv}
    sessions = [mine, {"bot": "ops", "task": "private-" + turns["team"][1]["conversation"]["id"]}]
    asked = lambda: post(live, "runners/me/session-tasks", {"sessions": sessions}, token=machine["token"])["sessions"]
    assert asked() == [{**mine, "status": "open"}]
    post(live, f"live-meetings/{mid}/control", {"action": "end"})
    assert asked() == [{**mine, "status": "done"}]


def test_a_runner_without_private_lanes_never_takes_a_private_meetings_turn(live):
    """An older runner would run a private meeting's turn in the bot's own checkout with write tokens: the server does
    not hand it one. The turn waits, the meeting says once why, and the runner takes it after its update."""
    from backend.execution import queued_reasons
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"], capabilities=["task_lanes_v2"])
    private = post(live, "live-meetings", {"title": "Private", "visibility": "private", "cooldown_seconds": 0})
    attach(live, private["id"], "ops")
    chunk(live, private["id"], 1, 0, 1000, "Ops, the press embargo lifts Nov 17 9am PT. Got it?")
    chunk(live, private["id"], 2, 1000, 2000, "Ops, and the launch price is $40?")
    assert post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None
    with live.app.state.store.read() as c:
        reasons = [why for _, why in queued_reasons(c, live.app.state.store.settings, machine["runner_id"], "ops")]
        waiting = [json.loads(r["payload"]) for r in c.execute(
            "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.bot_turn_waiting'",
            (private["id"],))]
    assert reasons and set(reasons) == {"private_update"}
    assert [(w["bot"], w["reason"], w["detail"]) for w in waiting] == [
        ("ops", "runner_update", "Test Mac needs a Tico update before it can join private meetings")], "said once"
    notices = get(live, f"live-meetings/{private['id']}")["notices"]
    assert [(n["kind"], n["bot"], n.get("detail") or n.get("reason")) for n in notices] == [
        ("waiting", "ops", "Test Mac needs a Tico update before it can join private meetings"),
        ("deferred", "ops", "replying_in_meeting")], "the view says it too, oldest first"
    team = post(live, "live-meetings", {"title": "Team", "visibility": "team", "cooldown_seconds": 0})
    attach(live, team["id"], "ops")
    chunk(live, team["id"], 1, 0, 1000, "Ops, the standup moved to 10. Got it?")
    attempt = claimed_turn(live, machine)
    assert attempt["lane"] == "main" and "standup" in attempt["message"]["body"], "a team meeting's turn still goes to it"
    post(live, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Got it.", "last_seq": 0},
         token=machine["token"])
    ready(live, machine, ["ops"], ["task_lanes_v2", "private_lanes_v1"])
    attempt = claimed_turn(live, machine)
    assert attempt["lane"] == "private:" + attempt["conversation"]["id"]
    post(live, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Got it.", "last_seq": 0},
         token=machine["token"])


def test_a_private_turn_with_no_copy_of_the_repository_ends_and_the_meeting_says_why_once(live):
    """runner-rs sessions::PRIVATE_COPY_FAILED: a computer that can make no private copy of the bot's repository would
    fail every retry alike, so the turn ends, the job is not resumed, and the meeting gets one plain notice."""
    machine = runner(live)
    assign(live, machine, "ops")
    ready(live, machine, ["ops"])
    meeting = post(live, "live-meetings", {"title": "Private", "visibility": "private", "cooldown_seconds": 0})
    mid = meeting["id"]
    attach(live, mid, "ops")

    def fails(text):
        chunk(live, mid, len(get(live, f"live-meetings/{mid}")["router"]["turns"]) + 1, 0, 1000, text)
        attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
        error = ("A private meeting's turn runs in a copy of the bot's repository kept for that meeting alone, "
                 "and none could be made here (could not add the task's worktree: fatal: invalid reference)")
        post(live, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "diagnostic", "payload": {
            "runner_error": error, "error_type": "RuntimeError", "code": "private_checkout"}}]}, token=machine["token"])
        post(live, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "text": "", "last_seq": 1},
             token=machine["token"])
        return attempt

    first = fails("Ops, the press embargo lifts Nov 17 9am PT. Got it?")
    with live.app.state.store.transaction() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0] == "canceled"
        live.app.state.execution.auto_reconcile(c)
        assert c.execute("SELECT state FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0] == "canceled"
    assert post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None, "not resumed"
    turns = get(live, f"live-meetings/{mid}")["router"]["turns"]
    assert [(t["status"], t["skip_reason"]) for t in turns] == [("skipped", "bot_execution_private_checkout")]
    second = fails("Ops, and the launch price is $40?")
    assert second["job_id"] != first["job_id"]
    with live.app.state.store.read() as c:
        said = [json.loads(r[0]) for r in c.execute(
            "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.bot_turn_failed'", (mid,))]
    assert [(s["bot"], s["reason"], s["detail"]) for s in said] == [(
        "ops", "private_checkout",
        "ops can't answer in this private meeting: Test Mac couldn't make a private copy of its repository")], "said once"


def test_the_meeting_view_says_the_model_its_replies_are_set_to_now(live, monkeypatch):
    plain = connect(live)
    assert (plain["meeting_model"], plain["meeting_model_effective"]) == (None, "")
    gateway_on(live, monkeypatch)
    assert get(live, f"live-meetings/{plain['id']}")["meeting_model_effective"] == MEETING
    own = post(live, "live-meetings", {"title": "Own", "meeting_model": ""})
    assert (own["meeting_model"], own["meeting_model_effective"]) == ("", "")
