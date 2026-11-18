"""Voice in Tico Meet calls (docs/meet.md, Voice): speech cut at pauses and stored in the order it was heard, who answers
and when, the bots' spoken replies, End, presence and the bot limit. LiveKit and the Gateway are fakes (FakeMedia, the
meet fixture), and every name is synthetic (Acme, acme.example)."""

import asyncio
import json
import logging
import threading
from array import array
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend import call_media, call_voice, calls, events, live_meetings, threads
from backend.store import H
from backend.tests.helpers.api import assign, get, headers, post, runner
from backend.tests.helpers.call_media import CONFIG, fake_rtc, wait_until  # noqa: F401 - pytest discovers the RTC fixture
from backend.tests.helpers.calls import (
    PCM_EIGHT_SECONDS,
    PCM_ONE_SECOND,
    SILENCE_ONE_SECOND,
    SYNTHETIC_SPEECH,
    join,
    meet,  # noqa: F401
    private_lanes_ready,
    speech_response,
    start_call,
    thread,
    thread_texts,
)
from backend.tests.helpers.live_meetings import live  # noqa: F401
from backend.tests.helpers.new_bot_flows import create_bot
from backend.tests.helpers.task_lanes import new_task, task_bot

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


def test_a_full_utterance_ends_at_460_ms_but_a_short_fragment_can_continue():
    cut = call_voice.Segmenter()
    first = ms(400, SPEECH) + ms(500, QUIET)
    assert cut.feed(first, 0) == []
    assert cut.feed(ms(600, SPEECH) + ms(440, QUIET), 900) == []
    assert cut.feed(QUIET, 1940) == [(0, first + ms(600, SPEECH) + ms(200, QUIET))]

    short = call_voice.Segmenter()
    assert short.feed(ms(300, SPEECH) + ms(680, QUIET), 0) == []
    assert short.feed(QUIET, 980) == [(0, ms(300, SPEECH) + ms(200, QUIET))]


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

    def say(call_id, pcm, rate, started=None, mark=None):
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
    assert heard == [1] and len(media.said) == 1 and media.said[0][2] == 48000
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


def test_a_one_to_one_call_wakes_the_runner_and_claims_while_its_bot_is_steering_a_task(meet):
    machine = task_bot(meet, lanes=True, capacity=5)
    rid, token = machine["runner_id"], machine["token"]
    task = new_task(meet, "Review the launch")
    claim = {"next_run": True, "start_places": True, "busy_bots": [], "busy_lanes": []}
    task_run = post(meet, "jobs/claim", claim, token)["attempt"]
    assert task_run["lane"] == "task:" + task["id"]
    post(meet, f"attempts/{task_run['id']}/started", {"thread_id": "task-session"}, token)
    followup = post(meet, f"tasks/{task['id']}/comments", {"text": "Check the numbers too."})["comment"]
    inputs = post(meet, f"attempts/{task_run['id']}/inputs", {}, token)["messages"]
    assert followup["id"] in [m["id"] for m in inputs]

    call = start_call(meet, _dm(meet), bots=["ops"])
    store = meet.app.state.store
    bell = events.runner_bell(store)
    with store.read() as c:
        cursor = events.latest(c)
    _speak(meet, call["id"], 0, "Can we ship today?")
    assert _turns(meet, call["meeting_id"]) == [{"bot": "ops", "status": "pending", "skip_reason": ""}]
    with store.read() as c:
        sent, _, reset, _ = events.runner_read(c, rid, "runner:" + rid, cursor)
    assert not reset
    (work_seq,) = [seq for seq, frame in sent if frame["kind"] == "work" and frame["bots"] == ["ops"]]
    assert bell.latest >= work_seq

    claim["busy_lanes"] = ["ops|" + task_run["lane"]]
    call_run = post(meet, "jobs/claim", claim, token)["attempt"]
    assert call_run is not None
    assert call_run["lane"] != task_run["lane"]
    assert call_run["message"]["refs"]["live_meeting"]["meeting_id"] == call["meeting_id"]
    with store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (task_run["id"],)).fetchone()[0] == "running"


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
    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(404)))
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    _speak(meet, call["id"], 0)
    with caplog.at_level(logging.WARNING, logger="tico.call_voice"):
        _answer(meet, _machine(meet, "ops"), "ops", "Ops can take it.")
    assert "Ops can take it." in thread_texts(meet, cid) and meet.app.state.call_media.said == []
    assert any("call_voice_speech_failed" in r.getMessage() and "status=404" in r.getMessage() for r in caplog.records)
    assert _states(meet, call["id"]) == [("ops", "listening", None)]


