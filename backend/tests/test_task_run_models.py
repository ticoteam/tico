"""A task's own model (backend/task_run_models.py): one task runs on the model set on it while the bot's other
tasks keep theirs, a check subtask keeps its check model over it, a model that cannot run falls back and says so,
whoever may change the bot's model may set a task's and a bot never does, change and clear are in the task's
history, a task handed to another owner loses its model, and a routine's Bot turn step stamps its model on the task
its run opens, which a routine nobody with that right has approved neither turns on nor runs by hand."""
import contextlib
import json

import pytest

from backend import strategy_acts, providers, task_run_models
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, headers, post, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_check_models import finish, heartbeat
from backend.tests.test_task_models import ops_on_sonnet, set_task_model, task_turn

HAIKU = {"model": "claude-haiku-5-5", "effort": "low", "harness": "claude"}
HAIKU_RAN = {"model": "claude-haiku-5-5", "runtime": "claude", "harness": "claude", "effort": "low", "billing": "api"}
SONNET_RAN = {"model": "claude-sonnet-5-5", "runtime": "claude", "harness": "claude", "effort": "high",
              "billing": "api"}
GROK = {"model": "grok-4.6", "effort": "low", "harness": "grok"}


def filed(api, title, owner="ops"):
    return post(api, "tasks", {"owner": owner, "title": title, "body": "Do the work."})


def own_model(api, task_id, body, token="ana-test", expected=200):
    return post(api, f"tasks/{task_id}/model", body, token=token, expected=expected)


def test_a_task_runs_on_its_own_model_and_the_bots_other_tasks_keep_theirs(api):
    machine = ops_on_sonnet(api)
    mine = filed(api, "Tidy the changelog")
    own_model(api, mine["id"], HAIKU)
    turn = claim(api, machine)
    assert turn["task"]["id"] == mine["id"]
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-haiku-5-5", "low")
    finish(api, machine, turn, HAIKU_RAN)
    ran = get(api, "tasks/" + mine["id"])["runs"][0]["ran_on"]
    assert ran["segments"][0]["model"] == "claude-haiku-5-5"
    assert ran["task_model"]["source"] == "task"
    # Only this task changed: the bot's task model is still unset, and its next task runs on its own model.
    assert get(api, "bots/ops")["task_model"] is None
    _, other = task_turn(api, machine, "Tidy the README")
    assert other["config"]["model"] == "claude-sonnet-5-5"


def test_a_tasks_own_model_that_cannot_run_falls_back_to_the_bots_model_and_says_so(api):
    machine = ops_on_sonnet(api)
    mine = filed(api, "Index the docs")
    own_model(api, mine["id"], GROK)
    heartbeat(api, machine, grok="missing")
    turn = claim(api, machine)
    assert turn is not None, "never held for the task's own model"
    assert turn["config"]["model"] == "claude-sonnet-5-5"
    finish(api, machine, turn, SONNET_RAN)
    ran = get(api, "tasks/" + mine["id"])["runs"][0]["ran_on"]
    assert ran["segments"][0]["model"] == "claude-sonnet-5-5"
    assert ran["task_model"]["source"] == "fallback"
    assert "grok-4.6" in ran["task_model"]["note"] and "could not run" in ran["task_model"]["note"]


