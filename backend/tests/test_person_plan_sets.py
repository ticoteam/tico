"""Paid by pools choose the compatible plan with the soonest reset and more than 0.5% left."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import agent_subscriptions, login_holds
from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.api import claim, get, headers, post
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import (
    OTHER,
    TOKEN,
    add,
    company,
    machine,
    on,
    pick,
)

THIRD = "sk-ant-oat01-" + "c" * 24
FOURTH = "sk-ant-oat01-" + "d" * 24


def _in(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture
def readings(monkeypatch):
    """Each plan's weekly reading, set by the test: {plan id: (used percent, resets in days)}. A plan not named has no
    reading. The readings themselves come from the computers' reports (`_plan_usage`); this pins only the rule."""
    known = {}
    real = MS._plan_usage

    def usage(c, sub, runtime, ctx, windows=None):
        if sub["id"] not in known:
            return real(c, sub, runtime, ctx, windows=windows)
        used, days = known[sub["id"]]
        at = _in(days)
        if windows is not None:
            windows.append(("week", "", used, H.parse_ts(at), datetime.now(timezone.utc)))
        return {
            "window": "week",
            "used_pct": used,
            "cost_usd_month": None,
            "resets_at": at,
            "weekly_resets_at": at,
            "weekly_used_pct": used,
        }

    monkeypatch.setattr(MS, "_plan_usage", usage)
    return known


def hold(api, sid, days=1, runtime="claude", since=None):
    """A plan held at its usage limit until `days` from now, as a limit met on it leaves it."""
    with api.app.state.store.transaction() as c:
        login_holds._save(
            c,
            {
                "runner_id": "",
                "profile": login_holds.subscription_login(sid),
                "runtime": runtime,
                "until": H.shift(H.now(), seconds=int(days * 86400)),
                "since": since or H.now(),
                "error": "You've hit your limit",
            },
        )


def turn(api, m):
    post(api, "chat/ops", {"text": "Summarize the Acme account."})
    found = claim(api, m)
    assert found and found["bot"] == "ops"
    return found


def signed_with(api, found):
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT subscription_id FROM attempt_subscriptions WHERE attempt_id=?", (found["id"],)
        ).fetchone()
    return row[0] if row else None


