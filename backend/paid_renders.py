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

A certain refusal is judged by the status and the Gateway's code only, never by whether a reason came with it: a 4xx
other than 408, HQ's `503 unconfigured` (refused before anything is sent), `502 auth` and `502 http` (the provider
refused the key or the request). Everything else is unknown: no answer, a timeout, `502 unreadable`, any other 5xx. A
shot whose latest event is `sent` and is not in flight is unknown too: the next request finds it, and the Run
housekeeping sweep (`sweep`) does once it is `STALE_MINUTES` old. That shot is never sent again.

While a card has unknown shots, it has one open child task "Reconcile shots" (`ensure`, after each unknown shot and on
every sweep): filed by Tico (BotOps is no party of the card, so it may not file under it), owned by the person the card falls to who can read it (waiting_on.person_for), its
text listing every unknown shot by its key, with its name, card and visit, edited in place as shots are added. The
card's `paid_render.reconcile` events keep which task lists which keys, so there is never a second open one. Closing
it reconciles the shots it lists; a later unknown shot opens a new one. A new take is a new shot name, a changed input
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
ALIAS = "paid_render.alias"  # a request's Idempotency-Key -> the shot key it resolved to
NAME_WORDS = 6  # a shot with no name is named by its prompt's first words
STALE_MINUTES = 5  # a sent shot this old with no answer, and not in flight, is unknown (`sweep`)
SWEEP_DAYS = 30  # how far back `sweep` reads
# The 5xx answers that are a certain refusal: HQ refused before sending (`unconfigured`), the provider refused the key
# (`auth`) or the request (`http`). Every other 5xx, a timeout among them, may have charged.
REFUSALS = {(503, "unconfigured"), (502, "auth"), (502, "http")}
RECONCILE = "paid_render.reconcile"  # a card's "Reconcile shots" task and the shot keys it lists
TITLE = "Reconcile shots"
CARRIED = ("task", "visit", "shot")  # what a shot sent again keeps from its first send
WHY = (
    "The Gateway never confirmed these videos, so they may have been charged. They are not made again on their own. "
    "Check Gateway usage, then close this task; a new take needs a new shot name."
)

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()


def refused(status: int, code: str) -> bool:
    """Whether the Gateway refused a video for certain, so nothing was charged: by its status and code only."""
    return (400 <= status < 500 and status != 408) or (status, code) in REFUSALS


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
        if known.get("input") != digest or known.get("shot", named) != named:
            raise Problem("idempotency_conflict", "This Idempotency-Key was used for a different video", 409)
        return str(known["key"]), said
    if not named and not visit:
        if not operation:
            raise Problem("validation", "Send an Idempotency-Key, or name the shot", 422)
        key = "shot_" + _hash(who.actor, "operation", operation)
    else:
        key = "shot_" + _hash(who.actor, task_id or "", visit or "", digest, name)
    if alias:
        H.event(c, who.actor, ALIAS, alias, {"key": key, "input": digest, "shot": named})
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
            elif refused(status, code):
                H.event(c, who.actor, REFUSED, key, {**detail, "status": status, "code": code})
            else:
                _unknown(c, who.actor, key, detail)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(key)
    if status != 202 and not refused(status, code):
        raise _reconcile(str(detail.get("shot") or ""))


# No later event of the shot `s`: its latest is `s` itself.
_LATER = (
    f"NOT EXISTS(SELECT 1 FROM events o WHERE o.action IN ({','.join('?' * len(ACTIONS))}) AND o.target=s.target "
    "AND o.rowid>s.rowid)"
)


def sweep(c: Any) -> int:
    """Run housekeeping, under the write lock on every tick, so it reads only unsettled shots of the last `SWEEP_DAYS`
    (the events index's action, then created_at). Every shot whose latest event is `sent`, `STALE_MINUTES` old or
    older and not in flight here, is unknown. Every open card with an unknown shot that no "Reconcile shots" task lists
    yet gets it listed (`ensure`). Answers how many shots it marked unknown and cards it opened or changed a task on."""
    now = H.now()
    since = H.shift(now, days=-SWEEP_DAYS)
    stale = c.execute(
        "SELECT s.actor, s.target, s.detail FROM events s WHERE s.action=? AND s.created_at>=? AND s.created_at<=? "
        f"AND {_LATER}",
        (SENT, since, H.shift(now, minutes=-STALE_MINUTES), *ACTIONS),
    ).fetchall()
    done = 0
    for actor, key, detail in stale:
        with _LOCK:
            if key in _IN_FLIGHT:
                continue
        _unknown(c, str(actor), str(key), dict(json.loads(detail or "{}")))
        done += 1
    cards = c.execute(
        "SELECT DISTINCT t.id FROM events s JOIN tasks t ON t.id=json_extract(s.detail, '$.task') "
        f"WHERE s.action=? AND s.created_at>=? AND t.status='open' AND {_LATER} "
        "AND NOT EXISTS(SELECT 1 FROM events r, json_each(r.detail, '$.keys') k WHERE r.action=? AND r.target=t.id "
        "AND k.value=s.target)",
        (UNKNOWN, since, *ACTIONS, RECONCILE),
    ).fetchall()
    return done + sum(1 for (card,) in cards if ensure(c, str(card)))


