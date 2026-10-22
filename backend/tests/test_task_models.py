"""A bot's task model (backend/task_models.py): its tasks run on another harness, model and effort than its chat, a
check subtask keeps its check model, a task model that cannot run never holds the task, and the run history names
what ran."""
import json

from backend import usage_limits
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401  (api is a fixture)
from backend.tests.test_check_models import company, finish, heartbeat

HAIKU = {"harness": "claude", "model": "claude-haiku-5-5", "effort": "low"}
HAIKU_RAN = {"model": "claude-haiku-5-5", "runtime": "claude", "harness": "claude", "effort": "low", "billing": "api"}


def revision(api, bot="ops"):
    return get(api, f"bots/{bot}/access")["revision"]


def set_task_model(api, choice, bot="ops", expected=200):
    return post(api, f"bots/{bot}/task-model", {"task_model": choice, "expected_revision": revision(api, bot)},
                expected=expected)


def ops_on_sonnet(api):
    """ops on Claude Sonnet at high effort, on a computer signed in to Claude and Grok."""
    company(api, "anthropic", "xai")
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        config.update(runtime="claude", harness="claude", model="claude-sonnet-5-5", reasoning_effort="high")
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps(config),))
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine)
    return machine


def task_turn(api, machine, title="Tidy the changelog"):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Sort the entries."})
    turn = claim(api, machine)
    assert turn["task"]["id"] == task["id"]
    return task, turn


def test_with_no_task_model_a_task_runs_on_the_bots_own_model_and_effort(api):
    machine = ops_on_sonnet(api)
    assert get(api, "bots/ops")["task_model"] is None
    task, turn = task_turn(api, machine)
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-sonnet-5-5", "high")
    finish(api, machine, turn, {"model": "claude-sonnet-5-5", "runtime": "claude", "harness": "claude",
                                "effort": "high"})
    ran = get(api, "tasks/" + task["id"])["runs"][0]["ran_on"]
    assert ran["segments"][0]["effort"] == "high" and ran["task_model"] is None


def test_a_task_runs_on_the_task_model_while_chat_stays_on_the_bots_and_history_names_it(api):
    machine = ops_on_sonnet(api)
    saved = set_task_model(api, HAIKU)
    assert saved["task_model"] == {"harness": "claude", "model": "claude-haiku-5-5", "reasoning_effort": "low",
                                   "runtime": "claude"}
    assert get(api, "bots/ops")["task_model"]["model"] == "claude-haiku-5-5"
    task, turn = task_turn(api, machine)
    config = turn["config"]
    assert (config["runtime"], config["harness"], config["model"], config["reasoning_effort"]) == \
        ("claude", "claude", "claude-haiku-5-5", "low")
    finish(api, machine, turn, HAIKU_RAN)
    runs = get(api, "tasks/" + task["id"])["runs"]
    segment = runs[0]["ran_on"]["segments"][0]
    assert {k: segment[k] for k in ("model", "runtime", "harness", "effort")} == \
        {k: HAIKU_RAN[k] for k in ("model", "runtime", "harness", "effort")}
    assert runs[0]["ran_on"]["task_model"] == {"source": "task", "note": "Ran on the bot's task model "
                                                                         "claude-haiku-5-5 (low)"}
    view = get(api, f"tasks/{task['id']}/run-view")
    assert view["attempts"][0]["ran_on"]["task_model"]["source"] == "task"
    # A person's chat with the bot, outside any task, stays on the bot's own model.
    post(api, "messages", {"to": "ops", "text": "What are you on today?"})
    chat = claim(api, machine)
    assert chat["task"] is None
    assert (chat["config"]["model"], chat["config"]["reasoning_effort"]) == ("claude-sonnet-5-5", "high")


def test_session_bot_keeps_tasks_on_the_bots_model(api):
    """One provider thread for everything: a task cannot switch it to another model."""
    machine = ops_on_sonnet(api)
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps({**config, "session": "bot"}),))
    set_task_model(api, HAIKU)
    _, turn = task_turn(api, machine)
    assert turn["config"]["model"] == "claude-sonnet-5-5"


