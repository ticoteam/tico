"""A queued job whose message its bot may no longer read (a private task it was taken off) is one no claim ever takes:
job_refusal says "unreadable" every time. The scheduler cancels it once a minute with a job.suppress event, so it
neither sits queued forever nor ages Health's queue_idle; work the claim holds for any other reason stays queued."""

import pytest

from backend import check_models, execution, health
from backend.scheduler import Scheduler
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot


@pytest.fixture(autouse=True)
def forget_claims():
    execution.CLAIMED_BUSY.clear()
    yield
    execution.CLAIMED_BUSY.clear()


def job_of(c, task_id, bot="ops"):
    return c.execute("SELECT j.* FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv "
                     f"ON cv.id=m.conversation_id WHERE j.bot_id=? AND {H.MESSAGE_TASK_SQL}=?", (bot, task_id)).fetchone()


def secret_taken_away(api):
    """A private task handed to ops, then taken off it with its notice still queued: ops may no longer read it."""
    secret = post(api, "tasks", {"owner": "ops", "title": "Salary bands", "body": "Please review.", "private": True})
    with api.app.state.store.transaction() as c:
        assert job_of(c, secret["id"])["state"] == "queued"
        # Any way a bot loses read access to a queued message; the claim and the sweep judge only the verdict.
        c.execute("UPDATE tasks SET owner_id='bot:finance' WHERE id=?", (secret["id"],))
    return secret


def refusal(api, machine, job):
    """job_refusal's verdict on `job` as the claim would ask it, with nothing busy."""
    store = api.app.state.store
    with store.read() as c:
        runner = c.execute("SELECT * FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()
        ready = execution.readiness_document(runner["readiness"])
        return execution.job_refusal(c, job, {}, ready, set(), execution.lanes_on(runner), execution.roots_on(runner))


def sweep(api):
    """The scheduler's once-a-minute pass, from a tick of a fresh scheduler (its first tick runs every timed pass)."""
    Scheduler(api.app.state.store, api.app.state.execution).tick()


def suppressed(c, job_id):
    return [H._json(r[0], {}) for r in c.execute(
        "SELECT detail FROM events WHERE action='job.suppress' AND target=?", (job_id,))]


def test_an_unreadable_job_is_cancelled_and_a_readable_one_is_still_claimed(api):
    machine = task_bot(api, lanes=True, others=("finance",))
    secret, finished = secret_taken_away(api), secret_taken_away(api)
    first = new_task(api, "first")
    store = api.app.state.store
    with store.transaction() as c:
        # Unreadable too, but no longer queued: the sweep leaves it as it is.
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job_of(c, finished["id"])["id"],))
        unreadable, readable = job_of(c, secret["id"]), job_of(c, first["id"])
    assert refusal(api, machine, unreadable) == "unreadable"
    assert refusal(api, machine, readable) is None
    sweep(api)
    sweep(api)                                                    # a second pass finds nothing left to cancel
    with store.read() as c:
        assert job_of(c, secret["id"])["state"] == "canceled"
        assert suppressed(c, unreadable["id"]) == [{"reason": "unreadable"}]
        done = job_of(c, finished["id"])
        assert done["state"] == "completed" and suppressed(c, done["id"]) == []
        assert job_of(c, first["id"])["state"] == "queued" and suppressed(c, readable["id"]) == []
        # The message stays where it was: only the job is let go.
        assert H.message(c, unreadable["message_id"])
    assert claim(api, machine)["job_id"] == readable["id"]
    assert claim(api, machine, "ops") is None


