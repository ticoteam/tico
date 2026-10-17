"""Health calls queued work a Tico bug (queue_idle) only when the claim itself has no reason to hold it: every reason
the claim passes over a bot (execution.claim_refusal, job_refusal) is Health's reason too, and a report names the bot
and computer only by the labels of the diagnostics bundle sent with it."""
import json
import re

import pytest

from backend import check_models, diagnostics, execution, health, health_monitor
from backend.store import H
from backend.tests.test_api import api, claim, headers, post  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot


@pytest.fixture(autouse=True)
def forget_claims():
    execution.CLAIMED_BUSY.clear()
    yield
    execution.CLAIMED_BUSY.clear()


def held(api, machine, asks=True, **busy):
    """The runner's claim with its own busy lists (it takes nothing), then Health's row for ops and the Health
    Monitor's findings, with ops's queued work made 30 minutes old. `asks`: False for a runner that never claimed it."""
    if asks:
        assert post(api, "jobs/claim", busy, token=machine["token"])["attempt"] is None
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE jobs SET created=? WHERE bot='ops' AND state='queued'", (H.shift(H.now(), minutes=-30),))
        online = {r["id"] for r in health._online_runners(c)}
        rows = [r for r in health._waiting(c, online, store.settings)[2] if r["bot"] == "ops"]
        found = {f["check_id"] for f in health_monitor.findings(c, store.settings)}
    return rows, found


def says(api, machine, reason, **busy):
    rows, found = held(api, machine, **busy)
    assert [r["reason"] for r in rows] == [reason]
    assert "queue_idle" not in found
    with api.app.state.store.read() as c:
        [row] = health._stuck(c, rows, set(), health_admin(api), True)
    assert row["why"] == health.HELD[reason][0].format(computer="Test Mac")


def health_admin(api):
    from backend.auth import Identity
    return Identity("human:ana", "owner")


