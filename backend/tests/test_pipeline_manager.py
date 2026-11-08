"""The pipeline manager (backend/pipeline_manager.py): a plain-code pass that asks once per visit, asks the backup at the
Limit, pauses the Limit while a ticket is on hold, opens one BotOps task when its passes stop, and takes no action
outside the allowed set. An empty exception list makes no model call."""

import importlib
from datetime import datetime, timedelta, timezone

import pytest

from backend import hubdb as H
from backend import pipeline_manager as PM
from backend import pipeline_overview as OV
from backend import task_holds
from backend.tests.helpers.api import get  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.dev_ticket_rules import dev_type
from backend.tests.helpers.pipeline_manager import ANA, STAGES, botops_roster, now, pipeline, stamp, step, ticket, visit


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


def test_a_hold_pauses_the_limit_and_the_time_before_and_after_it_both_count(api):
    """D40: a hold pauses the Warn after. 30 active minutes, a 60 minute hold, then 30 more: 60 active, so Late at the
    release-plus-30 mark and not a minute before."""
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
        task_holds.put(c, task["id"], "blocked", stamp(base - timedelta(minutes=90)))
        task_holds.release(c, task["id"], "blocked", stamp(base - timedelta(minutes=30)))
        assert PM.over_limit(c, type_id, base - timedelta(minutes=1)) == []
        [item] = PM.over_limit(c, type_id, base)
        assert item["task_id"] == task["id"] and item["limit_hours"] == 1
        assert item["limit_minutes"] == 60  # the Warn after value the rail prefers; limit_hours stays for older clients


def test_an_open_hold_pauses_the_clock_until_it_is_released(api):
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
        task_holds.put(c, task["id"], "blocked", stamp(base - timedelta(minutes=90)))
        assert PM.over_limit(c, type_id, base + timedelta(hours=5)) == []  # 30 active minutes, then held


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
        botops_roster(c)
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
    """Fix L2-5, D40: entered 08:00, waiting 08:10, back to doing 11:59. The active time is 10 minutes, then 1 more at
    12:00: 11 in all, so an 11 minute Warn after is Late at 12:00 and a 12 minute one is not."""
    store = api.app.state.store
    t0 = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
    with store.transaction() as c:
        type_id = pipeline(c)
        building = step(c, type_id, "Building")
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
        c.execute("UPDATE task_steps SET limit_minutes=11 WHERE id=?", (building,))
        assert PM.late_tickets(c, rows, t0 + timedelta(hours=3, minutes=59)) == {}
        late = PM.late_tickets(c, rows, t0 + timedelta(hours=4))
        assert late[task["id"]]["since"] == stamp(t0)
        c.execute("UPDATE task_steps SET limit_minutes=12 WHERE id=?", (building,))
        assert PM.late_tickets(c, rows, t0 + timedelta(hours=4)) == {}


def test_active_time_before_a_wait_counts_after_it_and_a_new_visit_starts_from_zero(api):
    """Fix L2-5 / Codex 15: 50 active minutes, a 10 minute wait, then 10 active minutes against a 60 minute Warn after: Late.
    A new visit into the stage starts the count from zero again."""
    store = api.app.state.store
    t0 = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        task = ticket(c, type_id, created=t0)
        visit(c, task["id"], type_id, "Building", t0)
        H.history_event(
            c, "task", task["id"], ANA, "status", "doing", "waiting", created_at=stamp(t0 + timedelta(minutes=50))
        )
        H.history_event(
            c, "task", task["id"], ANA, "status", "waiting", "doing", created_at=stamp(t0 + timedelta(minutes=60))
        )
        rows = [H.task(c, task["id"])]
        assert PM.late_tickets(c, rows, t0 + timedelta(minutes=69)) == {}
        assert task["id"] in PM.late_tickets(c, rows, t0 + timedelta(minutes=70))
        visit(c, task["id"], type_id, "Building", t0 + timedelta(minutes=70))
        assert PM.late_tickets(c, rows, t0 + timedelta(minutes=129)) == {}
        assert task["id"] in PM.late_tickets(c, rows, t0 + timedelta(minutes=130))


