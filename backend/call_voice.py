# pyright: strict
"""Voice in Tico Meet calls (docs/meet.md, Voice): speech cut at pauses, who answers, and the bots' spoken replies.

A call with `speak_replies` (every Tico Meet call unless its starter turned it off) is a voice call. Each person's speech
is cut into utterances where they pause (`Segmenter`); each utterance is transcribed and stored in the order it was
heard (backend/calls.py), and then routed here:

- In a 1:1 call with a bot (one bot attached, and it is the only bot in the thread's DM) the bot answers every utterance.
- In any other voice call only a bot whose name was said answers. The router's own decision windows do not run.
- A call has one open question at a time. A bot asked while another answers waits; a wait longer than
  QUESTION_WAIT_SECONDS is dropped with a note.

The answer is the bot's normal meeting turn on its own model, told to answer in a few spoken sentences. A turn not
answered in ANSWER_SECONDS is ended (`voice_deadline`) with a note, so a late answer is never posted or played. The
reply is always posted as text in the thread (calls.project_reply). Only after that write commits is it handed here to
be spoken: sentence by sentence through the Gateway's text-to-speech, the next sentence made while one plays, each
checked first against the call (still live, not ending, the bot still attached with its attacher's access). A voice
failure leaves the text as it is.

One owner: the process that holds a call's recorder (call_media.LiveKitMedia, `running`) holds its voice too. Everything
here is that process's memory: the open question, the bots' states and captions, the replies already spoken. Another
process skips playback for a call it does not hold, and a restart starts the call's voice afresh (the recorder rejoins
from the sweep). The states readers see (`listening`, `waiting`, `thinking`, `making_voice`, `speaking`) are in the call
view as `voice.bots`, and each change writes a `call.updated` change so open views read the call again.
"""

import logging
import math
import re
import threading
import time
from array import array
from collections import OrderedDict, deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from . import call_media as _call_media
from . import gateway_connection as _gateway_connection
from . import live_meetings as _live_meetings
from .store import H as _H
from .store import Problem

# Untyped modules, used through Any here (this module is strict; they are not yet).
call_media: Any = _call_media
gateway_connection: Any = _gateway_connection
live_meetings: Any = _live_meetings
H: Any = _H

log = logging.getLogger("tico.call_voice")

# --- pause detection (16 kHz mono 16-bit, as the recorder hears it)
BYTES_PER_MS = call_media.SAMPLE_RATE * 2 // 1000
FRAME_MS = 20
LOUD_RMS = 500  # a 20 ms frame this loud (of 32767) is speech; quieter is a pause
SILENCE_MS = 700  # a pause this long after speech ends the utterance
QUESTION_MAX_MS = 60_000  # an utterance is cut here even without a pause
PREROLL_MS = 200  # kept from before the first loud frame, so a soft first syllable is not clipped
MIN_SPEECH_MS = 200  # less speech than this (a cough, a click) is not sent

# --- questions and answers
ANSWER_SECONDS = 30  # a bot that has not answered this long after it was asked does not answer
QUESTION_WAIT_SECONDS = 30  # a question waiting behind another this long is dropped
NOTE_SECONDS = 10  # how long a note stays on the call view
TICK_SECONDS = 0.25
REPLIED_GRACE_SECONDS = 5  # a reply the hook has not handed over this long after it was stored is not spoken

# --- speaking
SPOKEN_CHARS = 600  # the part of a reply that is spoken; the whole reply is in the thread
VOICE_CAP_MS = 30 * 60 * 1000  # the bots' spoken time in one call
PLAYED_KEEP = 200  # reply ids remembered per call, so a reply is spoken once

STATES = ("listening", "waiting", "thinking", "making_voice", "speaking")
SPOKEN_PROMPT = (
    "This meeting is a live voice call: your reply is read aloud to the people in it. Answer in plain spoken "
    "sentences, at most about 80 words. No markdown, lists, headings, links, code or emoji.\n"
)


def loudness(frame: bytes) -> float:
    """The root mean square of one frame of 16-bit samples."""
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    if not samples:
        return 0.0
    return math.sqrt(math.sumprod(samples, samples) / len(samples))


