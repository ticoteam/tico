"""How much the CEO Bot does (backend/ceo_bot.py, backend/goal_asks.py; spec: Head of Product, CEO Bot, tests 3, 4, 8,
14, 16 and 18; v3 10.11). Every install starts at Passive, which keeps only the regular checks (the KPI pass and the
automatic goal colours); only the owner changes the level or turns the regular checks off, on their own click, and
each change is kept with who and when. Above Passive the CEO Bot asks for the vision first, asks for its proposed Why
lines as one item, starts its Monday note, and puts at most 5 items a day in a person's Needs you."""

from backend import ceo_bot, goals as G, routines
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_company import bot, propose_why, with_ceo_bot
from backend.tests.test_goal_asks import asks, goal_manager, raise_level, sync
from backend.tests.test_kpis import goal_with_kpi, log

OWNER = "human:ana"
REVIEW = "goal-manager:goals-review"


def level(api, n, token="ana-test", expected=200):
    return post(api, "ceo-bot/level", {"level": n}, token=token, expected=expected)


def via_assistant(api):
    api.app.state.store.settings.test_identities["ana-via"] = Identity(OWNER, "owner", "ana@acme.example",
                                                                       via="assistant", confirmed=True)
    return "ana-via"


def review_routine(api):
    """The Monday note as the bot declares it: paused until the server starts it."""
    with api.app.state.store.transaction() as c:
        routines.create(c, H.KEEPER, "goal-manager", {"title": "Weekly goals review", "cron": "0 9 * * 1",
                                                      "timezone": "America/Los_Angeles", "enabled": False},
                        key="goals-review")


def review_enabled(api):
    with api.app.state.store.read() as c:
        return bool(c.execute("SELECT coalesce(enabled,1) FROM schedule_config WHERE schedule_id=?",
                              (REVIEW,)).fetchone()[0])


def test_every_install_starts_at_passive_and_says_what_each_level_does(api):
    """Spec test 16 (v3): an install is at Passive, with its regular checks on. Each level has its one sentence; level
    3, not arrived yet, says it does what level 2 does until then."""
    with_ceo_bot(api)
    shown = get(api, "ceo-bot")
    assert "on" not in shown and shown["level"] == 1 and shown["level_history"] == []
    assert shown["regular_checks"] is True and shown["name"] == "CEO Bot"
    assert [(lv["level"], lv["name"], lv["ready"]) for lv in shown["levels"]] == \
        [(1, "Passive", True), (2, "Suggests actions", True), (3, "Acts on its own", False)]
    assert "regular checks" in shown["levels"][0]["sentence"] and "changes nothing" in shown["levels"][0]["sentence"]
    assert shown["levels"][2]["sentence"].endswith(ceo_bot.NOT_YET)
    assert shown["daily_cap"] == 5 and get(api, "ceo-bot", token="cara-test")["level"] == 1    # everyone reads it


def test_only_the_owner_changes_the_level_and_each_change_is_logged(api):
    """Spec test 18 (v3): the owner picks a level and the change is kept with who and when. No admin or member changes
    it, no bot, the CEO Bot included, nor the Assistant acting for the owner."""
    ceo = with_ceo_bot(api)
    other = bot(api, "ops")
    changed = level(api, 2)
    assert changed["level"] == 2 and changed["level_changed_by"] == OWNER and changed["level_changed_at"]
    [entry] = changed["level_history"]
    assert (entry["level"], entry["was"], entry["by"]) == (2, 1, OWNER) and entry["by_name"]
    for token in ("ben-test", "cara-test", ceo, other, via_assistant(api)):
        refused = level(api, 3, token=token, expected=403)
        assert "Only the owner" in str(refused)
    assert level(api, 3)["level"] == 3
    shown = get(api, "ceo-bot")
    assert shown["level"] == 3 and [(e["level"], e["by"]) for e in shown["level_history"]] == [(3, OWNER), (2, OWNER)]
    # The same level again changes nothing and logs nothing; a level outside 1-3 is refused.
    assert len(level(api, 3)["level_history"]) == 2
    level(api, 4, expected=422)
    level(api, 0, expected=422)


