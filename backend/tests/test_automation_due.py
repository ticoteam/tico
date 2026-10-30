"""The "Before a due date" trigger (backend/automation_due.py, backend/automations.py): one run per due day however often
the scheduler passes, a missed day caught up, an overdue occurrence never holding back the next, month-end and leap
days, the zone the run starts in, and the due day and occurrence refs on the task it opens."""
from datetime import date, datetime, timezone

import pytest
import time_machine

from backend import automation_due as D
from backend import automations
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_automation_runs import make, sql

OPEN = {"id": "open", "type": "action", "do": "create_task", "args": {"title": "Payroll", "owner": "coo"}}


def due(api, trigger, steps=(OPEN,), owner="person:ana", at=datetime(2030, 1, 1, tzinfo=timezone.utc)):
    with time_machine.travel(at, tick=False):
        return make(api, list(steps), trigger={"type": "due", **trigger}, owner=owner)


def runs_of(api, aid):
    return sorted(run["trigger_ref"] for run in get(api, f"automations/{aid}/runs")["runs"])


def tick(api, at):
    automations.tick(api.app.state.store, at)


# ------------------------------------------------------------------------------------------- the dates (pure)
def test_a_monthly_routine_from_the_31st_falls_on_the_last_day_and_returns_to_the_31st():
    first = date(2030, 1, 31)
    days = [D.occurrence(first, "monthly", n) for n in range(4)]
    assert days == [date(2030, 1, 31), date(2030, 2, 28), date(2030, 3, 31), date(2030, 4, 30)]


def test_a_yearly_routine_from_leap_day_falls_on_the_28th_in_other_years():
    first = date(2028, 2, 29)
    assert [D.occurrence(first, "yearly", n) for n in (1, 2, 3, 4)] == [
        date(2029, 2, 28), date(2030, 2, 28), date(2031, 2, 28), date(2032, 2, 29)]


def test_quarterly_weekly_and_once_step_as_their_repeat_says():
    assert D.occurrence(date(2030, 11, 15), "quarterly", 1) == date(2031, 2, 15)
    assert D.occurrence(date(2030, 1, 7), "weekly", 2) == date(2030, 1, 21)
    assert D.occurrence(date(2030, 1, 7), "once", 0) == date(2030, 1, 7)
    assert D.occurrence(date(2030, 1, 7), "once", 1) is None


def test_the_next_day_from_a_cursor_skips_days_already_passed():
    assert D.first_on_or_after(date(2030, 1, 31), "monthly", date(2030, 2, 1)) == date(2030, 2, 28)
    assert D.first_on_or_after(date(2030, 1, 31), "monthly", date(2030, 2, 28)) == date(2030, 2, 28)
    assert D.first_on_or_after(date(2030, 3, 1), "weekly", date(2030, 1, 1)) == date(2030, 3, 1)
    assert D.next_after(date(2030, 1, 7), "weekly", date(2030, 1, 7)) == date(2030, 1, 14)


def test_the_run_starts_at_midnight_in_the_zone_the_lead_time_before_the_due_day():
    at = D.fire_at(date(2030, 3, 1), 14, "America/New_York")
    assert at == datetime(2030, 2, 15, 5, 0, tzinfo=timezone.utc)   # 00:00 EST, not UTC midnight


