"""The media side of Tico Meet calls (backend/calls.py): LiveKit join tokens, LiveKit's server API, the server's own audio
participant, and the Gateway's speech-to-text and text-to-speech relays.

Tokens are HS256 JWTs signed with the install's LiveKit API secret, built here with the standard library. A person's
token carries audio and screen-share sources only; camera is never granted, and neither is a data channel. The server's
own participant, "Tico voice", hears every speaker and publishes one microphone track: the bots' spoken replies. It
is visible, so people can see that the call is heard; clients and every presence count leave it out (`is_voice`).

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
from array import array
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx

log = logging.getLogger("tico.call_media")

TOKEN_TTL_SECONDS = 300
# Sources a person may publish. Camera is deliberately absent: Tico Meet has audio and screen share only.
AUDIO_SOURCES = ("microphone", "screen_share", "screen_share_audio")
SAMPLE_RATE = 16000
TWIRP_TIMEOUT = 8
TWIRP_ANSWER_MAX = 1_000_000  # bytes of a LiveKit API answer read; a longer one is refused, not cut short
TRANSCRIBE_TIMEOUT = 30  # a live clip that takes longer is lost: the person is asked to repeat it
TRANSCRIBE_MODEL = "tico-transcribe-1.1"  # live-call clips: the Gateway's fast speech-to-text
# The Gateway's older speech-to-text, tried once when it does not know TRANSCRIBE_MODEL yet (it answers 400).
TRANSCRIBE_FALLBACK = "elevenlabs/scribe-v2"
CONNECT_TIMEOUT = 15  # the recorder's join, and its leave
RECORDER_PREFIX = "tico-recorder-"  # the recorder's identity is this and the call id, never a person's
VOICE_NAME = "Tico voice"  # how the recorder shows in the room
VOICE_RATE = 48000  # the voice track's sample rate (mono); the Gateway's speech is resampled to it
VOICE_FRAME_MS = 20
VOICE_FRAME_BYTES = VOICE_RATE * VOICE_FRAME_MS // 1000 * 2
SPEECH_MODEL = "tico-voice-1.0"
SPEECH_TIMEOUT = 15  # one sentence's speech from the Gateway
SPEECH_CHARS = 4000  # the Gateway's limit for one request
VOICES_TTL_SECONDS = 3600  # the Gateway's voice list, kept this long
END_PAUSE_MS = 450
SPEECH_TRANSPORT = None  # tests supply the Gateway at the HTTP boundary


class MediaUnavailable(Exception):
    """Tico Meet is not configured, its real-time package is not installed, or the Gateway refused (`status`, 0 when
    it did not answer)."""

    def __init__(self, message="", status=0):
        super().__init__(message)
        self.status = status


def is_voice(identity):
    """Whether a room identity is the server's own participant (Tico voice), which no presence counts."""
    return str(identity or "").startswith(RECORDER_PREFIX)


class MediaRefused(Exception):
    """LiveKit's server API refused a call, with its HTTP status."""

    def __init__(self, status, code=""):
        super().__init__(f"LiveKit answered {status}")
        self.status, self.code = status, code


@dataclass(frozen=True)
class MeetConfig:
    url: str  # what clients connect to (wss://...), from TICO_MEET_URL
    internal_url: str  # the server's address for LiveKit's server API, from TICO_MEET_INTERNAL_URL
    api_key: str
    api_secret: str


def config_from_env(environ=None):
    """The Tico Meet settings, or None when the meet profile is not set up (every value must be present)."""
    environ = os.environ if environ is None else environ
    values = {
        name: str(environ.get(name, "")).strip()
        for name in ("TICO_MEET_URL", "TICO_MEET_INTERNAL_URL", "TICO_MEET_API_KEY", "TICO_MEET_API_SECRET")
    }
    if not all(values.values()):
        return None
    return MeetConfig(
        url=values["TICO_MEET_URL"].rstrip("/"),
        internal_url=values["TICO_MEET_INTERNAL_URL"].rstrip("/"),
        api_key=values["TICO_MEET_API_KEY"],
        api_secret=values["TICO_MEET_API_SECRET"],
    )


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
    video = {
        "roomJoin": True,
        "room": room,
        "canSubscribe": True,
        "canPublish": True,
        "canPublishData": False,
        "canPublishSources": list(AUDIO_SOURCES),
        "canUpdateOwnMetadata": False,
    }
    return sign(_claims(config, identity, name, video, ttl, now), config.api_secret)