def test_each_voice_turn_logs_one_timing_line_with_no_words_in_it(meet, monkeypatch, caplog):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    voice, now = meet.app.state.calls.voice, [1000.0]
    voice.clock = lambda: now[0]
    machine = _machine(meet, "ops")

    def synthesize(request):
        now[0] += 0.33
        return speech_response()

    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(synthesize))

    def lines():
        found = [r.getMessage() for r in caplog.records if r.getMessage().startswith("call_voice_turn ")]
        return [dict(part.split("=", 1) for part in line.split()[1:]) for line in found]

    with caplog.at_level(logging.INFO, logger="tico.call_voice"):
        _speak(meet, call["id"], 0)
        _answer(meet, machine, "ops", "Ops can take the reliability review.")
        _speak(meet, call["id"], 10_000)  # the next one is not answered in time
        _claim(meet, machine, "ops")
        now[0] += call_voice.ANSWER_SECONDS + 1
        voice.tick()
    spoken, skipped = lines()
    fields = {
        "call_id",
        "bot",
        "turn",
        "outcome",
        "skip_reason",
        "transcribe_ms",
        "queue_ms",
        "answer_ms",
        "speech_ms",
        "heard_to_transcript_ms",
        "tts_first_chunk_ms",
        "total_ms",
    }
    assert set(spoken) == set(skipped) == fields
    assert (spoken["call_id"], spoken["bot"], spoken["outcome"], spoken["skip_reason"]) == (
        call["id"],
        "ops",
        "spoken",
        "null",
    )
    assert all(spoken[k].isdigit() for k in ("transcribe_ms", "queue_ms", "answer_ms", "speech_ms", "total_ms"))
    assert spoken["heard_to_transcript_ms"] == spoken["transcribe_ms"]
    assert spoken["tts_first_chunk_ms"] == "330" and skipped["tts_first_chunk_ms"] == "null"
    assert (skipped["outcome"], skipped["skip_reason"]) == ("skipped", "voice_deadline")
    assert (
        skipped["queue_ms"].isdigit() and skipped["answer_ms"] == skipped["speech_ms"] == skipped["total_ms"] == "null"
    )
    assert not any(
        "reliability" in r.getMessage() for r in caplog.records if r.getMessage().startswith("call_voice_turn")
    )


def test_speech_made_after_the_call_began_ending_is_never_played(meet, monkeypatch):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])

    def speech(request):
        with meet.app.state.store.transaction() as c:  # End was pressed while the speech was being made
            c.execute("UPDATE calls SET ending_at=? WHERE id=?", (H.now(), call["id"]))
        return speech_response()

    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(speech))
    _speak(meet, call["id"], 0)
    _answer(meet, _machine(meet, "ops"), "ops", "Ops can take it.")
    assert "Ops can take it." in thread_texts(meet, cid) and meet.app.state.call_media.said == []