def test_only_the_owner_turns_the_regular_checks_off_and_off_they_hold_still(api):
    """v3: the regular checks are the KPI pass and the automatic goal colours. Only the owner turns them off, on their
    own click. Off, a reading changes no colour and the KPI pass is skipped; back on, every colour is worked out again
    at once."""
    ceo = with_ceo_bot(api)
    goal, kpi = goal_with_kpi(api, token="ben-test", owner="ben")
    log(api, kpi["id"], 10, token="ben-test", period_end=H.now())
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] == "red"
    for token in ("ben-test", "cara-test", ceo, bot(api, "ops"), via_assistant(api)):
        post(api, "ceo-bot/checks", {"on": False}, token=token, expected=403)
    off = post(api, "ceo-bot/checks", {"on": False})
    assert off["regular_checks"] is False and off["level"] == 1
    log(api, kpi["id"], 100, token="ben-test", period_end=H.now())
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] == "red"           # held still
    with api.app.state.store.transaction() as c:
        assert ceo_bot.held_routine(c, {"id": ceo_bot.PASS}) == "regular checks are off"
        assert ceo_bot.held_routine(c, {"id": REVIEW}) is None
        assert routines.no_input(c, api.app.state.store.settings, {"id": ceo_bot.PASS, "bot": "goal-manager",
                                                                      "routine_key": "kpi-pass", "playbook": ""},
                                 None) == "regular checks are off"
        assert G.refresh(c)["changed"] == []
    on = post(api, "ceo-bot/checks", {"on": True})
    assert on["regular_checks"] is True
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] != "red"           # worked out again at once
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT actor FROM events WHERE action='ceo_bot.checks' ORDER BY ts")] == \
            [OWNER, OWNER]
        assert ceo_bot.held_routine(c, {"id": ceo_bot.PASS}) is None


def test_no_vision_is_the_first_thing_it_asks_above_passive_and_saving_one_closes_it(api):
    """Spec test 3: with no vision, the CEO Bot's first item above Passive asks for it, at the top of the owner's list,
    and it closes by itself once a vision is saved. At Passive it is never asked."""
    goal_manager(api)
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    sync(api)
    assert asks(api, OWNER) == []                                           # Passive: nothing asked
    raise_level(api, 2)
    sync(api)
    vision = [a for a in asks(api, OWNER) if a["kind"] == "vision"]
    assert [(a["title"], a["status"]) for a in vision] == [("Tell your CEO Bot what this company is for", "open")]
    with api.app.state.store.read() as c:
        mine = H.tasks(c, owner=OWNER, status=H.ACTIVE_STATUSES)
    assert mine[0]["id"] == vision[0]["id"]                                 # first in the owner's queue
    assert [i["kind"] for i in get(api, "goals/needs-you")["items"]][0] == "vision"
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    with api.app.state.store.read() as c:
        assert H.task(c, vision[0]["id"])["status"] == "closed"
    assert all(i["kind"] != "vision" for i in get(api, "goals/needs-you")["items"])


def test_why_proposals_reach_the_owner_as_one_item(api):
    """Spec tests 4 and 14: the CEO Bot's Why proposals for several goals are one "These goals need a Why line" item,
    never one per goal, above Passive; it closes once every one is settled."""
    ceo = with_ceo_bot(api, level=2)
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    goals = [post(api, "goals", {"title": title, "owner": "company"})["goal"]["id"] for title in ("Grow rentals", "Cut churn")]
    first = propose_why(api, goals[0], "More rentals is more landlords on Tico.", ceo)
    second = propose_why(api, goals[1], "Landlords who stay are the vision.", ceo)
    sync(api)                                                               # the second joins the same item
    [why] = [a for a in asks(api, OWNER) if a["kind"] == "why"]
    assert why["title"] == "These goals need a Why line"
    assert "Grow rentals" in why["body"] and "Cut churn" in why["body"]
    [item] = [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "why"]
    assert {g["goal_id"] for g in item["goals"]} == set(goals)
    post(api, f"goal-proposals/{first['id']}/decide", {"decision": "confirm"})
    with api.app.state.store.read() as c:
        assert H.task(c, why["id"])["status"] == "open"                     # one still waits
    post(api, f"goal-proposals/{second['id']}/decide", {"decision": "confirm", "why": "Landlords who stay."})
    with api.app.state.store.read() as c:
        assert H.task(c, why["id"])["status"] == "closed"
        assert c.execute("SELECT count(*) FROM tasks WHERE title=?", (why["title"],)).fetchone()[0] == 1


