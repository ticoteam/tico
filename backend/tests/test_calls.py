"""Tico Meet calls: synthetic speech becomes a transcript and a bot reply in the thread, and access to a call follows the
thread. LiveKit and the Gateway are fakes here, and every name is synthetic (Acme, acme.example)."""
import json

import pytest

from backend import call_media, calls, events, gateway_connection, live_privacy, teammate_access, threads
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, assign, get, headers, post, runner  # noqa: F401
from backend.tests.test_call_media import CONFIG, decode, fake_rtc, wait_until  # noqa: F401
from backend.tests.test_live_meetings import live, settle  # noqa: F401
from clients import gateway

PCM_ONE_SECOND = b"\x01\x00" * 16000
PCM_EIGHT_SECONDS = PCM_ONE_SECOND * 8
SYNTHETIC_SPEECH = "The rollout needs a reliability review"


class FakeMedia:
    """A stand-in for LiveKit: rooms are sets of identities, and the recorder is a callback the test feeds."""
    url = "wss://meet.acme.example"

    def __init__(self):
        self.rooms, self.removed, self.recorders, self.ended = {}, [], {}, []
        self.down = False            # LiveKit unreachable: every call to it is refused until the test clears this

    def _reachable(self):
        if self.down:
            raise call_media.MediaUnavailable("the media server is down")

    def person_token(self, room, identity, name):
        return call_media.person_token(CONFIG, room, identity, name)

    def room_exists(self, room):
        self._reachable()
        return room in self.rooms

    def remove(self, room, identity):
        self._reachable()
        self.removed.append((room, identity))
        self.rooms.get(room, set()).discard(identity)

    def end_room(self, room):
        self._reachable()
        self.rooms.pop(room, None)
        self.ended.append(room)

    def start_recorder(self, call_id, room, on_audio):
        self.recorders[call_id] = on_audio
        self.rooms.setdefault(room, set()).add("tico-recorder")

    def stop_recorder(self, call_id):
        self.recorders.pop(call_id, None)

    def running(self, call_id):
        return call_id in self.recorders


@pytest.fixture
def meet(live, monkeypatch):
    live.app.state.call_media = FakeMedia()
    monkeypatch.setattr(call_media, "transcribe", lambda base, key, audio: SYNTHETIC_SPEECH)
    monkeypatch.setattr(gateway_connection, "target",
                        lambda c, vault, settings=None: ("synthetic-gateway-key", "https://gateway.acme.example/v1", "x"))
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider", "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions}}
    return live


@pytest.fixture
def live_media(meet, fake_rtc, monkeypatch):
    """The real LiveKit adapter: the recorder really joins the fake server and hears what the test publishes. Only
    the media service (plain HTTP to LiveKit) is replaced, so the rest of the call code runs as in production."""
    media = call_media.LiveKitMedia(CONFIG)

    def twirp(self, method, payload, room=""):
        if method == "ListRooms":
            return {"rooms": [{"name": name} for name in payload["names"] if fake_rtc.rooms.get(name) is not None]}
        if method == "RemoveParticipant":
            fake_rtc.rooms.get(payload["room"], set()).discard(payload["identity"])
            return {}
        if method == "DeleteRoom":
            fake_rtc.rooms.pop(payload["room"], None)
            return {}
        raise AssertionError("unexpected room service call: " + method)

    monkeypatch.setattr(call_media.LiveKitMedia, "_twirp", twirp)
    meet.app.state.call_media = media
    return media


def drafted(items):
    """The Gateway's chat answer when the model drafts these action items."""
    content = json.dumps({"items": items})
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def thread(api, subject="Acme launch"):
    """A thread between Ana (the owner, who acts), Ben and the Ops bot."""
    with api.app.state.store.transaction() as c:
        return H.open_conversation(c, "human:ana", ["human:ben", "bot:ops"], kind="chat", subject=subject)["id"]


def start_call(api, cid, token="ana-test", title="Acme launch call"):
    return post(api, f"threads/{cid}/calls", {"title": title}, token=token)


def join(api, call_id, token="ana-test"):
    return post(api, f"calls/{call_id}/join", {}, token=token)


