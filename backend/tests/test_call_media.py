"""Tico Meet's media tokens never grant a camera, and the server's calls to LiveKit act only on the call's own room."""

import asyncio
import io
import json
import sys
import urllib.error
import wave

import pytest

from backend import call_media as media
from backend.tests.helpers.call_media import CONFIG, KIND_VIDEO, SOURCE_CAMERA, decode, fake_rtc, wait_until


def test_config_needs_all_four_values_and_drops_a_trailing_slash():
    env = {
        "TICO_MEET_URL": "wss://meet.acme.example/",
        "TICO_MEET_INTERNAL_URL": "http://livekit:7880/",
        "TICO_MEET_API_KEY": "APIacme",
        "TICO_MEET_API_SECRET": "secret",
    }
    config = media.config_from_env(env)
    assert (config.url, config.internal_url) == ("wss://meet.acme.example", "http://livekit:7880")
    assert media.config_from_env({**env, "TICO_MEET_API_SECRET": ""}) is None
    assert media.config_from_env({}) is None


def test_a_person_token_grants_audio_and_screen_share_only_and_never_camera():
    claims = decode(media.person_token(CONFIG, "thread-acme-1", "human:ana", "Ana", now=1000))
    grant = claims["video"]
    assert claims["iss"] == "APIacme" and claims["sub"] == "human:ana" and claims["name"] == "Ana"
    assert grant["roomJoin"] is True and grant["room"] == "thread-acme-1"
    assert grant["canPublish"] is True and grant["canSubscribe"] is True
    assert sorted(grant["canPublishSources"]) == ["microphone", "screen_share", "screen_share_audio"]
    assert "camera" not in json.dumps(claims)
    assert grant["canPublishData"] is False and grant["canUpdateOwnMetadata"] is False
    assert "roomAdmin" not in grant and "roomList" not in grant


def test_a_person_token_lives_five_minutes_from_when_it_is_minted():
    claims = decode(media.person_token(CONFIG, "thread-acme-1", "human:ana", "Ana", now=1000))
    assert claims["exp"] - 1000 == media.TOKEN_TTL_SECONDS == 300
    assert claims["nbf"] <= 1000


def test_the_recorder_hears_the_room_and_cannot_publish():
    claims = decode(media.recorder_token(CONFIG, "thread-acme-1", "tico-recorder-acme", now=1000))
    assert claims["video"]["hidden"] is True
    assert claims["video"]["canSubscribe"] is True
    assert claims["video"]["canPublish"] is False and claims["video"]["canPublishData"] is False
    assert "canPublishSources" not in claims["video"]


def test_the_server_api_token_is_admin_for_one_room_and_short_lived():
    claims = decode(media.admin_token(CONFIG, "thread-acme-1", now=1000))
    assert claims["video"]["roomAdmin"] is True and claims["video"]["roomList"] is True
    assert claims["video"]["roomCreate"] is True
    assert claims["video"]["room"] == "thread-acme-1"
    assert claims["exp"] - 1000 == 60