def test_a_bot_named_by_its_slug_joins_by_its_id_and_answers_everything_in_its_dm(meet):
    """Call now names bots by slug (the roster's `bot:<slug>`): a bot made through the API has a UUIDv7 id, and the call
    holds that id, so the DM's 1:1 rule finds it. Adding it again by either name is no second bot; it leaves by its
    slug; a name that is no bot is 404."""
    bot = create_bot(meet, "field-helper")
    assert bot != "field-helper"
    dm = get(meet, "threads/dm?with=" + "bot:field-helper")
    cid = (dm.get("thread") or dm)["id"]
    call = start_call(meet, cid, bots=["field-helper"])
    assert call["bots"] == [bot]
    with meet.app.state.store.read() as c:
        row = c.execute("SELECT * FROM calls WHERE id=?", (call["id"],)).fetchone()
        assert call_voice.Voice.answers_all(c, row) == bot
    join(meet, call["id"])
    again = post(meet, f"calls/{call['id']}/bots", {"bots": ["field-helper", bot, "bot:field-helper"]})
    assert again["bots"] == [bot]
    missing = post(meet, f"calls/{call['id']}/bots", {"bots": ["no-such-bot"]}, expected=404)
    assert "no-such-bot" in str(missing)
    left = meet.request("DELETE", f"/api/v2/calls/{call['id']}/bots/field-helper", json={}, headers=headers("ana-test"))
    assert left.status_code == 200, left.text
    assert left.json()["bots"] == []


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
    assert meet.app.state.calls.settle() and get(meet, f"calls/{other['id']}")["state"] == "ended"


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
    monkeypatch.setattr(live_meetings, "CALL_BOT_LIMIT", 1)
    call = start_call(meet, thread(meet))
    post(meet, f"calls/{call['id']}/bots", {"bots": ["ops"]})
    post(meet, f"calls/{call['id']}/bots", {"bots": ["ops"]})  # already in it: not counted twice
    refused = post(meet, f"calls/{call['id']}/bots", {"bots": ["finance"]}, expected=422)
    assert refused["error"]["code"] == "bot_limit"
    assert get(meet, f"calls/{call['id']}")["bots"] == ["ops"]


# --- End, and what it stops


def test_an_end_that_is_refused_leaves_the_call_fully_live(meet):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    call_id, media = call["id"], meet.app.state.call_media
    _speak(meet, call_id, 0)
    unkeyed = {"Authorization": headers("ana-test")["Authorization"]}
    refused = meet.post(f"/api/v2/calls/{call_id}/end", json={}, headers=unkeyed)
    assert refused.status_code == 422, refused.text  # no Idempotency-Key: nothing was stored
    assert media.hushed == [] and _states(meet, call_id) == [("ops", "thinking", None)]
    assert get(meet, f"calls/{call_id}")["state"] == "live"
    _speak(meet, call_id, 10_000, "Still listening")  # still heard
    assert "Still listening" in [chunk["text"] for chunk in _chunks(meet, call["meeting_id"])]


def test_two_ends_at_once_end_the_call_once_and_nobody_joins_an_ending_call(meet, monkeypatch):
    held, release = threading.Event(), threading.Event()

    def transcribe(base, key, audio, **kw):
        held.set()
        assert release.wait(10)
        return SYNTHETIC_SPEECH

    monkeypatch.setattr(call_media, "transcribe", transcribe)
    monkeypatch.setattr(calls, "END_WAIT_SECONDS", 0.05)
    closes, close = [], calls.Calls._close

    def counted(self, *args, **kwargs):
        closes.append(args[0])
        return close(self, *args, **kwargs)

    monkeypatch.setattr(calls.Calls, "_close", counted)
    call = start_call(meet, thread(meet))
    call_id, machine = call["id"], meet.app.state.calls
    join(meet, call_id)
    machine.ingest(call_id, "human:ana", PCM_EIGHT_SECONDS, 0)
    assert held.wait(10)
    answers, gate = [], threading.Barrier(2)

    def end():
        gate.wait(5)
        answers.append(machine.wind_down(call_id, "human:ana"))

    enders = [threading.Thread(target=end) for _ in range(2)]
    for one in enders:
        one.start()
    for one in enders:
        one.join(10)
    assert answers == [False, False] and closes == [call_id]  # one thread ends it; both answer `ending`
    assert post(meet, f"calls/{call_id}/end", {})["state"] == "ending"
    assert post(meet, f"calls/{call_id}/join", {}, token="ben-test", expected=409)["error"]["code"] == "call_ended"
    assert post(meet, f"calls/{call_id}/token", {}, expected=409)["error"]["code"] == "call_ended"
    with meet.app.state.store.read() as c:
        assert threads.live_calls(c, [call["thread_id"]]) == {}  # the rail shows no live call to join
    release.set()
    assert machine.settle() and closes == [call_id] and get(meet, f"calls/{call_id}")["state"] == "ended"


