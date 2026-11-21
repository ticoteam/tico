# pyright: strict
"""Voice in Tico Meet calls (docs/meet.md, Voice): speech cut at pauses, who answers, and the bots' spoken replies.

A call with `speak_replies` (every Tico Meet call unless its starter turned it off) is a voice call. Each person's speech
is cut into utterances where they pause (`Segmenter`); each utterance is transcribed and stored in the order it was
heard (backend/calls.py), and then routed here:

- In a 1:1 call with a bot (one bot attached, and the thread's people are exactly one person and that bot) the bot
  answers each turn of the person's, whole: the utterances it is cut into at their pauses ("um") are joined until they
  have been quiet TURN_END_MS since their last voiced frame, and then asked as one question.
- In any other voice call only a bot whose name was said (or typed) answers. The router's own decision windows do not
  run.
- A call has one open question at a time. A bot asked while another answers waits; a question is dropped with a note
  once QUESTION_WAIT_SECONDS have passed since it was heard.

A resident runner session streams a voice bot's answer. Each complete sentence can go to the Gateway's
text-to-speech while the model continues. The normal meeting reply hook posts the full text when the
answer ends. The open question expires ANSWER_SECONDS after it was heard. Playback is limited by
VOICE_CAP_MS and checks that the call is still live and the bot's attacher still has access.
A legacy runner's reply is spoken after its text commits.

One owner: the process that holds a call's recorder (call_media.LiveKitMedia, `running`) holds its voice too. Everything
here is that process's memory: the open question, the bots' states and captions, the replies already spoken. Another
process skips playback for a call it does not hold, and a restart starts the call's voice afresh (the recorder rejoins
from the sweep). The states readers see (`listening`, `waiting`, `thinking`, `making_voice`, `speaking`) are in the call
view as `voice.bots`, and each change writes a `call.updated` change so open views read the call again.

Each turn writes a `call_voice_turn` timing line to the support diagnostics ring.
"""

import asyncio
import logging
import math
import re
import threading
import time
from array import array
from collections import OrderedDict, deque
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import aclosing
from typing import Any

from . import background
from . import call_media as _call_media
from . import call_sessions as _call_sessions
from . import events as _events
from . import gateway_connection as _gateway_connection
from . import live_meetings as _live_meetings
from .store import H as _H
from .store import Problem

# Untyped modules, used through Any here (this module is strict; they are not yet).
call_media: Any = _call_media
call_sessions: Any = _call_sessions
events: Any = _events
gateway_connection: Any = _gateway_connection
live_meetings: Any = _live_meetings
H: Any = _H

log = logging.getLogger("tico.call_voice")

# --- pause detection (16 kHz mono 16-bit, as the recorder hears it)
BYTES_PER_MS = call_media.SAMPLE_RATE * 2 // 1000
FRAME_MS = 20
LOUD_RMS = 500  # a 20 ms frame this loud (of 32767) is speech; quieter is a pause
SILENCE_MS = call_media.END_PAUSE_MS
FRAGMENT_SILENCE_MS = 700  # a short phrase gets more time for the speaker to continue it
FRAGMENT_SPEECH_MS = 600
QUESTION_MAX_MS = 60_000  # an utterance is cut here even without a pause
PREROLL_MS = 200  # kept from before the first loud frame, so a soft first syllable is not clipped
MIN_SPEECH_MS = 200  # less speech than this (a cough, a click) is not sent
# A person who has said this much over a bot's voice has the floor: the bot stops at once and listens (barge-in). Longer
# than a cough or a click, short enough that the voice stops within a few hundred milliseconds of them starting.
BARGE_IN_MS = 300

# --- questions and answers
# A 1:1 call's turn ends at this much silence since the person's last voiced frame: speech is cut at every pause, an
# "um" too, and the pieces of one turn are asked as one question.
TURN_END_MS = 1200
ANSWER_SECONDS = 30  # a bot that has not answered this long after it was asked does not answer
QUESTION_WAIT_SECONDS = 30  # a question waiting behind another this long is dropped
NOTE_SECONDS = 10  # how long a note stays on the call view
TICK_SECONDS = 0.25
REPLIED_GRACE_SECONDS = 5  # a reply the hook has not handed over this long after it was stored is not spoken
REPEAT_NOTE = "Please repeat that"  # a question that could not be heard in time to answer

# --- speaking
SPOKEN_CHARS = 600  # the part of a reply that is spoken; the whole reply is in the thread
VOICE_CAP_MS = 30 * 60 * 1000  # the bots' spoken time in one call
SPOKEN_CHARS_PER_SECOND = 14  # a little slower than the Gateway's voices speak, so the estimate errs long
PLAYED_KEEP = 200  # reply ids remembered per call, so a reply is spoken once
FIRST_AUDIO_WINDOW = 100  # the spoken turns, this process's latest, that `first_audio_p90_ms` is read over
# An utterance heard this soon after a bot said nearly the same words is the bot's own voice coming back through a
# speaker (echo the browser did not cancel): it is never asked, or the bot would answer itself.
ECHO_SECONDS = 20
ECHO_SHARE = 0.8  # the share of an utterance's words that were just said for it to count as echo
ECHO_MIN_WORDS = 3  # "yes", "go on": too short to tell from echo, always asked

STATES = ("listening", "waiting", "thinking", "making_voice", "speaking")
SPOKEN_PROMPT = (
    "This meeting is a live voice call: your reply is read aloud to the people in it. Answer in plain spoken "
    "sentences, at most about 80 words, and say each thing once. No markdown, lists, headings, links, code or emoji.\n"
    "On a call you answer and discuss. You may read to answer a question. Start no work on your own: no tasks, "
    "messages to anyone, edits or settings changes unless the person clearly asks for that one action. When they "
    "do, say in one short sentence what you will do and wait for their yes. Never offer a list of things to do.\n"
    "When they ask you to look into something you cannot answer now, confirm it, then run "
    'tico call follow-up --call {call_id} "<what they asked for>" and say "I\'ll look into it." '
    "The task's answer comes back to this call.\n"
)


def loudness(frame: bytes) -> float:
    """The root mean square of one frame of 16-bit samples."""
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    if not samples:
        return 0.0
    return math.sqrt(math.sumprod(samples, samples) / len(samples))


