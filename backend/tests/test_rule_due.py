"""Due dates on rules (docs/dev/rules-plan.md §1, §4): occurrences, the month-end clamp, and when an occurrence turns overdue.
These are pure: the day "today" is passed in, so no test depends on the clock."""

from datetime import date

import pytest

from backend import automation_due
from backend import rule_due as D
from backend.store import Problem


def row(first, repeats="once", zone="America/Los_Angeles"):
    return {"first_due_on": first, "repeats": repeats, "timezone": zone}


def test_monthly_from_the_31st_falls_on_each_month_end_and_never_drifts_back():
    first = date(2026, 1, 31)
    assert [automation_due.occurrence(first, "monthly", n).isoformat() for n in range(4)] == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
        "2026-04-30",
    ]
    # The 31st comes back in March: the repeat counts from the first date, not from the month before.
    assert automation_due.occurrence(first, "monthly", 2) == date(2026, 3, 31)


def test_yearly_from_29_february_falls_on_the_28th_in_other_years():
    first = date(2024, 2, 29)
    assert automation_due.occurrence(first, "yearly", 1) == date(2025, 2, 28)
    assert automation_due.occurrence(first, "yearly", 4) == date(2028, 2, 29)


def test_quarterly_and_weekly_steps():
    assert automation_due.occurrence(date(2026, 1, 15), "quarterly", 1) == date(2026, 4, 15)
    assert automation_due.occurrence(date(2026, 1, 1), "weekly", 2) == date(2026, 1, 15)


def test_the_walk_of_occurrences_is_automation_dues_days_and_it_names_the_same_repeats():
    first = date(2026, 1, 31)
    walk = D.occurrences(first, "monthly", date(2026, 3, 1))  # Jan 31, Feb 28, then Mar 31: the first one past the day
    assert walk == [automation_due.occurrence(first, "monthly", n) for n in range(3)]
    assert D.is_occurrence(first, "monthly", date(2026, 3, 31))
    assert not D.is_occurrence(first, "monthly", date(2026, 3, 30))
    assert D.parse_repeats("quarterly") == "quarterly"
    with pytest.raises(Problem):
        D.parse_repeats("fortnightly")


def test_an_occurrence_is_overdue_only_after_the_end_of_its_day_in_the_zone():
    due = row("2026-10-30")
    on_the_day = D.state(due, set(), date(2026, 10, 30))
    assert on_the_day["status"] == "due_soon" and on_the_day["open"] == 1
    next_day = D.state(due, set(), date(2026, 10, 31))
    assert next_day["status"] == "overdue" and next_day["due_on"] == "2026-10-30"


def test_a_satisfied_repeating_occurrence_moves_on_to_the_next_one():
    due = row("2026-10-30", repeats="monthly")
    # Nothing is open and the next monthly occurrence (Nov 30) is past the 30-day window: it is what is due now.
    moved = D.state(due, {"2026-10-30"}, date(2026, 10, 30))
    assert moved["status"] == "upcoming" and moved["due_on"] == "2026-11-30" and moved["open"] == 0
    # Inside the window, the next one is open, so it is due soon.
    assert D.state(due, {"2026-10-30"}, date(2026, 11, 1))["status"] == "due_soon"


def test_a_satisfied_once_stays_satisfied():
    once = row("2026-10-30")
    marked = D.state(once, {"2026-10-30"}, date(2026, 10, 30))
    assert marked["status"] == "satisfied" and marked["due_on"] == "2026-10-30"


def test_an_occurrence_past_the_window_is_upcoming():
    far = D.state(row("2027-06-01"), set(), date(2026, 10, 30))
    assert far["status"] == "upcoming" and far["open"] == 0 and far["due_on"] == "2027-06-01"


def test_two_unsatisfied_weekly_occurrences_are_two_open():
    due = row("2026-10-01", repeats="weekly")
    state = D.state(due, set(), date(2026, 10, 30))
    assert state["status"] == "overdue"
    assert state["open"] >= 2


def test_a_zone_names_the_day_the_occurrence_is_judged_in():
    assert D.parse_zone("America/Chicago") == "America/Chicago"
    try:
        D.parse_zone("Mars/Olympus")
    except Exception as exc:
        assert getattr(exc, "status", None) == 422
    else:
        raise AssertionError("an unknown zone is refused")


def test_the_obligation_state_table():
    today = date(2026, 11, 18)
    before, on_the_day, after = date(2026, 11, 19), today, date(2026, 11, 17)
    met = {"outcome": "met", "proof_kind": ""}
    proved = {"outcome": "met", "proof_kind": "email"}
    # Before (or on) the day: due, whatever the basis.
    for basis in ("", *D.BASES):
        assert D.occurrence_state(basis, before, today, None) == "due"
        assert D.occurrence_state(basis, on_the_day, today, None) == "due"
    # After it with no mark: passed when only asked for, watched (None) when theirs, else unconfirmed.
    assert D.occurrence_state("asked", after, today, None) == "passed"
    assert D.occurrence_state("theirs", after, today, None) is None
    for basis in ("", "law", "order", "agency", "contract", "ours"):
        assert D.occurrence_state(basis, after, today, None) == "unconfirmed"
    # Marked met: met, unless the basis binds us and there is no proof.
    for basis in D.BINDING:
        assert D.occurrence_state(basis, after, today, met) == "unconfirmed"
        assert D.occurrence_state(basis, before, today, met) == "unconfirmed"  # an early mark proves nothing either
        assert D.occurrence_state(basis, after, today, proved) == "met"
    for basis in ("", "ours", "asked", "theirs"):
        assert D.occurrence_state(basis, after, today, met) == "met"
    # A mark from before slice 3 has no outcome column: it reads as met.
    assert D.occurrence_state("", after, today, {"proof_kind": None}) == "met"
    # Waived and missed stay so, binding or not.
    assert D.occurrence_state("order", after, today, {"outcome": "waived"}) == "waived"
    assert D.occurrence_state("asked", after, today, {"outcome": "missed"}) == "missed"


