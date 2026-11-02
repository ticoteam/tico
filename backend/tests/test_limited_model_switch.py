"""A person can always move a rate-limited bot to another model (Dev 334).

While a bot is `limited`, a person's message, Try now or the end of the cooldown each start a turn at once, and its
status stays `limited` while that turn runs (execution.settle_status). The model change used to refuse every running
turn ("Wait for the current bot turn to finish"), so the bot showed Rate limited yet could not be moved off the spent
model. The change now applies while the turn runs and takes effect from the next one; the limit that turn hits is the
old model's and never lands on the new one."""
from backend import login_holds
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401 (fixture)
from backend.tests.test_login_holds import CLAUDE, at, limit, providers_on, signed_in


def revision(api, bot="ops"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT revision FROM bot_config WHERE bot=?", (bot,)).fetchone()[0]


def status(api, bot="ops"):
    with api.app.state.store.read() as c:
        return H.status(c, bot) or {}


def test_a_limited_bot_with_a_turn_running_can_be_moved_to_another_model(api, monkeypatch):
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:16:00.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], ("codex", "claude"))
    post(api, "bots/ops/transitions", {"kind": "model", "model": "gpt-6-luna", "expected_revision": revision(api)})

    post(api, "tasks", {"owner_id": "ops", "title": "Plan the week", "body": "Please do it."})
    limit(api, machine, claim(api, machine))                    # the model's plan runs out
    assert status(api)["state"] == "limited"

    # A person writes, which is worth one try: the turn starts and the header still says Rate limited.
    at(monkeypatch, "2026-10-21T23:25:00.000000Z", api)
    post(api, "chat/ops", {"text": "Are you there?"})
    retry = claim(api, machine)
    post(api, f"attempts/{retry['id']}/started", {"thread_id": "old-" + retry["id"]}, machine["token"])
    assert status(api)["state"] == "limited"

    # The move the person makes to unstick it goes through, and lifts the old model's cooldown.
    at(monkeypatch, "2026-10-21T23:25:20.000000Z", api)
    moved = post(api, "bots/ops/transitions", {"kind": "model", "model": "claude-haiku-5-5",
                                               "expected_revision": revision(api)})
    assert moved["state"] == "applied"
    assert status(api)["state"] != "limited"

    # The turn that was already running hits the old model's limit: that is not the new model's.
    at(monkeypatch, "2026-10-21T23:25:40.000000Z", api)
    events = [{"seq": 1, "kind": "error", "payload": {"error": CLAUDE, "limit": True}}]
    post(api, f"attempts/{retry['id']}/events", {"events": events}, machine["token"])
    post(api, f"attempts/{retry['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
         machine["token"])
    assert status(api)["state"] != "limited"
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine["runner_id"], "", "claude")
        assert c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops' AND model='claude-haiku-5-5'").fetchone() is None

    # Its work goes back in the queue and the next turn runs on the new model at once.
    signed_in(api, machine, ["ops"], ("codex", "claude"))
    nxt = claim(api, machine)
    assert nxt and (nxt["config"]["runtime"], nxt["config"]["model"]) == ("claude", "claude-haiku-5-5")


def test_a_move_to_another_computer_still_waits_for_the_running_turn(api):
    providers_on(api)
    machine, other = runner(api), runner(api, label="Other Mac")
    assign(api, machine, "ops")
    signed_in(api, machine, ["ops"], ("codex",))
    post(api, "tasks", {"owner_id": "ops", "title": "Plan the week", "body": "Please do it."})
    turn = claim(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"]}, machine["token"])
    signed_in(api, other, [], ("fake", "codex"))
    generation = get(api, "bots/ops")["assignment"]["generation"]
    refused = post(api, "bots/ops/transitions", {"kind": "machine", "runner_id": other["runner_id"],
                                                 "expected_revision": revision(api),
                                                 "expected_generation": generation}, expected=409)
    assert refused["error"]["code"] == "busy"


def test_a_turn_leased_before_a_model_change_does_not_start_the_new_models_session(api, monkeypatch):
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:25:00.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    signed_in(api, machine, ["ops"], ("codex", "claude"))
    post(api, "bots/ops/transitions", {"kind": "model", "model": "gpt-6-luna", "expected_revision": revision(api)})
    post(api, "tasks", {"owner_id": "ops", "title": "Plan the week", "body": "Please do it."})
    turn = claim(api, machine)                                  # handed the old model
    at(monkeypatch, "2026-10-21T23:25:20.000000Z", api)
    post(api, "bots/ops/transitions", {"kind": "model", "model": "claude-haiku-5-5", "expected_revision": revision(api)})
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "old-" + turn["id"]}, machine["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops' AND model='claude-haiku-5-5'").fetchone() is None


