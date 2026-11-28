"""Removing a person: the preview says what moves before anything changes, Remove applies and records the same, and
Restore moves the reports back and names the plans that went (backend/leaving.py)."""

import json

from backend import access as Access
from backend import directory as D
from backend import views
from backend.store import H
from backend.tests.helpers.api import get, post, setup_attempt
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.model_subscriptions import OTHER, add, company, pick


def chart(api, **reports_to):
    """Put Dee on the roster and set who reports to whom (person id -> manager id)."""
    with api.app.state.store.transaction() as c:
        roster = views.roster(c)
        people = [dict(p) for p in roster["people"]]
        if not any(p["id"] == "dee" for p in people):
            people.append({"id": "dee", "name": "Dee", "email": "dee@acme.example"})
        people = [{**p, "reports_to": reports_to.get(p["id"], p.get("reports_to") or "")} for p in people]
        Access._save_roster(c, {**roster, "people": people})
        for p in people:
            Access._sync_human(c, {"name": p.get("name") or p["id"], **p})


def manager(api, pid):
    with api.app.state.store.read() as c:
        return next(p for p in views.roster(c)["people"] if p["id"] == pid).get("reports_to") or ""


def bot_id(api, slug):
    with api.app.state.store.read() as c:
        return H.resolve_bot(c, slug)["id"]


def ben_leaves_with_a_plan_and_two_reports(api):
    """Ben owns a Claude plan that Ops and CPO run on; Cara and Dee report to Ben, and Ben reports to Ana."""
    setup(api)
    company(api)
    plan = add(api, token="ben-test", name="Ben's Claude")["id"]
    for bot in ("ops", "cpo"):
        pick(api, bot, "claude", plan)
    chart(api, ben="ana", cara="ben", dee="ben")
    return plan


def test_the_preview_names_the_plan_its_bots_and_the_reports_and_changes_nothing(api):
    plan = ben_leaves_with_a_plan_and_two_reports(api)
    seen = get(api, "humans/ben/leaving")
    assert [(p["name"], sorted(b["slug"] for b in p["bots"])) for p in seen["plans"]] == [
        ("Ben's Claude", ["cpo", "ops"])
    ]
    assert sorted(b["slug"] for b in seen["bots"]) == ["cpo", "ops"]
    assert sorted(r["id"] for r in seen["reports"]) == ["cara", "dee"] and seen["to"]["id"] == "ana"
    # Nothing moved: the plan, the bots' model and the reports are as they were (Cancel changes nothing).
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM model_subscriptions WHERE id=?", (plan,)).fetchone()
        assert c.execute("SELECT count(*) FROM agent_subscriptions WHERE subscription_id=?", (plan,)).fetchone()[0] == 2
        assert not c.execute("SELECT 1 FROM events WHERE action='person.left'").fetchone()
    assert manager(api, "cara") == manager(api, "dee") == "ben"


def test_someone_with_nothing_to_move_gets_empty_lists(api):
    chart(api)
    seen = get(api, "humans/dee/leaving")
    assert (seen["plans"], seen["bots"], seen["reports"], seen["to"], seen["groups"], seen["message_bot"]) == (
        [],
        [],
        [],
        None,
        [],
        None,
    )
    assert seen["tasks"] == seen["asks"] == 0


def test_a_member_may_not_see_the_preview_as_they_may_not_remove(api):
    chart(api)
    get(api, "humans/dee/leaving", token="cara-test", expected=403)
    post(api, "humans/dee", {"left": True}, token="cara-test", expected=403)


def test_remove_records_the_bots_the_preview_listed(api):
    ben_leaves_with_a_plan_and_two_reports(api)
    seen = get(api, "humans/ben/leaving")
    post(api, "humans/ben", {"left": True})
    with api.app.state.store.read() as c:
        kept = json.loads(
            c.execute("SELECT detail FROM events WHERE action='person.left' AND target='ben'").fetchone()[0]
        )
        lost = {r[0] for r in c.execute("SELECT target FROM events WHERE action='agent_subscription.owner_left'")}
    assert set(kept["bots"]) == {b["id"] for b in seen["bots"]} == lost
    assert sorted(kept["reports"]) == ["cara", "dee"] and kept["to"] == "ana"
    assert [p["name"] for p in kept["plans"]] == ["Ben's Claude"]
    assert manager(api, "cara") == manager(api, "dee") == "ana"


