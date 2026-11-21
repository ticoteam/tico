# pyright: strict
"""One charge per paid shot (master plan P2 D2): a paid render is written down before it is sent, and one whose outcome
is unknown is never sent again on its own.

The paid render is a Gateway video (POST /api/v2/gateway/videos, backend/providers_routes.py). Its shot key is the
asker, the card's open visit, the input's version (a hash of everything the Gateway is sent, `shot_of`: worked out once
a request, outside any transaction) and the shot's name. A request with no name outside a visit is keyed to its own
Idempotency-Key instead (none is a 422). A request's Idempotency-Key is kept as an alias of its shot
(`paid_render.alias`) once that shot was sent: with its `sent`, or when the request finds it sent already. It is read
first: a retry after the card moved to a new visit is the same shot, and the same key with another input is 409
`idempotency_conflict`.

`resolve` runs before the Gateway and budget checks, in a read: a shot that started answers its job, one in flight is
busy (429), one whose outcome is unknown is refused (409 `reconcile_shot`, which no client retries) and one its person
reconciled is refused too (409 `shot_reconciled`). It writes only for a shot sent before. Only a new shot,
or one the Gateway refused, goes on to the checks and `send`. Before the call, `begin` writes the alias and
`paid_render.sent` in its own durable transaction, so a restart keeps them, with the card, visit and name the alias
first recorded; the key goes to the Gateway as the request's Idempotency-Key. `end` writes the answer:
`paid_render.started` (the job), `paid_render.refused` (a certain refusal: nothing was charged, so the shot may be sent
again) or `paid_render.unknown`.

A certain refusal is judged by the status and the Gateway's code only, never by whether a reason came with it: a 4xx
other than 408, HQ's `503 unconfigured` (refused before anything is sent), `502 auth` and `502 http` (the provider
refused the key or the request). Everything else is unknown: no answer, a timeout, `502 unreadable`, any other 5xx. A
shot whose latest event is `sent` and is not in flight is unknown too: the next request finds it, and the Run
housekeeping sweep (`sweep`) does once it is `STALE_MINUTES` old. That shot is never sent again.

Its card waits on its person for it: "Reconcile 1 shot" (waiting_on.with_shots, from `unsettled` at read time: no
child task, no question, no table), in their Needs you and status line, until they check Gateway usage and mark it
reconciled
(`reconcile`, POST /api/v2/tasks/{tid}/shots/reconcile, `tico gateway reconcile`): a `paid_render.reconciled` event. A
reconciled shot is never sent again; the agent names a new shot to render it again. The refusal names every unknown
shot of the card (key and name) in its `shots`. The events table holds the keys (its `events_action_target` index finds
them, `events_action_ts` the unknown ones).
"""

import hashlib
import json
import threading
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from . import hubdb, read_cache
from .store import Problem

H: Any = hubdb
Row = dict[str, Any]
Answer = tuple[int, Row, str]  # (status, answer, code), as clients/gateway.py answers

SENT, STARTED, REFUSED, UNKNOWN, RECONCILED = (
    "paid_render.sent",
    "paid_render.started",
    "paid_render.refused",
    "paid_render.unknown",
    "paid_render.reconciled",  # its person checked Gateway usage: never sent again
)
ACTIONS = (SENT, STARTED, REFUSED, UNKNOWN, RECONCILED)
ALIAS = "paid_render.alias"  # a request's Idempotency-Key -> the shot key it resolved to
WAIT = "paid_render:"  # + the card's id: its "Reconcile N shots" wait's id (waiting_on.with_shots)
NAME_WORDS = 6  # a shot with no name is named by its prompt's first words
LINE_NAMES = 120  # how much of the shots' names the wait's line says
STALE_MINUTES = 5  # a sent shot this old with no answer, and not in flight, is unknown (`sweep`)
SWEEP_DAYS = 30  # how far back `sweep` reads
# The 5xx answers that are a certain refusal: HQ refused before sending (`unconfigured`), the provider refused the key
# (`auth`) or the request (`http`). Every other 5xx, a timeout among them, may have charged.
REFUSALS = {(503, "unconfigured"), (502, "auth"), (502, "http")}

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()

