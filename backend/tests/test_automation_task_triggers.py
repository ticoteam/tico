"""Saved automations on the five task events (Dev 666): each event runs one saved automation once, task-triggered
automation chains run one level deep across all five events, and more than 50 task-event runs in an hour pause the
automation, which its row reads as "Paused: too many runs"."""
from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend import hubdb as H
from backend.tests.test_automation_triggers import make, runs
from backend.tests.test_task_trigger_rules import history as task_rule_history_rows, rule as task_rule, task_type


def sql(api, query, args=()):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(query, args)]


def task_rule_history(api, tid):
    with api.app.state.store.read() as c:
        return task_rule_history_rows(c, tid)


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


def test_task_trigger_action_does_not_start_a_task_event_automation(api):
    heard = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    task_rule(api, "task.created", action="create_task", params={"owner": "cpo", "title": "Rule follow-up"},
              when={"owner": "ops"})
    original = post(api, "tasks", {"owner": "ops", "title": "Starts a rule", "body": "Please."})

    assert started(api, heard) == 1
    assert len(runs(api, heard)["runs"]) == 1
    follow = sql(api, "SELECT id FROM tasks WHERE title='Rule follow-up'")
    assert len(follow) == 1 and follow[0]["id"] != original["id"]
    why = sql(api, "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND note LIKE '%did not run%'",
              (follow[0]["id"],))
    assert len(why) == 1
    assert "Task Trigger Action made this change" in why[0]["note"]
    assert "task-triggered automations run one level deep" in why[0]["note"]


def test_a_new_task_starts_a_task_step_automation_for_its_initial_step(api):
    with api.app.state.store.transaction() as c:
        typ = task_type(c)
    first_step = typ["steps"][0]["id"]
    aid = make(api, {"type": "event", "name": "task.step_changed",
                     "filters": [{"field": "event.data.step", "op": "is", "value": first_step}]}, [NOTE])

    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Initial step", "Synthetic body", "bot:ops", type=typ["id"],
                             lint=False, deduplicate=False)
    api.app.state.store.automations.drain()

    done = runs(api, aid)["runs"]
    assert len(done) == 1 and done[0]["trigger_ref"] == f"task.step_changed:{task['id']}"
    assert started(api, aid) == 1


def test_a_routine_made_task_starts_no_task_trigger_and_history_says_skipped(api):
    maker = make(api, {"type": "event", "name": "task.created"}, [SPAWN])
    heard = task_rule(api, "task.created", when={"owner": "coo"}, params={"text": "Must not run"})
    post(api, "tasks", {"owner": "ops", "title": "Start a routine", "body": "Please."})

    follow = sql(api, "SELECT id FROM tasks WHERE title='Follow up'")
    assert len(follow) == 1 and started(api, maker) == 1
    runs = sql(api, "SELECT fired FROM rule_task_runs WHERE rule_id=? AND task_id=? AND event='task.created'",
               (heard["id"], follow[0]["id"]))
    assert runs == [{"fired": 0}]
    why = task_rule_history(api, follow[0]["id"])
    assert len(why) == 1 and why[0]["new"] == heard["id"]
    assert "routine made this change" in why[0]["note"]
    assert "Skipped" in why[0]["note"] and "starts no task trigger" in why[0]["note"]