def recorder_token(config, room, identity, *, now=None, ttl=TOKEN_TTL_SECONDS):
    """The server's own participant, Tico voice: it hears the room and publishes one microphone track, the bots'
    spoken replies. Visible, so the room shows that it is heard; never a camera, a screen or data."""
    video = {
        "roomJoin": True,
        "room": room,
        "canSubscribe": True,
        "canPublish": True,
        "canPublishSources": ["microphone"],
        "canPublishData": False,
    }
    return sign(_claims(config, identity, VOICE_NAME, video, ttl, now), config.api_secret)


def admin_token(config, room="", *, now=None):
    """A short-lived token for LiveKit's server API: list and administer one room (or all, when `room` is empty)."""
    # DeleteRoom is gated on roomCreate, not roomAdmin: without it LiveKit answers 401 and the room stays open.
    # The token is minted on the server, sent only to LiveKit's API and lives a minute.
    video = {"roomList": True, "roomAdmin": True, "roomCreate": True, "room": room}
    return sign(_claims(config, "tico-server", "", video, 60, now), config.api_secret)


def _post_json(url, token, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TWIRP_TIMEOUT) as response:
            raw = response.read(TWIRP_ANSWER_MAX + 1)
    except urllib.error.HTTPError as exc:
        with exc:
            code = ""
            try:
                code = str(json.loads(exc.read(10_000) or b"{}").get("code") or "")
            except (ValueError, AttributeError):
                pass
            raise MediaRefused(exc.code, code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MediaUnavailable("LiveKit did not answer") from exc
    # An answer cut short or not JSON says nothing: the caller tries again on its next pass.
    if len(raw) > TWIRP_ANSWER_MAX:
        raise MediaUnavailable("LiveKit's answer was too large")
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        raise MediaUnavailable("LiveKit's answer was not JSON") from None
    if not isinstance(answer, dict):
        raise MediaUnavailable("LiveKit's answer was not an object")
    return answer


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
    block: backend/calls.py hands each clip to a worker. `on_quiet(identity)`, when given, is called on the media loop
    when a speaker's audio stops coming (muted, left), so what they said is sent without waiting for the sweep.
    `on_audio` can return the number of seconds to wait for missing audio. The recorder also plays 48 kHz mono speech
    on its microphone track, and `hush` drops the queued audio."""

    def __init__(self, sdk, loop, url, token, on_audio, on_quiet=None):
        self.sdk, self.loop, self.url, self.token, self.on_audio = sdk, loop, url, token, on_audio
        self.on_quiet = on_quiet
        self.quiet = {}  # identity -> the media loop's timer that says the speaker went quiet
        self.room = None
        self.source = None  # the voice track's audio source, once published
        self.hushed = 0  # bumped by hush: a playback that started before it stops
        self.cleared = 0  # the last hush generation applied to the native queue
        self.streams = {}  # media-loop playback tasks and their hush counts
        self.playback = asyncio.Lock()
        self.capturing = None
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
        if self.closed:  # closed while joining: leave again, so no recorder stays in the room unseen
            await self.room.disconnect()
            return
        source = self.sdk.AudioSource(VOICE_RATE, 1)
        track = self.sdk.LocalAudioTrack.create_audio_track("tico-voice", source)
        await self.room.local_participant.publish_track(
            track, self.sdk.TrackPublishOptions(source=self.sdk.TrackSource.SOURCE_MICROPHONE)
        )
        self.source = source

    def _subscribed(self, track, publication, participant):
        if is_voice(participant.identity):
            return  # never transcribe a Tico voice, this one's or another server's
        # Only a microphone is speech: a shared screen's sound (a video, a recording) is not someone asking.
        if track.kind == self.sdk.TrackKind.KIND_AUDIO and publication.source == self.sdk.TrackSource.SOURCE_MICROPHONE:
            self.loop.create_task(self._listen(track, participant.identity))

    async def _listen(self, track, identity):
        stream = self.sdk.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        heard, base = 0, None  # samples heard from this speaker, and where their first frame falls (ms since joining)
        try:
            async for event in stream:
                if self.closed:
                    break
                if base is None:
                    base = int((time.monotonic() - self.joined_at) * 1000)
                delay = self.on_audio(identity, bytes(event.frame.data), base + heard * 1000 // SAMPLE_RATE)
                heard += event.frame.samples_per_channel
                self._arm(identity, delay if delay is not None else END_PAUSE_MS / 1000)
        except Exception:  # noqa: BLE001 - one speaker's stream failing must not end the recording
            log.warning("The call recorder stopped hearing one speaker", exc_info=True)
        finally:
            self._went_quiet(identity)  # their track ended (they left): what they said is sent now
            await stream.aclose()

    def _arm(self, identity, delay):
        """Start (again) the timer that says a speaker went quiet: each frame they send pushes it back."""
        if self.on_quiet is None:
            return
        handle = self.quiet.pop(identity, None)
        if handle is not None:
            handle.cancel()
        self.quiet[identity] = self.loop.call_later(delay, self._went_quiet, identity)

    def _went_quiet(self, identity):
        handle = self.quiet.pop(identity, None)
        if handle is not None:
            handle.cancel()
        if self.on_quiet is None or self.closed:
            return
        try:
            self.on_quiet(identity)
        except Exception:  # noqa: BLE001 - the sweep sends it later; the media loop must keep running
            log.warning("A quiet speaker's speech was not sent at once", exc_info=True)

    def _disconnected(self, *_reason):
        self.lost = True  # LiveKit dropped the recorder: the sweep sees it is not running and joins again
        self.hush()

    def play(self, pcm, started=None, timeout=120, mark=None):
        """Send 48 kHz mono speech on the voice track and wait until it has played out; `started()` is called with the
        first frame sent. Returns the milliseconds sent: less than the whole when `hush` stopped it. `mark` is the hush
        count the caller read when it decided the speech may play (LiveKitMedia.mark): a hush since then, however soon,
        means none of it plays. Blocks the caller, never the media loop."""
        generation = self.hushed if mark is None else mark
        step = VOICE_FRAME_BYTES

        async def frames():
            for at in range(0, len(pcm), step):
                yield 0, pcm[at : at + step].ljust(step, b"\0")

        budget = ((len(pcm) + step - 1) // step) * VOICE_FRAME_MS
        sent, _ = self.play_stream(
            frames(), generation, budget, lambda _: True, lambda _: started() if started else None, timeout=timeout
        )
        return sent

    def play_stream(self, frames, generation, budget, before, started, *, timeout=120):
        """Play indexed 48 kHz frames as they arrive. Return (milliseconds sent, hit the call's cap)."""
        if self.source is None or not self.alive():
            return 0, False
        usage = _PlaybackUsage()
        future = asyncio.run_coroutine_threadsafe(
            self._play_stream(frames, generation, budget, before, started, usage), self.loop
        )
        try:
            return future.result(timeout)
        except TimeoutError:
            if self.hushed == generation:
                self.hush()
            log.warning("A bot's speech stopped streaming in a call", exc_info=True)
            return usage.sent, False

    async def _play_stream(self, frames, generation, budget, before, started, usage):
        task = asyncio.current_task()
        assert task is not None
        self.streams[task] = generation
        try:
            async with self.playback:
                source = self.source
                current, capped = None, False
                async with aclosing(frames):
                    if source is None or self.hushed != generation or not self.alive():
                        return 0, False
                    self._clear_hush()
                    while self.hushed == generation:
                        if usage.sent + VOICE_FRAME_MS > budget:
                            capped = True
                            break
                        item = await anext(frames, None)
                        if item is None or self.hushed != generation:
                            break
                        index, frame = item
                        first = index != current
                        if first:
                            if current is not None:
                                await source.wait_for_playout()
                            if not await asyncio.to_thread(before, index) or self.hushed != generation:
                                break
                            current = index
                        self.capturing = task
                        try:
                            await source.capture_frame(self.sdk.AudioFrame(frame, VOICE_RATE, 1, len(frame) // 2))
                            usage.sent += VOICE_FRAME_MS
                        finally:
                            self.capturing = None
                            hush = self.hushed
                            if hush != generation:
                                # The native capture can enqueue a frame after the first clear.
                                source.clear_queue()
                                self.cleared = hush
                        if first and self.hushed == generation:
                            started(index)
                if self.hushed == generation:
                    await source.wait_for_playout()
                return usage.sent, capped
        except asyncio.CancelledError:
            if self.hushed == generation:
                raise
        except Exception:  # noqa: BLE001 - a playback failure cuts the audio but still charges the submitted frames
            if self.hushed == generation:
                self.hush()
            log.warning("A bot's speech stopped streaming in a call", exc_info=True)
        finally:
            self.streams.pop(task)
        return usage.sent, False

    def hush(self):
        """Stop the voice at once: what is queued is dropped and a playback under way stops at its next frame."""
        self.hushed += 1
        if self.source is not None and not self.closed:
            self.loop.call_soon_threadsafe(self._hush, self.hushed)

    def _hush(self, generation):
        self._clear_hush()
        for task, mark in self.streams.items():
            # LiveKit owns the frame pointer until its async capture callback completes.
            if mark < generation and task is not self.capturing and not task.cancelling():
                task.cancel()

    def _clear_hush(self):
        generation = self.hushed
        if self.source is not None and self.cleared != generation:
            self.source.clear_queue()
            self.cleared = generation

    def close(self):
        self.hush()
        self.closed = True
        if self.room is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.room.disconnect(), self.loop).result(CONNECT_TIMEOUT)
        except Exception:  # noqa: BLE001 - leaving the room is best effort; the call is over either way
            log.warning("The call recorder did not leave its room cleanly", exc_info=True)


@dataclass
class _PlaybackUsage:
    sent: int = 0


class LiveKitMedia:
    """The media port backend/calls.py uses, against a real LiveKit server.

    `remove` and `end_room` treat a room or participant that is already gone as done: removal is idempotent."""

    def __init__(self, config):
        self.config = config
        self.recorders = {}
        self.lock = threading.Lock()  # guards `recorders` and the media loop
        self.starting = threading.Lock()  # one recorder joins at a time, so a call never has two
        self.loop = None
        self.speech_clients = _SpeechClients()

    @property
    def url(self):
        return self.config.url

    def running(self, call_id):
        recorder = self.recorders.get(call_id)
        return recorder is not None and recorder.alive()

    def _twirp(self, method, payload, room=""):
        return _post_json(
            f"{self.config.internal_url}/twirp/livekit.RoomService/{method}", admin_token(self.config, room), payload
        )

    def remove(self, room, identity):
        try:
            self._twirp("RemoveParticipant", {"room": room, "identity": identity}, room)
        except MediaRefused as exc:
            if exc.status != 404:  # not_found: the person is already out of the room
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

    def start_recorder(self, call_id, room, on_audio, on_quiet=None):
        """Join the room as Tico voice and hand each speaker's audio to `on_audio(identity, pcm, start_ms)`:
        `pcm` is 16 kHz mono signed 16-bit little-endian, and `start_ms` counts from when the recorder joined.
        `on_quiet(identity)` is told when a speaker's audio stops coming (Recorder). Returns once the recorder is in the
        room. Refuses with MediaUnavailable when it cannot join, or the package is missing."""
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
            recorder = Recorder(sdk, loop, _ws_url(self.config.internal_url), token, on_audio, on_quiet)
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

    def participants(self, room):
        """The identities in a room now, as LiveKit lists them, without Tico voice. [] when the room is gone. Raises
        MediaUnavailable when LiveKit did not answer, so a failed read is never taken for an empty room."""
        try:
            answer = self._twirp("ListParticipants", {"room": room}, room)
        except MediaRefused as exc:
            if exc.status == 404:
                return []
            raise MediaUnavailable("LiveKit refused the participant list") from None
        listed = answer.get("participants") or []
        if not isinstance(listed, list) or not all(isinstance(item, dict) for item in listed):
            raise MediaUnavailable("LiveKit's participant list was not a list")
        found = [str(item.get("identity") or "") for item in listed]
        return [identity for identity in found if identity and not is_voice(identity)]

    def mark(self, call_id):
        """Where the call's hushes stand now, to hand to `say`: read it before deciding speech may play, and a hush
        after that (End, a detach) keeps the speech from playing. None when this process does not hold the call."""
        recorder = self.recorders.get(call_id)
        return None if recorder is None else (recorder, recorder.hushed)

    def say(self, call_id, pcm, rate, started=None, mark=None):
        """Play speech (16-bit mono at `rate`) on this process's voice track in the call's room, and wait until it has
        played. Returns the milliseconds played; 0 when this process does not hold the call's recorder, or when the
        call was hushed since `mark` was read (or by another recorder than the one it was read from)."""
        recorder = self.recorders.get(call_id)
        if recorder is None or not recorder.alive():
            return 0
        if mark is not None and mark[0] is not recorder:
            return 0
        return recorder.play(resample(pcm, rate, VOICE_RATE), started, mark=None if mark is None else mark[1])

    def say_stream(self, call_id, frames, mark, budget, before, started):
        """Play a reply stream on the recorder whose hush count was read before speech was cleared to play."""
        recorder = self.recorders.get(call_id)
        if recorder is None or not recorder.alive() or mark is None or mark[0] is not recorder:
            return 0, False
        return recorder.play_stream(frames, mark[1], budget, before, started)

    def speech_frames(self, base, key, sentences, voice, first_chunk, *, request_started):
        return speech_frames(
            base, key, sentences, voice, first_chunk, request_started=request_started, clients=self.speech_clients
        )

    def hush(self, call_id):
        recorder = self.recorders.get(call_id)
        if recorder is not None:
            recorder.hush()


def wav_bytes(pcm, rate=SAMPLE_RATE):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def _unknown_model(status, answer):
    if status != 400:
        return False
    try:
        error = json.loads(answer or b"{}").get("error")
    except (ValueError, AttributeError):
        return False
    words = json.dumps(error).lower() if error is not None else ""
    return "model" in words and ("unknown" in words or "not found" in words or "unsupported" in words)


def transcribe(base, key, audio, *, model=TRANSCRIBE_MODEL, language=""):
    """The text of one WAV clip, through the Gateway's speech-to-text (clients/gateway.py relay). A Gateway that does
    not know `model` yet (400, unknown model) is asked once more with TRANSCRIBE_FALLBACK. Raises MediaUnavailable when
    the Gateway did not answer or refused, so the caller can keep the call going."""
    from clients import gateway as G

    def ask(name):
        body = {"model": name, "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": "wav"}}
        if language:
            body["language"] = language
        return G.relay(
            "POST",
            base + "/audio/transcriptions",
            json.dumps(body).encode("utf-8"),
            {"Authorization": "Bearer " + key},
            TRANSCRIBE_TIMEOUT,
        )

    status, answer = ask(model)
    if model != TRANSCRIBE_FALLBACK and _unknown_model(status, answer):
        log.warning("The Gateway does not know speech-to-text model %s; using %s", model, TRANSCRIBE_FALLBACK)
        status, answer = ask(TRANSCRIBE_FALLBACK)
    if status != 200:
        raise MediaUnavailable(f"The Gateway's speech-to-text answered {status}", status)
    try:
        text = json.loads(answer).get("text")
    except (ValueError, AttributeError):
        raise MediaUnavailable("The Gateway's speech-to-text answer was not JSON") from None
    return str(text or "").strip()


def resample(pcm, rate, target):
    """16-bit mono PCM at `rate` as `target` Hz, by linear interpolation: speech, not music, so this is enough."""
    if rate == target or not pcm:
        return bytes(pcm)
    samples = array("h")
    samples.frombytes(bytes(pcm[: len(pcm) - len(pcm) % 2]))
    if not samples:
        return b""
    if target == rate * 2:  # the Gateway's 24 kHz to the track's 48 kHz: every sample, then the mean of each pair
        doubled = array("h", bytes(len(samples) * 4))
        doubled[0::2] = samples
        tail = samples[1:] + samples[-1:]
        doubled[1::2] = array("h", ((a + b) >> 1 for a, b in zip(samples, tail, strict=True)))
        return doubled.tobytes()
    count = max(1, len(samples) * target // rate)
    last = len(samples) - 1
    step = rate / target
    out = array("h", bytes(count * 2))
    for n in range(count):
        at = n * step
        i = int(at)
        if i >= last:
            out[n] = samples[last]
            continue
        frac = at - i
        out[n] = int(samples[i] + (samples[i + 1] - samples[i]) * frac)
    return out.tobytes()


def _new_speech_client(trust_env, transport=None):
    return httpx.AsyncClient(
        timeout=SPEECH_TIMEOUT,
        trust_env=trust_env,
        transport=transport,
        # A connection pool can serve successive Gateway keys.
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=())),
    )


class _SpeechClients:
    """The process-lifetime media loop owns one connection pool for each proxy policy."""

    def __init__(self):
        self.clients = {}
        self.lock = asyncio.Lock()

    async def get(self, trust_env):
        async with self.lock:
            if trust_env not in self.clients:
                self.clients[trust_env] = await asyncio.to_thread(_new_speech_client, trust_env)
            return self.clients[trust_env]


@asynccontextmanager
async def _speech_client(trust_env, clients):
    transport = SPEECH_TRANSPORT
    if clients is not None and transport is None:
        yield await clients.get(trust_env)
    else:
        async with await asyncio.to_thread(_new_speech_client, trust_env, transport) as client:
            yield client


async def speech_frames(base, key, sentences, voice, first_chunk, *, request_started=None, clients=None):
    """Stream the indexed sentences as 48 kHz frames. Read ahead one sentence while playing the current one."""
    from clients import gateway as G

    try:
        url = G.safe_base(base + "/audio/speech")
    except ValueError:
        raise MediaUnavailable("The Gateway's speech URL is not allowed") from None
    headers = {
        "Authorization": "Bearer " + key,
        "Accept": "audio/pcm",
        "User-Agent": G.user_agent(),
    }
    # The Gateway allows plain HTTP to loopback only. A proxy must never see that key in clear.
    async with _speech_client(url.startswith("https:"), clients) as client:
        ahead = None
        try:
            for index, text in enumerate(sentences):
                if ahead is None:
                    frames = _sentence_frames(client, url, headers, text, voice, first_chunk, request_started)
                    head = None
                else:
                    frames, head = ahead
                    ahead = None
                async with aclosing(frames):
                    frame = await (head if head is not None else anext(frames))
                    yield index, frame
                    if index + 1 < len(sentences):
                        next_frames = _sentence_frames(client, url, headers, sentences[index + 1], voice, None, None)
                        ahead = next_frames, asyncio.create_task(anext(next_frames))
                    async for frame in frames:
                        yield index, frame
        finally:
            if ahead is not None:
                next_frames, next_head = ahead
                next_head.cancel()
                await asyncio.gather(next_head, return_exceptions=True)
                await next_frames.aclose()


class _SpeechPCM:
    """Resample mono PCM across input blocks and frame the output on the 48 kHz track's clock."""

    def __init__(self, rate):
        self.rate = rate
        self.samples = array("h")
        self.start = self.next_output = 0
        self.output = bytearray()

    def _convert(self, pcm, final):
        self.samples.frombytes(pcm)
        end = self.start + len(self.samples)
        limit = end * VOICE_RATE // self.rate
        if final:
            limit = max(1, limit)  # even a one-sample answer has audio after downsampling
        else:
            # Leave interpolation that needs the next input block for when its first sample arrives.
            limit = min(limit, ((end - 1) * VOICE_RATE + self.rate - 1) // self.rate)
        converted = array("h")
        for n in range(self.next_output, limit):
            index, fraction = divmod(n * self.rate, VOICE_RATE)
            at = index - self.start
            left = self.samples[at]
            right = self.samples[min(at + 1, len(self.samples) - 1)]
            converted.append(int((left * (VOICE_RATE - fraction) + right * fraction) / VOICE_RATE))
        self.next_output = limit
        consumed = min(len(self.samples), limit * self.rate // VOICE_RATE - self.start)
        del self.samples[:consumed]
        self.start += consumed
        return converted.tobytes()

    def frames(self, pcm, *, final=False):
        self.output += pcm if self.rate == VOICE_RATE else self._convert(pcm, final)
        while len(self.output) >= VOICE_FRAME_BYTES:
            yield bytes(self.output[:VOICE_FRAME_BYTES])
            del self.output[:VOICE_FRAME_BYTES]
        if final and self.output:
            yield bytes(self.output).ljust(VOICE_FRAME_BYTES, b"\0")
            self.output.clear()


async def _sentence_frames(client, url, headers, text, voice, first_chunk, request_started):
    """The Gateway's response is raw little-endian mono PCM. HTTP chunks can split a sample or a frame."""
    from clients import gateway as G

    body = {"model": SPEECH_MODEL, "input": text[:SPEECH_CHARS], "response_format": "pcm"}
    if voice:
        body["voice"] = voice
    status = 0
    try:
        if request_started is not None:
            request_started()
        async with client.stream("POST", url, headers=headers, json=body, follow_redirects=False) as response:
            status = response.status_code
            if status != 200:
                raise MediaUnavailable(f"The Gateway's text-to-speech answered {status}", status)
            named = response.headers.get("X-Audio-Sample-Rate", "").strip()
            rate = int(named) if named.isascii() and named.isdigit() and len(named) <= 5 else G.SPEECH_RATE
            if not 8000 <= rate <= 96000:
                rate = G.SPEECH_RATE
            frame_bytes = rate * VOICE_FRAME_MS // 1000 * 2
            pcm = _SpeechPCM(rate)
            tail, received = b"", 0
            async for chunk in response.aiter_bytes():
                if not received and first_chunk is not None:
                    first_chunk()
                received += len(chunk)
                if received > G.RELAY_LIMIT:
                    raise MediaUnavailable("The Gateway's speech answer was too large", status)
                data = tail + chunk
                whole = len(data) - len(data) % 2
                for at in range(0, whole, frame_bytes):
                    for frame in pcm.frames(data[at : min(at + frame_bytes, whole)]):
                        yield frame
                tail = data[whole:]
            if not received or received % 2:
                raise MediaUnavailable("The Gateway's text-to-speech answered with invalid audio", status)
            for frame in pcm.frames(b"", final=True):
                yield frame
    except httpx.HTTPError as exc:
        raise MediaUnavailable("The Gateway's speech connection stopped", status) from exc


_VOICES = {}  # Gateway base -> (when read, the voice names)
_VOICES_LOCK = threading.Lock()


def cached_voices(base):
    """The Gateway's voices as last read (`voices`), without asking it: None when they are not kept, or too old."""
    with _VOICES_LOCK:
        kept = _VOICES.get(base)
    return kept[1] if kept and time.monotonic() - kept[0] < VOICES_TTL_SECONDS else None


def voices(base, key):
    """The Gateway's voices (`GET /audio`, `speech.voices`), kept VOICES_TTL_SECONDS. [] when it lists none or does
    not answer: speech then uses the Gateway's own default."""
    from clients import gateway as G

    kept = cached_voices(base)
    if kept is not None:
        return kept
    status, answer = G.relay("GET", base + "/audio", None, {"Authorization": "Bearer " + key}, TWIRP_TIMEOUT)
    names = []
    if status == 200:
        try:
            listed = (json.loads(answer or b"{}").get("speech") or {}).get("voices") or []
        except (ValueError, AttributeError):
            listed = []
        for item in listed if isinstance(listed, list) else []:
            name = (
                item
                if isinstance(item, str)
                else (item.get("id") or item.get("name"))
                if isinstance(item, dict)
                else ""
            )
            if name:
                names.append(str(name))
    with _VOICES_LOCK:
        _VOICES[base] = (time.monotonic(), names)
    return names


def voice_for(slug, names):
    """A bot's voice: one of the Gateway's, picked by its slug, so it stays the same call after call."""
    if not names:
        return ""
    return names[int.from_bytes(hashlib.sha256(str(slug).encode()).digest()[:4], "big") % len(names)]
