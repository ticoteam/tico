"""Voice in Tico Meet calls (docs/meet.md, Voice): speech cut at pauses and stored in the order it was heard, who answers
and when, the bots' spoken replies, End, presence and the bot limit. LiveKit and the Gateway are fakes (FakeMedia, the
meet fixture), and every name is synthetic (Acme, acme.example)."""

import logging
import threading
from array import array
from datetime import datetime, timedelta, timezone

import pytest

from backend import call_media, call_voice, calls, live_meetings
from backend.store import H
from backend.tests.helpers.api import assign, get, headers, post, runner
from backend.tests.helpers.calls import (
    PCM_EIGHT_SECONDS,
    PCM_ONE_SECOND,
    SILENCE_ONE_SECOND,
    SYNTHETIC_SPEECH,
    join,
    meet,  # noqa: F401
    private_lanes_ready,
    start_call,
    thread,
    thread_texts,
)
from backend.tests.helpers.live_meetings import live  # noqa: F401

SPEECH = array("h", [3000, -3000] * 160).tobytes()  # 20 ms of speech at 16 kHz
QUIET = bytes(640)  # 20 ms of silence


def ms(n, frame):
    return frame * (n // 20)


# --- pause detection


def test_speech_is_cut_where_the_speaker_pauses_with_a_little_before_and_after():
    cut = call_voice.Segmenter()
    audio = ms(400, QUIET) + ms(1000, SPEECH) + ms(800, QUIET)
    clips = []
    for at in range(0, len(audio), 1000):  # pieces that do not line up with frames
        clips += cut.feed(audio[at : at + 1000], 5000 + at // call_voice.BYTES_PER_MS)
    [(start, pcm)] = clips
    assert start == 5000 + 400 - call_voice.PREROLL_MS  # the soft start before the first loud frame is kept
    assert len(pcm) // call_voice.BYTES_PER_MS == call_voice.PREROLL_MS + 1000 + call_voice.PREROLL_MS
    assert not cut.speaking and cut.flush() is None

    cut = call_voice.Segmenter()
    cut.feed(ms(600, SPEECH), 0)
    assert cut.speaking
    start, pcm = cut.flush()  # the speaker stopped sending (muted, left) or the call is ending
    assert start == 0 and len(pcm) == len(ms(600, SPEECH))


def test_a_long_utterance_is_cut_at_a_minute_and_a_click_is_not_speech():
    cut = call_voice.Segmenter()
    second = ms(1000, SPEECH)
    clips = []
    for n in range(61):
        clips += cut.feed(second, n * 1000)
    assert [len(pcm) // call_voice.BYTES_PER_MS for _start, pcm in clips] == [call_voice.QUESTION_MAX_MS]
    assert cut.speaking  # the last second goes on as the next utterance

    click = call_voice.Segmenter()
    assert click.feed(ms(100, SPEECH) + ms(800, QUIET), 0) == []


def test_a_reply_is_spoken_without_its_markdown_and_only_its_first_part():
    said = call_voice.spoken("## Plan\n- **Ship** the [review](https://acme.example/r) today.\n```\ncode\n```\nThanks!")
    assert said == "Plan Ship the review today. Thanks!"
    long = " ".join(f"Sentence number {n} is here." for n in range(60))
    cut = call_voice.spoken(long)
    assert len(cut) <= call_voice.SPOKEN_CHARS and cut.endswith(".")
    assert call_voice.sentences("Yes. The rollout needs a review first. Ok.") == [
        "Yes. The rollout needs a review first. Ok."
    ]
    assert call_voice.sentences("The rollout needs a review first. Then Ops can ship it on Friday.") == [
        "The rollout needs a review first.",
        "Then Ops can ship it on Friday.",
    ]


# --- order


def test_clips_are_stored_in_the_order_they_were_heard_whichever_is_transcribed_first(meet, monkeypatch):
    first_in, release = threading.Event(), threading.Event()
    words = iter(["First words", "Second words"])
    lock = threading.Lock()

    def transcribe(base, key, audio, **kw):
        with lock:
            text = next(words)
        if text == "First words":
            first_in.set()
            assert release.wait(10)
        return text

    monkeypatch.setattr(call_media, "transcribe", transcribe)
    call = start_call(meet, thread(meet), speak_replies=False)
    machine = meet.app.state.calls
    machine.ingest(call["id"], "human:ana", PCM_EIGHT_SECONDS, 0)
    assert first_in.wait(10)
    machine.ingest(call["id"], "human:ben", PCM_EIGHT_SECONDS, 1000)  # a later clip, transcribed first
    machine.workers.submit(lambda: None).result(5)
    release.set()
    assert machine.settle()
    assert [r["text"] for r in _chunks(meet, call["meeting_id"])] == ["First words", "Second words"]


# --- who answers, and the answer spoken


def test_in_a_one_to_one_call_the_bot_answers_every_question_aloud_one_at_a_time(meet, monkeypatch):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    call_id, media, voice = call["id"], meet.app.state.call_media, meet.app.state.calls.voice
    heard = []

    def say(call_id, pcm, rate, started=None):
        with meet.app.state.store.read() as c:  # the reply committed before any speech was made or played
            heard.append(c.execute("SELECT COUNT(*) FROM live_meeting_chat WHERE role='bot'").fetchone()[0])
        started()
        media.said.append((call_id, pcm, rate))
        return 100

    monkeypatch.setattr(media, "say", say)
    _speak(meet, call_id, 0)
    assert _states(meet, call_id) == [("ops", "thinking", None)]
    _speak(meet, call_id, 10_000)  # asked again while Ops answers: it waits
    assert len(_turns(meet, call["meeting_id"])) == 1

    machine = _machine(meet, "ops")
    _answer(meet, machine, "ops", "**Yes.** Ops can take the reliability review this week.")
    assert "**Yes.** Ops can take the reliability review this week." in thread_texts(meet, cid)
    assert heard == [1] and len(media.said) == 1 and media.said[0][2] == 24000
    assert _states(meet, call_id) == [("ops", "listening", None)]
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT voice_ms FROM calls WHERE id=?", (call_id,)).fetchone()[0] == 100
        reply_id = c.execute("SELECT id FROM live_meeting_chat WHERE role='bot'").fetchone()[0]
    voice.reply(call_id, "ops", reply_id, "Again")  # a reply is spoken once
    meet.app.state.calls.settle()
    assert len(media.said) == 1

    voice.tick()  # the question that waited is asked now
    assert len(_turns(meet, call["meeting_id"])) == 2 and _states(meet, call_id) == [("ops", "thinking", None)]
    media.recorders.pop(call_id)  # another process holds the call now: this one does not play into it
    _answer(meet, machine, "ops", "Done.")
    assert "Done." in thread_texts(meet, cid) and len(media.said) == 1
    assert _states(meet, call_id) == [("ops", "listening", None)]


def test_a_bot_that_does_not_answer_in_time_is_cut_off_and_its_late_answer_is_never_heard(meet):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    voice, now = meet.app.state.calls.voice, [1000.0]
    voice.clock = lambda: now[0]
    machine = _machine(meet, "ops")
    _speak(meet, call["id"], 0)
    attempt = _claim(meet, machine, "ops")  # the run starts, and is slow
    now[0] += call_voice.ANSWER_SECONDS - 1
    voice.tick()
    assert _turns(meet, call["meeting_id"])[0]["status"] in ("pending", "claimed")
    now[0] += 2
    voice.tick()
    [turn] = _turns(meet, call["meeting_id"])
    assert (turn["status"], turn["skip_reason"]) == ("skipped", "voice_deadline")
    shown = get(meet, f"calls/{call['id']}")["voice"]
    assert shown["note"].endswith("couldn't answer in time") and shown["bots"][0]["state"] == "listening"
    _complete(meet, machine, attempt, "A late answer.")
    assert "A late answer." not in thread_texts(meet, cid) and meet.app.state.call_media.said == []


def test_in_a_group_call_only_a_named_bot_answers_and_a_question_left_waiting_too_long_is_dropped(meet):
    cid = thread(meet)
    call = start_call(meet, cid, bots=["ops", "finance"])
    voice, now = meet.app.state.calls.voice, [1000.0]
    voice.clock = lambda: now[0]
    _speak(meet, call["id"], 0, "The rollout needs a review")
    assert _turns(meet, call["meeting_id"]) == []  # nobody named, and the router's windows do not run in a voice call
    assert live_meetings._run_windows(meet.app, call["meeting_id"]) is None
    _speak(meet, call["id"], 10_000, "Ops, can you take the review?")
    _speak(meet, call["id"], 20_000, "Finance, what does it cost?")
    assert [t["bot"] for t in _turns(meet, call["meeting_id"])] == ["ops"]
    assert dict((s, st) for s, st, _c in _states(meet, call["id"])) == {"ops": "thinking", "finance": "waiting"}
    now[0] += call_voice.ANSWER_SECONDS + 1
    voice.tick()  # Ops ran out of time, and Finance's question has waited too long
    assert [t["bot"] for t in _turns(meet, call["meeting_id"])] == ["ops"]
    shown = get(meet, f"calls/{call['id']}")["voice"]
    assert {b["state"] for b in shown["bots"]} == {"listening"} and shown["note"].endswith("missed a question")


def test_a_clip_that_cannot_be_transcribed_asks_for_a_repeat_and_asks_no_bot(meet, monkeypatch):
    def refuse(base, key, audio, **kw):
        raise call_media.MediaUnavailable("The Gateway's speech-to-text answered 503", 503)

    monkeypatch.setattr(call_media, "transcribe", refuse)
    call = start_call(meet, _dm(meet), bots=["ops"])
    _speak(meet, call["id"], 0)
    assert _turns(meet, call["meeting_id"]) == [] and _chunks(meet, call["meeting_id"]) == []
    assert get(meet, f"calls/{call['id']}")["voice"]["note"] == calls.REPEAT_NOTE


def test_a_voice_that_fails_leaves_the_reply_as_text_and_logs_why(meet, monkeypatch, caplog):
    def refuse(base, key, text, voice=""):
        raise call_media.MediaUnavailable("The Gateway's text-to-speech answered 404", 404)

    monkeypatch.setattr(call_media, "speech", refuse)
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    _speak(meet, call["id"], 0)
    with caplog.at_level(logging.WARNING, logger="tico.call_voice"):
        _answer(meet, _machine(meet, "ops"), "ops", "Ops can take it.")
    assert "Ops can take it." in thread_texts(meet, cid) and meet.app.state.call_media.said == []
    assert any("call_voice_speech_failed" in r.getMessage() and "status=404" in r.getMessage() for r in caplog.records)
    assert _states(meet, call["id"]) == [("ops", "listening", None)]


def test_speech_made_after_the_call_began_ending_is_never_played(meet, monkeypatch):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])

    def speech(base, key, text, voice=""):
        with meet.app.state.store.transaction() as c:  # End was pressed while the speech was being made
            c.execute("UPDATE calls SET ending_at=? WHERE id=?", (H.now(), call["id"]))
        return array("h", [1000] * 2400).tobytes(), 24000

    monkeypatch.setattr(call_media, "speech", speech)
    _speak(meet, call["id"], 0)
    _answer(meet, _machine(meet, "ops"), "ops", "Ops can take it.")
    assert "Ops can take it." in thread_texts(meet, cid) and meet.app.state.call_media.said == []


# --- stopping the voice


def test_detach_and_end_stop_the_voice_at_once_and_end_stops_listening(meet):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    call_id, media = call["id"], meet.app.state.call_media
    join(meet, call_id)
    _speak(meet, call_id, 0)
    response = meet.request("DELETE", f"/api/v2/calls/{call_id}/bots/ops", json={}, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    assert media.hushed == [call_id] and _states(meet, call_id) == []
    assert _turns(meet, call["meeting_id"])[0]["skip_reason"] == "bot_detached"

    post(meet, f"calls/{call_id}/bots", {"bots": ["ops"]})
    _speak(meet, call_id, 10_000)
    assert _states(meet, call_id) == [("ops", "thinking", None)]
    ended = post(meet, f"calls/{call_id}/end", {})
    assert ended["state"] == "ended" and ended["voice"] is None and media.hushed[-1] == call_id
    assert _turns(meet, call["meeting_id"])[-1]["status"] == "skipped"
    before = len(_chunks(meet, call["meeting_id"]))
    meet.app.state.calls.ingest(call_id, "human:ana", PCM_EIGHT_SECONDS, 20_000)  # heard after End: dropped
    assert meet.app.state.calls.settle() and len(_chunks(meet, call["meeting_id"])) == before


def test_end_answers_ending_while_speech_already_heard_is_transcribed_then_ends(meet, monkeypatch):
    held, release = threading.Event(), threading.Event()

    def transcribe(base, key, audio, **kw):
        held.set()
        assert release.wait(10)
        return SYNTHETIC_SPEECH

    monkeypatch.setattr(call_media, "transcribe", transcribe)
    monkeypatch.setattr(calls, "END_WAIT_SECONDS", 0.05)
    cid = thread(meet)
    call = start_call(meet, cid)
    meet.app.state.calls.ingest(call["id"], "human:ana", PCM_EIGHT_SECONDS, 0)
    assert held.wait(10)
    ending = post(meet, f"calls/{call['id']}/end", {})
    assert ending["state"] == "ending"
    assert get(meet, f"threads/{cid}/calls")["calls"][0]["state"] == "ending"
    meet.app.state.calls.sweep()  # this process is finishing it: the sweep leaves it alone
    assert get(meet, f"calls/{call['id']}")["state"] == "ending"
    release.set()
    assert meet.app.state.calls.settle()
    assert get(meet, f"calls/{call['id']}")["state"] == "ended"
    assert any(t.startswith("Call transcript") and SYNTHETIC_SPEECH in t for t in thread_texts(meet, cid))

    # A call whose End was pressed in a process that is gone is ended by the sweep.
    other = start_call(meet, cid)
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE calls SET ending_at=? WHERE id=?", (H.now(), other["id"]))
    meet.app.state.calls.sweep()
    assert get(meet, f"calls/{other['id']}")["state"] == "ended"


# --- presence


def test_who_is_in_the_call_comes_from_the_room_and_an_empty_room_ends_after_its_grace(meet, monkeypatch):
    cid = thread(meet)
    call = start_call(meet, cid)
    call_id, media, machine = call["id"], meet.app.state.call_media, meet.app.state.calls
    join(meet, call_id)
    machine.sweep()
    shown = get(meet, f"calls/{call_id}")
    assert shown["live_count"] == 1 and shown["status_source"] == "recorder"  # Tico voice is not counted
    assert [p["in_call"] for p in shown["participants"]] == [True]

    monkeypatch.setattr(media, "participants", lambda room: (_ for _ in ()).throw(call_media.MediaUnavailable()))
    media.rooms["tico-call-" + call_id].discard("human:ana")
    machine.sweep()  # a read LiveKit did not answer changes nothing
    assert get(meet, f"calls/{call_id}")["live_count"] == 1
    monkeypatch.undo()
    meet.app.state.call_media = media
    monkeypatch.setattr(call_media, "transcribe", lambda base, key, audio, **kw: SYNTHETIC_SPEECH)

    machine.sweep()  # everyone left: the call waits for a rejoin
    shown = get(meet, f"calls/{call_id}")
    assert shown["state"] == "live" and shown["live_count"] == 0 and shown["participants"][0]["in_call"] is False
    _backdate(meet, call_id, "meet_empty_at", calls.EMPTY_SECONDS + 1)
    machine.sweep()
    shown = get(meet, f"calls/{call_id}")
    assert (shown["state"], shown["end_reason"]) == ("ended", "empty")


def test_a_call_nobody_ever_joins_ends_unanswered(meet):
    call = start_call(meet, thread(meet))
    _backdate(meet, call["id"], "started_at", calls.NO_ANSWER_SECONDS + 1)
    meet.app.state.calls.sweep()
    shown = get(meet, f"calls/{call['id']}")
    assert (shown["state"], shown["end_reason"]) == ("ended", "no_answer")


# --- the bot limit


def test_the_bot_limit_counts_the_bots_already_in_the_call(meet, monkeypatch):
    monkeypatch.setattr(calls, "BOT_LIMIT", 1)
    call = start_call(meet, thread(meet))
    post(meet, f"calls/{call['id']}/bots", {"bots": ["ops"]})
    post(meet, f"calls/{call['id']}/bots", {"bots": ["ops"]})  # already in it: not counted twice
    refused = post(meet, f"calls/{call['id']}/bots", {"bots": ["finance"]}, expected=422)
    assert refused["error"]["code"] == "bot_limit"
    assert get(meet, f"calls/{call['id']}")["bots"] == ["ops"]


# --- helpers


def _dm(api):
    """A DM between Ana and the Ops bot: a call in it with Ops is a 1:1 bot call."""
    with api.app.state.store.transaction() as c:
        return H.open_conversation(c, "human:ana", ["bot:ops"], kind="chat", subject="Ops")["id"]


def _speak(api, call_id, start_ms, text=None):
    """One utterance, then a pause: transcribed (as `text` when given) and stored."""
    if text is not None:
        call_media_transcribe = call_media.transcribe
        call_media.transcribe = lambda base, key, audio, **kw: text
    try:
        api.app.state.calls.ingest(call_id, "human:ana", PCM_ONE_SECOND * 2 + SILENCE_ONE_SECOND, start_ms)
        assert api.app.state.calls.settle()
    finally:
        if text is not None:
            call_media.transcribe = call_media_transcribe


def _machine(api, bot):
    machine = runner(api)
    assign(api, machine, bot)
    private_lanes_ready(api, machine, bot)
    return machine


def _answer(api, machine, bot, text):
    """The bot's run answers its next meeting turn, as the runner does."""
    _complete(api, machine, _claim(api, machine, bot), text)


def _claim(api, machine, bot):
    attempt = post(api, "jobs/claim", {"bot": bot}, token=machine["token"])["attempt"]
    assert attempt, "no turn was queued for the bot"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-call-turn"}, token=machine["token"])
    return attempt


def _complete(api, machine, attempt, text):
    response = api.post(
        f"/api/v2/attempts/{attempt['id']}/complete",
        json={"outcome": "completed", "text": text, "last_seq": 0},
        headers=headers(machine["token"]),
    )
    assert response.status_code in (200, 409), response.text  # a run canceled under it may be refused
    assert api.app.state.calls.settle()


def _states(api, call_id):
    voice = get(api, f"calls/{call_id}")["voice"] or {"bots": []}
    return [(b["slug"], b["state"], b["caption"]) for b in voice["bots"]]


def _turns(api, meeting_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT bot,status,skip_reason FROM live_meeting_turns WHERE meeting_id=? ORDER BY created_at,id",
                (meeting_id,),
            )
        ]


def _chunks(api, meeting_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT seq,text FROM live_meeting_chunks WHERE meeting_id=? ORDER BY seq", (meeting_id,)
            )
        ]


def _backdate(api, call_id, column, seconds):
    stamp = calls._stamp(datetime.now(timezone.utc) - timedelta(seconds=seconds))
    with api.app.state.store.transaction() as c:
        c.execute(f"UPDATE calls SET {column}=? WHERE id=?", (stamp, call_id))


@pytest.fixture(autouse=True)
def _quiet_voice(meet):
    """The voice ticker is off in these tests (the meet fixture): each test ticks it when it means to."""
    yield
    meet.app.state.calls.voice.stop()
