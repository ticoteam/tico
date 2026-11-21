# pyright: strict
"""One charge per paid shot (master plan P2 D2): a paid render is written down before it is sent, and one whose outcome
is unknown is never sent again on its own.

The paid render is a Gateway video (POST /api/v2/gateway/videos, backend/providers_routes.py). Its shot key is the
asker, the card's open visit, the input's version (a hash of everything the Gateway is sent) and the shot's name. A
request with no name outside a visit is keyed to its own Idempotency-Key instead (none is a 422). The Idempotency-Key a
shot was first sent under is kept as its alias (`paid_render.alias`) and read first, so a retry after the card moved
to a new visit is the same shot; the same key with another input is 409 `idempotency_conflict`.

A shot that already started is answered from `replay` before any other check. Before a new call, `begin` writes the key
as an event `paid_render.sent` in its own durable transaction, so a server or runner restart keeps it, and the key goes
to the Gateway as the request's Idempotency-Key. `end` writes the answer beside it: `paid_render.started` (the job, which
a retry of the same shot gets back with no second call), `paid_render.refused` (the Gateway refused it for certain and
charged nothing, so it may be sent again) or `paid_render.unknown`.

Unknown is every other answer: none came back (a timeout, a lost connection), a 5xx that gives no refusal of its own
(HQ's `503 unavailable` is a provider timeout that may still charge), a 202 whose id could not be read. A shot whose
latest event is `sent`, not in flight, is unknown too: the next request for it finds it, and the Run housekeeping sweep
(`sweep`) does once it is `STALE_MINUTES` old. That shot is never sent again. On its card one question, "Reconcile N
shots", asks the person the card falls to (waiting_on.person_for), naming every shot in its text; BotOps asks it, or
the agent when BotOps is not here, and never in place of the asker's own open question. A later unknown shot on the
card is added to it, edited in place; one nobody could ask about yet is asked about by the sweep once someone can. The
request is refused with `reconcile_shot` (409, which no client retries). A new take is a new shot name, a changed input
or a new visit. No new table: the events table holds the keys (its `events_action_target` index finds them).
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
ALIAS = "paid_render.alias"  # a request's Idempotency-Key -> the shot key it was sent under
NAME_WORDS = 6  # a shot with no name is named by its prompt's first words
STALE_MINUTES = 5  # a sent shot this old with no answer, and not in flight, is unknown (`sweep`)
WHY = (
    "The Gateway never confirmed it, so it may have been charged. It is not made again on its own: check Gateway "
    "usage first, and give a new take a new shot name."
)

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()


def refused(status: int, answer: Row) -> bool:
    """Whether the Gateway refused a video for certain, so nothing was charged: a 4xx, or a 5xx that says why (the
    provider's own refusal, which HQ passes on as its `detail`)."""
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


def _alias(who: Any, operation: str | None) -> str:
    return "op_" + _hash(who.actor, operation) if operation else ""


def _shot(c: Any, who: Any, job: Row, shot: str | None, operation: str | None) -> tuple[str, Row]:
    """The shot key and what is written with it: {task, visit, input, shot}. A request's Idempotency-Key that was sent
    already names its shot (`ALIAS`), whatever visit the card is in now; with another input it is a conflict."""
    digest = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
    named = " ".join(str(shot or "").split())
    name = named or " ".join(str(job.get("prompt") or "").split()[:NAME_WORDS])
    task_id, visit = _turn(c, who)
    said: Row = {"task": task_id, "visit": visit, "input": digest, "shot": name}
    alias = _alias(who, operation)
    found = (
        alias and c.execute("SELECT detail FROM events WHERE action=? AND target=? LIMIT 1", (ALIAS, alias)).fetchone()
    )
    if found:
        sent = dict(json.loads(found[0] or "{}"))
        if sent.get("input") != digest:
            raise Problem("idempotency_conflict", "This Idempotency-Key was used for a different video", 409)
        return str(sent["key"]), said
    if not named and not visit:
        if not operation:
            raise Problem("validation", "Send an Idempotency-Key, or name the shot", 422)
        return "shot_" + _hash(who.actor, "operation", operation), said
    return "shot_" + _hash(who.actor, task_id or "", visit or "", digest, name), said


def replay(store: Any, who: Any, job: Row, shot: str | None, operation: str | None = None) -> Row | None:
    """The job of this shot when it already started, else None. Read before any other check: a replay sends nothing."""
    with store.read() as c:
        key, _ = _shot(c, who, job, shot, operation)
        last, detail = _latest(c, key)
    return dict(detail["job"]) if last == STARTED else None


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
    (key, the started job) when the same shot already started. Refuses a shot that is in flight (429, retried) or whose
    outcome is unknown (409 `reconcile_shot`, asking the person once)."""
    key, sending, name = "", False, ""
    try:
        with store.transaction(durable=True) as c:
            key, said = _shot(c, who, job, shot, operation)
            name = said["shot"]
            last, detail = _latest(c, key)
            if last == STARTED:
                return key, dict(detail["job"])
            with _LOCK:
                if last == SENT and key in _IN_FLIGHT:
                    raise Problem("paid_render_busy", "This shot is still being sent; try again shortly", 429, True)
                sending = last not in (SENT, UNKNOWN)
                if sending:
                    _IN_FLIGHT.add(key)
            if sending:
                H.event(c, who.actor, SENT, key, said)
                if (
                    operation
                    and not c.execute(
                        "SELECT 1 FROM events WHERE action=? AND target=?", (ALIAS, _alias(who, operation))
                    ).fetchone()
                ):
                    H.event(c, who.actor, ALIAS, _alias(who, operation), {"key": key, "input": said["input"]})
            elif last == SENT:  # sent, never answered and not in flight: the server stopped mid-call
                _unknown(c, who.actor, key, detail)
    except BaseException:
        if sending:
            with _LOCK:
                _IN_FLIGHT.discard(key)
        raise
    if not sending:
        raise _reconcile(name)
    return key, None


def end(store: Any, who: Any, key: str, status: int, answer: Row, code: str) -> None:
    """Write the Gateway's answer to a sent shot; an unknown one asks the person and refuses (`reconcile_shot`)."""
    detail: Row = {}
    try:
        with store.transaction() as c:
            _, detail = _latest(c, key)
            if status == 202:
                H.event(c, who.actor, STARTED, key, {**detail, "job": answer})
            elif refused(status, answer):
                H.event(c, who.actor, REFUSED, key, {**detail, "status": status, "code": code})
            else:
                _unknown(c, who.actor, key, detail)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(key)
    if status != 202 and not refused(status, answer):
        raise _reconcile(str(detail.get("shot") or ""))


def sweep(c: Any) -> int:
    """Run housekeeping. Every shot whose latest event is `sent`, `STALE_MINUTES` old or older and not in flight here,
    is unknown, and its card's person is asked to reconcile it with no request needed. Every unknown shot on an open
    card that nobody was asked about yet (BotOps could not ask) is asked about now. Answers how many it asked about."""
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
    unasked = c.execute(
        f"SELECT s.actor, s.target, s.detail FROM events s JOIN tasks t ON t.id=json_extract(s.detail, '$.task') "
        f"WHERE s.action=? AND json_extract(s.detail, '$.asked') IS NULL AND t.status='open' AND {later}",
        (UNKNOWN, *ACTIONS),
    ).fetchall()
    for actor, key, detail in unasked:
        said = dict(json.loads(detail or "{}"))
        asked = _ask(c, str(actor), said)
        if asked:
            H.event(c, str(actor), UNKNOWN, str(key), {**said, "asked": asked})
            done += 1
    return done


def _reconcile(name: str) -> Problem:
    return Problem(
        "reconcile_shot",
        f"The Gateway never confirmed shot {name}, so it may have been charged. It is not sent again: a person is "
        "asked to reconcile it, and a new take needs a new shot name.",
        409,
    )


def _open_ask(c: Any, task_id: str) -> Row | None:
    """The open reconcile question on this card, with `shots` the names it lists, or None."""
    found = c.execute(
        "SELECT json_extract(detail, '$.asked') FROM events WHERE action=? AND json_extract(detail, '$.task')=? "
        "AND json_extract(detail, '$.asked') IS NOT NULL ORDER BY rowid DESC LIMIT 1",
        (UNKNOWN, task_id),
    ).fetchone()
    mark = found and c.execute("SELECT * FROM question_marks WHERE id=? AND cleared_at IS NULL", (found[0],)).fetchone()
    if not mark:
        return None
    shots = [
        str(row[0])
        for row in c.execute(
            "SELECT json_extract(detail, '$.shot') FROM events WHERE action=? AND json_extract(detail, '$.asked')=? "
            "ORDER BY rowid",
            (UNKNOWN, mark["id"]),
        )
    ]
    return {**dict(mark), "shots": shots}


def _text(shots: list[str]) -> tuple[str, str]:
    """The question's short first line, and its whole text: every shot by name, then why."""
    line = f"Reconcile {len(shots)} shot" + ("" if len(shots) == 1 else "s")
    names = "\n".join(f"- {name}" for name in shots)
    return line, f"{line}\n\n{names}\n\n{WHY}"


def _asker(c: Any, actor: str, task_id: str) -> str | None:
    """Who asks: BotOps, else the agent; never a bot whose own open question on the card the new one would replace."""
    from . import person_asks as _asks
    from . import question_marks as _marks

    asks: Any = _asks
    marks: Any = _marks
    for bot in (asks.author(c), actor if H.is_bot(actor) else None):
        if bot and marks.open_of(c, bot, task_id) is None:
            return str(bot)
    return None


def _ask(c: Any, actor: str, detail: Row) -> str | None:
    """Add the shot to its card's one reconcile question for the card's person; the question's id, or None when there
    is no card, no person or no bot that can ask."""
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
            line, text = _text([*mark["shots"], shot])
            c.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (text, H.now(), mark["message_id"]))
            c.execute("UPDATE question_marks SET line=? WHERE id=?", (line, mark["id"]))
            H.event(c, mark["bot_id"], "message.edited", mark["message_id"], {"task": task_id})
            return str(mark["id"])
        person = waiting_on.person_for(c, task) if task else None
        bot = _asker(c, actor, task_id) if person else None
        if task and person and bot:
            return str(marks.ask(c, bot, task_id, person, _text([shot])[1], checked=False)["id"])
    except Exception:  # a refused question still leaves the shot unknown, never sent again
        log.exception("A paid render's reconcile question was refused")
    return None


def _unknown(c: Any, actor: str, key: str, detail: Row) -> None:
    """Mark the shot unknown and ask about it on its card (`_ask`); the sweep asks later when nobody could now."""
    asked = _ask(c, actor, detail)
    if not asked:
        log.warning("A paid render's outcome is unknown and no person on a card is asked about it yet")
    H.event(c, actor, UNKNOWN, key, {**detail, "asked": asked})
