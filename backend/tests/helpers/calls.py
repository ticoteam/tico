"""Helpers that other tests share, moved out of test_calls.py so they import no test file."""

import asyncio
from array import array
from contextlib import aclosing

import httpx
import pytest
from backend import call_media, call_voice, gateway_connection
from backend.store import H
from backend.tests.helpers.api import assign, headers, post, runner
from backend.tests.helpers.call_media import CONFIG


# One second of speech as the pause detection hears it (call_voice.Segmenter): a loud square wave, 16 kHz mono.
PCM_ONE_SECOND = array("h", [3000, -3000] * 8000).tobytes()


SILENCE_ONE_SECOND = bytes(32000)


# Eight seconds of speech, then the pause that ends the utterance.
PCM_EIGHT_SECONDS = PCM_ONE_SECOND * 8 + SILENCE_ONE_SECOND


# A sentence of the Gateway's speech: 24 kHz mono, a tenth of a second.
SPEECH_PCM = array("h", [2000, -2000] * 1200).tobytes()


def speech_response(pcm=SPEECH_PCM):
    return httpx.Response(200, headers={"Content-Type": "audio/pcm", "X-Audio-Sample-Rate": "24000"}, content=pcm)


SYNTHETIC_SPEECH = "The rollout needs a reliability review"


class FakeMedia:
    """A stand-in for LiveKit: rooms are sets of identities, and the recorder is a callback the test feeds."""

    url = "wss://meet.acme.example"
    speech_frames = staticmethod(call_media.speech_frames)

    def __init__(self):
        self.rooms, self.removed, self.recorders, self.ended = {}, [], {}, []
        self.said, self.hushed = [], []  # (call_id, pcm, rate) played on the voice track; calls hushed
        self.quiet = {}  # call_id -> the recorder's on_quiet, which a test calls as the media loop's timer would
        self.down = False  # LiveKit unreachable: every call to it is refused until the test clears this

    def _reachable(self):
        if self.down:
            raise call_media.MediaUnavailable("the media server is down")

    def person_token(self, room, identity, name):
        self.rooms.setdefault(room, set()).add(identity)  # the person connects with it
        return call_media.person_token(CONFIG, room, identity, name)

    def participants(self, room):
        self._reachable()
        return sorted(identity for identity in self.rooms.get(room, set()) if not call_media.is_voice(identity))

    def mark(self, call_id):
        return self.hushed.count(call_id) if call_id in self.recorders else None

    def say(self, call_id, pcm, rate, started=None, mark=None):
        if call_id not in self.recorders or mark is not None and mark != self.hushed.count(call_id):
            return 0  # hushed since the speech was cleared to play
        if started is not None:
            started()
        self.said.append((call_id, pcm, rate))
        return max(1, len(pcm) * 1000 // (rate * 2))

    def say_stream(self, call_id, frames, mark, budget, before, started):
        async def play():
            sent, current, pcm = 0, None, bytearray()

            def flush(index):
                return self.say(call_id, bytes(pcm), call_media.VOICE_RATE, lambda: started(index), mark)

            async with aclosing(frames):
                async for index, frame in frames:
                    if current != index:
                        if current is not None:
                            sent += flush(current)
                            pcm.clear()
                        if not before(index):
                            return sent, False
                        current = index
                    length = (len(pcm) + len(frame)) * 1000 // (call_media.VOICE_RATE * 2)
                    if sent + length > budget:
                        return sent + (flush(current) if pcm else 0), True
                    pcm.extend(frame)
            return sent + (flush(current) if pcm else 0), False

        return asyncio.run(play())

    def hush(self, call_id):
        self.hushed.append(call_id)

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

    def start_recorder(self, call_id, room, on_audio, on_quiet=None):
        self.recorders[call_id] = on_audio
        self.quiet[call_id] = on_quiet
        self.rooms.setdefault(room, set()).add(call_media.RECORDER_PREFIX + call_id)

    def stop_recorder(self, call_id):
        self.recorders.pop(call_id, None)

    def running(self, call_id):
        return call_id in self.recorders


@pytest.fixture
def meet(live, monkeypatch):
    live.app.state.call_media = FakeMedia()
    live.app.state.calls.voice.ticking = False  # tests tick the voice by hand (calls.voice.tick)
    # A 1:1 call asks a turn once its person is quiet: at once here, unless a test sets the real wait.
    monkeypatch.setattr(call_voice, "TURN_END_MS", 0)
    monkeypatch.setattr(call_media, "transcribe", lambda base, key, audio, **kw: SYNTHETIC_SPEECH)
    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(lambda request: speech_response()))
    monkeypatch.setattr(call_media, "voices", lambda base, key: ["acme-alto", "acme-bass"])
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


def start_call(api, cid, token="ana-test", title="Acme launch call", **fields):
    return post(api, f"threads/{cid}/calls", {"title": title, **fields}, token=token)


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


def private_lanes_ready(api, machine, bot):
    """Advertise readiness with the older runner's private-lane capability."""
    return post(
        api,
        "runners/heartbeat",
        {"version": "test", "platform": "test", "capabilities": ["private_lanes_v1"], "readiness": {bot: True}},
        token=machine["token"],
    )


def bot_reply_by_run(api, bot, text):
    """The bot answers its meeting turn the way the runner does: the job is claimed, started and completed with its reply."""
    machine = runner(api)
    assign(api, machine, bot)
    private_lanes_ready(api, machine, bot)
    attempt = post(api, "jobs/claim", {"bot": bot}, token=machine["token"])["attempt"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-call-turn"}, token=machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": text, "last_seq": 0},
        token=machine["token"],
    )
    return attempt
