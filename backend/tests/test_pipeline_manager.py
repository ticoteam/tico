"""The pipeline manager (backend/pipeline_manager.py): a plain-code pass that asks once per visit, asks the backup at the
Limit, pauses the Limit while a ticket is on hold, opens one BotOps task when its passes stop, and takes no action
outside the allowed set. An empty exception list makes no model call."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import hubdb as H
from backend import pipeline_manager as PM
from backend import pipeline_overview as OV
from backend import task_holds
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type

ANA = "human:ana"
STAGES = [
    {"name": "Building", "status": "doing"},
    {"name": "Review", "status": "doing"},
    {"name": "Shipped", "status": "done"},
]


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(at):
    return at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def pipeline(c, manager="coo", every=10, name="Launch"):
    typ = H.type_create(c, ANA, name, STAGES, mover=True)
    c.execute("UPDATE task_types SET manager=?, manager_every_minutes=? WHERE id=?", (manager, every, typ["id"]))
    return typ["id"]


def step(c, type_id, name):
    return c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (type_id, name)).fetchone()[0]


def ticket(c, type_id, stage="Building", created=None):
    row = H.task_create(c, ANA, "Ship the launch page", "", ANA, type=type_id, step=stage, lint=False)
    if created is not None:
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (stamp(created), row["id"]))
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?",
            (stamp(created), row["id"]),
        )
    return H.task(c, row["id"])


def visit(c, task_id, type_id, stage, entered):
    """The ticket's open visit, started at `entered`: the one the engine opened at creation is moved to that time, else
    one is opened (a ticket has one open visit at a time)."""
    open_visit = c.execute("SELECT id FROM stage_visits WHERE task_id=? AND left_at IS NULL", (task_id,)).fetchone()
    if open_visit:
        c.execute(
            "UPDATE stage_visits SET pipeline_stage_id=?, entered_at=? WHERE id=?",
            (step(c, type_id, stage), stamp(entered), open_visit[0]),
        )
        return
    c.execute(
        "INSERT INTO stage_visits (id, task_id, pipeline_id, pipeline_stage_id, direction, entered_by, entered_at) "
        "VALUES (?,?,?,?,'initial',?,?)",
        (f"visit-{task_id}-{entered.timestamp():.0f}", task_id, type_id, step(c, type_id, stage), ANA, stamp(entered)),
    )


def requirement(c, type_id, stage, name="Screenshot", reviewer="human:ben", backup=None):
    rid = f"req-{type_id}-{name}".replace(" ", "-")
    c.execute(
        "INSERT INTO stage_requirements (id, pipeline_id, pipeline_stage_id, name, kind, needs, reviewed_by, reviewer_id, "
        "backup_reviewer_id, position, version, created_by, created_at, updated_at) "
        "VALUES (?,?,?,?,'attachment','{}','person',?,?,1,1,?,?,?)",
        (rid, type_id, step(c, type_id, stage), name, reviewer, backup, ANA, stamp(now()), stamp(now())),
    )
    return rid


def actions(c, action, pipeline_id=None):
    return c.execute(
        "SELECT COUNT(*) FROM pipeline_manager_actions WHERE action=? AND pipeline_id=coalesce(?, pipeline_id)",
        (action, pipeline_id),
    ).fetchone()[0]


def runs(c, pipeline_id):
    return [
        dict(r)
        for r in c.execute(
            "SELECT * FROM pipeline_manager_runs WHERE pipeline_id=? ORDER BY started_at, id", (pipeline_id,)
        ).fetchall()
    ]


def botops_roster(c):
    H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)


def botops_tasks(c):
    return c.execute(
        "SELECT COUNT(*) FROM tasks WHERE owner_id='bot:botops' AND title='Pipeline manager stopped: Launch'"
    ).fetchone()[0]


@pytest.fixture
def woken(monkeypatch):
    """Records the wakes the pipeline manager sends, instead of waking anyone."""
    sent = []

    def fake(conn, task_row, target, body, refs=None, **kwargs):
        if str(kwargs.get("source") or "").startswith("pipeline_manager"):
            sent.append((target, kwargs.get("source"), (refs or {}).get("wake")))
            return {"id": "msg-fake"}  # delivered; _wake returns None when the target cannot read the ticket
        return None

    monkeypatch.setattr(H, "_wake", fake)
    return sent


def test_an_empty_exception_list_is_a_quiet_pass_with_no_model_call(api, woken, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        type_id = pipeline(c)
        ticket(c, type_id)
    PM.tick(store, now())
    with store.read() as c:
        [run] = runs(c, type_id)
    assert (run["status"], run["exceptions_count"], run["actions_count"], run["model_called"]) == ("ok", 0, 0, 0)
    assert woken == []


def test_an_ask_is_made_once_per_visit_and_again_after_a_move_back_then_forward(api, woken):
    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        requirement(c, type_id, "Review")
        task = ticket(c, type_id, stage="Review")
        visit(c, task["id"], type_id, "Review", t0)
    PM.tick(store, t0)
    PM.tick(store, t0 + timedelta(minutes=10))
    with store.read() as c:
        assert actions(c, "ask") == 1
    assert [target for target, _, _ in woken] == ["human:ana"]  # no work yet: the assignee owes it, not the reviewer

    with store.transaction() as c:
        c.execute(
            "UPDATE stage_visits SET left_at=? WHERE task_id=? AND left_at IS NULL",
            (stamp(t0 + timedelta(minutes=15)), task["id"]),
        )
        visit(c, task["id"], type_id, "Review", t0 + timedelta(minutes=20))
    PM.tick(store, t0 + timedelta(minutes=20))
    with store.read() as c:
        assert actions(c, "ask") == 2


def test_a_hold_pauses_the_limit_and_the_clock_starts_again_at_the_release(api):
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
        task_holds.put(c, task["id"], "blocked", stamp(base - timedelta(hours=1)))
        assert PM.over_limit(c, type_id, base) == []
        task_holds.release(c, task["id"], "blocked", stamp(base - timedelta(minutes=30)))
        assert PM.over_limit(c, type_id, base) == []
        [item] = PM.over_limit(c, type_id, base + timedelta(minutes=40))
        assert item["task_id"] == task["id"] and item["limit_hours"] == 1
        assert item["limit_minutes"] == 60  # the Warn after value the rail prefers; limit_hours stays for older clients


def test_one_ticket_is_late_on_the_rail_and_the_overview_at_the_same_instant(api):
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        task = ticket(c, type_id)
        visit(c, task["id"], type_id, "Building", base - timedelta(minutes=60))
        since = "2000-01-01T00:00:00.000000Z"
        for at, late in ((base - timedelta(minutes=1), False), (base, True)):
            rail_late = [item["task_id"] for item in PM.rail(c, type_id, at)["late"]]
            overview = OV._counts(c, [type_id], "1=1", PM.stamp_of(at), since)[type_id]
            assert rail_late == ([task["id"]] if late else []), at
            assert overview["overdue"] == (1 if late else 0), at


def test_the_backup_is_asked_once_when_the_card_is_past_its_limit(api, woken):
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        requirement(c, type_id, "Building", backup="human:chris")
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
    PM.tick(store, base)
    PM.tick(store, base + timedelta(minutes=10))
    with store.read() as c:
        assert actions(c, "ask") == 1
        assert actions(c, "ask_backup") == 1
    assert ("human:chris", "pipeline_manager.ask_backup", "pipeline_manager") in woken


def test_three_missed_passes_open_exactly_one_botops_task(api):
    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        botops_roster(c)
        pid = pipeline(c)
    PM.tick(store, t0)
    PM.watchdog(store, t0 + timedelta(minutes=39))
    with store.read() as c:
        assert botops_tasks(c) == 0
    PM.watchdog(store, t0 + timedelta(minutes=40))
    PM.watchdog(store, t0 + timedelta(minutes=50))
    with store.read() as c:
        assert botops_tasks(c) == 1
        assert actions(c, "open_botops", pipeline_id=pid) == 1


def test_run_due_is_throttled_to_one_round_a_minute_per_store(api, monkeypatch):
    store = api.app.state.store
    rounds = []
    monkeypatch.setattr(PM, "tick", lambda s, at: rounds.append(at))
    monkeypatch.setattr(PM, "watchdog", lambda s, at: None)
    monkeypatch.setattr(PM, "_LAST_RUN", PM.WeakKeyDictionary())
    t0 = now()
    PM.run_due(store, t0)
    PM.run_due(store, t0 + timedelta(seconds=10))
    PM.run_due(store, t0 + timedelta(seconds=61))
    assert len(rounds) == 2


def test_a_pass_that_raises_is_recorded_as_a_failed_run_and_the_others_still_run(api, monkeypatch):
    store = api.app.state.store
    failures: list[dict[str, str]] = []
    with store.transaction() as c:
        broken = pipeline(c, name="Broken")
        healthy = pipeline(c, name="Healthy")

    real = PM._pass

    def flaky(c, pipeline_row, at):
        if pipeline_row["id"] == broken:
            raise RuntimeError("boom")
        return real(c, pipeline_row, at)

    monkeypatch.setattr(PM, "_pass", flaky)
    PM.run_due(store, now(), failures)
    with store.read() as c:
        by_pipeline = {pid: runs(c, pid)[0]["status"] for pid in (broken, healthy)}
    assert by_pipeline == {broken: "failed", healthy: "ok"}
    assert failures == []


def test_only_the_allowed_actions_are_taken(api):
    assert set(PM.EMITTED) <= set(PM.ALLOWED)
    for action in ("hold", "forward", "propose_rule"):
        with pytest.raises(ValueError):
            PM._ask_once(
                None,  # the refusal comes before any write
                pipeline_id="p",
                run_id="r",
                task={"id": "t", "title": ""},
                visit_id="v",
                requirement_id="q",
                action=action,
                target="human:ana",
                text="",
                at="",
            )


def test_the_watchdog_judges_the_missed_passes_before_a_scheduled_round_records_its_own(api, monkeypatch):
    """D2: the dispatcher runs the watchdog first. Recording the round first hid an outage from the watchdog."""
    from backend import factory_manager

    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        botops_roster(c)
        pipeline(c)
    PM.tick(store, t0)
    monkeypatch.setattr(factory_manager, "tick", lambda s, at: None)
    monkeypatch.setattr(PM, "_LAST_RUN", PM.WeakKeyDictionary())
    PM.run_due(store, t0 + timedelta(minutes=40))
    with store.read() as c:
        assert botops_tasks(c) == 1


def test_the_dispatcher_runs_the_watchdog_then_the_pass_then_the_factory_managers(api, monkeypatch):
    from backend import factory_manager

    order = []
    monkeypatch.setattr(PM, "watchdog", lambda s, at: order.append("watchdog"))
    monkeypatch.setattr(PM, "tick", lambda s, at: order.append("tick"))
    monkeypatch.setattr(factory_manager, "tick", lambda s, at: order.append("factory"))
    monkeypatch.setattr(PM, "_LAST_RUN", PM.WeakKeyDictionary())
    PM.run_due(api.app.state.store, now())
    assert order == ["watchdog", "tick", "factory"]


def test_an_undelivered_ask_is_not_recorded_so_the_next_pass_tries_it_again(api, monkeypatch):
    """D5: a wake that reaches nobody (the target cannot read the ticket) consumes no once-per-visit key."""
    store = api.app.state.store
    tries = []

    def undelivered(conn, task_row, target, body, refs=None, **kwargs):
        if str(kwargs.get("source") or "").startswith("pipeline_manager"):
            tries.append(target)
        return None

    monkeypatch.setattr(H, "_wake", undelivered)
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        requirement(c, type_id, "Building", reviewer="human:ben")
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
    for minutes in (0, 10, 20):
        PM.tick(store, base + timedelta(minutes=minutes))
    with store.read() as c:
        assert actions(c, "ask") == 0
    assert len(tries) >= 3


def test_a_requirement_that_failed_twice_in_a_visit_wakes_the_manager_for_one_judgment(api):
    """D6: repeated failed judgments in the open visit are a judgment wake, once per visit and requirement."""
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        req = requirement(c, type_id, "Building", reviewer="human:ben")
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
        visit_id = c.execute(
            "SELECT id FROM stage_visits WHERE task_id=? AND left_at IS NULL", (task["id"],)
        ).fetchone()[0]
        for n in range(PM.REPEATED_FAILURES):
            c.execute(
                "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, stage_visit_id, "
                "work_version, judge, verdict, created_at) VALUES (?,?,?,?,?,?,'llm','failed',?)",
                (f"fail-{n}", task["id"], step(c, type_id, "Building"), req, visit_id, 0, stamp(base)),
            )
    PM.tick(store, base)
    PM.tick(store, base + timedelta(minutes=10))
    with store.read() as c:
        judged = c.execute("SELECT COUNT(*) FROM events WHERE action=?", (PM.JUDGMENT_EVENT,)).fetchone()[0]
    assert judged == 1


def test_over_limit_costs_the_same_number_of_statements_for_two_tickets_as_for_six(api):
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        pid = type_id
        for n in range(2):
            row = H.task_create(c, ANA, f"Ship page {n} early", "", ANA, type=type_id, step="Building", lint=False)
            visit(c, row["id"], type_id, "Building", base - timedelta(hours=2))

    def count(conn):
        statements = []
        conn.set_trace_callback(statements.append)
        PM.over_limit(conn, pid, base)
        conn.set_trace_callback(None)
        return len(statements)

    def add_late(c, n):
        row = H.task_create(c, ANA, f"Ship page {n}", "", ANA, type=type_id, step="Building", lint=False)
        visit(c, row["id"], type_id, "Building", base - timedelta(hours=2))

    with store.read() as c:
        few = count(c)
    with store.transaction() as c:
        for n in range(4):
            add_late(c, n)
    with store.read() as c:
        assert count(c) == few


def test_a_type_created_after_boot_gets_the_default_warn_after_on_its_working_steps(api):
    """Track L: a type made after boot is seeded when it is made, not at the next restart."""
    store = api.app.state.store
    with store.transaction() as c:
        typ = dev_type(c)
        limits = {
            r[0]: r[1]
            for r in c.execute("SELECT name, limit_minutes FROM task_steps WHERE type_id=?", (typ["id"],)).fetchall()
        }
    assert limits["In Progress"] == 240 and limits["QA Fixes"] == 240
    assert limits["Done"] is None and limits["In QA"] is None


def test_a_ticket_with_no_visit_is_late_from_its_move_into_the_step_not_from_its_creation(api):
    """Fix L2-4: a card made at 08:00 and moved into a stage with no requirements (so no visit row) at 12:00 is not Late
    at 12:30 with a 4 h Warn after, and is Late at 16:00."""
    store = api.app.state.store
    t0 = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
    with store.transaction() as c:
        type_id = pipeline(c)
        building = step(c, type_id, "Building")
        c.execute("UPDATE task_steps SET limit_minutes=240 WHERE id=?", (building,))
        task = ticket(c, type_id, created=t0)
        c.execute("DELETE FROM stage_visits WHERE task_id=?", (task["id"],))
        H.history_event(c, "task", task["id"], ANA, "step", None, building, created_at=stamp(t0 + timedelta(hours=4)))
        rows = [H.task(c, task["id"])]
        assert PM.late_tickets(c, rows, t0 + timedelta(hours=4, minutes=30)) == {}
        assert task["id"] in PM.late_tickets(c, rows, t0 + timedelta(hours=8))


def test_waiting_pauses_the_clock_without_a_hold_row(api):
    """Fix L2-5: entered 08:00, waiting 08:10, back to doing 11:59. The clock restarts at 11:59, as it does after a hold
    (test_a_hold_pauses_the_limit...): at 12:58 it is 59 minutes in, and Late at 12:59 with a 60 min Warn after."""
    store = api.app.state.store
    t0 = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        task = ticket(c, type_id, created=t0)
        visit(c, task["id"], type_id, "Building", t0)
        H.history_event(
            c, "task", task["id"], ANA, "status", "doing", "waiting", created_at=stamp(t0 + timedelta(minutes=10))
        )
        H.history_event(
            c,
            "task",
            task["id"],
            ANA,
            "status",
            "waiting",
            "doing",
            created_at=stamp(t0 + timedelta(hours=3, minutes=59)),
        )
        rows = [H.task(c, task["id"])]
        assert PM.late_tickets(c, rows, t0 + timedelta(hours=4, minutes=58)) == {}
        late = PM.late_tickets(c, rows, t0 + timedelta(hours=4, minutes=59))
        assert late[task["id"]]["since"] == stamp(t0 + timedelta(hours=3, minutes=59))