class _Answer:
    def __init__(self, body):
        self.body = body

    def read(self, _limit=None):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_removing_a_participant_calls_the_room_service_with_the_rooms_admin_token(monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["auth"] = request.get_header("Authorization")
        sent["body"] = json.loads(request.data)
        return _Answer(b"{}")

    monkeypatch.setattr(media.urllib.request, "urlopen", fake_urlopen)
    media.LiveKitMedia(CONFIG).remove("thread-acme-1", "human:ben")
    assert sent["url"] == "http://livekit:7880/twirp/livekit.RoomService/RemoveParticipant"
    assert sent["body"] == {"room": "thread-acme-1", "identity": "human:ben"}
    claims = decode(sent["auth"].split(" ", 1)[1])
    assert claims["video"]["room"] == "thread-acme-1" and claims["video"]["roomAdmin"] is True


def test_ending_a_room_deletes_it_with_a_token_allowed_to_create_and_delete_rooms(monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["auth"] = request.get_header("Authorization")
        return _Answer(b"{}")

    monkeypatch.setattr(media.urllib.request, "urlopen", fake_urlopen)
    media.LiveKitMedia(CONFIG).end_room("thread-acme-1")
    assert sent["url"] == "http://livekit:7880/twirp/livekit.RoomService/DeleteRoom"
    claims = decode(sent["auth"].split(" ", 1)[1])
    assert claims["video"]["roomCreate"] is True and claims["video"]["room"] == "thread-acme-1"


def test_removing_someone_already_gone_is_done_and_other_refusals_are_not(monkeypatch):
    def not_found(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO(b'{"code":"not_found"}'))

    monkeypatch.setattr(media.urllib.request, "urlopen", not_found)
    media.LiveKitMedia(CONFIG).remove("thread-acme-1", "human:ben")

    def refused(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(media.urllib.request, "urlopen", refused)
    with pytest.raises(media.MediaRefused) as caught:
        media.LiveKitMedia(CONFIG).remove("thread-acme-1", "human:ben")
    assert caught.value.status == 401


def test_a_server_that_does_not_answer_is_unavailable_not_a_refusal(monkeypatch):
    def down(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(media.urllib.request, "urlopen", down)
    with pytest.raises(media.MediaUnavailable):
        media.LiveKitMedia(CONFIG).room_exists("thread-acme-1")


def test_the_pcm_wraps_into_a_16khz_mono_wav():
    pcm = b"\x01\x00" * 1600
    with wave.open(io.BytesIO(media.wav_bytes(pcm)), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, 16000)
        assert reader.readframes(1600) == pcm


def test_transcribing_returns_the_gateway_text_and_a_failed_gateway_is_unavailable(monkeypatch):
    from clients import gateway

    calls = []

    def relay_ok(method, url, body=None, headers=None, timeout=None):
        calls.append((method, url, json.loads(body), headers))
        return 200, json.dumps({"text": " hello from Acme "}).encode()

    monkeypatch.setattr(gateway, "relay", relay_ok)
    text = media.transcribe("https://gateway.acme.example/v1", "gw-key", b"RIFF")
    assert text == "hello from Acme"
    method, url, body, headers = calls[0]
    assert method == "POST" and url == "https://gateway.acme.example/v1/audio/transcriptions"
    assert body["model"] == media.TRANSCRIBE_MODEL and body["input_audio"]["format"] == "wav"
    assert headers == {"Authorization": "Bearer gw-key"}

    monkeypatch.setattr(gateway, "relay", lambda *args, **kwargs: (503, b"{}"))
    with pytest.raises(media.MediaUnavailable):
        media.transcribe("https://gateway.acme.example/v1", "gw-key", b"RIFF")


def test_the_recorder_joins_hidden_and_hears_microphones_but_never_camera(fake_rtc):
    live = media.LiveKitMedia(CONFIG)
    heard = []
    live.start_recorder(
        "call1", "tico-call-call1", lambda identity, pcm, start_ms: heard.append((identity, pcm, start_ms))
    )
    try:
        recorder = fake_rtc.joins[-1]
        assert fake_rtc.rooms["tico-call-call1"] == {media.RECORDER_PREFIX + "call1"}
        claims = decode(recorder.token)["video"]
        assert claims["hidden"] is True and claims["canPublish"] is False and claims["canSubscribe"] is True
        assert recorder.options.auto_subscribe is True
        frame = b"\x01\x00" * 800  # 50 ms at 16 kHz
        fake_rtc.hear(live.loop, recorder.identity, "human:ana", [frame], kind=KIND_VIDEO, source=SOURCE_CAMERA)
        fake_rtc.hear(live.loop, recorder.identity, "human:ana", [frame, frame])
        wait_until(lambda: len(heard) == 2)
        assert {identity for identity, _pcm, _ms in heard} == {"human:ana"}
        assert heard[0][1] == frame and heard[1][2] - heard[0][2] == 50
        assert live.running("call1")
    finally:
        live.stop_recorder("call1")
    assert not live.running("call1") and fake_rtc.rooms["tico-call-call1"] == set()


def test_a_recorder_that_livekit_drops_is_not_running_until_it_joins_again(fake_rtc):
    live = media.LiveKitMedia(CONFIG)
    live.start_recorder("call2", "tico-call-call2", lambda *args: None)
    first = fake_rtc.joins[-1]
    asyncio.run_coroutine_threadsafe(first.emit("disconnected", "server_shutdown"), live.loop).result(5)
    assert not live.running("call2")
    live.start_recorder("call2", "tico-call-call2", lambda *args: None)
    assert live.running("call2") and fake_rtc.joins[-1] is not first
    live.stop_recorder("call2")


def test_a_recorder_the_server_refuses_leaves_the_call_unavailable(fake_rtc):
    fake_rtc.refuse = True
    live = media.LiveKitMedia(CONFIG)
    with pytest.raises(media.MediaUnavailable):
        live.start_recorder("call3", "tico-call-call3", lambda *args: None)
    assert not live.running("call3")


def test_a_server_without_the_livekit_package_refuses_calls_and_keeps_running(monkeypatch):
    monkeypatch.setitem(sys.modules, "livekit", None)
    with pytest.raises(media.MediaUnavailable):
        media.LiveKitMedia(CONFIG).start_recorder("call4", "tico-call-call4", lambda *args: None)
