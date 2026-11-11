"""Recurrence maths for Tico's calendar (backend/calendar_recur.py): wall time across clock changes, the RFC 5545 rule
subset, exceptions keyed by original start, and the bounds."""

from datetime import datetime, timezone

import pytest

from backend import calendar_recur as CR
from backend.store import Problem


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


def timed(start, end, zone, rrule=None):
    return {"starts_at_local": start, "ends_at_local": end, "time_zone": zone, "rrule": rrule}


def all_day(start, end, rrule=None):
    return {"all_day": True, "starts_on": start, "ends_on": end, "rrule": rrule}


CASES = {
    # 09:30 Pacific stays 09:30 through spring forward (PST -8 -> PDT -7).
    "la_spring_forward": (
        timed("2026-03-06T09:30:00", "2026-03-06T10:00:00", "America/Los_Angeles", "FREQ=DAILY"),
        (utc(2026, 3, 7), utc(2026, 3, 10)),
        ["2026-03-07T17:30:00Z", "2026-03-08T16:30:00Z", "2026-03-09T16:30:00Z"],
    ),
    # 02:30 does not exist on 8 March: it moves forward by the gap to 03:30 PDT.
    "la_gap_moves_forward": (
        timed("2026-03-07T02:30:00", "2026-03-07T03:00:00", "America/Los_Angeles", "FREQ=DAILY"),
        (utc(2026, 3, 8), utc(2026, 3, 9)),
        ["2026-03-08T10:30:00Z"],
    ),
    # 01:30 happens twice on 1 November: the first (PDT) wins.
    "la_fall_back_first": (
        timed("2026-10-30T01:30:00", "2026-10-30T02:00:00", "America/Los_Angeles", "FREQ=DAILY"),
        (utc(2026, 10, 31, 12), utc(2026, 11, 2, 12)),
        ["2026-11-01T08:30:00Z", "2026-11-02T09:30:00Z"],
    ),
    # London: BST ends 25 October 2026, so a 09:00 weekly meeting moves from 08:00Z to 09:00Z.
    "london_fall_back": (
        timed("2026-10-19T09:00:00", "2026-10-19T09:30:00", "Europe/London", "FREQ=WEEKLY"),
        (utc(2026, 10, 19), utc(2026, 11, 3)),
        ["2026-10-19T08:00:00Z", "2026-10-26T09:00:00Z", "2026-11-02T09:00:00Z"],
    ),
    # London: BST starts 29 March 2026; 01:30 that day does not exist and moves to 02:30 BST (01:30Z).
    "london_spring_gap": (
        timed("2026-03-28T01:30:00", "2026-03-28T02:00:00", "Europe/London", "FREQ=DAILY"),
        (utc(2026, 3, 28), utc(2026, 3, 31)),
        ["2026-03-28T01:30:00Z", "2026-03-29T01:30:00Z", "2026-03-30T00:30:00Z"],
    ),
    # Day 31 skips the months without one (RFC 5545), never rolling into the next month.
    "monthly_day_31": (
        timed("2026-01-31T12:00:00", "2026-01-31T13:00:00", "UTC", "FREQ=MONTHLY;BYMONTHDAY=31"),
        (utc(2026, 1, 1), utc(2026, 8, 1)),
        ["2026-01-31T12:00:00Z", "2026-03-31T12:00:00Z", "2026-05-31T12:00:00Z", "2026-07-31T12:00:00Z"],
    ),
    "second_tuesday": (
        timed("2026-01-13T10:00:00", "2026-01-13T11:00:00", "UTC", "FREQ=MONTHLY;BYDAY=2TU"),
        (utc(2026, 1, 1), utc(2026, 4, 1)),
        ["2026-01-13T10:00:00Z", "2026-02-10T10:00:00Z", "2026-03-10T10:00:00Z"],
    ),
    "every_other_week_mon_wed": (
        timed("2026-11-02T15:00:00", "2026-11-02T15:30:00", "UTC", "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE"),
        (utc(2026, 11, 1), utc(2026, 11, 30)),
        ["2026-11-02T15:00:00Z", "2026-11-04T15:00:00Z", "2026-11-16T15:00:00Z", "2026-11-18T15:00:00Z"],
    ),
    "count_stops": (
        timed("2026-11-02T15:00:00", "2026-11-02T15:30:00", "UTC", "FREQ=DAILY;COUNT=3"),
        (utc(2026, 11, 1), utc(2026, 12, 1)),
        ["2026-11-02T15:00:00Z", "2026-11-03T15:00:00Z", "2026-11-04T15:00:00Z"],
    ),
    # A date UNTIL runs through that whole local day.
    "until_date_inclusive": (
        timed("2026-11-02T23:00:00", "2026-11-02T23:30:00", "America/Los_Angeles", "FREQ=DAILY;UNTIL=20261104"),
        (utc(2026, 11, 1), utc(2026, 12, 1)),
        ["2026-11-03T07:00:00Z", "2026-11-04T07:00:00Z", "2026-11-05T07:00:00Z"],
    ),
    # A UTC UNTIL one second before an instance (what a "following" edit writes) ends the series just before it.
    "until_utc_excludes": (
        timed("2026-11-02T09:00:00", "2026-11-02T09:30:00", "UTC", "FREQ=DAILY;UNTIL=20261104T085959Z"),
        (utc(2026, 11, 1), utc(2026, 12, 1)),
        ["2026-11-02T09:00:00Z", "2026-11-03T09:00:00Z"],
    ),
    "all_day_weekly_dates": (
        all_day("2026-11-06", "2026-11-07", "FREQ=WEEKLY"),
        (utc(2026, 11, 1), utc(2026, 11, 21)),
        ["2026-11-06", "2026-11-13", "2026-11-20"],
    ),
    "single_event_overlap": (
        timed("2026-11-10T23:30:00", "2026-11-11T00:30:00", "UTC"),
        (utc(2026, 11, 11), utc(2026, 11, 12)),
        ["2026-11-10T23:30:00Z"],
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_expand_cases(name):
    event, (start, end), expected = CASES[name]
    assert [i["starts_at"] for i in CR.expand(event, [], start, end)] == expected


def test_instances_last_exactly_as_long_as_the_first_across_a_clock_change():
    event = timed("2026-10-31T01:30:00", "2026-10-31T02:00:00", "America/Los_Angeles", "FREQ=DAILY")
    (inst,) = CR.expand(event, [], utc(2026, 11, 1), utc(2026, 11, 1, 12))
    assert (inst["starts_at"], inst["ends_at"]) == ("2026-11-01T08:30:00Z", "2026-11-01T09:00:00Z")


def test_a_moved_instance_shows_at_its_new_time_even_from_outside_the_window():
    event = timed("2026-11-02T09:00:00", "2026-11-02T09:30:00", "UTC", "FREQ=WEEKLY")
    moved = {
        "original_starts_at": "2026-11-09T09:00:00Z",
        "status": "confirmed",
        "starts_at": "2026-11-17T14:00:00Z",
        "ends_at": "2026-11-17T14:30:00Z",
    }
    found = CR.expand(event, [moved], utc(2026, 11, 15), utc(2026, 11, 22))
    assert [(i["original_starts_at"], i["starts_at"]) for i in found] == [
        ("2026-11-16T09:00:00Z", "2026-11-16T09:00:00Z"),
        ("2026-11-09T09:00:00Z", "2026-11-17T14:00:00Z"),
    ]
    assert found[1]["exception"] is moved
    # Its original slot is empty.
    assert CR.expand(event, [moved], utc(2026, 11, 9), utc(2026, 11, 10)) == []


def test_a_cancelled_instance_is_gone_and_a_stale_exception_is_ignored():
    event = timed("2026-11-02T09:00:00", "2026-11-02T09:30:00", "UTC", "FREQ=DAILY")
    cancelled = {"original_starts_at": "2026-11-03T09:00:00Z", "status": "cancelled"}
    # An exception for a start the rule does not have (the series moved since) shows nothing.
    stray = {
        "original_starts_at": "2026-11-03T10:00:00Z",
        "status": "confirmed",
        "starts_at": "2026-11-03T12:00:00Z",
        "ends_at": "2026-11-03T13:00:00Z",
    }
    found = CR.expand(event, [cancelled, stray], utc(2026, 11, 2), utc(2026, 11, 5))
    assert [i["starts_at"] for i in found] == ["2026-11-02T09:00:00Z", "2026-11-04T09:00:00Z"]


def test_expansion_is_capped_per_series():
    event = timed("2020-01-01T09:00:00", "2020-01-01T09:30:00", "UTC", "FREQ=DAILY")
    found = CR.expand(event, [], utc(2020, 1, 1), utc(2026, 1, 1))
    assert len(found) == CR.CAP == 750
    assert len(CR.expand(event, [], utc(2020, 1, 1), utc(2026, 1, 1), cap=5)) == 5


@pytest.mark.parametrize(
    "text, rule",
    [
        ("weekly on mon,wed", "FREQ=WEEKLY;BYDAY=MO,WE"),
        ("every 2 weeks on fri", "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR"),
        ("monthly on the 2nd tue", "FREQ=MONTHLY;BYDAY=2TU"),
        ("monthly on day 15 until 2027-01-31", "FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20270131"),
        ("daily for 10 times", "FREQ=DAILY;COUNT=10"),
        ("RRULE:interval=2;freq=weekly;byday=we,mo", "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE,MO"),
        ("FREQ=YEARLY;INTERVAL=1", "FREQ=YEARLY"),
    ],
)
def test_parse_repeat_normalizes(text, rule):
    assert CR.parse_repeat(text) == rule


@pytest.mark.parametrize(
    "text",
    [
        "FREQ=HOURLY",
        "FREQ=WEEKLY;BYHOUR=9",
        "FREQ=MONTHLY;BYSETPOS=-1;BYDAY=MO,TU,WE,TH,FR",
        "FREQ=WEEKLY;BYDAY=2TU",
        "FREQ=DAILY;COUNT=3;UNTIL=20261231",
        "FREQ=MONTHLY;BYMONTHDAY=0",
        "FREQ=DAILY;COUNT=0",
        "now and then",
        "",
    ],
)
def test_parse_repeat_refuses_what_it_does_not_support(text):
    with pytest.raises(Problem) as refused:
        CR.parse_repeat(text)
    assert (refused.value.code, refused.value.status) == ("repeat", 422)


def test_last_end_and_instance_lookup():
    event = timed("2026-11-02T09:00:00", "2026-11-02T09:30:00", "Europe/London", "FREQ=WEEKLY;COUNT=3")
    assert CR.last_end(event) == "2026-11-16T09:30:00Z"
    assert CR.last_end({**event, "rrule": "FREQ=WEEKLY"}) is None
    assert CR.instance_at(event, "2026-11-09T09:00:00Z")["original_start_local"] == "2026-11-09T09:00:00"
    assert CR.instance_at(event, "2026-11-23T09:00:00Z") is None  # past COUNT
    assert CR.instance_at(event, "2026-11-09T10:00:00Z") is None
