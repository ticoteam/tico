"""The "Before a due date" trigger (backend/automation_due.py, backend/automations.py): one run per due day however often
the scheduler passes, a missed day caught up, an overdue occurrence never holding back the next, month-end and leap
days, the zone the run starts in, the due and occurrence marker on the task it opens, pauses that hold a day without
losing it, a failed day run again, and the idle wake at the run's start."""
import json
from datetime import date, datetime, timezone

import pytest
import time_machine

from backend import ask_deadlines, sleep_idle
from backend import automation_due as D
from backend import automation_engine as E
from backend import automations, cloud_access, hubdb
from backend.tests.automation_fakes import Services
from backend.tests.test_api import api, assign, get, headers, post, ready, runner  # noqa: F401
from backend.tests.test_automation_runs import FIND, make, sql

OPEN = {"id": "open", "type": "action", "do": "create_task", "args": {"title": "Payroll", "owner_id": "coo"}}


def due(api, trigger, steps=(OPEN,), owner="person:ana", at=datetime(2030, 1, 1, tzinfo=timezone.utc)):
    with time_machine.travel(at, tick=False):
        return make(api, list(steps), trigger={"type": "due", **trigger}, owner=owner)


def runs_of(api, aid):
    return sorted(run["trigger_ref"] for run in get(api, f"automations/{aid}/runs")["runs"])


def tick(api, at):
    automations.tick(api.app.state.store, at)


def drain(api):
    api.app.state.store.automations.drain()


LLM = {"id": "draft", "type": "llm", "as": "bot:ops", "prompt": "Draft the payroll note", "out": "reply"}


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


def test_a_task_is_due_at_the_last_second_of_its_day_in_the_zone():
    assert D.due_end(date(2030, 3, 1), "America/New_York") == "2030-03-01T23:59:59-05:00"
    assert D.due_end(date(2030, 3, 1), "UTC") == "2030-03-01T23:59:59+00:00"


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
    drain(api)
    first = sql(api, "SELECT id, status FROM tasks WHERE title='Payroll' ORDER BY created_at")
    assert len(first) == 1 and first[0]["status"] == "open"          # never closed: it is overdue by February
    tick(api, datetime(2030, 2, 28, 0, 0, 5, tzinfo=timezone.utc))   # the clamped February day still runs
    drain(api)
    assert runs_of(api, aid) == ["due:2030-01-31", "due:2030-02-28"]
    assert all(run["status"] == "ok" for run in get(api, f"automations/{aid}/runs")["runs"])
    open_tasks = sql(api, "SELECT id, due_at, occurrence FROM tasks WHERE title='Payroll' AND status='open' ORDER BY due_at")
    assert [row["occurrence"] for row in open_tasks] == [f"{aid}:2030-01-31", f"{aid}:2030-02-28"]
    # The day after January's due day, the bot-handed sweep leaves January's task open: it is that day's deadline.
    for row in open_tasks[:1]:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE tasks SET requester_id='bot:ops', created_at='2030-01-01T00:00:00.000000Z' WHERE id=?", (row["id"],))
    with api.app.state.store.read() as c:
        handed = [t["id"] for t in ask_deadlines.handed_to_people(c, "2030-02-01T00:00:00.000000Z",
                                                                  "2030-02-01T00:00:00.000000Z")]
    assert open_tasks[0]["id"] not in handed