def test_restore_moves_the_reports_back_except_one_moved_by_hand_and_names_the_plan(api):
    ben_leaves_with_a_plan_and_two_reports(api)
    post(api, "humans/ben", {"left": True})
    post(api, "humans/dee", {"reports_to": "cara"})  # moved by hand after the removal: stays
    back = post(api, "access/humans/ben", {"left": False})["restored"]
    assert manager(api, "cara") == "ben" and manager(api, "dee") == "cara"
    assert [r["id"] for r in back["reports"]] == ["cara"]
    assert [p["name"] for p in back["plans"]] == ["Ben's Claude"]
    assert sorted(b["slug"] for b in back["bots"]) == ["cpo", "ops"]


def test_restore_after_a_directory_sync_moves_the_reports_back(api):
    ben_leaves_with_a_plan_and_two_reports(api)
    settings = api.app.state.store.settings
    with api.app.state.store.transaction() as c:
        plan = {
            "adds": [],
            "updates": [],
            "restores": [],
            "leaves": [{"id": "ben", "email": "ben@acme.example", "reason": "gone"}],
        }
        D.apply(c, H.TICO_ACTOR, "google", plan, views.roster(c), settings=settings)
    assert manager(api, "cara") == manager(api, "dee") == "ana"
    with api.app.state.store.transaction() as c:
        plan = {"adds": [], "updates": [], "restores": [{"id": "ben", "email": "ben@acme.example"}], "leaves": []}
        D.apply(c, H.TICO_ACTOR, "google", plan, views.roster(c), settings=settings)
    assert manager(api, "cara") == manager(api, "dee") == "ben"
    assert bot_id(api, "ops")  # the bots are still there, without Ben's plan


def directory_sync(api, **managers):
    """A Google sync that lists Cara and Dee (linked to Google) with the directory's manager for each (an email)."""
    with api.app.state.store.transaction() as c:
        roster = views.roster(c)
        people = [{**p, "directory": "google"} if p["id"] in ("cara", "dee") else p for p in roster["people"]]
        roster = {**roster, "people": people}
        Access._save_roster(c, roster)
        records = [
            {"email": f"{pid}@acme.example", "name": pid.title(), "title": "", "manager": boss, "active": True}
            for pid, boss in managers.items()
        ]
        found = D.plan(roster, records, "google", "owner@acme.example", False)
        D.apply(c, H.TICO_ACTOR, "google", found, roster, settings=api.app.state.store.settings)


def test_restore_after_an_unchanged_directory_sync_moves_the_reports_back(api):
    ben_leaves_with_a_plan_and_two_reports(api)
    directory_sync(api, cara="ben@acme.example", dee="ben@acme.example")
    post(api, "humans/ben", {"left": True})
    # The directory still names Ben, who is marked left here: nothing moves, and that is not a change to keep.
    directory_sync(api, cara="ben@acme.example", dee="ben@acme.example")
    assert manager(api, "cara") == manager(api, "dee") == "ana"
    back = post(api, "access/humans/ben", {"left": False})["restored"]
    assert manager(api, "cara") == manager(api, "dee") == "ben"
    assert sorted(r["id"] for r in back["reports"]) == ["cara", "dee"]


def test_restore_keeps_a_report_the_directory_gave_a_new_manager(api):
    ben_leaves_with_a_plan_and_two_reports(api)
    directory_sync(api, cara="ben@acme.example", dee="ben@acme.example")
    post(api, "humans/ben", {"left": True})
    # The directory now puts Dee under Cara: a real reassignment, which Restore keeps.
    directory_sync(api, cara="ben@acme.example", dee="cara@acme.example")
    assert manager(api, "dee") == "cara"
    back = post(api, "access/humans/ben", {"left": False})["restored"]
    assert manager(api, "dee") == "cara" and manager(api, "cara") == "ben"
    assert [r["id"] for r in back["reports"]] == ["cara"]


def test_restore_leaves_a_report_moved_away_and_back_by_hand(api):
    ben_leaves_with_a_plan_and_two_reports(api)
    post(api, "humans/ben", {"left": True})
    post(api, "humans/dee", {"reports_to": "cara"})
    post(api, "humans/dee", {"reports_to": "ana"})  # back where Remove put them, but by a person's choice: stays
    back = post(api, "access/humans/ben", {"left": False})["restored"]
    assert manager(api, "dee") == "ana" and manager(api, "cara") == "ben"
    assert [r["id"] for r in back["reports"]] == ["cara"]


