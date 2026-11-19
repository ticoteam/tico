"""A computer whose given plans are all held at their usage limit (backend/model_subscriptions.py `plan_hold`): Health and
the bot's readiness name the plan, its limit and its reset, and the one change that runs its turns now. They never ask
for a sign-in or say the computer has no plan (task #2370: one plan pinned to two computers met its weekly limit)."""

from backend import login_holds
from backend import model_subscriptions as MS
from backend import readiness
from backend.store import H
from backend.tests.helpers.api import get, put
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import OTHER, add, company, give, machine, on

UNTIL = "2999-11-22T09:00:00.000000Z"


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


def checks(api):
    return {row["id"]: row for row in get(api, "health", "ana-test")["checks"]}


def ready(api):
    with api.app.state.store.read() as c:
        return readiness.can_run(c, "ops", "claude")


def pinned(api, *, second=True):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan = add(api, name="Matheus")["id"]
    other = add(api, secret=OTHER, name="Spare")["id"] if second else None
    m = machine(api)  # Claude not signed in on the computer itself
    give(api, m, "claude", plan)
    hold(api, plan)
    return m, plan, other


def test_a_computer_whose_only_plan_is_held_says_so_and_offers_its_owners_plans_in_turn(api):
    m, plan, _ = pinned(api)
    found = checks(api)
    assert "computer_signin" not in found
    line = found["computer_plan_held"]["summary"]
    assert "Test Mac's only Claude plan, Matheus, is at its weekly usage limit until Nov 22 09:00 UTC" in line
    with api.app.state.store.read() as c:
        owner = MS._name_of(c, MS.row(c, plan)["owner_id"])
    assert f"Give Test Mac {owner}'s plans, in turn, in Settings > Computers" in line
    assert "sign in" not in line.lower()
    assert [f["tab"] for f in found["computer_plan_held"]["fixes"]] == ["devices"]
    verdict = ready(api)
    assert verdict["can_run"] is False and not verdict.get("sign_in")
    assert "Matheus" in verdict["problem"] and "has no" not in verdict["problem"]
    assert verdict["bot_problems"] == [verdict["problem"]]


def test_a_held_plan_with_no_other_plan_left_says_to_add_one(api):
    pinned(api, second=False)
    line = checks(api)["computer_plan_held"]["summary"]
    assert "Add a Claude plan with usage left in Settings > Models" in line
    assert [f["tab"] for f in checks(api)["computer_plan_held"]["fixes"]] == ["models"]


def test_another_owners_plan_with_usage_left_is_offered_by_name(api):
    _, _, other = pinned(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE model_subscriptions SET owner_id='human:someone-else' WHERE id=?", (other,))
    assert "Give Test Mac Spare, which has usage left" in checks(api)["computer_plan_held"]["summary"]


def test_a_held_plan_is_not_named_while_the_computers_own_login_runs_its_turns(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    plan = add(api, name="Matheus")["id"]
    m = machine(api, signed_in=True)
    give(api, m, "claude", plan)
    hold(api, plan)
    assert "computer_plan_held" not in checks(api)


def test_a_person_whose_plans_are_all_held_names_the_first_reset_and_another_plan(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    first, second = add(api, name="Plan A")["id"], add(api, secret=OTHER, name="Plan B")["id"]
    m = machine(api)
    with api.app.state.store.read() as c:
        owner = MS.row(c, first)["owner_id"]
    put(api, "computer-subscriptions", {"runner_id": m["runner_id"], "runtime": "claude", "person": owner})
    hold(api, first, "2999-11-21T09:00:00.000000Z")
    hold(api, second)
    found = checks(api)
    assert "computer_signin" not in found
    line = found["computer_plan_held"]["summary"]
    assert "each is at its usage limit; the first to reset is Plan A until Nov 21 09:00 UTC" in line
    assert "Add a Claude plan with usage left" in line
