"""Due dates on rules (docs/rules-plan.md §1, §4): occurrences, the month-end clamp, and when an occurrence turns overdue.
These are pure: the day "today" is passed in, so no test depends on the clock."""
from datetime import date

from backend import rule_due as D


def row(first, repeats="once", zone="America/Los_Angeles"):
    return {"first_due": first, "repeats": repeats, "timezone": zone}


def test_monthly_from_the_31st_falls_on_each_month_end_and_never_drifts_back():
    first = date(2026, 1, 31)
    assert [D.occurrence(first, "monthly", n).isoformat() for n in range(4)] == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]
    # The 31st comes back in March: the repeat counts from the first date, not from the month before.
    assert D.occurrence(first, "monthly", 2) == date(2026, 3, 31)


def test_yearly_from_29_february_falls_on_the_28th_in_other_years():
    first = date(2024, 2, 29)
    assert D.occurrence(first, "yearly", 1) == date(2025, 2, 28)
    assert D.occurrence(first, "yearly", 4) == date(2028, 2, 29)


def test_quarterly_and_weekly_steps():
    assert D.occurrence(date(2026, 1, 15), "quarterly", 1) == date(2026, 4, 15)
    assert D.occurrence(date(2026, 1, 1), "weekly", 2) == date(2026, 1, 15)


def test_a_once_date_has_no_second_occurrence():
    assert D.occurrence(date(2026, 5, 1), "once", 1) is None


def test_an_occurrence_is_overdue_only_after_the_end_of_its_day_in_the_zone():
    due = row("2026-10-30")
    on_the_day = D.state(due, set(), date(2026, 10, 30))
    assert on_the_day["status"] == "due_soon" and on_the_day["open"] == 1
    next_day = D.state(due, set(), date(2026, 10, 31))
    assert next_day["status"] == "overdue" and next_day["due_on"] == "2026-10-30"


def test_a_satisfied_occurrence_reads_satisfied_until_the_next_one_is_open():
    due = row("2026-10-30", repeats="monthly")
    marked = D.state(due, {"2026-10-30"}, date(2026, 10, 30))
    assert marked["status"] == "satisfied"
    # The next monthly occurrence (Nov 30) falls inside the 30-day window from Oct 30 and is open then.
    assert D.state(due, {"2026-10-30"}, date(2026, 11, 1))["status"] == "due_soon"


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
