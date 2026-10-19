"""Synthetic checks of the company's models choice (backend/models_preset.py): the Gateway before sign-in, only on a
computer that runs bots as their own user, and the Gateway as the last fallback only under the two Gateway choices."""
import json

import pytest

from backend import models_preset, providers
from backend.tests.test_api import api, assign, claim, post, runner  # noqa: F401
from backend.tests.test_gateway_connection import KEY, fake_gateway, handed_to, save, setup

CLAUDE = {"runtime": "claude", "harness": "claude-code", "model": "claude-opus-5-5"}


def choose(api, preset, enabled=("anthropic", "openai")):
    with api.app.state.store.transaction() as c:
        record = providers.load(c, api.app.state.store.settings)
        providers.save(c, "human:ana", {"enabled": list(enabled), "runtime": "claude", "model": CLAUDE["model"],
                                        "preset": preset, "expected_revision": record["revision"]}, "2026-10-19T00:00:00Z")


def on_claude(api, bot):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config = {k: v for k, v in config.items() if k != "fallback"}
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (json.dumps({**config, **CLAUDE}), bot))


def heartbeat(api, machine, bot, signed_in, isolated):
    """A computer that has Claude Code installed, signed in or not, and says (or does not say) it isolates bots."""
    readiness = {"schema_version": 1,
                 "runtimes": {"claude": {"installed": True, "authenticated": "ready" if signed_in else "missing"},
                              "pi": {"installed": True, "authenticated": "missing"}},
                 "bots": {bot: {"ready": signed_in, "runtime": "claude", "model": CLAUDE["model"],
                                "repository_present": True, "configuration_valid": True,
                                "problems": [] if signed_in else ["claude is not signed in"]}}}
    if isolated is not None:
        readiness["isolated"] = isolated
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": readiness},
         token=machine["token"])


def connected(api, monkeypatch):
    setup(api, monkeypatch)
    with fake_gateway() as fake:
        fake.active(KEY, 0.1)
        save(api, fake)


@pytest.mark.parametrize("isolated", [True, False, None])
def test_before_sign_in_a_bot_runs_on_the_gateway_only_where_bots_run_as_their_own_user(api, monkeypatch, isolated):
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    on_claude(api, "ops")
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine, "ops", signed_in=False, isolated=isolated)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    got = post(api, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    if isolated is not True:
        # A Mac (or a runner that does not say): no Gateway turn is handed out; the work waits, it never fails.
        assert got is None
        with api.app.state.store.read() as c:
            assert c.execute("SELECT state FROM jobs WHERE bot='ops'").fetchone()[0] == "queued"
        return
    assert (got["config"]["model"], got["config"]["runtime"]) == ("tico-mid-1.0", "pi")
    assert got["config"]["waiting_for_sign_in"] == "claude" and got["gateway"] is True
    assert "TICOAI_GATEWAY_KEY" in handed_to(api, machine, got)
    # Once signed in, the next turn runs on the subscription with no restart, and the bot's own choice was kept.
    heartbeat(api, machine, "ops", signed_in=True, isolated=True)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture again"})
    with api.app.state.store.read() as c:
        assert json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])["model"] \
            == CLAUDE["model"]
        record = providers.load(c, api.app.state.store.settings)
    ready = {"isolated": True, "runtimes": {"claude": {"authenticated": "ready"}}}
    assert models_preset.main_config(record, ready, providers.fill(record, CLAUDE))["model"] == CLAUDE["model"]


@pytest.mark.parametrize("preset", models_preset.PRESETS + ("",))
def test_only_the_two_gateway_choices_put_the_gateway_last_and_spend_its_key(api, monkeypatch, preset):
    connected(api, monkeypatch)
    choose(api, preset)
    on_claude(api, "ops")
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine, "ops", signed_in=True, isolated=True)
    with api.app.state.store.read() as c:
        record = providers.load(c, api.app.state.store.settings)
    rows = providers.default_fallbacks(record)
    spends = preset in ("subscriptions-gateway", "gateway")
    assert (rows[-1]["model"] == "tico-mid-1.0") is spends
    assert sum(1 for row in rows if row["model"].startswith("tico-")) == (1 if spends else 0)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    assert attempt["config"]["model"] == CLAUDE["model"]
    assert attempt["gateway"] is spends
    # A choice without the Gateway never hands the computer the key for a bot that names no Tico model.
    assert ("TICOAI_GATEWAY_KEY" in handed_to(api, machine, attempt)) is spends


def test_a_bot_that_names_its_own_fallback_keeps_it(api, monkeypatch):
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    with api.app.state.store.read() as c:
        record = providers.load(c, api.app.state.store.settings)
    own = {**CLAUDE, "fallback": {"harness": "codex", "model": "gpt-5.5"}}
    assert models_preset.with_gateway_fallback(record, {"isolated": True}, own) == own
    assert "gateway_fallback" not in models_preset.with_gateway_fallback(record, {"isolated": False}, CLAUDE)


def test_the_installer_choice_seeds_claude_and_chatgpt_and_an_unknown_choice_is_refused(api):
    settings = api.app.state.store.settings
    seeded = providers.env_seed(type("S", (), {"models_preset": "subscriptions-gateway"})())
    assert (seeded["enabled"], seeded["preset"]) == (["anthropic", "openai"], "subscriptions-gateway")
    assert providers.env_seed(type("S", (), {"models_preset": "something-else"})()) is None
    with api.app.state.store.transaction() as c, pytest.raises(providers.ProviderError):
        record = providers.load(c, settings)
        providers.save(c, "human:ana", {"enabled": ["anthropic"], "preset": "something-else",
                                        "expected_revision": record["revision"]}, "2026-10-19T00:00:00Z")