def finish(api, m, found):
    post(api, f"attempts/{found['id']}/started", {"thread_id": "t-" + found["id"]}, m["token"])
    post(api, f"attempts/{found['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, m["token"])


def three_plans(api, readings):
    """The spec's example: Ana owns Plans 1, 2 and 3 for Claude Code. Plan 1 is held until it resets in a day; Plan 2
    resets in 2 days with 50% left; Plan 3 in 3 days with 90% left. Added newest first, so the oldest plan is not the
    rule's pick by age alone."""
    p3 = add(api, secret=THIRD, name="Plan 3")["id"]
    p2 = add(api, secret=OTHER, name="Plan 2")["id"]
    p1 = add(api, secret=TOKEN, name="Plan 1")["id"]
    readings.update({p1: (100, 1), p2: (50, 2), p3: (10, 3)})
    hold(api, p1, days=1)
    return p1, p2, p3


def test_a_pool_rechecks_soonest_reset_and_strictly_more_than_half_a_percent_each_turn(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    _, p2, p3 = three_plans(api, readings)
    m = machine(api)
    pick(api, "ops", "claude", person="ana")
    first = turn(api, m)
    assert signed_with(api, first) == p2
    finish(api, m, first)
    readings[p3] = (99.5, 0.5)
    second = turn(api, m)
    assert signed_with(api, second) == p2
    finish(api, m, second)
    readings[p3] = (99.49, 0.5)
    third = turn(api, m)
    assert signed_with(api, third) == p3
    finish(api, m, third)


def test_pool_rotation_preserves_the_agents_claude_session(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    _, p2, p3 = three_plans(api, readings)
    m = machine(api, caps=("model_subscriptions_v1", agent_subscriptions.CAPABILITY, MS.RESUME_CAPABILITY))
    pick(api, "ops", "claude", person="ana")
    first = turn(api, m)
    assert signed_with(api, first) == p2
    finish(api, m, first)
    hold(api, p2, days=2, since=H.shift(H.now(), seconds=5))
    second = turn(api, m)
    assert signed_with(api, second) == p3
    with api.app.state.store.read() as c:
        session = c.execute("SELECT thread_id FROM bot_sessions WHERE bot='ops' AND runtime='claude'").fetchone()
    assert session and session["thread_id"] == "t-" + first["id"]


def test_an_older_runner_waits_instead_of_using_its_own_login_for_a_pool(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    add(api)
    m = machine(api, signed_in=True, caps=("model_subscriptions_v1",))
    pick(api, "ops", "claude", person="ana")
    post(api, "chat/ops", {"text": "Summarize the account."})
    assert claim(api, m) is None
    wait = next(check for check in get(api, "health")["checks"] if check["id"] == "paid_by_wait")
    assert "Update Test Mac" in str(wait["items"])


def limit_met(api, m, found):
    """The turn meets its plan's usage limit as a runner reports it: a limit error naming a reset, then a limited end."""
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    notice = f"You've hit your limit · resets {soon:%-I:%M}{soon:%p} (UTC)".replace("AM", "am").replace("PM", "pm")
    post(api, f"attempts/{found['id']}/started", {"thread_id": "t-" + found["id"]}, m["token"])
    post(
        api,
        f"attempts/{found['id']}/events",
        {"events": [{"seq": 1, "kind": "error", "payload": {"error": notice, "limit": True}}]},
        m["token"],
    )
    post(
        api,
        f"attempts/{found['id']}/complete",
        {"outcome": "failed", "last_seq": 1, "limited": True, "text": ""},
        m["token"],
    )


def test_a_plan_that_runs_out_in_a_turn_hands_the_held_work_to_the_next_plan_at_once(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    found = turn(api, m)
    assert signed_with(api, found) == p2
    limit_met(api, m, found)
    again = claim(api, m)
    assert again and again["job_id"] == found["job_id"]
    assert signed_with(api, again) == p3


def test_a_limit_with_every_other_plan_held_still_waits_for_the_reset(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    hold(api, p3, days=3)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    found = turn(api, m)
    assert signed_with(api, found) == p2
    limit_met(api, m, found)
    with api.app.state.store.read() as c:
        assert (H.status(c, "ops") or {}).get("state") == "limited"
    assert claim(api, m) is None


def test_when_all_plans_are_held_the_agent_waits_even_with_a_local_login(api, readings):
    setup(api)
    company(api)
    model = "claude-sonnet-5-5"
    on(api, "ops", "claude", model)
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, signed_in=True)
    pick(api, "ops", "claude", person="ana")
    hold(api, p2, days=2)
    hold(api, p3, days=3)
    post(api, "chat/ops", {"text": "Summarize the account."})
    assert claim(api, m) is None
    with api.app.state.store.transaction() as c:
        first_end = login_holds.get(c, "", login_holds.subscription_login(p1), "claude")["until"]
        payment = agent_subscriptions.resolve(c, "ops", "claude", model)
        assert payment and payment.until == H.parse_ts(first_end)
        login_holds.try_now(c, "", login_holds.subscription_login(p1), "claude")
    readings[p1] = (0, 1)
    assert signed_with(api, claim(api, m)) == p1


def test_a_plan_with_no_reading_comes_after_every_plan_with_one(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p4 = add(api, secret=FOURTH, name="Plan 4")["id"]  # the oldest, and never read
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    found = turn(api, m)
    assert signed_with(api, found) == p2  # not Plan 4, though it is the oldest
    finish(api, m, found)
    # AC-5: Plans 2 and 3 held, Plan 1 still held: the next turn runs on Plan 4.
    hold(api, p2, days=2)
    hold(api, p3, days=3)
    assert signed_with(api, turn(api, m)) == p4


def test_a_plan_that_needs_reconnecting_is_skipped_but_still_lists_its_agents(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    with api.app.state.store.transaction() as c:
        MS._status(c, p2, "needs_reconnect", "Token expired")
    found = turn(api, m)
    assert signed_with(api, found) == p3
    row = next(plan for plan in get(api, "model-subscriptions")["subscriptions"] if plan["id"] == p2)
    assert row["status"] == "needs_reconnect" and "ops" in {agent["id"] for agent in row["agents"]}


def test_a_plan_the_person_adds_joins_their_set_and_ownership_cannot_transfer(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    hold(api, p2, days=2)
    assert post(api, f"model-subscriptions/{p3}", {"owner_id": "human:ben"}, expected=422)["error"]["code"] == "owner"
    hold(api, p3, days=3)
    p5 = add(api, secret="sk-ant-oat01-" + "e" * 24, name="Plan 5")["id"]
    assert signed_with(api, turn(api, m)) == p5


def test_removing_a_pool_member_keeps_the_agents_pick(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    api.delete(f"/api/v2/model-subscriptions/{p2}", headers=headers("ana-test"))
    assert get(api, "bots/ops/subscriptions")["subscriptions"]["claude"]["person"] == "human:ana"
    assert signed_with(api, turn(api, m)) == p3


def test_an_empty_pool_is_a_valid_pick_but_a_non_person_and_other_owners_pool_are_refused(api):
    setup(api)
    company(api)
    model = "claude-sonnet-5-5"
    on(api, "ops", "claude", model)
    add(api, kind="anthropic_key", secret="sk-ant-api03-" + "k" * 24, name="Key")
    m = machine(api)
    pick(api, "ops", "claude", person="ana")
    post(api, "chat/ops", {"text": "Summarize the account."})
    assert claim(api, m) is None
    with api.app.state.store.read() as c:
        payment = agent_subscriptions.resolve(c, "ops", "claude", model)
        assert payment and "no provider for Claude" in payment.reason
    pick(api, "ops", "claude", person="bot:ops", expected=422)
    pick(api, "ops", "claude", person="ana", token="cara-test", expected=403)


def test_a_live_turn_keeps_its_exact_plan_when_paid_by_switches_to_a_pool(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    _, p2, p3 = three_plans(api, readings)
    m = machine(api)
    pick(api, "ops", "claude", p3)
    found = turn(api, m)
    pick(api, "ops", "claude", person="ana")
    assert signed_with(api, found) == p3
    assert not post(api, f"attempts/{found['id']}/renew", {}, m["token"]).get("stop")
    finish(api, m, found)
    assert signed_with(api, turn(api, m)) == p2


def test_the_plan_list_names_active_computers_and_agents_selecting_the_pool(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    running = turn(api, m)
    assert signed_with(api, running) == p2
    rows = {s["id"]: s for s in get(api, "model-subscriptions")["subscriptions"]}
    # AC-8's data: Plan 2 in use on Studio Mac and first, Plan 3 next, Plan 1 held (no place in the order).
    assert [(rows[p]["order"], rows[p]["in_use_on"]) for p in (p2, p3, p1)] == [
        (1, [m["runner_id"]]),
        (2, []),
        (None, []),
    ]
    # The pool pick is visible on every member plan, including the held one.
    assert all("ops" in {agent["id"] for agent in rows[p]["agents"]} for p in (p1, p2, p3))


def test_a_pools_turn_report_stays_with_its_plan_after_a_local_login_report(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, name="Plan 2")["id"]
    m = machine(api, label="Studio Mac")
    pick(api, "ops", "claude", person="ana")
    found = turn(api, m)
    assert signed_with(api, found) == sid
    reported = datetime.now(timezone.utc)
    heartbeat = {
        "version": "test",
        "platform": "test",
        "capabilities": ["model_subscriptions_v1", "computer_logins_v1", agent_subscriptions.CAPABILITY],
        "readiness": {
            "schema_version": 1,
            "bots": {"ops": {"runtime": "claude", "ready": True}},
            "computer_profile": "own",
            "runtimes": {"claude": {"installed": True, "authenticated": "missing"}},
        },
        "profiles": [
            {
                "name": "own",
                "runtimes": {
                    "claude": {
                        "signed_in": True,
                        "weekly": {
                            "used_percent": 30,
                            "resets_at": (reported + timedelta(days=2)).isoformat(),
                            "reported_at": reported.isoformat(),
                            "origin": "subscription",
                            "subscription_id": sid,
                        },
                    }
                },
            }
        ],
    }
    post(api, "runners/heartbeat", heartbeat, token=m["token"])
    usage = next(s for s in get(api, "model-subscriptions")["subscriptions"] if s["id"] == sid)["usage"]
    assert (usage["window"], usage["used_pct"], usage["weekly_used_pct"]) == ("week", 30, 30)

    heartbeat["profiles"][0]["runtimes"]["claude"]["weekly"] = {
        "origin": "login",
        "used_percent": 90,
        "resets_at": (reported + timedelta(days=2)).isoformat(),
        "reported_at": (reported + timedelta(microseconds=1)).isoformat(),
    }
    post(api, "runners/heartbeat", heartbeat, token=m["token"])
    assert next(s for s in get(api, "model-subscriptions")["subscriptions"] if s["id"] == sid)["usage"] == usage


def test_settings_search_finds_the_picker_by_the_name_of_a_person_who_owns_a_plan(api):
    setup(api)
    company(api)

    def found(q):
        return [s["key"] for s in get(api, f"settings/catalog?q={q}")["settings"]]

    with api.app.state.store.read() as c:
        name = H.human(c, "ana")["name"]
    first = name.split()[0]
    # AC-13: a name finds the picker only once that person owns a plan to give.
    assert "models.agent_subscription" not in found(first)
    add(api, name="Plan 1")
    assert "models.agent_subscription" in found(first)
    assert "models.agent_subscription" in found("pool")
