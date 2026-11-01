"""Tico Meet against a real LiveKit server. A call is created, a person joins with the token Tico gives them and
publishes a generated tone on a microphone track through LiveKit's own SDK, the server's recorder hears it, and the
transcript is posted into the thread. Only the Gateway's speech-to-text relay is faked. The test runs only where
TICO_LIVE_LIVEKIT names a running server with its development keys; CI starts one with docker (docs/meet.md)."""
import asyncio
import base64
import io
import json
import math
import os
import wave
from array import array

import pytest

from backend import call_media, calls
from backend.tests.test_call_media import wait_until
from backend.tests.test_calls import SYNTHETIC_SPEECH, _restricted, join, post, start_call, thread, thread_texts
from backend.tests.test_live_meetings import live  # noqa: F401
from clients import gateway

LIVE = os.environ.get("TICO_LIVE_LIVEKIT", "").rstrip("/")
GATEWAY = "https://gateway.acme.example/v1"
# LiveKit's development keys, the public defaults of a local --dev server; they are not this install's secrets.
CONFIG = call_media.MeetConfig(url=call_media._ws_url(LIVE), internal_url=LIVE,
                               api_key="devkey", api_secret="secret")
SPEECH_SECONDS = 4
HEARD_BYTES = 3 * call_media.SAMPLE_RATE * 2     # three of the four seconds spoken, 16-bit mono


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
    """A person's microphone, published through LiveKit's SDK: a 440 Hz tone in 20 ms frames, in real time."""
    from livekit import rtc

    async def publish():
        room = rtc.Room()
        await room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        source = rtc.AudioSource(call_media.SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        tone = array("h", (round(8000 * math.sin(2 * math.pi * 440 * n / call_media.SAMPLE_RATE))
                           for n in range(call_media.SAMPLE_RATE * seconds)))
        frame = call_media.SAMPLE_RATE // 50
        for start in range(0, len(tone), frame):
            chunk = tone[start:start + frame]
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
        heard.append(len(pcm))          # what the recorder got from the server, before any batching
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
