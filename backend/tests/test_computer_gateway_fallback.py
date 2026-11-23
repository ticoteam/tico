"""Gateway fallback is available for unset agents; a selected payer waits when it cannot pay."""

import pytest

from backend import login_holds, models_preset
from backend.store import H
from backend.tests.helpers.api import assign, get, post, runner
from backend.tests.helpers.model_subscriptions import add, on, pick
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.gateway_connection import KEY
from backend.tests.helpers.models_preset import CLAUDE, choose


def computer(api, bot="ops", signed_in=False, label="Studio Mac", bot_ready=None):
    """An isolated computer that takes subscriptions, with Claude Code installed and signed in or not, and pi. The
    bot reads ready when it is signed in, or (`bot_ready`) when a plan given to the computer signs it in, as the
    runner reports it (runner-rs/src/readiness.rs `subscribed`)."""
    bot_ready = signed_in if bot_ready is None else bot_ready
    m = runner(api, label=label)
    assign(api, m, bot)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": ["model_subscriptions_v1", "attempt_credentials_v1"],
            "readiness": {
                "schema_version": 1,
                "isolated": True,
                "runtimes": {
                    "claude": {"installed": True, "authenticated": "ready" if signed_in else "missing"},
                    "pi": {"installed": True, "authenticated": "missing"},
                },
                "bots": {
                    bot: {
                        "ready": bot_ready,
                        "runtime": "claude",
                        "model": CLAUDE["model"],
                        "repository_present": True,
                        "configuration_valid": True,
                        "problems": [] if bot_ready else ["claude is not signed in"],
                    }
                },
            },
        },
        token=m["token"],
    )
    return m


def used_up(api, sid, days=1):
    """The plan held at its usage limit until `days` from now, as a limit met on it leaves it."""
    with api.app.state.store.transaction() as c:
        login_holds._save(
            c,
            {
                "runner_id": "",
                "profile": login_holds.subscription_login(sid),
                "runtime": "claude",
                "until": H.shift(H.now(), seconds=int(days * 86400)),
                "since": H.now(),
                "error": "You've hit your limit",
            },
        )


def switch(api, m, on, token="ana-test", expected=200):
    return post(api, f"computers/{m['runner_id']}/gateway-fallback", {"on": on}, token, expected=expected)


def listed(api, m):
    return next(x for x in get(api, "operations")["machines"] if x["id"] == m["runner_id"])


def turn(api, m, bot="ops"):
    post(api, f"chat/{bot}", {"text": "Use only the synthetic fixture"})
    return post(api, "jobs/claim", {"bot": bot}, token=m["token"])["attempt"]


@pytest.fixture
def gateway_company(api, monkeypatch):
    setup(api)
    monkeypatch.setenv("TICO_GATEWAY_KEY", KEY)
    choose(api, models_preset.RECOMMENDED)
    on(api, "ops", "claude", CLAUDE["model"])


def test_a_held_pick_waits_even_when_gateway_fallback_is_enabled(api, gateway_company):
    m = computer(api)
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    used_up(api, sid)
    assert listed(api, m)["gateway_fallback"] is False
    assert turn(api, m) is None
    assert switch(api, m, True, token="ben-test") == {"gateway_fallback": True}
    assert listed(api, m)["gateway_fallback"] is True
    assert post(api, "jobs/claim", {"bot": "ops"}, token=m["token"])["attempt"] is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE bot_id='ops'").fetchone()[0] == "queued"


def test_an_unset_agent_can_use_gateway_while_an_agent_with_a_held_pick_waits(api, gateway_company):
    on(api, "cpo", "claude", CLAUDE["model"])
    waits, falls = computer(api, "ops", label="Pool Mac"), computer(api, "cpo", label="Unset Mac")
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    used_up(api, sid)
    switch(api, waits, True)
    assert turn(api, waits, "ops") is None
    got = turn(api, falls, "cpo")
    assert got and got["config"]["runtime"] == "pi" and got["gateway"]


def test_a_plan_with_usage_left_runs_whatever_the_switch_says(api, gateway_company):
    m = computer(api, bot_ready=True)
    sid = add(api)["id"]
    pick(api, "ops", "claude", sid)
    for allowed in (False, True):
        switch(api, m, allowed)
        got = turn(api, m)
        # AC-5: on the plan, never the Gateway as its main model.
        assert got["config"]["model"] == CLAUDE["model"]
        assert "gateway_fallback" not in got["config"] and got["config"]["fallback"] == {"off": True}
        post(api, f"attempts/{got['id']}/started", {"thread_id": "t-" + got["id"]}, m["token"])
        post(
            api, f"attempts/{got['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"]
        )


def test_a_computer_not_signed_in_yet_still_runs_on_the_gateway_with_its_switch_off(api, gateway_company):
    m = computer(api)
    # AC-6: no plan given, no login: "Use until a computer is signed in" runs it on the Gateway, as today.
    got = turn(api, m)
    assert got["config"]["runtime"] == "pi" and got["config"]["waiting_for_sign_in"] == "claude"


def test_only_an_admin_changes_the_switch_and_everyone_reads_it(api, gateway_company):
    m = computer(api)
    # AC-7: a member reads it and cannot change it.
    switch(api, m, True, token="cara-test", expected=403)
    assert listed(api, m)["gateway_fallback"] is False
    switch(api, m, True, token="ana-test")
    with api.app.state.store.read() as c:
        row = c.execute("SELECT detail FROM events WHERE action='runner.gateway_fallback'").fetchone()
    assert row and '"on": true' in row[0]


def test_removing_the_last_pool_plan_unsets_the_pick_and_allows_gateway(api, gateway_company):
    from backend.tests.helpers.api import headers

    m = computer(api)
    sid = add(api)["id"]
    pick(api, "ops", "claude", person="ana")
    response = api.delete(f"/api/v2/model-subscriptions/{sid}?confirm=1", headers=headers("ana-test"))
    assert response.status_code == 200
    assert "claude" not in get(api, "bots/ops/subscriptions")["subscriptions"]
    got = turn(api, m)
    assert got and got["config"]["runtime"] == "pi" and got["config"]["waiting_for_sign_in"] == "claude"
