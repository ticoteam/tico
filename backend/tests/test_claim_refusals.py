"""Health calls queued work a Tico bug (queue_idle) only when the claim itself has no reason to hold it: every reason
the claim passes over a bot (execution.claim_refusal, job_refusal) is Health's reason too, and a report names the bot
and computer only by the labels of the diagnostics bundle sent with it."""
import json
import re
from types import SimpleNamespace

import pytest

from backend import check_models, diagnostics, execution, health, health_monitor
from backend.store import H
from backend.tests.test_api import api, claim, get, headers, post  # noqa: F401  (api is a fixture)
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
        c.execute("UPDATE jobs SET created_at=? WHERE bot='ops' AND state='queued'", (H.shift(H.now(), minutes=-30),))
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


def test_a_bot_limited_to_one_task_claims_only_it_and_its_subtasks_until_released(api):
    machine = task_bot(api, lanes=True)
    def control(**body):
        post(api, "bots/ops/control", {**body, "expected_revision": get(api, "bots/ops")["revision"]})
    task, other = new_task(api, "the check"), new_task(api, "unrelated")
    child = post(api, "tasks", {"owner": "ops", "title": "review", "body": "Review it.",
                                "relations": [{"task": task["id"], "kind": "parent"}]})
    control(action="pause")
    control(action="scope", task_id=task["id"])
    assert claim(api, machine) is None                       # the scope never lifts a pause
    control(action="resume")
    post(api, "chat/ops", {"text": "Anything new?"})          # new work outside the task arrives during the check
    taken = {claim(api, machine)["lane"], claim(api, machine)["lane"]}
    assert taken == {"task:" + task["id"], "task:" + child["id"]} and claim(api, machine) is None
    with api.app.state.store.read() as c:                     # Health reads the same rule
        assert execution.bot_refusal(c, api.app.state.store.settings, machine["runner_id"], "ops") == "scoped"
    control(action="pause")
    control(action="unscope")
    assert claim(api, machine) is None                       # released while paused: eligible again, not started
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM attempts WHERE bot='ops' AND state IN ('leased','running')").fetchone()
        assert [r[0] for r in c.execute("SELECT action FROM events WHERE target='ops' AND action LIKE 'bot.%scope' "
                                        "ORDER BY rowid")] == ["bot.scope", "bot.unscope"]
    control(action="resume")
    lanes = {claim(api, machine)["lane"] for _ in range(4)}
    assert lanes == {"task:" + t["id"] for t in (task, child, other)} | {"main"}


def test_a_turn_running_when_its_bot_is_limited_takes_in_only_work_inside_the_limit(api):
    machine = task_bot(api, lanes=True)
    other, task = new_task(api, "unrelated"), new_task(api, "the check")
    turns = {a["lane"]: a for a in (claim(api, machine), claim(api, machine))}
    for a in turns.values():
        post(api, f"attempts/{a['id']}/started", {"thread_id": a["lane"]}, machine["token"])
    post(api, "bots/ops/control", {"action": "scope", "task_id": task["id"],
                                   "expected_revision": get(api, "bots/ops")["revision"]})
    with api.app.state.store.transaction() as c:              # follow-ups to both turns, after the limit is set
        outside = H.task_comment(c, "human:ana", other["id"], "Use the new numbers.")
        inside = H.task_comment(c, "human:ana", task["id"], "Check the March total too.")
    poll = lambda t: [m["id"] for m in post(api, f"attempts/{turns['task:' + t['id']]['id']}/inputs", {},
                                            machine["token"])["messages"]]
    assert poll(other) == [] and poll(task) == [inside["id"]]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (outside["id"],)).fetchone()[0] == "queued"


def test_a_limited_bots_turn_carries_only_next_run_work_inside_the_limit_until_released(api, monkeypatch):
    from backend import bot_to_bot
    backlog = []
    monkeypatch.setattr(bot_to_bot, "continue_backlog", lambda c, bot: backlog.append(bot))
    machine = task_bot(api, lanes=True)
    task = new_task(api, "the check")
    child = post(api, "tasks", {"owner": "ops", "title": "review", "body": "Review it.", "next_run": True,
                                "relations": [{"task": task["id"], "kind": "parent"}]})
    other = post(api, "tasks", {"owner": "ops", "title": "unrelated", "body": "Do it later.", "next_run": True})
    note = post(api, "notes", {"to": "ops", "text": "Cancellations are flat."})["note"]
    post(api, "bots/ops/control", {"action": "scope", "task_id": task["id"],
                                   "expected_revision": get(api, "bots/ops")["revision"]})
    run = lambda: post(api, "jobs/claim", {"next_run": True}, token=machine["token"])["attempt"]  # noqa: E731
    limited = run()
    assert limited["lane"] == "task:" + task["id"]
    assert [t["id"] for t in limited["next_run"]] == [child["id"]] and limited["notes"] == [] and backlog == []
    with api.app.state.store.read() as c:                     # the held work is left exactly as it was
        assert c.execute("SELECT carried_by FROM tasks WHERE id=?", (other["id"],)).fetchone()[0] is None
        assert c.execute("SELECT carried_by FROM notes WHERE id=?", (note["id"],)).fetchone()[0] is None
    post(api, "bots/ops/control", {"action": "unscope", "expected_revision": get(api, "bots/ops")["revision"]})
    post(api, "chat/ops", {"text": "Anything new?"})
    released = run()
    assert [t["id"] for t in released["next_run"]] == [other["id"]]
    assert [n["id"] for n in released["notes"]] == [note["id"]] and backlog == ["ops"]


