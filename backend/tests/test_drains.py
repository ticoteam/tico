"""The slow drains beside the loop (backend/drains.py): a pass never holds the scheduler's tick, stops claiming once its
budget is spent, and never runs beside another pass of the same drain."""

import threading
import time

from backend import automations, drains
from backend.scheduler import Scheduler
from backend.tests.helpers.api import post
from backend.tests.helpers.automation_runs import make, queued, run_row

WAKE = {"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "assistant", "text": "Done"}}


def hub(api):
    return api.app.state.store.automations


def slow_steps(monkeypatch, seconds):
    """Every run step takes `seconds`, as a slow model or connector would; returns the runs advanced, in order."""
    real, went = automations.Hub.go, []

    def go(self, claimed):
        time.sleep(seconds)
        went.append(claimed.id)
        return real(self, claimed)

    monkeypatch.setattr(automations.Hub, "go", go)
    return went


def started(worker):
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    return thread


def test_a_slow_automation_queue_does_not_hold_the_schedulers_tick(api, monkeypatch):
    aid = make(api, [WAKE])
    rids = [queued(api, aid) for _ in range(4)]
    went = slow_steps(monkeypatch, 0.5)
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    scheduler.tick()  # the first pass's own setup (built-ins, mirrors) is not what is measured
    workers = drains.Drains(api.app.state.store, api.app.state.auth, budget=1.0).start()
    scheduler.drains = workers
    try:
        begun = time.monotonic()
        scheduler.tick()
        took = time.monotonic() - begun
        assert took < 1.5, f"the tick waited for the queue ({took:.1f}s)"  # inline it is 4 x 0.5 s and more
        deadline = time.monotonic() + 10
        while len(went) < len(rids) and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        workers.stop(10)
    assert went == rids, "the worker drained the whole queue, oldest first"
    assert all(run_row(api, rid)["status"] == "ok" for rid in rids)
    assert hub(api).worker is None, "a stopped worker hands the doorbell back to inline drains"


def test_a_pass_claims_nothing_once_its_budget_is_spent_and_the_next_carries_on(api, monkeypatch):
    aid = make(api, [WAKE])
    rids = [queued(api, aid) for _ in range(5)]
    went = slow_steps(monkeypatch, 0.5)
    budget = drains.Budget(0.75)
    assert hub(api).drain(budget=budget) == 2  # claimed at 0 s and 0.5 s; none at 1 s
    assert budget.spent
    assert [run_row(api, rid)["status"] for rid in rids] == ["ok", "ok", "queued", "queued", "queued"]
    rest = drains.Budget(5)
    assert hub(api).drain(budget=rest) == 3 and not rest.spent
    assert went == rids


def test_a_run_that_outlasts_its_budget_is_saved_once_not_cut_off(api, monkeypatch):
    aid = make(api, [WAKE])
    rid = queued(api, aid)
    slow_steps(monkeypatch, 0.4)
    assert hub(api).drain(budget=drains.Budget(0.1)) == 1
    row = run_row(api, rid)
    assert row["status"] == "ok" and row["claimed_at"] is None and not row["state"].get("resumes")


def test_a_wake_during_a_pass_asks_for_one_more_pass_never_a_second_beside_it():
    inside, release, live, seen = threading.Event(), threading.Event(), [0], []
    lock = threading.Lock()

    def work(budget):
        with lock:
            live[0] += 1
            seen.append(live[0])
        inside.set()
        release.wait(5)
        with lock:
            live[0] -= 1
        return 1

    worker = drains.Worker("automations", work)
    thread = started(worker)
    try:
        first = worker.wake()
        assert inside.wait(5)
        inside.clear()
        # The doorbell, a tick and a manual run, all while the first pass runs.
        asks = [threading.Thread(target=worker.wake) for _ in range(3)]
        for ask in asks:
            ask.start()
        for ask in asks:
            ask.join()
        last = worker.wake()
        assert not worker.wait(last, 0.2), "a wake during a pass is not answered by that pass"
        release.set()
        assert worker.wait(first, 5) and worker.wait(last, 5)
        time.sleep(0.3)
    finally:
        worker.stop.set()
        thread.join(5)
    assert worker.passes == 2, "one more pass after the first, for all the wakes during it"
    assert max(seen) == 1, "never two passes at once"


def test_a_failing_pass_reaches_the_schedulers_health_row_and_the_next_pass_runs(api):
    calls = []

    def work(budget):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("provider down")
        return 0

    errors = []
    worker = drains.Worker("email", work, store=api.app.state.store, on_error=lambda name, exc: errors.append(name))
    worker.pass_once()
    assert worker.take_failures() == [{"email": "RuntimeError"}] and worker.take_failures() == []
    assert errors == ["email"] and drains.TELEMETRY["email"] == "email_drain"
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='drain:email'").fetchone()
    assert row["last_error"] == "RuntimeError"
    worker.pass_once()
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM service_health WHERE service='drain:email'").fetchone()
    assert row["last_error"] is None and row["last_succeeded_at"]


def test_a_stopping_server_claims_nothing_new():
    stopping = threading.Event()
    budget = drains.Budget(None, stopping)
    assert budget.left()
    stopping.set()
    assert not budget.left() and budget.spent
    assert drains.left(None)


def test_a_manual_run_answers_after_the_workers_pass_that_takes_it(api):
    aid = make(api, [WAKE])
    worker = drains.Worker("automations", lambda budget: hub(api).drain(budget=budget))
    thread = started(worker)
    hub(api).worker = worker
    try:
        run = post(api, f"automations/{aid}/run", {})["run"]
    finally:
        hub(api).worker = None
        worker.stop.set()
        thread.join(5)
    assert run["status"] == "ok" and worker.passes >= 1


def test_runs_and_gmail_actions_share_one_budget_and_take_turns_going_first(api, monkeypatch):
    aid = make(api, [WAKE])
    for _ in range(4):
        queued(api, aid)
    slow_steps(monkeypatch, 0.5)
    seen = []

    def mail(store, limit=200, budget=None):
        seen.append(("mail", budget is not None and budget.left()))
        return 0

    monkeypatch.setattr(automations, "drain_workspace_mail", mail)
    first = drains.Budget(0.75)
    hub(api).drain(budget=first)  # Gmail actions first: the whole budget is still there
    second = drains.Budget(0.75)
    assert hub(api).drain(budget=second) == 2 and second.spent  # runs first, then Gmail gets no fresh budget
    assert seen == [("mail", True), ("mail", False)]


def test_a_pass_that_raises_partway_still_wakes_the_workers(api, monkeypatch):
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    scheduler.tick()
    woken = []

    class Workers:
        def take_failures(self):
            return []

        def wake(self):
            woken.append(1)

    scheduler.drains = Workers()

    def boom(*a, **k):
        raise RuntimeError("a step before the wake")

    monkeypatch.setattr(scheduler, "_pass", boom)
    try:
        scheduler.tick()
    except RuntimeError:
        pass
    assert woken == [1], "email and the queue still get their pass when the tick fails early"


def test_a_shutdown_waits_one_deadline_for_all_workers_not_one_each(api):
    workers = drains.Drains(api.app.state.store, api.app.state.auth)
    release = threading.Event()
    workers.threads = [threading.Thread(target=release.wait, args=(5,), daemon=True) for _ in range(3)]
    for thread in workers.threads:
        thread.start()
    try:
        begun = time.monotonic()
        workers.stop(0.6)
        took = time.monotonic() - begun
    finally:
        release.set()
    assert took < 1.2, f"three stuck workers waited {took:.1f}s, one deadline is 0.6 s"