def test_the_late_list_costs_the_same_queries_for_many_tickets_and_is_capped(api):
    """Fix V-12: visibility is one query over the late ids, so 100 late tickets cost no more queries than 4; the answer is
    capped at LATE_CAP with `more` set; `ids` limits it to the cards asked for."""
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))

    def add(count, start):
        with store.transaction() as c:
            for n in range(start, start + count):
                row = H.task_create(c, ANA, f"Late {n}", "", ANA, type=type_id, step="Building", lint=False)
                visit(c, row["id"], type_id, "Building", base - timedelta(hours=2))

    def selects(wanted=None):
        with store.read() as c:
            statements: list[str] = []
            c.set_trace_callback(statements.append)
            try:
                listing, cursor = PM.late_listing(c, ANA, "1=1", type_id, base, wanted)
            finally:
                c.set_trace_callback(None)
        return sum(1 for s in statements if s.lstrip().upper().startswith("SELECT")), listing, cursor

    add(4, 0)
    few, listing, cursor = selects()
    assert len(listing) == 4 and cursor is None
    add(96, 4)
    many, listing, cursor = selects()
    assert len(listing) == 100 and cursor is None
    assert many == few and many < 15

    add(401, 100)
    _, listing, cursor = selects()
    assert len(listing) == PM.LATE_CAP == 500 and cursor is not None

    picked = [sorted(listing)[0], sorted(listing)[1], "not-late"]
    _, listing, cursor = selects(set(picked))
    assert sorted(listing) == sorted(picked[:2]) and cursor is None


def test_one_asked_ticket_among_5000_is_the_only_row_whose_clock_is_computed(api, monkeypatch):
    """R13: the ids and visibility narrow the tickets before any clock is computed. Asking for one of 5,000 live tickets
    evaluates one row, not the pipeline; the pipeline-wide list reads bounded pages."""
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        ids = [
            H.task_create(c, ANA, f"Live {n}", "", ANA, type=type_id, step="Building", lint=False)["id"]
            for n in range(5000)
        ]
        for ticket in ids:
            visit(c, ticket, type_id, "Building", base - timedelta(hours=2))
    evaluated: list[int] = []
    real = PM.late_tickets

    def counting(c, rows, at):
        evaluated.append(len(rows))
        return real(c, rows, at)

    monkeypatch.setattr(PM, "late_tickets", counting)
    with store.read() as c:
        listing, cursor = PM.late_listing(c, ANA, "1=1", type_id, base, {ids[4321]})
    assert list(listing) == [ids[4321]] and cursor is None
    assert sum(evaluated) == 1

    evaluated.clear()
    with store.read() as c:
        listing, cursor = PM.late_listing(c, ANA, "1=1", type_id, base, None)
    assert len(listing) == PM.LATE_CAP and cursor is not None
    assert max(evaluated) <= PM.LATE_PAGE and sum(evaluated) <= PM.LATE_SCAN


def test_a_manager_set_on_an_old_pipeline_is_not_judged_on_its_first_tick(api):
    """R4: the watchdog's first tick after an update found no ok pass and judged the pipeline from its creation, so every
    managed pipeline got a "stopped" task. A manager with no run yet is not behind."""
    store = api.app.state.store
    with store.transaction() as c:
        botops_roster(c)
        type_id = assign_manager(c)  # assigned now; the pipeline itself is three days old
        c.execute("UPDATE task_types SET created_at=? WHERE id=?", (stamp(now() - timedelta(days=3)), type_id))
    PM.watchdog(store, now())
    with store.read() as c:
        assert botops_tasks(c) == 0


def test_a_members_only_pipelines_stopped_task_names_it_to_no_one_outside_botops(api):
    """F4: the watchdog's task for a members-only pipeline is private to BotOps. A company member who is not on the
    pipeline reads neither the task nor its title in the task list or the Log; the company sees the pipeline's name
    nowhere in the stall's record."""
    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        botops_roster(c)
        pid = pipeline(c, name="Legal")
        c.execute("UPDATE task_types SET pipeline_visibility='members' WHERE id=?", (pid,))
    PM.tick(store, t0)
    PM.watchdog(store, t0 + timedelta(minutes=40))
    with store.read() as c:
        rows = c.execute(
            "SELECT id, private, title, body FROM tasks WHERE owner_id='bot:botops' "
            "AND title LIKE 'Pipeline manager stopped%' AND body LIKE ?",
            (f"%{pid}%",),
        ).fetchall()
        [(tid, private, title, body)] = rows
        assert private == 1
        assert H.task_private_readable(c, "bot:botops", H.task(c, tid))
        # BotOps's managers read its private tasks: the text itself must not name the pipeline (r4 B3).
        assert "Legal" not in title and "Legal" not in body and pid in body
    get(api, f"tasks/{tid}", "cara-test", expected=404)
    assert "Pipeline manager stopped: Legal" not in str(get(api, "tasks", "cara-test"))
    assert "Pipeline manager stopped: Legal" not in str(get(api, "history?limit=200", "cara-test"))


def assign_manager(c, name="Launch"):
    """A pipeline given a manager now: the manager_since trigger stamps the assignment."""
    typ = H.type_create(c, ANA, name, STAGES, mover=True)
    c.execute("UPDATE task_types SET manager=?, manager_every_minutes=10 WHERE id=?", ("coo", typ["id"]))
    return typ["id"]


