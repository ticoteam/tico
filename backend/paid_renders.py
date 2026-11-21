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
durable transaction, so a restart keeps it, with the card, visit and name the shot's alias first recorded; the key goes
to the Gateway as the request's Idempotency-Key. `end` writes the answer: `paid_render.started` (the job), `paid_render.refused`
(a certain refusal: nothing was charged, so the shot may be sent again) or `paid_render.unknown`.

A certain refusal is judged by the status and the Gateway's code only, never by whether a reason came with it: a 4xx
other than 408, HQ's `503 unconfigured` (refused before anything is sent), `502 auth` and `502 http` (the provider
refused the key or the request). Everything else is unknown: no answer, a timeout, `502 unreadable`, any other 5xx. A
shot whose latest event is `sent` and is not in flight is unknown too: the next request finds it, and the Run
housekeeping sweep (`sweep`) does once it is `STALE_MINUTES` old. That shot is never sent again.

The refusal names every unknown shot of the card (key and name) in its `shots`. The agent asks the card's person with
its own question to reconcile them, and never makes one again under another name. Housekeeping (`sweep`) only marks
stale sent shots unknown and posts nothing. No new table: the events table holds the keys (its `events_action_target`
index finds them).
"""

import hashlib
import json
import threading
from collections.abc import Callable
from typing import Any

from . import hubdb
from .store import Problem

H: Any = hubdb
Row = dict[str, Any]
Answer = tuple[int, Row, str]  # (status, answer, code), as clients/gateway.py answers

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
        # The card, visit and name it was first asked under, wherever the card is now.
        said = {
            **said,
            **{k: known[f] for k, f in (("task", "task"), ("visit", "visit"), ("shot", "name")) if f in known},
        }
        return str(known["key"]), said
    if not named and not visit:
        if not operation:
            raise Problem("validation", "Send an Idempotency-Key, or name the shot", 422)
        key = "shot_" + _hash(who.actor, "operation", operation)
    else:
        key = "shot_" + _hash(who.actor, task_id or "", visit or "", digest, name)
    if alias:
        H.event(
            c,
            who.actor,
            ALIAS,
            alias,
            {"key": key, "input": digest, "shot": named, "task": task_id, "visit": visit, "name": name},
        )
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
        return _reconcile(_unknown_shots(c, key, detail))
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
                else:
                    H.event(c, who.actor, SENT, key, said)
    except BaseException:
        if sending:
            with _LOCK:
                _IN_FLIGHT.discard(key)
        raise
    if isinstance(state, Problem):
        raise state
    return key, state


def end(store: Any, who: Any, key: str, status: int, answer: Row, code: str) -> None:
    """Write the Gateway's answer to a sent shot; an unknown one is refused (`reconcile_shot`)."""
    shots: list[Row] = []
    try:
        with store.transaction() as c:
            _, detail = _latest(c, key)
            if status == 202:
                H.event(c, who.actor, STARTED, key, {**detail, "job": answer})
            elif refused(status, code):
                H.event(c, who.actor, REFUSED, key, {**detail, "status": status, "code": code})
            else:
                _unknown(c, who.actor, key, detail)
                shots = _unknown_shots(c, key, detail)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(key)
    if shots:
        raise _reconcile(shots)


# No later event of the shot `s`: its latest is `s` itself.
_LATER = (
    f"NOT EXISTS(SELECT 1 FROM events o WHERE o.action IN ({','.join('?' * len(ACTIONS))}) AND o.target=s.target "
    "AND o.rowid>s.rowid)"
)


def sweep(c: Any) -> int:
    """Run housekeeping, under the write lock on every tick, so it reads only shots of the last `SWEEP_DAYS` (the events
    index's action, then created_at). Every shot whose latest event is `sent`, `STALE_MINUTES` old or older and not in
    flight here, is marked unknown; nothing is posted. Answers how many it marked."""
    now = H.now()
    stale = c.execute(
        "SELECT s.actor, s.target, s.detail FROM events s WHERE s.action=? AND s.created_at>=? AND s.created_at<=? "
        f"AND {_LATER}",
        (SENT, H.shift(now, days=-SWEEP_DAYS), H.shift(now, minutes=-STALE_MINUTES), *ACTIONS),
    ).fetchall()
    done = 0
    for actor, key, detail in stale:
        with _LOCK:
            if key in _IN_FLIGHT:
                continue
        _unknown(c, str(actor), str(key), dict(json.loads(detail or "{}")))
        done += 1
    return done


def _unknown(c: Any, actor: str, key: str, detail: Row) -> None:
    H.event(c, actor, UNKNOWN, key, detail)


def _unknown_shots(c: Any, key: str, detail: Row) -> list[Row]:
    """The unknown shots a refusal names: every one of this shot's card in the last `SWEEP_DAYS`, this one included,
    each {key, name}; outside a card, this one."""
    shots = {key: str(detail.get("shot") or "")}
    if detail.get("task"):
        for found, name in c.execute(
            "SELECT s.target, json_extract(s.detail, '$.shot') FROM events s WHERE s.action=? AND s.created_at>=? "
            f"AND json_extract(s.detail, '$.task')=? AND {_LATER} ORDER BY s.rowid",
            (UNKNOWN, H.shift(H.now(), days=-SWEEP_DAYS), detail["task"], *ACTIONS),
        ):
            shots.setdefault(str(found), str(name or ""))
    return [{"key": k, "name": n} for k, n in shots.items()]


def _reconcile(shots: list[Row]) -> Problem:
    names = ", ".join(f"{s['name']} ({s['key']})" for s in shots)
    return Problem(
        "reconcile_shot",
        f"The Gateway never confirmed {names}, so it may have been charged, and it is not sent again. Ask the card's "
        "person: Reconcile shot <name>: check Gateway usage before re-rendering. Never make it again under another name.",
        409,
        extra={"shots": shots},
    )
