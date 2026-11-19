"""Tico acts on the open app (backend/screen_actions.py): a `screen` event reaches only the person it is for, while the
tool waits; the app's report is what the tool hands back, from that person alone; with no app open the tool answers at
once; and an action the app does not have is refused with the list."""

import queue
import threading
from contextlib import contextmanager

import pytest

from backend import events as E
from backend import screen_actions as SA
from backend.assistant import write_allowed
from backend.auth import Identity
from backend.tests.helpers.api import headers
from clients import hubtools

ANA = Identity("human:ana", "human", "ana@acme.example")
CARA = Identity("human:cara", "human", "cara@acme.example")
FILTER = {"action": "tasks.filter", "input": {"pipeline": "engineering", "owner": "agent:qa"}}


@pytest.fixture
def hub(api):
    """Ana's app is open: one stream of hers follows `screen`, as an open /api/v2/events does."""
    screens = SA.screens(api.app.state.store)
    leave = screens.follow("human:ana")
    yield screens
    leave()


@contextmanager
def showing(api, monkeypatch, body, token="ana-test"):
    """The tool's call, made on a thread as a turn makes it, and the request it asked, once its event is logged and the
    call waits on the app: yields (request, answers), where answers holds the call's response once the block ends."""
    asked, real = queue.Queue(), SA.Screens.wait

    def wait(self, request, seconds):
        asked.put(request)
        return real(self, request, seconds)

    monkeypatch.setattr(SA.Screens, "wait", wait)
    answers = {}
    call = threading.Thread(
        target=lambda: answers.update(response=api.post("/api/v2/screen-actions", json=body, headers=headers(token)))
    )
    call.start()
    try:
        yield asked.get(timeout=30), answers  # a deadline: the call waits at once
    finally:
        call.join(timeout=30)
    assert not call.is_alive()


def screen_events(api, who, after):
    with api.app.state.store.read() as c:
        events, _, reset, _ = E.read(
            c, api.app.state.auth, who, after, topics=("screen",), screens=SA.screens(api.app.state.store)
        )
    assert not reset
    return [data for _, topic, data in events if topic == "screen"]


def latest(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def report(api, ident, body, token="ana-test"):
    return api.post(f"/api/v2/screen-actions/{ident}/result", json=body, headers=headers(token))


def test_the_event_reaches_only_its_person_and_her_report_is_what_the_tool_says(api, hub, monkeypatch):
    monkeypatch.setattr(SA, "WAIT_SECONDS", 30)  # a deadline: the report below ends the wait
    after = latest(api)
    with showing(api, monkeypatch, FILTER) as (request, answers):
        [event] = screen_events(api, ANA, after)
        assert {k: event[k] for k in ("id", "action", "input", "done", "actor")} == {
            "id": request.id,
            "action": "tasks.filter",
            "input": FILTER["input"],
            "done": False,
            "actor": "human:ana",
        }
        assert screen_events(api, CARA, after) == []
        # Another person cannot answer it, and is told no more than for an id that does not exist.
        refused = report(api, request.id, {"ok": True, "did": "Filtered"}, token="cara-test")
        assert refused.status_code == 404 and refused.json() == report(api, "nope", {"ok": True}).json()
        assert report(api, request.id, {"ok": True, "did": "Opened Engineering in Tasks"}).status_code == 200
    shown = answers["response"]
    assert shown.status_code == 200, shown.text
    assert shown.json() == {
        "id": request.id,
        "status": "shown",
        "did": "Opened Engineering in Tasks",
        "message": "Shown on their screen: Opened Engineering in Tasks",
    }
    # Answered once: a second report, and a stream that reads the log again, find nothing.
    assert report(api, request.id, {"ok": True}).status_code == 404
    assert screen_events(api, ANA, after) == []


def test_the_apps_error_is_what_the_tool_says(api, hub, monkeypatch):
    monkeypatch.setattr(SA, "WAIT_SECONDS", 30)
    with showing(api, monkeypatch, {"action": "tasks.open", "input": {"task": 412}, "done": True}) as (
        request,
        answers,
    ):
        assert request.event["done"] is True
        assert report(api, request.id, {"ok": False, "error": "not_allowed"}).status_code == 200
    assert answers["response"].json() == {
        "id": request.id,
        "status": "failed",
        "error": "not_allowed",
        "message": "The app could not do it (not_allowed): nothing moved.",
    }


def test_no_report_in_time_is_no_answer_and_the_request_is_gone(api, hub, monkeypatch):
    monkeypatch.setattr(SA, "WAIT_SECONDS", 0)
    after = latest(api)
    answered = api.post("/api/v2/screen-actions", json=FILTER, headers=headers())
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["status"] == "no_answer" and "do not say the screen moved" in body["message"]
    # Nobody waits on it now: a late report is a 404 and a reconnecting stream is not sent it.
    assert report(api, body["id"], {"ok": True}).status_code == 404
    assert screen_events(api, ANA, after) == []


def test_with_no_app_open_the_tool_answers_at_once(api, monkeypatch):
    monkeypatch.setattr(SA, "WAIT_SECONDS", 600)  # waiting at all would hang the test

    def never(self, *args):
        raise AssertionError("asked with no app open")

    monkeypatch.setattr(SA.Screens, "ask", never)
    after = latest(api)
    answered = api.post("/api/v2/screen-actions", json=FILTER, headers=headers())
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "not_open"
    assert latest(api) == after  # nothing logged


def test_an_action_the_app_does_not_have_or_input_it_does_not_take_is_refused(api, hub):
    unknown = api.post("/api/v2/screen-actions", json={"action": "tasks.delete", "input": {}}, headers=headers())
    assert unknown.status_code == 422
    detail = unknown.json()["error"]["detail"]
    assert "tasks.delete" in detail and "tasks.open (Open a task)" in detail
    wrong = api.post(
        "/api/v2/screen-actions", json={"action": "tasks.filter", "input": {"owner": "x"}}, headers=headers()
    )
    assert wrong.status_code == 422 and "input.pipeline: required" in wrong.json()["error"]["detail"]


def test_the_assistant_is_offered_the_tool_and_its_turn_may_call_it_without_a_card():
    assert set(hubtools.offered_to(hubtools.BY_NAME["tico_screen_show"])) == {"owner", "admin", "member", "assistant"}
    assert write_allowed("POST", "/api/v2/screen-actions")
    assert not write_allowed("POST", "/api/v2/screen-actions/x/result")
