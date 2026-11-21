# pyright: strict
"""One charge per paid shot (master plan P2 D2): a paid render is written down before it is sent, and one whose outcome
is unknown is never sent again on its own.

The paid render is a Gateway video (POST /api/v2/gateway/videos, backend/providers_routes.py). Its shot key is the
asker, the card's open visit, the input's version (a hash of everything the Gateway is sent) and the shot's name. A
request with no name outside a visit is keyed to its own Idempotency-Key instead (none is a 422). Every request's
Idempotency-Key is kept as an alias of the shot it resolved to (`paid_render.alias`), whatever state that shot is in,
and is read first: a retry after the card moved to a new visit is the same shot, and the same key with another input is
409 `idempotency_conflict`.

`resolve` runs before the Gateway and budget checks: a shot that started answers its job, one in flight is busy (429)
and one whose outcome is unknown is refused (409 `reconcile_shot`, which no client retries). Only a new shot, or one
the Gateway refused, goes on to the checks and `send`. Before the call, `begin` writes `paid_render.sent` in its own
durable transaction, so a restart keeps it, carrying the shot's first card, visit and name forward; the key goes to the
Gateway as the request's Idempotency-Key. `end` writes the answer: `paid_render.started` (the job), `paid_render.refused`
(a certain refusal: nothing was charged, so the shot may be sent again) or `paid_render.unknown`.

A certain refusal is a 4xx other than a timeout, HQ's `503 unconfigured` (refused before anything is sent), or a 5xx
that gives the provider's reason and is not a timeout. Everything else is unknown: no answer, a 504, a 503 whose code is
a timeout or `unavailable` (with or without a reason), a 202 whose id could not be read. A shot whose latest event is
`sent` and is not in flight is unknown too: the next request finds it, and the Run housekeeping sweep (`sweep`) does
once it is `STALE_MINUTES` old. That shot is never sent again.

On its card one question, "Reconcile N shots", asks the person the card falls to (waiting_on.person_for), naming every
shot in its text. Only BotOps asks it, and only while BotOps has no other open question in the card's chat room (a new
one would replace it); never the agent that rendered. A later unknown shot is added to it, edited in place. The sweep
asks again, on open cards, for every unknown shot whose question was never posted, was cleared or replaced unanswered,
or whose person can no longer answer it; a question still open or answered is left alone. A new take is a new shot
name, a changed input or a new visit. No new table: the events table holds the keys (its `events_action_target` index
finds them).
"""

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from . import hubdb
from .store import Problem

H: Any = hubdb
Row = dict[str, Any]
Answer = tuple[int, Row, str]  # (status, answer, code), as clients/gateway.py answers

log = logging.getLogger(__name__)

SENT, STARTED, REFUSED, UNKNOWN = (
    "paid_render.sent",
    "paid_render.started",
    "paid_render.refused",
    "paid_render.unknown",
)
ACTIONS = (SENT, STARTED, REFUSED, UNKNOWN)
ALIAS = "paid_render.alias"  # a request's Idempotency-Key -> the shot key it resolved to
NAME_WORDS = 6  # a shot with no name is named by its prompt's first words
STALE_MINUTES = 5  # a sent shot this old with no answer, and not in flight, is unknown (`sweep`)
ANSWERED = ("replied", "approval")  # a question cleared by its person's answer (question_marks)
CARRIED = ("task", "visit", "shot")  # what a shot sent again keeps from its first send
WHY = (
    "The Gateway never confirmed them, so they may have been charged. They are not made again on their own: check "
    "Gateway usage first, and give a new take a new shot name."
)

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()


def refused(status: int, answer: Row, code: str) -> bool:
    """Whether the Gateway refused a video for certain, so nothing was charged (module docstring)."""
    if status in (408, 504) or "timeout" in code or code == "unavailable":
        return False
    if status == 503 and code == "unconfigured":
        return True
    return 400 <= status < 500 or (status >= 500 and bool(answer.get("detail")))