def test_health_counts_no_queued_work_from_the_cancelled_job(api):
    machine = task_bot(api, lanes=True, others=("finance",))
    secret_taken_away(api)
    store = api.app.state.store

    def ops_rows():
        with store.transaction() as c:
            c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'", (H.shift(H.now(), minutes=-30),))
            online = {r["id"] for r in health._online_runners(c)}
            waiting, slow, stuck = health._waiting(c, online, store.settings)
        return [r for r in waiting + slow + stuck if r["bot_id"] == "ops"]

    assert post(api, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None   # the claim passes it over...
    before = ops_rows()
    assert before and {r["queued"] for r in before} == {1} and "unreadable" in {r["reason"] for r in before}
    sweep(api)                                                                     # ...the sweep lets it go
    assert ops_rows() == []


def test_work_held_for_another_reason_stays_queued(api, monkeypatch):
    machine = task_bot(api, lanes=True, others=("finance",))
    secret = secret_taken_away(api)
    preset, checked, drained = new_task(api, "preset"), new_task(api, "checked"), new_task(api, "drained", "finance")
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE jobs SET held=1 WHERE id=?", (job_of(c, preset["id"])["id"],))       # a wake preset holds it
        # Held and unreadable: its release would only hand the claim the same unreadable message.
        c.execute("UPDATE jobs SET held=1 WHERE id=?", (job_of(c, secret["id"])["id"],))
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('finance',1)")
        jobs = {"preset": job_of(c, preset["id"]), "checked": job_of(c, checked["id"]),
                "drained": job_of(c, drained["id"], "finance")}
    # A check subtask whose model a usage limit still holds.
    real_of, real_held = check_models.of, check_models.held
    monkeypatch.setattr(check_models, "of", lambda c, task_id, bot: {
        "runtime": "codex", "model": "gpt-5", "harness": "codex", "effort": None, "role": "check"}
                        if task_id == checked["id"] else real_of(c, task_id, bot))
    monkeypatch.setattr(check_models, "held", lambda c, task_id: task_id == checked["id"] or real_held(c, task_id))
    assert refusal(api, machine, jobs["checked"]) == "check_model"
    with store.read() as c:
        assert execution.bot_refusal(c, store.settings, machine["runner_id"], "finance") == "draining"
    sweep(api)
    with store.read() as c:
        for job in jobs.values():
            assert c.execute("SELECT state FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "queued"
            assert suppressed(c, job["id"]) == []
        assert job_of(c, secret["id"])["state"] == "canceled"


def test_the_claim_that_finds_its_pick_unreadable_says_why_it_let_it_go(api, monkeypatch):
    """The claim's own last check (the job turned unreadable between candidate() and the write) records it too."""
    machine = task_bot(api, lanes=True, others=("finance",))
    secret = secret_taken_away(api)
    store = api.app.state.store
    with store.read() as c:
        job = job_of(c, secret["id"])
    picks = []

    def candidate(self, c, who, body, runner, *, job_id=None, skip=(), **_):
        picks.append(job_id)
        return None if job["id"] in skip else job

    monkeypatch.setattr(execution.Execution, "candidate", candidate)
    monkeypatch.setattr(execution.Execution, "idle_claim", lambda self, *a, **k: None)
    assert claim(api, machine, "ops") is None
    assert picks == [None, job["id"]], "select in the read snapshot, then recheck that pick under the writer"
    with store.read() as c:
        assert job_of(c, secret["id"])["state"] == "canceled"
        assert suppressed(c, job["id"]) == [{"reason": "unreadable"}]


def test_a_stale_prepared_pick_leaves_the_backfill_to_the_next_poll(api, monkeypatch):
    from backend import auto_tasks

    machine = task_bot(api, lanes=True)
    task = new_task(api, "queued")
    store = api.app.state.store
    with store.read() as c:
        job = job_of(c, task["id"])
    picks = []
    backfills = []

    def candidate(self, c, who, body, runner, *, job_id=None, skip=(), **_):
        picks.append(job_id)
        return job if job_id is None and job["id"] not in skip else None

    def backfill(*args, **kwargs):
        backfills.append(True)

    monkeypatch.setattr(execution.Execution, "candidate", candidate)
    monkeypatch.setattr(execution.Execution, "idle_claim", lambda self, *a, **k: None)
    monkeypatch.setattr(auto_tasks, "missing", backfill)
    assert claim(api, machine, "ops") is None
    # The stale pick is passed over and the next pick finds nothing else, so the claim answers none.
    assert picks == [None, job["id"], None]
    assert backfills == [], "a stale prepared pick leaves automatic backfill to the next poll"
    with store.read() as c:
        assert job_of(c, task["id"])["state"] == "queued"
