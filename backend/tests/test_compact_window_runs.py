"""Where a conversation compacts, for what is not the bot's own model (task #237, part 3 of #230): the fallback, a
task's own run and a routine's Bot turn step each keep their own size from the same twelve, or none ("Same as bot").
The fallback's is lowered to fit the fallback model's window; a run's and a step's are the turn's, and the bot's own
window stays as it was."""

from backend import bot_settings as BS
from backend.tests.helpers.api import claim, get, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.task_models import ops_on_sonnet, revision

SIZES = [100_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000, 600_000, 700_000, 800_000, 900_000, 1_000_000]
GROK = {"harness": "grok", "model": "grok-4.6", "effort": "low"}  # a 500k window


def set_fallback(api, fallback, expected=200):
    return post(api, "bots/ops/fallback", {"fallback": fallback, "expected_revision": revision(api)}, expected=expected)


def saved_fallback(api):
    with api.app.state.store.read() as c:
        return BS.read(c, "ops").get("fallback") or {}


def set_compact_at(api, size):
    post(api, "bots/ops/compact-at", {"compact_at": size, "expected_revision": revision(api)})


def chat_turn(api, machine):
    post(api, "messages", {"to": "ops", "text": "What are you on today?"})
    return claim(api, machine)


def filed(api, **run):
    return post(api, "tasks", {"owner": "ops", "title": "Tidy the changelog", "body": "Sort the entries.", **run})


def refused_naming_the_sizes(answer):
    said = str(answer)
    assert all(str(size) in said for size in SIZES), said


# ------------------------------------------------------------------------------------------------------- fallback


def test_a_fallback_keeps_its_own_window_and_a_size_off_the_list_is_refused(api):
    ops_on_sonnet(api)
    assert set_fallback(api, {**GROK, "compact_at": 300_000})["fallback"]["compact_at"] == 300_000
    assert saved_fallback(api)["compact_at"] == 300_000
    refused_naming_the_sizes(set_fallback(api, {**GROK, "compact_at": 350_000}, expected=422))
    assert saved_fallback(api)["compact_at"] == 300_000
    # Changing only its window is a change, and leaving it out is "Same as bot" again.
    set_fallback(api, {**GROK, "compact_at": 200_000})
    assert saved_fallback(api)["compact_at"] == 200_000
    set_fallback(api, GROK)
    assert saved_fallback(api).get("compact_at") is None


def test_a_fallback_on_same_as_bot_takes_the_bots_window_lowered_to_fit_its_model(api):
    machine = ops_on_sonnet(api)
    set_compact_at(api, 600_000)
    set_fallback(api, GROK)
    turn = chat_turn(api, machine)
    assert turn["config"]["compact_at"] == 600_000
    # grok 4.6 holds 500k: the largest size that fits it.
    assert turn["config"]["fallback"]["compact_at"] == 500_000


def test_a_fallbacks_own_window_is_what_its_turn_compacts_at(api):
    machine = ops_on_sonnet(api)
    set_fallback(api, {**GROK, "compact_at": 150_000})
    turn = chat_turn(api, machine)
    assert turn["config"]["fallback"]["compact_at"] == 150_000
    assert turn["config"]["compact_at"] == 250_000  # the bot's own, Sonnet's default, unchanged


# ------------------------------------------------------------------------------------------------- a task's run


def test_a_new_tasks_run_window_is_what_its_turns_use_and_the_bots_own_stays(api):
    machine = ops_on_sonnet(api)
    task = filed(api, model="claude-haiku-5-5", effort="low", compact_at=400_000)
    turn = claim(api, machine)
    assert turn["task"]["id"] == task["id"]
    assert (turn["config"]["model"], turn["config"]["compact_at"]) == ("claude-haiku-5-5", 400_000)
    assert get(api, "tasks/" + task["id"])["run_model"]["compact_at"] == 400_000
    assert get(api, "bots/ops")["compact_at"] == 250_000