def test_restore_names_the_bots_of_each_plan(api):
    setup(api)
    company(api)
    first = add(api, token="ben-test", name="Ben's Claude")["id"]
    second = add(api, token="ben-test", secret=OTHER, name="Ben's other Claude")["id"]
    pick(api, "ops", "claude", first)
    pick(api, "cpo", "claude", second)
    seen = get(api, "humans/ben/leaving")
    assert {p["name"]: [b["slug"] for b in p["bots"]] for p in seen["plans"]} == {
        "Ben's Claude": ["ops"],
        "Ben's other Claude": ["cpo"],
    }
    post(api, "humans/ben", {"left": True})
    back = post(api, "access/humans/ben", {"left": False})["restored"]
    assert {p["name"]: [b["slug"] for b in p["bots"]] for p in back["plans"]} == {
        "Ben's Claude": ["ops"],
        "Ben's other Claude": ["cpo"],
    }


def test_restoring_a_chain_in_either_order_rebuilds_it(api):
    """Ana > Ben > Cara > Dee. Ben and Cara leave in either order: restoring them in either order puts the chart back."""
    for gone in (("ben", "cara"), ("cara", "ben")):
        for first, then in (("ben", "cara"), ("cara", "ben")):
            chart(api, ben="ana", cara="ben", dee="cara")
            for pid in gone:
                post(api, f"humans/{pid}", {"left": True})
            assert manager(api, "dee") == "ana"
            post(api, f"access/humans/{first}", {"left": False})
            post(api, f"access/humans/{then}", {"left": False})
            got = (manager(api, "ben"), manager(api, "cara"), manager(api, "dee"))
            assert got == ("ana", "ben", "cara"), (gone, first)


def test_restore_after_two_removals_keeps_a_report_moved_by_hand_in_between(api):
    """Cara leaves (Dee to Ben), Ben leaves (Dee on to Ana), then someone gives Dee to Ana by hand: Cara's restore
    leaves Dee where that person put them."""
    chart(api, ben="ana", cara="ben", dee="cara")
    post(api, "humans/cara", {"left": True})
    post(api, "humans/ben", {"left": True})
    post(api, "humans/dee", {"reports_to": "ana"})
    post(api, "access/humans/cara", {"left": False})
    assert manager(api, "dee") == "ana"


def test_removing_and_restoring_the_same_manager_twice_puts_the_report_back(api):
    """Ana > Ben > Cara > Dee, and nobody moves Dee by hand: Dee ends under Cara (QA's sequence). Ben's restore moved
    Dee from Ana to Ben, so Cara's second restore follows Dee there."""
    chart(api, ben="ana", cara="ben", dee="cara")
    steps = [
        ("humans/cara", True, "ben"),
        ("humans/ben", True, "ana"),
        ("access/humans/cara", False, "cara"),
        ("humans/cara", True, "ana"),
        ("access/humans/ben", False, "ben"),
        ("access/humans/cara", False, "cara"),
    ]
    for path, left, dee in steps:
        post(api, path, {"left": left})
        assert manager(api, "dee") == dee, path
    assert (manager(api, "ben"), manager(api, "cara")) == ("ana", "ben")


def test_restoring_the_same_manager_twice_keeps_a_report_moved_by_hand_in_between(api):
    """The same sequence, but someone gives Dee to Ana by hand after Ben's restore: Cara's restore leaves Dee there."""
    chart(api, ben="ana", cara="ben", dee="cara")
    for path, left in (("humans/cara", True), ("humans/ben", True), ("access/humans/cara", False)):
        post(api, path, {"left": left})
    post(api, "humans/cara", {"left": True})
    post(api, "access/humans/ben", {"left": False})
    post(api, "humans/dee", {"reports_to": "ana"})
    post(api, "access/humans/cara", {"left": False})
    assert manager(api, "dee") == "ana"


def operated_by(api, slug, person):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET owner_id=? WHERE bot IN (?,?)", (person, slug, H.resolve_bot(c, slug)["id"]))


def test_a_bot_run_for_the_owner_reads_the_preview_and_one_run_for_a_member_may_not(api):
    token = setup_attempt(api, "ops")[2]["token"]  # a real run's credential, through the server's own sign-in
    chart(api, ben="ana", cara="ben", dee="ben")
    operated_by(api, "ops", "ana")
    seen = get(api, "humans/ben/leaving", token=token)
    assert sorted(r["id"] for r in seen["reports"]) == ["cara", "dee"] and seen["to"]["id"] == "ana"
    operated_by(api, "ops", "cara")
    get(api, "humans/ben/leaving", token=token, expected=403)
    post(api, "humans/ben", {"left": True}, token=token, expected=403)  # reading never lets a bot remove