def test_the_task_is_due_at_the_end_of_its_day_and_its_refs_name_the_occurrence(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    task = sql(api, "SELECT due_at, occurrence FROM tasks WHERE title='Payroll'")
    assert [(row["due_at"], row["occurrence"]) for row in task] == [("2030-03-01T23:59:59+00:00", f"{aid}:2030-03-01")]
    refs = sql(api, "SELECT refs FROM messages WHERE json_extract(refs,'$.automation')=?", (aid,))
    assert any('"due_on": "2030-03-01"' in ref["refs"] for ref in refs)


def test_a_due_day_only_routine_without_a_rule_id_stores_no_rule_id(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    refs = sql(api, "SELECT refs FROM messages WHERE json_extract(refs,'$.automation')=?", (aid,))
    assert refs and not any("rule_due_id" in ref["refs"] for ref in refs)


def test_a_step_due_overrides_the_occurrence_due_day(api):
    step = {"id": "open", "type": "action", "do": "create_task",
            "args": {"title": "Payroll", "owner_id": "coo", "due_at": "2030-04-02"}}
    due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"}, steps=[step])
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    assert [row["due_at"] for row in sql(api, "SELECT due_at FROM tasks WHERE title='Payroll'")] == ["2030-04-02"]


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
        c.execute("UPDATE automations SET flow=json_remove(flow,'$.trigger.open_days','$.trigger.tz'),"
                  "next_due_at=NULL WHERE id=?", (aid,))
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
    body = post(api, "automations", {"name": "Bad", "owner_id": "person:ana", "trigger": trigger,
                                     "steps": [OPEN]}, expected=422)
    assert "trigger" in str(body)


# ------------------------------------------------------------------------------------------- holds and retries
@pytest.mark.parametrize("target, reason", [
    ((cloud_access, "routines_paused"), "the company is out of credit"),
    ((automations, "_bot_away"), "its bot is paused"),
])
def test_a_paused_routine_holds_its_days_and_catches_up_after_the_pause(api, monkeypatch, target, reason):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"})
    monkeypatch.setattr(*target, lambda *args: reason)
    tick(api, datetime(2030, 1, 14, 0, 30, tzinfo=timezone.utc))
    tick(api, datetime(2030, 1, 14, 0, 40, tzinfo=timezone.utc))
    assert runs_of(api, aid) == [] and get(api, f"automations/{aid}")["next_due"] == "2030-01-07"
    events = sql(api, "SELECT detail FROM events WHERE action='automation.deferred' AND target=?", (aid,))
    assert [json.loads(row["detail"]) for row in events] == [{"due_on": "2030-01-07", "reason": reason}]
    monkeypatch.undo()
    tick(api, datetime(2030, 1, 14, 0, 50, tzinfo=timezone.utc))
    assert runs_of(api, aid) == ["due:2030-01-07", "due:2030-01-14"]


def test_a_find_routine_waits_for_its_unfinished_run_and_loses_no_day(api):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"}, steps=[FIND, OPEN])
    tick(api, datetime(2030, 1, 14, 0, 30, tzinfo=timezone.utc))      # Jan 7 is queued and not yet drained
    assert runs_of(api, aid) == ["due:2030-01-07"]
    assert get(api, f"automations/{aid}")["next_due"] == "2030-01-14"
    drain(api)
    tick(api, datetime(2030, 1, 14, 0, 40, tzinfo=timezone.utc))
    assert runs_of(api, aid) == ["due:2030-01-07", "due:2030-01-14"]
    assert get(api, f"automations/{aid}")["next_due"] == "2030-01-21"


def test_a_failed_day_is_run_again_hourly_until_it_makes_its_one_task(api, monkeypatch):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    real = hubdb.task_create

    def broken(*args, **kwargs):
        raise ValueError("the office is closed")

    monkeypatch.setattr(hubdb, "task_create", broken)
    with time_machine.travel(datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc), tick=False):
        tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
        drain(api)
    assert [run["status"] for run in get(api, f"automations/{aid}/runs")["runs"]] == ["failed"]
    assert get(api, f"automations/{aid}")["next_due"] is None              # the cursor is not moved back for it
    monkeypatch.setattr(hubdb, "task_create", real)
    with time_machine.travel(datetime(2030, 2, 15, 0, 30, tzinfo=timezone.utc), tick=False):
        tick(api, datetime(2030, 2, 15, 0, 30, tzinfo=timezone.utc))
        drain(api)
    assert len(get(api, f"automations/{aid}/runs")["runs"]) == 1          # not retried within the hour
    with time_machine.travel(datetime(2030, 2, 15, 1, 10, tzinfo=timezone.utc), tick=False):
        tick(api, datetime(2030, 2, 15, 1, 10, tzinfo=timezone.utc))
        drain(api)
    statuses = sorted(run["status"] for run in get(api, f"automations/{aid}/runs")["runs"])
    assert statuses == ["failed", "ok"]
    tasks = sql(api, "SELECT due_at, occurrence FROM tasks WHERE title='Payroll'")
    assert [(row["due_at"], row["occurrence"]) for row in tasks] == [("2030-03-01T23:59:59+00:00", f"{aid}:2030-03-01")]


def test_the_idle_wake_for_a_due_routine_is_its_run_time_not_its_due_day(api):
    due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "America/New_York"})
    with api.app.state.store.read() as c:
        times = sleep_idle.routine_times(c, datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp())
    assert datetime(2030, 2, 15, 5, tzinfo=timezone.utc).timestamp() in times     # 00:00 EST, 14 days before
    assert datetime(2030, 3, 1, tzinfo=timezone.utc).timestamp() not in times