def _turn(c: Any, who: Any) -> tuple[str | None, str | None]:
    """(task id, its open visit's id) for a bot's turn, else (None, None)."""
    from . import email_sending as _sending

    sending: Any = _sending
    task_id: str | None = sending.turn_task(c, who)
    if not task_id:
        return None, None
    visit = c.execute("SELECT id FROM visits WHERE task_id=? AND left_at IS NULL", (task_id,)).fetchone()
    return task_id, (str(visit[0]) if visit else None)


def _latest(c: Any, key: str) -> tuple[str | None, Row]:
    found = c.execute(
        f"SELECT action, detail FROM events WHERE action IN ({','.join('?' * len(ACTIONS))}) AND target=? "
        "ORDER BY rowid DESC LIMIT 1",
        (*ACTIONS, key),
    ).fetchone()
    return (str(found[0]), dict(json.loads(found[1] or "{}"))) if found else (None, {})


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def _shot(c: Any, who: Any, job: Row, shot: str | None, operation: str | None) -> tuple[str, Row]:
    """The shot key, and what a send writes with it ({task, visit, input, shot}). The request's Idempotency-Key names
    its shot when it named one before; otherwise it is kept now as that shot's alias."""
    digest = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
    named = " ".join(str(shot or "").split())
    name = named or " ".join(str(job.get("prompt") or "").split()[:NAME_WORDS])
    task_id, visit = _turn(c, who)
    said: Row = {"task": task_id, "visit": visit, "input": digest, "shot": name}
    alias = "op_" + _hash(who.actor, operation) if operation else ""
    found = (
        alias and c.execute("SELECT detail FROM events WHERE action=? AND target=? LIMIT 1", (ALIAS, alias)).fetchone()
    )
    if found:
        known = dict(json.loads(found[0] or "{}"))
        if known.get("input") != digest:
            raise Problem("idempotency_conflict", "This Idempotency-Key was used for a different video", 409)
        return str(known["key"]), said
    if not named and not visit:
        if not operation:
            raise Problem("validation", "Send an Idempotency-Key, or name the shot", 422)
        key = "shot_" + _hash(who.actor, "operation", operation)
    else:
        key = "shot_" + _hash(who.actor, task_id or "", visit or "", digest, name)
    if alias:
        H.event(c, who.actor, ALIAS, alias, {"key": key, "input": digest})
    return key, said


def _state(c: Any, who: Any, key: str, last: str | None, detail: Row) -> Row | Problem | None:
    """What an existing shot answers: its job when it started, a Problem when it is in flight or unknown (marking a
    sent shot that is not in flight unknown), or None when it may be sent."""
    if last == STARTED:
        return dict(detail["job"])
    with _LOCK:
        if last == SENT and key in _IN_FLIGHT:
            return Problem("paid_render_busy", "This shot is still being sent; try again shortly", 429, True)
    if last == SENT:  # sent, never answered and not in flight: the server stopped mid-call
        _unknown(c, who.actor, key, detail)
    if last in (SENT, UNKNOWN):
        return _reconcile(str(detail.get("shot") or ""))
    return None


def resolve(store: Any, who: Any, job: Row, shot: str | None, operation: str | None = None) -> Row | None:
    """Before any check: the job of a shot that started, or None for one that may be sent; raises for a shot in flight
    or unknown. The request's Idempotency-Key is kept as an alias of its shot either way."""
    with store.transaction(durable=True) as c:
        key, _ = _shot(c, who, job, shot, operation)
        state = _state(c, who, key, *_latest(c, key))
    if isinstance(state, Problem):
        raise state
    return state


def send(
    store: Any, who: Any, job: Row, shot: str | None, submit: Callable[[str], Answer], operation: str | None = None
) -> Answer:
    """Send a paid render once: `submit(key)` makes the call, with the shot key as its Idempotency-Key, and answers
    (status, answer, code). The same shot started already answers its job with no call; a call that raises is unknown."""
    key, started = begin(store, who, job, shot, operation)
    if started:
        return 202, started, ""
    status, answer, code = 0, {}, "unreachable"  # a call that raises: unknown
    try:
        status, answer, code = submit(key)
    finally:
        end(store, who, key, status, answer, code)
    return status, answer, code


