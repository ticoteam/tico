"""Tico's own calendar (backend/calendar_store.py) on a real hub database: series stored as rules and read as
occurrences, the three edit scopes, stale edits, cancel-not-delete, private events, search, time away and the purge.
The company is the fictional Acme (backend/tests/helpers/company.py): Ana, Ben and Cara, and the bots."""

from datetime import datetime, timezone

import pytest

from backend import calendar_store as CS
from backend.store import Problem


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


@pytest.fixture
def cal(company):
    with company.transaction() as c:
        c.execute("UPDATE humans SET name='Ana', time_zone='America/Los_Angeles' WHERE id='ana'")
        calendar = CS.calendar_for(c, "human", "ana")
    return company, calendar


def standup(c, calendar, **extra):
    fields = {
        "title": "Standup",
        "start": "2026-11-02T09:30:00",
        "end": "2026-11-02T09:45:00",
        "repeat": "weekly on mon,wed",
        "attendees": ["human:ben", "bot:assistant", "guest@outside.example"],
    } | extra
    return CS.create_event(c, calendar["id"], fields, "human:ana")


def starts(store, calendar, start, end):
    with store.read() as c:
        return [(o["start"], o["title"]) for o in CS.occurrences(c, [calendar["id"]], start, end)]


def test_calendar_for_is_made_once_on_first_use(company):
    with company.transaction() as c:
        assert CS.calendar_for(c, "human", "ben", create=False) is None
        first = CS.calendar_for(c, "human", "ben")
        assert CS.calendar_for(c, "human", "ben")["id"] == first["id"]
        assert (first["home"], first["time_zone"], first["name"]) == ("tico", "UTC", "Ben")
        assert CS.calendar_for(c, "bot", "assistant")["owner_id"] == "assistant"
        with pytest.raises(Problem) as missing:
            CS.calendar_for(c, "human", "nobody")
        assert missing.value.status == 404
        assert c.execute("SELECT count(*) FROM calendars").fetchone()[0] == 2


def test_a_series_is_one_row_read_as_occurrences_in_wall_time(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar)
        assert made["start"] == "2026-11-02T17:30:00Z"  # 09:30 PST
        assert made["recurring"] and made["occurrence_id"] == f"tico:{made['event_id']}:2026-11-02T17:30:00Z"
        assert {(a["type"], a["response"]) for a in made["attendees"]} == {
            ("human", "needs_action"),
            ("bot", "needs_action"),
            ("external", "needs_action"),
        }
        assert c.execute("SELECT count(*) FROM calendar_events").fetchone()[0] == 1
        actions = [r[0] for r in c.execute("SELECT action FROM events WHERE action LIKE 'calendar.%'")]
        assert actions == ["calendar.event.created"]
        assert (
            c.execute(
                "SELECT count(*) FROM audit_log WHERE object=?", ("calendar_event:" + made["event_id"],)
            ).fetchone()[0]
            == 1
        )
    # The week of 2 November, and again after daylight saving ended: still 09:30 local, now 17:30Z both ways
    # (Los Angeles fell back on 1 November, before the first instance).
    assert starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 9)) == [
        ("2026-11-02T17:30:00Z", "Standup"),
        ("2026-11-04T17:30:00Z", "Standup"),
    ]


def test_this_scope_moves_one_instance(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar)
        moved = CS.update_event(
            c,
            made["event_id"],
            {"start": "2026-11-04T11:00:00", "end": "2026-11-04T11:30:00", "title": "Standup (late)"},
            "human:ana",
            original_starts_at="2026-11-04T17:30:00Z",
            scope="this",
            expected_sequence=made["sequence"],
        )
        assert moved["occurrence_id"].endswith(":2026-11-04T17:30:00Z")
        assert (moved["start"], moved["end"]) == ("2026-11-04T19:00:00Z", "2026-11-04T19:30:00Z")
        # The instance keeps its attendees.
        assert len(moved["attendees"]) == 3
    assert starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 12)) == [
        ("2026-11-02T17:30:00Z", "Standup"),
        ("2026-11-04T19:00:00Z", "Standup (late)"),
        ("2026-11-09T17:30:00Z", "Standup"),
        ("2026-11-11T17:30:00Z", "Standup"),
    ]


def test_following_scope_ends_the_old_series_and_starts_a_new_one(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar)
        # One moved instance after the split point travels to the new series.
        CS.update_event(
            c,
            made["event_id"],
            {"title": "Standup with demo"},
            "human:ana",
            original_starts_at="2026-11-16T17:30:00Z",
            scope="this",
        )
        later = CS.update_event(
            c,
            made["event_id"],
            {"start": "2026-11-09T10:00:00", "end": "2026-11-09T10:15:00"},
            "human:ana",
            original_starts_at="2026-11-09T17:30:00Z",
            scope="following",
        )
        assert later["event_id"] != made["event_id"]
        assert later["start"] == "2026-11-09T18:00:00Z"
        old = c.execute("SELECT rrule, until_at FROM calendar_events WHERE id=?", (made["event_id"],)).fetchone()
        assert old["rrule"] == "FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261109T172959Z"
        assert old["until_at"] == "2026-11-04T17:45:00Z"
        assert len(later["attendees"]) == 3
    assert starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 19)) == [
        ("2026-11-02T17:30:00Z", "Standup"),
        ("2026-11-04T17:30:00Z", "Standup"),
        ("2026-11-09T18:00:00Z", "Standup"),
        ("2026-11-11T18:00:00Z", "Standup"),
        ("2026-11-16T18:00:00Z", "Standup"),  # the exception's original start is gone from the new rule's times
        ("2026-11-18T18:00:00Z", "Standup"),
    ]