def test_changing_the_bots_model_leaves_a_set_task_model_and_clearing_returns_to_the_bots(api):
    machine = ops_on_sonnet(api)
    set_task_model(api, HAIKU)
    moved = post(api, "bots/ops/model", {"model": "claude-opus-5-5", "effort": "max",
                                          "expected_revision": revision(api)})
    assert moved["model"] == "claude-opus-5-5"
    shown = get(api, "bots/ops")
    assert shown["model"] == "claude-opus-5-5" and shown["task_model"]["model"] == "claude-haiku-5-5"
    assert shown["task_model"]["reasoning_effort"] == "low"
    # Asking again for what is set is refused as unchanged; a retired or unknown model is refused.
    assert set_task_model(api, HAIKU, expected=409)["error"]["code"] == "unchanged"
    set_task_model(api, {"harness": "claude", "model": "claude-opus-5"}, expected=422)
    set_task_model(api, {"harness": "claude", "model": "nope"}, expected=422)
    set_task_model(api, {"harness": "codex", "model": "claude-haiku-5-5"}, expected=422)
    cleared = set_task_model(api, None)
    assert cleared["task_model"] is None and get(api, "bots/ops")["task_model"] is None
    _, turn = task_turn(api, machine)
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-opus-5-5", "max")
    # Both changes are in the settings history, and the clear can be undone.
    history = [h for h in get(api, "settings/history")["changes"] if h["field"] == "task_model"]
    assert len(history) == 2
    post(api, f"settings/history/{history[0]['id']}/undo", {"expected_revision": revision(api)})
    assert get(api, "bots/ops")["task_model"]["model"] == "claude-haiku-5-5"


def test_a_task_model_that_cannot_run_falls_back_to_the_bots_model_and_says_why(api):
    machine = ops_on_sonnet(api)
    set_task_model(api, {"harness": "grok", "model": "grok-4.6", "effort": "low"})
    heartbeat(api, machine, grok="missing")
    task, turn = task_turn(api, machine)
    assert turn is not None, "never held for the task model"
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-sonnet-5-5", "high")
    finish(api, machine, turn, {"model": "claude-sonnet-5-5", "runtime": "claude", "harness": "claude",
                                "effort": "high"})
    ran = get(api, "tasks/" + task["id"])["runs"][0]["ran_on"]
    assert ran["task_model"]["source"] == "fallback"
    assert ran["task_model"]["note"] == ("Ran on the bot's model claude-sonnet-5-5 (high): task model grok-4.6 (low) "
                                         "could not run (grok is not signed in on Test Mac)")
    with api.app.state.store.read() as c:
        events = c.execute("SELECT old,new,note FROM task_events WHERE task_id=? AND field='task_model'",
                           (task["id"],)).fetchall()
    assert [tuple(e) for e in events] == [("grok-4.6 (low)", "claude-sonnet-5-5 (high)", ran["task_model"]["note"])]
    # Its next turn says the same once more on the run, not again on the task.
    post(api, f"tasks/{task['id']}/comments", {"text": "One more thing: sort by date."})
    again = claim(api, machine)
    assert again["task"]["id"] == task["id"] and again["config"]["model"] == "claude-sonnet-5-5"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_events WHERE task_id=? AND field='task_model'",
                         (task["id"],)).fetchone()[0] == 1
    # Signed in again: the next task runs on the task model.
    finish(api, machine, again)
    heartbeat(api, machine)
    _, back = task_turn(api, machine, "Tidy the README")
    assert (back["config"]["model"], back["config"]["reasoning_effort"]) == ("grok-4.6", "low")


def test_a_retired_or_switched_off_task_model_falls_back_too(api):
    machine = ops_on_sonnet(api)
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        config["task_model"] = {"harness": "claude", "model": "claude-opus-5", "reasoning_effort": "high",
                                "runtime": "claude"}
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps(config),))
    task, turn = task_turn(api, machine)
    assert turn["config"]["model"] == "claude-sonnet-5-5"
    finish(api, machine, turn)
    assert "is retired" in get(api, "tasks/" + task["id"])["runs"][0]["ran_on"]["task_model"]["note"]
    set_task_model(api, {"harness": "grok", "model": "grok-4.6"})
    company(api, "anthropic")
    task, turn = task_turn(api, machine, "Tidy the README")
    assert turn["config"]["model"] == "claude-sonnet-5-5"
    finish(api, machine, turn)
    assert "to be enabled" in get(api, "tasks/" + task["id"])["runs"][0]["ran_on"]["task_model"]["note"]


def test_a_check_subtask_keeps_its_check_model_over_the_task_model_and_says_so(api):
    machine = ops_on_sonnet(api)
    set_task_model(api, HAIKU)
    parent, turn = task_turn(api, machine, "Ship the import fix")
    assert turn["config"]["model"] == "claude-haiku-5-5", "the builder's own task follows the task model set on it"
    sub = post(api, "tasks", {"owner": "ops", "title": "Review PR 700 with another model", "body": "Read it.",
                              "check_model": "grok", "check_role": "review",
                              "relations": [{"task": parent["id"], "kind": "parent"}]}, turn["token"])
    finish(api, machine, turn, HAIKU_RAN)
    check = claim(api, machine)
    assert check["task"]["id"] == sub["id"]
    assert (check["config"]["runtime"], check["config"]["model"]) == ("grok", "grok-4.6")
    finish(api, machine, check, {"model": "grok-4.6", "runtime": "grok", "harness": "grok", "effort": "high"})
    ran = get(api, "tasks/" + sub["id"])["runs"][0]["ran_on"]
    assert ran["segments"][0]["model"] == "grok-4.6"
    assert ran["task_model"] == {"source": "check", "note": "Kept the check model (grok:grok-4.6) over the bot's task "
                                                            "model claude-haiku-5-5 (low)"}