def test_the_obligations_tab_groups_and_how_far_back_and_ahead_it_reads():
    today = date(2026, 11, 18)
    assert D.occurrence_group("unconfirmed", date(2026, 1, 1), today, None) == "unconfirmed"
    assert D.occurrence_group("due", date(2026, 12, 2), today, None) == "next"  # 14 days ahead
    assert D.occurrence_group("due", date(2026, 12, 3), today, None) == "later"
    assert D.occurrence_group("passed", date(2026, 10, 19), today, None) == "past"  # 30 days back
    assert D.occurrence_group("passed", date(2026, 10, 18), today, None) is None
    assert D.occurrence_group(None, date(2026, 11, 1), today, None) == "past"
    # A date met early sits with the past ones; an old date marked lately does too.
    assert D.occurrence_group("met", date(2026, 11, 25), today, {"satisfied_at": "2026-11-18T10:00:00Z"}) == "past"
    assert D.occurrence_group("met", date(2026, 6, 1), today, {"satisfied_at": "2026-11-10T10:00:00Z"}) == "past"
    assert D.occurrence_group("waived", date(2026, 6, 1), today, {"satisfied_at": "2026-06-01T10:00:00Z"}) is None


def test_a_weekly_premium_reads_back_a_year_ahead_to_its_horizon_or_to_its_next_day():
    import sqlite3

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE rule_due_status(due_date_id TEXT, due_on TEXT, satisfied_at TEXT, outcome TEXT, proof_kind TEXT)"
    )
    c.execute("INSERT INTO rule_due_status VALUES('w','2026-11-11','2026-11-11T09:00:00Z','met','link')")
    weekly = {"id": "w", "first_due_on": "2026-10-28", "repeats": "weekly", "timezone": "UTC", "basis": "contract"}
    today = date(2026, 11, 18)
    seen = [(o["due_on"], o["state"], o["group"]) for o in D.obligations(c, [weekly], 21, today)]
    assert seen == [
        ("2026-10-28", "unconfirmed", "unconfirmed"),
        ("2026-11-04", "unconfirmed", "unconfirmed"),
        ("2026-11-11", "met", "past"),
        ("2026-11-18", "due", "next"),  # today is still due
        ("2026-11-25", "due", "next"),
        ("2026-12-02", "due", "next"),
        ("2026-12-09", "due", "later"),
    ]
    assert [o["due_on"] for o in D.obligations(c, [weekly], 21, today, only_next=True)][-1] == "2026-11-18"
    # A date over a year old that someone marked lately still shows with the past ones.
    c.execute("INSERT INTO rule_due_status VALUES('old','2025-10-14','2026-11-17T09:00:00Z','met','link')")
    old = {"id": "old", "first_due_on": "2025-10-14", "repeats": "once", "timezone": "UTC", "basis": "law"}
    assert [(o["due_on"], o["state"], o["group"]) for o in D.obligations(c, [old], 90, today)] == [
        ("2025-10-14", "met", "past")
    ]


def test_the_migration_reads_a_mark_from_before_it_as_met_with_no_proof_and_runs_again():
    import sqlite3
    from importlib import import_module

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE rule_due_dates(id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, first_due_on TEXT NOT NULL, repeats TEXT "
        "NOT NULL, timezone TEXT NOT NULL, owner TEXT NOT NULL, routine_id TEXT, replaces TEXT, ended_on TEXT, "
        "retired_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE rule_due_status(due_date_id TEXT NOT NULL, due_on TEXT NOT NULL, satisfied_by TEXT NOT NULL, via "
        "TEXT NOT NULL, task_id TEXT, satisfied_at TEXT NOT NULL, PRIMARY KEY(due_date_id, due_on))"
    )
    c.execute(
        "INSERT INTO rule_due_dates VALUES('d1','r1','2026-01-31','monthly','UTC','bot:ops',NULL,NULL,NULL,NULL,'x','t')"
    )
    c.execute("INSERT INTO rule_due_status VALUES('d1','2026-01-31','bot:ops','routine','t1','2026-01-31T09:00:00Z')")
    up = import_module("backend.migrations.20261119034134_rule_due_proof").up
    up(c)
    up(c)
    date_row = dict(c.execute("SELECT title, basis, task_id FROM rule_due_dates").fetchone())
    assert date_row == {"title": "", "basis": "", "task_id": None}
    mark = dict(c.execute("SELECT * FROM rule_due_status").fetchone())
    assert D.mark_fields(mark) == {
        "outcome": "met",
        "proof": None,
        "note": "",
        "confirmed_by_id": None,
        "confirmed_at": None,
    }
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE rule_due_dates SET basis='fortnightly'")