def test_a_check_subtask_keeps_its_check_model_over_the_tasks_own_model(api):
    machine = ops_on_sonnet(api)
    parent = filed(api, "Ship the import fix")
    own_model(api, parent["id"], HAIKU)
    turn = claim(api, machine)
    assert turn["config"]["model"] == "claude-haiku-5-5"
    sub = post(api, "tasks", {"owner": "ops", "title": "Review PR 700 with another model", "body": "Read it.",
                              "check_model": "grok", "check_role": "review",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    own_model(api, sub["id"], HAIKU)
    finish(api, machine, turn, HAIKU_RAN)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    assert (check["config"]["runtime"], check["config"]["model"]) == ("grok", "grok-4.6")
    finish(api, machine, check, {"model": "grok-4.6", "runtime": "grok", "harness": "grok", "effort": "high"})
    ran = get(api, "tasks/" + sub["id"])["runs"][0]["ran_on"]
    assert ran["segments"][0]["model"] == "grok-4.6" and ran["task_model"]["source"] == "check"


def test_only_whoever_may_change_the_bots_model_may_set_a_tasks_and_a_bot_never_does(api):
    machine = ops_on_sonnet(api)
    mine = filed(api, "Tidy the changelog")
    own_model(api, mine["id"], HAIKU, token="cara-test", expected=403)
    turn = claim(api, machine)
    own_model(api, mine["id"], HAIKU, token=turn["token"], expected=403)
    assert get(api, "tasks/" + mine["id"])["run_model"] is None
    own_model(api, mine["id"], HAIKU, token="ben-test")
    assert get(api, "tasks/" + mine["id"])["run_model"]["model"] == "claude-haiku-5-5"


def test_a_tasks_model_changes_and_clears_and_its_history_says_so(api):
    ops_on_sonnet(api)
    mine = filed(api, "Tidy the changelog")
    assert get(api, "tasks/" + mine["id"])["run_model"] is None
    first = own_model(api, mine["id"], HAIKU)
    assert first["run_model"]["model"] == "claude-haiku-5-5" and first["run_model"]["reasoning_effort"] == "low"
    assert get(api, "tasks/" + mine["id"])["run_model"]["harness"] == "claude"
    own_model(api, mine["id"], HAIKU, expected=409)
    cleared = own_model(api, mine["id"], {"model": None})
    assert cleared["run_model"] is None and get(api, "tasks/" + mine["id"])["run_model"] is None
    assert get(api, "bots/ops")["task_model"] is None
    with api.app.state.store.read() as c:
        events = c.execute("SELECT old,new FROM task_events WHERE task_id=? AND field='run_model' ORDER BY rowid",
                           (mine["id"],)).fetchall()
    assert [tuple(e) for e in events] == [("", "claude-haiku-5-5 (low)"), ("claude-haiku-5-5 (low)", "")]


def test_a_model_must_be_a_supported_one_taking_the_effort_and_only_a_bots_task_has_one(api):
    ops_on_sonnet(api)
    mine = filed(api, "Tidy the changelog")
    own_model(api, mine["id"], {"model": "no-such-model"}, expected=422)
    own_model(api, mine["id"], {"model": "claude-haiku-5-5", "effort": "extreme"}, expected=422)
    person = post(api, "tasks", {"owner": "ana", "title": "Call the vendor", "body": "Ring them back."})
    own_model(api, person["id"], HAIKU, expected=422)


def test_a_routine_step_stamps_its_model_on_the_task_its_run_opens(api):
    machine = ops_on_sonnet(api)
    trigger = {"type": "event", "name": "x.y"}
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **HAIKU}
    post(api, "automations", {"name": "Haiku audit", "owner": "bot:ops", "trigger": trigger,
                              "steps": [{**step, "effort": "extreme"}]}, expected=422)
    aid = post(api, "automations", {"name": "Haiku audit", "owner": "bot:ops", "trigger": trigger,
                                    "steps": [step]})["id"]
    post(api, f"automations/{aid}/run", {})
    turn = claim(api, machine)
    task_id = turn["task"]["id"]
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-haiku-5-5", "low")
    finish(api, machine, turn, HAIKU_RAN)
    assert get(api, "tasks/" + task_id)["run_model"]["model"] == "claude-haiku-5-5"
    with api.app.state.store.read() as c:
        notes = c.execute("SELECT note FROM task_events WHERE task_id=? AND field='run_model'", (task_id,)).fetchall()
    assert [n[0] for n in notes] == ["Set by routine step audit"]
    assert get(api, "bots/ops")["task_model"] is None


def test_a_routine_step_model_from_someone_who_may_not_set_it_waits_for_a_person_who_may(api):
    ops_on_sonnet(api)
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **HAIKU}
    flow = {"name": "Haiku audit", "owner": "human:cara", "trigger": {"type": "event", "name": "x.y"}, "steps": [step]}
    # Cara may make her own routine but not choose ops' model: it is saved off, its first version pending.
    draft = post(api, "automations", flow, token="cara-test")
    assert draft["pending"] == 1
    # Turning on her own draft would set the model all the same, so she may not; Ana, who manages ops, may.
    post(api, f"automations/{draft['id']}/versions/1/restore", {}, token="cara-test", expected=403)
    assert get(api, f"automations/{draft['id']}")["enabled"] is False
    on = post(api, f"automations/{draft['id']}/versions/1/restore", {})
    assert on["enabled"] is True
    # A later edit of hers that changes the step's model is proposed, never saved over the live one.
    changed = {"steps": [{**step, "effort": "high"}]}
    proposed = api.patch(f"/api/v2/automations/{draft['id']}", json=changed, headers=headers("cara-test"))
    assert proposed.status_code == 200 and proposed.json()["pending"], proposed.text
    assert get(api, f"automations/{draft['id']}")["steps"][0]["effort"] == "low"


