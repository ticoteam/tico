"""Wake presets (backend/wake_presets.py): what a preset holds at enqueue time, what it never holds, and what it never
changes. Times are set on the jobs directly and the planner is run at a chosen moment, so nothing here sleeps."""
import json

import pytest

from backend import github as G
from backend import wake_presets as W
from backend.store import H
from backend.tests.test_api import api, claim, get, post, ready, setup_attempt  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import new_task, task_bot

PR = "https://github.com/ticoteam/tico/pull/412"


def preset(api, bot, name):
    return post(api, f"bots/{bot}/wake-preset", {"wake_preset": name, "expected_revision": get(api, "bots/" + bot)["revision"]})


def notice(api, task, body, to="ops", **refs):
    with api.app.state.store.transaction() as c:
        return H._wake(c, H.task(c, task["id"]), "bot:" + to, body, refs or None)


def jobs(api, bot="ops", since=""):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT j.id,j.state,j.held,j.wake_class,j.hold_until,m.body FROM jobs j "
                                           "JOIN messages m ON m.id=j.message_id WHERE j.bot=? AND m.created>? "
                                           "ORDER BY m.rowid", (bot, since))]


def at(api, job_id, when):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created=? WHERE id=?", (when, job_id))


def tick(api, when):
    with api.app.state.store.transaction() as c:
        W.tick(c, when)


def test_an_existing_bot_keeps_current_behaviour_and_a_new_bot_starts_on_token_saver(api):
    view = get(api, "bots/ops")["wake"]
    assert view["preset"] == "current" and not view["chosen"] and view["summary"].startswith("Current behaviour")
    task = new_task(api, "existing")
    notice(api, task, "Finished: a request", wake_batch="completion")
    assert [(j["state"], j["held"]) for j in jobs(api)] == [("queued", 0), ("queued", 0)]
    made = post(api, "bots", {"slug": "scout", "display_name": "Scout", "description": "A test bot.", "status": "active",
                              "repo": "emp-scout", "thread_mode": "personal", "model": "hermes-profile",
                              "effort": "as-configured", "harness": "hermes", "operator": "ana", "owners": ["ana"],
                              "runner_id": None})
    assert made["wake"]["preset"] == "token_saver"
    listed = {b["slug"]: b for b in get(api, "bots")}
    assert listed["scout"]["wake"]["batch_seconds"] == 1800 and listed["ops"]["wake"]["preset"] == "current"


@pytest.mark.parametrize("name", ["token_saver", "performance"])
def test_immediate_wakes_are_never_held_and_a_duplicate_wakes_once(api, name):
    preset(api, "ops", name)
    task = new_task(api, "assigned")                                     # an assignment
    post(api, "chat/ops", {"text": "Can you look at this?"})            # a person's question
    post(api, "chat/ops", {"text": "Can you look at this?"})            # the same, sent twice
    for body in ("Unblocked: assigned\nThe migration is finished.", "Stuck: the deploy key was revoked",
                 "Ready for review: assigned"):
        notice(api, task, body)
        notice(api, task, body)                                          # a duplicated event
    rows = jobs(api)
    assert all(not j["held"] for j in rows)
    live = [j["body"] for j in rows if j["state"] == "queued"]
    assert len(live) == len(set(live)) == 5
    assert sum(j["wake_class"] == "duplicate" for j in rows) == 4


def test_an_unchanged_webhook_queues_nothing(api):
    preset(api, "ops", "token_saver")
    task = post(api, "tasks", {"owner": "ops", "title": "Repair checks", "body": "x", "links": [PR]})
    failing = {"repository": {"full_name": "ticoteam/tico"}, "check_run": {
        "name": "Unit tests", "conclusion": "failure", "pull_requests": [{"number": 412}]}}
    with api.app.state.store.transaction() as c:
        G.pr_signal(c, "check_run", failing)
        G.pr_signal(c, "check_run", failing)
        c.execute("UPDATE registry_metadata SET value_json=json_set(value_json,'$.due',?) WHERE key=?",
                  (H.shift(H.now(), seconds=-1), "github-task-wake:" + task["id"]))
        assert G.flush_wakes(c) == [task["id"]]
        G.pr_signal(c, "check_run", failing)                            # still failing: nothing new
        assert G.flush_wakes(c) == []
    ci = [j for j in jobs(api) if j["wake_class"] == "ci"]
    assert len(ci) == 1 and ci[0]["held"] == 1                          # a failing check nobody waits on is batched


@pytest.mark.parametrize("name", ["token_saver", "performance"])
def test_a_subordinates_ordinary_edits_do_not_wake_its_manager(api, name):
    preset(api, "coo", name)
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "bot:coo", "Plan the launch", "Plan it.", "bot:coo")
        task = H.task_create(c, "bot:coo", "Draft the plan", "Please draft it.", "bot:ops", parent_id=parent["id"])
        before = c.execute("SELECT count(*) FROM jobs WHERE bot='coo'").fetchone()[0]
        H.task_update(c, "bot:ops", task["id"], status="doing", note="Started on the outline")
        H.task_update(c, "bot:ops", task["id"], note="Half way")
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='coo'").fetchone()[0] == before
        H.task_update(c, "bot:ops", task["id"], status="done", note="Drafted")
    [done] = [j for j in jobs(api, "coo") if j["body"].startswith("Finished")]
    assert done["held"] == 1 and done["wake_class"] == "completion"     # news it was not waiting on


