"""The media side of Tico Meet calls (backend/calls.py): LiveKit join tokens, LiveKit's server API, the server's own audio
participant, and the Gateway's speech-to-text relay.

Tokens are HS256 JWTs signed with the install's LiveKit API secret, built here with the standard library. A person's
token carries audio and screen-share sources only; camera is never granted, and neither is a data channel. The server's
recorder participant is hidden and can subscribe, but cannot publish.

The server API is LiveKit's Twirp HTTP interface (`/twirp/livekit.RoomService/...`), called with a short admin token.
Nothing here returns or logs a secret.

The recorder is the one place that needs LiveKit's real-time package (`livekit`, pinned in backend/requirements.txt and
imported only when a call starts). A server without it refuses calls with `meet_unavailable`, rather than running a
call that nobody hears.
"""

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass

log = logging.getLogger("tico.call_media")

TOKEN_TTL_SECONDS = 300
# Sources a person may publish. Camera is deliberately absent: Tico Meet has audio and screen share only.
AUDIO_SOURCES = ("microphone", "screen_share", "screen_share_audio")
SAMPLE_RATE = 16000
TWIRP_TIMEOUT = 8
TRANSCRIBE_TIMEOUT = 120
TRANSCRIBE_MODEL = "elevenlabs/scribe-v2"     # the Recorder's default speech-to-text model (recorder/src/settings.rs)
CONNECT_TIMEOUT = 15                          # the recorder's join, and its leave
RECORDER_PREFIX = "tico-recorder-"            # the recorder's identity is this and the call id, never a person's


class MediaUnavailable(Exception):
    """Tico Meet is not configured, or its real-time package is not installed."""


class MediaRefused(Exception):
    """LiveKit's server API refused a call, with its HTTP status."""

    def __init__(self, status, code=""):
        super().__init__(f"LiveKit answered {status}")
        self.status, self.code = status, code


@dataclass(frozen=True)
class MeetConfig:
    url: str            # what clients connect to (wss://...), from TICO_MEET_URL
    internal_url: str   # the server's address for LiveKit's server API, from TICO_MEET_INTERNAL_URL
    api_key: str
    api_secret: str


def config_from_env(environ=None):
    """The Tico Meet settings, or None when the meet profile is not set up (every value must be present)."""
    environ = os.environ if environ is None else environ
    values = {name: str(environ.get(name, "")).strip() for name in
              ("TICO_MEET_URL", "TICO_MEET_INTERNAL_URL", "TICO_MEET_API_KEY", "TICO_MEET_API_SECRET")}
    if not all(values.values()):
        return None
    return MeetConfig(url=values["TICO_MEET_URL"].rstrip("/"), internal_url=values["TICO_MEET_INTERNAL_URL"].rstrip("/"),
                      api_key=values["TICO_MEET_API_KEY"], api_secret=values["TICO_MEET_API_SECRET"])


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign(claims, secret):
    """A compact HS256 JWT."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(signature)}"


def _claims(config, identity, name, video, ttl, now):
    now = int(time.time()) if now is None else int(now)
    return {"iss": config.api_key, "sub": identity, "name": name, "nbf": now - 10, "exp": now + ttl, "video": video}


def person_token(config, room, identity, name, *, now=None, ttl=TOKEN_TTL_SECONDS):
    """A person's join token for one call room: audio and screen share, never camera or data."""
    video = {"roomJoin": True, "room": room, "canSubscribe": True, "canPublish": True, "canPublishData": False,
             "canPublishSources": list(AUDIO_SOURCES), "canUpdateOwnMetadata": False}
    return sign(_claims(config, identity, name, video, ttl, now), config.api_secret)


def recorder_token(config, room, identity, *, now=None, ttl=TOKEN_TTL_SECONDS):
    """The server's own participant: hidden, subscribe only, so it hears the room and says nothing."""
    video = {"roomJoin": True, "room": room, "canSubscribe": True, "canPublish": False, "canPublishData": False,
             "hidden": True}
    return sign(_claims(config, identity, "Tico recorder", video, ttl, now), config.api_secret)


