"""A computer that claims a bot's run and then says nothing (never starts it, never renews its lease, never hands it
back) does not loop silently (Tico Team, Nov 12: the shared machine did that to Performance Owner's runs ~344 times in
a day; each job failed after PRE_START_TRIES lapses, the next wake started over, nobody was told, and the bot's Runs
card showed none of it)."""

import json

from backend import execution, start_holds, task_wakes
from backend.run_diagnostics import LAPSED
from backend.store import H
from backend.tests.helpers.api import assign, claim, expire, get, post, ready, runner  # noqa: F401 (fixture)
from backend.tests.helpers.execution_review import add_recovery_reviewer


def start(api, botops=True):
    if botops:
        add_recovery_reviewer(api)
    machine = runner(api, "ana")
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    made = post(api, "tasks", {"owner": "ops", "title": "Review the negate PR", "body": "Review it."})
    task_id = made.get("task", made).get("id") or made["refs"]["task"]
    return machine, task_id, claim(api, machine)


def lapse(api, machine, attempt, wait_over=True):
    """Its lease runs out unrenewed and the server's sweep sees it; then (`wait_over`) any wait before the next try has
    passed."""
    expire(api, attempt["id"])
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)
        if wait_over:
            c.execute(
                "UPDATE attempts SET finished_at=? WHERE id=?",
                (H.shift(H.now(), seconds=-execution.HAND_BACK_MAX - 1), attempt["id"]),
            )
    ready(api, machine, ["ops"])


def job_state(api, job_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def botops_tasks(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,title,body,status FROM tasks WHERE owner_id='bot:botops'")]


def test_runs_that_only_lapse_go_to_botops_and_pause_the_task(api):
    machine, task_id, attempt = start(api)
    job_id = attempt["job_id"]
    for n in range(execution.PRE_START_TRIES):
        assert attempt and attempt["job_id"] == job_id
        lapse(api, machine, attempt)
        if n < execution.PRE_START_TRIES - 1:
            assert job_state(api, job_id) == "queued"
            assert not botops_tasks(api)
            attempt = claim(api, machine)
    assert job_state(api, job_id) == "failed"
    assert claim(api, machine) is None
    [review] = botops_tasks(api)
    assert review["title"] == "Find why ops's runs can't start" and review["status"] == "open"
    assert start_holds.LAPSED_WHY in review["body"] and "Review the negate PR" in review["body"]
    # Nobody is asked to fix the computer: BotOps has it, and the bot is not shown as crashed.
    line = get(api, "status?bot=ops")["status"]
    assert line["state"] == "idle"
    stop = line["last_stop"]
    assert stop["needs_person"] is False and stop["text"].endswith(start_holds.BOTOPS_HAS_IT)
    assert stop["reason"] == f"Its computer took this run {execution.PRE_START_TRIES} times and never started it."
    # The task waits on the bot, Paused, so the next wake does not start the loop over.
    with api.app.state.store.read() as c:
        assert task_wakes.task_cause(c, task_id, "bot:ops") == task_wakes.CANT_START


def test_every_lapse_is_logged_with_its_category_and_named_in_runs(api):
    machine, _, attempt = start(api, botops=False)
    lapse(api, machine, attempt)
    with api.app.state.store.read() as c:
        [detail] = [
            json.loads(r[0])
            for r in c.execute(
                "SELECT detail FROM events WHERE action='attempt.expired' AND target=?", (attempt["id"],)
            )
        ]
    assert detail["cause"] == "lapsed_unstarted"
    r = api.get("/api/runs", headers={"Authorization": "Bearer ana-test"})
    [row] = [x for x in r.json() if x["id"] == attempt["id"]]
    assert row["end_reason"] == LAPSED and row["outcome"] == "expired"
    [turn] = [t for t in get(api, "bots/ops/turns") if t["id"] == attempt["id"]]
    assert turn["end_reason"] == LAPSED


def test_a_second_lapse_in_a_row_waits_before_the_job_is_handed_out_again(api):
    machine, _, attempt = start(api, botops=False)
    job_id = attempt["job_id"]
    # The first lapse already waited out a whole lease: the job goes out again at once.
    lapse(api, machine, attempt, wait_over=False)
    attempt = claim(api, machine)
    assert attempt and attempt["job_id"] == job_id
    # From the second, it waits as a hand-back does, instead of being handed to the same silent computer at once.
    lapse(api, machine, attempt, wait_over=False)
    assert claim(api, machine) is None and job_state(api, job_id) == "queued"
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE attempts SET finished_at=? WHERE id=?",
            (H.shift(H.now(), seconds=-execution.HAND_BACK_WAIT - 1), attempt["id"]),
        )
    assert claim(api, machine)["job_id"] == job_id


def test_a_bot_page_reads_its_own_runs_past_the_companys_newest(api):
    machine, _, attempt = start(api, botops=False)
    lapse(api, machine, attempt)
    # A busy company: 200 newer runs of other bots fill the company-wide list.
    with api.app.state.store.transaction() as c:
        for n in range(200):
            c.execute(
                "INSERT INTO attempts(id,bot_id,created_at,trigger,trigger_kind,writes) VALUES(?,?,?,?,?,0)",
                (f"busy-{n}", "busy", H.shift(H.now(), seconds=n + 1), "message", "other"),
            )
    headers = {"Authorization": "Bearer ana-test"}
    assert attempt["id"] not in [x["id"] for x in api.get("/api/runs", headers=headers).json()]
    mine = api.get("/api/runs", params={"bot": "ops"}, headers=headers).json()
    assert attempt["id"] in [x["id"] for x in mine] and {x["bot"] for x in mine} == {"ops"}