def begin(
    store: Any, who: Any, job: Row, shot: str | None = None, operation: str | None = None
) -> tuple[str, Row | None]:
    """Write the shot down before it is sent. Answers (key, None) to send it now (it is in flight until `end`), or
    (key, the started job); raises for a shot in flight (429) or unknown (409 `reconcile_shot`)."""
    key, sending = "", False
    try:
        with store.transaction(durable=True) as c:
            key, said = _shot(c, who, job, shot, operation)
            last, detail = _latest(c, key)
            state = _state(c, who, key, last, detail)
            if state is None:
                with _LOCK:
                    sending = key not in _IN_FLIGHT
                    _IN_FLIGHT.add(key)
                if not sending:
                    state = Problem("paid_render_busy", "This shot is still being sent; try again shortly", 429, True)
                else:  # a shot sent again after a refusal keeps its first card, visit and name
                    H.event(c, who.actor, SENT, key, {**said, **{k: detail[k] for k in CARRIED if k in detail}})
    except BaseException:
        if sending:
            with _LOCK:
                _IN_FLIGHT.discard(key)
        raise
    if isinstance(state, Problem):
        raise state
    return key, state


def end(store: Any, who: Any, key: str, status: int, answer: Row, code: str) -> None:
    """Write the Gateway's answer to a sent shot; an unknown one asks the person and refuses (`reconcile_shot`)."""
    detail: Row = {}
    try:
        with store.transaction() as c:
            _, detail = _latest(c, key)
            if status == 202:
                H.event(c, who.actor, STARTED, key, {**detail, "job": answer})
            elif refused(status, answer, code):
                H.event(c, who.actor, REFUSED, key, {**detail, "status": status, "code": code})
            else:
                _unknown(c, who.actor, key, detail)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(key)
    if status != 202 and not refused(status, answer, code):
        raise _reconcile(str(detail.get("shot") or ""))


def sweep(c: Any) -> int:
    """Run housekeeping. Every shot whose latest event is `sent`, `STALE_MINUTES` old or older and not in flight here,
    is unknown, and its card's person is asked about it with no request needed. Every unknown shot on an open card
    whose question was never posted, was cleared unanswered or can no longer be answered is asked about again.
    Answers how many it asked about."""
    marks = ",".join("?" * len(ACTIONS))
    later = f"NOT EXISTS(SELECT 1 FROM events o WHERE o.action IN ({marks}) AND o.target=s.target AND o.rowid>s.rowid)"
    stale = c.execute(
        f"SELECT s.actor, s.target, s.detail FROM events s WHERE s.action=? AND s.created_at<=? AND {later}",
        (SENT, H.shift(H.now(), minutes=-STALE_MINUTES), *ACTIONS),
    ).fetchall()
    done = 0
    for actor, key, detail in stale:
        with _LOCK:
            if key in _IN_FLIGHT:
                continue
        _unknown(c, str(actor), str(key), dict(json.loads(detail or "{}")))
        done += 1
    unknown = c.execute(
        f"SELECT s.actor, s.target, s.detail FROM events s JOIN tasks t ON t.id=json_extract(s.detail, '$.task') "
        f"WHERE s.action=? AND t.status='open' AND {later}",
        (UNKNOWN, *ACTIONS),
    ).fetchall()
    for actor, key, detail in unknown:
        said = dict(json.loads(detail or "{}"))
        if _asked(c, said.get("asked")):
            continue
        asked = _ask(c, str(actor), said)
        if asked:
            H.event(c, str(actor), UNKNOWN, str(key), {**said, "asked": asked})
            done += 1
    return done


def _asked(c: Any, mark_id: object) -> bool:
    """Whether a shot's reconcile question still stands: open and answerable by its person, or answered."""
    from . import question_marks as _marks

    marks: Any = _marks
    mark = c.execute("SELECT * FROM question_marks WHERE id=?", (mark_id,)).fetchone() if mark_id else None
    if not mark:
        return False
    if mark["cleared_at"]:
        return mark["cleared_reason"] in ANSWERED
    return bool(marks.answerable(c, dict(mark)))