def test_a_manager_that_never_ran_is_stalled_once_its_grace_from_the_assignment_has_passed(api):
    """F11 (Codex repro 1): a manager assigned ten days ago and never run is stalled. Its clock is the assignment, and
    nothing is missed until one run interval and the stall limit have passed."""
    store = api.app.state.store
    with store.transaction() as c:
        botops_roster(c)
        since = now()
        pid = assign_manager(c)
    with store.read() as c:
        row = dict(c.execute(PM.SELECT_PIPELINE + " WHERE id=?", (pid,)).fetchone())
        assert PM.stalled(c, row, since + timedelta(minutes=30)) == 0
        assert PM.stalled(c, row, since + timedelta(days=10)) >= PM.MISSED_RUNS
    PM.watchdog(store, since + timedelta(days=10))
    with store.read() as c:
        assert botops_tasks(c) == 1


def test_a_run_from_before_the_assignment_is_no_health_history_for_the_new_manager(api):
    """F11 (Codex repro 2): a manager assigned to a pipeline whose last pass was twelve hours ago is not stalled on its
    first tick. The old pass is not this manager's, so it starts no clock."""
    store = api.app.state.store
    with store.transaction() as c:
        botops_roster(c)
        since = now()
        pid = assign_manager(c)
    PM.tick(store, since - timedelta(hours=12))  # a pass from before the assignment
    with store.read() as c:
        row = dict(c.execute(PM.SELECT_PIPELINE + " WHERE id=?", (pid,)).fetchone())
        assert PM.stalled(c, row, since + timedelta(minutes=1)) == 0
    PM.watchdog(store, since + timedelta(minutes=1))
    with store.read() as c:
        assert botops_tasks(c) == 0


def test_a_reassigned_manager_starts_a_new_clock_and_clearing_it_stops_the_clock(api):
    store = api.app.state.store
    with store.transaction() as c:
        pid = assign_manager(c)
        first = c.execute("SELECT manager_since FROM task_types WHERE id=?", (pid,)).fetchone()[0]
        assert first is not None
        c.execute("UPDATE task_types SET manager=NULL WHERE id=?", (pid,))
        assert c.execute("SELECT manager_since FROM task_types WHERE id=?", (pid,)).fetchone()[0] is None
        c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (pid,))
        again = c.execute("SELECT manager_since FROM task_types WHERE id=?", (pid,)).fetchone()[0]
        assert H.parse_ts(again) >= H.parse_ts(first)


def test_the_migration_backfills_existing_managed_pipelines_to_the_migration_time(api):
    """F11: a pipeline managed before the migration is stamped with the upgrade's time, not left without a clock."""
    migration = importlib.import_module("backend.migrations.20261108011313_pipeline_manager_since")
    store = api.app.state.store
    with store.transaction() as c:
        managed = assign_manager(c, name="Managed")
        unmanaged = H.type_create(c, ANA, "Unmanaged", STAGES, mover=True)["id"]
        c.execute("UPDATE task_types SET manager_since=NULL WHERE id=?", (managed,))
        before = now()
        migration.up(c)
        stamps = dict(c.execute("SELECT id, manager_since FROM task_types WHERE id IN (?, ?)", (managed, unmanaged)))
    assert H.parse_ts(stamps[managed]) >= before
    assert stamps[unmanaged] is None


def test_the_pipeline_wide_late_list_reads_a_bounded_window_and_a_cursor_visits_every_ticket_once(api, monkeypatch):
    """F10 (Codex): 5,000 live tickets, none late. One request evaluates at most LATE_SCAN of them and returns a `next`
    cursor; following the cursors evaluates every ticket exactly once."""
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        ids = [
            H.task_create(c, ANA, f"Fresh {n}", "", ANA, type=type_id, step="Building", lint=False)["id"]
            for n in range(5000)
        ]
        for ticket in ids:
            visit(c, ticket, type_id, "Building", base - timedelta(minutes=5))
    evaluated: list[str] = []
    real = PM.late_tickets

    def counting(c, rows, at):
        evaluated.extend(str(r["id"]) for r in rows)
        return real(c, rows, at)

    monkeypatch.setattr(PM, "late_tickets", counting)
    per_request: list[int] = []
    cursor = None
    while True:
        before = len(evaluated)
        query = "" if cursor is None else f"?after={cursor}"
        body = get(api, f"pipelines/{type_id}/late{query}")
        per_request.append(len(evaluated) - before)
        assert body["late"] == {}
        cursor = body["next"]
        if cursor is None:
            break
    assert per_request[0] == PM.LATE_SCAN and max(per_request) <= PM.LATE_SCAN
    assert len(per_request) == 3
    assert len(evaluated) == len(set(evaluated)) == 5000 and set(evaluated) == set(ids)


