"""Held agent payers expose the reset and the person who can help through Health."""

from backend import agent_subscriptions, login_holds, readiness
from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.api import claim, get, post, restrict, unrelated_task_managers
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import OTHER, add, company, machine, on, pick

UNTIL = "2999-11-22T09:00:00.000000Z"
MODEL = "claude-sonnet-5-5"


def hold(api, sid, until=UNTIL):
    with api.app.state.store.transaction() as c:
        login_holds._save(
            c,
            {
                "runner_id": "",
                "profile": login_holds.subscription_login(sid),
                "runtime": "claude",
                "until": until,
                "since": H.now(),
                "error": "You've hit your weekly limit · resets Nov 22, 9am (UTC)",
            },
        )


def plans(api, *, pool=False, second=False, signed_in=False, other_owner="ana-test", capacity=False):
    setup(api)
    company(api)
    on(api, "ops", "claude", MODEL)
    box = machine(api, signed_in=signed_in)
    first = add(api, name="Primary plan")["id"]
    other = add(api, secret=OTHER, name="Spare plan", token=other_owner)["id"] if second else None
    pick(api, "ops", "claude", None if pool else first, person="ana" if pool else None)
    if capacity:
        with api.app.state.store.transaction() as c:
            login_holds.record(c, "", login_holds.subscription_login(first), "claude", H.now(), error="overloaded")
    else:
        hold(api, first)
    post(api, "chat/ops", {"text": "Check the account."})
    return box, first, other


def checks(api, token="ana-test"):
    return {row["id"]: row for row in get(api, "health", token)["checks"]}


def wait(api, token="ana-test"):
    [item] = checks(api, token)["paid_by_wait"]["items"]
    return item


def test_a_held_exact_pick_names_its_reset_and_links_to_providers(api):
    box, _, _ = plans(api, second=True)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM assignments WHERE runner_id=? AND bot<>'ops'", (box["runner_id"],))
    assert claim(api, box) is None
    item = wait(api)
    assert "Primary plan: next reset" in item["text"] and "Ana or an admin" in item["text"]
    assert [fix["href"] for fix in item["fixes"]] == ["#/settings/providers"]
    assert "computer_signin" not in checks(api)
    with api.app.state.store.read() as c:
        verdict = readiness.can_run(c, "ops", "claude")
    assert verdict["can_run"] is False and not verdict.get("sign_in")
    assert verdict["link"] == "#/settings/providers"


def test_a_held_plan_stays_queued_until_its_hold_is_cleared(api):
    box, sid, _ = plans(api)
    assert claim(api, box) is None
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT state FROM jobs WHERE bot_id='ops'").fetchone()[0] == "queued"
        login_holds.try_now(c, "", login_holds.subscription_login(sid), "claude")
    assert claim(api, box)["attempt_credential"]["id"] == sid


def test_a_provider_capacity_hold_reports_busy_in_health(api):
    box, _, _ = plans(api, capacity=True)
    assert claim(api, box) is None
    assert "Primary plan: busy until" in wait(api)["text"]


def test_a_pool_does_not_run_on_another_owners_available_plan(api):
    box, _, _ = plans(api, pool=True, second=True, other_owner="ben-test")
    assert claim(api, box) is None
    assert "Ana's pool: next reset" in wait(api)["text"]


def test_a_held_pick_waits_even_with_a_local_login(api):
    box, _, _ = plans(api, signed_in=True)
    assert claim(api, box) is None
    assert "Primary plan: next reset" in wait(api)["text"]


def test_a_held_pool_reports_the_earliest_reset(api):
    box, _, other = plans(api, pool=True, second=True)
    earliest = "2999-11-21T09:00:00.000000Z"
    hold(api, other, earliest)
    assert claim(api, box) is None
    with api.app.state.store.read() as c:
        payer = agent_subscriptions.resolve(c, "ops", "claude", MODEL)
    assert payer and payer.until == H.parse_ts(earliest)
    assert "Ana's pool: next reset" in wait(api)["text"]


def test_a_paid_by_wait_is_explained_as_a_reset(api):
    box, _, _ = plans(api)
    assert claim(api, box) is None
    assert "next reset" in wait(api)["text"]
    assert "sign in" not in wait(api)["text"].lower()


def test_a_pool_uses_a_reconnected_plan_without_waiting_for_the_held_one(api):
    box, _, other = plans(api, pool=True, second=True)
    with api.app.state.store.transaction() as c:
        MS._status(c, other, "needs_reconnect", "Token expired")
    assert claim(api, box) is None
    assert "next reset" in wait(api)["text"]
    post(api, f"model-subscriptions/{other}", {"secret": OTHER, "confirm": True})
    assert claim(api, box)["attempt_credential"]["id"] == other


def test_a_member_cannot_see_paid_by_waits_for_an_agent_they_cannot_read(api):
    plans(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id='ana' WHERE bot='ops'")
        unrelated_task_managers(c, "ops")
        restrict(c, "ops", people=["ana"])
    assert "paid_by_wait" in checks(api)
    assert "paid_by_wait" not in checks(api, "cara-test")