def _reconcile(name: str) -> Problem:
    return Problem(
        "reconcile_shot",
        f"The Gateway never confirmed shot {name}, so it may have been charged. It is not sent again: a person is given "
        "a task to reconcile it, and a new take needs a new shot name.",
        409,
    )


def _body(c: Any, card: Row, shots: list[tuple[str, Row]]) -> str:
    """The task's text: why, then every shot on its own line by its key, with its name, card and visit."""
    lines = [
        f"- {d.get('shot') or 'Unnamed'}: shot {key}, card {card.get('title') or card['id']}, visit {d.get('visit') or 'none'}"
        for key, d in shots
    ]
    return WHY + "\n\n" + "\n".join(lines)


def ensure(c: Any, card_id: str) -> bool:
    """The card's one open "Reconcile shots" task lists every unknown shot on it that no closed one listed: opened by
    Tico for the person the card falls to who can read it (waiting_on.person_for), or its text edited in place when
    it is open already. Answers whether it opened or changed one. Closing it reconciles the shots it lists."""
    from . import task_engine as _engine
    from . import waiting_on

    engine: Any = _engine
    unknown = {
        str(key): dict(json.loads(detail or "{}"))
        for key, detail in c.execute(
            f"SELECT s.target, s.detail FROM events s WHERE s.action=? AND json_extract(s.detail, '$.task')=? "
            f"AND {_LATER} ORDER BY s.rowid",
            (UNKNOWN, card_id, *ACTIONS),
        )
    }
    done: set[str] = set()
    child, listed, message = None, [], None
    for (detail,) in c.execute(
        "SELECT detail FROM events WHERE action=? AND target=? ORDER BY rowid", (RECONCILE, card_id)
    ):
        said = dict(json.loads(detail or "{}"))
        state = c.execute("SELECT status FROM tasks WHERE id=?", (said.get("child"),)).fetchone()
        if state and state[0] in H.ACTIVE_STATUSES:
            child, listed, message = said["child"], list(said.get("keys") or []), said.get("message")
        else:
            done.update(said.get("keys") or [])
            if said.get("child") == child:
                child, listed, message = None, [], None
    keys = listed + [k for k in unknown if k not in done and k not in listed]
    if not keys or keys == listed:
        return False
    card: Row | None = engine.task(c, card_id) or H.task(c, card_id)
    if not card:
        return False
    shots = [(k, unknown.get(k, {})) for k in keys]
    if child and message:
        c.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (_body(c, card, shots), H.now(), message))
        H.event(c, H.TICO_ACTOR, "message.edited", message, {"task": child})
    else:
        person = waiting_on.person_for(c, card)
        if not person or (H.task_private(c, card) and not H.task_private_readable(c, person, card)):
            log.warning("A card with unknown paid shots has no person who can read it; asked again on the next pass")
            return False
        # Tico files it: BotOps is no party of the card, and only a card's parties file a bot's sub-task under it.
        made: Row = H.task_create(
            c,
            H.TICO_ACTOR,
            TITLE,
            _body(c, card, shots),
            person,
            parent_id=card_id,
            deduplicate=False,
            lint=False,
            private=bool(H.task_private(c, card)),
        )
        child = str(made["id"])
        first = c.execute(
            "SELECT m.id FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id WHERE t.id=? "
            "ORDER BY m.rowid LIMIT 1",
            (child,),
        ).fetchone()
        message = str(first[0]) if first else None
    H.event(c, H.TICO_ACTOR, RECONCILE, card_id, {"child": child, "keys": keys, "message": message})
    return True


def _unknown(c: Any, actor: str, key: str, detail: Row) -> None:
    """Mark the shot unknown and list it on its card's "Reconcile shots" task (`ensure`); the sweep tries again when
    that cannot be done now."""
    H.event(c, actor, UNKNOWN, key, detail)
    if not detail.get("task"):
        log.warning("A paid render's outcome is unknown outside a card; nobody is given a task for it")
        return
    try:
        ensure(c, str(detail["task"]))
    except Exception:  # the shot stays unknown, never sent again; the sweep tries the task again
        log.exception("A paid render's reconcile task could not be opened")
