# pyright: strict
"""One charge per paid shot (master plan P2 D2): a paid render is written down before it is sent, and one whose outcome
is unknown is never sent again on its own.

The paid render is a Gateway video (POST /api/v2/gateway/videos, backend/providers_routes.py). Its shot key is the
asker, the card's open visit, the input's version (a hash of everything the Gateway is sent) and the shot's name. Before
the call, `begin` writes it as an event `paid_render.sent` in its own durable transaction, so a server or runner restart
keeps it, and the key goes to the Gateway as the request's Idempotency-Key. `end` writes the answer beside it:
`paid_render.started` (the job: a retry of the same shot gets it back with no second call), `paid_render.refused` (the
Gateway said no and charged nothing, so the shot may be sent again) or `paid_render.unknown`.

Unknown: no answer came back (a timeout, a lost connection, a 504, an answer that could not be read), or a sent shot has
no answer and is not in flight in this process (the server restarted mid-call). That shot is never sent again. The bot on
the card asks the person the card falls to (waiting_on.person_for) "Reconcile shot <name>", a ❓ on the card
(person_asks.ask_on), once per shot, and the request is refused with `reconcile_shot` (409, which no client retries).
A new take is a new shot name, a changed input or a new visit. No new table: the events table holds the key (its
`events_action_target` index finds it).
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
NAME_WORDS = 6  # a shot with no name is named by its prompt's first words
WHY = (
    "The Gateway never confirmed this video, so it may have been charged. It is not made again on its own: check "
    "Gateway usage first, and give a new take a new shot name."
)

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()


def outcome_unknown(status: int, code: str) -> bool:
    """Whether a Gateway answer leaves it unknown if the job started: none came back, a proxy timed out, or it said
    202 with an id that could not be read (clients/gateway.py video_submit)."""
    return status in (0, 504) or code == "unreadable"


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


def send(store: Any, who: Any, job: Row, shot: str | None, submit: Callable[[str], Answer]) -> Answer:
    """Send a paid render once: `submit(key)` makes the call, with the shot key as its Idempotency-Key, and answers
    (status, answer, code). The same shot started already answers its job with no call; a call that raises is unknown."""
    key, started = begin(store, who, job, shot)
    if started:
        return 202, started, ""
    status, answer, code = 0, {}, "unreachable"  # a call that raises: unknown
    try:
        status, answer, code = submit(key)
    finally:
        end(store, who, key, status, answer, code)
    return status, answer, code


def begin(store: Any, who: Any, job: Row, shot: str | None = None) -> tuple[str, Row | None]:
    """Write the shot down before it is sent. Answers (key, None) to send it now (it is in flight until `end`), or
    (key, the started job) when the same shot already started. Refuses a shot that is in flight (429, retried) or whose
    outcome is unknown (409 `reconcile_shot`, asking the person once)."""
    digest = hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest()
    name = " ".join(str(shot or "").split()) or " ".join(str(job.get("prompt") or "").split()[:NAME_WORDS])
    key, sending = "", False
    try:
        with store.transaction(durable=True) as c:
            task_id, visit = _turn(c, who)
            parts = [who.actor, task_id or "", visit or "", digest, name]
            key = "shot_" + hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]
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
                H.event(c, who.actor, SENT, key, {"task": task_id, "visit": visit, "input": digest, "shot": name})
            elif last == SENT:  # sent, never answered and not in flight: the server stopped mid-call
                _unknown(c, who, key, detail)
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
            elif outcome_unknown(status, code):
                _unknown(c, who, key, detail)
            else:
                H.event(c, who.actor, REFUSED, key, {**detail, "status": status, "code": code})
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(key)
    if status != 202 and outcome_unknown(status, code):
        raise _reconcile(str(detail.get("shot") or ""))


def _reconcile(name: str) -> Problem:
    return Problem(
        "reconcile_shot",
        f"The Gateway never confirmed shot {name}, so it may have been charged. It is not sent again: a person is "
        "asked to reconcile it, and a new take needs a new shot name.",
        409,
    )


def _unknown(c: Any, who: Any, key: str, detail: Row) -> None:
    """Mark the shot unknown and, from a bot's turn on a card, ask the person the card falls to to reconcile it."""
    from . import person_asks, waiting_on

    task: Row | None = H.task(c, detail["task"]) if detail.get("task") else None
    person = waiting_on.person_for(c, task) if task and H.is_bot(who.actor) else None
    asked: str | None = None
    try:
        if task and person:
            asked = person_asks.ask_on(c, who.actor, task, person, f"Reconcile shot {detail.get('shot')}", WHY)
    except Exception:  # a refused ask still leaves the shot unknown, never sent again
        log.exception("A paid render's reconcile ask was refused")
    if not asked:
        log.warning("A paid render's outcome is unknown and no person on a card is asked about it")
    H.event(c, who.actor, UNKNOWN, key, {**detail, "asked": asked})