# ------------------------------------------------------------------------------------------- the runs
def test_one_run_per_due_day_across_repeated_and_concurrent_passes(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    store = api.app.state.store
    with store.read() as c:   # the routine as a pass reads it before the day's run is written
        stale = dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
    tick(api, datetime(2030, 2, 15, 0, 0, 30, tzinfo=timezone.utc))
    tick(api, datetime(2030, 2, 15, 0, 0, 40, tzinfo=timezone.utc))   # a repeated pass
    with store.transaction() as c:                                    # a concurrent pass, from the stale read
        automations._due_pass(c, stale, datetime(2030, 2, 15, 0, 1, tzinfo=timezone.utc))
    store.automations.drain()
    assert runs_of(api, aid) == ["due:2030-03-01"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Payroll'")) == 1


def test_a_missed_day_catches_up_on_the_next_pass_and_the_next_occurrence_waits_its_time(api):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"})
    tick(api, datetime(2030, 1, 20, 0, 30, tzinfo=timezone.utc))      # the server was down since New Year's
    assert runs_of(api, aid) == ["due:2030-01-07", "due:2030-01-14"]
    assert get(api, f"automations/{aid}")["next_due"] == "2030-01-21"
    tick(api, datetime(2030, 1, 21, 0, 0, 10, tzinfo=timezone.utc))
    assert runs_of(api, aid) == ["due:2030-01-07", "due:2030-01-14", "due:2030-01-21"]


def test_an_overdue_occurrence_stays_open_and_never_blocks_the_next(api):
    aid = due(api, {"first_due": "2030-01-31", "repeats": "monthly", "open_days": 0, "tz": "UTC"})
    tick(api, datetime(2030, 1, 31, 0, 0, 5, tzinfo=timezone.utc))
    first = sql(api, "SELECT id, status FROM tasks WHERE title='Payroll' ORDER BY created")
    assert len(first) == 1 and first[0]["status"] == "open"          # never closed: it is overdue by February
    tick(api, datetime(2030, 2, 28, 0, 0, 5, tzinfo=timezone.utc))   # the clamped February day still runs
    assert runs_of(api, aid) == ["due:2030-01-31", "due:2030-02-28"]
    assert all(run["status"] == "ok" for run in get(api, f"automations/{aid}/runs")["runs"])


def test_the_task_is_due_on_the_due_day_and_its_refs_name_the_occurrence_and_rule(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC",
                    "rule_due_id": "rdd_7"})
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    task = sql(api, "SELECT due FROM tasks WHERE title='Payroll'")
    assert [row["due"] for row in task] == ["2030-03-01"]
    refs = sql(api, "SELECT refs_json FROM messages WHERE json_extract(refs_json,'$.automation')=?", (aid,))
    assert any('"due_on": "2030-03-01"' in ref["refs_json"] and '"rule_due_id": "rdd_7"' in ref["refs_json"]
               for ref in refs)


def test_a_due_day_only_routine_without_a_rule_id_stores_no_rule_id(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    refs = sql(api, "SELECT refs_json FROM messages WHERE json_extract(refs_json,'$.automation')=?", (aid,))
    assert refs and not any("rule_due_id" in ref["refs_json"] for ref in refs)


def test_a_step_due_overrides_the_occurrence_due_day(api):
    step = {"id": "open", "type": "action", "do": "create_task",
            "args": {"title": "Payroll", "owner": "coo", "due": "2030-04-02"}}
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"}, steps=[step])
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    assert [row["due"] for row in sql(api, "SELECT due FROM tasks WHERE title='Payroll'")] == ["2030-04-02"]


def test_a_routine_whose_due_day_has_passed_starts_nothing_and_a_today_one_starts_at_once(api):
    old = due(api, {"first_due": "2029-12-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    assert get(api, f"automations/{old}")["next_due"] is None
    assert get(api, f"automations/{old}")["next_run"] is None
    today = due(api, {"first_due": "2030-01-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    tick(api, datetime(2030, 1, 1, 0, 0, 5, tzinfo=timezone.utc))
    assert runs_of(api, today) == ["due:2030-01-01"]
    assert runs_of(api, old) == []


def test_the_next_due_day_and_run_time_show_on_the_routine(api):
    with time_machine.travel(datetime(2030, 1, 1, tzinfo=timezone.utc), tick=False):
        aid = make(api, [OPEN], trigger={"type": "due", "first_due": "2030-03-01", "repeats": "yearly",
                                         "tz": "America/New_York"}, owner="person:ana")
    shown = get(api, f"automations/{aid}")
    assert shown["next_due"] == "2030-03-01"
    assert shown["next_run"] == "2030-02-15T05:00:00.000000Z"
    assert shown["trigger"]["open_days"] == 14


def test_a_routine_saved_without_the_new_fields_still_loads_and_runs(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE automations SET flow_json=json_remove(flow_json,'$.trigger.open_days','$.trigger.tz'),"
                  "next_due=NULL WHERE id=?", (aid,))
    assert get(api, f"automations/{aid}")["trigger"] == {"type": "due", "first_due": "2030-03-01", "repeats": "once"}
    tick(api, datetime(2030, 2, 15, 12, 0, tzinfo=timezone.utc))      # 14 days before, company zone midnight
    assert runs_of(api, aid) == ["due:2030-03-01"]


# ------------------------------------------------------------------------------------------- refusals
@pytest.mark.parametrize("trigger", [
    {"type": "due", "first_due": "2030-02-30", "repeats": "once"},
    {"type": "due", "first_due": "2030-01-31", "repeats": "daily"},
    {"type": "due", "first_due": "2030-01-31", "repeats": "monthly", "open_days": -1},
    {"type": "due", "first_due": "2030-01-31", "repeats": "monthly", "open_days": 400},
    {"type": "due", "first_due": "2030-01-31", "repeats": "monthly", "tz": "Mars/Olympus"},
])
def test_a_bad_due_trigger_is_refused_on_save(api, trigger):
    body = post(api, "automations", {"name": "Bad", "owner": "person:ana", "trigger": trigger,
                                     "steps": [OPEN]}, expected=422)
    assert "trigger" in str(body)