class Segmenter:
    """One speaker's speech cut into utterances at their pauses, by loudness. Time is the audio's own (`start_ms` of
    each piece fed), so the cut does not depend on when frames arrive. `flush` ends an utterance whose speaker stopped
    sending (muted, left) or whose call is ending."""

    def __init__(self):
        self.rest = bytearray()  # the part of a frame not yet whole
        self.preroll = bytearray()
        self.pcm: bytearray | None = None  # the utterance under way, or None between utterances
        self.start = self.voiced = self.quiet = 0
        self.at = 0.0  # wall time of the last piece fed (the call's idle flush reads it)

    @property
    def speaking(self) -> bool:
        return self.pcm is not None

    def feed(self, pcm: bytes, start_ms: int) -> list[tuple[int, bytes]]:
        """Take one piece of speech; returns the utterances it ended, as (start_ms, pcm)."""
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
            self.start = at_ms - len(self.preroll) // BYTES_PER_MS
            self.preroll = bytearray()
            self.voiced, self.quiet = FRAME_MS, 0
        else:
            self.pcm += frame
            if loud:
                self.voiced, self.quiet = self.voiced + FRAME_MS, 0
            else:
                self.quiet += FRAME_MS
        if self.quiet >= SILENCE_MS or len(self.pcm) >= QUESTION_MAX_MS * BYTES_PER_MS:
            return self._take()
        return None

    def _take(self) -> tuple[int, bytes] | None:
        pcm, start, voiced, quiet = self.pcm, self.start, self.voiced, self.quiet
        self.pcm, self.voiced, self.quiet = None, 0, 0
        if pcm is None or voiced < MIN_SPEECH_MS:
            return None
        # The pause that ended it is not speech: a short tail of it stays, so the last word is not cut.
        trailing = max(0, quiet - PREROLL_MS) * BYTES_PER_MS
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


def sentences(text: str) -> list[str]:
    """Text split into sentences to speak one at a time; a fragment too short to be worth a request joins the next."""
    parts: list[str] = []
    carry = ""
    for piece in re.split(r"(?<=[.!?…])\s+", text):
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