def patch(api, path, body, token="ana-test", expected=200):
    r = api.patch("/api/v2/" + path, json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def cara_routine(api):
    """Cara's routine whose step names ops' model: saved off, its only version pending, since she may not set it."""
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **HAIKU}
    draft = post(api, "automations", {"name": "Haiku audit", "owner": "human:cara",
                                      "trigger": {"type": "event", "name": "x.y"}, "steps": [step]},
                 token="cara-test")
    assert draft["pending"] == 1 and get(api, f"automations/{draft['id']}")["enabled"] is False
    return draft["id"]


def test_a_routine_step_model_is_not_turned_on_by_someone_who_may_not_set_it_until_one_who_may_approves(api):
    ops_on_sonnet(api)
    aid = cara_routine(api)
    # Switching the draft on would set the model all the same: Cara may not, and it stays off.
    patch(api, f"automations/{aid}", {"enabled": True}, token="cara-test", expected=403)
    assert get(api, f"automations/{aid}")["enabled"] is False
    # Ana manages ops: turning it on approves the version it runs, so nothing is left pending.
    assert patch(api, f"automations/{aid}", {"enabled": True})["enabled"] is True
    assert get(api, f"automations/{aid}/versions?status=pending")["versions"] == []
    # Approved, Cara may switch it off and on again: the step's model is no longer hers to approve.
    assert patch(api, f"automations/{aid}", {"enabled": False}, token="cara-test")["enabled"] is False
    assert patch(api, f"automations/{aid}", {"enabled": True}, token="cara-test")["enabled"] is True


def test_a_routine_step_model_is_not_run_by_hand_by_someone_who_may_not_set_it_before_it_is_approved(api):
    ops_on_sonnet(api)
    aid = cara_routine(api)
    post(api, f"automations/{aid}/run", {}, token="cara-test", expected=403)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 0
    # Ana may run it by hand; that is one run, and the routine stays off until someone turns it on.
    post(api, f"automations/{aid}/run", {})
    assert get(api, f"automations/{aid}")["enabled"] is False


@contextlib.contextmanager
def acting_as_strategy_bot():
    """Requests made inside it name the Strategy Bot as the bot acting (`H.VIA`, which `strategy_acts.is_strategy_bot`
    reads)."""
    token = H.VIA.set(strategy_acts.STRATEGY_BOT)
    try:
        yield
    finally:
        H.VIA.reset(token)


def test_the_strategy_bot_acting_for_a_person_who_manages_the_bot_cannot_approve_or_run_its_model_in_a_routine(api):
    ops_on_sonnet(api)
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **HAIKU}
    flow = {"name": "Haiku audit", "owner": "human:ben", "trigger": {"type": "event", "name": "x.y"}, "steps": [step]}
    with acting_as_strategy_bot():
        # Ben manages ops, but the Strategy Bot never chooses a bot's model: the routine is saved off, its version
        # pending.
        draft = post(api, "automations", flow, token="ben-test")
        aid = draft["id"]
        assert draft["pending"] == 1
        patch(api, f"automations/{aid}", {"enabled": True}, token="ben-test", expected=403)
        post(api, f"automations/{aid}/versions/1/restore", {}, token="ben-test", expected=403)
        post(api, f"automations/{aid}/run", {}, token="ben-test", expected=403)
    assert get(api, f"automations/{aid}")["enabled"] is False
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 0
    # Ben, acting himself, approves the model by turning the routine on; then he may run it by hand.
    assert patch(api, f"automations/{aid}", {"enabled": True}, token="ben-test")["enabled"] is True
    post(api, f"automations/{aid}/run", {}, token="ben-test")


