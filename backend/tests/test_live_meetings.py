"""Live meetings use disposable SQLite fixtures and synthetic text only."""

import asyncio
import json
import time

import pytest
from starlette.requests import Request

from backend import live_meetings as live_meetings_module
from backend.auth import Identity
from backend.store import H, digest, encode
from backend.tests.test_api import api, assign, get, headers, post, ready, restrict, runner  # noqa: F401


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
    return response.json() if expected == 200 else response


def test_connect_is_explicit_team_visible_and_second_human_must_join_to_chat(live):
    meeting = connect(live, client_id="recorder-session-1")
    candidates = get(live, "live-meetings/bot-candidates")
    assert set(candidates) == {"bots"}
    assert all(set(bot) == {"slug", "name", "description", "team"} for bot in candidates["bots"])
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
                                "WHERE json_extract(m.refs_json,'$.live_meeting.meeting_id')=?",
                                (mid,)).fetchone()[0]
        assert direct_jobs == 2
        direct_job_states = c.execute("SELECT DISTINCT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                                      "WHERE json_extract(m.refs_json,'$.live_meeting.meeting_id')=?",
                                      (mid,)).fetchall()
        assert {row[0] for row in direct_job_states} == {"cancelled"}


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
            "SELECT payload_json FROM live_meeting_events WHERE meeting_id=? AND type='meeting.router'", (mid,))]
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
    post(live, f"live-meetings/{mid}/chat", {"text": "Finance, any follow-up?"})
    after_cooldown = get(live, f"live-meetings/{mid}")
    assert after_cooldown["router"]["bypasses"][-1]["skipped"] == [{"bot": "finance", "reason": "cooldown"}]
    chunk(live, mid, 2, 40_000, 41_000, "Finance, please follow up")
    after_window = get(live, f"live-meetings/{mid}")
    assert any(turn["bot"] == "finance" and turn["status"] == "pending"
               for turn in after_window["router"]["turns"])
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
                                               "WHERE json_extract(m.refs_json,'$.live_meeting.meeting_id')=?",
                                               (queued["id"],))]
        assert states == ["cancelled"]

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
        finalized_event = c.execute("SELECT payload_json FROM live_meeting_events WHERE meeting_id=? "
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
    assert json.loads(finalized_event["payload_json"]) == {
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
        event = c.execute("SELECT payload_json FROM live_meeting_events WHERE meeting_id=? "
                          "AND type='meeting.bot_left'", (mid,)).fetchone()
    assert json.loads(event["payload_json"]) == {
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
        corrected_event = c.execute("SELECT payload_json FROM live_meeting_events WHERE meeting_id=? "
                                    "AND type='meeting.chunk_corrected'", (mid,)).fetchone()
    assert json.loads(corrected_event["payload_json"]) == {
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
    assert all(set(bot) == {"slug", "name", "description", "team"} for bot in candidates["bots"])
    assert "ops" not in {bot["slug"] for bot in candidates["bots"]}
    hidden_bot = live.post("/api/v2/live-meetings/" + private_meeting["id"] + "/bots",
                           json={"bots": ["ops"]}, headers=headers("cara-test"))
    assert hidden_bot.status_code == 403


def test_attachment_requires_write_and_keeper_delivery_keeps_human_contact_policy(live):
    with live.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["cara"]}, read={"people": ["cara"]},
                 write={"people": ["ana"], "bots": ["finance"]})
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        config["bot_contact"] = "tasks"
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (encode(config),))

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
                        "WHERE json_extract(m.refs_json,'$.live_meeting.turn_id')=?", (turn_id,)).fetchone()
    assert job["state"] == "cancelled"


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
