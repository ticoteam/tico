"""Tico acts on the person's open app: the `screen` topic of the live stream (backend/events.py).

A turn of the person's Assistant moves their screen with one tool, `tico_screen_show` (POST /api/v2/screen-actions): an
action id from contracts/screen-actions.json and its input. The server checks both against that file, read at each call
because the web app writes it from its own action registry, and makes no other check: an action only moves the screen,
and what the screen then shows is read with the person's own rights. The request goes to that person's own open streams
as one `screen` event, never to anyone else's; the app does it and reports back (POST
/api/v2/screen-actions/{id}/result), and the tool waits up to WAIT_SECONDS for that report, so Tico says the screen moved
only when it did. With no stream of theirs open, the tool answers at once.

A request lives in memory for as long as the tool waits, and no longer: one server process serves both the streams and
the tools (backend/server.py), a stream that reconnects later is never sent a request nobody is waiting on, and a late
answer is a plain 404. The change log row that wakes the streams carries only the request and action ids; the input
never reaches the database.
"""

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import Request
from pydantic import Field

from . import models as M
from .api_contract import api_v2, error
from .store import Problem
from .views import human_only

ACTIONS = Path(__file__).resolve().parents[1] / "contracts" / "screen-actions.json"
WAIT_SECONDS = 3.0  # how long the tool waits for the app's report; tests shorten it
ROW = (
    "INSERT INTO changes(topic,kind,subject_id,ref,actor,created_at) "
    "VALUES('screen',?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
)


class Show(M.Contract):
    action: str = Field(min_length=1, max_length=120)
    input: dict[str, Any] = Field(default_factory=dict)
    done: bool = False  # Tico already made the change with its own tool: the app only shows it


class Report(M.Contract):
    ok: bool
    did: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=500)


class _Asked:
    __slots__ = ("id", "person", "event", "answered", "result")

    def __init__(self, person, event):
        self.id, self.person, self.event = event["id"], person, event
        self.answered, self.result = threading.Event(), None


class Screens:
    """Who has a stream open that follows `screen`, and the requests waiting on an answer. Thread-safe: streams are
    read on the event loop's worker threads and tools wait on the route's."""

    def __init__(self):
        self._lock = threading.Lock()
        self._streams = {}
        self._asked = {}

    def follow(self, person):
        """A stream of `person`'s opened; call the result once when it ends."""
        with self._lock:
            self._streams[person] = self._streams.get(person, 0) + 1

        def leave():
            with self._lock:
                left = self._streams.get(person, 0) - 1
                if left > 0:
                    self._streams[person] = left
                else:
                    self._streams.pop(person, None)

        return leave

    def open(self, person):
        with self._lock:
            return self._streams.get(person, 0) > 0

    def ask(self, person, action, input_, done):
        asked = _Asked(person, {"id": str(uuid.uuid4()), "action": action, "input": input_, "done": done})
        with self._lock:
            self._asked[asked.id] = asked
        return asked

    def event(self, ident, person):
        """What a stream of `person`'s sends for request `ident`: None once it is answered or no longer waited on, and
        always for anyone else."""
        with self._lock:
            asked = self._asked.get(ident)
            return dict(asked.event) if asked is not None and asked.person == person else None

    def answer(self, ident, person, result):
        """The app's report on request `ident`; False when there is no such request waiting for `person`."""
        with self._lock:
            asked = self._asked.get(ident)
            if asked is None or asked.person != person:
                return False
            del self._asked[ident]
            asked.result = result
        asked.answered.set()
        return True

    def wait(self, asked, seconds):
        """The app's report, or None after `seconds`; either way the request is no longer waited on."""
        asked.answered.wait(seconds)
        return self.forget(asked)

    def forget(self, asked):
        with self._lock:
            self._asked.pop(asked.id, None)
            return asked.result


def screens(store):
    """The store's screens (one per Store, so per database), as events.bell is."""
    existing = getattr(store, "screen_actions", None)
    if existing is None:
        existing = store.screen_actions = Screens()
    return existing


def actions():
    """The actions the app can do, by id, read now: the web app rewrites the file."""
    try:
        listed = json.loads(ACTIONS.read_text())["actions"]
        return {entry["id"]: entry for entry in listed}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Problem("unavailable", "The list of screen actions cannot be read", 503) from exc


def check(action, input_):
    """Refuse an action the app does not have, or input its schema does not take, saying what would do."""
    from clients.hubtools import argument_error

    known = actions()
    entry = known.get(action)
    if entry is None:
        raise Problem(
            "validation",
            f"No screen action {action}. Use one of: "
            + "; ".join(f"{ident} ({known[ident].get('title', '')})" for ident in sorted(known)),
            422,
        )
    if wrong := argument_error(input_, entry.get("input") or {}, "input"):
        raise Problem("validation", f"{action}: {wrong}", 422)


def outcome(asked, result, seconds):
    """What the tool hands back to the turn, so it says only what really happened on the screen."""
    if result is None:
        return {
            "id": asked.id,
            "status": "no_answer",
            "message": f"Sent, but the app did not answer within {seconds:g} seconds: do not say the screen moved.",
        }
    if result.get("ok"):
        did = result.get("did")
        return {
            "id": asked.id,
            "status": "shown",
            "message": "Shown on their screen" + (f": {did}" if did else "."),
            **({"did": did} if did else {}),
        }
    reason = result.get("error") or "unknown"
    return {
        "id": asked.id,
        "status": "failed",
        "error": reason,
        "message": f"The app could not do it ({reason}): nothing moved.",
    }


NOT_OPEN = {"id": None, "status": "not_open", "message": "The app is not open, so nothing moved on their screen."}


def install(app, store):
    hub = screens(store)

    @app.post(
        "/api/v2/screen-actions",
        **api_v2(
            "Assistant",
            "showOnScreen",
            "Do one action from contracts/screen-actions.json on the signed-in person's open app and wait up to "
            "3 seconds for its report. `status` is shown, failed (with `error`), not_open (no app of theirs is "
            "open, answered at once) or no_answer",
            idempotency="Optional: each call is its own request and the header is not checked.",
        ),
    )
    def show(request: Request, body: Show):
        who = request.state.identity
        human_only(who)
        check(body.action, body.input)
        if not hub.open(who.actor):
            return dict(NOT_OPEN)
        asked = hub.ask(who.actor, body.action, body.input, body.done)
        # The person's Assistant moves the screen, so its event says so; the person's own call says them.
        actor = who.task_actor if who.via == "assistant" and who.task_actor else who.actor
        try:
            store.write(who, lambda c: c.execute(ROW, (body.action, who.actor, asked.id, actor)))
        except BaseException:
            hub.forget(asked)
            raise
        seconds = WAIT_SECONDS
        return outcome(asked, hub.wait(asked, seconds), seconds)

    @app.post(
        "/api/v2/screen-actions/{ident}/result",
        **api_v2(
            "Live events",
            "reportScreenAction",
            "The open app's report on a `screen` event it was sent: ok, and what it did (`did`) or why not (`error`). "
            "Only the person it was sent to answers it, once, while Tico still waits",
            responses={"404": error("not_found: no such request waiting on this person")},
            idempotency="Optional: a request is answered once and the header is not checked.",
        ),
    )
    def report(request: Request, ident: str, body: Report):
        who = request.state.identity
        human_only(who)
        if not hub.answer(ident, who.actor, body.model_dump(exclude_none=True)):
            raise Problem("not_found", "No such screen request", 404)
        return {"ok": True}
