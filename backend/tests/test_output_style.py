"""A bot's Claude Code output style and Codex verbosity (the More fold of the model picker): saved with the bot's other
settings on any harness, shown on its row, sent with every turn for the runner to apply on its own harness, Default
clearing it, and settable by an agent and on the command line."""

from typing import get_args

from backend import bot_settings as BS
from backend import harnesses
from backend import models as M
from backend.tests.helpers.api import claim, get, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.mcp import call
from backend.tests.helpers.task_models import ops_on_sonnet, revision


def saved(api, key, bot="ops"):
    with api.app.state.store.transaction() as c:
        return BS.read(c, bot).get(key, "unsaved")


def test_an_output_style_saves_shows_on_the_row_rides_the_turn_and_default_clears_it(api):
    machine = ops_on_sonnet(api)
    before = revision(api)
    out = post(api, "bots/ops/output-style", {"output_style": "Concise", "expected_revision": before})
    assert (out["output_style"], out["revision"]) == ("Concise", before + 1)
    assert get(api, "bots/ops")["output_style"] == "Concise"
    post(api, "messages", {"to": "ops", "text": "What are you on today?"})
    assert claim(api, machine)["config"]["output_style"] == "Concise"
    post(api, "bots/ops/output-style", {"output_style": "Default", "expected_revision": revision(api)})
    assert saved(api, "output_style") == "unsaved"
    assert get(api, "bots/ops")["output_style"] is None


def test_a_verbosity_saves_on_a_claude_bot_too_and_null_clears_it(api):
    ops_on_sonnet(api)
    post(api, "bots/ops/verbosity", {"verbosity": "low", "expected_revision": revision(api)})
    assert get(api, "bots/ops")["verbosity"] == "low"
    post(api, "bots/ops/verbosity", {"verbosity": None, "expected_revision": revision(api)})
    assert saved(api, "verbosity") == "unsaved"


def test_a_value_off_the_list_or_a_stale_revision_is_refused(api):
    ops_on_sonnet(api)
    post(api, "bots/ops/output-style", {"output_style": "Chatty", "expected_revision": revision(api)}, expected=422)
    post(api, "bots/ops/verbosity", {"verbosity": "extreme", "expected_revision": revision(api)}, expected=422)
    post(api, "bots/ops/verbosity", {"verbosity": "high", "expected_revision": revision(api) + 1}, expected=409)
    assert (saved(api, "output_style"), saved(api, "verbosity")) == ("unsaved", "unsaved")


def test_saving_the_same_value_again_keeps_the_revision(api):
    ops_on_sonnet(api)
    post(api, "bots/ops/output-style", {"output_style": "Learning", "expected_revision": revision(api)})
    now = revision(api)
    assert post(api, "bots/ops/output-style", {"output_style": "Learning", "expected_revision": now})["revision"] == now


def test_an_agent_sets_both_with_tico_bot_model_and_puts_them_back_on_default(api):
    ops_on_sonnet(api)
    err, out = call(api, "tico_bot_model", {"bot": "ops", "output_style": "Explanatory", "verbosity": "medium"})
    assert not err, out
    shown = get(api, "bots/ops")
    assert (shown["output_style"], shown["verbosity"], shown["model"]) == ("Explanatory", "medium", "claude-sonnet-5-5")
    err, out = call(api, "tico_bot_model", {"bot": "ops", "output_style": "default"})
    assert not err, out
    assert (saved(api, "output_style"), get(api, "bots/ops")["verbosity"]) == ("unsaved", "medium")


def test_the_command_line_takes_both():
    from clients import hubcli

    args = hubcli.parser().parse_args(["bot", "model", "ops", "--output-style", "Proactive", "--verbosity", "default"])
    assert (args.fn, args.output_style, args.verbosity) == ("bot model", "Proactive", "default")


def test_the_contract_the_server_and_the_agent_tools_offer_the_same_lists():
    from clients.commands import bots

    assert list(get_args(M.OutputStyle)) == ["Default", *harnesses.OUTPUT_STYLES]
    assert harnesses.OUTPUT_STYLES == bots.OUTPUT_STYLES
    assert get_args(M.Verbosity) == harnesses.VERBOSITIES == bots.VERBOSITIES
    assert harnesses.output_style({"output_style": "Nope"}) is None
    assert harnesses.verbosity({"verbosity": "high"}) == "high"
