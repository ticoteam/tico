"""Tico Meet against a real LiveKit server. A call is created, a person joins with the token Tico gives them and
publishes a generated tone on a microphone track through LiveKit's own SDK, the server's recorder hears it, and the
transcript is posted into the thread; and the call's Tico voice, a participant of its own, speaks into the room and a
person hears it. Only the Gateway's relays are faked. The test runs only where
TICO_LIVE_LIVEKIT names a running server with its development keys; no CI job starts one (docs/meet.md)."""

import asyncio
import base64
import io
import json
import math
import os
import threading
import time
import wave
from array import array

import httpx
import pytest

from backend import call_media, calls
from backend.store import H
from backend.tests.helpers.api import assign, runner
from backend.tests.helpers.call_media import wait_until
from backend.tests.helpers.calls import (
    SYNTHETIC_SPEECH,
    _restricted,
    join,
    post,
    private_lanes_ready,
    speech_response,
    start_call,
    thread,
    thread_texts,
)
from backend.tests.helpers.live_meetings import live  # noqa: F401
from clients import gateway

LIVE = os.environ.get("TICO_LIVE_LIVEKIT", "").rstrip("/")
GATEWAY = "https://gateway.acme.example/v1"
# LiveKit's development keys, the public defaults of a local --dev server; they are not this install's secrets.
CONFIG = call_media.MeetConfig(url=call_media._ws_url(LIVE), internal_url=LIVE, api_key="devkey", api_secret="secret")
SPEECH_SECONDS = 4
HEARD_BYTES = 3 * call_media.SAMPLE_RATE * 2  # three of the four seconds spoken, 16-bit mono


@pytest.fixture
def real_media(live, monkeypatch):
    """Tico with the real LiveKit adapter and the Gateway's settings, so only the speech-to-text relay is faked."""
    settings = live.app.state.store.settings
    monkeypatch.setattr(settings, "gateway_key", "synthetic-gateway-key", raising=False)
    monkeypatch.setattr(settings, "gateway_url", GATEWAY, raising=False)
    media = call_media.LiveKitMedia(CONFIG)
    live.app.state.call_media = media
    return media


def speak(url, token, seconds):
    """A person's microphone, published through LiveKit's SDK: a 440 Hz tone in 20 ms frames, in real time, then a
    second of silence, the pause that ends an utterance."""
    from livekit import rtc

    async def publish():
        room = rtc.Room()
        await room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        source = rtc.AudioSource(call_media.SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        tone = array(
            "h",
            (
                round(8000 * math.sin(2 * math.pi * 440 * n / call_media.SAMPLE_RATE))
                for n in range(call_media.SAMPLE_RATE * seconds)
            ),
        ) + array("h", bytes(call_media.SAMPLE_RATE * 2))
        frame = call_media.SAMPLE_RATE // 50
        for start in range(0, len(tone), frame):
            chunk = tone[start : start + frame]
            await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), call_media.SAMPLE_RATE, 1, frame))
        await source.wait_for_playout()
        await room.disconnect()

    asyncio.run(publish())


def loud(clip):
    """Whether a WAV clip the recorder sent holds the tone, not silence."""
    with wave.open(io.BytesIO(clip)) as reader:
        samples = array("h", reader.readframes(reader.getnframes()))
    return max(abs(sample) for sample in samples) > 2000