def test_the_strategy_bot_acting_for_a_person_who_manages_the_bot_cannot_set_a_tasks_model_either(api):
    ops_on_sonnet(api)
    mine = filed(api, "Tidy the changelog")
    with acting_as_strategy_bot():
        own_model(api, mine["id"], HAIKU, token="ben-test", expected=409)
    assert get(api, "tasks/" + mine["id"])["run_model"] is None


def finance_on_the_computer(api, machine):
    """Finance assigned to the computer and reported ready, so a turn handed to it can be claimed there."""
    assign(api, machine, "finance")
    readiness = {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": "ready"}},
                 "bots": {"finance": {"runtime": "claude", "ready": True}}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                                    "capabilities": ["task_lanes_v2", "session_roots_v1"], "readiness": readiness},
         token=machine["token"])


def test_a_task_handed_to_another_bot_loses_the_model_its_old_bot_was_given(api):
    machine = ops_on_sonnet(api)
    finance_on_the_computer(api, machine)
    mine = filed(api, "Tidy the changelog")
    own_model(api, mine["id"], HAIKU)
    post(api, "tasks/" + mine["id"], {"version": mine["version"], "owner": "finance"})
    assert get(api, "tasks/" + mine["id"])["run_model"] is None
    with api.app.state.store.read() as c:
        events = c.execute("SELECT old,new FROM task_events WHERE task_id=? AND field='run_model' ORDER BY rowid",
                           (mine["id"],)).fetchall()
    assert [tuple(e) for e in events] == [("", "claude-haiku-5-5 (low)"), ("claude-haiku-5-5 (low)", "")]
    turn = claim(api, machine, "finance")
    assert turn["task"]["id"] == mine["id"] and turn["config"]["model"] != "claude-haiku-5-5"


def test_a_bulk_handoff_of_a_task_loses_its_model_too(api):
    machine = ops_on_sonnet(api)
    finance_on_the_computer(api, machine)
    mine = filed(api, "Tidy the changelog")
    own_model(api, mine["id"], HAIKU)
    out = post(api, "task-bulk", {"filter": {"ids": [mine["id"]]}, "action": "owner", "owner": "finance"})
    assert out["changed"] == 1
    assert get(api, "tasks/" + mine["id"])["run_model"] is None
    turn = claim(api, machine, "finance")
    assert turn["task"]["id"] == mine["id"] and turn["config"]["model"] != "claude-haiku-5-5"


def test_a_temporary_assignment_retired_with_its_task_takes_the_model_off_it(api):
    from backend.shared_bots import retire_assignments
    ops_on_sonnet(api)
    mine = filed(api, "Tidy the changelog")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state,created_at) VALUES('ops-a1','ops-a1','Ops A1','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES('ops-a1',?,'ana')",
                  (json.dumps({"assignment_branch": True, "shared_from": "ops"}),))
        c.execute("UPDATE tasks SET owner_id='bot:ops-a1',status='doing' WHERE id=?", (mine["id"],))
        haiku = task_run_models.stamped("claude-haiku-5-5", "low", "claude")
        task_run_models.set_choice(c, "human:ana", mine["id"], haiku)
        assert task_run_models.of(c, mine["id"]) is not None
        # Ops is not active, so the task goes back to it directly rather than through a handoff.
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
        retire_assignments(c)
        assert H.task(c, mine["id"])["owner_id"] == "bot:ops"
        assert task_run_models.of(c, mine["id"]) is None


def test_a_tasks_own_model_falling_back_to_the_bots_task_model_starts_a_side_session(api):
    machine = ops_on_sonnet(api)
    set_task_model(api, HAIKU)
    mine = filed(api, "Index the docs")
    own_model(api, mine["id"], GROK)
    heartbeat(api, machine, grok="missing")
    turn = claim(api, machine)
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-haiku-5-5", "low")
    # The runner resumes its thread for a runtime unless the turn is marked a fallback: this one ran on another
    # model than the task asked for, so it starts a side session from the task's checkpoint.
    assert turn["config"]["task_model_fallback"] is True
    finish(api, machine, turn, HAIKU_RAN)
    ran = get(api, "tasks/" + mine["id"])["runs"][0]["ran_on"]
    assert ran["task_model"]["source"] == "fallback" and "grok-4.6" in ran["task_model"]["note"]


