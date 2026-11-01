"""How much the Strategy Bot does (backend/strategy_bot.py, backend/goal_asks.py; spec: Head of Product, Strategy Bot,
tests 3, 4, 8, 14, 16 and 18; v3 10.11). Every install starts at Suggests actions until its owner chooses a level. At
Passive the Strategy Bot keeps only the regular checks (the KPI pass and the automatic goal colours); only the owner
changes the level or turns the regular checks off, on their own click, and each change is kept with who and when. Above
Passive the Strategy Bot asks for the vision first, asks for its proposed Why lines as one item, starts its Monday note,
and puts at most 5 items a day in a person's Needs you."""

from backend import strategy_bot, strategy_suggest, goals as G, routines
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_company import bot, propose_why, with_strategy_bot
from backend.tests.test_goal_asks import asks, goal_manager, raise_level, sync
from backend.tests.test_kpis import goal_with_kpi, log

OWNER = "human:ana"
REVIEW = "goal-manager:goals-review"


def level(api, n, token="ana-test", expected=200):
    return post(api, "strategy-bot/level", {"level": n}, token=token, expected=expected)


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
    """Whether the Monday note is on: it moved to an automation, so its automation's on/off (routines.row)."""
    with api.app.state.store.read() as c:
        return bool(routines.row(c, REVIEW)["enabled"])


def test_every_install_starts_at_suggests_actions_and_says_what_each_level_does(api):
    """Spec test 16 (v3): an install the owner never set a level on is at Suggests actions, with its regular checks on.
    Each level has its one sentence, and every level has arrived: level 3 says what it does alone, with no "not yet"."""
    with_strategy_bot(api, level=None)
    shown = get(api, "strategy-bot")
    assert "on" not in shown and shown["level"] == 2 and shown["level_history"] == []
    assert shown["regular_checks"] is True and shown["name"] == "Strategy Bot"
    assert [(lv["level"], lv["name"], lv["ready"]) for lv in shown["levels"]] == \
        [(1, "Passive", True), (2, "Suggests actions", True), (3, "Acts on its own", True)]
    assert "regular checks" in shown["levels"][0]["sentence"] and "changes nothing" in shown["levels"][0]["sentence"]
    assert "decides on its own" in shown["levels"][2]["sentence"] and "Not yet" not in str(shown["levels"])
    assert get(api, "strategy-bot", token="cara-test")["level"] == 2    # everyone reads it


def test_no_chosen_level_is_suggests_actions_and_a_passive_the_owner_chose_stays(api):
    """With no level stored the Strategy Bot is at Suggests actions, and `proactive` is true. An owner who chose Passive
    keeps it: the startup `migrate`, which runs `reconcile`, does not raise it back, and the level it shows is the
    stored one."""
    store = api.app.state.store
    with_strategy_bot(api, level=None)
    with store.transaction() as c:
        strategy_bot.migrate(c)
    with store.read() as c:
        assert strategy_bot.level(c) == 2 and strategy_bot.proactive(c) is True
    level(api, 1)
    with store.transaction() as c:
        strategy_bot.migrate(c)
        strategy_bot.reconcile(c)
    with store.read() as c:
        assert strategy_bot.level(c) == 1 and strategy_bot.proactive(c) is False
    assert get(api, "strategy-bot")["level"] == 1


def test_an_owner_who_picks_the_default_keeps_it_as_their_choice(api):
    """Picking Suggests actions where nothing is stored records it, with no history line (the level did not change),
    so a later default never moves an owner who chose it."""
    store = api.app.state.store
    with_strategy_bot(api, level=None)
    assert get(api, "strategy-bot")["level_chosen"] is False
    with store.transaction() as c:
        strategy_bot.set_level(c, OWNER, 2)
        assert strategy_bot._record(c)["level"] == 2 and strategy_bot.level_history(c) == []
    assert get(api, "strategy-bot")["level_chosen"] is True