def test_a_due_routine_bot_turn_is_that_occurrences_task(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"},
              steps=[LLM], owner="bot:ops")
    tick(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    drain(api)
    tasks = sql(api, "SELECT due_at, occurrence, owner_id FROM tasks WHERE occurrence=?", (f"{aid}:2030-03-01",))
    assert [(row["due_at"], row["owner_id"]) for row in tasks] == [("2030-03-01T23:59:59+00:00", "bot:ops")]


def test_decide_on_a_due_run_is_given_the_occurrence_fields():
    services = Services()
    services.fields["due-review@1"] = ["due_on", "rule_due_id"]
    flow = {"name": "Review", "owner": "person:ana", "steps": [
        {"id": "d", "type": "decide", "set": "due-review@1", "out": "d1"}],
        "trigger": {"type": "due", "first_due": "2030-03-01", "repeats": "once"}}
    values = {"due_at": {"due_on": "2030-03-01", "first_due": "2030-03-01", "repeats": "once", "open_days": 14,
                      "tz": "UTC", "rule_due_id": "rdd_7", "run_at": "2030-02-15T00:00:00.000000Z"}}
    E.execute(flow, values, services)
    assert services.judged[0]["values"] == {"due_on": "2030-03-01", "rule_due_id": "rdd_7"}


def test_a_run_the_tick_gives_up_on_is_retried_too(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    store = api.app.state.store
    with store.transaction() as c:   # the pass queues the day's run, and nothing drains it
        automations._due_pass(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()),
                              datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    rid = sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))[0]["id"]
    with api.app.state.store.transaction() as c:
        automations._fail(c, rid, None, "2030-02-15T00:00:06.000000Z", "the tick gave up")
    assert get(api, f"automations/{aid}")["next_due_at"] is None              # owed by its run, not by the cursor
    with time_machine.travel(datetime(2030, 2, 15, 1, 10, tzinfo=timezone.utc), tick=False):
        tick(api, datetime(2030, 2, 15, 1, 10, tzinfo=timezone.utc))
        drain(api)
    assert sorted(run["status"] for run in get(api, f"automations/{aid}/runs")["runs"]) == ["failed", "ok"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Payroll' AND occurrence=?", (f"{aid}:2030-03-01",))) == 1


def test_three_failed_runs_give_up_the_day_and_the_next_day_runs(api, monkeypatch):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"})
    real = hubdb.task_create

    def broken(*args, **kwargs):
        raise ValueError("the owner bot was deleted")

    monkeypatch.setattr(hubdb, "task_create", broken)
    for hour in (0, 1, 2):   # the first run, then its hourly retries: each one fails
        at = datetime(2030, 1, 7, hour, 0, 5 + hour, tzinfo=timezone.utc) if hour == 0 else \
            datetime(2030, 1, 7, hour, 10, 0, tzinfo=timezone.utc)
        with time_machine.travel(at, tick=False):
            tick(api, at)
            drain(api)
    assert sorted(run["status"] for run in get(api, f"automations/{aid}/runs")["runs"]) == ["failed"] * 3
    gave_up = sql(api, "SELECT detail FROM events WHERE action='automation.due_gave_up' AND target=?", (aid,))
    assert [json.loads(row["detail"])["due_on"] for row in gave_up] == ["2030-01-07"]
    assert "owner bot was deleted" in json.loads(gave_up[0]["detail"])["error"]
    assert get(api, f"automations/{aid}")["next_due"] == "2030-01-14"
    monkeypatch.setattr(hubdb, "task_create", real)
    with time_machine.travel(datetime(2030, 1, 14, 0, 30, tzinfo=timezone.utc), tick=False):
        tick(api, datetime(2030, 1, 14, 0, 30, tzinfo=timezone.utc))
        drain(api)
    assert runs_of(api, aid).count("due:2030-01-07") == 3 and "due:2030-01-14" in runs_of(api, aid)
    assert [row["occurrence"] for row in sql(api, "SELECT occurrence FROM tasks WHERE title='Payroll' "
                                             "AND status='open'")] == [f"{aid}:2030-01-14"]


def _broken_unless(monkeypatch, fails):
    """task_create raises for the occurrences `fails` names (a predicate on the occurrence marker), else runs."""
    real = hubdb.task_create

    def flaky(*args, **kwargs):
        if fails(str(kwargs.get("occurrence") or "")):
            raise ValueError("the office is closed")
        return real(*args, **kwargs)

    monkeypatch.setattr(hubdb, "task_create", flaky)


def _pass_at(api, at):
    with time_machine.travel(at, tick=False):
        tick(api, at)
        drain(api)


def test_each_failed_day_of_one_catch_up_keeps_its_own_tries(api, monkeypatch):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"})
    _broken_unless(monkeypatch, lambda occurrence: True)
    for at in (datetime(2030, 1, 14, 0, 0, 5, tzinfo=timezone.utc), datetime(2030, 1, 14, 1, 10, tzinfo=timezone.utc),
               datetime(2030, 1, 14, 2, 10, tzinfo=timezone.utc), datetime(2030, 1, 14, 3, 10, tzinfo=timezone.utc)):
        _pass_at(api, at)   # the catch-up, two hourly retries, then one more pass that finds both days finished
    runs = runs_of(api, aid)
    assert runs.count("due:2030-01-07") == 3 and runs.count("due:2030-01-14") == 3
    assert sorted(run["status"] for run in get(api, f"automations/{aid}/runs")["runs"]) == ["failed"] * 6
    gave_up = sql(api, "SELECT detail FROM events WHERE action='automation.due_gave_up' AND target=?", (aid,))
    assert sorted(json.loads(row["detail"])["due_on"] for row in gave_up) == ["2030-01-07", "2030-01-14"]
    assert get(api, f"automations/{aid}")["next_due"] == "2030-01-21"     # the walk went on past both


def test_one_failed_day_recovers_while_the_other_day_keeps_its_own_schedule(api, monkeypatch):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"})
    _broken_unless(monkeypatch, lambda occurrence: occurrence.endswith("2030-01-07"))
    _pass_at(api, datetime(2030, 1, 14, 0, 0, 5, tzinfo=timezone.utc))      # 01-14 made; 01-07 fails
    assert [row["occurrence"] for row in sql(api, "SELECT occurrence FROM tasks WHERE title='Payroll'")] == [
        f"{aid}:2030-01-14"]
    monkeypatch.undo()
    _pass_at(api, datetime(2030, 1, 14, 1, 10, tzinfo=timezone.utc))       # 01-07 is owed its retry, and repaired
    assert sorted(row["occurrence"] for row in sql(api, "SELECT occurrence FROM tasks WHERE title='Payroll'")) == [
        f"{aid}:2030-01-07", f"{aid}:2030-01-14"]
    refs = {ref: [run["status"] for run in get(api, f"automations/{aid}/runs")["runs"] if run["trigger_ref"] == ref]
            for ref in ("due:2030-01-07", "due:2030-01-14")}
    assert sorted(refs["due:2030-01-07"]) == ["failed", "ok"] and refs["due:2030-01-14"] == ["ok"]
    assert not sql(api, "SELECT id FROM events WHERE action='automation.due_gave_up' AND target=?", (aid,))


def test_a_failed_run_of_a_routine_switched_to_a_schedule_moves_nothing(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    store = api.app.state.store
    with store.transaction() as c:
        automations._due_pass(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()),
                              datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    rid = sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))[0]["id"]
    api.patch(f"/api/v2/automations/{aid}", json={"trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}},
              headers=headers())
    before = sql(api, "SELECT next_due_at FROM automations WHERE id=?", (aid,))
    with store.transaction() as c:
        automations._fail(c, rid, None, "2030-02-15T00:00:06.000000Z", "the office is closed")
    assert sql(api, "SELECT next_due_at FROM automations WHERE id=?", (aid,)) == before
    assert not sql(api, "SELECT id FROM events WHERE action='automation.due_gave_up' AND target=?", (aid,))


def test_a_failed_run_under_changed_dates_neither_counts_nor_moves_the_cursor(api):
    aid = due(api, {"first_due": "2030-01-07", "repeats": "weekly", "open_days": 0, "tz": "UTC"})
    store = api.app.state.store
    with store.transaction() as c:
        automations._due_pass(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()),
                              datetime(2030, 1, 7, 0, 0, 5, tzinfo=timezone.utc))
    rid = sql(api, "SELECT id FROM automation_runs WHERE automation_id=?", (aid,))[0]["id"]
    api.patch(f"/api/v2/automations/{aid}", json={"trigger": {"type": "due", "first_due": "2030-01-14",
                                                                "repeats": "weekly", "open_days": 0, "tz": "UTC"}},
              headers=headers())
    before = get(api, f"automations/{aid}")["next_due_at"]
    with store.transaction() as c:
        automations._fail(c, rid, None, "2030-01-07T00:00:06.000000Z", "the office is closed")
    assert get(api, f"automations/{aid}")["next_due_at"] == before
    assert not sql(api, "SELECT id FROM events WHERE action='automation.due_gave_up' AND target=?", (aid,))
    _pass_at(api, datetime(2030, 2, 15, 1, 10, tzinfo=timezone.utc))
    assert runs_of(api, aid).count("due:2030-01-07") == 1   # not owed again under the new dates