def test_a_routine_step_without_an_effort_runs_at_its_models_default_effort(api):
    machine = ops_on_sonnet(api)
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.",
            "model": "claude-haiku-5-5", "harness": "claude"}
    aid = post(api, "automations", {"name": "Haiku audit", "owner": "bot:ops",
                                    "trigger": {"type": "event", "name": "x.y"}, "steps": [step]})["id"]
    post(api, f"automations/{aid}/run", {})
    turn = claim(api, machine)
    # Haiku's own default effort, not the harness's: the claimed turn names it, and so does the task.
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-haiku-5-5", "medium")
    finish(api, machine, turn, {"model": "claude-haiku-5-5", "runtime": "claude", "harness": "claude",
                                "effort": "medium", "billing": "api"})
    assert get(api, "tasks/" + turn["task"]["id"])["run_model"]["reasoning_effort"] == "medium"


@pytest.mark.parametrize("gone", ["retired", "removed"])
def test_a_routine_whose_stamped_model_has_since_gone_still_pauses_and_renames(api, monkeypatch, gone):
    ops_on_sonnet(api)
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **HAIKU}
    aid = post(api, "automations", {"name": "Haiku audit", "owner": "bot:ops",
                                    "trigger": {"type": "event", "name": "x.y"}, "steps": [step]})["id"]
    if gone == "retired":
        monkeypatch.setitem(providers.MODEL_BY_ID["claude-haiku-5-5"], "deprecated", True)
    else:
        monkeypatch.delitem(providers.MODEL_BY_ID, "claude-haiku-5-5")
    # The model is kept as it was saved, so turning the routine off and renaming it saves; nothing about it changed.
    assert patch(api, f"automations/{aid}", {"enabled": False})["enabled"] is False
    assert patch(api, f"automations/{aid}", {"name": "Audit"})["name"] == "Audit"
    assert get(api, f"automations/{aid}")["steps"][0]["model"] == "claude-haiku-5-5"