def test_at_most_five_a_day_in_one_persons_needs_you_most_important_first(api):
    """Spec test 14 above Passive: whatever it has to ask, the CEO Bot puts at most 5 items a day in one person's Needs
    you, the vision first; the rest wait for the next day's room. The server refuses a sixth it files itself, for that
    person only."""
    ceo = with_ceo_bot(api, level=2)
    for n in range(7):
        post(api, "kpis", {"name": f"Measure {n}", "owner": "me", "cadence": "weekly"})
    sync(api)
    mine = asks(api, OWNER)
    assert len(mine) == 5 and mine[0]["kind"] == "vision"
    assert get(api, "ceo-bot")["asked_today"] == {OWNER: 5}
    shown = get(api, "goals/needs-you")["items"]                             # what waits for tomorrow is not shown
    assert len(shown) == 5 and all(i.get("task") for i in shown) and shown[0]["kind"] == "vision"
    post(api, "tasks", {"title": "One more question", "body": "Why?", "owner": OWNER}, token=ceo, expected=422)
    post(api, "tasks", {"title": "A question for Ben", "body": "Why?", "owner": "human:ben"}, token=ceo)
    sync(api)
    assert len(asks(api, OWNER)) == 5                                        # still 5 today
    # Tomorrow there is room again: the asks kept for then are filed (as if the five were filed yesterday).
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET created=? WHERE requester='bot:goal-manager' AND owner=?",
                  (H.shift(H.now(), days=-1), OWNER))
    sync(api)
    assert len(asks(api, OWNER)) == 8 and ceo_bot.room_today
    assert len(get(api, "goals/needs-you")["items"]) == 8
    assert get(api, "ceo-bot")["asked_today"] == {OWNER: 3, "human:ben": 1}
    assert get(api, "ceo-bot", token=ceo)["asked_today"] == {OWNER: 3, "human:ben": 1}
    assert get(api, "ceo-bot", token="cara-test")["asked_today"] == {}                 # a member: only their own


def test_raising_it_starts_the_monday_note_and_passive_pauses_only_what_it_started(api):
    """The Monday note (goals-review, declared paused) runs once the owner raises the level from Passive. Back at
    Passive it is paused again only if the server started it; a person's own choice stands."""
    goal_manager(api)
    review_routine(api)
    assert not review_enabled(api)
    level(api, 2)
    assert review_enabled(api)
    level(api, 3)                                                            # 2 to 3 leaves it as it is
    assert review_enabled(api)
    level(api, 1)
    assert not review_enabled(api)
    # Started by the raise, then paused and started again by a person: it is theirs now, and Passive leaves it.
    level(api, 2)
    with api.app.state.store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        routines.update(c, OWNER, REVIEW, {"enabled": True})
    level(api, 1)
    assert review_enabled(api)
    with api.app.state.store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": False})
    # Started by a person at Passive: raising and lowering leaves it running.
    with api.app.state.store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": True})
    level(api, 2)
    level(api, 1)
    assert review_enabled(api)


def test_a_routine_that_appears_after_the_raise_starts_once(api):
    """Above Passive, a Monday note whose routine appears later is started once by the hourly reconcile; a person who
    pauses it after that stays paused. At Passive the reconcile never starts it."""
    goal_manager(api)
    store = api.app.state.store
    level(api, 2)                                                             # no routine yet: nothing to start
    with store.transaction() as c:
        ceo_bot.reconcile(c)
    review_routine(api)
    with store.transaction() as c:
        ceo_bot.reconcile(c)
    assert review_enabled(api)
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        ceo_bot.reconcile(c)
    assert not review_enabled(api)


def test_at_passive_the_reconcile_never_starts_the_monday_note(api):
    goal_manager(api)
    review_routine(api)
    with api.app.state.store.transaction() as c:
        ceo_bot.reconcile(c)
    assert not review_enabled(api)


def test_a_monday_note_the_old_level_1_started_is_paused_at_passive_once(api):
    """Before v3, level 1 with the switch on started the Monday note. At upgrade that install is at Passive: the hourly
    reconcile pauses the note it started, unless a person touched it since."""
    import json
    goal_manager(api)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        routines.update(c, H.KEEPER, REVIEW, {"enabled": True})
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (ceo_bot.KEY, json.dumps(
            {"on": True, "by": OWNER, "review_armed": H.now(), "review_settled": True})))
        ceo_bot.reconcile(c)
    assert not review_enabled(api)
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": True})                 # a person's own choice
        ceo_bot.reconcile(c)
    assert review_enabled(api)
