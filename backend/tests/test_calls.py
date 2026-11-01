"""Tico Meet calls: synthetic speech becomes a transcript and a bot reply in the thread, and access to a call follows the
thread. LiveKit and the Gateway are fakes here, and every name is synthetic (Acme, acme.example)."""
import json

import pytest

from backend import call_media, calls, gateway_connection
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_call_media import CONFIG, decode
from backend.tests.test_live_meetings import live, settle  # noqa: F401

PCM_EIGHT_SECONDS = b"\x01\x00" * (16000 * 8)
SYNTHETIC_SPEECH = "The rollout needs a reliability review"


class FakeMedia:
    """A stand-in for LiveKit: rooms are sets of identities, and the recorder is a callback the test feeds."""
    url = "wss://meet.acme.example"

    def __init__(self):
        self.rooms, self.removed, self.recorders, self.ended = {}, [], {}, []

    def person_token(self, room, identity, name):
        return call_media.person_token(CONFIG, room, identity, name)

    def room_exists(self, room):
        return room in self.rooms

    def remove(self, room, identity):
        self.removed.append((room, identity))
        self.rooms.get(room, set()).discard(identity)

    def end_room(self, room):
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


def thread(api, subject="Acme launch"):
    """A thread between Ana (the owner, who acts), Ben and the Ops bot."""
    with api.app.state.store.transaction() as c:
        return H.open_conversation(c, "human:ana", ["human:ben", "bot:ops"], kind="chat", subject=subject)["id"]


def start_call(api, cid, token="ana-test", title="Acme launch call"):
    return post(api, f"threads/{cid}/calls", {"title": title}, token=token)


def join(api, call_id, token="ana-test"):
    return post(api, f"calls/{call_id}/join", {}, token=token)


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
    settle(meet)
    with meet.app.state.store.read() as c:
        transcript = [row["text"] for row in c.execute(
            "SELECT text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq", (meeting_id,))]
    assert transcript == [SYNTHETIC_SPEECH] * 4

    turns = get(meet, f"live-meetings/{meeting_id}/turns", token="ops-live")["turns"]
    assert turns, "the attached bot was not given a turn on the call's speech"
    turn = turns[0]
    post(meet, f"live-meetings/{meeting_id}/turns/{turn['id']}/claim", {}, token="ops-live")
    post(meet, f"live-meetings/{meeting_id}/chat", {"text": "Ops can take the review.", "turn_id": turn["id"]},
         token="ops-live")
    assert any("Ops can take the review." in text for text in thread_texts(meet, cid))

    post(meet, f"calls/{call_id}/end", {}, token="ana-test")
    texts = thread_texts(meet, cid)
    transcripts = [text for text in texts if text.startswith("Call transcript")]
    assert len(transcripts) == 1 and SYNTHETIC_SPEECH in transcripts[0]
    assert get(meet, f"calls/{call_id}")["state"] == "ended"


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


def test_losing_the_thread_removes_the_person_and_refuses_their_refresh(meet, monkeypatch):
    cid = thread(meet)
    call_id = start_call(meet, cid)["id"]
    join(meet, call_id, "ana-test")
    join(meet, call_id, "ben-test")
    meet.app.state.calls.sweep()
    room = "tico-call-" + call_id
    assert meet.app.state.call_media.removed == []

    revoked = set()
    monkeypatch.setattr(calls, "READER", lambda c, actor, thread_id: actor not in revoked)
    revoked.add("human:ben")
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