def test_only_the_owner_changes_the_level_and_each_change_is_logged(api):
    """Spec test 18 (v3): the owner picks a level and the change is kept with who and when. No admin or member changes
    it, no bot, the Strategy Bot included, nor the Assistant acting for the owner."""
    strategy_token = with_strategy_bot(api, level=None)
    other = bot(api, "ops")
    changed = level(api, 1)
    assert changed["level"] == 1 and changed["level_changed_by"] == OWNER and changed["level_changed_at"]
    [entry] = changed["level_history"]
    assert (entry["level"], entry["was"], entry["by"]) == (1, 2, OWNER) and entry["by_name"]
    for token in ("ben-test", "cara-test", strategy_token, other, via_assistant(api)):
        refused = level(api, 3, token=token, expected=403)
        assert "Only the owner" in str(refused)
    assert level(api, 3)["level"] == 3
    shown = get(api, "strategy-bot")
    assert shown["level"] == 3 and [(e["level"], e["by"]) for e in shown["level_history"]] == [(3, OWNER), (1, OWNER)]
    # The same level again changes nothing and logs nothing; a level outside 1-3 is refused.
    assert len(level(api, 3)["level_history"]) == 2
    level(api, 4, expected=422)
    level(api, 0, expected=422)


def test_only_the_owner_turns_the_regular_checks_off_and_off_they_hold_still(api):
    """v3: the regular checks are the KPI pass and the automatic goal colours. Only the owner turns them off, on their
    own click. Off, a reading changes no colour and the KPI pass is skipped; back on, every colour is worked out again
    at once."""
    strategy_token = with_strategy_bot(api)
    goal, kpi = goal_with_kpi(api, token="ben-test", owner="ben")
    log(api, kpi["id"], 10, token="ben-test", period_end=H.now())
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] == "red"
    for token in ("ben-test", "cara-test", strategy_token, bot(api, "ops"), via_assistant(api)):
        post(api, "strategy-bot/checks", {"on": False}, token=token, expected=403)
    off = post(api, "strategy-bot/checks", {"on": False})
    assert off["regular_checks"] is False and off["level"] == 1
    log(api, kpi["id"], 100, token="ben-test", period_end=H.now())
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] == "red"           # held still
    with api.app.state.store.transaction() as c:
        assert strategy_bot.held_routine(c, {"id": strategy_bot.PASS}) == "regular checks are off"
        assert strategy_bot.held_routine(c, {"id": REVIEW}) is None
        assert routines.no_input(c, api.app.state.store.settings, {"id": strategy_bot.PASS, "bot": "goal-manager",
                                                                      "routine_key": "kpi-pass", "playbook": ""},
                                 None) == "regular checks are off"
        assert G.refresh(c)["changed"] == []
    on = post(api, "strategy-bot/checks", {"on": True})
    assert on["regular_checks"] is True
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] != "red"           # worked out again at once
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT actor FROM events WHERE action='strategy_bot.checks' ORDER BY ts")] == \
            [OWNER, OWNER]
        assert strategy_bot.held_routine(c, {"id": strategy_bot.PASS}) is None


def test_no_vision_is_the_first_thing_it_asks_above_passive_and_saving_one_closes_it(api):
    """Spec test 3: with no vision, the Strategy Bot's first item above Passive asks for it, at the top of the owner's
    list, and it closes by itself once a vision is saved. At Passive it is never asked."""
    goal_manager(api)
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    sync(api)
    assert asks(api, OWNER) == []                                           # Passive: nothing asked
    raise_level(api, 2)
    sync(api)
    vision = [a for a in asks(api, OWNER) if a["kind"] == "vision"]
    assert [(a["title"], a["status"]) for a in vision] == [("Tell your Strategy Bot what this team is for", "open")]
    with api.app.state.store.read() as c:
        mine = H.tasks(c, owner=OWNER, status=H.ACTIVE_STATUSES)
    assert mine[0]["id"] == vision[0]["id"]                                 # first in the owner's queue
    assert [i["kind"] for i in get(api, "goals/needs-you")["items"]][0] == "vision"
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    with api.app.state.store.read() as c:
        assert H.task(c, vision[0]["id"])["status"] == "closed"
    assert all(i["kind"] != "vision" for i in get(api, "goals/needs-you")["items"])