def test_spend_caps_meter_what_the_task_model_ran(api):
    machine = ops_on_sonnet(api)
    set_task_model(api, HAIKU)
    _, turn = task_turn(api, machine)
    finish(api, machine, turn, HAIKU_RAN)
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT model,effort,est_cost_usd FROM turn_usage_segments WHERE turn_id=?",
                         (turn["id"],)).fetchall()
        assert [tuple(r)[:2] for r in rows] == [("claude-haiku-5-5", "low")]
        cost = rows[0]["est_cost_usd"]
        assert cost and usage_limits.spent(c, "ops", "2000-01-01", "2999-01-01", True) == cost


def test_a_limit_on_the_task_model_holds_the_task_models_login_not_the_bots(api):
    """The turn ran on the task model, so its usage limit is that login's, not the bot's own model's."""
    from backend import login_holds
    machine = ops_on_sonnet(api)
    set_task_model(api, {"harness": "grok", "model": "grok-4.6"})
    _, turn = task_turn(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "grok-limit"}, machine["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 0, "limited": True, "text": ""},
         machine["token"])
    rid = machine["runner_id"]
    with api.app.state.store.read() as c:
        held = {rt: login_holds.get(c, rid, login_holds.canonical(c, rid, "", rt), rt) for rt in ("grok", "claude")}
    assert held["grok"] and not held["claude"]
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    assert status["state"] != "limited" and "task model" in status["focus"], "the task model's limit is not the bot's"
    # Its queued work goes on at once, on the bot's own model: the task's own retry falls back, saying why.
    again = claim(api, machine)
    assert again is not None and (again["config"]["model"], again["config"]["reasoning_effort"]) == \
        ("claude-sonnet-5-5", "high")
    with api.app.state.store.read() as c:
        record = json.loads(c.execute("SELECT model_json FROM attempts WHERE id=?", (again["id"],)).fetchone()[0])
    assert record["source"] == "fallback" and record["why"].startswith("grok is at its usage limit until ")


def rust_heartbeat(api, machine, runtimes, isolated=None):
    """A heartbeat in the Rust runner's shape: every harness it knows is listed `idle`, installed or not
    (runner-rs/src/harness_tools.rs), and a runtime it has not installed is `installed: false`."""
    readiness = {"schema_version": 1, "runtimes": runtimes,
                 "harnesses": {name: {"name": name.title(), "runtime": name, "installed": bool(state["installed"]),
                                      "state": "idle"} for name, state in runtimes.items()},
                 "bots": {"ops": {"runtime": "claude", "ready": True}}}
    if isolated is not None:
        readiness["isolated"] = isolated
    post(api, "runners/heartbeat", {"version": "test", "platform": "test",
                                    "capabilities": ["task_lanes_v2", "session_roots_v1"], "readiness": readiness},
         token=machine["token"])


