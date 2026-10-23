"""Stop and finished-task notices cannot resurrect work that never started."""
import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, expire, post, ready, runner  # noqa: F401


def task_attempt(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    return machine, task, claim(api, machine)


@pytest.mark.parametrize("outcome", ["failed", "interrupted"])
def test_stop_settles_a_login_wait_without_starting_or_retrying(api, outcome):
    machine, task, attempt = task_attempt(api)
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "status", "payload": {
        "waiting": "Waiting for this computer's ChatGPT login to be free", "login": "ChatGPT", "position": 1}}]},
        machine["token"])
    post(api, "bots/ops/stop", {"attempt_id": attempt["id"]})
    assert post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])["stop"] is True
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": outcome, "last_seq": 1}, machine["token"])
    with api.app.state.store.read() as c:
        row = c.execute("SELECT state,started,finished FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
        assert row["state"] == outcome and row["started"] is None and row["finished"]
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "cancelled"
        assert c.execute("SELECT exit FROM turns WHERE id=?", (attempt["id"],)).fetchone()[0] == outcome
        assert H.status(c, "ops")["state"] == "idle"
    # Even after the old lease would have elapsed, no fresh reservation is possible.
    expire(api, attempt["id"])
    assert claim(api, machine) is None


def test_unstopped_failed_completion_still_requires_start(api):
    machine, task, attempt = task_attempt(api)
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 0},
         machine["token"], expected=409)


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("cause", ["stop", "done", "closed", "declined", "open"])
def test_expiry_cancels_stopped_or_finished_work_and_preserves_normal_retry(api, cause, started, monkeypatch):
    from backend import execution
    # Cancellation must win even over the pre-start failure threshold.
    if cause != "open":
        monkeypatch.setattr(execution, "PRE_START_TRIES", 1)
    machine, task, attempt = task_attempt(api)
    if started:
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, machine["token"])
    if cause == "stop":
        post(api, "bots/ops/stop", {"attempt_id": attempt["id"]})
    elif cause != "open":
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET status=? WHERE id=?", (cause, task["id"]))
    expire(api, attempt["id"])
    # Inspect expiry before claim can hide a mistaken requeue by suppressing the notice.
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
        expected = ("uncertain" if started else "queued") if cause == "open" else "cancelled"
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == expected
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] == "expired"
    again = claim(api, machine)
    if cause == "open" and not started:
        assert again["job_id"] == attempt["job_id"] and again["id"] != attempt["id"]
    else:
        assert again is None


NOTICE_BODIES = ["New task from human:ana: Count the words", "Subtask result returned: Review complete",
                 "PR #7 checks passed", "A future keeper notice"]


@pytest.mark.parametrize("status", ["done", "closed", "declined", "open"])
@pytest.mark.parametrize("body", NOTICE_BODIES)
def test_all_keeper_notices_are_obsolete_only_on_finished_tasks(api, status, body):
    machine, task, original = task_attempt(api)
    with api.app.state.store.transaction() as c:
        # Retire the initial reservation so only the particular notice is eligible.
        c.execute("UPDATE attempts SET state='completed',finished=? WHERE id=?", (H.now(), original["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (original["job_id"],))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, task["id"]))
        notice = H.say(c, H.KEEPER, "bot:ops", body, kind="notice", conversation_id=task["conversation_id"],
                       refs={"task": task["id"], "trigger": "other"})
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (notice["id"],)).fetchone()[0] == "queued"
    attempt = claim(api, machine)
    assert (attempt is not None) == (status == "open")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (notice["id"],)).fetchone()[0] == (
            "leased" if status == "open" else "cancelled")
        if status != "open":
            assert H.message(c, notice["id"])["delivered_at"]


@pytest.mark.parametrize("status", ["done", "closed", "declined"])
@pytest.mark.parametrize("kind", ["say", "notice"])
def test_a_persons_own_message_on_a_finished_task_is_never_suppressed(api, status, kind):
    machine, task, original = task_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished=? WHERE id=?", (H.now(), original["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (original["job_id"],))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, task["id"]))
        message = H.say(c, "human:ana", "bot:ops", "Subtask result returned: please explain it", kind=kind,
                        conversation_id=task["conversation_id"], refs={"task": task["id"]})
    attempt = claim(api, machine)
    assert attempt and attempt["message"]["id"] == message["id"]


def test_obsolete_notice_is_cancelled_even_when_idle_claim_finds_no_candidate(api, monkeypatch):
    machine, task, original = task_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished=? WHERE id=?", (H.now(), original["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (original["job_id"],))
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
        notice = H.say(c, H.KEEPER, "bot:ops", "PR #7 checks passed", kind="notice",
                       conversation_id=task["conversation_id"], refs={"task": task["id"], "trigger": "other"})
    monkeypatch.setattr(api.app.state.execution, "candidate", lambda *a, **kw: None)
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (notice["id"],)).fetchone()[0] == "cancelled"


@pytest.mark.parametrize("cause", ["stop", "done", "declined"])
def test_expiry_judges_folded_inputs_by_their_own_task(api, cause):
    machine, task, attempt = task_attempt(api)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "folded"}, machine["token"])
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, "human:ana", "Read the weekly numbers", "Read them.", "bot:ops", lint=False)
        # Another task's human message in the same room is eligible for steering on a shared lane.
        follow = H.say(c, "human:ana", "bot:ops", "Also read the weekly numbers",
                       conversation_id=task["conversation_id"], refs={"task": other["id"]})
    assert follow["id"] in [m["id"] for m in post(api, f"attempts/{attempt['id']}/inputs", {}, machine["token"])["messages"]]
    if cause == "stop":
        post(api, "bots/ops/stop", {"attempt_id": attempt["id"]})
    else:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET status=? WHERE id=?", (cause, task["id"]))
    expire(api, attempt["id"])
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "cancelled"
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (follow["id"],)).fetchone()[0] == "uncertain"


def test_finished_task_notices_to_a_requester_are_also_obsolete(api):
    machine, task, original = task_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished=? WHERE id=?", (H.now(), original["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (original["job_id"],))
        c.execute("UPDATE tasks SET owner='bot:finance',requester='bot:ops',status='done' WHERE id=?", (task["id"],))
        notice = H.say(c, H.KEEPER, "bot:ops", "Subtask result returned: Finished", kind="notice",
                       conversation_id=task["conversation_id"], refs={"task": task["id"], "trigger": "other"})
    assert claim(api, machine) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (notice["id"],)).fetchone()[0] == "cancelled"


def test_an_explicitly_resumed_job_is_not_an_obsolete_automatic_notice(api):
    machine, task, attempt = task_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='expired',finished=? WHERE id=?", (H.now(), attempt["id"]))
        c.execute("UPDATE jobs SET state='queued' WHERE id=?", (attempt["job_id"],))
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
        c.execute("INSERT INTO job_recovery VALUES(?,?,?,?,?,?)", (attempt["job_id"], attempt["id"], "resume",
                  "Explicitly resume the request", "human:ana", H.now()))
    again = claim(api, machine)
    assert again and again["job_id"] == attempt["job_id"]