def test_a_routine_step_changed_to_a_retired_model_is_still_refused(api, monkeypatch):
    ops_on_sonnet(api)
    step = {"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **HAIKU}
    aid = post(api, "automations", {"name": "Haiku audit", "owner": "bot:ops",
                                    "trigger": {"type": "event", "name": "x.y"}, "steps": [step]})["id"]
    monkeypatch.setitem(providers.MODEL_BY_ID["claude-sonnet-5-5"], "deprecated", True)
    patch(api, f"automations/{aid}", {"steps": [{**step, "model": "claude-sonnet-5-5", "effort": "high"}]},
          expected=422)
    assert get(api, f"automations/{aid}")["steps"][0]["model"] == "claude-haiku-5-5"


def branch_id(api, slug):
    """A branch's id: its actor (`bot:<id>`), its readiness report and its claims use the id, not the slug."""
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


def branch_computer_ready(api, computer, bot):
    """A computer of a branch's person, signed in to Claude, with the branch's bot ready on it."""
    post(api, "runners/heartbeat", {
        "version": "test", "platform": "test", "capabilities": ["task_lanes_v2", "session_roots_v1"],
        "readiness": {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": "ready"}},
                      "bots": {bot: {"runtime": "claude", "ready": True}}}}, token=computer["token"])


def share_ops_with_branches(api):
    post(api, "bots/ops/definition", {"shared": True, "expected_revision": get(api, "bots/ops")["revision"]})


def test_a_branch_task_takes_its_own_model_from_whoever_may_change_the_originals(api):
    ops_on_sonnet(api)
    share_ops_with_branches(api)
    # Ben manages ops without operating it, so he may hold a branch of it; Cara manages neither.
    hers = runner(api, "ben", "Ben's Mac")
    post(api, "bots/ops/copies", {"runner_id": hers["runner_id"]}, token="ben-test")
    post(api, "bots/ops/copies", {"runner_id": runner(api, "cara", "Cara's Mac")["runner_id"]}, token="cara-test")
    mine = post(api, "tasks", {"owner": "ops", "title": "Tidy the changelog", "body": "Sort the entries."}, "ben-test")
    assert mine["owner_id"] == H.bot_actor(branch_id(api, "ops-ben"))
    # Ben manages ops, so his branch's task takes the model; the branch itself keeps following ops.
    own_model(api, mine["id"], HAIKU, token="ben-test")
    assert get(api, "tasks/" + mine["id"], "ben-test")["run_model"]["model"] == "claude-haiku-5-5"
    assert get(api, "bots/ops-ben", "ben-test")["task_model"] is None
    # Cara's branch is not ops' manager's: her task's model is refused, and it stays unset.
    theirs = post(api, "tasks", {"owner": "ops", "title": "Tidy the README", "body": "Sort it."}, "cara-test")
    assert theirs["owner_id"] == H.bot_actor(branch_id(api, "ops-cara"))
    own_model(api, theirs["id"], HAIKU, token="cara-test", expected=403)
    assert get(api, "tasks/" + theirs["id"], "cara-test")["run_model"] is None
    # The branch's computer runs the task on the task's model.
    branch_computer_ready(api, hers, branch_id(api, "ops-ben"))
    turn = claim(api, hers, branch_id(api, "ops-ben"))
    assert turn["task"]["id"] == mine["id"]
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-haiku-5-5", "low")
    finish(api, hers, turn, HAIKU_RAN)
    assert get(api, "tasks/" + mine["id"], "ben-test")["runs"][0]["ran_on"]["segments"][0]["model"] == \
        "claude-haiku-5-5"


def test_a_task_says_whether_its_reader_may_set_its_model_as_the_post_decides(api):
    from backend.tests.test_api import as_member
    ops_on_sonnet(api)
    share_ops_with_branches(api)
    post(api, "bots/ops/copies", {"runner_id": runner(api, "ben", "Ben's Mac")["runner_id"]}, token="ben-test")
    post(api, "bots/ops/copies", {"runner_id": runner(api, "cara", "Cara's Mac")["runner_id"]}, token="cara-test")
    theirs = post(api, "tasks", {"owner": "ops", "title": "Tidy the README", "body": "Sort it."}, "cara-test")
    assert theirs["owner_id"] == H.bot_actor(branch_id(api, "ops-cara"))
    # Cara operates her branch but manages neither it nor ops, whose model it follows: no form, and no save.
    assert get(api, "tasks/" + theirs["id"], "cara-test")["can_set_run_model"] is False
    own_model(api, theirs["id"], HAIKU, token="cara-test", expected=403)
    # Ben is an Admin here, so he manages every branch. As a member he manages this one only through ops, and he is not
    # yet one of ops' owners: the answer is no, and the save is refused.
    as_member(api, "ben@acme.example")
    assert get(api, "tasks/" + theirs["id"], "ben-test")["can_set_run_model"] is False
    own_model(api, theirs["id"], HAIKU, token="ben-test", expected=403)
    # Named an owner of ops, he may set the model of its branch's tasks, though he does not manage the branch itself.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET bot_owners=? WHERE bot='ops'", (json.dumps(["ana", "ben"]),))
    assert get(api, "tasks/" + theirs["id"], "ben-test")["can_set_run_model"] is True
    own_model(api, theirs["id"], HAIKU, token="ben-test")
    assert get(api, "tasks/" + theirs["id"], "ben-test")["run_model"]["model"] == "claude-haiku-5-5"


def test_a_routine_step_on_a_branch_needs_the_originals_managers_to_set_its_model(api):
    ops_on_sonnet(api)
    share_ops_with_branches(api)
    post(api, "bots/ops/copies", {"runner_id": runner(api, "cara", "Cara's Mac")["runner_id"]}, token="cara-test")
    step = {"id": "audit", "type": "bot_turn", "bot": "ops-cara", "prompt": "Run the audit.", **HAIKU}
    # Cara may not set ops' model, branch or not: her routine waits for a manager of ops, as one on ops does.
    draft = post(api, "automations", {"name": "Haiku audit", "owner": "human:cara",
                                      "trigger": {"type": "event", "name": "x.y"}, "steps": [step]},
                 token="cara-test")
    assert draft["pending"] == 1 and get(api, f"automations/{draft['id']}")["enabled"] is False