def test_claim_limit_migration_upgrades_a_database_at_62_without_lifting_a_drain(api):
    store = api.app.state.store
    task_bot(api, lanes=True)
    with store.transaction() as c:                            # a database from before the limit existed
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('ops',1) ON CONFLICT(bot) DO UPDATE SET draining=1")
        c.execute("ALTER TABLE bot_control DROP COLUMN claim_task")
        c.execute("DELETE FROM cloud_migrations WHERE version=63")
    store.initialize()
    store.initialize()                                        # a repeated start takes the same additive path
    with store.read() as c:
        assert [dict(r) for r in c.execute("SELECT * FROM bot_control WHERE bot='ops'")] == [
            {"bot": "ops", "draining": 1, "claim_task": None}]
        assert c.execute("SELECT count(*) FROM cloud_migrations WHERE version=63").fetchone()[0] == 1
        assert execution.claim_scope(c, "ops") is None


def test_a_bot_whose_worktrees_the_runner_is_maintaining(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    says(api, machine, "maintaining", busy_bots=["ops"])


def test_a_lane_a_lapsed_lease_still_runs_in(api, monkeypatch):
    """expire() requeued the job, but the runner's worker still runs it: the runner's busy list holds its lane."""
    # A new computer can still be inside the freshness window measured from monotonic zero.
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: execution.CLAIMED_FRESH / 2))
    machine = task_bot(api, lanes=True)
    task = new_task(api, "first")
    says(api, machine, "lane_held", busy_lanes=["ops|task:" + task["id"]])
    # The busy list is the runner's latest claim, and only while it is fresh.
    expired = execution.time.monotonic() - execution.CLAIMED_FRESH - 1
    execution.CLAIMED_BUSY[machine["runner_id"]] = (*execution.CLAIMED_BUSY[machine["runner_id"]][:2], expired)
    store = api.app.state.store
    with store.read() as c:
        assert execution.bot_refusal(c, store.settings, machine["runner_id"], "ops") is None


def test_work_fenced_behind_an_interrupted_run_on_its_task(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "first")
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='lost',finished_at=? WHERE id=?", (H.now(), attempt["id"]))
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


def test_operations_names_a_limited_bots_runtime_when_config_was_primed_slug_first(api):
    """prime_claimables and the machines page prime bot_config rows whose first column is the slug, not the JSON;
    the limit issue reads the configuration by name, so it does not parse the slug as JSON."""
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_status(bot,state,since,updated_at) VALUES('ops','limited',?,?) "
                  "ON CONFLICT(bot) DO UPDATE SET state='limited',since=excluded.since", (H.now(), H.now()))
    body = api.get("/api/v2/operations", headers=headers())
    assert body.status_code == 200, body.text
    [issue] = [i for i in body.json()["issues"] if i["kind"] == "bot" and i["bot"] == "ops"]
    assert issue["detail"].startswith("fake; resumes")


def readiness(api, machine, document):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness=? WHERE id=?", (json.dumps(document), machine["runner_id"]))


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


def test_the_report_names_the_bot_and_computer_by_their_bundle_labels(api):
    machine = task_bot(api, lanes=True)
    new_task(api, "first")
    held(api, machine, asks=False)
    store = api.app.state.store
    with store.transaction() as c:
        item = next(f for f in health_monitor.findings(c, store.settings) if f["check_id"] == "queue_idle")
        now = H.now()
        row = {**item, "first_seen_at": now, "last_seen_at": now, "occurrences": 1}
        c.execute("INSERT INTO health_findings(key,signature,check_id,kind,title,detail,fix,first_seen_at,last_seen_at) "
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
        c.execute("UPDATE jobs SET created_at=? WHERE bot='ops' AND state='queued' AND message_id IN "
                  "(SELECT id FROM messages WHERE body LIKE ?)", (H.shift(H.now(), minutes=-minutes), f"%{task['title']}%"))
        return c.execute("SELECT created_at FROM jobs WHERE bot='ops' AND state='queued' AND message_id IN "
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
    # The claim takes a person's message before older work: a young chat ahead of a free job old at sixteen minutes
    # is not slow, and the chat itself is old at two.
    queued_at(api, second, 16)
    chat = post(api, "chat/ops", {"text": "Are you there?"})
    rows, found = ops_rows(api)
    assert rows == [] and "queue_idle" not in found
    with api.app.state.store.transaction() as c:
        asked = H.shift(H.now(), minutes=-3)
        c.execute("UPDATE jobs SET created_at=? WHERE message_id=?", (asked, chat["id"]))
    assert [(r["reason"], r["oldest"]) for r in ops_rows(api)[0]] == [("slow", asked)]
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
