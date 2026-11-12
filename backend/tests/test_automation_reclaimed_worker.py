"""An automation worker that outlived its claim (stalled past STALE_MINUTES, so the tick took its run back and another
worker claimed it) cannot save or settle the run the newer claim owns (state-machines audit SM-04 A).
backend/automations.py: Hub.claim stamps a fresh token, the tick clears it when it takes a run back, and
HubServices.checkpoint, on_hold and Hub.go require it. The webhook half is test_webhook_reclaimed_worker.py."""

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend import automations as A
from backend.tests.helpers.automation_runs import make as make_automation, queued, run_row, sql

class Stop(BaseException):
    """The server stopping mid-run: nothing in the hub catches it."""


NOTE = [{"id": "note", "type": "action", "do": "wake_bot", "args": {"bot": "assistant", "text": "Acme check"}}]


def later(minutes):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


# ------------------------------------------------------------------------------------------------------ automations
def stalled_first_worker(api, first, second, hold=None):
    """Worker A claims a run and stalls inside its step; the tick takes the run back after STALE_MINUTES and worker
    B runs it to `second`; then A comes back with `first`. `hold(services)` runs in A once B is done, before it
    returns. Returns (the run after B, the run after A, A's go() result)."""
    rid = queued(api, make_automation(api, NOTE))
    store = api.app.state.store
    hub = store.automations
    claimed = hub.claim()
    assert claimed.id == rid
    entered, release, calls = threading.Event(), threading.Event(), []
    result = {}

    def advance(flow, state, services):
        calls.append(services.token)
        if len(calls) == 1:
            entered.set()
            assert release.wait(15)
            if hold:
                hold(services)
            return {**state, **first, "threads": [], "steps": []}
        return {**state, **second, "threads": [], "steps": []}

    with patch.object(A.E, "advance", advance):
        worker = threading.Thread(target=lambda: result.update(won=hub.go(claimed)))
        worker.start()
        assert entered.wait(10)
        A.tick(store, later(A.STALE_MINUTES + 1))  # takes the run back and drains it: worker B
        after_b = run_row(api, rid)
        release.set()
        worker.join(15)
        assert not worker.is_alive()
    assert len(calls) == 2 and calls[0] != calls[1]  # two claims, two tokens
    return after_b, run_row(api, rid), result["won"]


@pytest.mark.parametrize(
    "first, second",
    [
        ({"status": "ok", "cost": 1.0}, {"status": "failed", "error": "Acme API down", "cost": 2.0}),
        ({"status": "failed", "error": "stalled", "cost": 1.0}, {"status": "ok", "cost": 2.0}),
    ],
    ids=["old-ok-after-new-failed", "old-failed-after-new-ok"],
)
def test_an_old_automation_worker_after_reclaim_cannot_overwrite_the_new_result_or_cost(api, first, second):
    after_b, after_a, won = stalled_first_worker(api, first, second)
    assert after_b["status"] == second["status"] and after_b["cost"] == 2.0
    assert after_b["state"]["resumes"] == 1
    assert won is False
    # A's answer changed nothing: status, cost, the resume count and the finish time are B's.
    for key in ("status", "cost", "finished_at", "saved_state", "claim_token"):
        assert after_a[key] == after_b[key], key
    assert after_a["claim_token"] is None  # settled runs keep no claim


def test_an_old_automation_worker_cannot_save_after_reclaim_and_is_told_to_stop(api):
    seen = {}

    def hold(services):
        services.dirty = True
        services.checkpoint({"status": "running", "threads": [], "steps": [], "cost": 9.0})
        seen["on_hold"] = services.on_hold()

    after_b, after_a, won = stalled_first_worker(
        api, {"status": "ok", "cost": 1.0}, {"status": "failed", "error": "x", "cost": 2.0}, hold
    )
    assert seen["on_hold"] is True  # the executor starts no further step
    assert after_a["cost"] == 2.0 and after_a["saved_state"] == after_b["saved_state"]
    assert won is False


def test_a_run_taken_back_and_claimed_again_is_settled_only_by_the_new_claim(api):
    """The ABA case: running (A) -> queued -> running (B). A status-only check sees `running` both times."""
    rid = queued(api, make_automation(api, NOTE))
    store = api.app.state.store
    hub = store.automations
    old = hub.claim()
    A.tick(store, later(A.STALE_MINUTES + 1), drain=False)
    assert run_row(api, rid)["status"] == "queued" and run_row(api, rid)["claim_token"] is None
    new = hub.claim()
    assert new.id == rid and new.token != old.token and run_row(api, rid)["status"] == "running"

    def outcome(status, cost):
        return lambda flow, state, services: {**state, "status": status, "cost": cost, "threads": [], "steps": []}

    with patch.object(A.E, "advance", outcome("failed", 1.0)):
        assert hub.go(old) is False  # A fails late, while B holds the run
    assert run_row(api, rid)["status"] == "running" and run_row(api, rid)["claim_token"] == new.token
    with patch.object(A.E, "advance", outcome("ok", 2.0)):
        assert hub.go(new) is True
    done = run_row(api, rid)
    assert done["status"] == "ok" and done["cost"] == 2.0 and done["claim_token"] is None


def test_a_resumed_run_still_does_each_effect_once(api):
    """The fence keeps `_once`: a run cut off after its effect is taken back, claimed again and goes on from its save
    without repeating the effect."""
    aid = make_automation(
        api, [{"id": "spawn", "type": "action", "do": "create_task", "args": {"title": "Acme fence check"}}, *NOTE]
    )
    rid = queued(api, aid)
    store = api.app.state.store
    hub = store.automations
    old = hub.claim()
    real = A.HubServices.checkpoint

    def cut_off(self, state):
        real(self, state)
        raise Stop()

    with patch.object(A.HubServices, "checkpoint", cut_off), pytest.raises(Stop):
        hub.go(old)
    assert run_row(api, rid)["claim_token"] == old.token
    A.tick(store, later(A.STALE_MINUTES + 1))
    done = run_row(api, rid)
    assert done["status"] == "ok" and done["state"]["resumes"] == 1
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Acme fence check'")) == 1




def test_an_old_automation_worker_cannot_save_while_the_new_claim_holds_the_run(api):
    """A checkpoint (and the stop check before each step) from A while B's claim holds the run, still `running`: a
    status-only check lets both through."""
    rid = queued(api, make_automation(api, NOTE))
    store = api.app.state.store
    hub = store.automations
    old = hub.claim()
    entered, release, seen, result = threading.Event(), threading.Event(), {}, {}

    def advance(flow, state, services):
        entered.set()
        assert release.wait(15)
        services.dirty = True
        services.checkpoint({**state, "status": "running", "threads": [], "steps": [], "cost": 9.0})
        seen["on_hold"] = services.on_hold()
        return {**state, "status": "ok", "cost": 9.0, "threads": [], "steps": []}

    with patch.object(A.E, "advance", advance):
        worker = threading.Thread(target=lambda: result.update(won=hub.go(old)))
        worker.start()
        assert entered.wait(10)
        A.tick(store, later(A.STALE_MINUTES + 1), drain=False)
        new = hub.claim()  # B holds the run, not started yet
        before = run_row(api, rid)
        release.set()
        worker.join(15)
        assert not worker.is_alive()
    after = run_row(api, rid)
    assert seen["on_hold"] is True and result["won"] is False
    assert (after["status"], after["cost"], after["saved_state"], after["claim_token"]) == (
        "running",
        before["cost"],
        before["saved_state"],
        new.token,
    )