def test_following_on_a_count_series_splits_the_count(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar, repeat="weekly on mon for 4 times")
        later = CS.update_event(
            c,
            made["event_id"],
            {"title": "Planning"},
            "human:ana",
            original_starts_at="2026-11-16T17:30:00Z",
            scope="following",
        )
        rules = dict(c.execute("SELECT id, rrule FROM calendar_events WHERE recurring_event_id IS NULL").fetchall())
        assert rules == {
            made["event_id"]: "FREQ=WEEKLY;BYDAY=MO;COUNT=2",
            later["event_id"]: "FREQ=WEEKLY;BYDAY=MO;COUNT=2",
        }
    assert starts(store, calendar, utc(2026, 11, 1), utc(2027, 1, 1)) == [
        ("2026-11-02T17:30:00Z", "Standup"),
        ("2026-11-09T17:30:00Z", "Standup"),
        ("2026-11-16T17:30:00Z", "Planning"),
        ("2026-11-23T17:30:00Z", "Planning"),
    ]


def test_all_scope_changes_the_series_and_keeps_exceptions_that_still_exist(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar)
        CS.update_event(
            c,
            made["event_id"],
            {"title": "Moved one"},
            "human:ana",
            original_starts_at="2026-11-04T17:30:00Z",
            scope="this",
        )
        CS.update_event(c, made["event_id"], {"title": "Daily sync", "notes": "Bring blockers"}, "human:ana")
    assert starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 7)) == [
        ("2026-11-02T17:30:00Z", "Daily sync"),
        ("2026-11-04T17:30:00Z", "Moved one"),
    ]
    with store.transaction() as c:
        # Moving the Monday instance an hour later with scope all moves every instance; the old exception's time is gone.
        CS.update_event(
            c,
            made["event_id"],
            {"start": "2026-11-02T10:30:00"},
            "human:ana",
            original_starts_at="2026-11-02T17:30:00Z",
            scope="all",
        )
        stale = c.execute(
            "SELECT status FROM calendar_events WHERE recurring_event_id=?", (made["event_id"],)
        ).fetchone()
        assert stale["status"] == "cancelled"
    assert starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 7)) == [
        ("2026-11-02T18:30:00Z", "Daily sync"),
        ("2026-11-04T18:30:00Z", "Daily sync"),
    ]


def test_a_stale_sequence_is_refused(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar, repeat=None)
        CS.update_event(c, made["event_id"], {"title": "One"}, "human:ana", expected_sequence=made["sequence"])
        with pytest.raises(Problem) as stale:
            CS.update_event(c, made["event_id"], {"title": "Two"}, "human:ben", expected_sequence=made["sequence"])
        assert (stale.value.code, stale.value.status) == ("stale", 409)
        assert tuple(c.execute("SELECT title, sequence FROM calendar_events").fetchone()) == ("One", 1)


def test_cancel_never_deletes_and_each_scope_hides_what_it_names(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar)
        CS.cancel_event(c, made["event_id"], "human:ana", original_starts_at="2026-11-04T17:30:00Z", scope="this")
    assert [s for s, _t in starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 10))] == [
        "2026-11-02T17:30:00Z",
        "2026-11-09T17:30:00Z",
    ]
    with store.transaction() as c:
        CS.cancel_event(c, made["event_id"], "human:ana", original_starts_at="2026-11-09T17:30:00Z", scope="following")
    assert [s for s, _t in starts(store, calendar, utc(2026, 11, 2), utc(2026, 12, 1))] == ["2026-11-02T17:30:00Z"]
    with store.transaction() as c:
        CS.cancel_event(c, made["event_id"], "human:ana")
        rows = c.execute("SELECT status FROM calendar_events ORDER BY recurring_event_id IS NULL").fetchall()
        assert [r[0] for r in rows] == ["cancelled", "cancelled"]
        actions = [r[0] for r in c.execute("SELECT action FROM events WHERE action='calendar.event.cancelled'")]
        assert len(actions) == 3
        with pytest.raises(Problem) as gone:
            CS.update_event(c, made["event_id"], {"title": "Back"}, "human:ana")
        assert gone.value.status == 404
    assert starts(store, calendar, utc(2026, 11, 2), utc(2026, 12, 1)) == []


