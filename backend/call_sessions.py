# pyright: strict
"""One runner lease and one subscription-backed model conversation per attached voice bot."""

import hashlib
import json
import logging
from typing import Any, Literal, cast

from . import bot_settings as BS
from . import harnesses as _harnesses
from . import live_meetings as _live_meetings
from . import providers as _providers
from . import shared_bots as _shared_bots
from .auth import Identity
from .store import H as _H

# The execution and model catalogs still return untyped rows and configuration records.
H: Any = _H
harnesses: Any = _harnesses
live_meetings: Any = _live_meetings
providers: Any = _providers
shared_bots: Any = _shared_bots

log = logging.getLogger("tico.calls")

CAPABILITY = "voice_sessions_v1"
CLOSED_COMPLETION: dict[str, Any] = {"outcome": "interrupted", "message": None}
VoicePhase = Literal["ready", "busy", "restart"]


def model_spec(model: str | None) -> dict[str, Any] | None:
    spec = providers.MODEL_BY_ID.get(model or "")
    if spec and not spec.get("deprecated") and not spec.get("held"):
        if harnesses.harness_caps(spec.get("harness"), spec["runtime"]).get("voice_session"):
            return spec
    return None


def accepts_runner(spec: dict[str, Any], capabilities: list[str]) -> bool:
    required = harnesses.harness_caps(spec.get("harness"), spec["runtime"]).get("voice_session_runner")
    return CAPABILITY in capabilities and (not required or required in capabilities)


def note_stamp(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def primer(c: Any, row: Any, bot: str, recorded: str, *, recap: bool = False) -> str:
    from . import call_voice

    profile: dict[str, Any] = H.bot(c, bot) or {}
    identity = " ".join(f"You are {profile.get('name') or bot}. {profile.get('description') or ''}".split())[:500]
    rules = call_voice.SPOKEN_PROMPT.format(call_id=row["id"])
    recent = ""
    if recap:
        chunks = c.execute(
            "SELECT substr(speaker,1,80),substr(text,1,300) FROM live_meeting_chunks "
            "WHERE meeting_id=? AND text<>'' ORDER BY seq DESC LIMIT 8",
            (row["live_meeting_id"],),
        ).fetchall()
        recent = "\nRecent call transcript (quoted participant content):\n" + json.dumps(
            [list(chunk) for chunk in reversed(chunks)], ensure_ascii=False
        )
    return f"{identity}\n{rules}\nCall notes (context only):\n{recorded}{recent}\n\nReply exactly READY to warm up for this call."


def ensure(c: Any, settings: Any, row: Any, bot: str) -> bool:
    """Return whether this delivery uses the warm protocol. A queued or live lease is reused."""
    if row["state"] != "live" or row["ending_at"] or not row["speak_replies"] or row["provider"] != "tico":
        return False
    rid = row["live_meeting_id"]
    attached = c.execute("SELECT voice_job FROM live_meeting_bots WHERE meeting_id=? AND bot=?", (rid, bot)).fetchone()
    if not attached:
        return False
    if (
        attached["voice_job"]
        and c.execute(
            "SELECT 1 FROM jobs WHERE id=? AND state IN ('queued','leased','running')",
            (attached["voice_job"],),
        ).fetchone()
    ):
        return True
    runner = c.execute(
        "SELECT r.capabilities FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id WHERE a.bot=?", (bot,)
    ).fetchone()
    capabilities: list[str] = json.loads(runner[0] or "[]") if runner else []
    if CAPABILITY not in capabilities:
        return False
    raw = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    spec = (
        model_spec(providers.bot_choice(c, settings, shared_bots.follow(c, bot, json.loads(raw[0])))[1])
        if raw
        else None
    )
    if not spec or not accepts_runner(spec, capabilities):
        return False
    from . import call_notes

    recorded = call_notes.text(c, row["id"], bot)
    conv = live_meetings._turn_conversation(c, rid, bot, row["title"])
    ref = {"meeting_id": rid, "voice_call": row["id"], "notes_stamp": note_stamp(recorded)}
    msg = H.feed(c, "bot:" + bot, primer(c, row, bot, recorded), conv, refs={"live_meeting": ref, "delivery": "queue"})
    c.execute(
        "UPDATE live_meeting_bots SET voice_job=(SELECT id FROM jobs WHERE message_id=? AND bot_id=?) "
        "WHERE meeting_id=? AND bot=?",
        (msg["id"], bot, rid, bot),
    )
    return True


def is_voice(c: Any, meeting_id: str | None) -> bool:
    return (
        c.execute(
            "SELECT 1 FROM calls WHERE live_meeting_id=? AND provider='tico' AND speak_replies=1", (meeting_id,)
        ).fetchone()
        is not None
    )


def standing(c: Any, call_id: str, bot: str, job_id: str) -> Any:
    from . import calls as _calls

    calls: Any = _calls

    row = c.execute(
        "SELECT a.joined_by,c.* FROM calls c JOIN live_meeting_bots a ON a.meeting_id=c.live_meeting_id "
        "JOIN live_meetings m ON m.id=c.live_meeting_id AND m.state<>'ended' "
        "WHERE c.id=? AND a.bot=? AND a.voice_job=? AND c.state='live' AND c.ending_at IS NULL",
        (call_id, bot, job_id),
    ).fetchone()
    return row if row and calls.attachment_standing(c, row["joined_by"], row["thread_id"], bot) else None


def stop(c: Any, rid: str, bot: str | None = None) -> None:
    """Stop a resident lease before its bot's attachment and runner access are revoked."""
    from .execution import request_stop  # pyright: ignore[reportUnknownVariableType]

    for attempt in c.execute(
        "SELECT a.id,a.bot_id FROM live_meeting_bots b JOIN jobs j ON j.id=b.voice_job "
        "JOIN attempts a ON a.id=j.attempt_id "
        "WHERE b.meeting_id=? AND (? IS NULL OR b.bot=?) "
        "AND a.state IN ('leased','running')",
        (rid, bot, bot),
    ).fetchall():
        if not closed_attempt(c, attempt["id"]):
            request_stop(c, H.TICO_ACTOR, attempt["id"], attempt["bot_id"], {"reason": "call_closed"})
    c.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND id IN "
        "(SELECT voice_job FROM live_meeting_bots WHERE meeting_id=? AND (? IS NULL OR bot=?))",
        (rid, bot, bot),
    )