# No later event of the shot `s`: its latest is `s` itself.
_LATER = (
    f"NOT EXISTS(SELECT 1 FROM events o WHERE o.action IN ({','.join('?' * len(ACTIONS))}) AND o.target=s.target "
    "AND o.rowid>s.rowid)"
)


class Shot(NamedTuple):
    """What a request asks for (`shot_of`): the input's digest, the name it gave, and the name the shot goes by."""

    digest: str
    named: str
    name: str


def shot_of(job: Row, shot: str | None) -> Shot:
    """The request's shot, worked out once before any transaction: hashing a job (up to 6 MB of frames) never holds the
    write lock."""
    digest = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
    named = " ".join(str(shot or "").split())
    return Shot(digest, named, named or " ".join(str(job.get("prompt") or "").split()[:NAME_WORDS]))


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


def _shot(c: Any, who: Any, shot: Shot, operation: str | None) -> tuple[str, Row, tuple[str, Row] | None]:
    """The shot key, what a send writes with it ({task, visit, input, shot}), and the alias a send keeps for the
    request's Idempotency-Key ((alias, detail), None when it has one already or no key). A key kept before names the
    shot it was sent as."""
    task_id, visit = _turn(c, who)
    said: Row = {"task": task_id, "visit": visit, "input": shot.digest, "shot": shot.name}
    alias = "op_" + _hash(who.actor, operation) if operation else ""
    found = (
        alias and c.execute("SELECT detail FROM events WHERE action=? AND target=? LIMIT 1", (ALIAS, alias)).fetchone()
    )
    if found:
        known = dict(json.loads(found[0] or "{}"))
        if known.get("input") != shot.digest or known.get("shot", shot.named) != shot.named:
            raise Problem("idempotency_conflict", "This Idempotency-Key was used for a different video", 409)
        # The card, visit and name it was first asked under, wherever the card is now.
        said = {
            **said,
            **{k: known[f] for k, f in (("task", "task"), ("visit", "visit"), ("shot", "name")) if f in known},
        }
        return str(known["key"]), said, None
    if not shot.named and not visit:
        if not operation:
            raise Problem("validation", "Send an Idempotency-Key, or name the shot", 422)
        key = "shot_" + _hash(who.actor, "operation", operation)
    else:
        key = "shot_" + _hash(who.actor, task_id or "", visit or "", shot.digest, shot.name)
    kept: Row = {
        "key": key,
        "input": shot.digest,
        "shot": shot.named,
        "task": task_id,
        "visit": visit,
        "name": shot.name,
    }
    return key, said, ((alias, kept) if alias else None)


def _busy() -> Problem:
    return Problem("paid_render_busy", "This shot is still being sent; try again shortly", 429, True)


def _stranded(key: str, last: str | None) -> bool:
    """A sent shot never answered and not in flight here: the server stopped mid-call, so its outcome is unknown."""
    with _LOCK:
        return last == SENT and key not in _IN_FLIGHT


def _state(c: Any, key: str, last: str | None, detail: Row) -> Row | Problem | None:
    """What an existing shot answers, a read: its job when it started, a Problem when it is in flight, unknown or
    reconciled, or None when it may be sent."""
    if last == STARTED:
        return dict(detail["job"])
    if last == RECONCILED:
        return Problem(
            "shot_reconciled",
            "This shot was reconciled, so it is not sent again. Name a new shot only if the card's person wants it "
            "rendered again.",
            409,
        )
    if last == SENT and not _stranded(key, last):
        return _busy()
    if last in (SENT, UNKNOWN):
        return _reconcile(_shots(c, key, detail))
    return None


# The same shot sent from another visit of its card: same asker, card, input and name.
_TWINS = (
    "SELECT s.target FROM events s WHERE s.actor=? AND s.action=? AND s.target<>? "
    "AND json_extract(s.detail, '$.task')=? AND json_extract(s.detail, '$.input')=? "
    "AND json_extract(s.detail, '$.shot')=?"
)