def test_purge_removes_old_cancelled_rows_but_keeps_a_live_series_cancelled_instance(cal):
    store, calendar = cal
    with store.transaction() as c:
        live = standup(c, calendar)
        CS.cancel_event(c, live["event_id"], "human:ana", original_starts_at="2026-11-04T17:30:00Z", scope="this")
        gone = standup(c, calendar, title="Old offsite", repeat=None)
        CS.cancel_event(c, gone["event_id"], "human:ana")
        assert CS.purge_cancelled(c, "2026-12-01T00:00:00.000000Z") == 0  # too recent
        assert CS.purge_cancelled(c, "2099-01-01T00:00:00.000000Z") == 1
        assert {r[0] for r in c.execute("SELECT title FROM calendar_events")} == {"Standup"}
        assert (
            c.execute(
                "SELECT count(*) FROM calendar_event_attendees WHERE calendar_event_id=?", (gone["event_id"],)
            ).fetchone()[0]
            == 0
        )
    assert "2026-11-04T17:30:00Z" not in [s for s, _t in starts(store, calendar, utc(2026, 11, 2), utc(2026, 11, 6))]


def test_private_events_are_marked_for_the_caller(cal):
    store, calendar = cal
    with store.transaction() as c:
        standup(c, calendar, title="Doctor", repeat=None, visibility="private", attendees=[])
    with store.read() as c:
        (occ,) = CS.occurrences(c, [calendar["id"]], utc(2026, 11, 2), utc(2026, 11, 3))
    assert (occ["visibility"], occ["title"], occ["busy_only"]) == ("private", "Doctor", False)


def test_search_finds_words_across_title_notes_and_location_nearest_first(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar, notes="Quarterly roadmap review")
        standup(
            c,
            calendar,
            title="Lunch",
            location="Roadhouse diner",
            repeat=None,
            start="2026-11-20T12:00:00",
            end="2026-11-20T13:00:00",
        )
        CS.update_event(
            c,
            made["event_id"],
            {"title": "Retro"},
            "human:ana",
            original_starts_at="2026-11-04T17:30:00Z",
            scope="this",
        )
        with pytest.raises(Problem):
            CS.update_event(c, made["event_id"], {"title": "x", "colour": "red"}, "human:ana")
    with store.read() as c:
        hits = CS.search(c, [calendar["id"]], "roadmap", utc(2026, 11, 1), utc(2026, 11, 8), now=utc(2026, 11, 4))
        # Nearest to now first; the moved instance's row kept the series' notes, so it matches too.
        assert [h["start"] for h in hits] == ["2026-11-04T17:30:00Z", "2026-11-02T17:30:00Z"]
        assert [h["title"] for h in CS.search(c, [calendar["id"]], "road", utc(2026, 11, 1), utc(2026, 11, 30))].count(
            "Lunch"
        ) == 1
        assert CS.search(c, [calendar["id"]], "retro", utc(2026, 11, 1), utc(2026, 11, 30))[0]["start"] == (
            "2026-11-04T17:30:00Z"
        )
        assert CS.search(c, ["other"], "roadmap", utc(2026, 11, 1), utc(2026, 11, 30)) == []
    with store.transaction() as c:
        CS.cancel_event(c, made["event_id"], "human:ana")
    with store.read() as c:
        assert CS.search(c, [calendar["id"]], "roadmap", utc(2026, 11, 1), utc(2026, 11, 30)) == []


def test_responses_go_to_the_series_or_one_instance_and_only_from_attendees(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = standup(c, calendar)
        CS.set_response(c, made["event_id"], "human:ben", "accepted")
        CS.set_response(c, made["event_id"], "human:ben", "declined", original_starts_at="2026-11-04T17:30:00Z")
        with pytest.raises(Problem) as outsider:
            CS.set_response(c, made["event_id"], "human:cara", "accepted")
        assert outsider.value.status == 403
    with store.read() as c:
        occ = CS.occurrences(c, [calendar["id"]], utc(2026, 11, 2), utc(2026, 11, 5))
    ben = [next(a["response"] for a in o["attendees"] if a["id"] == "ben") for o in occ]
    assert ben == ["accepted", "declined"]


def test_all_day_events_and_absences_are_dates(cal):
    store, calendar = cal
    with store.transaction() as c:
        made = CS.create_event(
            c,
            calendar["id"],
            {"title": "Offsite", "all_day": True, "start": "2026-11-12", "end": "2026-11-14"},
            "human:ana",
        )
        assert (made["start"], made["end"], made["time_zone"]) == ("2026-11-12", "2026-11-14", None)
        away = CS.add_absence(c, "ana", "2026-11-20", "2026-11-21", "Holiday", "human:ana")
        assert (away["kind"], away["start"], away["end"]) == ("away", "2026-11-20", "2026-11-22")
        with pytest.raises(Problem):
            CS.add_absence(c, "ana", "2026-11-21", "2026-11-20", None, "human:ana")
    with store.read() as c:
        assert [o["start"] for o in CS.occurrences(c, [calendar["id"]], utc(2026, 11, 13), utc(2026, 11, 14))] == [
            "2026-11-12"
        ]
        assert [a["title"] for a in CS.absences(c, ["ana", "ben"], "2026-11-21", "2026-11-28")] == ["Holiday"]
        assert CS.absences(c, ["ana"], "2026-11-22", "2026-11-28") == []