@pytest.mark.skipif(not LIVE, reason="needs a LiveKit server: set TICO_LIVE_LIVEKIT")
def test_a_speaker_on_a_real_livekit_server_becomes_a_transcript_in_the_thread(live, real_media, monkeypatch):
    clips, heard = [], []

    def relay(method, url, body=None, headers=None, timeout=None):
        # The one fake. The Gateway's speech-to-text answers with the words; drafting action items is not answered.
        if url.endswith("/audio/transcriptions"):
            clips.append(base64.b64decode(json.loads(body)["input_audio"]["data"]))
            return 200, json.dumps({"text": SYNTHETIC_SPEECH}).encode()
        return 503, b"{}"

    monkeypatch.setattr(gateway, "relay", relay)
    ingest = calls.Calls.ingest

    def counting(self, call_id, identity, pcm, start_ms):
        heard.append(len(pcm))  # what the recorder got from the server, before any batching
        ingest(self, call_id, identity, pcm, start_ms)

    monkeypatch.setattr(calls.Calls, "ingest", counting)

    cid = thread(live)
    call = start_call(live, cid)
    call_id = call["id"]
    room = "tico-call-" + call_id
    try:
        assert real_media.room_exists(room), "the recorder did not join the room on the server"
        assert real_media.running(call_id)
        joined = join(live, call_id, "ana-test")
        assert joined["url"] == CONFIG.url and joined["token"]
        speak(joined["url"], joined["token"], SPEECH_SECONDS)
        wait_until(lambda: sum(heard) >= HEARD_BYTES, seconds=20)
        post(live, f"calls/{call_id}/end", {}, token="ana-test")
        wait_until(lambda: any(text.startswith("Call transcript") for text in thread_texts(live, cid)), seconds=30)
    finally:
        real_media.end_room(room)

    transcripts = [text for text in thread_texts(live, cid) if text.startswith("Call transcript")]
    assert len(transcripts) == 1 and SYNTHETIC_SPEECH in transcripts[0]
    assert clips and all(loud(clip) for clip in clips)
    assert _restricted(live, "Call transcript") == [1]
    assert not real_media.room_exists(room) and not real_media.running(call_id)


def listen(url, token, joined, done, heard):
    """A person in the call, through LiveKit's SDK: the loudest sample of each frame the Tico voice sends goes in
    `heard`. `joined` is set once connected; the person leaves when `done` is set."""
    from livekit import rtc

    async def run():
        room, readers = rtc.Room(), []

        async def read(track):
            async for event in rtc.AudioStream(track, sample_rate=call_media.VOICE_RATE, num_channels=1):
                samples = array("h", bytes(event.frame.data.cast("B")))
                heard.append(max((abs(sample) for sample in samples), default=0))

        def subscribed(track, _publication, who):
            if track.kind == rtc.TrackKind.KIND_AUDIO and call_media.is_voice(who.identity):
                readers.append(asyncio.ensure_future(read(track)))

        room.on("track_subscribed", subscribed)
        await room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        joined.set()
        await asyncio.to_thread(done.wait, 60)
        for reader in readers:
            reader.cancel()
        await room.disconnect()

    asyncio.run(run())


@pytest.mark.skipif(not LIVE, reason="needs a LiveKit server: set TICO_LIVE_LIVEKIT")
def test_the_tico_voice_speaks_into_a_real_livekit_room_and_is_not_counted_as_a_person(live, real_media):
    cid = thread(live)
    call = start_call(live, cid)
    call_id, room = call["id"], "tico-call-" + call["id"]
    joined, done, heard = threading.Event(), threading.Event(), []
    try:
        ticket = join(live, call_id, "ana-test")
        person = threading.Thread(target=listen, args=(ticket["url"], ticket["token"], joined, done, heard))
        person.start()
        assert joined.wait(20)
        wait_until(lambda: real_media.participants(room) == ["human:ana"], seconds=20)  # the voice is not a person
        tone = array("h", (round(8000 * math.sin(2 * math.pi * 330 * n / 24000)) for n in range(24000)))
        started = threading.Event()
        played = real_media.say(call_id, tone.tobytes(), 24000, started.set)
        assert started.is_set() and played >= 900
        wait_until(lambda: sum(1 for peak in heard if peak > 2000) >= 25, seconds=20)  # half a second heard, loud
    finally:
        done.set()
        real_media.end_room(room)
    person.join(20)