def limited_with_a_retry_running(api, monkeypatch, machine, model="gpt-6-luna"):
    """The bot on `model` hits its plan's limit, a person writes, and the retry's turn is running: Rate limited."""
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], ("codex", "claude"))
    post(api, "bots/ops/model", {"model": model, "expected_revision": revision(api)})
    post(api, "tasks", {"owner_id": "ops", "title": "Plan the week", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    assert status(api)["state"] == "limited"
    at(monkeypatch, "2026-10-21T23:25:00.000000Z", api)
    post(api, "chat/ops", {"text": "Are you there?"})
    retry = claim(api, machine)
    post(api, f"attempts/{retry['id']}/started", {"thread_id": "old-" + retry["id"]}, machine["token"])
    assert status(api)["state"] == "limited"
    return retry


def finish_limited(api, machine, turn):
    events = [{"seq": 1, "kind": "error", "payload": {"error": CLAUDE, "limit": True}}]
    post(api, f"attempts/{turn['id']}/events", {"events": events}, machine["token"])
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
         machine["token"])


def test_the_hub_and_mcp_model_change_also_moves_a_limited_bot_while_its_turn_runs(api, monkeypatch):
    # The hub CLI and MCP model tool (POST bots/{bot}/model) follow the Settings rule (Dev 353, Dev 334).
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:16:00.000000Z")
    machine = runner(api)
    retry = limited_with_a_retry_running(api, monkeypatch, machine)
    at(monkeypatch, "2026-10-21T23:25:20.000000Z", api)
    moved = post(api, "bots/ops/model", {"model": "claude-haiku-5-5", "expected_revision": revision(api)})
    assert moved["model"] == "claude-haiku-5-5"
    assert status(api)["state"] != "limited"

    at(monkeypatch, "2026-10-21T23:25:40.000000Z", api)
    finish_limited(api, machine, retry)
    assert status(api)["state"] != "limited"
    with api.app.state.store.read() as c:
        assert not login_holds.get(c, machine["runner_id"], "", "claude")
        assert c.execute("SELECT 1 FROM bot_sessions WHERE bot='ops' AND model='claude-haiku-5-5'").fetchone() is None
    signed_in(api, machine, ["ops"], ("codex", "claude"))
    nxt = claim(api, machine)
    assert nxt and (nxt["config"]["runtime"], nxt["config"]["model"]) == ("claude", "claude-haiku-5-5")


def test_an_effort_only_change_or_a_move_away_and_back_keeps_the_running_turns_limit(api, monkeypatch):
    # The turn still runs on the model the bot is set to: its limit is that model's, and the bot waits it out.
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:16:00.000000Z")
    machine = runner(api)
    retry = limited_with_a_retry_running(api, monkeypatch, machine)
    at(monkeypatch, "2026-10-21T23:25:20.000000Z", api)
    post(api, "bots/ops/transitions", {"kind": "model", "model": "gpt-6-luna", "effort": "low",
                                       "expected_revision": revision(api)})
    # An effort-only change leaves the bot on the spent model: nothing lifts, and no model is said to have changed.
    assert status(api)["state"] == "limited" and "Model changed" not in (status(api).get("focus") or "")
    post(api, "bots/ops/model", {"model": "claude-haiku-5-5", "expected_revision": revision(api)})
    post(api, "bots/ops/model", {"model": "gpt-6-luna", "effort": "low", "expected_revision": revision(api)})
    at(monkeypatch, "2026-10-21T23:25:40.000000Z", api)
    finish_limited(api, machine, retry)
    assert status(api)["state"] == "limited"


def test_a_move_onto_a_model_whose_login_is_held_keeps_rate_limited(api, monkeypatch):
    # Another model on the same spent login waits for the same reset: the header does not say work resumes.
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:16:00.000000Z")
    machine = runner(api)
    retry = limited_with_a_retry_running(api, monkeypatch, machine, model="claude-opus-5-5")
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "claude")
    at(monkeypatch, "2026-10-21T23:25:20.000000Z", api)
    post(api, "bots/ops/transitions", {"kind": "model", "model": "claude-haiku-5-5",
                                       "expected_revision": revision(api)})
    now = status(api)
    assert now["state"] == "limited"
    assert "resumes" not in (now.get("focus") or "")
    # The turn still out on the old model ends on the limit: the new model waits on that login too.
    at(monkeypatch, "2026-10-21T23:25:40.000000Z", api)
    finish_limited(api, machine, retry)
    assert status(api)["state"] == "limited"
    # Moving on to another login lifts it.
    post(api, "bots/ops/model", {"model": "gpt-6-luna", "expected_revision": revision(api)})
    now = status(api)
    assert now["state"] != "limited"


def test_after_the_gateways_cap_another_tico_model_waits_on_the_same_key(api, monkeypatch):
    # The Gateway's cap is the company key's: any Tico model meets it again, another provider's model does not.
    from backend import execution
    from backend.store import encode
    import json
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:16:00.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    signed_in(api, machine, ["ops"], ("codex",))
    post(api, "tasks", {"owner_id": "ops", "title": "Plan the week", "body": "Please do it."})
    limit(api, machine, claim(api, machine), errors=("Tico AI Gateway: cap_reached for this key",))
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        for model, held in (("tico-mid-1.0", "gateway"), ("claude-haiku-5-5", "")):
            config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
            config.update({"model": model, "runtime": "pi" if held else "claude"})
            c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (encode(config),))
            assert execution.model_change_hold(c, settings, "ops") == held


def test_a_turn_moved_to_another_model_on_the_same_login_holds_that_login_when_it_hits_the_limit(api, monkeypatch):
    # Opus to Haiku keeps the Claude login: the running turn's limit is the new model's too, so the next turn waits.
    providers_on(api)
    at(monkeypatch, "2026-10-21T23:16:00.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    signed_in(api, machine, ["ops"], ("codex", "claude"))
    post(api, "bots/ops/model", {"model": "claude-opus-5-5", "expected_revision": revision(api)})
    post(api, "tasks", {"owner_id": "ops", "title": "Plan the week", "body": "Please do it."})
    turn = claim(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"]}, machine["token"])
    post(api, "bots/ops/model", {"model": "claude-haiku-5-5", "expected_revision": revision(api)})
    at(monkeypatch, "2026-10-21T23:16:20.000000Z", api)
    finish_limited(api, machine, turn)
    assert status(api)["state"] == "limited"
    with api.app.state.store.read() as c:
        assert login_holds.get(c, machine["runner_id"], "", "claude")
