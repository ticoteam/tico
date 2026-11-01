"""Sub-goals use the same automatic colours as unlinked goals, including on the first pass."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import strategy_bot, goals as G, hubdb as H, kpis as K
from backend.tests.test_api import api, post  # noqa: F401

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
ACTOR = "human:ana"


def stamp(days=0):
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(H, "now", stamp)
    monkeypatch.setattr(K, "now", lambda: NOW)


def make_goal(c, parent_id=None):
    return G.create(c, ACTOR, "Ship the next improvement", "human:ben", parent_id=parent_id)


def measure(c, goal, value):
    """Seed persisted data without triggering a status pass, as it exists before an upgrade."""
    kpi = K.create(c, ACTOR, "human:ben", {"name": "Activation", "unit": "%", "cadence": "weekly"})
    target, error = K.validate_target(kpi, {"kind": "maintain", "min": 40, "max": 60})
    assert error is None
    K.set_link(c, ACTOR, goal["id"], kpi["id"], target)
    K.add_reading(c, ACTOR, kpi, value, period_end=stamp())


def checkin(c, goal, signal="at_risk", days=3):
    c.execute("INSERT INTO goal_checkins(id, goal_id, created_at, author, source_actor, body, signal) "
              "VALUES(?,?,?,?,?,?,?)", (H.new_id(), goal["id"], stamp(days), "human:ben", "human:ben",
                                       "The dependency is late.", signal))


def status_events(c, goal):
    return [e for e in G.history(c, goal["id"]) if e["field"] == "status"]


@pytest.mark.parametrize("value,expected", [(50, "green"), (41, "yellow"), (39, "red")])
def test_new_subgoal_gets_its_measure_colour_on_the_next_pass(api, clock, value, expected):
    with api.app.state.store.transaction() as c:
        parent = make_goal(c)
        child = make_goal(c, parent["id"])
        unlinked = make_goal(c)
        for goal in (child, unlinked):
            measure(c, goal, value)
            assert G.goal(c, goal["id"])["status"] is None

    result = post(api, "goals/refresh", {})
    assert {r["goal"] for r in result["changed"]} == {child["id"], unlinked["id"]}
    with api.app.state.store.read() as c:
        coloured = G.goal(c, child["id"])
        peer = G.goal(c, unlinked["id"])
        assert coloured["status"] == peer["status"] == expected
        assert coloured["status_note"] == peer["status_note"]
        assert coloured["status_source"] == "auto"
        assert coloured["parent_id"] == parent["id"]


@pytest.mark.parametrize("days,expected", [(0, "yellow"), (20, "yellow"), (22, None)])
def test_subgoal_uses_a_recent_at_risk_checkin_without_a_measure(api, clock, days, expected):
    with api.app.state.store.transaction() as c:
        child = make_goal(c, make_goal(c)["id"])
        checkin(c, child, days=days)

    post(api, "goals/refresh", {})
    with api.app.state.store.read() as c:
        row = G.goal(c, child["id"])
        assert row["status"] == expected
        if expected:
            assert row["status_note"].startswith("Check-in ")
            assert row["status_note"].endswith(" ago: at risk")
            assert row["status_source"] == "auto"
        else:
            assert status_events(c, child) == []


def test_subgoal_with_no_data_stays_uncoloured_like_an_unlinked_goal(api, clock):
    with api.app.state.store.transaction() as c:
        parent = make_goal(c)
        child = make_goal(c, parent["id"])
    assert post(api, "goals/refresh", {})["changed"] == []
    with api.app.state.store.read() as c:
        for goal in (parent, child):
            assert G.goal(c, goal["id"])["status"] is None
            assert status_events(c, goal) == []


def test_person_colour_on_subgoal_stays_and_strategy_only_suggests(api, clock):
    with api.app.state.store.transaction() as c:
        child = make_goal(c, make_goal(c)["id"])
        measure(c, child, 39)
        before = G.set_status(c, ACTOR, child["id"], "green", "A temporary exception.")
        history = status_events(c, child)
    result = post(api, "goals/refresh", {})
    assert result["changed"] == []
    assert [(r["goal"], r["suggested"]) for r in result["suggested"]] == [(child["id"], "red")]
    with api.app.state.store.read() as c:
        row = G.goal(c, child["id"])
        for field in ("status", "status_source", "status_by", "status_note", "status_at", "parent_id"):
            assert row[field] == before[field]
        assert row["suggest_status"] == "red"
        assert row["suggest_note"] == "Activation 39% below range 40–60%"
        assert status_events(c, child) == history


def test_regular_checks_off_leaves_blank_auto_and_person_subgoals_untouched(api, clock):
    with api.app.state.store.transaction() as c:
        parent = make_goal(c)
        children = [make_goal(c, parent["id"]) for _ in range(3)]
        measure(c, children[1], 50)
        G.apply_auto(c, children[1]["id"])
        G.set_status(c, ACTOR, children[2]["id"], "green", "A temporary exception.")
        for child in children:
            measure(c, child, 39)
        strategy_bot.set_checks(c, ACTOR, False)
        before = [G.goal(c, g["id"]) for g in children]
        history = [status_events(c, g) for g in children]
    result = post(api, "goals/refresh", {})
    assert result["changed"] == result["suggested"] == []
    with api.app.state.store.read() as c:
        assert [G.goal(c, g["id"]) for g in children] == before
        assert [status_events(c, g) for g in children] == history


def test_first_pass_colours_existing_blank_subgoals_and_records_each_automatic_change(api, clock):
    with api.app.state.store.transaction() as c:
        parent = make_goal(c)
        children = [make_goal(c, parent["id"]) for _ in range(6)]
        expected = ["green", "yellow", "red", "green", "yellow", "red"]
        for index, child in enumerate(children):
            c.execute("UPDATE goals SET created_at=?, updated_at=? WHERE id=?", (stamp(30), stamp(30), child["id"]))
            if index < 3:
                measure(c, child, [50, 41, 39][index])
            else:
                checkin(c, child, ["on_track", "at_risk", "off_track"][index - 3])
            assert G.goal(c, child["id"])["status"] is None
    result = post(api, "goals/refresh", {})
    assert {r["goal"]: r["status"] for r in result["changed"]} == dict(zip([g["id"] for g in children], expected))
    with api.app.state.store.read() as c:
        for child, colour in zip(children, expected):
            row = G.goal(c, child["id"])
            assert row["status"] == colour
            assert row["status_source"] == "auto" and row["status_by_id"] == G.AUTO_ACTOR
            assert row["parent_id"] == parent["id"]
            events = status_events(c, child)
            assert len(events) == 1
            event = events[0]
            assert (event["old"], event["new"], event["status_source"]) == (None, colour, "auto")
            assert event["actor"] == event["status_by_id"] == G.AUTO_ACTOR
            assert event["note"] == row["status_note"]
    assert post(api, "goals/refresh", {})["changed"] == []
    with api.app.state.store.read() as c:
        assert all(len(status_events(c, g)) == 1 for g in children)
