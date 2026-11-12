"""Calendar's derived occurrences (backend/calendar_derived.py): bot runs, scheduled automations and due dates, read from
the tables that hold them, and the grouping and counts over them. Rows go straight into those tables on the company
fixture, so the tests state the schema they rely on."""

from datetime import datetime, timedelta, timezone

from backend import bot_hours, scheduler
from backend import calendar_derived as D
from backend.bot_hours import stamp
from backend.store import H, encode

UTC = timezone.utc
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def add_run(c, rid, bot, start, end=None, *, task=None, source=None, summary=None, message=None, thread=None):
    """A run as attempts holds it: started at `start`, finished and settled at `end`, or still open when `end` is None."""
    c.execute(
        "INSERT INTO attempts(id,bot_id,created_at,started_at,finished_at,settled_at,trigger,trigger_source,task_id,"
        "thread_id,summary,message_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid,
            bot,
            stamp(start),
            stamp(start),
            stamp(end) if end else None,
            stamp(end) if end else None,
            "message",
            source,
            task,
            thread,
            summary,
            message,
        ),
    )


def add_schedule(c, aid, expr, *, name="Digest", owner="bot:ops", zone="UTC", enabled=1, steps=()):
    flow = {"name": name, "owner": owner, "trigger": {"type": "schedule", "cron": expr, "tz": zone}}
    flow["steps"] = list(steps)
    c.execute(
        "INSERT INTO automations(id,name,owner_id,trigger_type,enabled,builtin,flow,version,created_at,created_by_id,"
        "updated_at) VALUES(?,?,?,'schedule',?,0,?,1,?,'human:ana',?)",
        (aid, name, owner, enabled, encode(flow), stamp(NOW), stamp(NOW)),
    )


def add_length(c, aid, n, minutes_of, start=NOW - timedelta(days=30)):
    """`n` finished runs of automation `aid`, one a day from `start`, each as long as minutes_of(i) minutes."""
    for i in range(n):
        at = start + timedelta(days=i)
        c.execute(
            "INSERT INTO automation_runs(id,automation_id,version,started_at,finished_at,status) VALUES(?,?,1,?,?,'ok')",
            (f"{aid}-run{i}", aid, stamp(at), stamp(at + timedelta(minutes=minutes_of(i)))),
        )


def add_task(c, tid, owner, due, *, title="Write the brief", private=0, status="open"):
    c.execute(
        "INSERT INTO tasks(id,title,owner_id,status,due_at,private,created_at,updated_at) VALUES(?,?,?,'open',?,?,?,?)",
        (tid, title, owner, due, private, stamp(NOW), stamp(NOW)),
    )
    if status != "open":
        # The task engine owns status: only its guard lets the trigger accept the change.
        c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (tid,))
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")


def add_kpi(c, link, kpi, owner, deadline, *, goal_status=None, archived=None):
    goal = f"goal-{link}"
    c.execute(
        "INSERT INTO goals(id,title,owner_id,status,created_at,created_by_id,updated_at) VALUES(?,?,?,?,?,?,?)",
        (goal, "Grow the business", "human:ana", goal_status, stamp(NOW), "human:ana", stamp(NOW)),
    )
    c.execute(
        "INSERT INTO kpis(id,name,created_at,created_by_id,owner_id,archived_at) VALUES(?,?,?,?,?,?)",
        (kpi, f"KPI {kpi}", stamp(NOW), "human:ana", owner, archived),
    )
    c.execute(
        "INSERT INTO goal_kpis(id,goal_id,kpi_id,kind,deadline,created_at,created_by_id,updated_at,updated_by_id) "
        "VALUES(?,?,?,'improve',?,?,'human:ana',?,'human:ana')",
        (link, goal, kpi, deadline, stamp(NOW), stamp(NOW)),
    )


