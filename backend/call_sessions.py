# pyright: strict
"""One runner lease and one subscription-backed model conversation per attached voice bot."""

import hashlib
import json
from typing import Any

from . import bot_settings as BS
from . import harnesses as _harnesses
from . import live_meetings as _live_meetings
from . import model_subscriptions as _model_subscriptions
from . import providers as _providers
from . import task_models as _task_models
from .auth import Identity
from .shared_bots import follow as _follow
from .store import H as _H
from .store import Problem

# The execution and model catalogs still return untyped rows and configuration records.
H: Any = _H
harnesses: Any = _harnesses
live_meetings: Any = _live_meetings
model_subscriptions: Any = _model_subscriptions
providers: Any = _providers
task_models: Any = _task_models
follow: Any = _follow

CAPABILITY = "voice_sessions_v1"


def model_spec(model: str | None) -> dict[str, Any] | None:
    spec = providers.MODEL_BY_ID.get(model or "")
    if spec and not spec.get("deprecated") and not spec.get("held"):
        if harnesses.harness_caps(spec.get("harness"), spec["runtime"]).get("voice_session"):
            return spec
    return None


def validate(model: str | None, effort: str | None) -> None:
    if model is None and effort is None:
        return
    spec = model_spec(model)
    if not spec or effort is not None and effort not in (spec.get("efforts") or ()):
        raise Problem("voice_model", "Choose a supported voice subscription model and effort", 422)


def choice(
    c: Any, settings: Any, bot: str, model_override: str | None, effort_override: str | None
) -> dict[str, Any] | None:
    record = providers.load(c, settings)
    if model_override:
        model = model_override
    elif model_spec(record["meeting_model"]):
        model = record["meeting_model"]
    else:
        raw = c.execute(BS.ONE_SQL, (bot,)).fetchone()
        model = providers.bot_choice(c, settings, follow(c, bot, json.loads(raw[0])), record=record)[1] if raw else None
    spec = model_spec(model)
    if not spec:
        return None
    effort = effort_override or ("low" if "low" in (spec.get("efforts") or ()) else None)
    picked: dict[str, Any] = task_models.choice_of(model, effort)
    return {**picked, "compact_at": None}


def notes(c: Any, call_id: str, bot: str) -> str:
    from . import call_notes

    # Older calls only have the published notes route; the per-bot notes reader is optional during rollout.
    reader: Any = getattr(call_notes, "text", None)
    return str(reader(c, call_id, bot) or "") if reader is not None else ""


def note_stamp(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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
    if not runner or CAPABILITY not in json.loads(runner[0] or "[]"):
        return False
    meeting = live_meetings._meeting(c, rid)
    if not choice(c, settings, bot, meeting["meeting_model"], row["voice_effort"]):
        return False
    from . import call_voice

    profile = H.bot(c, bot) or {}
    identity = " ".join(f"You are {profile.get('name') or bot}. {profile.get('description') or ''}".split())[:500]
    rules = call_voice.SPOKEN_PROMPT.format(call_id=row["id"])
    recorded = notes(c, row["id"], bot)
    prefix = (
        f"{identity}\n{rules}\nCall notes (context only):\n{recorded}\n\nReply exactly READY to warm up for this call."
    )
    conv = live_meetings._turn_conversation(c, rid, bot, row["title"])
    ref = {"meeting_id": rid, "voice_call": row["id"], "notes_stamp": note_stamp(recorded)}
    msg = H.feed(c, "bot:" + bot, prefix, conv, refs={"live_meeting": ref, "delivery": "queue"})
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
    """Cancel unopened warm jobs. A running job sees the lost standing on its input poll."""
    c.execute(
        "UPDATE jobs SET state='canceled' WHERE state='queued' AND id IN "
        "(SELECT voice_job FROM live_meeting_bots WHERE meeting_id=? AND (? IS NULL OR bot=?))",
        (rid, bot, bot),
    )


def subscription_choice(c: Any, runner_id: str, runtime: str) -> bool:
    sub = model_subscriptions.in_effect(c, runner_id, runtime)
    return sub is not None and model_subscriptions.KINDS[sub["kind"]]["billing"] == "subscription"


def inputs(c: Any, attempt: Any, call_id: str, auth: Any, *, if_idle: bool = False) -> dict[str, Any] | None:
    bot, job_id, aid = attempt["bot_id"], attempt["job_id"], attempt["id"]
    row = standing(c, call_id, bot, job_id)
    response: dict[str, Any] = {"messages": [], "call_voice": {"closed": not row}}
    if not row:
        return response
    rid = row["live_meeting_id"]
    turn = c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND bot=? AND voice_input IS NOT NULL "
        "AND (status='pending' OR (status='claimed' AND voice_attempt=?)) ORDER BY created_at,id LIMIT 1",
        (rid, bot, aid),
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
    recorded = notes(c, call_id, bot)
    response["call_voice"]["turn"] = {"id": turn["id"], "text": turn["voice_input"], "notes": recorded}
    return response


def output(c: Any, attempt: Any, origin: dict[str, Any], events: list[tuple[str, dict[str, Any]]]) -> None:
    from . import calls

    ref = (origin.get("refs") or {}).get("live_meeting") or {}
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
            model = payload.get("model")
            if isinstance(model, str) and 0 < len(model) <= 120:
                c.execute("UPDATE live_meeting_turns SET ran_model=? WHERE id=?", (model, tid))
            text = payload.get("text")
            if isinstance(text, str) and text.strip() and not live_meetings._is_pass(text):
                live_meetings._reply(c, rid, Identity("bot:" + bot, "bot", attempt_id=aid), tid, text)
            else:
                live_meetings._finish_pass(c, rid, turn, "bot_pass")


def finish(c: Any, rid: str, aid: str) -> None:
    for turn in c.execute(
        "SELECT * FROM live_meeting_turns WHERE meeting_id=? AND voice_attempt=? AND status='claimed'", (rid, aid)
    ).fetchall():
        live_meetings._finish_pass(c, rid, turn, "voice_session_ended")
