"""Health's "Failed runs" counts jobs that failed, not every try: a run its computer never started and that went back in
the queue (a lapsed lease before the start, or a hand-back) is a retry, not a failure (Nov 12: Health said 352 failed
runs, about 310 of them retries of one job). Its link opens the Runs list of those failures, and each of the newest
five says which bot, when and why: "couldn't start on <computer> after N tries", or the error its run stored."""

from backend import execution
from backend.store import H
from backend.tests.helpers.api import assign, claim, expire, get, post, ready, runner  # noqa: F401 (fixture)
from backend.tests.helpers.task_lanes import task_bot


def lapse(api, machine, attempt):
    """The run's lease runs out before its computer started it; the sweep settles it, and the computer reports in."""
    expire(api, attempt["id"])
    ex = api.app.state.execution
    ex.served_at = None  # no server-stall grace: the computer alone lost the run
    with api.app.state.store.transaction() as c:
        ex.expire(c)
        c.execute(  # a lapse after the first waits before the next try; make that wait over
            "UPDATE attempts SET finished_at=? WHERE id=?",
            (H.shift(H.now(), seconds=-execution.HAND_BACK_MAX - 1), attempt["id"]),
        )
    ready(api, machine, ["ops"])


def hand_back(api, machine, attempt):
    """The computer gives the run back before starting it, with no reason (a hiccup), and its wait is made over."""
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "interrupted", "last_seq": 0, "text": ""},
        machine["token"],
    )
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempts SET finished_at=? WHERE id=?",
            (H.shift(H.now(), seconds=-execution.HAND_BACK_MAX - 1), attempt["id"]),
        )
    ready(api, machine, ["ops"])


def runs(api, query=""):
    r = api.get("/api/runs" + query, headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 200, r.text
    return r.json()


def test_one_job_that_failed_after_many_retries_is_one_failed_run_and_its_link_lists_it(api, monkeypatch):
    monkeypatch.setattr(execution, "PRE_START_TRIES", 4)
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    attempt = claim(api, machine)
    job = attempt["job_id"]
    hand_back(api, machine, attempt)  # a hand-back: back in the queue
    tries = []
    for _ in range(execution.PRE_START_TRIES - 1):  # the hand-back is one of its pre-start tries
        attempt = claim(api, machine)
        assert attempt and attempt["job_id"] == job
        tries.append(attempt["id"])
        lapse(api, machine, attempt)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "failed"
        states = [r[0] for r in c.execute("SELECT state FROM attempts WHERE job_id=?", (job,))]
    # A hand-back and two lapses that went back in the queue, then the lapse that failed the job.
    assert len(states) == execution.PRE_START_TRIES and states.count("failed") == 1

    body = get(api, "health")
    check = next(row for row in body["checks"] if row["id"] == "failed")
    assert check["status"] == "warn"
    assert check["summary"] == "1 run failed in the last day.", check["summary"]
    # Which bot, when, and why: the computer it could not start on and how many tries that took.
    [failure] = body["failures"]
    assert failure["bot"] == "ops" and failure["state"] == "failed" and failure["at"]
    assert failure["why"] == f"couldn't start on Test Mac after {execution.PRE_START_TRIES} tries", failure["why"]
    # The link opens Runs showing only the failures, so the one that failed is there however many runs came after.
    [fix] = check["fixes"]
    assert fix["href"] == "#/settings/history?view=runs&failed=1" and not fix["tab"]
    listed = runs(api, "?failed=true")
    assert [row["id"] for row in listed] == [tries[-1]]
    assert listed[0]["exit"] == 1
    # The full list still shows every try.
    assert {row["id"] for row in runs(api)} >= set(tries)


def test_a_run_that_started_and_was_lost_still_counts_and_is_listed(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "lost-run"}, machine["token"])
    lapse(api, machine, attempt)
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state,started_at FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    assert row["state"] == "expired" and row["started_at"]
    body = get(api, "health")
    check = next(row for row in body["checks"] if row["id"] == "failed")
    assert check["summary"] == "1 run failed in the last day."
    assert [row["id"] for row in runs(api, "?failed=true")] == [attempt["id"]]


def test_no_failures_says_so_and_lists_nothing(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    attempt = claim(api, machine)
    hand_back(api, machine, attempt)
    lapse(api, machine, claim(api, machine))
    check = next(row for row in get(api, "health")["checks"] if row["id"] == "failed")
    assert check["status"] == "ok" and check["summary"] == "No failed runs in the last day." and check["fixes"] == []
    assert runs(api, "?failed=true") == []


def test_a_run_that_started_and_failed_says_the_error_it_stored_and_a_bot_hears_only_which_and_when(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "timed-out"}, machine["token"])
    cause = {"seq": 1, "kind": "diagnostic", "payload": {"end_reason": "Timeout: max_run_minutes (60 minutes)"}}
    post(api, f"attempts/{attempt['id']}/events", {"events": [cause]}, machine["token"])
    # The run's own text is its answer, not its error: it is never the reason.
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "failed", "text": "Half a summary", "last_seq": 1},
        machine["token"],
    )
    [failure] = get(api, "health")["failures"]
    assert failure["bot"] == "ops" and failure["why"] == "Timeout: max_run_minutes (60 minutes)", failure
    # A bot reads Health too; why a run failed stays with the administrators, who may read every bot's runs.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE run_backoff SET wait_until=NULL")  # the failure's wait is over
    post(api, "tasks", {"title": "Look at Health", "body": "Read Health.", "owner": "ops"})
    turn = claim(api, machine, "ops")
    assert turn
    assert [sorted(row) for row in get(api, "health", turn["token"])["failures"]] == [["at", "bot", "state"]]


def test_a_started_run_its_computer_lost_says_so(api):
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    post(api, "chat/ops", {"text": "Please summarize the current work."})
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "lost-run"}, machine["token"])
    lapse(api, machine, attempt)
    [failure] = get(api, "health")["failures"]
    assert (
        failure["state"] == "expired" and failure["why"] == "Computer lost or heartbeat stopped (attempt lease expired)"
    ), failure


def test_a_job_refused_for_its_task_says_so_not_that_its_computer_could_not_start(api):
    machine = task_bot(api, lanes=True)
    # A queued run for `ops` whose wake names no task (as before #240): the claim refuses it, before any start.
    message = post(api, "chat/ops", {"text": "Spec has room."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET wake_key='room:product-spec:start' WHERE id=?", (message["id"],))
    claim(api, machine)
    [failure] = get(api, "health")["failures"]
    assert failure["why"] == execution.REFUSED_RUN, failure