def _twin(c: Any, actor: str, key: str, said: Row) -> Problem | None:
    """A new key's refusal when the card has the same shot unknown or reconciled under another visit's key: a stage
    move never makes it a new shot that may be charged again. None outside a card, or with no such shot."""
    if not said.get("task"):
        return None
    for (other,) in c.execute(_TWINS, (actor, SENT, key, said["task"], said["input"], said["shot"])).fetchall():
        last, detail = _latest(c, str(other))
        if last in (UNKNOWN, RECONCILED):
            found = _state(c, str(other), last, detail)
            return found if isinstance(found, Problem) else None
    return None


def _settle(c: Any, who: Any, key: str) -> Row | Problem | None:
    """`_state` in a write: a stranded sent shot is marked unknown first."""
    last, detail = _latest(c, key)
    if _stranded(key, last):
        _unknown(c, who.actor, key, detail)
    return _state(c, key, last, detail)


def resolve(store: Any, who: Any, shot: Shot, operation: str | None = None) -> Row | None:
    """Before any check: the job of a shot that started, or None for one that may be sent; raises for a shot in flight,
    unknown or reconciled. A read for a new shot, which `begin` writes down: it writes only for a shot sent before, to
    mark it unknown when it was stranded and keep the request's key as its alias, so a retry after the card moved
    finds that shot again."""
    with store.read() as c:
        key, said, alias = _shot(c, who, shot, operation)
        last, detail = _latest(c, key)
        stranded = _stranded(key, last)
        state = None if stranded else _state(c, key, last, detail) or _twin(c, who.actor, key, said)
    if stranded or (alias and state is not None):
        with store.transaction() as c:
            key, said, alias = _shot(c, who, shot, operation)
            if alias:
                H.event(c, who.actor, ALIAS, *alias)
            state = _settle(c, who, key) or _twin(c, who.actor, key, said)
    if isinstance(state, Problem):
        raise state
    return state


def send(store: Any, who: Any, shot: Shot, submit: Callable[[str], Answer], operation: str | None = None) -> Answer:
    """Send a paid render once: `submit(key)` makes the call, with the shot key as its Idempotency-Key, and answers
    (status, answer, code). The same shot started already answers its job with no call; a call that raises is unknown."""
    key, started = begin(store, who, shot, operation)
    if started:
        return 202, started, ""
    status, answer, code = 0, {}, "unreachable"  # a call that raises: unknown
    try:
        status, answer, code = submit(key)
    finally:
        end(store, who, key, status, answer, code)
    return status, answer, code


def begin(store: Any, who: Any, shot: Shot, operation: str | None = None) -> tuple[str, Row | None]:
    """Write the shot down before it is sent, its request's alias with it. Answers (key, None) to send it now (it is in
    flight until `end`), or (key, the started job); raises for a shot in flight (429), unknown (409 `reconcile_shot`) or
    reconciled (409 `shot_reconciled`)."""
    key, sending = "", False
    try:
        with store.transaction(durable=True) as c:
            key, said, alias = _shot(c, who, shot, operation)
            if alias:  # kept whatever it finds: a request that lost a race retries into the same shot
                H.event(c, who.actor, ALIAS, *alias)
            state = _settle(c, who, key) or _twin(c, who.actor, key, said)
            if state is None:
                with _LOCK:
                    sending = key not in _IN_FLIGHT
                    _IN_FLIGHT.add(key)
                if not sending:
                    state = _busy()
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
                shots = _shots(c, key, detail)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(key)
    if shots:
        raise _reconcile(shots)


def sweep(c: Any) -> int:
    """Run housekeeping, under the write lock on every tick, so it reads only shots of the last `SWEEP_DAYS` (the events
    index's action, then created_at). Every shot whose latest event is `sent`, `STALE_MINUTES` old or older and not in
    flight here, is marked unknown, and its card waits on its person for it (`_unknown`). Answers how many it marked."""
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
    if detail.get("task"):
        _moved(c, str(detail["task"]))