def test_a_hush_after_speech_was_cleared_to_play_keeps_it_from_playing(meet, monkeypatch):
    call = start_call(meet, _dm(meet), bots=["ops"])
    call_id, media, voice = call["id"], meet.app.state.call_media, meet.app.state.calls.voice
    standing = voice._standing

    def then_detached(*args):
        cleared = standing(*args)
        voice.cancel(call_id, "ops")  # a detach commits just after the check: its hush comes before the speech plays
        return cleared

    monkeypatch.setattr(voice, "_standing", then_detached)
    _speak(meet, call_id, 0)
    _answer(meet, _machine(meet, "ops"), "ops", "Ops can take it.")
    assert media.hushed == [call_id] and media.said == []


# --- the deadline


def test_a_reply_stored_in_time_whose_speech_would_start_late_stays_text(meet, monkeypatch):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    voice, now = meet.app.state.calls.voice, [1000.0]
    voice.clock = lambda: now[0]
    _speak(meet, call["id"], 0)  # heard at 1000
    machine = _machine(meet, "ops")
    attempt = _claim(meet, machine, "ops")
    now[0] += call_voice.ANSWER_SECONDS - 1  # stored with a second to spare

    def slow(request):
        now[0] += 11  # its speech is made only after the deadline
        return speech_response()

    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(slow))
    _complete(meet, machine, attempt, "Ops can take it.")
    assert "Ops can take it." in thread_texts(meet, cid) and meet.app.state.call_media.said == []
    assert _states(meet, call["id"]) == [("ops", "listening", None)]


def test_a_reply_stored_after_its_deadline_but_before_the_ticker_stays_text(meet):
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    voice, now = meet.app.state.calls.voice, [1000.0]
    voice.clock = lambda: now[0]
    _speak(meet, call["id"], 0)
    machine = _machine(meet, "ops")
    attempt = _claim(meet, machine, "ops")
    now[0] += call_voice.ANSWER_SECONDS + 1  # the reply reaches the writer before the ticker ends the turn
    _complete(meet, machine, attempt, "Ops can take it.")
    assert "Ops can take it." in thread_texts(meet, cid) and meet.app.state.call_media.said == []
    assert _states(meet, call["id"]) == [("ops", "listening", None)]


# --- the meeting routes on a voice call


