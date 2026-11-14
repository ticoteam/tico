"""Bots follow the company's Claude and ChatGPT plans (backend/plan_models.py): each bot nobody set by hand runs on its
role's pick for the plans connected now, its tasks on its role's task pick, and both move when a plan comes or goes."""

import json

from backend import bot_settings as BS, model_subscriptions as MS, pipeline_judges, plan_models as PM, providers
from backend import task_models as TM
from backend.store import H
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import add, codex_file, company

ROLES = {"ops": "feature-owner", "cpo": "reviewer", "inbox": "librarian"}
CLAUDE_TASKS = {"model": "claude-opus-5-5", "reasoning_effort": "medium", "runtime": "claude"}
CHATGPT_TASKS = {"model": "gpt-6.1-sol", "reasoning_effort": "high", "runtime": "codex"}


def bots(api):
    """ops a Feature Owner, cpo Reviewer 1, inbox the Librarian (light), and a second reviewer: Reviewer 2."""
    setup(api)
    company(api, enabled=["anthropic"])
    with api.app.state.store.transaction() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM bots ORDER BY id")]
        roles = dict(ROLES)
        spare = next(b for b in ids if b > "cpo" and b not in roles)  # sorts after cpo: the second reviewer
        roles[spare] = "reviewer"
        for bot, template in roles.items():
            BS.update(
                c, bot, {"template": template, "runtime": "claude", "harness": "claude", "model": "claude-opus-5-5"}
            )
    return spare


def on(api, bot):
    with api.app.state.store.read() as c:
        config = BS.read(c, bot)
    return config.get("model"), config.get("reasoning_effort"), config.get("runtime")


def tasks_on(api, bot):
    """The bot's task model as {model, reasoning_effort, runtime}, or None when its tasks run on its own model."""
    with api.app.state.store.read() as c:
        found = TM.of(BS.read(c, bot))
    return {k: found[k] for k in ("model", "reasoning_effort", "runtime")} if found else None


def offered(model):
    return model if PM._offered(model) else None


def plan_moves(api):
    with api.app.state.store.read() as c:
        return [
            json.loads(r[0])
            for r in c.execute("SELECT detail FROM events WHERE action='bot.model_changed' ORDER BY rowid")
            if json.loads(r[0]).get("why") == "plans"
        ]


def test_a_claude_plan_puts_the_bots_on_the_claude_set(api):
    second = bots(api)
    add(api)
    # A Feature Owner and Reviewer 1 run their own turns on Haiku and their tasks on Opus.
    assert on(api, "ops") == ("claude-haiku-5-5", "xhigh", "claude")
    assert on(api, "cpo") == ("claude-haiku-5-5", "xhigh", "claude")
    assert tasks_on(api, "ops") == tasks_on(api, "cpo") == CLAUDE_TASKS
    assert on(api, "inbox") == ("claude-haiku-5-5", "xhigh", "claude")
    assert on(api, second) == ("claude-haiku-5-5", "xhigh", "claude")  # Reviewer 2's tasks on another model
    assert tasks_on(api, "inbox") is None and tasks_on(api, second) is None
    moves = plan_moves(api)
    assert {m["plans"] for m in moves} == {"claude"} and len(moves) == 4
    assert {m["bot"] for m in task_moves(api)} == {"ops", "cpo"}


def test_a_chatgpt_plan_alone_moves_the_bots_off_claude(api):
    second = bots(api)
    add(api, "codex_login", codex_file(), name="ChatGPT")
    light = offered("gpt-6.1-terra") or "gpt-6.1-sol"
    assert on(api, "ops")[0] == light and on(api, "ops")[2] == "codex"
    assert on(api, "cpo")[0] == light and on(api, "cpo")[2] == "codex"
    assert tasks_on(api, "ops") == tasks_on(api, "cpo") == CHATGPT_TASKS
    assert on(api, "inbox")[0] == light and on(api, "inbox")[2] == "codex"
    assert on(api, second)[2] == "codex"
    with api.app.state.store.read() as c:
        assert BS.read(c, "ops")["harness"] == "codex"
        assert "openai" in providers.load(c, api.app.state.store.settings)["enabled"]