def test_a_task_model_its_computer_has_not_installed_falls_back_rather_than_waiting_on_an_install(api):
    machine = ops_on_sonnet(api)
    rust_heartbeat(api, machine, {"claude": {"installed": True, "authenticated": "ready"},
                                  "grok": {"installed": False, "authenticated": "missing"}})
    saved = set_task_model(api, {"harness": "grok", "model": "grok-4.6", "effort": "low"})
    assert saved["readiness"]["can_run"] is False
    assert saved["readiness"]["problem"] == "grok is not installed on Test Mac"
    assert saved["readiness"]["fix"] == "Install grok on Test Mac, or choose a model it already runs"
    task, turn = task_turn(api, machine)
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("claude-sonnet-5-5", "high")
    with api.app.state.store.read() as c:
        record = json.loads(c.execute("SELECT model_json FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0])
    assert record["source"] == "fallback" and record["why"] == "grok is not installed on Test Mac"


def test_under_the_gateway_preset_a_task_model_not_signed_in_runs_on_the_bots_model_not_the_gateway(api, monkeypatch):
    from backend import models_preset
    from backend.tests.test_models_preset import choose, connected
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        config = {k: v for k, v in config.items() if k != "fallback"}
        config.update(runtime="claude", harness="claude", model="claude-sonnet-5-5", reasoning_effort="high")
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps(config),))
    machine = runner(api)
    assign(api, machine, "ops")
    rust_heartbeat(api, machine, {"claude": {"installed": True, "authenticated": "ready"},
                                  "codex": {"installed": True, "authenticated": "missing"},
                                  "pi": {"installed": True, "authenticated": "missing"}}, isolated=True)
    saved = set_task_model(api, {"harness": "codex", "model": "gpt-6.1-sol", "effort": "high"})
    assert saved["readiness"]["can_run"] is False and "gateway" not in saved["readiness"]
    assert saved["readiness"]["fix"].startswith("Sign in codex on Test Mac in Settings > Computers")
    task, turn = task_turn(api, machine)
    assert (turn["config"]["runtime"], turn["config"]["model"], turn["config"]["reasoning_effort"]) == \
        ("claude", "claude-sonnet-5-5", "high")
    assert "waiting_for_sign_in" not in turn["config"]   # not moved to Tico Mid; the Gateway is only its last fallback
    with api.app.state.store.read() as c:
        record = json.loads(c.execute("SELECT model_json FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0])
    assert record["source"] == "fallback" and record["why"] == "codex is not signed in on Test Mac"


def test_a_task_model_whose_sign_in_cannot_be_checked_runs_rather_than_falling_back_for_good(api):
    """Grok's sign-in can't be checked without starting it, so the runner always reports it `unknown`
    (runner/harnesses/grok.toml `check = "none"`): a "don't know" runs the task model, it is not a reason to fall back."""
    machine = ops_on_sonnet(api)
    rust_heartbeat(api, machine, {"claude": {"installed": True, "authenticated": "ready"},
                                  "grok": {"installed": True, "authenticated": "unknown"}})
    set_task_model(api, {"harness": "grok", "model": "grok-4.6", "effort": "low"})
    _, turn = task_turn(api, machine)
    assert (turn["config"]["model"], turn["config"]["reasoning_effort"]) == ("grok-4.6", "low")
    with api.app.state.store.read() as c:
        record = json.loads(c.execute("SELECT model_json FROM attempts WHERE id=?", (turn["id"],)).fetchone()[0])
    assert record["source"] == "task"


def test_an_ordinary_fallback_usage_limit_is_not_a_task_model_limit(api):
    machine = ops_on_sonnet(api)
    _, turn = task_turn(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "ordinary-fallback"}, machine["token"])
    post(api, f"attempts/{turn['id']}/complete", {
        "outcome": "failed", "last_seq": 0, "text": "", "limited": True,
        "fallback": "codex", "primary_limited": True,
        "usage": {"runtime": "codex", "harness": "codex", "model": "gpt-6.1-sol",
                  "input_tokens": 10, "output_tokens": 1, "billing": "subscription"}}, machine["token"])
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    assert status["state"] == "limited", status
    assert "task model" not in status["focus"], status


def test_a_task_model_limit_without_fallback_usage_keeps_own_model_work_available(api):
    machine = ops_on_sonnet(api)
    set_task_model(api, {"harness": "grok", "model": "grok-4.6"})
    _, turn = task_turn(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "task-fallback"}, machine["token"])
    post(api, f"attempts/{turn['id']}/complete", {
        "outcome": "failed", "last_seq": 0, "text": "", "limited": True,
        "fallback": "codex", "primary_limited": True}, machine["token"])
    with api.app.state.store.read() as c:
        status = H.status(c, "ops")
    assert status["state"] == "idle", status
    assert "task model" in status["focus"], status


def test_a_cheaper_task_model_on_a_bot_that_sends_warns_in_plain_words_and_still_saves_and_runs(api):
    """Spec test 11 (task 7a9b30d4): warn, never refuse; the run says it was below the minimum."""
    machine = ops_on_sonnet(api)
    # Not high-stakes yet: no warning for the same cheaper pick.
    assert set_task_model(api, HAIKU)["warning"] == ""
    set_task_model(api, None)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO approvals(id,kind,requested_by,created) VALUES('ap-1','send','bot:ops',?)", (H.now(),))
    saved = set_task_model(api, HAIKU)
    assert saved["task_model"]["model"] == "claude-haiku-5-5", "it still saves"
    assert saved["warning"] == ("This bot sends messages for you. Haiku 5.5 is a cheaper model than its own Sonnet 5.5, "
                                "and may word things badly. It still saves, and its tasks run on it.")
    task, turn = task_turn(api, machine)
    assert turn["config"]["model"] == "claude-haiku-5-5", "it still runs"
    finish(api, machine, turn, HAIKU_RAN)
    note = get(api, "tasks/" + task["id"])["runs"][0]["ran_on"]["task_model"]["note"]
    assert note.startswith("Ran on the bot's task model claude-haiku-5-5 (low), below the minimum for this bot")
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT note FROM task_events WHERE task_id=? AND field='task_model'",
                                        (task["id"],))] == [note]
    # A pick of the same tier or dearer, or another provider's, is not below the minimum.
    set_task_model(api, None)
    assert set_task_model(api, {"harness": "claude", "model": "claude-opus-5-5", "effort": "high"})["warning"] == ""
    assert set_task_model(api, {"harness": "grok", "model": "grok-4.6", "effort": "low"})["warning"] == ""
