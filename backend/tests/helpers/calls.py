"""Helpers that other tests share, moved out of test_calls.py so they import no test file."""

import pytest
from backend import call_media, gateway_connection
from backend.store import H
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.call_media import CONFIG


PCM_ONE_SECOND = b"\x01\x00" * 16000


PCM_EIGHT_SECONDS = PCM_ONE_SECOND * 8


SYNTHETIC_SPEECH = "The rollout needs a reliability review"


class FakeMedia:
    """A stand-in for LiveKit: rooms are sets of identities, and the recorder is a callback the test feeds."""

    url = "wss://meet.acme.example"

    def __init__(self):
        self.rooms, self.removed, self.recorders, self.ended = {}, [], {}, []
        self.down = False  # LiveKit unreachable: every call to it is refused until the test clears this

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
    monkeypatch.setattr(
        gateway_connection,
        "target",
        lambda c, vault, settings=None: ("synthetic-gateway-key", "https://gateway.acme.example/v1", "x"),
    )
    live.app.state.live_meeting_decider = lambda state, questions, label: {
        "model": "synthetic-decider",
        "ms": 1,
        "answers": {key: {"noul": 0.99, "confidence": 0.95} for key in questions},
    }
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


def _restricted(api, prefix):
    with api.app.state.store.read() as c:
        return [
            row["restricted"]
            for row in c.execute(
                "SELECT restricted FROM messages WHERE body LIKE ? ORDER BY created_at", (prefix + "%",)
            )
        ]