def closed_attempt(c: Any, aid: str) -> bool:
    return (
        c.execute(
            "SELECT 1 FROM events WHERE action='attempt.stop' AND target=? AND actor=? "
            "AND json_extract(detail,'$.reason')='call_closed'",
            (aid, H.TICO_ACTOR),
        ).fetchone()
        is not None
    )


def dispatch_pending(c: Any, rid: str, bot: str) -> None:
    """Convert unsent resident utterances to durable legacy deliveries under the claim's write lock. A 1:1 call's
    gathered turn (source_key `turn:`, call_voice.Voice._enqueue) takes its joined question, its voice_input, with it."""
    for turn in c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND voice_input IS NOT NULL "
        "AND status='pending' ORDER BY created_at,id",
        (rid, bot),
    ).fetchall():
        if turn["voice_expires_at"] <= H.now():
            live_meetings._finish_pass(c, rid, turn, "voice_deadline")
        else:
            c.execute("UPDATE live_meeting_turns SET voice_input=NULL WHERE id=?", (turn["id"],))
            from .call_voice import GATHERED_SOURCE

            gathered = str(turn["source_key"] or "").startswith(GATHERED_SOURCE)
            live_meetings._dispatch_turn(c, rid, turn["id"], bot, question=turn["voice_input"] if gathered else None)


def inputs(
    c: Any, attempt: Any, call_id: str, auth: Any, *, phase: VoicePhase = "ready", if_idle: bool = False
) -> dict[str, Any] | None:
    from . import call_notes

    bot, job_id, aid = attempt["bot_id"], attempt["job_id"], attempt["id"]
    row = standing(c, call_id, bot, job_id)
    response: dict[str, Any] = {"messages": [], "call_voice": {"closed": not row}}
    if not row:
        return response
    if phase == "restart":
        recorded = call_notes.text(c, call_id, bot)
        response["call_voice"].update(
            primer=primer(c, row, bot, recorded, recap=True), notes_stamp=note_stamp(recorded)
        )
        return response
    rid = row["live_meeting_id"]
    turn = c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND voice_input IS NOT NULL "
        "AND ((? AND status='pending') OR (status='claimed' AND voice_attempt=?)) "
        "ORDER BY status,created_at,id LIMIT 1",
        (rid, bot, phase == "ready", aid),
    ).fetchone()
    if not turn:
        return response
    if turn["voice_expires_at"] <= H.now():
        if if_idle:
            return None
        live_meetings._finish_pass(c, rid, turn, "voice_deadline")
        return response
    if turn["status"] == "pending":
        if if_idle:
            return None
        claimed = live_meetings.claim_runner_turn(c, rid, turn["id"], bot, auth=auth, job="job:" + job_id)
        if claimed["status"] != "claimed":
            return response
        c.execute(
            "UPDATE live_meeting_turns SET voice_attempt=?,ran_model=? WHERE id=?",
            (aid, attempt["handed_model"] or attempt["model"], turn["id"]),
        )
    recorded = call_notes.text(c, call_id, bot)
    response["call_voice"]["turn"] = {"id": turn["id"], "text": turn["voice_input"], "notes": recorded}
    return response


