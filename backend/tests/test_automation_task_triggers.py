"""Saved automations on the five task events (Dev 666): each event runs one saved automation once, a task an automation
makes starts no task-event automation for its creation (one level deep, and its history says why), and more than 50
task-event runs in an hour pause the automation, which its row reads as "Paused: too many runs"."""
from backend.tests.test_api import api, get, post  # noqa: F401
from backend import hubdb as H
from backend.tests.test_automation_triggers import make, runs


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def started(api, aid):
    """Runs started, counted in SQL: the runs listing shows one page of 25."""
    return sql(api, "SELECT count(*) AS n FROM automation_runs WHERE automation_id=?", (aid,))[0]["n"]


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
    assert started(api, aid) == 1
    assert len(sql(api, "SELECT id FROM tasks WHERE note='Seen'")) == 1


def test_a_task_an_automation_makes_starts_no_task_event_automation(api):
    maker = make(api, {"type": "event", "name": "task.created"}, [SPAWN])
    heard = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    post(api, "tasks", {"owner": "ops", "title": "Start", "body": "Please."})
    assert len(runs(api, maker)["runs"]) == 1
    assert len(runs(api, heard)["runs"]) == 1          # the human's task, not the one the maker made
    follow = sql(api, "SELECT id FROM tasks WHERE title='Follow up'")
    assert len(follow) == 1
    why = sql(api, "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND note LIKE '%did not run%'", (follow[0]["id"],))
    assert len(why) == 1 and "automation" in why[0]["note"]


def test_fifty_task_event_runs_an_hour_pause_the_automation(api):
    aid = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    for i in range(50):
        post(api, "tasks", {"owner": "ops", "title": f"Batch {i}", "body": "Please."})
    assert started(api, aid) == 50
    post(api, "tasks", {"owner": "ops", "title": "Batch 50", "body": "Please."})
    assert started(api, aid) == 50                      # the 51st is not started
    row = automation(api, aid)
    assert row["enabled"] is False and row["paused"] is True
    assert row["paused_reason"] == "Paused: too many runs"


def test_a_task_change_made_inside_a_chain_still_runs_the_later_task_event_automations(api):
    """Only a task's creation is held back by the chain: a done or step change made in a chained turn runs its automation."""
    from backend.automations import CHAIN
    done = make(api, {"type": "event", "name": "task.done"}, [NOTE])
    post(api, "tasks", {"owner": "ops", "title": "Finish me", "body": "Please."})
    with api.app.state.store.read() as c:
        tid = c.execute("SELECT id FROM tasks WHERE title='Finish me'").fetchone()[0]
    token = CHAIN.set(("auto_maker",))
    try:
        with api.app.state.store.transaction() as c:
            H.task_update(c, "bot:ops", tid, status="done", note="Finished in a turn")
        api.app.state.store.automations.drain()
    finally:
        CHAIN.reset(token)
    assert len(runs(api, done)["runs"]) == 1
    assert started(api, done) == 1
