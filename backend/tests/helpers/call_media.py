"""Helpers that other tests share, moved out of test_call_media.py so they import no test file."""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import time
import types
from types import SimpleNamespace
import pytest
from backend import call_media as media


CONFIG = media.MeetConfig(
    url="wss://meet.acme.example",
    internal_url="http://livekit:7880",
    api_key="APIacme",
    api_secret="acme-secret-0123456789abcdef0123456789abcdef",
)


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def decode(token, secret=CONFIG.api_secret):
    """The claims of a token, after checking its HS256 signature against the secret."""
    head, body, signature = token.split(".")
    expected = hmac.new(secret.encode(), f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
    assert hmac.compare_digest(_unb64(signature), expected), "token is not signed by this secret"
    assert json.loads(_unb64(head)) == {"alg": "HS256", "typ": "JWT"}
    return json.loads(_unb64(body))


KIND_AUDIO, KIND_VIDEO = "audio", "video"


SOURCE_MICROPHONE, SOURCE_CAMERA, SOURCE_SCREENSHARE_AUDIO = "microphone", "camera", "screen_share_audio"


def wait_until(check, seconds=5):
    """Poll until `check()` is true: the recorder's frames arrive on its own thread and loop."""
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the recorder")
        time.sleep(0.01)


class FakeLiveKit:
    """The LiveKit server and the rtc package the recorder uses, in memory. `rooms` maps a room to the identities in it,
    as the room service lists them. `joins` holds every room the recorder made, so a test can speak into one."""

    def __init__(self):
        self.rooms, self.joins, self.refuse = {}, [], False

    def hear(self, loop, recorder, speaker, frames, *, kind=KIND_AUDIO, source=SOURCE_MICROPHONE):
        """Publish a track from `speaker` that the recorder `recorder` subscribes to, on the media loop."""
        room = [joined for joined in self.joins if joined.identity == recorder][-1]
        track = SimpleNamespace(
            kind=kind,
            frames=[SimpleNamespace(data=memoryview(frame), samples_per_channel=len(frame) // 2) for frame in frames],
        )
        publication, participant = SimpleNamespace(source=source), SimpleNamespace(identity=speaker)
        asyncio.run_coroutine_threadsafe(room.emit("track_subscribed", track, publication, participant), loop).result(5)


class FakeRoom:
    def __init__(self, server):
        self.server, self.handlers, self.token, self.options = server, {}, None, None
        self.identity = self.room = None
        self.connected = False

    def on(self, event, handler):
        self.handlers[event] = handler

    async def connect(self, url, token, options=None):
        if self.server.refuse:
            raise RuntimeError("the server refused the join")
        claims = decode(token)
        self.token, self.options = token, options
        self.identity, self.room = claims["sub"], claims["video"]["room"]
        self.server.rooms.setdefault(self.room, set()).add(self.identity)
        self.server.joins.append(self)
        self.connected = True

    async def disconnect(self):
        self.connected = False
        self.server.rooms.get(self.room, set()).discard(self.identity)

    async def emit(self, event, *args):
        self.handlers[event](*args)


class FakeAudioStream:
    def __init__(self, track, sample_rate=0, num_channels=0):
        self.frames = list(track.frames)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return SimpleNamespace(frame=self.frames.pop(0))

    async def aclose(self):
        pass


@pytest.fixture
def fake_rtc(monkeypatch):
    """`livekit.rtc` as the recorder imports it, backed by one FakeLiveKit."""
    server = FakeLiveKit()
    rtc = types.ModuleType("livekit.rtc")
    rtc.Room = lambda: FakeRoom(server)
    rtc.RoomOptions = lambda **options: SimpleNamespace(**options)
    rtc.AudioStream = FakeAudioStream
    rtc.TrackKind = SimpleNamespace(KIND_AUDIO=KIND_AUDIO, KIND_VIDEO=KIND_VIDEO)
    rtc.TrackSource = SimpleNamespace(
        SOURCE_MICROPHONE=SOURCE_MICROPHONE,
        SOURCE_CAMERA=SOURCE_CAMERA,
        SOURCE_SCREENSHARE_AUDIO=SOURCE_SCREENSHARE_AUDIO,
    )
    package = types.ModuleType("livekit")
    package.rtc = rtc
    monkeypatch.setitem(sys.modules, "livekit", package)
    monkeypatch.setitem(sys.modules, "livekit.rtc", rtc)
    return server