def test_a_routine_started_bot_turn_does_not_start_a_task_step_trigger(api):
    import json

    from backend import automations
    from backend.tests.test_api import runner

    routine = make(api, {"type": "event", "name": "task.created"}, [NOTE], enabled=False)
    entered = task_rule(api, "task.step_changed", when={"owner": "ops", "step": "In QA"})
    with api.app.state.store.transaction() as c:
        typ = task_type(c)
        task = H.task_create(c, "human:ana", "Move in a routine turn", "Synthetic body", "bot:ops",
                             type=typ["id"], step="Open", lint=False, deduplicate=False)

    enrolled = runner(api)
    now = H.now()
    message_id, job_id, attempt_id = (H.new_id() for _ in range(3))
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,refs,created_at) VALUES(?,?,?,?,?,?,?)",
                  (message_id, "human:ana", "bot:ops", "say", "Routine started this turn",
                   json.dumps({"automation_chain": [routine]}), now))
        c.execute("INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,?,'running',?,?)",
                  (job_id, message_id, "ops", now, attempt_id))
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                  "VALUES(?,?,?,?,?,?,'running',?,?)",
                  (attempt_id, job_id, "ops", enrolled["runner_id"], 1, H.new_id(), H.shift(now, minutes=5), now))
        run_token = H.RUN.set(attempt_id)
        chain_token = automations.CHAIN.set(())
        try:
            H.task_update(c, "bot:ops", task["id"], step="In QA", note="Moved in routine turn", mover=True)
        finally:
            automations.CHAIN.reset(chain_token)
            H.RUN.reset(run_token)

    why = task_rule_history(api, task["id"])
    assert len(why) == 1 and why[0]["new"] == entered["id"]
    assert "routine made this change" in why[0]["note"]
    assert "Skipped" in why[0]["note"] and "starts no task trigger" in why[0]["note"]
    runs = sql(api, "SELECT fired FROM rule_task_runs WHERE rule_id=? AND task_id=? AND event='task.step_changed'",
               (entered["id"], task["id"]))
    assert runs == [{"fired": 0}]


def test_fifty_task_event_runs_an_hour_pause_the_automation_until_it_is_reenabled(api):
    aid = make(api, {"type": "event", "name": "task.created"}, [NOTE])
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=2)
    with time_machine.travel(start, tick=False):
        for i in range(50):
            post(api, "tasks", {"owner": "ops", "title": f"Batch {i}", "body": "Please."})
        assert started(api, aid) == 50
        post(api, "tasks", {"owner": "ops", "title": "Batch 50", "body": "Please."})
        assert started(api, aid) == 50                      # the 51st is not started
        row = automation(api, aid)
        assert row["enabled"] is False and row["paused"] is True
        assert row["paused_reason"] == "Paused: too many runs"

    with time_machine.travel(start + timedelta(minutes=1), tick=False):
        response = api.patch(f"/api/v2/automations/{aid}", json={"enabled": True}, headers=headers())
        assert response.status_code == 200, response.text
        post(api, "tasks", {"owner": "ops", "title": "After re-enable", "body": "Please."})

    assert started(api, aid) == 51
    row = automation(api, aid)
    assert row["enabled"] is True and not row.get("paused", False)


@pytest.mark.parametrize(("event", "field", "value"), [
    ("task.done", "status", "done"),
    ("task.step_changed", "step", "In QA"),
    ("task.declined", "status", "declined"),
    ("task.closed", "status", "closed"),
])
def test_a_task_trigger_chain_runs_one_level_across_all_task_events(api, event, field, value):
    from backend.automations import CHAIN
    if field == "step":
        with api.app.state.store.transaction() as c:
            typ = task_type(c)
            value = next(s["id"] for s in typ["steps"] if s["name"] == "In QA")
            task = H.task_create(c, "human:ana", "Finish me", "Please.", "bot:ops", type=typ["id"], step="Open",
                                 lint=False, deduplicate=False)
    else:
        task = post(api, "tasks", {"owner": "ops", "title": "Finish me", "body": "Please."})
    maker = make(api, {"type": "event", "name": "routine.started"}, [NOTE])
    heard = make(api, {"type": "event", "name": event}, [NOTE])
    token = CHAIN.set((maker,))
    try:
        with api.app.state.store.transaction() as c:
            if event == "task.closed":
                H.task_close(c, "human:ana", task["id"], note="Closed in a routine", mover=True)
            else:
                H.task_update(c, "bot:ops", task["id"], **{field: value}, note="Changed in a routine", mover=True)
        api.app.state.store.automations.drain()
    finally:
        CHAIN.reset(token)
    assert runs(api, heard)["runs"] == []
    assert started(api, heard) == 0
    why = sql(api, "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND note LIKE '%did not run%'",
              (task["id"],))
    assert len(why) == 1 and f"routine Test made this change" in why[0]["note"]
    assert "task-triggered automations run one level deep" in why[0]["note"]