def test_why_proposals_reach_the_owner_as_one_item(api):
    """Spec tests 4 and 14: the Strategy Bot's Why proposals for several goals are one "These goals need a Why line"
    item, never one per goal, above Passive; it closes once every one is settled."""
    strategy_token = with_strategy_bot(api, level=2)
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    goals = [post(api, "goals", {"title": title, "owner": "company"})["goal"]["id"] for title in ("Grow rentals", "Cut churn")]
    first = propose_why(api, goals[0], "More rentals is more landlords on Tico.", strategy_token)
    second = propose_why(api, goals[1], "Landlords who stay are the vision.", strategy_token)
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


def test_no_daily_limit_in_one_persons_needs_you_most_important_first(api):
    """Above Passive the Strategy Bot puts everything it has to ask in the person's Needs you at once, the vision first,
    and counts what it asked today per person."""
    strategy_token = with_strategy_bot(api, level=2)
    for n in range(7):
        post(api, "kpis", {"name": f"Measure {n}", "owner": "me", "cadence": "weekly"})
    sync(api)
    mine = asks(api, OWNER)
    assert len(mine) == 8 and mine[0]["kind"] == "vision"
    shown = get(api, "goals/needs-you")["items"]
    assert len(shown) == 8 and all(i.get("task") for i in shown) and shown[0]["kind"] == "vision"
    post(api, "tasks", {"title": "One more question", "body": "Why?", "owner": OWNER}, token=strategy_token)
    post(api, "tasks", {"title": "A question for Ben", "body": "Why?", "owner": "human:ben"}, token=strategy_token)
    assert get(api, "strategy-bot")["asked_today"] == {OWNER: 9, "human:ben": 1}
    assert get(api, "strategy-bot", token=strategy_token)["asked_today"] == {OWNER: 9, "human:ben": 1}
    assert get(api, "strategy-bot", token="cara-test")["asked_today"] == {}                 # a member: only their own


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
        strategy_bot.reconcile(c)
    review_routine(api)
    with store.transaction() as c:
        strategy_bot.reconcile(c)
    assert review_enabled(api)
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        strategy_bot.reconcile(c)
    assert not review_enabled(api)


def test_at_passive_the_reconcile_never_starts_the_monday_note(api):
    goal_manager(api)
    review_routine(api)
    with api.app.state.store.transaction() as c:
        strategy_bot.reconcile(c)
    assert not review_enabled(api)


def test_with_no_chosen_level_the_monday_note_starts_once_and_a_persons_pause_stands(api):
    """An install whose owner never chose a level is at Suggests actions, so the reconcile starts its Monday note the
    way a raise would, once. A person who pauses it afterwards keeps it paused: the reconcile does not start it
    again."""
    with_strategy_bot(api, level=None)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        strategy_bot.reconcile(c)
    assert review_enabled(api)
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        strategy_bot.reconcile(c)
    assert not review_enabled(api)
    with store.transaction() as c:
        strategy_bot.reconcile(c)
    assert not review_enabled(api)


def test_an_upgrade_to_the_new_default_keeps_a_monday_note_a_person_paused(api):
    """Before the default was Suggests actions, a person started the Monday note and paused it again. The upgrade's
    reconcile leaves it paused, and never tries again."""
    with_strategy_bot(api, level=None)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": True})
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        strategy_bot.reconcile(c)
        assert strategy_bot._record(c).get("review_settled") is True
        strategy_bot.reconcile(c)
    assert not review_enabled(api)


