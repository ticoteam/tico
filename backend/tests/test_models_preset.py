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


def needs(api, token="ana-test"):
    from backend.tests.test_api import get
    return [it for it in get(api, "needs-you", token)["items"] if it["kind"] == "setup"]


@pytest.mark.parametrize("isolated", [True, False])
def test_one_sign_in_card_until_claude_and_chatgpt_are_signed_in_and_it_names_the_docker_runner(api, isolated):
    choose(api, models_preset.RECOMMENDED)
    for bot in ("ops", "finance"):
        on_claude(api, bot)
    first, second = runner(api), runner(api)
    for machine, bot in ((first, "ops"), (second, "finance")):
        assign(api, machine, bot)
        heartbeat(api, machine, bot, signed_in=False, isolated=isolated)
    cards = needs(api)
    # One card for the company, not one per bot or per computer.
    assert [c["title"] for c in cards] == [models_preset.SIGN_IN_TITLE]
    assert (models_preset.NOT_ISOLATED_LINE in cards[0]["first_line"]) is (not isolated)
    assert cards[0]["need"] == "connect"
    # Nothing failed while the bots waited.
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE state='failed'").fetchone()[0] == 0
    # Claude signed in on one computer is not enough; ChatGPT too and the card goes by itself.
    heartbeat(api, first, "ops", signed_in=True, isolated=isolated)
    assert len(needs(api)) == 1
    readiness = {"schema_version": 1, "runtimes": {"claude": {"installed": True, "authenticated": "ready"},
                                                   "codex": {"installed": True, "authenticated": "ready"}}, "bots": {}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": readiness},
         token=second["token"])
    assert needs(api) == []


def test_no_sign_in_card_under_the_other_choices(api):
    for preset in ("subscriptions", "gateway", "own-keys", ""):
        choose(api, preset)
        assert needs(api) == []


@pytest.mark.parametrize("isolated", [True, False])
def test_a_computer_that_cannot_run_gateway_work_says_so_and_the_picker_marks_tico_models(api, monkeypatch, isolated):
    from backend import readiness
    from backend.tests.test_api import get
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED, enabled=("anthropic", "openai", "tico"))
    on_claude(api, "ops")
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine, "ops", signed_in=True, isolated=isolated)
    with api.app.state.store.read() as c:
        verdict = readiness.can_run(c, "ops", "pi", model="tico-mid-1.0")
    marked = get(api, "operations", "ana-test")["runnable"].get("ops", {})
    if isolated:
        assert verdict["can_run"] is True and "tico-mid-1.0" not in marked
        return
    assert verdict["can_run"] is False
    assert "can't run Tico Gateway work" in verdict["problem"] and "Docker runner" in verdict["fix"]
    assert marked["tico-mid-1.0"]["short"] == "not on this computer"
    # The harness itself stays offered: pi on the bot's own key still runs here.
    assert "pi" not in marked or marked["pi"]["short"] != "not on this computer"


@pytest.mark.parametrize("preset", models_preset.PRESETS)
def test_drafts_are_written_on_the_gateway_only_under_the_two_gateway_choices(api, monkeypatch, preset):
    import io
    from backend import updates
    from clients import judge as J
    connected(api, monkeypatch)
    choose(api, preset)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = api.app.state.store
    with store.read() as c:
        write = updates.writer_for(c, store.settings, store, api.app.state.vault)
    if preset not in models_preset.GATEWAY_PRESETS:
        assert write is None       # no server key for Claude or ChatGPT here, and the Gateway is not spent for it
        return
    sent = []

    def opener(request, timeout):
        sent.append(request)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"text": "ok"}'}}]}).encode())
    monkeypatch.setattr(J, "gateway_open", opener)
    with store.read() as c:
        write = updates.writer_for(c, store.settings, store, api.app.state.vault)
    assert write.model == "tico-mid-1.0" and write("system", "facts") == {"text": "ok"}
    [request] = sent
    assert request.full_url.endswith("/chat/completions") and request.get_header("Authorization") == "Bearer " + KEY
    assert json.loads(request.data)["model"] == "tico-mid-1.0"


def test_a_gateway_segment_records_which_key_and_install_paid_never_the_key(api, monkeypatch):
    from backend.tests.test_check_models import finish
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    on_claude(api, "ops")
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine, "ops", signed_in=False, isolated=True)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = post(api, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    finish(api, machine, attempt, {"model": "tico-mid-1.0", "runtime": "pi", "harness": "pi", "billing": "api"})
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM turn_usage_segments").fetchone())
        install = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='usage-count'").fetchone()[0])
    assert row["model"] == "tico-mid-1.0" and row["gateway_key_id"] and row["install_id"] == install["install_id"]
    assert KEY not in json.dumps(row) and KEY[3:-3] not in json.dumps(row)


def test_the_owner_saves_the_choice_through_the_api_and_reads_it_back(api):
    from backend.tests.test_api import get, put
    view = get(api, "providers")
    assert view["presets"] == list(models_preset.PRESETS) and view["preset"] == ""
    saved = put(api, "providers", {"enabled": ["anthropic", "openai"], "model": CLAUDE["model"],
                                   "preset": models_preset.RECOMMENDED, "expected_revision": view["revision"]})
    assert saved["preset"] == models_preset.RECOMMENDED
    # A save about something else keeps it.
    again = put(api, "providers", {"enabled": ["anthropic", "openai"], "model": CLAUDE["model"],
                                   "expected_revision": saved["revision"]})
    assert again["preset"] == models_preset.RECOMMENDED
    put(api, "providers", {"enabled": ["anthropic"], "preset": "something-else", "expected_revision": again["revision"]},
        expected=422)
