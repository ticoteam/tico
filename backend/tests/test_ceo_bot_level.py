"""How much the CEO Bot does, and what it does at level 1 (backend/ceo_bot.py, backend/goal_asks.py; spec: Head of
Product, CEO Bot, tests 3, 4, 8, 14, 16 and 18). Every install starts at level 1; only the owner or an admin changes the
level, on their own click, and each change is kept with who and when. Where the switch is on, the CEO Bot asks for the
vision first, asks for its proposed Why lines as one item, starts its Monday note, and puts at most 5 items a day in a
person's Needs you."""

from backend import ceo_bot, routines
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_company import bot, propose_why, switched_on
from backend.tests.test_goal_asks import asks, goal_manager, sync

OWNER = "human:ana"


def level(api, n, token="ana-test", expected=200):
    return post(api, "ceo-bot/level", {"level": n}, token=token, expected=expected)


def via_assistant(api):
    api.app.state.store.settings.test_identities["ana-via"] = Identity(OWNER, "owner", "ana@acme.example",
                                                                       via="assistant", confirmed=True)
    return "ana-via"


def test_every_install_starts_at_level_1_and_says_what_each_level_does(api):
    """Spec test 16: after the upgrade, an install with the switch on is at level 1. Each level has its one sentence;
    the two that have not arrived yet say they do what level 1 does until then."""
    switched_on(api)
    shown = get(api, "ceo-bot")
    assert shown["on"] is True and shown["level"] == 1 and shown["level_history"] == []
    assert [(lv["level"], lv["name"], lv["ready"]) for lv in shown["levels"]] == \
        [(1, "Goals and tracking", True), (2, "Suggests actions", False), (3, "Acts on its own", False)]
    assert "never changes anyone's tasks" in shown["levels"][0]["sentence"]
    assert all(lv["sentence"].endswith(ceo_bot.NOT_YET) for lv in shown["levels"][1:])
    assert shown["daily_cap"] == 5 and get(api, "ceo-bot", token="cara-test")["level"] == 1    # everyone reads it


def test_only_the_owner_or_an_admin_changes_the_level_and_each_change_is_logged(api):
    """Spec test 18: the owner and admins pick a level and the change is kept with who and when. No bot changes it,
    the CEO Bot included, nor the Assistant acting for the owner, nor a member."""
    ceo = switched_on(api)
    other = bot(api, "ops")
    changed = level(api, 2)
    assert changed["level"] == 2 and changed["level_changed_by"] == OWNER and changed["level_changed_at"]
    [entry] = changed["level_history"]
    assert (entry["level"], entry["was"], entry["by"]) == (2, 1, OWNER) and entry["by_name"]
    assert level(api, 3, token="ben-test")["level"] == 3                                 # an admin
    for token in (ceo, other, via_assistant(api), "cara-test"):
        level(api, 1, token=token, expected=403)
    shown = get(api, "ceo-bot")
    assert shown["level"] == 3 and [(e["level"], e["by"]) for e in shown["level_history"]] == \
        [(3, "human:ben"), (2, OWNER)]
    # The same level again changes nothing and logs nothing; a level outside 1-3 is refused.
    assert len(level(api, 3)["level_history"]) == 2
    level(api, 4, expected=422)
    level(api, 0, expected=422)


def test_with_the_switch_off_there_is_no_level_to_change(api):
    """Spec test 17's guard: off, the level can't be changed by anyone, and it reads 1."""
    goal_manager(api)
    ceo = bot(api, "goal-manager")
    for token in ("ana-test", "ben-test"):
        level(api, 2, token=token, expected=409)
    for token in (ceo, "cara-test"):
        level(api, 2, token=token, expected=403)
    assert get(api, "ceo-bot")["level"] == 1 and get(api, "ceo-bot")["level_history"] == []


def test_no_vision_is_the_first_thing_it_asks_and_saving_one_closes_it(api):
    """Spec test 3: with no vision, the CEO Bot's first item asks for it, at the top of the owner's list, and it
    closes by itself once a vision is saved. Off, it is never asked (test 17)."""
    goal_manager(api)
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    assert [a["kind"] for a in asks(api, OWNER)] == ["kpi_stale"]          # off: as before, no vision ask
    assert post(api, "ceo-bot", {"on": True})["on"] is True
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
    never one per goal; it closes once every one is settled."""
    ceo = switched_on(api)
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
    """Spec test 14 at level 1: whatever it has to ask, the CEO Bot puts at most 5 items a day in one person's Needs
    you, the vision first; the rest wait for the next day's room. The server refuses a sixth it files itself, for that
    person only. Off, the Goal Manager asks as before (test 17)."""
    ceo = switched_on(api)
    for n in range(7):
        post(api, "kpis", {"name": f"Measure {n}", "owner": "me", "cadence": "weekly"})
    sync(api)
    mine = asks(api, OWNER)
    assert len(mine) == 5 and mine[0]["kind"] == "vision"
    assert get(api, "ceo-bot")["asked_today"] == {OWNER: 5}
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
    assert get(api, "ceo-bot")["asked_today"] == {OWNER: 3, "human:ben": 1}
    assert get(api, "ceo-bot", token=ceo)["asked_today"] == {OWNER: 3, "human:ben": 1}
    assert get(api, "ceo-bot", token="cara-test")["asked_today"] == {}                 # a member: only their own


def test_with_the_switch_off_the_goal_manager_asks_as_before(api):
    goal_manager(api)
    for n in range(7):
        post(api, "kpis", {"name": f"Measure {n}", "owner": "me", "cadence": "weekly"})
    assert [a["kind"] for a in asks(api, OWNER)] == ["kpi_stale"] * 7


def test_switching_on_starts_the_monday_note_and_off_pauses_only_what_it_started(api):
    """The Monday note (goals-review, declared paused) runs once the switch is on. Off, it is paused again only if
    the switch started it; a person's own choice stands."""
    goal_manager(api)
    sid = "goal-manager:goals-review"
    with api.app.state.store.transaction() as c:
        routines.create(c, H.KEEPER, "goal-manager", {"title": "Weekly goals review", "cron": "0 9 * * 1",
                                                      "timezone": "America/Los_Angeles", "enabled": False},
                        key="goals-review")
    enabled = lambda: bool(get(api, "ceo-bot") and api.app.state.store.read().__enter__().execute(
        "SELECT coalesce(enabled,1) FROM schedule_config WHERE schedule_id=?", (sid,)).fetchone()[0])
    assert not enabled()
    assert post(api, "ceo-bot", {"on": True})["on"] is True
    assert enabled()
    assert post(api, "ceo-bot", {"on": False})["on"] is False
    assert not enabled()
    # Started by a person while the switch was off: switching on and off leaves it running.
    with api.app.state.store.transaction() as c:
        routines.update(c, OWNER, sid, {"enabled": True})
    post(api, "ceo-bot", {"on": True})
    post(api, "ceo-bot", {"on": False})
    assert enabled()