def test_a_retry_after_a_later_step_failed_keeps_the_task_its_first_attempt_made(api, monkeypatch):
    second = {"id": "second", "type": "action", "do": "create_task", "args": {"title": "Second", "owner_id": "coo"}}
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"}, steps=[OPEN, second])
    real = hubdb.task_create
    failed = []

    def once_more_fails(*args, **kwargs):
        if args[2] == "Second" and not failed:
            failed.append(True)
            raise ValueError("the second step broke")
        return real(*args, **kwargs)

    monkeypatch.setattr(hubdb, "task_create", once_more_fails)
    _pass_at(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    assert [run["status"] for run in get(api, f"automations/{aid}/runs")["runs"]] == ["failed"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Payroll' AND occurrence=?", (f"{aid}:2030-03-01",))) == 1
    _pass_at(api, datetime(2030, 2, 15, 1, 10, tzinfo=timezone.utc))
    assert sorted(run["status"] for run in get(api, f"automations/{aid}/runs")["runs"]) == ["failed", "ok"]
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Payroll' AND occurrence=?", (f"{aid}:2030-03-01",))) == 1
    assert len(sql(api, "SELECT id FROM tasks WHERE title='Second'")) == 1


def test_the_same_occurrence_and_step_is_one_task_however_often_it_is_made(api):
    store = api.app.state.store
    marker = "a1:2030-03-01"
    with store.transaction() as c:
        first = hubdb.task_create(c, "human:ana", "Payroll", "Payroll", "human:ana", lint=False, deduplicate=False,
                                  occurrence=marker, occurrence_step="open")
        again = hubdb.task_create(c, "human:ana", "Payroll", "Payroll", "human:ana", lint=False, deduplicate=False,
                                  occurrence=marker, occurrence_step="open")
        other = hubdb.task_create(c, "human:ana", "Payroll", "Payroll", "human:ana", lint=False, deduplicate=False,
                                  occurrence=marker, occurrence_step="other")
    assert again["id"] == first["id"] != other["id"]
    assert len(sql(api, "SELECT id FROM tasks WHERE occurrence=?", (marker,))) == 2


def test_a_bot_turn_retried_for_the_same_occurrence_step_reuses_its_task(api):
    store = api.app.state.store
    marker = "a2:2030-03-01"
    turn = {"automation_run": "run_1", "automation_step": "draft", "automation_key": "k1", "automation": "a2"}
    with store.transaction() as c:
        first = hubdb.task_create(c, "human:ana", "Draft", "Draft", "human:ana", lint=False, deduplicate=False,
                                  occurrence=marker, occurrence_step="draft", wake_refs=turn)
        retry = hubdb.task_create(c, "human:ana", "Draft", "Draft", "human:ana", lint=False, deduplicate=False,
                                  occurrence=marker, occurrence_step="draft",
                                  wake_refs={**turn, "automation_run": "run_2"})
    assert retry["id"] == first["id"]
    assert len(sql(api, "SELECT id FROM tasks WHERE occurrence=?", (marker,))) == 1


def test_a_bot_occurrence_due_in_two_weeks_is_stalled_when_idle_and_a_plain_task_stays_parked(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    store = api.app.state.store
    bot_step = {"id": "open", "type": "action", "do": "create_task", "args": {"title": "Payroll", "owner_id": "bot:ops"}}
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"}, steps=[bot_step])
    _pass_at(api, datetime(2030, 2, 15, 0, 0, 5, tzinfo=timezone.utc))
    occurrence = sql(api, "SELECT id FROM tasks WHERE occurrence=?", (f"{aid}:2030-03-01",))[0]["id"]
    with store.transaction() as c:
        plain = hubdb.task_create(c, "human:ana", "Plain", "Plain", "bot:ops", lint=False, deduplicate=False,
                                  due="2030-03-01T23:59:59+00:00")["id"]
        c.execute("UPDATE tasks SET updated_at=? WHERE id IN (?, ?)",
                  ("2030-02-15T00:00:00.000000Z", occurrence, plain))
    at = datetime(2030, 2, 15, 1, 0, tzinfo=timezone.utc)
    with store.transaction() as c:   # the creates queued wakes that would make the bot busy: this checks the stall rule
        c.execute("DELETE FROM jobs WHERE state='queued'")
    with store.read() as c:
        stalled = [row["id"] for row in hubdb.stalled_tasks(c, at=at)]
    assert occurrence in stalled and plain not in stalled


def test_a_manual_run_with_no_input_is_the_next_due_days_occurrence(api):
    aid = due(api, {"first_due": "2030-03-01", "repeats": "once", "open_days": 14, "tz": "UTC"})
    with time_machine.travel(datetime(2030, 2, 15, 9, 0, tzinfo=timezone.utc), tick=False):
        post(api, f"automations/{aid}/run", {})
    tasks = sql(api, "SELECT due_at, occurrence FROM tasks WHERE title='Payroll'")
    assert [(row["due_at"], row["occurrence"]) for row in tasks] == [("2030-03-01T23:59:59+00:00", f"{aid}:2030-03-01")]