def _reconcile(name: str) -> Problem:
    return Problem(
        "reconcile_shot",
        f"The Gateway never confirmed shot {name}, so it may have been charged. It is not sent again: a person is "
        "asked to reconcile it, and a new take needs a new shot name.",
        409,
    )


def _open_ask(c: Any, task_id: str) -> Row | None:
    """The card's reconcile question while it stands open, with `shots` the names it lists, or None."""
    found = c.execute(
        "SELECT json_extract(detail, '$.asked') FROM events WHERE action=? AND json_extract(detail, '$.task')=? "
        "AND json_extract(detail, '$.asked') IS NOT NULL ORDER BY rowid DESC LIMIT 1",
        (UNKNOWN, task_id),
    ).fetchone()
    mark = found and c.execute("SELECT * FROM question_marks WHERE id=? AND cleared_at IS NULL", (found[0],)).fetchone()
    if not mark or not _asked(c, mark["id"]):
        return None
    rows = c.execute(
        "SELECT json_extract(detail, '$.shot') FROM events WHERE action=? AND json_extract(detail, '$.asked')=? "
        "ORDER BY rowid",
        (UNKNOWN, mark["id"]),
    )
    return {**dict(mark), "shots": list(dict.fromkeys(str(row[0]) for row in rows))}


def _text(shots: list[str]) -> tuple[str, str]:
    """The question's short first line, and its whole text: every shot by name, then why."""
    line = f"Reconcile {len(shots)} shot" + ("" if len(shots) == 1 else "s")
    names = "\n".join(f"- {name}" for name in shots)
    return line, f"{line}\n\n{names}\n\n{WHY}"


def _botops_free(c: Any, task: Row) -> str | None:
    """BotOps, when it is here and has no open question in the card's chat room (a new one would replace it)."""
    from . import person_asks as _asks
    from . import question_marks as _marks

    asks: Any = _asks
    marks: Any = _marks
    bot = asks.author(c)
    conv = H.conversation(c, task.get("conversation_id")) if task.get("conversation_id") else None
    if bot != asks.botops() or not conv:
        return None
    taken = c.execute(
        "SELECT 1 FROM question_marks WHERE room_id=? AND bot_id=? AND cleared_at IS NULL", (marks.room_of(conv), bot)
    ).fetchone()
    return None if taken else str(bot)


def _ask(c: Any, actor: str, detail: Row) -> str | None:
    """Add the shot to its card's one reconcile question for the card's person; the question's id, or None when there
    is no card, no person, or BotOps cannot ask now (the sweep tries again)."""
    from . import question_marks as _marks
    from . import task_engine as _engine
    from . import waiting_on

    marks: Any = _marks
    engine: Any = _engine
    task_id = str(detail.get("task") or "")
    shot = str(detail.get("shot") or "")
    try:
        task: Row | None = (engine.task(c, task_id) or H.task(c, task_id)) if task_id else None
        mark = _open_ask(c, task_id) if task else None
        if task and mark:  # the card's question lists this shot too, edited in place
            line, text = _text(list(dict.fromkeys([*mark["shots"], shot])))
            c.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (text, H.now(), mark["message_id"]))
            c.execute("UPDATE question_marks SET line=? WHERE id=?", (line, mark["id"]))
            H.event(c, mark["bot_id"], "message.edited", mark["message_id"], {"task": task_id})
            return str(mark["id"])
        person = waiting_on.person_for(c, task) if task else None
        bot = _botops_free(c, task) if task and person else None
        if task and person and bot:
            return str(marks.ask(c, bot, task_id, person, _text([shot])[1], checked=False)["id"])
    except Exception:  # a refused question still leaves the shot unknown, never sent again
        log.exception("A paid render's reconcile question was refused")
    return None


def _unknown(c: Any, actor: str, key: str, detail: Row) -> None:
    """Mark the shot unknown and ask about it on its card (`_ask`); the sweep asks later when BotOps cannot now."""
    asked = _ask(c, actor, detail)
    if not asked:
        log.warning("A paid render's outcome is unknown and no person on a card is asked about it yet")
    H.event(c, actor, UNKNOWN, key, {**detail, "asked": asked})