def test_confirming_the_default_never_restarts_a_monday_note_a_person_paused(api):
    """The routine appears after the upgrade, a person starts and pauses it, and the owner confirms Suggests actions
    before the next reconcile: confirming the default is no raise, so the reconcile leaves the note paused."""
    with_strategy_bot(api, level=None)
    store = api.app.state.store
    with store.transaction() as c:
        strategy_bot.reconcile(c)                                                  # no routine yet
    review_routine(api)
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": True})
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        strategy_bot.set_level(c, OWNER, 2)
        strategy_bot.reconcile(c)
    assert not review_enabled(api)


def test_a_raise_while_the_note_is_missing_still_starts_it_over_an_older_pause(api):
    """A person paused the Monday note and deleted it, and the owner raised the level from Passive: when the note is
    back, still paused, the reconcile starts it, since the raise is newer than the pause."""
    goal_manager(api)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": True})
        routines.update(c, OWNER, REVIEW, {"enabled": False})
        routines.remove(c, OWNER, REVIEW)
    with store.transaction() as c:
        strategy_bot.set_level(c, OWNER, 2)
        # The note comes back still paused, the way backend/redesign_migration.py brings an automation back.
        c.execute("UPDATE automations SET deleted_at=NULL WHERE id=?", (routines.row(c, REVIEW)["automation"],))
        strategy_bot.reconcile(c)
    assert review_enabled(api)


def test_with_no_chosen_level_passive_pauses_the_note_and_withdraws_what_waits_and_the_default_changes_nothing(api):
    """The owner picks Passive on an install that never chose a level: the Monday note the reconcile started is paused
    and the recoveries still waiting are withdrawn, exactly as when lowering from level 2. Picking the level the install
    is already at (2) changes nothing and logs nothing."""
    strategy_token = with_strategy_bot(api, level=None)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        strategy_bot.reconcile(c)
    assert review_enabled(api)
    goal_id = post(api, "goals", {"title": "Grow rentals", "owner": "company"})["goal"]["id"]
    payload = {"evidence": "Activation fell from 40% to 10% this week.", "pick": 0, "options": [
        {"label": "Finance pauses the campaign", "actions": [
            {"do": "create", "owner": "finance", "title": "Pause the signup campaign"}]}]}
    waiting = post(api, "goal-proposals", {"kind": "recovery", "goal_id": goal_id, "reason": "Red for a week",
                                           "payload": payload}, token=strategy_token)["proposal"]
    assert waiting["status"] == "pending"
    level(api, 2)
    assert get(api, "strategy-bot")["level_history"] == [] and review_enabled(api)
    level(api, 1)
    assert not review_enabled(api)
    [entry] = get(api, "strategy-bot")["level_history"]
    assert (entry["level"], entry["was"], entry["by"]) == (1, 2, OWNER)
    with store.read() as c:
        status, note, by = c.execute("SELECT status, decision_note, decided_by FROM goal_proposals WHERE id=?",
                                     (waiting["id"],)).fetchone()
    # Tico withdrew it, not the owner: no streak, an older release's after a rollback included, counts it as a no.
    assert (status, note, by) == ("rejected", strategy_suggest.WITHDRAWN, H.KEEPER)
    with store.transaction() as c:
        strategy_bot.reconcile(c)
    assert not review_enabled(api)                                           # at Passive nothing starts it again


def test_a_monday_note_the_old_level_1_started_is_paused_at_passive_once(api):
    """Before v3, level 1 with the switch on started the Monday note. At upgrade that install is at Passive (its stored
    level 1): the hourly reconcile pauses the note it started, unless a person touched it since."""
    import json
    goal_manager(api)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        routines.update(c, H.KEEPER, REVIEW, {"enabled": True})
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (strategy_bot.KEY, json.dumps(
            {"on": True, "level": 1, "by": OWNER, "review_armed": H.now(), "review_settled": True})))
        strategy_bot.reconcile(c)
    assert not review_enabled(api)
    with store.transaction() as c:
        routines.update(c, OWNER, REVIEW, {"enabled": True})                 # a person's own choice
        strategy_bot.reconcile(c)
    assert review_enabled(api)


