"""Changing a bot's connection re-checks its usage-limit hold: a hold belongs to the previous login, so the bot
runs on the new one at once unless that login is held too."""
from backend import login_holds
from backend.store import H
from backend.tests.test_api import api, assign, claim, post, runner  # noqa: F401 (fixture)
from backend.tests.test_login_holds import at, limit, providers_on, signed_in


def status(api, bot="ops"):
    with api.app.state.store.read() as c:
        return H.status(c, bot) or {}


def move(api, machine, generation):
    return post(api, "bots/ops/assignment", {"runner_id": machine["runner_id"], "expected_generation": generation},
                token="ana-test")


def test_moving_a_limited_bot_to_a_computer_re_checks_the_hold_there(api, monkeypatch):
    providers_on(api)
    at(monkeypatch, "2026-10-19T17:00:00.000000Z")
    old, free, held = runner(api), runner(api, label="Free Mac"), runner(api, label="Held Mac")
    assign(api, old, "ops")
    for machine in (old, free, held):
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    signed_in(api, old, ["ops"], ("fake",))
    signed_in(api, free, [], ("fake",))
    signed_in(api, held, [], ("fake",))
    post(api, "tasks", {"owner_id": "ops", "title": "Ship it", "body": "Please do it."})
    limit(api, old, claim(api, old))
    assert status(api)["state"] == "limited"
    with api.app.state.store.read() as c:
        until = login_holds.get(c, old["runner_id"], "", "fake")["until"]
    assert until > "2026-10-19T17:00:00"

    # The other computer's login has its own hold; the bot shows that one.
    with api.app.state.store.transaction() as c:
        login_holds.record(c, held["runner_id"], "", "fake", "2026-10-19T17:00:00.000000Z",
                           "2026-10-19T19:30:00.000000Z", error="Usage limit")
    at(monkeypatch, "2026-10-19T17:05:00.000000Z", api)
    move(api, held, 1)
    assert status(api)["state"] == "limited" and "19:30" in status(api)["focus"]

    # The computer with no hold frees the bot, and its queued work starts before the old hold would have ended.
    at(monkeypatch, "2026-10-19T17:10:00.000000Z", api)
    move(api, free, 2)
    assert status(api)["state"] != "limited"
    signed_in(api, free, ["ops"], ("fake",))
    assert claim(api, free)["bot"] == "ops"
    assert H.now() < until


def test_switching_a_computers_login_frees_the_bots_limited_on_it(api, monkeypatch):
    from backend import execution
    providers_on(api)
    at(monkeypatch, "2026-10-19T17:00:00.000000Z")
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=16 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], ("fake",))
    post(api, "tasks", {"owner_id": "ops", "title": "Ship it", "body": "Please do it."})
    limit(api, machine, claim(api, machine))
    assert status(api)["state"] == "limited"
    # The actor model_login.report passes: a roster actor, so the status write is accepted.
    with api.app.state.store.transaction() as c:
        execution.lift_after_login_switch(c, api.app.state.store.settings, H.KEEPER, machine["runner_id"], "fake")
    assert status(api)["state"] != "limited"
