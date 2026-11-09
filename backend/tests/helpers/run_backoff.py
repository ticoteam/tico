"""Helpers that other tests share, moved out of test_run_backoff.py so they import no test file."""

from backend import run_backoff
from backend.store import H
from backend.tests.helpers.api import assign, claim, post, ready, runner


TOO_BIG = "API Error: 400 too many total text bytes: 32000440 > 32000000"


def at(monkeypatch, api, when):
    """The clock reads `when`, and the test computer has been reporting in all along."""
    monkeypatch.setattr(H, "now", lambda: when)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=?,awake_since=NULL", (when,))


def fail(api, machine, attempt, error, summary="", **extra):
    """An older runner's failed run: the error as an event with no limit flag, `limited` not set."""
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, machine["token"])
    events = [{"seq": 1, "kind": "error", "payload": {"error": error}}]
    post(api, f"attempts/{attempt['id']}/events", {"events": events}, machine["token"])
    return post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "failed", "last_seq": 1, "text": summary, **extra},
        machine["token"],
    )


def complete(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "ok-" + attempt["id"]}, machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done."},
        machine["token"],
    )


def setup(api, monkeypatch, tasks=8, when="2026-10-24T12:00:00.000000Z"):
    at(monkeypatch, api, when)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    for n in range(tasks):
        post(api, "tasks", {"owner": "ops", "title": f"Review change {n}", "body": "Please review it."})
    return machine


def hold_bot(api, machine, monkeypatch):
    """Five fast failures in a row, each after the wait the one before it set. Answers the time of the last one."""
    now = "2026-10-24T12:00:00.000000Z"
    for fails in range(1, 6):
        at(monkeypatch, api, now)
        ready(api, machine, ["ops"])
        fail(api, machine, claim(api, machine), TOO_BIG)
        now = H.shift(now, seconds=run_backoff.wait_for(fails) + 1)
    with api.app.state.store.read() as c:
        assert run_backoff.get(c, "ops")["held_at"]
        return run_backoff.get(c, "ops")["held_at"]