def test_more_late_tickets_than_the_cap_are_listed_across_cursors_without_a_gap_or_a_repeat(api):
    """F10: 600 late tickets: the first answer lists LATE_CAP of them and a cursor; the cursor's answer lists the rest."""
    store = api.app.state.store
    base = now()
    with store.transaction() as c:
        type_id = pipeline(c)
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        ids = [
            H.task_create(c, ANA, f"Overdue {n}", "", ANA, type=type_id, step="Building", lint=False)["id"]
            for n in range(600)
        ]
        for ticket in ids:
            visit(c, ticket, type_id, "Building", base - timedelta(hours=2))
    listed: list[str] = []
    cursor = None
    requests = 0
    while True:
        with store.read() as c:
            listing, cursor_out = PM.late_listing(c, ANA, "1=1", type_id, base, None, cursor or "")
        requests += 1
        listed.extend(listing)
        cursor = cursor_out
        if cursor is None:
            break
    assert requests == 2
    assert len(listed) == len(set(listed)) == 600 and set(listed) == set(ids)


def judgment_wakes(sent):
    return [target for target, source, _wake in sent if source == "pipeline_manager.judgment"]


def fail_twice(store, base, manager, reviewer="human:ben"):
    """A ticket in an open visit whose one requirement has failed REPEATED_FAILURES times. Returns the pipeline id."""
    with store.transaction() as c:
        type_id = pipeline(c, manager=manager)
        req = requirement(c, type_id, "Building", reviewer=reviewer)
        task = ticket(c, type_id, created=base - timedelta(hours=2))
        visit(c, task["id"], type_id, "Building", base - timedelta(hours=2))
        visit_id = c.execute(
            "SELECT id FROM stage_visits WHERE task_id=? AND left_at IS NULL", (task["id"],)
        ).fetchone()[0]
        for n in range(PM.REPEATED_FAILURES):
            c.execute(
                "INSERT INTO stage_decisions (id, task_id, pipeline_stage_id, stage_requirement_id, stage_visit_id, "
                "work_version, judge, verdict, created_at) VALUES (?,?,?,?,?,?,'llm','failed',?)",
                (f"fail-{manager}-{n}", task["id"], step(c, type_id, "Building"), req, visit_id, 0, stamp(base)),
            )
    return type_id


def test_a_stored_bot_prefixed_manager_is_woken_as_the_bare_bot(api, woken):
    """A7: the settings route stores `bot:<id>`. The judgment wake goes to bot:<id>, not bot:bot:<id>."""
    store = api.app.state.store
    base = now()
    fail_twice(store, base, "bot:coo")
    PM.tick(store, base)
    assert judgment_wakes(woken) == ["bot:coo"]


def test_a_stored_bot_prefixed_manager_gets_its_judgment_wake_delivered_to_the_bot(api):
    """A7 on the real wake: the message is addressed to bot:<id> and is written, not just logged."""
    store = api.app.state.store
    base = now()
    fail_twice(store, base, "bot:coo")
    PM.tick(store, base)
    with store.read() as c:
        recipients = [
            r[0]
            for r in c.execute(
                "SELECT recipient_id FROM messages WHERE refs LIKE ? AND recipient_id LIKE 'bot:%'",
                ('%"wake": "pipeline_manager"%',),
            ).fetchall()
        ]
        judged = c.execute("SELECT COUNT(*) FROM events WHERE action=?", (PM.JUDGMENT_EVENT,)).fetchone()[0]
    assert judged == 1
    assert recipients == ["bot:coo"]


def test_a_manager_change_restarts_the_clock_and_the_new_manager_gets_the_next_judgment(api, woken):
    """A4: after a change the new active bot is woken for a stuck task, and the old one gets no more wakes."""
    store = api.app.state.store
    base = now()
    pid = fail_twice(store, base, "coo")
    PM.tick(store, base)
    with store.transaction() as c:
        c.execute("UPDATE task_types SET manager='ops' WHERE id=?", (pid,))
        changed = c.execute("SELECT manager_since FROM task_types WHERE id=?", (pid,)).fetchone()[0]
    assert H.parse_ts(changed) >= base
    PM.tick(store, base + timedelta(minutes=10))
    PM.tick(store, base + timedelta(minutes=20))
    assert judgment_wakes(woken) == ["bot:coo", "bot:ops"]


def test_an_archived_manager_is_not_woken_but_the_pass_still_runs(api, woken):
    store = api.app.state.store
    base = now()
    pid = fail_twice(store, base, "coo")
    with store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE id='coo'")
    PM.tick(store, base)
    with store.read() as c:
        [run] = runs(c, pid)
    assert judgment_wakes(woken) == []
    assert run["status"] == "ok"


def test_a_missing_manager_is_not_woken(api, woken):
    store = api.app.state.store
    base = now()
    fail_twice(store, base, "no-such-bot")
    PM.tick(store, base)
    assert judgment_wakes(woken) == []
