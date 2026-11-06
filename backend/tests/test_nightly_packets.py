"""Nightly learnings packets: the closing step (learnings.close_finished_packets, run by Clean up) closes a packet whose
night has ended or that a newer packet replaces, whatever its status, so it stops counting as held Reviewer work
(profiles.mismatches, Health's reviewer_holds_tasks). An open subtask keeps its packet open and is left alone."""

from backend import hubdb as H
from backend import learnings
from backend import profiles as P
from backend.maintenance_steps import clean_up
from backend.tests.test_api import api  # noqa: F401  (api is a fixture)
from backend.tests.test_profiles import add_bot, put

NOW = "2026-11-06T15:00:00Z"            # Nov 6 in the team's zone
ENDED = "2026-11-03T12:00:00Z"          # Nov 3: an earlier night
TODAY = "2026-11-06T13:00:00Z"          # the same night as NOW


def packet(c, bot, created, status):
    task = H.task_create(c, H.KEEPER, "Nightly learnings: Nov 3", "x", "bot:" + bot, deduplicate=False, lint=False,
                         private=True)
    c.execute("UPDATE tasks SET created_at=?, status=? WHERE id=?", (created, status, task["id"]))
    return task["id"]


def holds(c, bot="checker"):
    return [m for m in P.mismatches(c) if m[1] == bot]


def test_a_doing_packet_from_an_ended_night_closes_and_stops_holding_its_reviewer(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        task = packet(c, "checker", ENDED, "doing")
        assert holds(c) == [("reviewer_holds_tasks", "checker", 1)]
        assert learnings.close_finished_packets(c, NOW) == {"closed": [task], "kept": []}
        assert H.task(c, task)["status"] == "closed"
        assert holds(c) == []


def test_a_closed_packet_is_not_counted_and_is_not_closed_again(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        packet(c, "checker", ENDED, "closed")
        assert holds(c) == []
        assert learnings.close_finished_packets(c, NOW) == {"closed": [], "kept": []}
        assert holds(c) == []


def test_an_ordinary_active_task_still_counts_and_todays_packet_is_kept(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        c.execute("INSERT INTO tasks(id,title,body,owner_id,requester_id,status,created_at,updated_at) "
                  "VALUES('t-odd','Odd job','x','bot:checker','human:ana','doing',?,?)", (ENDED, ENDED))
        today = packet(c, "checker", TODAY, "open")
        assert learnings.close_finished_packets(c, NOW) == {"closed": [], "kept": []}
        assert H.task(c, today)["status"] == "open"
        assert holds(c) == [("reviewer_holds_tasks", "checker", 2)]


def test_a_finished_packet_with_an_open_child_stays_open_and_the_child_is_left_alone(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        add_bot(c, "lead")
        task = packet(c, "checker", ENDED, "doing")
        child = H.task_create(c, H.KEEPER, "Route decision", "x", "bot:lead", parent_id=task)
        assert learnings.close_finished_packets(c, NOW) == {
            "closed": [], "kept": [{"id": task, "reason": "open subtask " + child["id"]}]}
        assert H.task(c, task)["status"] == "doing"
        assert H.task(c, child["id"])["status"] == "open"
        assert holds(c) == [("reviewer_holds_tasks", "checker", 1)]


def test_an_older_packet_is_superseded_by_a_newer_one_for_the_same_bot_only(api):
    with api.app.state.store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        add_bot(c, "other")
        older = packet(c, "checker", "2026-11-06T09:00:00Z", "open")
        newer = packet(c, "checker", "2026-11-06T12:00:00Z", "waiting")
        other = packet(c, "other", "2026-11-06T09:00:00Z", "open")
        assert learnings.close_finished_packets(c, NOW) == {"closed": [older], "kept": []}
        assert H.task(c, newer)["status"] == "waiting"
        assert H.task(c, other)["status"] == "open"


def test_clean_up_closes_the_finished_packet_and_reports_it(api):
    store = api.app.state.store
    with store.transaction() as c:
        add_bot(c, "checker")
        put(c, "checker", P.REVIEWER)
        task = packet(c, "checker", ENDED, "doing")
    result = clean_up.run(None, {"store": store, "now": NOW})
    assert result["details"]["packets"] == {"closed": [task], "kept": []}
    with store.read() as c:
        assert H.task(c, task)["status"] == "closed"
        assert holds(c) == []
