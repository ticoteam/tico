"""Saved automations on the five task events (Dev 666): each event runs one saved automation once, a task an automation
makes starts no task-event automation for its creation (one level deep, and its history says why), and more than 50
task-event runs in an hour pause the automation, which its row reads as "Paused: too many runs"."""
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_automation_triggers import make, runs


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def automation(api, aid):
    got = get(api, f"automations/{aid}")
    return got.get("automation", got)


NOTE = {"id": "note", "type": "action", "do": "note", "args": {"text": "Seen"}}
SPAWN = {"id": "spawn", "type": "action", "do": "create_task", "args": {"title": "Follow up", "owner": "coo"}}


def test_a_task_event_runs_one_saved_automation_once_and_not_the_old_engine(api):
    aid = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    post(api, "tasks", {"owner": "ops", "title": "Once", "body": "Please."})
    assert len(runs(api, aid)["runs"]) == 1
    assert sql(api, "SELECT count(*) AS n FROM rule_task_runs WHERE rule_id=?", (aid,))[0]["n"] == 0
    assert len(sql(api, "SELECT id FROM task_events WHERE note='Seen'")) == 1


def test_a_task_an_automation_makes_starts_no_task_event_automation(api):
    maker = make(api, {"type": "event", "name": "task.created"}, [SPAWN])
    heard = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    post(api, "tasks", {"owner": "ops", "title": "Start", "body": "Please."})
    assert len(runs(api, maker)["runs"]) == 1
    assert len(runs(api, heard)["runs"]) == 1          # the human's task, not the one the maker made
    follow = sql(api, "SELECT id FROM tasks WHERE title='Follow up'")
    assert len(follow) == 1
    why = sql(api, "SELECT note FROM task_events WHERE task_id=? AND note LIKE '%did not run%'", (follow[0]["id"],))
    assert len(why) == 1 and "automation" in why[0]["note"]


def test_fifty_task_event_runs_an_hour_pause_the_automation(api):
    aid = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    for i in range(50):
        post(api, "tasks", {"owner": "ops", "title": f"Batch {i}", "body": "Please."})
    assert len(runs(api, aid)["runs"]) == 50
    post(api, "tasks", {"owner": "ops", "title": "Batch 50", "body": "Please."})
    assert len(runs(api, aid)["runs"]) == 50           # the 51st is not started
    row = automation(api, aid)
    assert row["enabled"] is False and row["paused"] is True
    assert row["paused_reason"] == "Paused: too many runs"
