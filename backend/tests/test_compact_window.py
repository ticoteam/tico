"""Where a bot's conversation compacts (task #230): twelve sizes from 100k to 1M, a default that follows the model (Opus
400k, Haiku 150k, any other 250k, never past the model's window), sent with every turn, and settable by an agent."""

from backend import bot_settings as BS
from backend import providers
from backend.tests.helpers.api import claim, get, post  # noqa: F401  (api is a fixture)
from backend.tests.helpers.mcp import call
from backend.tests.helpers.task_models import ops_on_sonnet, revision

SIZES = [100_000, 150_000, 200_000, 250_000, 300_000, 400_000, 500_000, 600_000, 700_000, 800_000, 900_000, 1_000_000]


def on_model(api, model, bot="ops"):
    with api.app.state.store.transaction() as c:
        config = BS.read(c, bot)
        config.update(runtime="claude", harness="claude", model=model, reasoning_effort="medium")
        BS.write(c, bot, config)


def chat_turn(api, machine, bot="ops"):
    post(api, "messages", {"to": bot, "text": "What are you on today?"})
    return claim(api, machine)


def test_every_one_of_the_twelve_sizes_saves_and_any_other_is_refused_naming_them(api):
    ops_on_sonnet(api)
    for size in SIZES:
        post(api, "bots/ops/compact-at", {"compact_at": size, "expected_revision": revision(api)})
        assert get(api, "bots/ops")["compact_at"] == size
    refused = post(
        api, "bots/ops/compact-at", {"compact_at": 350_000, "expected_revision": revision(api)}, expected=422
    )
    said = str(refused)
    assert all(str(size) in said for size in SIZES), said
    assert get(api, "bots/ops")["compact_at"] == 1_000_000


def test_a_task_context_length_takes_the_new_sizes(api):
    ops_on_sonnet(api)
    saved = post(
        api,
        "bots/ops/task-model",
        {"task_model": {"compact_at": 700_000}, "expected_revision": revision(api)},
    )
    assert saved["task_model"]["compact_at"] == 700_000


def test_the_default_follows_the_model_and_never_passes_its_window():
    assert providers.compact_default("claude-opus-5-5") == 400_000
    assert providers.compact_default("claude-haiku-5-5") == 150_000
    assert providers.compact_default("claude-sonnet-5-5") == 250_000
    assert providers.compact_default("") == 250_000
    assert providers.compact_default("no-such-model") == 250_000
    # A window below the default: the largest size that fits in it.
    assert providers.fit_compact_at(400_000, 128_000) == 100_000
    assert providers.fit_compact_at(600_000, 500_000) == 500_000
    assert providers.fit_compact_at(250_000, None) == 250_000


def test_a_bot_with_no_saved_window_shows_and_runs_on_its_models_default(api):
    machine = ops_on_sonnet(api)
    on_model(api, "claude-opus-5-5")
    assert get(api, "bots/ops")["compact_at"] == 400_000
    assert chat_turn(api, machine)["config"]["compact_at"] == 400_000
    on_model(api, "claude-haiku-5-5")
    assert get(api, "bots/ops")["compact_at"] == 150_000


def test_a_haiku_turn_with_no_saved_window_compacts_at_150k(api):
    machine = ops_on_sonnet(api)
    on_model(api, "claude-haiku-5-5")
    assert chat_turn(api, machine)["config"]["compact_at"] == 150_000


def test_a_saved_window_wins_over_the_models_default(api):
    machine = ops_on_sonnet(api)
    on_model(api, "claude-opus-5-5")
    post(api, "bots/ops/compact-at", {"compact_at": 700_000, "expected_revision": revision(api)})
    assert get(api, "bots/ops")["compact_at"] == 700_000
    assert chat_turn(api, machine)["config"]["compact_at"] == 700_000


def test_an_agent_sets_the_window_with_tico_bot_model_and_a_size_off_the_list_is_refused(api):
    ops_on_sonnet(api)
    err, out = call(api, "tico_bot_model", {"bot": "ops", "compact_at": 300_000})
    assert not err, out
    assert get(api, "bots/ops")["compact_at"] == 300_000
    assert get(api, "bots/ops")["model"] == "claude-sonnet-5-5"
    err, out = call(api, "tico_bot_model", {"bot": "ops", "model": "claude-opus-5-5", "compact_at": 800_000})
    assert not err, out
    shown = get(api, "bots/ops")
    assert (shown["model"], shown["compact_at"]) == ("claude-opus-5-5", 800_000)
    err, out = call(api, "tico_bot_model", {"bot": "ops", "compact_at": 350_000})
    assert err, out
    assert all(str(size) in str(out) for size in SIZES), out
    assert get(api, "bots/ops")["compact_at"] == 800_000


def test_the_server_the_contract_and_the_agent_tools_offer_the_same_twelve_sizes():
    from typing import get_args

    from backend import models as M
    from clients.commands import bots

    assert list(providers.COMPACT_SIZES) == list(get_args(M.CompactAt)) == list(bots.COMPACT_SIZES) == SIZES


def test_tico_bot_model_takes_compact_at_on_the_command_line():
    from clients import hubcli

    args = hubcli.parser().parse_args(["bot", "model", "ops", "--compact-at", "300000"])
    assert (args.fn, args.bot, args.model, args.compact_at) == ("bot model", "ops", None, 300_000)


def test_a_saved_window_larger_than_the_models_is_lowered_to_fit_on_the_turn():
    # Saved at 600k, then moved to a 500k model by an agent or the API: the turn compacts at 500k, not past the window.
    assert providers.with_compact_default({"model": "grok-4.6", "compact_at": 600_000})["compact_at"] == 500_000
    assert providers.with_compact_default({"model": "claude-opus-5-5", "compact_at": 700_000})["compact_at"] == 700_000
    assert providers.with_compact_default({"model": "grok-4.6"})["compact_at"] == 250_000