class Segmenter:
    """One speaker's speech cut into utterances at their pauses, by loudness. Gaps on the audio timeline count as
    silence. `flush` ends an utterance whose speaker stopped sending (muted, left) or whose call is ending."""

    def __init__(self):
        self.rest = bytearray()  # the part of a frame not yet whole
        self.preroll = bytearray()
        self.pcm: bytearray | None = None  # the utterance under way, or None between utterances
        self.end: int | None = None  # byte position of the next sample on the audio timeline
        self.start = self.voiced = self.quiet = 0
        self.barges = 0  # utterances that reached BARGE_IN_MS of speech: each one takes the floor from a bot's voice
        self.at = 0.0  # wall time of the last piece fed (the call's idle flush reads it)
        # When the last loud frame arrived, and the utterance under way's first, on the call's clock in ms (`at`, the
        # arrival of the piece that carried it): what a 1:1 call's turn is measured by, whatever the audio timeline does.
        self.loud_at: int | None = None
        self.first_at = 0
        # (first loud frame's arrival, last loud frame's arrival) of each utterance cut, in order, on the call's clock in ms,
        # for the caller to take with it (calls.Calls.ingest): a 1:1 call joins pieces by them.
        self.spans: list[tuple[int, int]] = []
        # This recorder's timeline onto the call's clock in ms, for ordering clips (calls.Calls.ingest sets it).
        self.offset_ms = 0
        # (start_ms, pcm) of the utterance under way once its pause reaches PREROLL_MS: byte for byte the clip `_take`
        # returns if no loud frame comes first, so its transcription can start before the pause ends. None otherwise.
        self.early: tuple[int, bytes] | None = None

    def voiced_at(self) -> int | None:
        """When the last loud frame arrived, on the call's clock in ms, or None before any."""
        return self.loud_at

    @property
    def speaking(self) -> bool:
        return self.pcm is not None

    def _pause_ms(self) -> int:
        return FRAGMENT_SILENCE_MS if self.speaking and self.voiced < FRAGMENT_SPEECH_MS else SILENCE_MS

    def idle_seconds(self) -> float:
        return (self._pause_ms() - self.quiet) / 1000

    def feed(self, pcm: bytes, start_ms: int) -> list[tuple[int, bytes]]:
        """Take one piece of speech; returns the utterances it ended, as (start_ms, pcm)."""
        done: list[tuple[int, bytes]] = []
        start = start_ms * BYTES_PER_MS
        if self.end is not None:
            if start_ms == self.end // BYTES_PER_MS:
                start = self.end  # successive timestamps have millisecond precision, even for partial frames
            elif start > self.end:
                missing = start - self.end
                # A pause and a full preroll suffice even when the speaker goes quiet for minutes.
                pad = min(missing, (FRAGMENT_SILENCE_MS + PREROLL_MS + FRAME_MS) * BYTES_PER_MS)
                done = self._feed(bytes(pad), self.end // BYTES_PER_MS)
                if pad < missing:
                    self.rest.clear()
        self.end = start + len(pcm)
        done.extend(self._feed(pcm, start_ms))
        return done

    def _feed(self, pcm: bytes, start_ms: int) -> list[tuple[int, bytes]]:
        data = self.rest + pcm
        base = start_ms - len(self.rest) // BYTES_PER_MS
        step = FRAME_MS * BYTES_PER_MS
        whole = len(data) - len(data) % step
        done: list[tuple[int, bytes]] = []
        for at in range(0, whole, step):
            clip = self._frame(bytes(data[at : at + step]), base + at // BYTES_PER_MS)
            if clip:
                done.append(clip)
        self.rest = bytearray(data[whole:])
        return done

    def _frame(self, frame: bytes, at_ms: int) -> tuple[int, bytes] | None:
        loud = loudness(frame) >= LOUD_RMS
        if self.pcm is None:
            if not loud:
                self.preroll += frame
                del self.preroll[: max(0, len(self.preroll) - PREROLL_MS * BYTES_PER_MS)]
                return None
            self.pcm = bytearray(self.preroll) + frame
            self.first_at = self.loud_at = round(self.at * 1000)
            self.start = at_ms - len(self.preroll) // BYTES_PER_MS
            self.preroll = bytearray()
            self.voiced, self.quiet = FRAME_MS, 0
        else:
            self.pcm += frame
            if loud:
                self.loud_at = round(self.at * 1000)
                self.voiced, self.quiet = self.voiced + FRAME_MS, 0
                self.early = None
                if self.voiced == BARGE_IN_MS:
                    self.barges += 1
            else:
                self.quiet += FRAME_MS
                if self.quiet == PREROLL_MS and self.voiced >= MIN_SPEECH_MS:
                    self.early = (self.start, bytes(self.pcm))
        if self.quiet >= self._pause_ms() or len(self.pcm) >= QUESTION_MAX_MS * BYTES_PER_MS:
            return self._take()
        return None

    def _take(self) -> tuple[int, bytes] | None:
        pcm, start, voiced, quiet = self.pcm, self.start, self.voiced, self.quiet
        self.pcm, self.voiced, self.quiet, self.early = None, 0, 0, None
        if pcm is None or voiced < MIN_SPEECH_MS:
            return None
        # The pause that ended it is not speech: a short tail of it stays, so the last word is not cut.
        trailing = max(0, quiet - PREROLL_MS) * BYTES_PER_MS
        self.spans.append((self.first_at, self.loud_at if self.loud_at is not None else self.first_at))
        return start, bytes(pcm[: len(pcm) - trailing] if trailing else pcm)

    def flush(self) -> tuple[int, bytes] | None:
        """The utterance under way, ended now (or None)."""
        if self.pcm is None:
            self.rest = bytearray()
            return None
        self.pcm += self.rest
        self.rest = bytearray()
        return self._take()


def spoken(text: object) -> str:
    """A reply as it is read aloud: markdown marks and links taken out, cut at SPOKEN_CHARS on a sentence's end."""
    text = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+•]|\d+[.)]|#+|>)\s+", "", text)
    text = re.sub(r"[*_`#|~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= SPOKEN_CHARS:
        return text
    cut = text[:SPOKEN_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > SPOKEN_CHARS // 3 else cut.rsplit(" ", 1)[0] + "…"


def words(text: str) -> list[str]:
    """A sentence's words, lower case, without punctuation: what two sayings of it share."""
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def sentences(text: str) -> list[str]:
    """Text split into sentences to speak one at a time; a fragment too short to be worth a request joins the next. A
    sentence the reply already said (a model that restates its answer after a tool call) is said once."""
    parts: list[str] = []
    seen: set[tuple[str, ...]] = set()
    carry = ""
    for piece in re.split(r"(?<=[.!?…])\s+", text):
        key = tuple(words(piece))
        if key and key in seen:
            continue
        seen.add(key)
        carry = (carry + " " + piece).strip() if carry else piece.strip()
        if len(carry) >= 24:
            parts.append(carry)
            carry = ""
    if carry:
        if parts and len(carry) < 24:
            parts[-1] += " " + carry
        else:
            parts.append(carry)
    return parts


def echoes(heard: str, said: list[str]) -> bool:
    """Whether an utterance is a bot's own recent words heard back through someone's speaker: nearly all its words were
    just said. A short one ("yes") never counts."""
    found = words(heard)
    if len(found) < ECHO_MIN_WORDS:
        return False
    known = {word for sentence in said for word in words(sentence)}
    return sum(word in known for word in found) >= ECHO_SHARE * len(found)


class ReplyStream:
    """A bounded producer of complete spoken sentences. Writers hold the call's voice lock."""

    def __init__(self, timing: dict[str, Any]) -> None:
        self.timing = timing
        self.pending = ""
        self.raw = ""
        self.consumed = 0
        self.count = 0
        self.closed = False
        self.said: set[tuple[str, ...]] = set()  # the sentences sent, by their words: none is said twice
        self.buffer: list[str] = []
        self.send: Callable[[str | None], None] = self._buffer

    def _buffer(self, sentence: str | None) -> None:
        if sentence is not None:
            self.buffer.append(sentence)

    def connect(self, send: Callable[[str | None], None]) -> None:
        self.send = send
        for sentence in self.buffer:
            send(sentence)
        self.buffer.clear()
        if self.closed:
            send(None)

    def add(self, text: str, at: float) -> None:
        if self.closed or len(self.raw) >= SPOKEN_CHARS:
            return
        chunk = text[: SPOKEN_CHARS - len(self.raw)]
        self.raw += chunk
        self.pending += chunk

        self._drain(at)

    def _drain(self, at: float) -> None:
        while boundary := re.search(r"[.!?…](?:[\"'”’]+)?\s+", self.pending):
            end = boundary.end()
            sentence, self.pending = self.pending[:end], self.pending[end:]
            self.consumed += end
            self._sentence(sentence, at)

    def _sentence(self, text: str, at: float) -> None:
        clean = spoken(text)
        if not clean or live_meetings._is_pass(clean):
            return
        key = tuple(words(clean))
        if key in self.said:
            return
        self.said.add(key)
        left = SPOKEN_CHARS - self.count
        if left:
            self.timing.setdefault("sentence", at)
            self.count += len(clean[:left])
            self.send(clean[:left])

    def finish(self, full: str, at: float) -> None:
        if self.closed:
            return
        prefix = self.raw[: self.consumed].strip()
        full = full.strip()
        if full.startswith(prefix):
            self.pending = full[len(prefix) : SPOKEN_CHARS]
            self._drain(at)
        self._sentence(self.pending, at)
        self.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.send(None)


class CallVoice:
    """One call's voice state, in this process. Guarded by `lock`; no transaction is opened while it is held."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        # The question being answered: {"bot", "turn", "asked" (when it was heard), "stored" (when its text was stored,
        # for an utterance), "phase", "reply", "replied_at", "handed" (when its reply's commit handed it here)}.
        self.open: dict[str, Any] | None = None
        # bot -> the question (Voice.route) it was asked while another bot answered
        self.waiting: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.follow_ups: deque[tuple[str, str, str]] = deque()
        # A 1:1 call's turns not yet asked, oldest first: {"bot", "question": its latest piece with `text` its pieces
        # joined}. A piece joins the last one or starts a new one (`_gather`); each is asked once the person is quiet.
        self.gathering: list[dict[str, Any]] = []
        self.states: dict[str, tuple[str, str | None]] = {}  # bot -> (state, caption)
        self.note: tuple[str, float] | None = None  # (text, wall time)
        self.played: deque[str] = deque(maxlen=PLAYED_KEEP)
        self.heard: deque[tuple[float, str]] = deque(maxlen=40)  # (when, sentence) the bots began saying, for echo
        self.generation = 0  # bumped by a cancel of the whole call: a playback that started before it stops
        self.epochs: dict[str, int] = {}  # bot -> cancels of that bot alone: only its own playback stops


def _epoch(voice: CallVoice, bot: str) -> tuple[int, int]:
    """Where a bot's cancels stand (voice.lock held): a reply is spoken only while this has not moved."""
    return voice.generation, voice.epochs.get(bot, 0)


def estimate_ms(text: str) -> int:
    """How long a sentence will take to say, from its length: what the voice cap is checked against before any of it
    is made."""
    return len(text) * 1000 // SPOKEN_CHARS_PER_SECOND


class Voice:
    """The voice of every call this process holds. `clock` is monotonic seconds, swapped in tests; `tick` is what the
    ticker thread runs, and tests call it directly."""

    def __init__(self, app: Any, store: Any, calls: Any) -> None:
        self.app, self.store, self.calls = app, store, calls
        self.lock = threading.Lock()
        self.voices: dict[str, CallVoice] = {}
        self.clock: Callable[[], float] = time.monotonic
        self.ticking = True  # tests turn the thread off and tick by hand
        self.ticker: threading.Thread | None = None
        self.wake = threading.Event()
        self.stopped = False
        self.makers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tico-meet-tts")
        self.players = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tico-meet-voice")
        self.playing: list[Future[None]] = []  # playback futures, for settle
        self.first_audio: deque[int] = deque(maxlen=FIRST_AUDIO_WINDOW)  # first_audio_ms of the latest spoken turns
        self.first_audio_lock = threading.Lock()

    # --- state

    def _call(self, call_id: str) -> CallVoice:
        """The call's voice state, made when it has none."""
        with self.lock:
            found = self.voices.get(call_id)
            if found is None:
                found = self.voices[call_id] = CallVoice()
                self._start_ticker()
            return found

    def _found(self, call_id: str) -> CallVoice | None:
        with self.lock:
            return self.voices.get(call_id)

    def _start_ticker(self) -> None:
        if self.ticking and (self.ticker is None or not self.ticker.is_alive()) and not self.stopped:
            self.ticker = background.start("tico-meet-voice-tick", self._run)

    def _run(self) -> None:
        report: Any = _report()

        while not self.stopped:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - one bad pass must not stop the call's voice
                report("calls_voice_tick", "voice", exc, logger="tico.call_voice")
            with self.lock:
                if not self.voices:
                    self.ticker = None
                    return
            self.wake.wait(TICK_SECONDS)
            self.wake.clear()

    def stop(self) -> None:
        self.stopped = True
        self.wake.set()
        with self.lock:
            ids = list(self.voices)
        for call_id in ids:
            self.forget(call_id)

    def _set(self, voice: CallVoice, bot: str, state: str, caption: str | None = None) -> bool:
        with voice.lock:
            if voice.states.get(bot) == (state, caption):
                return False
            voice.states[bot] = (state, caption)
            return True

    def _note(self, voice: CallVoice, text: str) -> None:
        with voice.lock:
            voice.note = (text, self.clock())

    def view(self, call_id: str, bots: list[str], voiced: bool) -> dict[str, Any] | None:
        """What the call view shows of its voice: each attached bot's state and caption, and the latest note. None for a
        call without voice."""
        if not voiced:
            return None
        voice = self._found(call_id)
        states: dict[str, tuple[str, str | None]] = {}
        note: str | None = None
        if voice is not None:
            with voice.lock:
                states = dict(voice.states)
                if voice.note and self.clock() - voice.note[1] < NOTE_SECONDS:
                    note = voice.note[0]
        shown = [(bot, *states.get(bot, ("listening", None))) for bot in bots]
        return {
            "bots": [{"slug": bot, "state": state, "caption": caption} for bot, state, caption in shown],
            "note": note,
        }

    def touch(self, call_id: str, c: Any = None) -> None:
        """A `call.updated` change, so open views read the call again (its voice states moved)."""

        def write(c: Any) -> None:
            row = c.execute("SELECT thread_id,connector_actor FROM calls WHERE id=?", (call_id,)).fetchone()
            if row:
                c.execute(
                    "INSERT INTO changes(topic,kind,subject_id,conversation_id,actor,created_at) "
                    "VALUES('threads','call.updated',?,?,?,?)",
                    (call_id, row["thread_id"], row["connector_actor"], H.now()),
                )

        if c is not None:
            write(c)
            return
        with self.store.transaction() as c:
            write(c)

    def warm(self) -> None:
        """Read the Gateway's voices ahead of the first reply (a call starting, a question asked), so no reply waits on
        them. Off the caller's thread; nothing is read when they are already kept."""

        def read() -> None:
            try:
                with self.store.read() as c:
                    key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
                if key and call_media.cached_voices(base) is None:
                    call_media.voices(base, key)
            except Exception as exc:  # noqa: BLE001 - a reply without the list uses the Gateway's own voice
                report: Any = _report()
                report("calls_voice", "voices", exc, logger="tico.call_voice")

        self.makers.submit(read)

    # --- questions

    @staticmethod
    def answers_all(c: Any, row: Any) -> str | None:
        """The bot that answers every utterance in this call, when it is a 1:1 call with a bot: one bot attached, and
        the thread's people are exactly one person and that bot. None otherwise (a group DM answers only when named)."""
        from . import threads as _threads

        threads: Any = _threads

        bots = [
            r["bot"]
            for r in c.execute("SELECT bot FROM live_meeting_bots WHERE meeting_id=?", (row["live_meeting_id"],))
        ]
        if len(bots) != 1:
            return None
        try:
            root = threads.root_of(c, threads.thread_row(c, row["thread_id"]))
        except Problem:
            return None
        members = [str(actor) for actor in threads._dm_members(c, root)]
        people = [actor for actor in members if actor.startswith("human:")]
        in_thread = [actor for actor in members if actor.startswith("bot:")]
        return bots[0] if len(people) == 1 and in_thread == ["bot:" + bots[0]] else None

    def route(self, c: Any, row: Any, question: dict[str, Any]) -> list[str]:
        """Inside the transaction that stored a question (a transcribed utterance, a transcript line, a chat message):
        ask the bots that answer it, one open question per call. `question` is {"text", "source_key", "seq", "at_ms",
        "heard" (this clock's time it was heard), "spoken" (an utterance: in a 1:1 call every one is asked)}. A question
        heard ANSWER_SECONDS ago or more (a transcription backlog) is not asked. Returns the bots asked now."""
        if row["ending_at"] or row["state"] != "live":
            return []
        if question.get("spoken"):
            question["stored"] = self.clock()  # an utterance's text is stored in the transaction that calls here
            if self._echo(row["id"], question["text"]):
                log.info("call_voice_echo call=%s seq=%s", row["id"], question.get("seq"))
                return []
        rid = row["live_meeting_id"]
        only = self.answers_all(c, row) if question.get("spoken") else None
        targets = [only] if only else live_meetings._named_bots(c, rid, question["text"])
        if not targets:
            return []
        voice = self._call(row["id"])
        if self.clock() - question["heard"] >= ANSWER_SECONDS:
            self._note(voice, REPEAT_NOTE)
            self.touch(row["id"], c)
            return []
        self.warm()
        asked: list[str] = []
        if only:
            # Gathered once its clip is committed, and asked from there (calls.Calls._settle, `ask_ready`).
            c.after_commit.append(lambda: self._gathered(voice, only, question))
            return []
        with voice.lock:
            for bot in targets:
                if voice.open is not None or asked:
                    voice.waiting.pop(bot, None)
                    voice.waiting[bot] = question
                    if not (voice.open and voice.open["bot"] == bot):
                        self._set(voice, bot, "waiting")
                    continue
                if self._ask(c, voice, rid, bot, question):
                    asked.append(bot)
        self.touch(row["id"], c)
        return asked

    @staticmethod
    def _gather(voice: CallVoice, bot: str, question: dict[str, Any]) -> None:
        """Add a 1:1 call's committed piece of speech to the person's turn (voice.lock held): to the last one when its
        first voiced frame follows that turn's last voiced frame by less than TURN_END_MS on the call's clock, else as a
        new turn after it. Turns keep their boundaries while they wait."""
        question["gathered"] = True
        last = voice.gathering[-1] if voice.gathering else None
        if last is not None and last["bot"] == bot:
            before = last["question"]
            # Its first voiced frame after the last one before, both on the call's clock in ms (Calls.ingest).
            frm, to = question.get("voiced_from"), before.get("voiced_to")
            if frm is not None and to is not None and frm - to < TURN_END_MS:
                question["text"] = f"{before['text']} {question['text']}".strip()
                last["question"] = question
                return
        voice.gathering.append({"bot": bot, "question": question})

    def _gathered(self, voice: CallVoice, bot: str, question: dict[str, Any]) -> None:
        """After its clip's commit: gather a 1:1 call's piece (`_gather`)."""
        with voice.lock:
            self._gather(voice, bot, question)
            if voice.open is None:
                self._set(voice, bot, "listening")

    def _reserve(self, call_id: str, voice: CallVoice) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Inside the transaction that asks it: take a 1:1 call's oldest gathered turn when it may be asked, as
        (turn, reservation), the reservation held as the call's open question until after the commit; else None. Speech
        activity is checked and the turn reserved in one critical section, calls.lock then voice.lock (ingest's order),
        both let go before any SQL: nothing open, nothing being said, transcribed or stored, and the person quiet
        TURN_END_MS since their last voiced frame on the call's clock."""
        with self.calls.lock:
            pending = self.calls.turn_pending_locked(call_id)
            voiced = self.calls.voiced.get(call_id)
            with voice.lock:
                if pending or voice.open is not None or not voice.gathering:
                    return None
                if voiced is not None and round(self.clock() * 1000) - voiced < TURN_END_MS:
                    return None
                turn = voice.gathering.pop(0)
                reservation = {"bot": turn["bot"], "phase": "reserved", "asked": turn["question"]["heard"]}
                voice.open = reservation
                return turn, reservation

    def _release(self, voice: CallVoice, reservation: dict[str, Any], turn: dict[str, Any] | None) -> None:
        """Let a reservation go when its write failed anywhere: that exact one, and the turn back at the head of the
        queue while it can still be asked."""
        with voice.lock:
            if voice.open is reservation:
                voice.open = None
                if turn is not None and self.clock() - turn["question"]["heard"] < QUESTION_WAIT_SECONDS:
                    voice.gathering.insert(0, turn)

    def _dispatch(
        self, c: Any, voice: CallVoice, row: Any, turn: dict[str, Any], reservation: dict[str, Any]
    ) -> list[str] | None:
        """Write a reserved gathered turn inside the transaction that reserved it, as a 1:1 turn whatever has joined the
        call since; it becomes the call's open question after the commit (`_published`). voice.lock is held only to
        read or update the reservation, never across SQL. Returns the bots asked ([] when it went unasked), or None
        when the reservation was canceled meanwhile."""
        with voice.lock:
            canceled = voice.open is not reservation
        if canceled:
            return None
        bot, question = turn["bot"], turn["question"]
        live = row["state"] == "live" and not row["ending_at"]
        if not live or self.clock() - question["heard"] >= QUESTION_WAIT_SECONDS:
            name = _name(c, bot) if live else ""
            with voice.lock:
                if voice.open is reservation:
                    voice.open = None
                self._set(voice, bot, "listening")
                if live:
                    self._note(voice, f"{name} missed a question")
            return []
        selected, warm = self._enqueue(c, row, bot, question)
        if not selected:
            with voice.lock:
                if voice.open is reservation:
                    voice.open = None
                self._set(voice, bot, "listening")
            return []
        if warm:
            events.queue_voice_inputs(c, self.store, row["id"], bot)
        turn_id, call_id = selected[0]["id"], row["id"]
        c.after_commit.append(lambda: self._published(call_id, voice, reservation, bot, turn_id, question))
        return [bot]

    def _published(
        self, call_id: str, voice: CallVoice, reservation: dict[str, Any], bot: str, turn_id: str, question: Any
    ) -> None:
        """After the commit that wrote it: the turn is the call's open question, while its reservation stands; one
        canceled meanwhile is passed, its SQL run apart, after the lock is let go."""
        with voice.lock:
            canceled = voice.open is not reservation
            if not canceled:
                self._opened(voice, bot, turn_id, question)
        if canceled:
            self.makers.submit(self._pass_canceled, call_id, turn_id)

    def _pass_canceled(self, call_id: str, turn_id: str) -> None:
        report: Any = _report()
        try:
            with self.store.transaction(notify=True) as c:
                turn = c.execute("SELECT * FROM live_meeting_turns WHERE id=?", (turn_id,)).fetchone()
                if turn:
                    live_meetings._finish_pass(c, turn["meeting_id"], turn, "canceled")
        except Exception as exc:  # noqa: BLE001 - the turn then ends at its deadline
            report("calls_voice", call_id, exc, logger="tico.call_voice")

    def ask_ready(self, call_id: str) -> None:
        """Ask a 1:1 call's oldest gathered turn if it may be asked now: from the ticker, and once a clip is stored
        (calls.Calls._settle). A failure anywhere, after its write too, puts the turn back at the head."""
        voice = self._found(call_id)
        if voice is None:
            return
        with voice.lock:
            if voice.open is not None or not voice.gathering:
                return
        reserved = None
        try:
            with self.store.transaction(notify=True) as c:
                row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
                reserved = self._reserve(call_id, voice) if row is not None else None
                if reserved is None:
                    return
                if self._dispatch(c, voice, row, *reserved) is not None:  # asked, or unasked: the view changed
                    self.touch(call_id, c)
        except Exception:
            if reserved is not None:
                self._release(voice, reserved[1], reserved[0])
            raise

    def _echo(self, call_id: str, text: str) -> bool:
        """Whether a person's utterance is a bot's own voice heard back: nearly the words a bot began saying in the last
        ECHO_SECONDS."""
        voice = self._found(call_id)
        if voice is None:
            return False
        now = self.clock()
        with voice.lock:
            said = [sentence for at, sentence in voice.heard if now - at <= ECHO_SECONDS]
        return echoes(text, said)

    def _enqueue(self, c: Any, row: Any, bot: str, question: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Put one bot's turn on the queue (inside a transaction, no lock held): the turns made, and whether it goes to a
        resident session. A gathered 1:1 turn keeps its joined question on the turn (live_meeting_turns.question)."""
        warm = call_sessions.ensure(c, self.store.settings, row, bot)
        selected, _skipped = live_meetings._enqueue(
            c,
            row["live_meeting_id"],
            [bot],
            source_key=question["source_key"],
            transcript_seq=question.get("seq"),
            at_ms=question.get("at_ms"),
            named=True,
            voice_input=question["text"] if warm else None,
            question=question["text"] if question.get("gathered") else None,
            voice_expires_at=H.shift(H.now(), seconds=question["heard"] + ANSWER_SECONDS - self.clock())
            if warm
            else None,
        )
        return selected, warm

    def _opened(self, voice: CallVoice, bot: str, turn_id: str, question: dict[str, Any]) -> None:
        """The call's open question is now this turn (voice.lock held)."""
        voice.open = {
            "bot": bot,
            "turn": turn_id,
            "asked": question["heard"],
            "stored": question.get("stored"),
            "phase": "thinking",
        }
        self._set(voice, bot, "thinking")

    def _ask(self, c: Any, voice: CallVoice, rid: str, bot: str, question: dict[str, Any]) -> bool:
        """Put one bot's turn on the queue as the call's open question (voice.lock held, inside a transaction)."""
        row = c.execute("SELECT * FROM calls WHERE live_meeting_id=?", (rid,)).fetchone()
        selected, warm = self._enqueue(c, row, bot, question)
        if not selected:
            self._set(voice, bot, "listening")
            return False
        if warm:
            events.queue_voice_inputs(c, self.store, row["id"], bot)
        self._opened(voice, bot, selected[0]["id"], question)
        return True

    def busy(self, call_id: str) -> bool:
        """Whether the call has a question open (live_meetings asks before a typed line opens one of its own)."""
        voice = self._found(call_id)
        if voice is None:
            return False
        with voice.lock:
            return voice.open is not None

    def tick(self) -> None:
        """One pass over every call this process voices: a turn that ended, one past its deadline, and the next waiting
        question."""
        with self.lock:
            ids = list(self.voices)
        for call_id in ids:
            self._tick_call(call_id)

    def _tick_call(self, call_id: str) -> None:
        voice = self._found(call_id)
        if voice is None:
            return
        now = self.clock()
        self.ask_ready(call_id)
        with voice.lock:
            current = dict(voice.open) if voice.open else None
            waiting = bool(voice.waiting)
            follow_ups = bool(voice.follow_ups)
            stale_note = voice.note and now - voice.note[1] >= NOTE_SECONDS
        if current is None and not waiting:
            if stale_note:
                with voice.lock:
                    voice.note = None
                self.touch(call_id)
            if follow_ups:
                self._next_follow_up(call_id, voice)
            return
        # Read first: the writer gate is taken only when there is something to write.
        with self.store.read() as c:
            row = c.execute("SELECT state,ending_at FROM calls WHERE id=?", (call_id,)).fetchone()
            status = None
            if current and current["phase"] == "thinking":
                turn = c.execute("SELECT status FROM live_meeting_turns WHERE id=?", (current["turn"],)).fetchone()
                status = turn["status"] if turn else "skipped"
        if not row or row["state"] != "live" or row["ending_at"]:
            self.forget(call_id)
            return
        due = current is None  # a question is waiting and none is open
        if status in ("pending", "claimed"):
            due = now - current["asked"] >= ANSWER_SECONDS if current else False
        elif status == "skipped":
            due = True
        elif status == "replied":
            # The reply hook hands a stored reply over after its commit; one never handed over (not this process's
            # call, or a lost hand-off) frees the call after a moment.
            with voice.lock:
                if voice.open is not None and voice.open.get("replied_at") is None:
                    voice.open["replied_at"] = now
                replied_at = voice.open.get("replied_at") if voice.open else None
                due = replied_at is not None and now - replied_at >= REPLIED_GRACE_SECONDS
        if due:
            self._move(call_id, voice, current, now)

    def _move(self, call_id: str, voice: CallVoice, current: dict[str, Any] | None, now: float) -> None:
        """The tick's writes: end a turn past its deadline, free the call from one that is over, ask the next."""
        moved = False
        with self.store.transaction(notify=True) as c:
            row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] != "live" or row["ending_at"]:
                c.after_commit.append(lambda: self.forget(call_id))
                return
            rid = row["live_meeting_id"]
            if current and current["phase"] == "thinking":
                turn = c.execute("SELECT * FROM live_meeting_turns WHERE id=?", (current["turn"],)).fetchone()
                status = turn["status"] if turn else "skipped"
                reason = turn["skip_reason"] if turn else None
                ended = ("skipped", None)
                past_due = status in ("pending", "claimed") and now - current["asked"] >= ANSWER_SECONDS
                if past_due:
                    live_meetings._finish_pass(c, rid, turn, "voice_deadline")
                    status, reason = "skipped", "voice_deadline"
                if reason == "voice_deadline":
                    self._note(voice, f"{_name(c, current['bot'])} couldn't answer in time")
                if status == "skipped":
                    # A resident still answering a skipped turn stops at once, not at its next backup read.
                    events.queue_voice_inputs(c, self.store, call_id, current["bot"])
                if status == "replied":
                    with voice.lock:
                        replied_at = voice.open.get("replied_at") if voice.open else None
                    if replied_at is not None and now - replied_at >= REPLIED_GRACE_SECONDS:
                        status, ended = "skipped", ("text_only", "not_handed_over")
                if status == "skipped":
                    with voice.lock:
                        if voice.open and voice.open.get("turn") == current["turn"]:
                            # A delta may have opened the stream since the ticker read the turn.
                            ended_turn = voice.open
                            voice.open = None
                            self._set(voice, current["bot"], "listening")
                            moved = True
                            if stream := ended_turn.get("stream"):
                                ended_turn["skip_reason"] = reason or ended[1]
                                stream.close()
                                voice.epochs[current["bot"]] = voice.epochs.get(current["bot"], 0) + 1
                                c.after_commit.append(lambda: self.calls.media.hush(call_id))
                            else:
                                self._log_turn(c, call_id, ended_turn, *ended)
            with voice.lock:
                if voice.open is None:
                    while voice.waiting:
                        bot, question = voice.waiting.popitem(last=False)
                        moved = True
                        if not c.execute(
                            "SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (rid, bot)
                        ).fetchone():
                            voice.states.pop(bot, None)
                            continue
                        if now - question["heard"] >= QUESTION_WAIT_SECONDS:
                            self._set(voice, bot, "listening")
                            self._note(voice, f"{_name(c, bot)} missed a question")
                            continue
                        if self._ask(c, voice, rid, bot, question):
                            break
            if moved:
                self.touch(call_id, c)
        if moved:
            self.wake.set()  # a 1:1 turn gathered while this one was open is asked on the next tick, not a tick later

    # --- speaking

    def delta(self, call_id: str, bot: str, turn_id: str, text: str) -> None:
        """Feed the open question's stream after the runner's event batch commits."""
        voice = self._found(call_id)
        if voice is None or self.calls.media is None or not self.calls.media.running(call_id):
            return
        at = self.clock()
        with voice.lock:
            current = voice.open
            if (
                not current
                or current["bot"] != bot
                or current["phase"] != "thinking"
                or current["turn"] != turn_id
                or at >= current["asked"] + ANSWER_SECONDS
            ):
                return
            stream = current.get("stream")
            if stream is None:
                stream = current["stream"] = ReplyStream(current)
                current.update(token=at, handed=at, reply=turn_id)
                self._set(voice, bot, "making_voice")
                self._play(
                    call_id, bot, turn_id, stream, _epoch(voice, bot), current["asked"] + ANSWER_SECONDS, current
                )
            stream.add(text, at)

    def _play(
        self,
        call_id: str,
        bot: str,
        reply_id: str,
        text: str | ReplyStream,
        epoch: tuple[int, int],
        deadline: float | None,
        timing: dict[str, Any],
    ) -> None:
        self.makers.submit(self.touch, call_id)
        future = self.players.submit(self._speak, call_id, bot, reply_id, text, epoch, deadline, timing)
        with self.lock:
            self.playing = [f for f in self.playing if not f.done()] + [future]

    def follow_up(self, call_id: str, bot: str, reply_id: str, text: str) -> None:
        """Queue an already committed task answer in the process holding the call's voice."""
        if self.calls.media is None or not self.calls.media.running(call_id):
            return
        voice = self._call(call_id)
        with voice.lock:
            if (
                reply_id in voice.played
                or (voice.open and voice.open.get("reply") == reply_id)
                or any(item[1] == reply_id for item in voice.follow_ups)
            ):
                return
            voice.follow_ups.append((bot, reply_id, text))
        self.wake.set()

    def _next_follow_up(self, call_id: str, voice: CallVoice) -> None:
        if not self.calls.media.running(call_id) or self.calls.speech_pending(call_id):
            return
        with voice.lock:
            # A person's turn being gathered has the floor too: a follow-up waits until it is asked.
            if voice.open is not None or voice.waiting or voice.gathering or not voice.follow_ups:
                return
            bot, reply_id, text = voice.follow_ups.popleft()
            if reply_id in voice.played:
                return
            epoch = _epoch(voice, bot)
            voice.open = {"bot": bot, "phase": "making_voice", "reply": reply_id, "follow_up": True}
            self._set(voice, bot, "making_voice")
        self._play(call_id, bot, reply_id, text, epoch, None, {})

    def human_speaking(self, call_id: str) -> None:
        """A person has said BARGE_IN_MS over the call's voice: the voice stops at once and the call listens (barge-in).
        A follow-up being read yields and is read again at the next pause. A bot answering (its words streaming or
        playing) is cut off: what it has not said yet is never said, its turn ends as `interrupted`, and its resident
        stops writing, so the person's next words are asked at once. A bot still thinking, with nothing to say yet,
        goes on: what the person says next waits behind it. Called with calls.lock held: it opens no transaction."""
        voice = self._found(call_id)
        if voice is None:
            return
        with voice.lock:
            current = voice.open
            if current is None:
                return
            if current.get("follow_up"):
                current["yielded"] = True
                self.calls.media.hush(call_id)
                return
            if current.get("stream") is None and current["phase"] != "making_voice":
                return
            bot = current["bot"]
            voice.epochs[bot] = voice.epochs.get(bot, 0) + 1  # playback of this answer stops at its next frame
            current["skip_reason"] = "interrupted"
            if stream := current.get("stream"):
                stream.close()
            voice.open = None
            self._set(voice, bot, "listening")
            turn = current.get("turn")
        self.calls.media.hush(call_id)
        if turn:
            self.makers.submit(self._interrupted, call_id, bot, turn)
        self.wake.set()

    def _interrupted(self, call_id: str, bot: str, turn_id: str) -> None:
        """A turn cut off by the person: if its reply was not stored yet it ends `interrupted`, and its resident is woken
        to drop the rest of it."""
        report: Any = _report()
        try:
            with self.store.transaction(notify=True) as c:
                turn = c.execute("SELECT * FROM live_meeting_turns WHERE id=?", (turn_id,)).fetchone()
                if turn and live_meetings._finish_pass(c, turn["meeting_id"], turn, "interrupted"):
                    events.queue_voice_inputs(c, self.store, call_id, bot)
                self.touch(call_id, c)
        except Exception as exc:  # noqa: BLE001 - the voice already stopped; a failed write leaves the turn to its deadline
            report("calls_voice", call_id, exc, logger="tico.call_voice")

    def reply(self, call_id: str, bot: str, reply_id: str, text: str, turn_id: str | None = None) -> None:
        """A bot's reply, stored and posted (after its commit): speak it, once, if it answers the call's open question
        (`turn_id`), this process holds the call, and its turn's deadline has not passed. It runs as an after-commit
        hook, while the writer gate is still held: it opens no transaction itself (the change rows are written from a
        worker)."""
        voice = self._call(call_id)
        with voice.lock:
            if reply_id in voice.played:
                return
            voice.played.append(reply_id)
            if (
                voice.open is None
                or voice.open["phase"] != "thinking"
                or voice.open["bot"] != bot
                or voice.open["turn"] != turn_id
            ):
                # A reply that is not the open question's (a late one, one to a typed line): it stays text.
                return
            deadline = voice.open["asked"] + ANSWER_SECONDS
            if stream := voice.open.get("stream"):
                stream.finish(text, self.clock())
                voice.open["phase"] = "making_voice"
                if voice.open.get("playback_done"):
                    voice.open = None
                    self._set(voice, bot, "listening")
                    self.wake.set()
                return
            voice.open["handed"] = self.clock()
            timing = dict(voice.open)
            if self.clock() >= deadline:
                # Stored after its turn's deadline (it reached the writer before the ticker did): it stays text.
                self.makers.submit(self._log_turn, None, call_id, timing, "text_only", "late")
                voice.open = None
                self._set(voice, bot, "listening")
                self.makers.submit(self._late, call_id, bot)
                self.wake.set()
                return
            if self.calls.media is None or not self.calls.media.running(call_id):
                log.info("A call's reply was not spoken: this process does not hold call %s", call_id)
                self.makers.submit(self._log_turn, None, call_id, timing, "text_only", "not_held")
                voice.open = None
                self._set(voice, bot, "listening")
                self.makers.submit(self.touch, call_id)
                return
            voice.open.update(phase="making_voice", reply=reply_id)
            self._set(voice, bot, "making_voice")
            epoch = _epoch(voice, bot)
        self._play(call_id, bot, reply_id, text, epoch, deadline, timing)

    def _late(self, call_id: str, bot: str) -> None:
        """Say on the call that a bot's answer came too late to be spoken (its text is in the thread)."""
        voice = self._call(call_id)
        with self.store.transaction() as c:
            self._note(voice, f"{_name(c, bot)} couldn't answer in time")
            self.touch(call_id, c)

    def _speak(
        self,
        call_id: str,
        bot: str,
        reply_id: str,
        text: str | ReplyStream,
        epoch: tuple[int, int],
        deadline: float | None,
        timing: dict[str, Any],
    ) -> None:
        report: Any = _report()
        voice = self._found(call_id)
        if voice is None:
            if deadline is not None:
                self._log_turn(None, call_id, timing, "skipped", "canceled")
            return
        media = self.calls.media
        follow_up = deadline is None
        played = 0
        mark = None
        why = "not_spoken"
        stream = text if isinstance(text, ReplyStream) else None
        try:
            with self.store.read() as c:
                key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
                used = c.execute("SELECT voice_ms FROM calls WHERE id=?", (call_id,)).fetchone()
            budget = VOICE_CAP_MS - (int(used[0] or 0) if used else 0)
            mark = media.mark(call_id)  # a hush after standing was checked also stops this reply
            if follow_up and mark is None:
                return
            if not key or not self._standing(call_id, bot, voice, epoch):
                why = "no_voice" if not key else "canceled"
                return
            names = call_media.cached_voices(base)
            if names is None:
                self.warm()
            name = call_media.voice_for(bot, names or [])
            parts: list[str] = []
            at_cap = False

            async def incoming() -> AsyncGenerator[str]:
                if stream is None:
                    for sentence in sentences(spoken(text)):
                        yield sentence
                    return
                loop = asyncio.get_running_loop()
                ready: asyncio.Queue[str | None] = asyncio.Queue()

                def send(sentence: str | None) -> None:
                    loop.call_soon_threadsafe(ready.put_nowait, sentence)

                with voice.lock:
                    stream.connect(send)
                try:
                    while (sentence := await ready.get()) is not None:
                        yield sentence
                finally:
                    with voice.lock:
                        stream.send = lambda sentence: None

            async def sentences_to_say() -> AsyncGenerator[str]:
                nonlocal at_cap, why
                planned = 0
                async with aclosing(incoming()) as pending:
                    async for sentence in pending:
                        with voice.lock:
                            canceled = _epoch(voice, bot) != epoch
                        if (
                            canceled
                            or stream is not None
                            and not await asyncio.to_thread(self._standing, call_id, bot, voice, epoch)
                        ):
                            why = "canceled"
                            return
                        planned += estimate_ms(sentence)
                        if planned > budget:
                            at_cap = True
                            return
                        timing.setdefault("sentence", self.clock())
                        parts.append(sentence)
                        yield sentence

            def first_chunk() -> None:
                timing["tts_chunk"] = self.clock()

            def request_started() -> None:
                timing["tts_requested"] = self.clock()

            async def frames() -> AsyncGenerator[tuple[int, bytes]]:
                nonlocal why
                try:
                    audio: AsyncGenerator[tuple[int, bytes]] = media.speech_frames(
                        base, key, sentences_to_say(), name, first_chunk, request_started=request_started
                    )
                    async with aclosing(audio):
                        async for frame in audio:
                            yield frame
                except call_media.MediaUnavailable as exc:
                    log.warning(
                        "call_voice_speech_failed call=%s bot=%s reply=%s status=%s", call_id, bot, reply_id, exc.status
                    )
                    why = "voice_failed"

            def before(index: int) -> bool:
                nonlocal why
                if not self._standing(call_id, bot, voice, epoch):
                    why = "canceled"
                    return False
                if follow_up and not self._follow_up_may_play(call_id, reply_id):
                    why = "canceled"
                    return False
                if follow_up:
                    speech_pending = self.calls.speech_pending(call_id)
                    with voice.lock:
                        if (
                            voice.open
                            and voice.open.get("reply") == reply_id
                            and (speech_pending or voice.gathering or voice.open.get("yielded"))
                        ):
                            voice.open["yielded"] = True
                            why = "yielded"
                            return False
                if index == 0 and deadline is not None and self.clock() >= deadline:
                    self.makers.submit(self._late, call_id, bot)
                    why = "late"
                    return False
                return True

            def started(index: int) -> None:
                if index == 0:
                    timing["audio"] = self.clock()
                with voice.lock:
                    voice.heard.append((self.clock(), parts[index]))
                    changed = _epoch(voice, bot) == epoch and self._set(voice, bot, "speaking", parts[index])
                if changed:
                    self.makers.submit(self.touch, call_id)

            played, capped = media.say_stream(call_id, frames(), mark, budget, before, started)
            if capped or at_cap:
                self._note(voice, "Voice limit reached")
                why = "voice_limit"
        except Exception as exc:  # noqa: BLE001 - a voice failure leaves the posted text as it is
            why = "voice_failed"
            report("calls_voice", call_id, exc, logger="tico.call_voice")
        finally:
            recorder_lost = (
                follow_up
                and not played
                and (
                    mark is None or not media.running(call_id) or (why == "not_spoken" and media.mark(call_id) != mark)
                )
            )
            finish_follow_up = follow_up and not recorder_lost
            with voice.lock:
                if _epoch(voice, bot) != epoch:
                    why = "canceled"
                if voice.open and voice.open.get("reply") == reply_id:
                    if follow_up and isinstance(text, str):
                        if voice.open.get("yielded") and not played and _epoch(voice, bot) == epoch:
                            voice.follow_ups.appendleft((bot, reply_id, text))
                            finish_follow_up = False
                        elif finish_follow_up:
                            voice.played.append(reply_id)
                    if stream is not None and not stream.closed:
                        voice.open["playback_done"] = True
                    else:
                        voice.open = None
                if _epoch(voice, bot) == epoch:
                    self._set(voice, bot, "listening")
                why = timing.get("skip_reason") or why
            try:
                with self.store.transaction() as c:
                    if played:
                        c.execute("UPDATE calls SET voice_ms=voice_ms+? WHERE id=?", (played, call_id))
                    if finish_follow_up:
                        from . import call_follow_ups

                        call_follow_ups.finish_speech(c, call_id, reply_id)
                    self.touch(call_id, c)
            except Exception as exc:  # noqa: BLE001 - the spoken time is a cap, not a record
                report("calls_voice", call_id, exc, logger="tico.call_voice")
            if not follow_up:
                spoke = timing.get("audio") is not None
                self._log_turn(None, call_id, timing, "spoken" if spoke else "text_only", None if spoke else why)
            self.wake.set()

    def _follow_up_may_play(self, call_id: str, reply_id: str) -> bool:
        from . import call_follow_ups

        with self.store.read() as c:
            return bool(call_follow_ups.can_speak(c, call_id, reply_id))

    def _standing(self, call_id: str, bot: str, voice: CallVoice, epoch: tuple[int, int]) -> bool:
        """Whether a reply may still be heard: nothing canceled it, the call is live and not ending, and the bot is still
        attached by someone who may still read the thread and write to it."""
        from . import calls as _calls

        calls: Any = _calls

        with voice.lock:
            if _epoch(voice, bot) != epoch:
                return False
        with self.store.read() as c:
            row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] != "live" or row["ending_at"]:
                return False
            attached = c.execute(
                "SELECT joined_by FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (row["live_meeting_id"], bot)
            ).fetchone()
            return bool(attached) and calls.attachment_standing(c, attached[0], row["thread_id"], bot)

    # --- canceling

    def cancel(self, call_id: str, bot: str | None = None) -> None:
        """Stop a bot's voice in a call (or every bot's): its open question and its waiting one go, and when it is the one
        answering, what is playing stops at once. Another bot speaking goes on. Detach, lost access and the call's end
        all come here."""
        voice = self._found(call_id)
        if voice is None:
            return
        with voice.lock:
            if bot is None:
                voice.generation += 1
            else:
                voice.epochs[bot] = voice.epochs.get(bot, 0) + 1
            answering = voice.open is not None and (bot is None or voice.open["bot"] == bot)
            if answering and voice.open is not None:
                if stream := voice.open.get("stream"):
                    stream.close()
                elif voice.open["phase"] == "thinking":
                    self.makers.submit(self._log_turn, None, call_id, dict(voice.open), "skipped", "canceled")
            if answering:
                voice.open = None
            voice.follow_ups = deque(item for item in voice.follow_ups if bot is not None and item[0] != bot)
            for one in [bot] if bot else list(voice.waiting):
                voice.waiting.pop(one, None)
            voice.gathering = [turn for turn in voice.gathering if bot is not None and turn["bot"] != bot]
            for one in [bot] if bot else list(voice.states):
                voice.states.pop(one, None)
        media = self.calls.media
        if (answering or bot is None) and media is not None and hasattr(media, "hush"):
            media.hush(call_id)
        self.wake.set()

    def forget(self, call_id: str) -> None:
        """The call ended: stop its voice and drop its state."""
        self.cancel(call_id)
        with self.lock:
            self.voices.pop(call_id, None)

    # --- timing

    def _log_turn(self, c: Any, call_id: str, timing: dict[str, Any], outcome: str, reason: str | None) -> None:
        """The `call_voice_turn` line for one turn, no words in it (docs/meet.md, Voice). The pause, the stored
        text, the hand-over and the first audio are this clock's; asked, picked up and replied are the turn's own row.
        A step the turn never reached is null. `reason` is why it was skipped, or why its reply stayed text."""
        report: Any = _report()

        def ms(start: Any, end: Any) -> int | None:
            return None if start is None or end is None else max(0, round((end - start) * 1000))

        def wall(at: Any) -> float | None:
            parsed = H.parse_ts(at)
            return parsed.timestamp() if parsed is not None else None

        try:
            sql = "SELECT created_at,claimed_at,replied_at,skip_reason FROM live_meeting_turns WHERE id=?"
            if c is not None:
                turn = c.execute(sql, (timing["turn"],)).fetchone()
            else:
                with self.store.read() as read:
                    turn = read.execute(sql, (timing["turn"],)).fetchone()
            created, claimed, replied = (
                (wall(turn[k]) for k in ("created_at", "claimed_at", "replied_at")) if turn else (None,) * 3
            )
            if outcome == "skipped" and reason is None:
                reason = (turn["skip_reason"] if turn else "") or "skipped"
            heard, audio = timing.get("asked"), timing.get("audio")

            def first(stamp: str) -> int | None:
                return ms(heard, timing.get(stamp))

            # The support ring captures warnings and clips long lines. Keep the latency milestones up front.
            log.warning(
                "call_voice_turn first_token_ms=%s first_sentence_ms=%s first_audio_ms=%s first_audio_p90_ms=%s "
                "call_id=%s bot=%s turn=%s outcome=%s skip_reason=%s transcribe_ms=%s queue_ms=%s "
                "answer_ms=%s speech_ms=%s total_ms=%s heard_to_transcript_ms=%s tts_first_chunk_ms=%s",
                *(
                    "null" if v is None else v
                    for v in (first("token"), first("sentence"), first("audio"), self._p90(first("audio")))
                ),
                call_id,
                timing["bot"],
                timing["turn"],
                outcome,
                reason or "null",
                *(
                    "null" if v is None else v
                    for v in (
                        ms(heard, timing.get("stored")),
                        ms(created, claimed),
                        ms(claimed, None if outcome == "skipped" else replied),
                        ms(timing.get("handed"), audio),
                        first("audio") if timing.get("stored") is not None else None,
                        first("stored"),
                        ms(timing.get("tts_requested"), timing.get("tts_chunk")),
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001 - a timing line never stops the call's voice
            report("calls_voice", call_id, exc, logger="tico.call_voice")

    def _p90(self, first_audio: int | None) -> int | None:
        """The nearest-rank p90 of first_audio_ms over the latest FIRST_AUDIO_WINDOW spoken turns, this one counted."""
        with self.first_audio_lock:
            if first_audio is not None:
                self.first_audio.append(first_audio)
            seen = sorted(self.first_audio)
        return seen[math.ceil(0.9 * len(seen)) - 1] if seen else None

    def settle(self, timeout: float = 30) -> bool:
        """Wait for the replies being spoken (tests)."""
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                left = [f for f in self.playing if not f.done()]
            if not left:
                return True
            if time.monotonic() > deadline:
                return False
            wait(left, timeout=max(0.0, deadline - time.monotonic()))


def _report() -> Any:
    from . import batch_work

    work: Any = batch_work
    return work.report


def _name(c: Any, bot: str) -> str:
    found: dict[str, Any] = H.bot(c, bot) or {}
    return str(found.get("display_name") or found.get("slug") or bot)