def test_a_second_plan_moves_to_both_and_removing_one_goes_back(api):
    second = bots(api)
    add(api)
    chatgpt = add(api, "codex_login", codex_file(), name="ChatGPT")["id"]
    assert on(api, "ops")[0] == "claude-haiku-5-5" and tasks_on(api, "ops") == CLAUDE_TASKS  # the Claude pair
    assert on(api, "cpo")[2] == "claude" and on(api, second) == ("gpt-6.1-sol", "high", "codex")  # two providers
    assert on(api, "inbox")[2] == "codex"
    with api.app.state.store.transaction() as c:
        MS.remove(c, api.app.state.store.settings, _owner(), chatgpt, confirm=True)
    assert on(api, second) == ("claude-haiku-5-5", "xhigh", "claude")
    assert on(api, "inbox") == ("claude-haiku-5-5", "xhigh", "claude")
    assert plan_moves(api)[-1]["plans"] == "claude"


def task_moves(api):
    with api.app.state.store.read() as c:
        return [
            {**json.loads(r[1]), "bot": r[0]}
            for r in c.execute("SELECT target, detail FROM events WHERE action='bot.task_model_changed' ORDER BY rowid")
            if json.loads(r[1]).get("why") == "plans"
        ]


def test_a_plan_the_provider_refuses_counts_as_gone(api):
    bots(api)
    add(api)
    add(api, "codex_login", codex_file(), name="ChatGPT")
    [claude] = [s for s in _subs(api) if s["kind"] == "claude_token"]
    with api.app.state.store.transaction() as c:
        MS._status(c, claude["id"], "needs_reconnect", "refused")
        PM.follow(c, api.app.state.store.settings, H.KEEPER)
    assert on(api, "ops")[2] == "codex" and tasks_on(api, "ops") == CHATGPT_TASKS