def at(day, hour=0, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_a_running_attempt_ends_at_now(company):
    with company.transaction() as c:
        add_run(c, "r1", "ops", NOW - timedelta(minutes=30))
    with company.read_transaction() as c:
        [occ] = D.runs(c, ["ops"], NOW - timedelta(hours=1), NOW + timedelta(hours=1), NOW)
    assert occ["occurrence_id"] == "run:r1"
    assert occ["kind"] == "run" and occ["home"] == "derived" and occ["planned"] is False
    assert (occ["start"], occ["end"]) == ("2026-11-10T11:30:00Z", "2026-11-10T12:00:00Z")
    assert occ["calendar"] == {"ref": "bot:ops", "type": "bot", "id": "ops", "name": "ops"}


def test_only_runs_of_the_bots_asked_for_that_overlap_the_half_open_window(company):
    with company.transaction() as c:
        add_run(c, "ended-before", "ops", at(10, 9), at(10, 10))
        add_run(c, "ends-at-start", "ops", at(10, 11), at(10, 12))
        add_run(c, "across", "ops", at(10, 11, 50), at(10, 12, 10))
        add_run(c, "inside", "ops", at(10, 12, 30), at(10, 12, 40))
        add_run(c, "starts-at-end", "ops", at(10, 13), at(10, 13, 30))
        add_run(c, "other-bot", "coo", at(10, 12, 30), at(10, 12, 40))
    with company.read_transaction() as c:
        found = D.runs(c, ["ops"], at(10, 12), at(10, 13), NOW)
    assert [occ["occurrence_id"] for occ in found] == ["run:across", "run:inside"]


def test_a_run_is_titled_by_its_automation_then_its_task_then_its_summary(company):
    with company.transaction() as c:
        add_schedule(c, "digest", "0 9 * * *", name="Morning digest")
        add_task(c, "t1", "bot:ops", None, title="Ship the pricing page")
        add_run(c, "by-source", "ops", at(10, 9), at(10, 9, 5), source="automation:digest")
        add_run(c, "by-message", "ops", at(10, 10), at(10, 10, 5), message="m1")
        c.execute(
            "INSERT INTO messages(id,kind,refs,created_at) VALUES('m1','say',?,?)",
            (encode({"automation": "digest"}), stamp(NOW)),
        )
        add_run(c, "by-task", "ops", at(10, 11), at(10, 11, 5), task="t1", thread="th1")
        add_run(c, "by-summary", "ops", at(10, 12), at(10, 12, 5), summary="  Reviewed\n the PR  ")
        add_run(c, "untitled", "ops", at(10, 13), at(10, 13, 5))
    with company.read_transaction() as c:
        found = {occ["occurrence_id"]: occ for occ in D.runs(c, ["ops"], at(10), at(11), NOW)}
    assert found["run:by-source"]["title"] == "Morning digest"
    assert found["run:by-source"]["links"]["automation"] == "digest"
    assert found["run:by-message"]["title"] == "Morning digest"
    assert found["run:by-message"]["links"]["automation"] == "digest"
    assert found["run:by-task"]["title"] == "Ship the pricing page"
    assert found["run:by-task"]["links"] == {
        "thread": "th1",
        "meeting": None,
        "task": "t1",
        "automation": None,
        "attempt": "by-task",
    }
    assert found["run:by-summary"]["title"] == "Reviewed the PR"
    assert found["run:untitled"]["title"] == "Run"


def test_a_schedule_expands_in_its_zone_and_keeps_its_wall_time_across_the_clock_change(company):
    # US clocks moved on 8 March 2026: 09:00 Pacific is 17:00Z before that and 16:00Z after.
    march = datetime(2026, 3, 7, tzinfo=UTC)
    with company.transaction() as c:
        add_schedule(c, "daily", "0 9 * * *", name="Daily check", zone="America/Los_Angeles")
    with company.read_transaction() as c:
        found = D.scheduled(c, ["ops"], march, march + timedelta(days=3), now=datetime(2026, 1, 1, tzinfo=UTC))
    assert [occ["start"] for occ in found] == [
        "2026-03-07T17:00:00Z",
        "2026-03-08T16:00:00Z",
        "2026-03-09T16:00:00Z",
    ]
    first = found[0]
    assert first["planned"] is True and first["kind"] == "run" and first["all_day"] is False
    assert first["calendar"]["ref"] == "bot:ops"
    assert first["time_zone"] == "America/Los_Angeles"
    assert first["links"]["automation"] == "daily"
    assert first["occurrence_id"] == "auto:daily:2026-03-07T17:00:00Z"
    assert first["end"] == "2026-03-07T17:15:00Z", "no finished runs yet: the shortest planned length"


def test_a_schedule_only_from_now_on_and_never_before_it(company):
    with company.transaction() as c:
        add_schedule(c, "daily", "0 9 * * *")
    with company.read_transaction() as c:
        found = D.scheduled(c, ["ops"], at(9), at(13), now=at(11, 12))
    assert [occ["start"] for occ in found] == ["2026-11-12T09:00:00Z"]


def test_disabled_schedules_and_bots_that_are_not_active_are_skipped(company):
    with company.transaction() as c:
        add_schedule(c, "off", "0 9 * * *", enabled=0)
        add_schedule(c, "paused-bot", "0 9 * * *", name="Paused bot's routine")
        add_schedule(c, "assistant-routine", "0 9 * * *", name="Assistant's routine", owner="bot:assistant")
        c.execute("UPDATE bots SET state='paused' WHERE id='ops'")
    with company.read_transaction() as c:
        assert D.scheduled(c, ["ops"], at(11), at(12), now=at(10)) == []
        found = D.scheduled(c, ["assistant"], at(11), at(12), now=at(10))
    assert [occ["title"] for occ in found] == ["Assistant's routine"]


def test_a_schedule_firing_more_often_than_hourly_is_one_pill_per_day(company):
    with company.transaction() as c:
        add_schedule(c, "sweep", "*/30 * * * *", name="Sweep")
    with company.read_transaction() as c:
        found = D.scheduled(c, ["ops"], at(11), at(13), now=at(10))
    assert [(occ["start"], occ["end"], occ["title"], occ["all_day"]) for occ in found] == [
        ("2026-11-11", "2026-11-12", "Sweep ×48", True),
        ("2026-11-12", "2026-11-13", "Sweep ×48", True),
    ]
    assert found[0]["occurrence_id"] == "auto:sweep:2026-11-11"
    assert found[0]["planned"] is True


def test_an_hourly_schedule_is_not_collapsed(company):
    with company.transaction() as c:
        add_schedule(c, "hourly", "0 * * * *", name="Hourly check")
    with company.read_transaction() as c:
        found = D.scheduled(c, ["ops"], at(11), at(12), now=at(10))
    assert len(found) == 24 and not any(occ["all_day"] for occ in found)


def test_a_planned_run_is_as_long_as_the_median_of_the_last_ten_finished_runs(company):
    # The newest ten run 20, 40 ... 200 minutes (median 110); the oldest one, 600 minutes, is past the ten.
    with company.transaction() as c:
        add_schedule(c, "long", "0 9 * * *", name="Long digest")
        add_length(c, "long", 11, lambda i: 600 if i == 0 else (10 - i + 1) * 20)
        # A skipped run never ran: it is newest and 1 minute long, and it is not counted (with it the median is 90).
        c.execute(
            "INSERT INTO automation_runs(id,automation_id,version,started_at,finished_at,status) "
            "VALUES('long-skipped',?,1,?,?,'skipped')",
            ("long", stamp(NOW - timedelta(days=1)), stamp(NOW - timedelta(days=1) + timedelta(minutes=1))),
        )
        add_schedule(c, "short", "0 10 * * *", name="Short check")
        add_length(c, "short", 3, lambda i: 3)
    with company.read_transaction() as c:
        long_run = D.scheduled(c, ["ops"], at(11), at(12), now=at(10))
        short_run = D.scheduled(c, ["ops"], at(11, 10), at(11, 11), now=at(10))
    assert long_run[0]["start"] == "2026-11-11T09:00:00Z"
    assert long_run[0]["end"] == "2026-11-11T10:50:00Z"
    assert short_run[0]["end"] == "2026-11-11T10:15:00Z", "a run of 3 minutes is planned at the 15-minute minimum"


def test_due_lists_open_tasks_and_kpi_deadlines_of_the_owner_only(company):
    with company.transaction() as c:
        add_task(c, "open-ana", "human:ana", "2026-11-12T17:00:00.000Z")
        add_task(c, "closed-ana", "human:ana", "2026-11-12", status="closed")
        add_task(c, "done-ana", "human:ana", "2026-11-12", status="done")
        add_task(c, "ben", "human:ben", "2026-11-12")
        add_task(c, "late", "human:ana", "2026-11-20")
        add_task(c, "ops-open", "bot:ops", "2026-11-13", private=1)
        add_kpi(c, "gk-ana", "kpi-ana", "human:ana", "2026-11-15")
        add_kpi(c, "gk-done-goal", "kpi-done", "human:ana", "2026-11-15", goal_status="done")
        add_kpi(c, "gk-ben", "kpi-ben", "human:ben", "2026-11-15")
        add_kpi(c, "gk-archived", "kpi-archived", "human:ana", "2026-11-15", archived=stamp(NOW))
    with company.read_transaction() as c:
        found = D.due(c, [("human", "ana")], at(11), at(16))
        bot_found = D.due(c, [("bot", "ops")], at(11), at(16))
    assert [occ["occurrence_id"] for occ in found] == ["due:task:open-ana", "due:kpi:gk-ana"]
    task, kpi = found
    assert task["kind"] == "due" and task["all_day"] is True
    assert (task["start"], task["end"]) == ("2026-11-12", "2026-11-13")
    assert task["links"]["task"] == "open-ana" and task["visibility"] == "public"
    assert task["calendar"]["ref"] == "human:ana"
    assert (kpi["start"], kpi["end"], kpi["title"]) == ("2026-11-15", "2026-11-16", "KPI kpi-ana")
    assert [occ["occurrence_id"] for occ in bot_found] == ["due:task:ops-open"]
    assert bot_found[0]["visibility"] == "private" and bot_found[0]["calendar"]["ref"] == "bot:ops"


def test_group_runs_merges_touching_short_runs_and_keeps_long_ones(company):
    def run(rid, bot, start, minutes):
        begin = at(10, 10) + timedelta(minutes=start)
        end = begin + timedelta(minutes=minutes)
        return {
            "occurrence_id": f"run:{rid}",
            "event_id": None,
            "calendar": {"ref": f"bot:{bot}", "type": "bot", "id": bot, "name": bot},
            "kind": "run",
            "planned": False,
            "title": rid,
            "start": begin.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "all_day": False,
        }

    scheduled_pill = {
        "occurrence_id": "auto:x:2026-11-10",
        "kind": "run",
        "planned": True,
        "start": "2026-11-10",
        "end": "2026-11-11",
        "all_day": True,
        "calendar": {"ref": "bot:ops"},
        "title": "x ×3",
    }
    occurrences = [
        run("long", "ops", 0, 30),  # 10:00-10:30, kept
        run("s1", "ops", 35, 5),  # 10:35-10:40
        run("s2", "ops", 40, 3),  # 10:40-10:43, touches s1
        run("s3", "ops", 42, 5),  # 10:42-10:47, overlaps s2
        run("alone", "ops", 120, 3),  # 12:00-12:03, touches nothing
        run("other", "coo", 36, 2),  # 10:36-10:38, another bot's, not merged
        scheduled_pill,
    ]
    out = D.group_runs(occurrences)
    ids = [occ["occurrence_id"] for occ in out]
    assert "run:long" in ids and "run:alone" in ids and "run:other" in ids
    assert "auto:x:2026-11-10" in ids, "a planned pill passes through untouched"
    [block] = [occ for occ in out if occ["occurrence_id"].startswith("runs:")]
    assert block["occurrence_id"] == "runs:ops:2026-11-10T10:35:00Z"
    assert (block["start"], block["end"], block["title"]) == ("2026-11-10T10:35:00Z", "2026-11-10T10:47:00Z", "3 runs")
    assert block["count"] == 3
    assert block["children"] == ["run:s1", "run:s2", "run:s3"]
    assert "run:s1" not in ids and "run:s2" not in ids and "run:s3" not in ids
    assert "count" not in next(occ for occ in out if occ["occurrence_id"] == "run:alone")


def test_month_counts_are_runs_per_bot_per_company_day(company):
    with company.transaction() as c:
        add_run(c, "late-pacific", "ops", at(10, 1, 30), at(10, 1, 40))  # 17:30 on 9 November, Pacific
        add_run(c, "day-one", "ops", at(10, 20), at(10, 20, 10))  # 12:00 on 10 November
        add_run(c, "day-two", "ops", at(10, 21), at(10, 21, 10))
        add_run(c, "coo-day", "coo", at(10, 20), at(10, 20, 10))
        add_run(c, "outside", "ops", at(12, 20), at(12, 20, 10))
    with company.read_transaction() as c:
        counts = D.month_counts(c, ["ops", "coo"], at(9, 0), at(12, 0))
    assert counts == [
        {"bot_id": "ops", "date": "2026-11-09", "count": 1},
        {"bot_id": "coo", "date": "2026-11-10", "count": 1},
        {"bot_id": "ops", "date": "2026-11-10", "count": 2},
    ]


def test_the_typed_names_in_the_module_are_the_helpers_they_stand_for():
    # Each name is a cast of the helper, so at run time it is that very object.
    assert D._stamp is bot_hours.stamp
    assert D._next_due is scheduler.next_due
    assert D._bot is H.bot
    assert D._resolve_bot is H.resolve_bot
    assert D._person_name is H.person_name