def test_a_run_window_alone_keeps_the_bots_model(api):
    machine = ops_on_sonnet(api)
    task = filed(api, compact_at=150_000)
    turn = claim(api, machine)
    assert (turn["config"]["model"], turn["config"]["compact_at"]) == ("claude-sonnet-5-5", 150_000)
    # Not a switch to another model: the task's session is resumed, not started over (spec AC-15).
    assert not turn["config"].get("task_model_fallback")
    run = get(api, "tasks/" + task["id"])["run_model"]
    assert (run["model"], run["compact_at"]) == ("", 150_000)


def test_a_tasks_run_window_changes_on_its_own_and_clears_to_same_as_bot(api):
    machine = ops_on_sonnet(api)
    task = filed(api, model="claude-haiku-5-5", effort="low")
    assert get(api, "tasks/" + task["id"])["run_model"]["compact_at"] is None
    post(api, f"tasks/{task['id']}/model", {"compact_at": 700_000})
    run = get(api, "tasks/" + task["id"])["run_model"]
    assert (run["model"], run["compact_at"]) == ("claude-haiku-5-5", 700_000)
    refused_naming_the_sizes(post(api, f"tasks/{task['id']}/model", {"compact_at": 350_000}, expected=422))
    post(api, f"tasks/{task['id']}/model", {"compact_at": None})
    assert get(api, "tasks/" + task["id"])["run_model"]["compact_at"] is None
    # Same as bot: Haiku's default once nothing is saved on the task or the bot.
    assert claim(api, machine)["config"]["compact_at"] == 150_000
    # Back to the bot's model, keeping a window of its own; then that window cleared too, which leaves nothing.
    post(api, f"tasks/{task['id']}/model", {"model": None, "compact_at": 300_000})
    run = get(api, "tasks/" + task["id"])["run_model"]
    assert (run["model"], run["compact_at"]) == ("", 300_000)
    post(api, f"tasks/{task['id']}/model", {"compact_at": None})
    assert get(api, "tasks/" + task["id"])["run_model"] is None


def test_a_size_off_the_list_is_refused_when_a_task_is_filed(api):
    ops_on_sonnet(api)
    refused_naming_the_sizes(
        post(api, "tasks", {"owner": "ops", "title": "Tidy", "body": "x", "compact_at": 350_000}, expected=422)
    )


# ------------------------------------------------------------------------------------------------ a routine step


def routine(api, **step):
    body = {
        "name": "Audit",
        "owner": "bot:ops",
        "trigger": {"type": "event", "name": "x.y"},
        "steps": [{"id": "audit", "type": "bot_turn", "bot": "ops", "prompt": "Run the audit.", **step}],
    }
    return post(api, "automations", body, expected=200 if step.get("compact_at") in SIZES + [None] else 422)


def test_a_routine_steps_window_is_what_its_turn_uses(api):
    machine = ops_on_sonnet(api)
    aid = routine(api, model="claude-haiku-5-5", harness="claude", compact_at=150_000)["id"]
    post(api, f"automations/{aid}/run", {})
    turn = claim(api, machine)
    assert (turn["config"]["model"], turn["config"]["compact_at"]) == ("claude-haiku-5-5", 150_000)
    assert get(api, "tasks/" + turn["task"]["id"])["run_model"]["compact_at"] == 150_000
    assert get(api, "bots/ops")["compact_at"] == 250_000


def test_a_routine_step_may_set_only_its_window(api):
    machine = ops_on_sonnet(api)
    aid = routine(api, compact_at=300_000)["id"]
    post(api, f"automations/{aid}/run", {})
    turn = claim(api, machine)
    assert (turn["config"]["model"], turn["config"]["compact_at"]) == ("claude-sonnet-5-5", 300_000)


def test_a_routine_step_size_off_the_list_is_refused(api):
    ops_on_sonnet(api)
    refused_naming_the_sizes(routine(api, compact_at=350_000))


def test_the_bots_window_is_kept_for_a_task_without_one(api):
    machine = ops_on_sonnet(api)
    with api.app.state.store.transaction() as c:
        config = BS.read(c, "ops")
        config["compact_at"] = 800_000
        BS.write(c, "ops", config)
    filed(api, model="claude-haiku-5-5", effort="low")
    assert claim(api, machine)["config"]["compact_at"] == 800_000