def test_a_bot_a_person_set_stays_until_the_change_is_undone(api):
    bots(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO settings_changes(id,bot,field,before,after,actor,created_at) VALUES('x1','ops','model','{}','{}',"
            "'human:ana',?)",
            (H.now(),),
        )
        BS.update(c, "ops", {"model": "claude-sonnet-5-5", "reasoning_effort": "high"})
    add(api, "codex_login", codex_file(), name="ChatGPT")
    assert on(api, "ops") == ("claude-sonnet-5-5", "high", "claude")
    assert tasks_on(api, "ops") is None  # its tasks keep running on the model the person chose
    assert on(api, "cpo")[2] == "codex"
    assert not [m for m in plan_moves(api) if m["from"] == "claude-sonnet-5-5"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE settings_changes SET undone_at=? WHERE id='x1'", (H.now(),))
        PM.follow(c, api.app.state.store.settings, H.KEEPER)
    assert on(api, "ops")[2] == "codex" and tasks_on(api, "ops") == CHATGPT_TASKS


def test_a_company_with_a_plan_already_converges_on_start_and_nothing_moves_twice(api):
    bots(api)
    add(api, "codex_login", codex_file(), name="ChatGPT")
    with api.app.state.store.transaction() as c:  # a bot still on the Claude default from before the table
        BS.update(c, "cpo", {"model": "claude-opus-5-5", "runtime": "claude", "harness": "claude"})
        before = len(plan_moves(api))
        api.app.state.onboarding.complete_company(c)
    assert on(api, "cpo")[2] == "codex" and tasks_on(api, "cpo") == CHATGPT_TASKS
    assert len(plan_moves(api)) == before + 1
    with api.app.state.store.transaction() as c:
        assert PM.follow(c, api.app.state.store.settings, H.KEEPER) == []


def test_no_plan_moves_nothing_and_an_api_key_is_not_a_plan(api):
    bots(api)
    add(api, "anthropic_key", "sk-ant-api03-" + "k" * 24, name="Key")
    assert on(api, "ops") == ("claude-opus-5-5", None, "claude")
    assert tasks_on(api, "ops") is None
    assert plan_moves(api) == [] and task_moves(api) == []


def test_decision_models_stay_on_the_gateway(api):
    bots(api)
    with api.app.state.store.read() as c:
        record = providers.load(c, api.app.state.store.settings)
    add(api, "codex_login", codex_file(), name="ChatGPT")
    with api.app.state.store.read() as c:
        after = providers.load(c, api.app.state.store.settings)
        assert pipeline_judges.judge_model_of(c, None) == "jev"
    assert after.get("check_models") == record.get("check_models")
    assert pipeline_judges.MODELS["jev"] == "tico-decide-low"


def test_a_task_model_a_person_set_stays_and_the_bot_still_follows(api):
    bots(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO settings_changes(id,bot,field,before,after,actor,created_at) VALUES('t1','ops','task_model',"
            "'null','{}','human:ana',?)",
            (H.now(),),
        )
        config = BS.read(c, "ops")
        config[TM.KEY] = {"model": "claude-sonnet-5-5", "reasoning_effort": "high", "compact_at": 300_000}
        BS.write(c, "ops", config)
    add(api)
    assert on(api, "ops") == ("claude-haiku-5-5", "xhigh", "claude")  # its own model follows
    assert tasks_on(api, "ops") == {"model": "claude-sonnet-5-5", "reasoning_effort": "high", "runtime": "claude"}
    assert "ops" not in {m["bot"] for m in task_moves(api)}


def test_a_task_model_keeps_its_context_length_and_leaves_with_the_role(api):
    second = bots(api)
    with api.app.state.store.transaction() as c:
        config = BS.read(c, second)
        config[TM.KEY] = {"compact_at": 300_000}  # a task context length alone, nobody's change on record
        BS.write(c, second, config)
        config = BS.read(c, "ops")
        config[TM.KEY] = {"compact_at": 300_000}
        BS.write(c, "ops", config)
    add(api)
    with api.app.state.store.read() as c:
        assert TM.stored(BS.read(c, "ops"))["compact_at"] == 300_000
        assert TM.stored(BS.read(c, second)) == {
            "harness": "",
            "model": "",
            "reasoning_effort": "",
            "runtime": "",
            "compact_at": 300_000,
        }
    assert tasks_on(api, "ops") == CLAUDE_TASKS
    # A role with no task pick (here Reviewer 2) gives back a task model the plans set, and keeps the length.
    with api.app.state.store.transaction() as c:
        config = BS.read(c, second)
        config[TM.KEY] = {**config[TM.KEY], "model": "claude-opus-5-5", "harness": "claude", "runtime": "claude"}
        BS.write(c, second, config)
        PM.follow(c, api.app.state.store.settings, H.KEEPER)
        assert TM.stored(BS.read(c, second))["model"] == ""
        assert TM.stored(BS.read(c, second))["compact_at"] == 300_000
        assert PM.follow(c, api.app.state.store.settings, H.KEEPER) == []


def test_the_assistant_keeps_the_strong_model_and_no_task_model():
    assert PM.role_of("assistant", False) == PM.ASSISTANT
    assert PM.pick(PM.ASSISTANT, PM.CLAUDE) == ("claude-opus-5-5", "medium")
    assert PM.pick(PM.ASSISTANT, PM.CHATGPT) == ("gpt-6.1-sol", "high")
    assert PM.task_pick(PM.ASSISTANT, PM.CLAUDE) is None
    # Every Owner, Feature Owner and manager is heavy: Haiku for its own turns, Opus for its tasks.
    for template in ("qa-owner", "release-owner", "feature-owner", "engineering-manager", "general-manager"):
        assert PM.role_of(template, False) == PM.HEAVY
    # A Feature Owner pitches, specs, builds and validates in one task: Haiku 5.5 xhigh routes, Opus 5.5 medium works.
    assert PM.pick(PM.role_of("feature-owner", False), PM.CLAUDE) == ("claude-haiku-5-5", "xhigh")
    assert PM.task_pick(PM.role_of("feature-owner", False), PM.CLAUDE) == ("claude-opus-5-5", "medium")
    assert PM.pick(PM.HEAVY, PM.BOTH) == ("claude-haiku-5-5", "xhigh")
    assert PM.task_pick(PM.HEAVY, PM.BOTH) == ("claude-opus-5-5", "medium")
    assert PM.task_pick(PM.LIGHT, PM.CLAUDE) is None and PM.task_pick(PM.REVIEWER_2, PM.CLAUDE) is None


def test_a_model_not_offered_yet_gives_the_sets_pick_on_the_same_tool(monkeypatch):
    monkeypatch.setitem(PM.SETS[PM.LIGHT], PM.CHATGPT, ("gpt-6.1-terra", "medium"))
    monkeypatch.setattr(PM, "_offered", lambda model: model != "gpt-6.1-terra")
    assert PM.pick(PM.LIGHT, PM.CHATGPT) == ("gpt-6.1-sol", "high")


def _owner():
    from backend.auth import Identity

    return Identity("human:ana", "owner", email="ana@acme.example")


def _subs(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT id,kind FROM model_subscriptions")]
