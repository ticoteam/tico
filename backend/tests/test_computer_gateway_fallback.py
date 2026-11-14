"""A computer's own "Fall back to Tico Gateway" (backend/models_preset.py computer_falls_back, plans_out): off for every
computer until an admin turns it on. When every plan given to a computer for a tool is used up and it has no login of
its own, its turns run on the Gateway only where that switch is on; elsewhere they wait for a plan. The company's
Gateway switch keeps only its other job, running turns until a computer is signed in.
Spec: "Gateway fallback is a setting of each computer" (task #465)."""

import pytest

from backend import login_holds, models_preset
from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.api import assign, get, post, runner
from backend.tests.helpers.model_subscriptions import add, give, on
from backend.tests.helpers.models_preset import CLAUDE, choose, connected


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
            "capabilities": [MS.CAPABILITY],
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
    return post(api, f"runners/{m['runner_id']}/gateway-fallback", {"on": on}, token, expected=expected)


def listed(api, m):
    return next(x for x in get(api, "operations")["machines"] if x["id"] == m["runner_id"])


def turn(api, m, bot="ops"):
    post(api, f"chat/{bot}", {"text": "Use only the synthetic fixture"})
    return post(api, "jobs/claim", {"bot": bot}, token=m["token"])["attempt"]


@pytest.fixture
def gateway_company(api, monkeypatch):
    connected(api, monkeypatch)
    choose(api, models_preset.RECOMMENDED)
    on(api, "ops", "claude", CLAUDE["model"])


def test_a_computer_whose_plans_ran_out_waits_unless_its_own_switch_is_on(api, gateway_company):
    m = computer(api)
    sid = add(api)["id"]
    give(api, m, "claude", sid)
    used_up(api, sid)
    # AC-1: never changed, the switch is off. AC-2: the turn waits; no Gateway turn runs.
    assert listed(api, m)["gateway_fallback"] is False
    assert turn(api, m) is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE bot_id='ops'").fetchone()[0] == "queued"
    # AC-3: an admin turns it on; the next claim runs it on the Gateway.
    assert switch(api, m, True, token="ben-test") == {"gateway_fallback": True}
    assert listed(api, m)["gateway_fallback"] is True
    got = post(api, "jobs/claim", {"bot": "ops"}, token=m["token"])["attempt"]
    assert (got["config"]["runtime"], got["config"]["model"]) == ("pi", "tico-medium-1.0")
    assert got["gateway"] is True


def test_only_the_computer_that_falls_back_runs_on_the_gateway(api, gateway_company):
    on(api, "cpo", "claude", CLAUDE["model"])
    waits, falls = computer(api, "ops", label="Off Mac"), computer(api, "cpo", label="On Mac")
    for m in (waits, falls):
        sid = add(api, name="Plan for " + m["runner_id"])["id"]
        give(api, m, "claude", sid)
        used_up(api, sid)
    switch(api, falls, True)
    # AC-4: both plans are used up; only the bot on the computer that falls back runs, on the Gateway.
    assert turn(api, waits, "ops") is None
    got = turn(api, falls, "cpo")
    assert got and got["config"]["runtime"] == "pi"


def test_a_plan_with_usage_left_runs_whatever_the_switch_says(api, gateway_company):
    m = computer(api, bot_ready=True)
    sid = add(api)["id"]
    give(api, m, "claude", sid)
    for allowed in (False, True):
        switch(api, m, allowed)
        got = turn(api, m)
        # AC-5: on the plan, never the Gateway as its main model.
        assert got["config"]["model"] == CLAUDE["model"]
        # The Gateway is the last hop when the plan runs out mid-turn only where the computer falls back to it.
        assert ("gateway_fallback" in got["config"]) is allowed
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