_UNSETTLED = (
    "SELECT json_extract(s.detail, '$.task'), s.target, json_extract(s.detail, '$.shot'), s.created_at FROM events s "
    f"WHERE s.action=? AND {_LATER} %s ORDER BY s.rowid"
)


def _rows(found: Iterable[Any]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for task, key, name, at in found:
        if task:
            out.setdefault(str(task), []).append({"key": str(key), "name": str(name or ""), "since": str(at)})
    return out


def unsettled(c: Any, task_id: str) -> list[Row]:
    """One card's shots whose outcome is unknown, each {key, name, since}, oldest first: from the unknown events alone
    (`events_action_ts`), which only a call with no answer writes. A sent shot left unanswered joins them once it is
    marked unknown: by the next request for it, or by `sweep` on the next tick, so a read never scans every shot ever
    sent."""
    sql = _UNSETTLED % "AND json_extract(s.detail, '$.task')=?"
    return _rows(c.execute(sql, (UNKNOWN, *ACTIONS, task_id))).get(task_id, [])


def cards(c: Any) -> dict[str, list[Row]]:
    """`unsettled` for every card, {task id: shots}, read once a snapshot: for a list's reads (board rows, Needs you)."""
    return read_cache.value(
        c, ("paid_renders.cards",), lambda: _rows(c.execute(_UNSETTLED % "", (UNKNOWN, *ACTIONS))), shared=True
    )


def line(shots: list[Row]) -> str:
    """The card's wait: "Reconcile 2 shots: kettle close-up, intro", its names clipped to `LINE_NAMES`."""
    count = len(shots)
    names = ", ".join(s["name"] for s in shots if s["name"])
    if len(names) > LINE_NAMES:
        names = names[: LINE_NAMES - 1].rstrip(", ") + "\u2026"
    return f"Reconcile {count} shot{'' if count == 1 else 's'}" + (f": {names}" if names else "")


def reconcile(c: Any, actor: str, task_id: str, keys: Iterable[str] = ()) -> list[Row]:
    """Mark the card's unknown shots reconciled, every one or those `keys` names: a `paid_render.reconciled` each. They
    leave the card's wait and are never sent again (`_state`). Answers the shots it marked, each {key, name}."""
    wanted = set(keys)
    shots = [s for s in unsettled(c, task_id) if not wanted or s["key"] in wanted]
    for s in shots:
        H.event(c, actor, RECONCILED, s["key"], {**_latest(c, s["key"])[1], "task": task_id})
    if shots:
        _moved(c, task_id)
    return [{"key": s["key"], "name": s["name"]} for s in shots]


def _moved(c: Any, task_id: str) -> None:
    """The card's wait moved: its row and its person's Needs you are sent again (pushed, not polled)."""
    from . import events as _events, task_engine, task_reads as _reads, waiting_on

    events: Any = _events
    reads: Any = _reads
    reads.status_moved(c, (), [task_id], kind="shots")  # an open task's Details reads its waits again
    row = task_engine.task(c, task_id)
    person = waiting_on.person_for(c, row) if row else None
    if person:
        events.needs_moved(c, [person], "ask")


def _shots(c: Any, key: str, detail: Row) -> list[Row]:
    """The unknown shots a refusal names, each {key, name}: this one, and every other of its card (`unsettled`)."""
    shots = {key: str(detail.get("shot") or "")}
    for found in unsettled(c, str(detail.get("task") or "")) if detail.get("task") else ():
        shots.setdefault(found["key"], found["name"])
    return [{"key": k, "name": n} for k, n in shots.items()]


def _reconcile(shots: list[Row]) -> Problem:
    names = ", ".join(f"{s['name']} ({s['key']})" for s in shots)
    return Problem(
        "reconcile_shot",
        f"The Gateway never confirmed {names}, so it may have been charged, and it is not sent again. Its card asks "
        "its person to check Gateway usage and mark it reconciled. Never make it again under another name.",
        409,
        extra={"shots": shots},
    )