def private_lanes_ready(api, machine, bot):
    """A current runner that takes a private meeting's turns: a call is a private meeting (execution.PRIVATE_LANES)."""
    return post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                "capabilities": ["private_lanes_v1"], "readiness": {bot: True}}, token=machine["token"])


def bot_reply_by_run(api, bot, text):
    """The bot answers its meeting turn the way the runner does: the job is claimed, started and completed with its reply."""
    machine = runner(api)
    assign(api, machine, bot)
    private_lanes_ready(api, machine, bot)
    attempt = post(api, "jobs/claim", {"bot": bot}, token=machine["token"])["attempt"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-call-turn"}, token=machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": text, "last_seq": 0},
         token=machine["token"])
    return attempt


def thread_texts(api, cid, token="ana-test"):
    response = api.get(f"/api/v2/conversations/{cid}/messages", headers=headers(token))
    assert response.status_code == 200, response.text
    return [message["body"] for message in response.json()["messages"]]


def test_synthetic_speech_becomes_a_transcript_and_a_bot_reply_in_the_thread(meet):
    cid = thread(meet)
    call = start_call(meet, cid)
    call_id, meeting_id = call["id"], call["meeting_id"]
    join(meet, call_id, "ana-test")
    join(meet, call_id, "ben-test")
    post(meet, f"calls/{call_id}/bots", {"bots": ["ops"]}, token="ana-test")
    for index in range(4):
        meet.app.state.calls.ingest(call_id, "human:ana", PCM_EIGHT_SECONDS, index * 8000)
    meet.app.state.calls.drain(call_id)
    settle(meet)
    with meet.app.state.store.read() as c:
        transcript = [row["text"] for row in c.execute(
            "SELECT text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq", (meeting_id,))]
    assert transcript == [SYNTHETIC_SPEECH] * 4

    bot_reply_by_run(meet, "ops", "Ops can take the review.")
    assert any("Ops can take the review." in text for text in thread_texts(meet, cid))

    post(meet, f"calls/{call_id}/end", {}, token="ana-test")
    texts = thread_texts(meet, cid)
    transcripts = [text for text in texts if text.startswith("Call transcript")]
    assert len(transcripts) == 1 and SYNTHETIC_SPEECH in transcripts[0]
    assert get(meet, f"calls/{call_id}")["state"] == "ended"


def test_the_recorder_hears_the_room_and_the_thread_gets_the_transcript_a_reply_and_the_action_items(
        meet, live_media, fake_rtc, monkeypatch):
    cid = thread(meet)
    call = start_call(meet, cid)
    call_id, meeting_id = call["id"], call["meeting_id"]
    room, recorder = "tico-call-" + call_id, call_media.RECORDER_PREFIX + call_id
    assert fake_rtc.rooms[room] == {recorder}
    join(meet, call_id, "ana-test")
    join(meet, call_id, "ben-test")
    post(meet, f"calls/{call_id}/bots", {"bots": ["ops"]}, token="ana-test")
    fake_rtc.hear(live_media.loop, recorder, "human:ana", [PCM_ONE_SECOND] * 32)     # four 8-second clips
    wait_until(lambda: len(_chunks(meet, meeting_id)) == 4)
    settle(meet)
    assert _chunks(meet, meeting_id) == [SYNTHETIC_SPEECH] * 4

    bot_reply_by_run(meet, "ops", "Ops can take the review.")

    monkeypatch.setattr(gateway, "relay", lambda method, url, body=None, headers=None, timeout=None: (200, drafted([
        {"title": "Schedule the reliability review with Ops", "quote": SYNTHETIC_SPEECH},
        {"title": "Invented task", "quote": "Nobody said this on the call"}])))
    post(meet, f"calls/{call_id}/end", {}, token="ana-test")

    texts = thread_texts(meet, cid)
    transcripts = [text for text in texts if text.startswith("Call transcript")]
    assert len(transcripts) == 1 and SYNTHETIC_SPEECH in transcripts[0]
    assert "Ops can take the review." in texts
    assert [text for text in texts if text.startswith("Action items:")] == [
        "Action items: Acme launch call\n- Schedule the reliability review with Ops"]
    assert _restricted(meet, "Call transcript") == [1] and _restricted(meet, "Action items") == [1]
    assert room not in fake_rtc.rooms and not live_media.running(call_id)
    assert get(meet, f"calls/{call_id}")["state"] == "ended"


