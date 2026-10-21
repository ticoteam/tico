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

    post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Please do it."})
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
    post(api, "tasks", {"owner": "ops", "title": "Plan the week", "body": "Please do it."})
    turn = claim(api, machine)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"]}, machine["token"])
    signed_in(api, other, [], ("fake", "codex"))
    generation = get(api, "bots/ops")["assignment"]["generation"]
    refused = post(api, "bots/ops/transitions", {"kind": "machine", "runner_id": other["runner_id"],
                                                 "expected_revision": revision(api),
                                                 "expected_generation": generation}, expected=409)
    assert refused["error"]["code"] == "busy"
