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


@pytest.mark.parametrize("isolated", [True, False, None])
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
    if isolated is False:
        assert "can't run Tico Gateway work" in verdict["problem"] and "Docker runner" in verdict["fix"]
    else:
        # A runner from before it said: only an explicit yes runs Gateway work, and the fix is to update it.
        assert "hasn't said it can run Tico Gateway work" in verdict["problem"] and "Update" in verdict["fix"]
    assert marked["tico-mid-1.0"]["short"] == "not on this computer"
    # The harness itself stays offered: pi on the bot's own key still runs here.
    assert "pi" not in marked or marked["pi"]["short"] != "not on this computer"


def on_tico(api, machine, isolated):
    """ops on Tico Mid 1.0 itself, ready on `machine`, which says (or does not say) it runs bots as their own user."""
    from backend.tests.test_gateway_connection import put_bot_on
    put_bot_on(api, "ops", "tico-mid-1.0")
    readiness = {"schema_version": 1, "runtimes": {"pi": {"installed": True, "authenticated": "missing"}},
                 "bots": {"ops": {"ready": True, "runtime": "pi", "model": "tico-mid-1.0",
                                  "repository_present": True, "configuration_valid": True}}}
    if isolated is not None:
        readiness["isolated"] = isolated
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": readiness},
         token=machine["token"])


@pytest.mark.parametrize("preset", [models_preset.RECOMMENDED, ""])
@pytest.mark.parametrize("isolated", [True, False, None])
def test_a_bot_on_a_tico_model_waits_where_the_computer_does_not_say_it_runs_bots_as_their_own_user(
        api, monkeypatch, preset, isolated):
    """A Tico model the bot names itself, under any choice: no attempt is handed out that its runner must refuse
    (runner-rs/src/gateway_relay.rs NOT_ISOLATED). The work stays queued and Health says why."""
    from backend import health
    from backend.tests.test_claim_refusals import health_admin, held
    connected(api, monkeypatch)
    choose(api, preset, enabled=("anthropic", "openai", "tico"))
    machine = runner(api)
    assign(api, machine, "ops")
    on_tico(api, machine, isolated)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    if isolated:
        attempt = claim(api, machine, "ops")
        assert attempt["config"]["model"] == "tico-mid-1.0" and attempt["gateway"] is True
        return
    rows, found = held(api, machine)
    reason = "not_isolated" if isolated is False else "isolation_unknown"
    assert [r["reason"] for r in rows] == [reason] and "queue_idle" not in found
    with api.app.state.store.read() as c:
        assert {r[0] for r in c.execute("SELECT state FROM jobs WHERE bot='ops'")} == {"queued"}
        assert c.execute("SELECT count(*) FROM attempts WHERE bot='ops'").fetchone()[0] == 0
        [row] = health._stuck(c, rows, set(), health_admin(api), True)
    assert "Tico Gateway work" in row["why"], row
    # Once the computer says it can, the same work starts.
    on_tico(api, machine, True)
    assert claim(api, machine, "ops")["config"]["model"] == "tico-mid-1.0"


def waiting_heartbeat(api, machine, change):
    """ops on Claude Code, not signed in on an isolated computer, with one other thing wrong (`change`)."""
    bot = {"ready": False, "runtime": "claude", "model": CLAUDE["model"], "repository_present": True,
           "configuration_valid": True, "problems": ["claude is not signed in"]}
    pi = {"installed": True, "authenticated": "missing"}
    if change == "no repository":
        bot = {**bot, "repository_present": False, "problems": bot["problems"] + ["Missing bot repository or AGENT.md"]}
    elif change == "settings differ":
        bot = {**bot, "configuration_valid": False, "problems": bot["problems"] + ["Configuration differs from server"]}
    elif change == "no pi":
        pi = {"installed": False}
    readiness = {"schema_version": 1, "isolated": True,
                 "runtimes": {"claude": {"installed": True, "authenticated": "missing"}, "pi": pi},
                 "bots": {} if change == "no report" else {"ops": bot}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": readiness},
         token=machine["token"])


@pytest.mark.parametrize("change", ["only the sign-in", "no repository", "settings differ", "no pi", "no report"])
def test_waiting_for_a_sign_in_lets_through_only_the_missing_sign_in(api, monkeypatch, change):
    """The Gateway turn before sign-in passes over the bot's missing subscription and nothing else: its checkout, its
    settings and the Gateway's own runtime must still be ready, and the computer must have reported the bot."""
    from backend.tests.test_claim_refusals import held
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    on_claude(api, "ops")
    machine = runner(api)
    assign(api, machine, "ops")
    waiting_heartbeat(api, machine, change)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    if change == "only the sign-in":
        attempt = claim(api, machine, "ops")
        assert (attempt["config"]["model"], attempt["config"]["waiting_for_sign_in"]) == ("tico-mid-1.0", "claude")
        return
    rows, found = held(api, machine)
    assert [r["reason"] for r in rows] == ["readiness_unknown" if change == "no report" else "not_ready"], rows
    assert "queue_idle" not in found
    with api.app.state.store.read() as c:
        assert {r[0] for r in c.execute("SELECT state FROM jobs WHERE bot='ops'")} == {"queued"}


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


@pytest.mark.parametrize("change", ["none", "rotated", "cleared"])
def test_a_gateway_segment_records_the_key_its_runner_was_handed_and_the_install_never_the_key(api, monkeypatch, change):
    """The key id is the one handed with the credentials: a key rotated or cleared before the run ends doesn't change
    which key paid. A turn that used up Claude and finished on the Gateway records it on the Gateway segment only."""
    from backend import gateway_connection
    from backend.tests.test_gateway_connection import OTHER, delete
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    on_claude(api, "ops")
    machine = runner(api)
    assign(api, machine, "ops")
    heartbeat(api, machine, "ops", signed_in=True, isolated=True)
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    attempt = claim(api, machine, "ops")
    with api.app.state.store.read() as c:
        paid = gateway_connection.key_id(c, api.app.state.store.settings)
    assert paid and "TICOAI_GATEWAY_KEY" in handed_to(api, machine, attempt)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"][:8]}, machine["token"])
    # While it runs, the owner rotates the key or clears the connection.
    if change == "rotated":
        with fake_gateway() as fake:
            fake.active(OTHER, 0.1)
            save(api, fake, key=OTHER)
    elif change == "cleared":
        delete(api, "providers/gateway")
    with api.app.state.store.read() as c:
        assert (gateway_connection.key_id(c, api.app.state.store.settings) == paid) is (change == "none")
    claude = {"model": CLAUDE["model"], "runtime": "claude", "harness": "claude-code", "billing": "subscription"}
    tico = {"model": "tico-mid-1.0", "runtime": "pi", "harness": "pi", "billing": "api"}
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done.", "fallback": "pi",
         "usage": {**tico, "input_tokens": 60, "output_tokens": 60, "segments": [
             {**claude, "input_tokens": 30, "output_tokens": 30}, {**tico, "input_tokens": 30, "output_tokens": 30}]}},
         machine["token"])
    with api.app.state.store.read() as c:
        rows = {r["model"]: dict(r) for r in c.execute("SELECT * FROM turn_usage_segments")}
        install = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='usage-count'").fetchone()[0])
    assert rows["tico-mid-1.0"]["gateway_key_id"] == paid
    assert rows["tico-mid-1.0"]["install_id"] == install["install_id"]
    assert rows[CLAUDE["model"]]["gateway_key_id"] is None and rows[CLAUDE["model"]]["install_id"] is None
    assert KEY not in json.dumps(rows) and KEY[3:-3] not in json.dumps(rows)


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