def converse(url, token, seconds, spoke, heard, done):
    """Ana in a 1:1 call with a bot, through LiveKit's SDK: she says a tone for `seconds` and pauses; `spoke` gets the
    moment her last loud frame was sent and `heard` the moment of the first loud frame of the Tico voice she hears. She
    leaves when `done` is set."""
    from livekit import rtc

    async def run():
        room, readers = rtc.Room(), []

        async def read(track):
            async for event in rtc.AudioStream(track, sample_rate=call_media.VOICE_RATE, num_channels=1):
                samples = array("h", bytes(event.frame.data.cast("B")))
                if not heard and max((abs(sample) for sample in samples), default=0) > 2000:
                    heard.append(time.monotonic())

        def subscribed(track, _publication, who):
            if track.kind == rtc.TrackKind.KIND_AUDIO and call_media.is_voice(who.identity):
                readers.append(asyncio.ensure_future(read(track)))

        room.on("track_subscribed", subscribed)
        await room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        source = rtc.AudioSource(call_media.SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        frame = call_media.SAMPLE_RATE // 50
        tone = array(
            "h",
            (
                round(8000 * math.sin(2 * math.pi * 440 * n / call_media.SAMPLE_RATE))
                for n in range(call_media.SAMPLE_RATE * seconds)
            ),
        )
        for start in range(0, len(tone), frame):
            await source.capture_frame(
                rtc.AudioFrame(tone[start : start + frame].tobytes(), call_media.SAMPLE_RATE, 1, frame)
            )
        await source.wait_for_playout()
        spoke.append(time.monotonic())  # the pause begins: from here on she sends silence
        quiet = bytes(frame * 2)
        while not done.is_set() and len(heard) == 0:
            await source.capture_frame(rtc.AudioFrame(quiet, call_media.SAMPLE_RATE, 1, frame))
        await asyncio.to_thread(done.wait, 60)
        for reader in readers:
            reader.cancel()
        await room.disconnect()

    asyncio.run(run())


@pytest.mark.skipif(not LIVE, reason="needs a LiveKit server: set TICO_LIVE_LIVEKIT")
def test_a_question_said_in_a_one_to_one_bot_call_is_answered_aloud_and_the_wait_is_measured(
    live, real_media, monkeypatch
):
    """End of the question to the first sound of the answer, on a real LiveKit server with the Gateway faked (it
    answers at once) and the bot's run answering as soon as its turn is queued. The pieces are printed (-s)."""
    tone = array("h", (round(8000 * math.sin(2 * math.pi * 330 * n / 24000)) for n in range(24000))).tobytes()

    def relay(method, url, body=None, headers=None, timeout=None):
        if url.endswith("/audio/transcriptions"):
            return 200, json.dumps({"text": SYNTHETIC_SPEECH}).encode()
        if url.endswith("/audio"):
            return 200, json.dumps({"speech": {"voices": [{"name": "acme-alto"}]}}).encode()
        return 503, b"{}"

    monkeypatch.setattr(gateway, "relay", relay)
    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(lambda request: speech_response(tone)))
    with live.app.state.store.transaction() as c:
        cid = H.open_conversation(c, "human:ana", ["bot:ops"], kind="chat", subject="Ops")["id"]
    call = start_call(live, cid, bots=["ops"])
    call_id, meeting_id, room = call["id"], call["meeting_id"], "tico-call-" + call["id"]
    machine = runner(live)
    assign(live, machine, "ops")
    private_lanes_ready(live, machine, "ops")
    spoke, heard, done = [], [], threading.Event()
    marks = {}
    try:
        ticket = join(live, call_id, "ana-test")
        person = threading.Thread(target=converse, args=(ticket["url"], ticket["token"], 2, spoke, heard, done))
        person.start()

        def seen(name, sql):
            with live.app.state.store.read() as c:
                if name not in marks and c.execute(sql, (meeting_id,)).fetchone()[0]:
                    marks[name] = time.monotonic()
            return name in marks

        chunk_sql = "SELECT COUNT(*) FROM live_meeting_chunks WHERE meeting_id=?"
        turn_sql = "SELECT COUNT(*) FROM live_meeting_turns WHERE meeting_id=?"
        wait_until(lambda: seen("transcript", chunk_sql) and seen("turn", turn_sql), seconds=20)
        attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
        post(live, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-call-turn"}, token=machine["token"])
        post(
            live,
            f"attempts/{attempt['id']}/complete",
            {"outcome": "completed", "text": "Yes, Ops can take it.", "last_seq": 0},
            token=machine["token"],
        )
        marks["reply"] = time.monotonic()
        wait_until(lambda: bool(heard), seconds=20)
    finally:
        done.set()
        real_media.end_room(room)
    person.join(20)

    paused = spoke[0]
    pieces = {
        "pause to transcript": marks["transcript"] - paused,
        "transcript to turn": marks["turn"] - marks["transcript"],
        "turn to reply stored (the bot)": marks["reply"] - marks["turn"],
        "reply stored to first audio": heard[0] - marks["reply"],
        "end of question to first audio": heard[0] - paused,
    }
    print("\n" + "\n".join(f"{name}: {seconds * 1000:.0f} ms" for name, seconds in pieces.items()))
    assert pieces["end of question to first audio"] < 10