def admin_token(config, room="", *, now=None):
    """A short-lived token for LiveKit's server API: list and administer one room (or all, when `room` is empty)."""
    # DeleteRoom is gated on roomCreate, not roomAdmin: without it LiveKit answers 401 and the room stays open.
    # The token is minted on the server, sent only to LiveKit's API and lives a minute.
    video = {"roomList": True, "roomAdmin": True, "roomCreate": True, "room": room}
    return sign(_claims(config, "tico-server", "", video, 60, now), config.api_secret)


def _post_json(url, token, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TWIRP_TIMEOUT) as response:
            return json.loads(response.read(1_000_000) or b"{}")
    except urllib.error.HTTPError as exc:
        code = ""
        try:
            code = str(json.loads(exc.read(10_000) or b"{}").get("code") or "")
        except (ValueError, AttributeError):
            pass
        raise MediaRefused(exc.code, code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MediaUnavailable("LiveKit did not answer") from exc


def _sdk():
    """LiveKit's real-time package, imported when a call starts: a server without it still runs, and refuses calls."""
    try:
        from livekit import rtc
    except ImportError:
        raise MediaUnavailable("The Tico Meet recorder needs the livekit package on this server") from None
    return rtc


def _ws_url(url):
    """The SDK connects over a websocket; the server's own address (TICO_MEET_INTERNAL_URL) is http(s)."""
    return url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


class Recorder:
    """The server's audio participant in one call's room. Its frames arrive on the media loop, so `on_audio` must not
    block: backend/calls.py hands each clip to a worker."""

    def __init__(self, sdk, loop, url, token, on_audio):
        self.sdk, self.loop, self.url, self.token, self.on_audio = sdk, loop, url, token, on_audio
        self.room = None
        self.joined_at = None
        self.lost = self.closed = False

    def alive(self):
        return not (self.lost or self.closed)

    def connect(self):
        asyncio.run_coroutine_threadsafe(self._connect(), self.loop).result(CONNECT_TIMEOUT)

    async def _connect(self):
        # The SDK binds its objects to the loop that is running when they are made, so they are made here.
        self.room = self.sdk.Room()
        self.room.on("track_subscribed", self._subscribed)
        self.room.on("disconnected", self._disconnected)
        await self.room.connect(self.url, self.token, options=self.sdk.RoomOptions(auto_subscribe=True))
        self.joined_at = time.monotonic()
        if self.closed:         # closed while joining: leave again, so no recorder stays in the room unseen
            await self.room.disconnect()

    def _subscribed(self, track, publication, participant):
        speech = (self.sdk.TrackSource.SOURCE_MICROPHONE, self.sdk.TrackSource.SOURCE_SCREENSHARE_AUDIO)
        if track.kind == self.sdk.TrackKind.KIND_AUDIO and publication.source in speech:
            self.loop.create_task(self._listen(track, participant.identity))

    async def _listen(self, track, identity):
        stream = self.sdk.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        heard, base = 0, None   # samples heard from this speaker, and where their first frame falls (ms since joining)
        try:
            async for event in stream:
                if self.closed:
                    break
                if base is None:
                    base = int((time.monotonic() - self.joined_at) * 1000)
                self.on_audio(identity, bytes(event.frame.data), base + heard * 1000 // SAMPLE_RATE)
                heard += event.frame.samples_per_channel
        except Exception:
            log.warning("The call recorder stopped hearing one speaker", exc_info=True)
        finally:
            await stream.aclose()

    def _disconnected(self, *_reason):
        self.lost = True        # LiveKit dropped the recorder: the sweep sees it is not running and joins again

    def close(self):
        self.closed = True
        if self.room is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.room.disconnect(), self.loop).result(CONNECT_TIMEOUT)
        except Exception:
            log.warning("The call recorder did not leave its room cleanly", exc_info=True)


class LiveKitMedia:
    """The media port backend/calls.py uses, against a real LiveKit server.

    `remove` and `end_room` treat a room or participant that is already gone as done: removal is idempotent."""

    def __init__(self, config):
        self.config = config
        self.recorders = {}
        self.lock = threading.Lock()        # guards `recorders` and the media loop
        self.starting = threading.Lock()    # one recorder joins at a time, so a call never has two
        self.loop = None

    @property
    def url(self):
        return self.config.url

    def running(self, call_id):
        recorder = self.recorders.get(call_id)
        return recorder is not None and recorder.alive()

    def _twirp(self, method, payload, room=""):
        return _post_json(f"{self.config.internal_url}/twirp/livekit.RoomService/{method}",
                          admin_token(self.config, room), payload)

    def remove(self, room, identity):
        try:
            self._twirp("RemoveParticipant", {"room": room, "identity": identity}, room)
        except MediaRefused as exc:
            if exc.status != 404:       # not_found: the person is already out of the room
                raise

    def room_exists(self, room):
        answer = self._twirp("ListRooms", {"names": [room]}, room)
        return any(item.get("name") == room for item in answer.get("rooms") or [])

    def end_room(self, room):
        try:
            self._twirp("DeleteRoom", {"room": room}, room)
        except MediaRefused as exc:
            if exc.status != 404:
                raise

    def person_token(self, room, identity, name):
        return person_token(self.config, room, identity, name)

    def start_recorder(self, call_id, room, on_audio):
        """Join the room as the hidden recorder and hand each speaker's audio to `on_audio(identity, pcm, start_ms)`:
        `pcm` is 16 kHz mono signed 16-bit little-endian, and `start_ms` counts from when the recorder joined. Returns
        once the recorder is in the room. Refuses with MediaUnavailable when it cannot join, or the package is
        missing."""
        sdk = _sdk()
        with self.starting:
            with self.lock:
                current = self.recorders.get(call_id)
                if current is not None and current.alive():
                    return
                if self.loop is None:
                    self.loop = asyncio.new_event_loop()
                    threading.Thread(target=self.loop.run_forever, name="tico-meet-media", daemon=True).start()
                loop = self.loop
            token = recorder_token(self.config, room, RECORDER_PREFIX + call_id)
            recorder = Recorder(sdk, loop, _ws_url(self.config.internal_url), token, on_audio)
            try:
                recorder.connect()
            except Exception as exc:
                recorder.close()
                raise MediaUnavailable("The Tico Meet recorder could not join the call") from exc
            with self.lock:
                stale, self.recorders[call_id] = self.recorders.get(call_id), recorder
            if stale is not None:
                stale.close()

    def stop_recorder(self, call_id):
        with self.lock:
            recorder = self.recorders.pop(call_id, None)
        if recorder is not None:
            recorder.close()


def wav_bytes(pcm, rate=SAMPLE_RATE):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def transcribe(base, key, audio, *, model=TRANSCRIBE_MODEL, language=""):
    """The text of one WAV clip, through the Gateway's speech-to-text (the Recorder's path, clients/gateway.py relay).
    Raises MediaUnavailable when the Gateway did not answer or refused, so the caller can keep the call going."""
    from clients import gateway as G

    body = {"model": model, "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": "wav"}}
    if language:
        body["language"] = language
    status, answer = G.relay("POST", base + "/audio/transcriptions", json.dumps(body).encode("utf-8"),
                             {"Authorization": "Bearer " + key}, TRANSCRIBE_TIMEOUT)
    if status != 200:
        raise MediaUnavailable(f"The Gateway's speech-to-text answered {status}")
    try:
        text = json.loads(answer).get("text")
    except (ValueError, AttributeError):
        raise MediaUnavailable("The Gateway's speech-to-text answer was not JSON") from None
    return str(text or "").strip()