def test_unexplained_queued_work_is_still_queue_idle(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    rows, found = held(api, machine, asks=False)     # a claim would take it: nothing explains why none did
    assert [r["reason"] for r in rows] == ["slow"] and "queue_idle" in found


def test_a_drained_bot(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('ops',1)")
    says(api, machine, "draining")


def test_a_bot_whose_worktrees_the_runner_is_maintaining(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    says(api, machine, "maintaining", busy_bots=["ops"])


def test_a_lane_a_lapsed_lease_still_runs_in(api):
    """expire() requeued the job, but the runner's worker still runs it: the runner's busy list holds its lane."""
    machine = task_bot(api, lanes=True)
    task = new_task(api, "first")
    says(api, machine, "lane_held", busy_lanes=["ops|task:" + task["id"]])
    # The busy list is the runner's latest claim, and only while it is fresh.
    execution.CLAIMED_BUSY[machine["runner_id"]] = (*execution.CLAIMED_BUSY[machine["runner_id"]][:2], 0.0)
    store = api.app.state.store
    with store.read() as c:
        assert execution.bot_refusal(c, store.settings, machine["runner_id"], "ops") is None


def test_work_fenced_behind_an_interrupted_run_on_its_task(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "first")
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='lost',finished=? WHERE id=?", (H.now(), attempt["id"]))
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=(SELECT job_id FROM attempts WHERE id=?)", (attempt["id"],))
    post(api, "tasks/" + task["id"] + "/comments", {"text": "Is the first part done?"})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='ops' AND state='queued'").fetchone()[0] == 1
    says(api, machine, "uncertain")


def test_a_bot_cooling_off_after_a_usage_limit(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_status(bot,state,since,updated_at) VALUES('ops','limited',?,?) "
                  "ON CONFLICT(bot) DO UPDATE SET state='limited',since=excluded.since", (H.now(), H.now()))
    says(api, machine, "cooling_off")


def readiness(api, machine, document):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness_json=? WHERE id=?", (json.dumps(document), machine["runner_id"]))


def test_a_computer_that_has_not_said_whether_it_can_run_the_bot(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    readiness(api, machine, {"schema_version": 1, "runtimes": {}, "bots": {"ops": {"runtime": "codex"}}})
    says(api, machine, "readiness_unknown")
    readiness(api, machine, {"schema_version": 1, "runtimes": {}, "bots": {}})
    says(api, machine, "readiness_unknown")


def test_a_rejected_sign_in(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    readiness(api, machine, {"schema_version": 1, "runtimes": {"codex": {"installed": True, "authenticated": "rejected"}},
                             "bots": {"ops": {"ready": True, "runtime": "codex"}}})
    says(api, machine, "sign_in_rejected")


def test_a_subscription_profile_the_computer_has_not_reported(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO subscription_assignments(scope,target,profile) VALUES('bot','ops','team-plan')")
    says(api, machine, "profile")


def test_the_report_names_the_bot_and_computer_by_their_bundle_labels(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    held(api, machine, asks=False)
    store = api.app.state.store
    with store.transaction() as c:
        item = next(f for f in health_monitor.findings(c, store.settings) if f["check_id"] == "queue_idle")
        now = H.now()
        row = {**item, "first_seen": now, "last_seen": now, "occurrences": 1}
        c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen,last_seen) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (item["key"], item["signature"], item["check_id"], item["kind"],
                                                item["title"], item["detail"], item["fix"], now, now))
        message = health_monitor.report_message(c, row)
        bot, computer = diagnostics.bot_label(c, "ops"), diagnostics.runner_label(c, machine["runner_id"])
        bots = [(b["slug"], [b.get("display_name")]) for b in H.bots(c)]
        names = {b["slug"]: b["display_name"] for b in H.bots(c)}
    assert f"\nWhere: {bot} on {computer}\n" in message
    # The same labels the bundle gives them.
    assert diagnostics.Redactor((), bots, ()).label("ops") == bot and computer.startswith("runner-")
    assert not re.search(r"\bops\b", message) and "Test Mac" not in message and machine["runner_id"] not in message
    assert not names.get("ops") or names["ops"] not in message


def ops_rows(api):
    """Health's Stuck rows for ops and the Health Monitor's findings, as the jobs stand."""
    store = api.app.state.store
    with store.read() as c:
        online = {r["id"] for r in health._online_runners(c)}
        rows = [r for r in health._waiting(c, online, store.settings)[2] if r["bot"] == "ops"]
        found = {f["check_id"] for f in health_monitor.findings(c, store.settings)}
    return rows, found


def queued_at(api, task, minutes):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created=? WHERE bot='ops' AND state='queued' AND message_id IN "
                  "(SELECT id FROM messages WHERE body LIKE ?)", (H.shift(H.now(), minutes=-minutes), f"%{task['title']}%"))
        return c.execute("SELECT created FROM jobs WHERE bot='ops' AND state='queued' AND message_id IN "
                         "(SELECT id FROM messages WHERE body LIKE ?)", (f"%{task['title']}%",)).fetchone()[0]


def test_an_old_held_check_beside_a_young_free_job_is_neither_held_nor_slow(api):
    """Health ages the job the claim would take: an old check held by its model's usage limit says nothing while a
    young job waits normally, and its own reason once nothing else is queued."""
    machine = task_bot(api, lanes=True)
    check = new_task(api, "the check")
    with api.app.state.store.transaction() as c:
        check_models.record(c, "bot:ops", check["id"], {"runtime": "codex", "model": "gpt-6.1", "harness": "codex",
                                                         "effort": "", "role": "check"})
        check_models.hold(c, check["id"], "codex", H.shift(H.now(), minutes=30))
    queued_at(api, check, 40)
    rows, found = ops_rows(api)
    assert [r["reason"] for r in rows] == ["check_model"] and "queue_idle" not in found
    second = new_task(api, "the second")
    queued_at(api, second, 1)
    rows, found = ops_rows(api)
    assert rows == [] and "queue_idle" not in found
    # A person's message the claim would take is old at two minutes, behind a free job not yet old at ten.
    waiting = queued_at(api, second, 10)
    assert ops_rows(api)[0] == []
    chat = post(api, "chat/ops", {"text": "Are you there?"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created=? WHERE message_id=?", (H.shift(H.now(), minutes=-3), chat["id"]))
    assert [(r["reason"], r["oldest"]) for r in ops_rows(api)[0]] == [("slow", waiting)]
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs WHERE message_id=?", (chat["id"],))
    created = queued_at(api, second, 16)
    rows, found = ops_rows(api)
    assert [(r["reason"], r["oldest"]) for r in rows] == [("slow", created)] and "queue_idle" in found


def test_a_computer_restarting_holds_its_work_and_is_a_finding_once_it_takes_too_long(api):
    machine = task_bot(api, lanes=True)
    queued_at(api, new_task(api, "first"), 16)
    beat = {"version": "test", "platform": "test", "capacity": 8, "readiness": {"ops": True},
            "capabilities": ["task_lanes_v2"]}
    post(api, "runners/heartbeat", {**beat, "restart": {"since": H.shift(H.now(), minutes=-10)}}, token=machine["token"])
    rows, found = ops_rows(api)
    assert [r["reason"] for r in rows] == ["restarting"] and not found & {"queue_idle", "runner_restarting"}
    with api.app.state.store.read() as c:
        [row] = health._stuck(c, rows, set(), health_admin(api), True)
    assert row["why"] == "Test Mac is restarting: it starts new work once its running turns end"
    assert next(m for m in api.get("/api/v2/operations", headers=headers()).json()["computers"]
                if m["id"] == machine["runner_id"])["restarting"]
    # Past the runner's own drain and stop windows the restart is stuck: a setup finding, still no Tico bug.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET restart_since=? WHERE id=?", (H.shift(H.now(), minutes=-35), machine["runner_id"]))
    assert {"runner_restarting"} <= ops_rows(api)[1] and "queue_idle" not in ops_rows(api)[1]
    # The restarted runner's first heartbeat no longer says so.
    post(api, "runners/heartbeat", beat, token=machine["token"])
    rows, found = ops_rows(api)
    assert [r["reason"] for r in rows] == ["slow"] and "runner_restarting" not in found