def test_the_meeting_routes_keep_to_a_voice_call_one_question_its_bot_limit_and_its_voice(meet, monkeypatch):
    call = start_call(meet, _dm(meet), bots=["ops"])
    call_id, rid, voice = call["id"], call["meeting_id"], meet.app.state.calls.voice
    join(meet, call_id)
    _speak(meet, call_id, 0)
    assert len(_turns(meet, rid)) == 1

    post(meet, f"live-meetings/{rid}/chat", {"text": "Ops, and the budget?"})  # typed while Ops answers: it waits
    seq = get(meet, f"live-meetings/{rid}")["seq"] + 1
    line = {"seq": seq, "speaker": "ana", "start_ms": 30_000, "end_ms": 31_000, "text": "Ops, one more thing"}
    post(meet, f"live-meetings/{rid}/chunks", {"chunks": [line]})
    assert len(_turns(meet, rid)) == 1 and list(voice._found(call_id).waiting) == ["ops"]

    voice.reply(call_id, "ops", "synthetic-chat-reply", "Not the open question.", "synthetic-other-turn")
    assert meet.app.state.call_media.said == [] and voice._found(call_id).open["phase"] == "thinking"

    monkeypatch.setattr(live_meetings, "CALL_BOT_LIMIT", 1)
    refused = post(meet, f"live-meetings/{rid}/bots", {"bots": ["finance"]}, expected=422)
    assert refused["error"]["code"] == "bot_limit"

    response = meet.request("DELETE", f"/api/v2/live-meetings/{rid}/bots/ops", json={}, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    assert voice._found(call_id).open is None and meet.app.state.call_media.hushed == [call_id]


# --- what is heard


def test_a_backlog_of_clips_is_bounded_and_freed_and_a_question_heard_too_long_ago_is_not_asked(meet, monkeypatch):
    release, lock, texts = threading.Event(), threading.Lock(), iter(f"Clip {n}" for n in range(1, 100))

    def transcribe(base, key, audio, **kw):
        assert release.wait(10)
        with lock:
            return next(texts)

    monkeypatch.setattr(call_media, "transcribe", transcribe)
    monkeypatch.setattr(calls, "BACKLOG", 2, raising=False)
    call = start_call(meet, thread(meet), speak_replies=False)
    call_id, machine = call["id"], meet.app.state.calls
    for n in range(4):  # four utterances while the Gateway is slow: the two oldest are dropped
        machine.ingest(call_id, "human:ana", PCM_ONE_SECOND * 2 + SILENCE_ONE_SECOND, n * 10_000)
    release.set()
    assert machine.settle()
    assert len(_chunks(meet, call["meeting_id"])) == 2
    assert machine.pending.get(call_id) is None and machine.order[call_id]["queue"] == []  # nothing kept once stored

    monkeypatch.setattr(calls, "BACKLOG", 4, raising=False)
    dm = start_call(meet, _dm(meet), bots=["ops"])
    voice, now = machine.voice, [1000.0]
    voice.clock = lambda: now[0]

    def late(base, key, audio, **kw):
        now[0] += call_voice.ANSWER_SECONDS + 1  # transcribed long after it was said
        return "Ops, can you take the review?"

    monkeypatch.setattr(call_media, "transcribe", late)
    machine.ingest(dm["id"], "human:ana", PCM_ONE_SECOND * 2 + SILENCE_ONE_SECOND, 0)
    assert machine.settle()
    assert _turns(meet, dm["meeting_id"]) == [] and get(meet, f"calls/{dm['id']}")["voice"]["note"] == calls.REPEAT_NOTE


def test_clips_are_stored_in_the_order_people_began_saying_them(meet, monkeypatch):
    monkeypatch.setattr(
        call_media, "transcribe", lambda base, key, audio, **kw: "Ana's long point" if len(audio) > 80_000 else "Ben"
    )
    call = start_call(meet, thread(meet), speak_replies=False)
    call_id, machine = call["id"], meet.app.state.calls
    machine.ingest(call_id, "human:ana", PCM_ONE_SECOND * 4, 0)  # Ana is still talking
    machine.ingest(call_id, "human:ben", PCM_ONE_SECOND + SILENCE_ONE_SECOND, 2000)  # Ben says something short
    assert machine.settle()
    machine.ingest(call_id, "human:ana", SILENCE_ONE_SECOND, 4000)  # Ana pauses
    assert machine.settle()
    assert [chunk["text"] for chunk in _chunks(meet, call["meeting_id"])] == ["Ana's long point", "Ben"]


def test_in_a_group_dm_with_two_people_and_a_bot_the_bot_answers_only_when_named(meet):
    call = start_call(meet, thread(meet), bots=["ops"])  # Ana, Ben and Ops
    _speak(meet, call["id"], 0, "The rollout needs a review")
    assert _turns(meet, call["meeting_id"]) == []
    _speak(meet, call["id"], 10_000, "Ops, can you take it?")
    assert [turn["bot"] for turn in _turns(meet, call["meeting_id"])] == ["ops"]


def test_a_speaker_whose_audio_stops_has_what_they_said_sent_without_the_sweep(meet):
    call = start_call(meet, thread(meet), speak_replies=False)
    machine, media = meet.app.state.calls, meet.app.state.call_media
    machine.ingest(call["id"], "human:ana", PCM_ONE_SECOND * 2, 0)  # then muted: no pause is ever heard
    assert machine.settle() and _chunks(meet, call["meeting_id"]) == []
    media.quiet[call["id"]]("human:ana")  # the media loop's timer after their last frame
    assert machine.settle() and [chunk["text"] for chunk in _chunks(meet, call["meeting_id"])] == [SYNTHETIC_SPEECH]


@pytest.mark.parametrize("end", ["timer", "sweep", "track"])
def test_missing_mic_packets_give_a_fragment_its_full_pause(meet, fake_rtc, end):
    call = start_call(meet, thread(meet), speak_replies=False)
    machine, fake = meet.app.state.calls, meet.app.state.call_media
    call_id, meeting_id, identity = call["id"], call["meeting_id"], "human:ana"
    now = [1000.0]
    machine.voice.clock = lambda: now[0]
    on_audio, on_quiet = fake.recorders[call_id], fake.quiet[call_id]
    fragment = ms(300, SPEECH)

    if end == "sweep":
        on_audio(identity, fragment, 0)
        now[0] += 0.5
        machine.flush(idle=True)
        assert machine.settle() and _chunks(meet, meeting_id) == []
        now[0] += 0.201
        machine.flush(idle=True)
    else:
        heard, flushed = threading.Event(), threading.Event()

        def feed(*args):
            delay = on_audio(*args)
            heard.set()
            return delay

        def flush(who):
            on_quiet(who)
            flushed.set()

        live_media = call_media.LiveKitMedia(CONFIG)
        live_media.start_recorder(call_id, "tico-call-test", feed, flush)
        loop = live_media.loop
        loop.time = lambda: now[0]
        track = fake_rtc.hear(loop, call_media.RECORDER_PREFIX + call_id, identity, [fragment], held=True)
        release = track.release

        async def advance(at):
            now[0] = at
            marker = loop.create_future()
            loop.call_at(at, marker.set_result, None)
            await marker

        try:
            assert heard.wait(5), "the recorder did not receive the microphone fragment"
            asyncio.run_coroutine_threadsafe(advance(1000.5), loop).result(5)
            assert not flushed.is_set()
            if end == "track":
                release.set()
            else:
                asyncio.run_coroutine_threadsafe(advance(1000.701), loop).result(5)
            assert flushed.wait(5), "the recorder did not flush the microphone fragment"
        finally:
            release.set()
            live_media.stop_recorder(call_id)

    assert machine.settle() and [chunk["text"] for chunk in _chunks(meet, meeting_id)] == [SYNTHETIC_SPEECH]


# --- two bots, the voice cap and the ticker


def test_taking_out_a_waiting_bot_leaves_the_one_speaking_alone(meet, monkeypatch):
    call = start_call(meet, thread(meet), bots=["ops", "finance"])
    call_id, media, machine = call["id"], meet.app.state.call_media, meet.app.state.calls
    join(meet, call_id)
    playing, release, said = threading.Event(), threading.Event(), []

    def say(call_id, pcm, rate, started=None, mark=None):
        started()
        playing.set()
        assert release.wait(10)
        said.append(pcm)
        return 100

    monkeypatch.setattr(media, "say", say)
    _speak(meet, call_id, 0, "Ops, can you take the review?")
    _speak(meet, call_id, 10_000, "Finance, what does it cost?")
    worker = _machine(meet, "ops")
    attempt = _claim(meet, worker, "ops")
    worker_headers = headers(worker["token"])
    body = {"outcome": "completed", "text": "Ops can take the review. It starts on Monday morning.", "last_seq": 0}
    assert meet.post(f"/api/v2/attempts/{attempt['id']}/complete", json=body, headers=worker_headers).status_code == 200
    assert playing.wait(10)
    response = meet.request("DELETE", f"/api/v2/calls/{call_id}/bots/finance", json={}, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    release.set()
    assert machine.settle()
    assert len(said) == 2 and media.hushed == []  # both of Ops's sentences played
    assert _states(meet, call_id) == [("ops", "listening", None)]


def test_speech_is_never_made_past_the_voice_cap_or_for_a_call_already_ending(meet, monkeypatch):
    made = []

    def speech(request):
        made.append(json.loads(request.content)["input"])
        return speech_response()

    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(speech))
    cid = _dm(meet)
    call = start_call(meet, cid, bots=["ops"])
    call_id, media = call["id"], meet.app.state.call_media
    first, second = "Ops can take the review this week.", "It starts on Monday morning at nine."
    room = call_voice.estimate_ms(first) + call_voice.estimate_ms(second) // 2  # the first fits, not both
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE calls SET voice_ms=? WHERE id=?", (call_voice.VOICE_CAP_MS - room, call_id))
    machine = _machine(meet, "ops")
    _speak(meet, call_id, 0)
    _answer(meet, machine, "ops", f"{first} {second}")
    assert made == [first] and len(media.said) == 1  # the second was never made

    with meet.app.state.store.transaction() as c:  # too little left for even one sentence
        c.execute("UPDATE calls SET voice_ms=? WHERE id=?", (call_voice.VOICE_CAP_MS - 10, call_id))
    _speak(meet, call_id, 10_000)
    _answer(meet, machine, "ops", first)
    assert made == [first] and len(media.said) == 1

    with meet.app.state.store.transaction() as c:  # End is in before the reply is spoken: nothing is made
        c.execute("UPDATE calls SET voice_ms=0 WHERE id=?", (call_id,))
    _speak(meet, call_id, 20_000)
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE calls SET ending_at=? WHERE id=?", (H.now(), call_id))
    _answer(meet, machine, "ops", first)
    assert made == [first] and len(media.said) == 1


def test_the_voice_ticker_takes_the_writer_only_when_something_changed(meet, monkeypatch):
    call = start_call(meet, _dm(meet), bots=["ops"])
    voice, store, now = meet.app.state.calls.voice, meet.app.state.store, [1000.0]
    voice.clock = lambda: now[0]
    _speak(meet, call["id"], 0)
    writes, transaction = [], store.transaction

    def counted(*args, **kwargs):
        writes.append(1)
        return transaction(*args, **kwargs)

    monkeypatch.setattr(store, "transaction", counted)
    for _ in range(3):
        voice.tick()  # Ops is thinking, well inside its time
    assert writes == []
    now[0] += call_voice.ANSWER_SECONDS
    voice.tick()
    assert writes and _turns(meet, call["meeting_id"])[0]["skip_reason"] == "voice_deadline"


def test_the_gateway_voices_are_read_when_the_call_starts_and_a_reply_never_waits_on_them(meet, monkeypatch):
    reads, made = [], []
    monkeypatch.setattr(call_media, "voices", lambda base, key: reads.append(base) or ["acme-alto", "acme-bass"])
    monkeypatch.setattr(
        call_media, "cached_voices", lambda base: ["acme-alto", "acme-bass"] if reads else None, raising=False
    )

    def synthesize(request):
        made.append(json.loads(request.content)["voice"])
        return speech_response()

    monkeypatch.setattr(call_media, "SPEECH_TRANSPORT", httpx.MockTransport(synthesize))
    call = start_call(meet, _dm(meet), bots=["ops"])
    voice = meet.app.state.calls.voice
    voice.makers.submit(lambda: None).result(5)
    wait_until(lambda: reads == ["https://gateway.acme.example/v1"])  # read as the call starts
    _speak(meet, call["id"], 0)
    _answer(meet, _machine(meet, "ops"), "ops", "Ops can take it.")
    assert made == [call_media.voice_for("ops", ["acme-alto", "acme-bass"])]


# --- rollout


def test_a_start_retried_from_before_voice_calls_is_answered_from_its_first_answer(meet):
    from backend.store import digest, encode

    cid = thread(meet)
    first = post(meet, f"threads/{cid}/calls", {"title": "Acme call"}, key="synthetic-start-key")
    stored = {"title": "Acme call", "speak_replies": False, "provider": "auto"}  # how an older server hashed it
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE idempotency SET request_hash=? WHERE key=?", (digest(encode(stored)), "synthetic-start-key"))
    again = post(meet, f"threads/{cid}/calls", {"title": "Acme call"}, key="synthetic-start-key")
    assert again["id"] == first["id"]
    refused = post(meet, f"threads/{cid}/calls", {"title": "Other"}, key="synthetic-start-key", expected=409)
    assert refused["error"]["code"] == "idempotency_conflict"


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