class CallVoice:
    """One call's voice state, in this process. Guarded by `lock`; no transaction is opened while it is held."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        # The question being answered: {"bot", "turn", "asked", "phase", "reply", "replied_at"}.
        self.open: dict[str, Any] | None = None
        self.waiting: OrderedDict[str, dict[str, Any]] = (
            OrderedDict()
        )  # bot -> {"seq", "at"}: asked while another answers
        self.states: dict[str, tuple[str, str | None]] = {}  # bot -> (state, caption)
        self.note: tuple[str, float] | None = None  # (text, wall time)
        self.played: deque[str] = deque(maxlen=PLAYED_KEEP)
        self.generation = 0  # bumped by every cancel: a playback that started before it stops


class Voice:
    """The voice of every call this process holds. `clock` is wall time in seconds, swapped in tests; `tick` is what the
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
            self.ticker = threading.Thread(target=self._run, name="tico-meet-voice-tick", daemon=True)
            self.ticker.start()

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

    # --- questions

    @staticmethod
    def answers_all(c: Any, row: Any) -> str | None:
        """The bot that answers every utterance in this call, when it is a 1:1 call with a bot: one bot attached, and
        it is the only bot among the thread's people. None otherwise."""
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
        members = [actor for actor in threads._dm_members(c, root) if str(actor).startswith("bot:")]
        return bots[0] if members == ["bot:" + bots[0]] else None

    def route(self, c: Any, row: Any, chunk: Any) -> list[str]:
        """Inside the transaction that stored a transcribed utterance: ask the bots that answer it, one open question
        per call. Returns the bots asked now."""
        if row["ending_at"] or row["state"] != "live":
            return []
        rid = row["live_meeting_id"]
        only = self.answers_all(c, row)
        targets = [only] if only else live_meetings._named_bots(c, rid, chunk["text"])
        if not targets:
            return []
        voice = self._call(row["id"])
        asked: list[str] = []
        with voice.lock:
            for bot in targets:
                if voice.open is not None or asked:
                    voice.waiting.pop(bot, None)
                    voice.waiting[bot] = {"seq": chunk["seq"], "at": self.clock()}
                    if not (voice.open and voice.open["bot"] == bot):
                        self._set(voice, bot, "waiting")
                    continue
                if self._ask(c, voice, rid, bot, chunk["seq"], chunk["end_ms"]):
                    asked.append(bot)
        self.touch(row["id"], c)
        return asked

    def _ask(self, c: Any, voice: CallVoice, rid: str, bot: str, seq: int, at_ms: int | None = None) -> bool:
        """Put one bot's turn on the queue as the call's open question (voice.lock held, inside a transaction)."""
        selected, _skipped = live_meetings._enqueue(
            c, rid, [bot], source_key=f"chunk:{seq}", transcript_seq=seq, at_ms=at_ms, named=True
        )
        if not selected:
            self._set(voice, bot, "listening")
            return False
        voice.open = {"bot": bot, "turn": selected[0]["id"], "asked": self.clock(), "phase": "thinking"}
        self._set(voice, bot, "thinking")
        return True

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
        with voice.lock:
            current = dict(voice.open) if voice.open else None
            idle = voice.open is None and not voice.waiting
            stale_note = voice.note and now - voice.note[1] >= NOTE_SECONDS
        if idle:
            if stale_note:
                with voice.lock:
                    voice.note = None
                self.touch(call_id)
            return
        moved = False
        with self.store.transaction() as c:
            row = c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] != "live" or row["ending_at"]:
                self.forget(call_id)
                return
            rid = row["live_meeting_id"]
            if current and current["phase"] == "thinking":
                turn = c.execute("SELECT * FROM live_meeting_turns WHERE id=?", (current["turn"],)).fetchone()
                status = turn["status"] if turn else "skipped"
                if status in ("pending", "claimed") and now - current["asked"] >= ANSWER_SECONDS:
                    live_meetings._finish_pass(c, rid, turn, "voice_deadline")
                    self._note(voice, f"{_name(c, current['bot'])} couldn't answer in time")
                    status = "skipped"
                if status == "replied":
                    # The reply hook hands a stored reply over after its commit; one never handed over (not this
                    # process's call, or a lost hand-off) frees the call after a moment.
                    with voice.lock:
                        replied_at = voice.open.get("replied_at") if voice.open else None
                        if voice.open is not None and replied_at is None:
                            voice.open["replied_at"] = now
                        elif (
                            voice.open is not None
                            and replied_at is not None
                            and now - replied_at >= REPLIED_GRACE_SECONDS
                        ):
                            status = "skipped"
                if status == "skipped":
                    with voice.lock:
                        if voice.open and voice.open["turn"] == current["turn"]:
                            voice.open = None
                            self._set(voice, current["bot"], "listening")
                            moved = True
            with voice.lock:
                if voice.open is None:
                    while voice.waiting:
                        bot, asked = voice.waiting.popitem(last=False)
                        moved = True
                        if not c.execute(
                            "SELECT 1 FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (rid, bot)
                        ).fetchone():
                            voice.states.pop(bot, None)
                            continue
                        if now - asked["at"] >= QUESTION_WAIT_SECONDS:
                            self._set(voice, bot, "listening")
                            self._note(voice, f"{_name(c, bot)} missed a question")
                            continue
                        if self._ask(c, voice, rid, bot, asked["seq"]):
                            break
            if moved:
                self.touch(call_id, c)

    # --- speaking

    def reply(self, call_id: str, bot: str, reply_id: str, text: str) -> None:
        """A bot's reply, stored and posted (after its commit): speak it, once, if this process holds the call. It runs as
        an after-commit hook, while the writer gate is still held: it opens no transaction itself (the change rows are
        written from a worker)."""
        voice = self._call(call_id)
        with voice.lock:
            if reply_id in voice.played:
                return
            voice.played.append(reply_id)
            if voice.open is None or voice.open["bot"] != bot:
                # A reply that is not the open question's (a late one, a typed one): it stays text.
                return
            if self.calls.media is None or not self.calls.media.running(call_id):
                log.info("A call's reply was not spoken: this process does not hold call %s", call_id)
                voice.open = None
                self._set(voice, bot, "listening")
                self.makers.submit(self.touch, call_id)
                return
            voice.open.update(phase="making_voice", reply=reply_id)
            self._set(voice, bot, "making_voice")
            generation = voice.generation
        self.makers.submit(self.touch, call_id)
        future = self.players.submit(self._speak, call_id, bot, reply_id, text, generation)
        with self.lock:
            self.playing = [f for f in self.playing if not f.done()] + [future]

    def _speak(self, call_id: str, bot: str, reply_id: str, text: str, generation: int) -> None:
        report: Any = _report()

        voice = self._call(call_id)
        played = 0
        made: list[Future[tuple[bytes, int]]] = []
        try:
            with self.store.read() as c:
                key, base, _fingerprint = gateway_connection.target(c, self.app.state.vault, self.store.settings)
                used = c.execute("SELECT voice_ms FROM calls WHERE id=?", (call_id,)).fetchone()
            used = int(used[0] or 0) if used else 0
            parts = sentences(spoken(text))
            if not key or not parts:
                return
            if used >= VOICE_CAP_MS:
                self._note(voice, "Voice limit reached")
                return
            name = call_media.voice_for(bot, call_media.voices(base, key))
            made.append(self.makers.submit(call_media.speech, base, key, parts[0], name))
            for index, sentence in enumerate(parts):
                if index + 1 < len(parts):
                    made.append(self.makers.submit(call_media.speech, base, key, parts[index + 1], name))
                try:
                    pcm, rate = made[index].result(call_media.SPEECH_TIMEOUT + 5)
                except call_media.MediaUnavailable as exc:
                    log.warning(
                        "call_voice_speech_failed call=%s bot=%s reply=%s status=%s", call_id, bot, reply_id, exc.status
                    )
                    break
                if not self._standing(call_id, bot, voice, generation):
                    break
                length = len(pcm) * 1000 // (rate * 2)
                if used + played + length > VOICE_CAP_MS:
                    self._note(voice, "Voice limit reached")
                    break

                def started(caption: str = sentence) -> None:
                    # On the media loop: the state is set here, and its change is written off the loop.
                    if self._set(voice, bot, "speaking", caption):
                        self.makers.submit(self.touch, call_id)

                said = self.calls.media.say(call_id, pcm, rate, started)
                played += said
                if not said or voice.generation != generation:
                    break
        except Exception as exc:  # noqa: BLE001 - a voice failure leaves the posted text as it is
            report("calls_voice", call_id, exc, logger="tico.call_voice")
        finally:
            for future in made:
                future.cancel()
            with voice.lock:
                if voice.open and voice.open.get("reply") == reply_id:
                    voice.open = None
                if voice.generation == generation:
                    self._set(voice, bot, "listening")
            try:
                with self.store.transaction() as c:
                    if played:
                        c.execute("UPDATE calls SET voice_ms=voice_ms+? WHERE id=?", (played, call_id))
                    self.touch(call_id, c)
            except Exception as exc:  # noqa: BLE001 - the spoken time is a cap, not a record
                report("calls_voice", call_id, exc, logger="tico.call_voice")
            self.wake.set()

    def _standing(self, call_id: str, bot: str, voice: CallVoice, generation: int) -> bool:
        """Whether a reply may still be heard: nothing canceled it, the call is live and not ending, and the bot is still
        attached by someone who may still read the thread and write to it."""
        from . import calls as _calls

        calls: Any = _calls

        if voice.generation != generation:
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
        """Stop a bot's voice in a call (or every bot's): its open question and its waiting one go, and what is playing
        stops at once. Detach, lost access and the call's end all come here."""
        voice = self._found(call_id)
        if voice is None:
            return
        with voice.lock:
            voice.generation += 1
            if voice.open and (bot is None or voice.open["bot"] == bot):
                voice.open = None
            for one in [bot] if bot else list(voice.waiting):
                voice.waiting.pop(one, None)
            for one in [bot] if bot else list(voice.states):
                voice.states.pop(one, None)
        media = self.calls.media
        if media is not None and hasattr(media, "hush"):
            media.hush(call_id)
        self.wake.set()

    def forget(self, call_id: str) -> None:
        """The call ended: stop its voice and drop its state."""
        self.cancel(call_id)
        with self.lock:
            self.voices.pop(call_id, None)

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