def test_lowering_to_passive_keeps_the_vision_and_why_asks_until_they_are_resolved(api):
    """v3: at Passive nothing new is asked, but a vision or Why ask filed above it stays open and shown until its cause
    is resolved; only then does it close."""
    strategy_token = with_strategy_bot(api, level=2)
    goal_id = post(api, "goals", {"title": "Grow rentals", "owner": "company"})["goal"]["id"]
    why = propose_why(api, goal_id, "More rentals is more landlords on Tico.", strategy_token)
    sync(api)
    filed = {a["kind"]: a["id"] for a in asks(api, OWNER)}
    assert set(filed) == {"vision", "why"}
    level(api, 1)
    sync(api)
    assert {a["kind"]: a["id"] for a in asks(api, OWNER) if a["status"] == "open"} == filed
    assert {"vision", "why"} <= {i["kind"] for i in get(api, "goals/needs-you")["items"]}
    post(api, "goals", {"title": "Cut churn", "owner": "company"})
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    sync(api)
    assert {a["id"] for a in asks(api)} == set(filed.values())               # nothing new at Passive
    post(api, "company", {"vision": "Every small landlord runs their rentals on Tico."})
    post(api, f"goal-proposals/{why['id']}/decide", {"decision": "confirm"})
    sync(api)
    with api.app.state.store.read() as c:
        assert [H.task(c, t)["status"] for t in filed.values()] == ["closed", "closed"]


def test_with_regular_checks_off_a_due_kpi_pass_is_skipped(api):
    """The KPI pass moved to an automation. Off, its due run is skipped: no run, no task, no message; on, it runs."""
    from datetime import datetime, timezone
    from backend import automations
    goal_manager(api)
    store = api.app.state.store
    with store.transaction() as c:
        routines.create(c, H.KEEPER, "goal-manager", {"title": "KPI pass", "cron": "0 7 * * *", "timezone": "UTC"},
                        key="kpi-pass")
        aid = routines.row(c, strategy_bot.PASS)["automation"]
    assert aid
    post(api, "strategy-bot/checks", {"on": False})

    def due_at(day):
        with store.transaction() as c:
            c.execute("UPDATE automations SET next_due=? WHERE id=?", (f"2026-09-{day}T07:00:00.000000Z", aid))
        automations.tick(store, datetime(2026, 9, day, 8, tzinfo=timezone.utc))
        with store.read() as c:
            return (c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0],
                    c.execute("SELECT count(*) FROM events WHERE action='automation.skipped' AND target=?",
                              (aid,)).fetchone()[0])

    automations.tick(store, datetime(2026, 9, 13, 8, tzinfo=timezone.utc))        # the first tick: the owner's built-ins
    with store.read() as c:
        said = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert due_at(14) == (0, 1)
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == said
    post(api, "strategy-bot/checks", {"on": True})
    assert due_at(15) == (1, 1)

def test_at_upgrade_a_due_monday_note_the_old_level_1_started_never_fires(api):
    """An install from before v3 at level 1 with its Monday note started and overdue: the startup migration pauses
    it before the scheduler's first pass, so no weekly review opens at Passive."""
    import json
    from datetime import datetime, timezone
    from backend.scheduler import Scheduler
    goal_manager(api)
    review_routine(api)
    store = api.app.state.store
    with store.transaction() as c:
        routines.update(c, H.KEEPER, REVIEW, {"enabled": True})
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (strategy_bot.KEY, json.dumps(
            {"on": True, "level": 1, "review_armed": H.now(), "review_settled": True})))
        c.execute("UPDATE schedules SET next_due=NULL, last_fired_at='2026-09-01T00:00:00.000000Z' WHERE id=?", (REVIEW,))
        strategy_bot.migrate(c)                                                   # as the server does at startup
    assert not review_enabled(api)
    Scheduler(store, api.app.state.execution).tick(datetime(2026, 9, 14, 17, tzinfo=timezone.utc))
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM schedule_occurrences WHERE schedule_id=? AND task_id IS NOT NULL",
                         (REVIEW,)).fetchone()[0] == 0
