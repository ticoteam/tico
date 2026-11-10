"""Where queued_reasons (Health) says a queued job is handed out, the claim must hand it out: a bot whose own login and
codex are held while claude is free on this computer, and a queued keeper notice that is obsolete ahead of a
real one."""

import pytest

from backend import login_holds
from backend import wake_presets as W
from backend.execution import queued_reasons
from backend.store import H
from backend.tests.helpers.api import assign, post, runner
from backend.tests.helpers.login_holds import at, providers_on, signed_in
from backend.tests.helpers.stop_waiting import task_attempt


def test_claim_hands_out_the_queued_job_to_the_free_claude_fallback_that_health_says_is_free(api, monkeypatch):
    providers_on(api)
    at(monkeypatch, "2026-10-20T10:30:00.000000Z", api)
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=8 WHERE runner_id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], ["codex", "claude"])
    hit, reset = "2026-10-20T10:00:00.000000Z", "2026-10-20T12:00:00.000000Z"
    with api.app.state.store.transaction() as c:
        login_holds.record(c, machine["runner_id"], "", "fake", hit=hit, reset=reset)
        login_holds.record(c, machine["runner_id"], "", "codex", hit=hit, reset=reset)
    post(api, "tasks", {"owner": "ops", "title": "Free work", "body": "Please do it."})
    with api.app.state.store.read() as c:
        reasons = list(queued_reasons(c, api.app.state.store.settings, machine["runner_id"], "ops"))
    assert [why for _, why in reasons] == [None], "Health should say the queued job is handed out"
    # The sim's claim body: no bot of this runner is named busy, and the runner has free capacity.
    body = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
    reply = post(api, "jobs/claim", body, token=machine["token"])
    assert reply["attempt"] is not None, f"claim answered {reply} for work Health says is handed out"
    assert reply["attempt"]["job_id"] == reasons[0][0]["id"]
    assert reply["attempt"]["config"]["primary_held"]


def finished_with_notices(api, real_on_open_task):
    """ops's task done, with two queued Tico notices: an obsolete one on the done task (nothing a person did caused
    it), and a real one, older last. The real one is a person-caused notice on an open task of ops's, or on the done
    task itself. Returns (machine, obsolete job, real job)."""
    machine, task, original = task_attempt(api)
    other = post(api, "tasks", {"owner": "ops", "title": "Count the lines", "body": "Count them."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished_at=? WHERE id=?", (H.now(), original["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (original["job_id"],))
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")  # the other task's own wake has run
        H.task_move(c, "human:ana", task["id"], "done", "Finished.")
        refs = {"task": task["id"], "trigger": "other", "wake": "update"}
        obsolete = H.say(
            c,
            H.KEEPER,
            "bot:ops",
            "PR #7 checks passed",
            kind="notice",
            conversation_id=task["conversation_id"],
            refs=refs,
        )
        on = other if real_on_open_task else task
        real = H.say(
            c,
            H.KEEPER,
            "bot:ops",
            "A comment from human:ana",
            kind="notice",
            conversation_id=on["conversation_id"],
            refs={**refs, "task": on["id"], "caused_by": "human:ana"},
        )
        # The obsolete notice is the older of the two, so the candidate's own order reaches it first.
        c.execute("UPDATE jobs SET created_at=? WHERE message_id=?", (H.shift(H.now(), minutes=-5), obsolete["id"]))
        W.tick(c, H.shift(H.now(), minutes=2))
        obsolete_job = c.execute("SELECT id FROM jobs WHERE message_id=?", (obsolete["id"],)).fetchone()[0]
        real_job = c.execute("SELECT id FROM jobs WHERE message_id=?", (real["id"],)).fetchone()[0]
    return machine, obsolete_job, real_job


def claimed_after_health(api, machine):
    with api.app.state.store.read() as c:
        reasons = list(queued_reasons(c, api.app.state.store.settings, machine["runner_id"], "ops"))
    assert [why for _, why in reasons] == [None, None], "Health should say both queued notices are handed out"
    body = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
    reply = post(api, "jobs/claim", body, token=machine["token"])
    assert reply["attempt"] is not None, f"claim answered {reply} for work Health says is handed out"
    return reply["attempt"]


def test_claim_hands_out_the_real_notice_behind_an_obsolete_one_that_health_says_is_handed_out(api):
    """Health counts an obsolete keeper notice as handed out, and the claim's write path cancels it before it chooses:
    the claim must then go on to the next queued job instead of answering nothing."""
    machine, obsolete_job, real_job = finished_with_notices(api, real_on_open_task=True)
    assert claimed_after_health(api, machine)["job_id"] == real_job
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (obsolete_job,)).fetchone()[0] == "canceled"


def test_health_and_the_claim_agree_on_a_person_caused_notice_on_a_done_task(api):
    """Nothing wakes a bot about a Done task (docs/pipelines-v3.md, check rule 7), a person-caused Tico notice
    included: task_wakes.may_start holds it, so it is obsolete as any notice on a finished task is
    (obsolete_notices_sql). Health counts both queued notices as handed out, as it does every obsolete one, and the
    claim cancels both before it chooses: nothing is left queued that the claim would not hand out. The person's own
    words still get their answer; only Tico's notice of them is dropped."""
    machine, obsolete_job, real_job = finished_with_notices(api, real_on_open_task=False)
    body = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
    with api.app.state.store.read() as c:
        reasons = list(queued_reasons(c, api.app.state.store.settings, machine["runner_id"], "ops"))
    assert [why for _, why in reasons] == [None, None]
    assert post(api, "jobs/claim", body, token=machine["token"])["attempt"] is None
    with api.app.state.store.read() as c:
        for job in (obsolete_job, real_job):
            assert c.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0] == "canceled"
        assert list(queued_reasons(c, api.app.state.store.settings, machine["runner_id"], "ops")) == []


def test_a_person_caused_notice_on_a_done_task_does_not_keep_the_claim_from_the_work_behind_it(api):
    """The held notice on the Done task is the oldest queued job; the claim drops it and hands out the person-caused
    notice on the open task behind it, as Health says, instead of answering nothing."""
    machine, task, original = task_attempt(api)
    other = post(api, "tasks", {"owner": "ops", "title": "Count the lines", "body": "Count them."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed',finished_at=? WHERE id=?", (H.now(), original["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE id=?", (original["job_id"],))
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued'")
        H.task_move(c, "human:ana", task["id"], "done", "Finished.")
        sent = {}
        for name, on in (("done", task), ("open", other)):
            sent[name] = H.say(
                c,
                H.KEEPER,
                "bot:ops",
                "A comment from human:ana",
                kind="notice",
                conversation_id=on["conversation_id"],
                refs={"task": on["id"], "trigger": "other", "wake": "update", "caused_by": "human:ana"},
            )
        c.execute("UPDATE jobs SET created_at=? WHERE message_id=?", (H.shift(H.now(), minutes=-5), sent["done"]["id"]))
        W.tick(c, H.shift(H.now(), minutes=2))
        done_job, open_job = (
            c.execute("SELECT id FROM jobs WHERE message_id=?", (sent[k]["id"],)).fetchone()[0]
            for k in ("done", "open")
        )
    body = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
    attempt = post(api, "jobs/claim", body, token=machine["token"])["attempt"]
    assert attempt is not None and attempt["job_id"] == open_job
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (done_job,)).fetchone()[0] == "canceled"