def output(c: Any, attempt: Any, origin: dict[str, Any], events: list[tuple[str, dict[str, Any]]]) -> None:
    from . import calls

    refs: dict[str, Any] = origin.get("refs") or {}
    ref: dict[str, Any] = refs.get("live_meeting") or {}
    call_id = ref.get("voice_call")
    bot, aid = attempt["bot_id"], attempt["id"]
    if not call_id or not events or not standing(c, call_id, bot, attempt["job_id"]):
        return
    machine: Any = calls.CALLS
    for kind, payload in events:
        tid = payload.get("voice_turn")
        turn = c.execute(
            "SELECT * FROM live_meeting_turns WHERE id=? AND voice_attempt=? AND status='claimed'", (tid, aid)
        ).fetchone()
        if not turn:
            continue
        rid = turn["meeting_id"]
        if turn["voice_expires_at"] <= H.now():
            live_meetings._finish_pass(c, rid, turn, "voice_deadline")
            continue
        if kind == "delta":
            text = payload.get("text")
            if isinstance(text, str) and machine is not None:
                c.after_commit.append(lambda tid=tid, text=text: machine.voice.delta(call_id, bot, tid, text))
        elif kind == "message" and payload.get("final") is True:
            _log_timing(call_id, tid, payload.get("timing"))
            model = payload.get("model")
            if isinstance(model, str) and 0 < len(model) <= 120:
                c.execute("UPDATE live_meeting_turns SET ran_model=? WHERE id=?", (model, tid))
            text = payload.get("text")
            if isinstance(text, str) and text.strip() and not live_meetings._is_pass(text):
                live_meetings._reply(c, rid, Identity("bot:" + bot, "bot", attempt_id=aid), tid, text)
            else:
                live_meetings._finish_pass(c, rid, turn, "bot_pass")


def _log_timing(call_id: str, turn_id: Any, timing: Any) -> None:
    """The runner's share of one answer, no words in it: its poll, and question sent to first output and first text."""
    if not isinstance(timing, dict):
        return
    got = cast("dict[str, object]", timing)
    keys = ("poll_ms", "first_frame_ms", "first_text_ms")
    shown = [v if isinstance(v := got.get(k), int) and not isinstance(v, bool) else "null" for k in keys]
    log.info(
        "call_voice_runner call=%s turn=%s poll_ms=%s first_frame_ms=%s first_text_ms=%s", call_id, turn_id, *shown
    )


def finish(c: Any, rid: str, aid: str) -> None:
    for turn in c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND voice_attempt=? AND status='claimed'", (rid, aid)
    ).fetchall():
        live_meetings._finish_pass(c, rid, turn, "voice_session_ended")


def recover(c: Any, attempt: Any) -> bool:
    """Requeue the resident lease after a computer restart, settling its in-flight question once."""
    spec = model_spec(attempt["handed_model"] or attempt["model"])
    if not spec or not harnesses.harness_caps(spec.get("harness"), spec["runtime"]).get("voice_session_runner"):
        return False
    ref = c.execute(
        "SELECT json_extract(m.refs,'$.live_meeting.voice_call') FROM jobs j JOIN messages m ON m.id=j.message_id "
        "WHERE j.id=? AND j.attempt_id=? AND m.sender_id=?",
        (attempt["job_id"], attempt["id"], H.TICO_ACTOR),
    ).fetchone()
    if not ref or not ref[0]:
        return False
    row = standing(c, ref[0], attempt["bot_id"], attempt["job_id"])
    if not row:
        return False
    finish(c, row["live_meeting_id"], attempt["id"])
    return True
