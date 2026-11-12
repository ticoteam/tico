"""A computer given a person's plans for a runtime (backend/model_subscriptions.py `_pick`, `settle`): every plan that
person owns for it, used one at a time. The plan in use stays while it has usage left; when it is held at its limit or
needs reconnecting, the next turn moves to the available plan whose weekly reset comes soonest, in a fresh session.
Spec: Models, "Give a computer a person's plans" (task #226)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import login_holds
from backend import model_subscriptions as MS
from backend.store import H
from backend.tests.helpers.api import claim, get, post, put
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import OTHER, TOKEN, add, codex_file, company, machine, on

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

    def usage(c, sub, runtime, ctx):
        if sub["id"] not in known:
            return real(c, sub, runtime, ctx)
        used, days = known[sub["id"]]
        at = _in(days)
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


def give_person(api, m, person="ana", runtime="claude", confirm=False, token="ana-test", expected=200):
    return put(
        api,
        "computer-subscriptions",
        {"runner_id": m["runner_id"], "runtime": runtime, "person": person, "confirm": confirm},
        token,
        expected=expected,
    )


def cell(api, m, runtime="claude"):
    view = get(api, "computer-subscriptions")
    return next(a for a in view["assignments"] if a["runner_id"] == m["runner_id"] and a["runtime"] == runtime)


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


def epoch(api, bot="ops"):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT epoch FROM bot_session_epochs WHERE bot=?", (bot,)).fetchone()
    return row[0] if row else 0


def moves(api):
    with api.app.state.store.read() as c:
        return [
            json.loads(r[0])
            for r in c.execute("SELECT detail FROM events WHERE action='subscription.set_moved' ORDER BY rowid")
        ]


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


def test_a_persons_plans_run_on_the_soonest_reset_with_usage_left_then_move_on_when_it_is_held(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")  # no login of its own: only the plans sign it in
    given = give_person(api, m)
    assert (given["person"], given["subscription_id"]) == ("human:ana", p2)
    # AC-1: the turn runs on Plan 2, and the cell says why Plan 1 is passed over.
    first = turn(api, m)
    assert signed_with(api, first) == p2
    state = cell(api, m)["set"]
    assert (state["on"], state["on_name"], state["plans"]) == (p2, "Plan 2", 3)
    assert (state["why"]["kind"], state["why"]["plan"]) == ("held", "Plan 1")
    finish(api, m, first)
    # AC-3: the readings shift so that Plan 3 would sort first; turns stay on Plan 2 while it has usage left.
    readings[p3] = (10, 0.5)
    again = turn(api, m)
    assert signed_with(api, again) == p2 and moves(api) == []
    finish(api, m, again)
    # AC-2: Plan 2 is held. The next turn runs on Plan 3, in a fresh session, and the cell says why.
    before = epoch(api)
    hold(api, p2, days=2, since=H.shift(H.now(), seconds=5))
    moved = turn(api, m)
    assert signed_with(api, moved) == p3
    assert epoch(api) > before
    [event] = moves(api)
    assert (event["from"], event["to"], event["why"], event["person"]) == (p2, p3, "held", "human:ana")
    state = cell(api, m)["set"]
    assert (state["on"], state["why"]["kind"], state["why"]["plan"]) == (p3, "held", "Plan 2")
    finish(api, m, moved)
    # Staying on Plan 3 is not a move: no fresh session for the turn after.
    after = epoch(api)
    finish(api, m, turn(api, m))
    assert epoch(api) == after and len(moves(api)) == 1


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
    give_person(api, m)
    found = turn(api, m)
    assert signed_with(api, found) == p2
    before = epoch(api)
    limit_met(api, m, found)
    # AC-2 as a runner reports it: the work Plan 2's limit interrupted does not wait for Plan 2's reset while Plan 3
    # has usage left. The bot is not Rate limited, and its next turn (no new message) runs on Plan 3, fresh.
    with api.app.state.store.read() as c:
        assert (H.status(c, "ops") or {}).get("state") != "limited"
    again = claim(api, m)
    assert again and again["bot"] == "ops"
    assert signed_with(api, again) == p3
    assert epoch(api) > before
    [event] = moves(api)
    assert (event["from"], event["to"], event["why"]) == (p2, p3, "held")


def test_a_limit_with_every_other_plan_held_still_waits_for_the_reset(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    hold(api, p3, days=3)
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    found = turn(api, m)
    assert signed_with(api, found) == p2
    limit_met(api, m, found)
    with api.app.state.store.read() as c:
        assert (H.status(c, "ops") or {}).get("state") == "limited"
    assert claim(api, m) is None


def test_when_every_plan_is_held_the_computers_own_login_signs_in_and_the_first_reset_brings_its_plan_back(
    api, readings
):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, signed_in=True, label="Studio Mac")
    give_person(api, m)
    hold(api, p2, days=2)
    hold(api, p3, days=3)
    # AC-4: as a held single plan does today, the turn runs on the computer's own login.
    found = turn(api, m)
    assert signed_with(api, found) is None
    state = cell(api, m)["set"]
    assert state["on"] is None and state["why"]["kind"] == "all_held"
    with api.app.state.store.read() as c:
        first_end = login_holds.get(c, "", login_holds.subscription_login(p1), "claude")["until"]
    assert state["why"]["until"] == first_end
    finish(api, m, found)
    # Plan 1's hold ends first: the next turn uses it.
    with api.app.state.store.transaction() as c:
        login_holds.try_now(c, "", login_holds.subscription_login(p1), "claude")
    assert signed_with(api, turn(api, m)) == p1


def test_a_plan_with_no_reading_comes_after_every_plan_with_one(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p4 = add(api, secret=FOURTH, name="Plan 4")["id"]  # the oldest, and never read
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    found = turn(api, m)
    assert signed_with(api, found) == p2  # not Plan 4, though it is the oldest
    finish(api, m, found)
    # AC-5: Plans 2 and 3 held, Plan 1 still held: the next turn runs on Plan 4.
    hold(api, p2, days=2)
    hold(api, p3, days=3)
    assert signed_with(api, turn(api, m)) == p4


def test_a_plan_that_needs_reconnecting_is_skipped_and_the_cell_says_so(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    with api.app.state.store.transaction() as c:
        MS._status(c, p2, "needs_reconnect", "Token expired")
    # AC-6: skipped; Plan 3 signs in, and the cell names the plan that needs reconnecting.
    found = turn(api, m)
    assert signed_with(api, found) == p3
    state = cell(api, m)["set"]
    assert (state["on"], state["why"]["kind"], state["why"]["plan"]) == (p3, "reconnect", "Plan 2")
    assert moves(api)[-1]["why"] == "needs_reconnect"


def test_a_plan_the_person_adds_joins_their_set_and_one_given_to_someone_else_leaves_it(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    hold(api, p2, days=2)
    # AC-7: Plan 3 now belongs to Ben; Ana adds Plan 5. Nothing is given to the computer again.
    post(api, f"model-subscriptions/{p3}", {"owner_id": "human:ben"})
    p5 = add(api, secret="sk-ant-oat01-" + "e" * 24, name="Plan 5")["id"]
    found = turn(api, m)
    assert signed_with(api, found) == p5
    assert cell(api, m)["set"]["plans"] == 3  # Plans 1, 2 and 5
    finish(api, m, found)
    # The plan in use given to someone else leaves the set at the next turn.
    with api.app.state.store.transaction() as c:
        login_holds.try_now(c, "", login_holds.subscription_login(p2), "claude")
    post(api, f"model-subscriptions/{p5}", {"owner_id": "human:ben"})
    assert signed_with(api, turn(api, m)) == p2
    assert moves(api)[-1]["why"] == "left_set"


def test_removing_the_plan_in_use_keeps_the_persons_set_on_the_computer(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    get(api, f"model-subscriptions", "ana-test")
    from backend.tests.helpers.api import headers

    api.delete(f"/api/v2/model-subscriptions/{p2}", headers=headers("ana-test"))
    assert cell(api, m)["person"] == "human:ana"
    assert signed_with(api, turn(api, m)) == p3
    assert moves(api)[-1]["why"] == "removed"


def test_only_a_person_who_owns_a_plan_for_that_tool_is_offered_or_accepted(api):
    setup(api)
    company(api)
    add(api, name="Ana Max")
    add(api, kind="anthropic_key", secret="sk-ant-api03-" + "k" * 24, name="Key")
    add(api, kind="codex_login", secret=codex_file(), name="Ana ChatGPT", token="ben-test")  # owned by Ben
    m = machine(api)
    people = {p["id"]: p for p in get(api, "computer-subscriptions")["people"]}
    assert people["human:ana"].get("claude") == 1 and "codex" not in people["human:ana"]
    assert people["human:ben"].get("codex") == 1 and "claude" not in people["human:ben"]
    # AC-9: Ana owns no Codex plan, Ben no Claude one; an API key belongs to nobody's set.
    assert give_person(api, m, "ana", runtime="codex", expected=422)["error"]["code"] == "person"
    assert give_person(api, m, "ben", runtime="claude", expected=422)["error"]["code"] == "person"
    assert give_person(api, m, "bot:ops", expected=422)["error"]["code"] == "person"
    both = {"runner_id": m["runner_id"], "runtime": "claude", "person": "ana", "subscription": "x"}
    assert put(api, "computer-subscriptions", both, expected=422)["error"]["code"] == "person"
    # Owners and admins only, as for a single subscription.
    give_person(api, m, token="cara-test", expected=403)


def test_switching_a_computer_with_runs_going_to_a_persons_plans_asks_first(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    put(api, "computer-subscriptions", {"runner_id": m["runner_id"], "runtime": "claude", "subscription": p3})
    found = turn(api, m)
    assert signed_with(api, found) == p3
    # AC-10: the same confirm, with the count of runs it stops, as a change of subscription.
    refused = give_person(api, m, expected=409)
    assert refused["error"]["code"] == "confirm_restart" and refused["error"]["running"] == 1
    assert give_person(api, m, confirm=True)["stopped"] == 1
    # Giving the same person's plans again changes nothing.
    assert give_person(api, m)["stopped"] == 0
    # Taking it off.
    off = put(api, "computer-subscriptions", {"runner_id": m["runner_id"], "runtime": "claude", "subscription": None})
    assert off["subscription_id"] is None and get(api, "computer-subscriptions")["assignments"] == []


def test_the_plan_list_orders_each_owners_plans_and_names_the_computers_using_one(api, readings):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    p1, p2, p3 = three_plans(api, readings)
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    rows = {s["id"]: s for s in get(api, "model-subscriptions")["subscriptions"]}
    # AC-8's data: Plan 2 in use on Studio Mac and first, Plan 3 next, Plan 1 held (no place in the order).
    assert [(rows[p]["order"], rows[p]["in_use_on"]) for p in (p2, p3, p1)] == [
        (1, [m["runner_id"]]),
        (2, []),
        (None, []),
    ]
    # A plan in a person's set counts as on each computer given the set.
    assert all(m["runner_id"] in rows[p]["computers"] for p in (p1, p2, p3))


def test_a_reading_a_turn_reports_on_a_plan_in_a_persons_set_is_that_plans_usage(api):
    setup(api)
    company(api)
    on(api, "ops", "claude", "claude-sonnet-5-5")
    sid = add(api, name="Plan 2")["id"]
    m = machine(api, label="Studio Mac")
    give_person(api, m)
    found = turn(api, m)
    assert signed_with(api, found) == sid
    reported = datetime.now(timezone.utc)
    post(
        api,
        "runners/heartbeat",
        {
            "version": "test",
            "platform": "test",
            "capabilities": [MS.CAPABILITY, "computer_logins_v1"],
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
        },
        token=m["token"],
    )
    usage = next(s for s in get(api, "model-subscriptions")["subscriptions"] if s["id"] == sid)["usage"]
    assert (usage["window"], usage["used_pct"], usage["weekly_used_pct"]) == ("week", 30, 30)


def test_the_person_migration_adds_the_column_and_skips_a_new_file():
    import importlib
    import sqlite3

    step = importlib.import_module("backend.migrations.20261112190000_computer_subscription_person")
    fresh = sqlite3.connect(":memory:")
    step.up(fresh)  # no table yet: SCHEMA makes it with the column
    assert not fresh.execute("SELECT 1 FROM sqlite_master WHERE name='computer_subscriptions'").fetchone()
    old = sqlite3.connect(":memory:")
    old.execute(
        "CREATE TABLE computer_subscriptions(runner_id TEXT NOT NULL, runtime TEXT NOT NULL, subscription_id TEXT "
        "NOT NULL, set_by TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(runner_id, runtime))"
    )
    old.execute("INSERT INTO computer_subscriptions VALUES('r','claude','s','human:ana','2026-11-12')")
    step.up(old)
    step.up(old)  # a second start changes nothing
    assert old.execute("SELECT subscription_id, person FROM computer_subscriptions").fetchall() == [("s", "")]


def test_settings_search_finds_the_picker_by_the_name_of_a_person_who_owns_a_plan(api):
    setup(api)
    company(api)

    def found(q):
        return [s["key"] for s in get(api, f"settings/catalog?q={q}")["settings"]]

    with api.app.state.store.read() as c:
        name = H.human(c, "ana")["name"]
    first = name.split()[0]
    # AC-13: a name finds the picker only once that person owns a plan to give.
    assert "models.computer_subscription" not in found(first)
    add(api, name="Plan 1")
    assert "models.computer_subscription" in found(first)
    assert "models.computer_subscription" in found("failover")