def test_token_saver_gives_three_notices_in_its_window_one_wake(api):
    preset(api, "ops", "token_saver")
    tasks = [new_task(api, f"report {n}") for n in range(3)]
    since = H.now()
    t0 = H.shift(H.now(), hours=-1)
    for n, task in enumerate(tasks):
        notice(api, task, f"Finished: part {n}", wake_batch="completion")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, minutes=10 * n))
    tick(api, H.shift(t0, minutes=29))
    assert all(j["held"] and j["hold_until"] == H.shift(t0, minutes=30) for j in jobs(api, since=since))
    tick(api, H.shift(t0, minutes=31))
    rows = jobs(api, since=since)
    assert [j["state"] for j in rows] == ["cancelled"] * 3 + ["queued"]
    assert rows[-1]["wake_class"] == "digest" and all(f"report {n}" in rows[-1]["body"] for n in range(3))
    # The next batch waits a whole window after this one went out.
    notice(api, tasks[0], "Finished: part 4", wake_batch="completion")
    at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, minutes=40))
    tick(api, H.shift(t0, minutes=41))
    assert jobs(api, since=since)[-1]["hold_until"] == H.shift(t0, minutes=70)


def test_performance_coalesces_one_tasks_notices_within_a_minute(api):
    preset(api, "ops", "performance")
    task = new_task(api, "fix")
    since, t0 = H.now(), H.shift(H.now(), hours=-1)
    for n, body in enumerate(("1 review comments on tico#412", "2 review comments on tico#412")):
        notice(api, task, body, wake_batch="review")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=20 * n))
    tick(api, H.shift(t0, seconds=59))
    assert all(j["held"] for j in jobs(api, since=since))
    tick(api, H.shift(t0, seconds=61))
    assert [(j["state"], j["held"]) for j in jobs(api, since=since)] == [("completed", 0), ("queued", 0)]


@pytest.mark.parametrize("name,gap", [("token_saver", 2 * 3600), ("performance", 15 * 60)])
def test_an_enabled_sweep_runs_at_most_once_per_cap(api, name, gap):
    preset(api, "ops", name)
    task = new_task(api, "hourly digest")
    since, t0 = H.now(), H.shift(H.now(), hours=-5)
    for n in range(3):
        notice(api, task, f"Scheduled occurrence {n} absorbed into this open task.", wake="occurrence", sweep="s1")
        at(api, jobs(api, since=since)[-1]["id"], H.shift(t0, seconds=60 * n))
        tick(api, H.shift(t0, seconds=60 * n + 1))
    first, second, third = jobs(api, since=since)
    assert (first["state"], first["held"]) == ("queued", 0)              # the first run starts at once
    assert second["held"] and second["hold_until"] == H.shift(t0, seconds=gap)
    assert third["state"] == "cancelled"                                 # the same sweep already waits


def test_switching_presets_changes_only_the_preset(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=json_set(config_json,'$.compact_at',400000) WHERE bot='ops'")
        tables = [t for t in ("schedules", "triggers", "credential_grants", "subscriptions")
                  if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
        def state():
            row = c.execute("SELECT config_json,access_json FROM bot_config WHERE bot='ops'").fetchone()
            config = json.loads(row[0])
            config.pop("wake_preset", None)
            return config, row[1], [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables]
        before = state()
    for name in ("performance", "token_saver", "current"):
        assert preset(api, "ops", name)["wake"]["preset"] == name
        with api.app.state.store.read() as c:
            assert state() == before
    assert get(api, "bots/ops")["compact_at"] == 400000
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT after_json FROM settings_changes WHERE bot='ops' AND field='wake_preset' "
                                        "ORDER BY created")] == ['"performance"', '"token_saver"', '"current"']


def test_a_usage_limit_with_a_known_reset_waits_for_it_under_a_preset(api, monkeypatch):
    from backend.execution import cooling_off
    from backend.scheduler import Scheduler
    machine, _, attempt = setup_attempt(api)
    preset(api, "ops", "token_saver")
    reset = H.shift(H.now(), minutes=40)[:19] + "+00:00"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "reset"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True,
         "limit_resets_at": reset, "text": ""}, machine["token"])
    scheduler = Scheduler(api.app.state.store, api.app.state.execution)
    for minutes in (-30, -10, -1):                                       # no retry before the reset
        monkeypatch.setattr(H, "now", lambda m=minutes: H.shift(reset, minutes=m))
        ready(api, machine, ["ops"])
        scheduler.tick()
        assert claim(api, machine) is None
    monkeypatch.setattr(H, "now", lambda: H.shift(reset, seconds=2))
    scheduler.tick()
    with api.app.state.store.read() as c:
        assert not cooling_off(c, "ops")
        assert c.execute("SELECT count(*) FROM attempts WHERE job_id=?", (attempt["job_id"],)).fetchone()[0] == 1
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "queued"


def test_a_turn_records_its_wake_and_the_measures_count_it_by_preset(api):
    machine = task_bot(api, lanes=True)
    preset(api, "ops", "token_saver")
    since = H.shift(H.now(), minutes=-1)
    task = new_task(api, "measured")
    assert claim(api, machine)
    [run] = get(api, f"tasks/{task['id']}/run-view")["attempts"]
    assert run["wake"]["class"] == "assignment" and run["wake"]["preset_label"] == "Token saver"
    measures = {p["preset"]: p for p in get(api, "wake-presets/measures?since=" + since)["presets"]}
    assert measures["token_saver"]["turns"] == 1 and measures["token_saver"]["pickup_seconds"]["immediate_p50"] is not None
    assert measures["token_saver"]["missed_actions"] == 0 and measures["performance"]["jobs"] == 0