def test_a_gateway_that_does_not_answer_keeps_the_transcript_and_posts_no_action_items(meet, monkeypatch):
    monkeypatch.setattr(gateway, "relay", lambda *args, **kwargs: (0, b""))
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ana-test")
    meet.app.state.calls.ingest(call_id, "human:ana", PCM_EIGHT_SECONDS, 0)
    settle(meet)
    post(meet, f"calls/{call_id}/end", {}, token="ana-test")
    texts = thread_texts(meet, cid)
    assert any(text.startswith("Call transcript") and SYNTHETIC_SPEECH in text for text in texts)
    assert not any(text.startswith("Action items:") for text in texts)


def test_a_person_outside_a_members_thread_cannot_see_or_join_its_call(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    response = meet.post(f"/api/v2/calls/{call_id}/join", json={}, headers=headers("cara-test"))
    assert response.status_code == 404
    assert meet.get(f"/api/v2/calls/{call_id}", headers=headers("cara-test")).status_code == 404
    assert meet.get(f"/api/v2/threads/{cid}/calls", headers=headers("cara-test")).status_code in (403, 404)


def test_no_join_or_refresh_token_grants_a_camera(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    grants = []
    for token in ("ana-test", "ben-test"):
        response = join(meet, call_id, token)
        grants.append(decode(response["token"], CONFIG.api_secret)["video"])
        refreshed = post(meet, f"calls/{call_id}/token", {}, token=token)
        grants.append(decode(refreshed["token"], CONFIG.api_secret)["video"])
    for grant in grants:
        assert "camera" not in grant["canPublishSources"]
        assert set(grant["canPublishSources"]) <= set(call_media.AUDIO_SOURCES)
        assert grant["canPublishData"] is False


def test_losing_the_thread_removes_the_person_and_refuses_their_refresh(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ana-test")
    join(meet, call_id, "ben-test")
    meet.app.state.calls.sweep()
    room = "tico-call-" + call_id
    assert meet.app.state.call_media.removed == []

    with meet.app.state.store.transaction() as c:
        owner = meet.app.state.calls.auth.identity_for_actor(c, "human:ana")
        threads.remove_member(c, owner, cid, "human:ben")
    meet.app.state.calls.sweep()

    assert meet.app.state.call_media.removed == [(room, "human:ben")]
    refused = meet.post(f"/api/v2/calls/{call_id}/token", json={}, headers=headers("ben-test"))
    assert refused.status_code == 403
    assert post(meet, f"calls/{call_id}/token", {}, token="ana-test")["token"]
    ben = [p for p in get(meet, f"calls/{call_id}")["participants"] if p["actor"] == "human:ben"][0]
    assert ben["removed_at"]
    assert any(row["kind"] == "call.participant_removed" for row in _changes(meet))


def test_a_media_server_restart_ends_the_call_and_keeps_its_transcript(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ana-test")
    meet.app.state.calls.sweep()
    meet.app.state.calls.ingest(call_id, "human:ana", PCM_EIGHT_SECONDS, 0)
    settle(meet)
    meet.app.state.call_media.rooms.clear()
    meet.app.state.calls.sweep()

    detail = get(meet, f"calls/{call_id}")
    assert detail["state"] == "ended" and detail["end_reason"] == "media_lost"
    assert any(text.startswith("Call transcript") and SYNTHETIC_SPEECH in text for text in thread_texts(meet, cid))


def test_speech_is_sent_on_only_when_a_call_asks_for_spoken_replies(meet):
    cid = thread(meet)
    quiet = start_call(meet, cid)
    assert quiet["speak_replies"] is False
    post(meet, f"calls/{quiet['id']}/end", {}, token="ana-test")
    spoken = post(meet, f"threads/{cid}/calls", {"title": "Acme call", "speak_replies": True}, token="ana-test")
    assert spoken["speak_replies"] is True and get(meet, f"calls/{spoken['id']}")["speak_replies"] is True


def test_the_call_list_pages_and_stays_under_its_byte_cap(meet, monkeypatch):
    cid = thread(meet)
    ids = []
    for index in range(3):
        call_id = start_call(meet, cid, title=f"Acme call {index}")["id"]
        post(meet, f"calls/{call_id}/end", {}, token="ana-test")
        ids.append(call_id)
    monkeypatch.setattr(calls, "CALL_LIST_BYTES", 1)     # every call is over the cap, so each page holds one call
    seen, before = [], ""
    for _ in range(5):
        response = meet.get(f"/api/v2/threads/{cid}/calls", params={"limit": 10, "before": before},
                            headers=headers("ana-test"))
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["calls"]) == 1
        seen += [row["id"] for row in page["calls"]]
        before = page["next_before"]
        if before is None:
            break
    assert sorted(seen) == sorted(ids)


def test_a_call_change_reaches_the_threads_topic_of_members_only(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    ben = _threads_events(meet, "human:ben")
    started = [event for event in ben if event["kind"] == "call.started" and event["call_id"] == call_id]
    assert len(started) == 1 and started[0]["thread_id"] == cid       # one event per change, not one per handler
    assert not [event for event in _threads_events(meet, "human:cara") if event.get("call_id") == call_id]


def test_a_tico_restart_brings_the_recorder_back_from_the_database(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ana-test")
    meet.app.state.call_media.recorders.pop(call_id)     # the process restarted and lost its recorder
    meet.app.state.calls.sweep()
    assert meet.app.state.call_media.running(call_id)
    assert get(meet, f"calls/{call_id}")["state"] == "live"


def test_calls_are_off_without_the_media_server(meet):
    meet.app.state.call_media = None
    cid = thread(meet)
    response = meet.post(f"/api/v2/threads/{cid}/calls", json={"title": "Acme call"}, headers=headers("ana-test"))
    assert response.status_code == 503
    assert json.loads(response.text)["error"]["code"] == "meet_unavailable"


def _changes(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT kind,subject_id FROM changes WHERE topic='threads'")]


def _chunks(api, meeting_id):
    with api.app.state.store.read() as c:
        return [row["text"] for row in c.execute(
            "SELECT text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq", (meeting_id,))]


def _restricted(api, prefix):
    with api.app.state.store.read() as c:
        return [row["restricted"] for row in c.execute(
            "SELECT restricted FROM messages WHERE body LIKE ? ORDER BY created", (prefix + "%",))]


def _threads_events(api, actor):
    """The `threads` topic events `actor` is sent, as the events stream reads them."""
    auth = api.app.state.calls.auth
    with api.app.state.store.read() as c:
        sent, _cursor, _reset, _more = events.read(c, auth, auth.identity_for_actor(c, actor), 0, topics=("threads",))
    return [data for _seq, topic, data in sent if topic == "threads"]


class HubClient:
    """The `api` the hub tools run against (clients/hubtools.py), over the test server as one person."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _send(self, method, path, body=None, query=None, key=None):
        response = self.api.request(method, "/api/v2/" + path, params=query or None, json=body,
                                    headers=headers(self.token, key))
        assert response.status_code < 300, response.text
        return response.json()

    def get(self, path, **query):
        return self._send("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self._send("POST", path, body=body, key=key)

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        return self._send(method, path, body=body, key=key, query=query)


def test_the_call_tools_run_the_same_routes_the_rest_api_has(meet):
    from clients import hubtools

    ana = HubClient(meet, "ana-test")

    def tool(name, args):
        return hubtools.BY_NAME[name]["fn"](ana, args)

    cid = thread(meet)
    call_id = tool("hub_call_start", {"thread_id": cid, "title": "Acme standup", "operation_id": "op-start"})["id"]
    with pytest.raises(AssertionError):                  # one live call per thread: the second start is refused
        tool("hub_call_start", {"thread_id": cid, "operation_id": "op-start-again"})
    assert [row["id"] for row in tool("hub_call_list", {"thread_id": cid})["calls"]] == [call_id]
    assert tool("hub_call_show", {"call_id": call_id})["state"] == "live"
    joined = tool("hub_call_join", {"call_id": call_id, "operation_id": "op-join"})
    assert joined["url"] == meet.app.state.call_media.url and joined["token"] and "room" not in joined
    assert tool("hub_call_token", {"call_id": call_id})["token"]
    tool("hub_call_bot_add", {"call_id": call_id, "bots": ["ops"], "operation_id": "op-bot-add"})
    assert tool("hub_call_show", {"call_id": call_id})["bots"] == ["ops"]
    tool("hub_call_bot_remove", {"call_id": call_id, "bot": "ops", "operation_id": "op-bot-remove"})
    assert tool("hub_call_show", {"call_id": call_id})["bots"] == []
    tool("hub_call_leave", {"call_id": call_id, "operation_id": "op-leave"})
    assert tool("hub_call_end", {"call_id": call_id, "operation_id": "op-end"})["state"] == "ended"


def test_the_declared_call_answers_match_the_live_ones(meet):
    from backend.tests.test_openapi_v2 import conforms

    document = meet.get("/api/v2/openapi.json", headers=headers("ana-test")).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def send(method, path, operation, body=None):
        response = meet.request(method, path, json=body, headers=headers("ana-test"))
        assert response.status_code == 200, (path, response.text)
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(response.json(), schema, document) is None, (operation, response.text[:300])
        return response.json()

    cid = thread(meet)
    call_id = send("POST", f"/api/v2/threads/{cid}/calls", "startCall", {"title": "Acme standup"})["id"]
    assert send("GET", f"/api/v2/threads/{cid}/calls", "listCalls")["calls"]
    send("GET", f"/api/v2/calls/{call_id}", "getCall")
    send("POST", f"/api/v2/calls/{call_id}/join", "joinCall", {})
    send("POST", f"/api/v2/calls/{call_id}/token", "refreshCallToken")
    send("POST", f"/api/v2/calls/{call_id}/bots", "attachCallBots", {"bots": ["ops"]})
    send("DELETE", f"/api/v2/calls/{call_id}/bots/ops", "detachCallBot", {})
    send("POST", f"/api/v2/calls/{call_id}/leave", "leaveCall", {})
    send("POST", f"/api/v2/calls/{call_id}/end", "endCall", {})


def _remove_from_thread(meet, cid, actor):
    with meet.app.state.store.transaction() as c:
        owner = meet.app.state.calls.auth.identity_for_actor(c, "human:ana")
        threads.remove_member(c, owner, cid, actor)


def test_a_cached_join_is_refused_once_the_person_lost_the_thread(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    assert post(meet, f"calls/{call_id}/join", {}, token="ben-test", key="ben-join-once")["token"]
    _remove_from_thread(meet, cid, "human:ben")
    # The same request replayed from the cache must not hand out a token: authorization runs before the replay.
    post(meet, f"calls/{call_id}/join", {}, token="ben-test", key="ben-join-once", expected=404)


def test_leaving_drops_the_person_from_the_room_and_a_refused_drop_is_retried(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    room = "tico-call-" + call_id
    media = meet.app.state.call_media
    join(meet, call_id, "ben-test")
    media.rooms[room].add("human:ben")
    media.down = True
    post(meet, f"calls/{call_id}/leave", {}, token="ben-test")
    assert media.removed == []                      # the drop was refused, so it stays pending
    media.down = False
    meet.app.state.calls.sweep()                    # the sweep keeps the removal until LiveKit confirms it
    assert media.removed == [(room, "human:ben")] and "human:ben" not in media.rooms[room]
    assert [p for p in get(meet, f"calls/{call_id}")["participants"] if p["actor"] == "human:ben"][0]["left_at"]


def test_leaving_a_call_you_can_no_longer_read_is_a_bare_acknowledgement(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ben-test")
    _remove_from_thread(meet, cid, "human:ben")
    assert post(meet, f"calls/{call_id}/leave", {}, token="ben-test") == {"id": call_id, "left": True}
    post(meet, f"calls/{call_id}/leave", {}, token="cara-test", expected=404)


def test_a_media_restart_before_the_first_sweep_ends_the_call(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    media = meet.app.state.call_media
    assert media.running(call_id)                   # the recorder started, so the call's media has been seen
    media.rooms.pop("tico-call-" + call_id)         # LiveKit restarted and lost the room; no sweep has run since
    meet.app.state.calls.sweep()
    ended = get(meet, f"calls/{call_id}")
    assert ended["state"] == "ended" and ended["end_reason"] == "media_lost"


def test_a_call_whose_room_will_not_close_is_finished_once_the_sweep_can_reach_the_media_server(meet):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    media = meet.app.state.call_media
    media.down = True
    post(meet, f"calls/{call_id}/end", {}, token="ana-test")
    assert not [t for t in thread_texts(meet, cid) if t.startswith("Call transcript")]
    media.down = False
    meet.app.state.calls.sweep()
    meet.app.state.calls.sweep()                    # a second pass posts nothing more
    transcripts = [t for t in thread_texts(meet, cid) if t.startswith("Call transcript")]
    assert len(transcripts) == 1
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT finalized_at FROM calls WHERE id=?", (call_id,)).fetchone()[0]


def test_the_legacy_meeting_controls_refuse_a_call_meeting(meet):
    cid = thread(meet)
    call = start_call(meet, cid)
    post(meet, f"live-meetings/{call['meeting_id']}/control", {"action": "end"}, token="ana-test", expected=409)
    assert get(meet, f"calls/{call['id']}")["state"] == "live"


def test_a_bot_attached_by_someone_who_loses_the_thread_leaves_the_call_and_its_open_turns(meet):
    cid = thread(meet)
    call = start_call(meet, cid)
    call_id, meeting_id = call["id"], call["meeting_id"]
    join(meet, call_id, "ben-test")
    post(meet, f"calls/{call_id}/bots", {"bots": ["ops"]}, token="ben-test")
    meet.app.state.calls.ingest(call_id, "human:ben", PCM_EIGHT_SECONDS, 0)
    meet.app.state.calls.drain(call_id)
    settle(meet)
    assert get(meet, f"calls/{call_id}")["bots"] == ["ops"]
    _remove_from_thread(meet, cid, "human:ben")
    meet.app.state.calls.sweep()
    assert get(meet, f"calls/{call_id}")["bots"] == []
    with meet.app.state.store.read() as c:
        open_turns = c.execute("SELECT COUNT(*) FROM live_meeting_turns WHERE meeting_id=? AND bot='ops' "
                               "AND status IN ('pending','claimed')", (meeting_id,)).fetchone()[0]
    assert open_turns == 0


def test_the_transcript_names_its_meeting_so_the_meetings_rules_decide_who_reads_it(meet):
    cid = thread(meet)
    call = start_call(meet, cid)
    join(meet, call["id"], "ben-test")
    meet.app.state.calls.ingest(call["id"], "human:ben", PCM_EIGHT_SECONDS, 0)
    post(meet, f"calls/{call['id']}/end", {}, token="ana-test")
    with meet.app.state.store.read() as c:
        row = c.execute("SELECT id,refs_json FROM messages WHERE body LIKE 'Call transcript%'").fetchone()
        refs = json.loads(row["refs_json"])
        assert live_privacy.sources(c).references(refs) == {call["meeting_id"]}
        unattached_bot = threads.viewer(meet.app.state.calls.auth, Identity("bot:ops", "bot"))
        assert not threads.message_readable(c, unattached_bot, H.message(c, row["id"]))


def test_a_call_shows_only_the_people_the_viewer_may_see(meet, monkeypatch):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ben-test")
    real = teammate_access.levels_for

    def hidden(c, viewer, target, auth=None):
        levels = real(c, viewer, target, auth)
        return dict(levels, see=False) if target == "human:ben" else levels

    monkeypatch.setattr(teammate_access, "levels_for", hidden)
    people = [p["actor"] for p in get(meet, f"calls/{call_id}")["participants"]]
    assert people == ["human:ana"]
