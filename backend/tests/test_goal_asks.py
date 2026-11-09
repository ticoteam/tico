"""What the Goals page needs from a person is a Strategy Bot task (backend/goal_asks.py): one per KPI or proposal, kind
and person, in Tasks and Decide, closed by itself once resolved. Only above Passive: at Passive nobody is asked anything
(spec: Strategy Bot v3, test 20). `goal_manager(api)` is an install the owner set to Passive."""

from datetime import datetime, timedelta, timezone

from backend import strategy_bot
from backend import goal_asks as GA
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_kpis import goal_with_kpi, log
from backend import bot_settings as BS

MANAGER = "bot:goal-manager"


def goal_manager(api, level=1):
    """The Strategy Bot at `level`, chosen by the owner (Passive unless asked otherwise); None leaves it as built, at
    the default level."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,effort,state,created_at) "
            "VALUES('goal-manager','goal-manager','Strategy Bot','','active',?)",
            (H.now(),),
        )
        c.execute("INSERT INTO bot_config(bot,team,owner_id) VALUES('goal-manager',NULL,'ana')")
        BS.write(c, 'goal-manager', {"runtime": "fake", "host": "keeper"})
    raise_level(api, level)


def raise_level(api, level):
    """The owner chooses `level` (`strategy_bot.set_level`); None chooses nothing."""
    if level is not None:
        with api.app.state.store.transaction() as c:
            strategy_bot.set_level(c, "human:ana", level)


def asks(api, owner=None):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT t.id, t.title, t.body, t.owner_id, t.status, a.kind, a.ref FROM goal_asks a JOIN tasks t ON t.id=a.task_id "
                "WHERE t.requester_id=?" + (" AND t.owner_id=?" if owner else "") + " ORDER BY t.created_at",
                (MANAGER, owner) if owner else (MANAGER,),
            )
        ]


def manager_tasks(api, status):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tasks WHERE requester_id=? AND status=?", (MANAGER, status))]


def sync(api, at=None):
    with api.app.state.store.transaction() as c:
        return GA.sync(c, api.app.state.auth.owner_id(c), at)


def needs_tasks(api, token):
    page = get(api, "needs-you", token)
    return {str(i["id"]) for i in page["items"] + page["older"]}


def stale_asks(api, owner="human:ana"):
    return [a for a in asks(api, owner) if a["kind"] == "kpi_stale"]


def test_a_stale_kpi_is_one_task_for_its_owner_closed_by_a_fresh_reading(api):
    goal_manager(api, level=2)
    kpi = post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})["kpi"]
    [task] = stale_asks(api)
    assert (task["kind"], task["ref"], task["status"]) == ("kpi_stale", GA.KPIS_REF, "open")
    assert task["title"] == "Add a number for Unreported measure"
    assert task["body"].startswith(task["title"]) and "- Unreported measure: has no reading yet" in task["body"]
    # In Tasks and Decide (the person's Needs you), and on the Goals page with the task named.
    assert task["id"] in needs_tasks(api, "ana-test")
    [item] = [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "kpi_stale"]
    assert item["task"]["id"] == task["id"] and [k["kpi_id"] for k in item["kpis"]] == [kpi["id"]]
    assert (item["id"], item["subject"], item["actions"]) == ("kpi_stale:kpis", None, ["open"])
    assert item["title"] and "ref" not in item and item["created_at"]
    # Another pass asks nothing twice.
    sync(api)
    assert [a["id"] for a in stale_asks(api)] == [task["id"]]
    # A fresh reading closes it at once, as the Strategy Bot.
    log(api, kpi["id"], 12, period_end=H.now())
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
    assert (row["status"], row["closed_by_id"]) == ("closed", MANAGER)
    assert not [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "kpi_stale"]
    # Time passing makes it stale again: the hourly pass asks again, in a new task.
    later = datetime.now(timezone.utc) + timedelta(days=20)
    made = sync(api, later)["created"]
    [again] = stale_asks(api)
    assert again["status"] == "open" and again["id"] != task["id"] and again["id"] in made


def test_several_stale_measures_for_one_person_are_one_task_listing_them_all(api):
    goal_manager(api, level=2)
    names = ["Calls made", "Demos booked", "Trials started"]
    kpis = [post(api, "kpis", {"name": name, "owner": "me", "cadence": "weekly"})["kpi"] for name in names]
    [task] = stale_asks(api)
    assert task["title"] == "Add numbers for 3 measures"
    assert all(f"- {name}: has no reading yet" in task["body"] for name in names)
    [item] = [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "kpi_stale"]
    assert {k["kpi_id"] for k in item["kpis"]} == {k["id"] for k in kpis}
    # Another pass, or another write, files no second task: the one task lists them.
    post(api, "kpis", {"name": "Refunds issued", "owner": "me", "cadence": "weekly"})
    sync(api)
    [same] = stale_asks(api)
    assert same["id"] == task["id"] and same["title"] == "Add numbers for 4 measures"


def test_a_measure_that_gets_a_number_leaves_the_list_and_the_last_one_closes_the_ask(api):
    goal_manager(api, level=2)
    kpis = [
        post(api, "kpis", {"name": name, "owner": "me", "cadence": "weekly"})["kpi"]
        for name in ("Calls made", "Demos booked")
    ]
    [task] = stale_asks(api)
    log(api, kpis[0]["id"], 12, period_end=H.now())
    # Still open, now for the one measure still waiting.
    [still] = stale_asks(api)
    assert (still["id"], still["status"], still["title"]) == (task["id"], "open", "Add a number for Demos booked")
    assert "Calls made" not in still["body"]
    log(api, kpis[1]["id"], 7, period_end=H.now())
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
    assert (row["status"], row["closed_by_id"]) == ("closed", MANAGER)
    assert stale_asks(api) == []


def test_a_measure_ask_from_before_the_bundle_is_folded_into_the_one_ask(api):
    """An install from before the bundle has one ask per measure. Those close as folded into the one ask, which
    lists the measures still waiting, and their close note never says a reading was logged."""
    goal_manager(api, level=2)
    kpis = [
        post(api, "kpis", {"name": name, "owner": "me", "cadence": "weekly"})["kpi"]
        for name in ("Calls made", "Demos booked")
    ]
    [bundle] = stale_asks(api)
    old = []
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", bundle["id"], status="declined", note="Replaced by the old asks")
        c.execute("DELETE FROM goal_asks WHERE kind='kpi_stale'")
        for kpi in kpis:
            made = H.task_create(
                c, MANAGER, f"Log the first {kpi['name']} reading", "Log it.", "human:ana", lint=False, lane="company"
            )
            c.execute(
                "INSERT INTO goal_asks (kind, ref, owner, task_id, created_at) VALUES ('kpi_stale', ?, 'human:ana', ?, ?)",
                (kpi["id"], made["id"], H.now()),
            )
            old.append(made["id"])
    sync(api)
    with api.app.state.store.read() as c:
        rows = [H.task(c, t) for t in old]
    assert [(r["status"], r["note"]) for r in rows] == [("closed", "Folded into one ask for all measures.")] * 2
    [folded] = stale_asks(api)
    assert folded["ref"] == GA.KPIS_REF and folded["status"] == "open"
    assert all(f"- {name}: has no reading yet" in folded["body"] for name in ("Calls made", "Demos booked"))


def test_a_bundled_ask_that_closes_does_not_say_it_went_to_another_person(api):
    goal_manager(api, level=2)
    mine = post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})["kpi"]
    goal_with_kpi(api, token="ben-test", owner="ben")  # Ben's own out-of-date measure: his ask, not Ana's
    [ana_task] = stale_asks(api, "human:ana")
    assert stale_asks(api, "human:ben")
    log(api, mine["id"], 12, period_end=H.now())
    with api.app.state.store.read() as c:
        row = H.task(c, ana_task["id"])
    assert row["status"] == "closed" and "Now asked of" not in row["note"]


def test_a_red_kpi_asks_the_goal_owner_and_closes_once_back_on_track(api):
    goal_manager(api, level=2)
    goal, kpi = goal_with_kpi(api, token="ben-test", owner="ben")
    log(api, kpi["id"], 10, token="ben-test", period_end=H.now())
    red = [a for a in asks(api, "human:ben") if a["kind"] == "kpi_red"]
    assert [(a["ref"], a["title"]) for a in red] == [
        (kpi["id"], "Check why Activation is off track on Raise activation")
    ]
    assert red[0]["id"] in needs_tasks(api, "ben-test")
    assert red[0]["id"] not in needs_tasks(api, "ana-test")
    log(api, kpi["id"], 75, token="ben-test", period_end=H.now())
    assert not [a for a in asks(api) if a["kind"] == "kpi_red"]
    assert red[0]["id"] in {t["id"] for t in manager_tasks(api, "closed")}


def test_a_proposal_asks_whoever_decides_it_and_closes_when_decided(api):
    goal_manager(api, level=2)
    goal, kpi = goal_with_kpi(api, token="ben-test", owner="ben")
    log(api, kpi["id"], 45, token="ben-test", period_end=H.now())
    proposal = post(
        api,
        "goal-proposals",
        {
            "kind": "kpi_target",
            "goal_id": goal["id"],
            "kpi_id": kpi["id"],
            "reason": "The deadline cannot be met at this pace",
            "payload": {"kind": "improve", "baseline": 40, "target": 60, "deadline": "2099-12-31"},
        },
        token="ben-test",
    )["proposal"]
    [task] = [a for a in asks(api) if a["kind"] == "proposal"]
    assert (task["ref"], task["owner_id"]) == (proposal["id"], "human:ben")
    assert task["title"] == "Use a new Activation target on Raise activation?"
    [item] = [i for i in get(api, "goals/needs-you", "ben-test")["items"] if i["kind"] == "proposal"]
    assert item["task"]["id"] == task["id"] and item["proposal"]["id"] == proposal["id"]
    assert (item["subject"], item["actions"]) == ({"type": "proposal", "id": proposal["id"]}, ["decide"])
    # The environment owner may decide it too: it stays on their Goals page, without the task, which is Ben's.
    [theirs] = [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "proposal"]
    assert theirs["proposal"]["id"] == proposal["id"] and "task" not in theirs
    post(api, f"goal-proposals/{proposal['id']}/decide", {"decision": "reject", "note": "Keep it"}, token="ben-test")
    with api.app.state.store.read() as c:
        row = H.task(c, task["id"])
    assert row["status"] == "closed" and "rejected" in row["note"]


def test_a_task_the_person_closed_is_not_asked_again_while_the_cause_stands(api):
    goal_manager(api, level=2)
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    [task] = stale_asks(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", task["id"], status="declined", note="Not tracking this one")
    sync(api)
    assert [a["id"] for a in stale_asks(api)] == [task["id"]]
    assert not [i for i in get(api, "goals/needs-you")["items"] if i["kind"] == "kpi_stale"]


def test_an_owner_change_moves_the_ask_and_without_a_goal_manager_nothing_is_filed(api):
    raise_level(api, 2)
    goal, kpi = goal_with_kpi(api, token="ben-test", owner="ben")
    log(api, kpi["id"], 10, token="ben-test", period_end=H.now())
    # No Strategy Bot: no task, and the Goals page still says what waits.
    assert not asks(api)
    assert any(i["kind"] == "kpi_red" and "task" not in i for i in get(api, "goals/needs-you", "ben-test")["items"])
    goal_manager(api, level=2)
    sync(api)
    [old] = [a for a in asks(api) if a["kind"] == "kpi_red"]
    assert old["owner_id"] == "human:ben"
    post(api, f"goals/{goal['id']}", {"owner": "cara"})
    [new] = [a for a in asks(api) if a["kind"] == "kpi_red"]
    assert new["owner_id"] == "human:cara" and new["id"] != old["id"]
    assert old["id"] in {t["id"] for t in manager_tasks(api, "closed")}


def test_a_task_marked_done_while_the_cause_stands_is_asked_again(api):
    goal_manager(api, level=2)
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    [task] = stale_asks(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "human:ana", task["id"], status="done", note="Done")
    sync(api)
    [again] = stale_asks(api)
    assert again["id"] != task["id"] and again["status"] == "open"
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "closed"


def test_at_passive_nobody_is_asked_anything_and_what_was_asked_still_closes(api):
    """Spec test 20 (candidate): at Passive, a red KPI, a stale one and an empty vision file nothing and show nothing
    in Needs you, while the colours keep updating. An ask filed before the level went down stays and closes by itself
    once resolved."""
    goal_manager(api)
    goal, kpi = goal_with_kpi(api, token="ben-test", owner="ben")
    log(api, kpi["id"], 10, token="ben-test", period_end=H.now())
    post(api, "kpis", {"name": "Unreported measure", "owner": "me", "cadence": "weekly"})
    assert sync(api) == {"created": [], "closed": []} and not asks(api)
    assert get(api, "goals/needs-you", "ben-test")["items"] == [] and get(api, "goals/needs-you")["items"] == []
    assert get(api, f"goals/{goal['id']}")["goal"]["status"] == "red"  # the regular checks still colour it
    raise_level(api, 2)
    sync(api)
    [red] = [a for a in asks(api) if a["kind"] == "kpi_red"]
    assert {a["kind"] for a in asks(api, "human:ana")} == {"vision", "kpi_stale"}
    raise_level(api, 1)
    post(api, "kpis", {"name": "Another measure", "owner": "me", "cadence": "weekly"})
    assert sync(api)["created"] == []  # nothing new at Passive
    log(api, kpi["id"], 100, token="ben-test", period_end=H.now())
    assert red["id"] in {t["id"] for t in manager_tasks(api, "closed")}  # resolved: it still closes


def test_a_bots_id_never_reaches_the_text_people_read(api):
    """A bot made since ids has a UUID for an id: an ask, a resolved note and a reopen hint name it, not that."""
    bot_id = "0192f0aa-1111-7000-8000-000000000001"
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO bots(id,slug,display_name,state) VALUES(?,'acme-planner','Acme Planner','active')", (bot_id,)
        )
        item = {
            "kind": "proposal",
            "goal_title": "Grow",
            "proposal": {
                "kind": "goal_wording",
                "proposed_by": "bot:" + bot_id,
                "reason": "",
                "payload": {"title": "Grow more"},
            },
        }
        _, body = GA._ask(c, item)
        hint = H.task_reopen_hint(
            c,
            "bot:ops",
            {
                "id": "t1",
                "status": "closed",
                "closed_by_id": "bot:" + bot_id,
                "requester_id": "bot:" + bot_id,
                "owner_id": "bot:ops",
            },
        )
    assert "Acme Planner proposed it" in body and bot_id not in body
    assert "ask Acme Planner to reopen it" in hint and bot_id not in hint
