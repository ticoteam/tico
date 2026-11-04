"""A Dev ticket long at a working step reaches its type's manager once when it runs late (backend/step_age.py): four hours at
the step. No owner is woken for a status: the daily task check lists quiet tasks once a day instead. Time away from work
(waiting) is not counted."""

from backend import step_age as SA
from backend import wake_rules as W
from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type


def ticket(c, title="Fix the connection", owner="bot:cpo", manager="coo"):
    found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
    typ = {"id": found[0]} if found else dev_type(c)
    if manager is None:
        c.execute("UPDATE task_types SET manager=NULL WHERE id=?", (typ["id"],))
    else:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", (manager, typ["id"]))
    row = H.task_create(c, "bot:ops", title, "Build.", owner, type=typ["id"], step="In Progress",
                        lint=False)
    return H.task(c, row["id"])


def late_events(c, tid):
    return c.execute("SELECT * FROM events WHERE action=? AND target=?", (SA.LATE_EVENT, tid)).fetchall()


def at_checkpoint(c, row):
    """The owner's last turn settled here, so the plain stall and status checks are held (wake_rules.same_state)."""
    c.execute("UPDATE tasks SET checkpoint_state=?,checkpoint_by_id=?,checkpoint_at=updated_at WHERE id=?",
              (W.state_of(c, row), row["owner_id"], row["id"]))


def later(hours):
    return H.shift(H.now(), hours=hours)


def wakes(c, tid, to, kind):
    return [m for m in c.execute(
        "SELECT * FROM messages WHERE recipient_id=? AND json_extract(refs,'$.task')=? "
        "AND json_extract(refs,'$.wake')=?", (to, tid, kind)).fetchall()]


def queued(c, message):
    return bool(c.execute("SELECT 1 FROM jobs WHERE message_id=? AND state='queued'", (message["id"],)).fetchone())


def test_a_quiet_ticket_at_its_checkpoint_is_never_asked_for_a_status_and_its_late_notice_reaches_the_manager_once(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        at_checkpoint(c, row)
        assert W.at_checkpoint(c, H.task(c, row["id"]), "bot:cpo")
        assert SA.sweep(c, later(1)) == {"late": [], "untold": [], "failed": []}
        assert SA.sweep(c, later(2.5)) == {"late": [], "untold": [], "failed": []}, "no status wake at two hours"
        assert wakes(c, row["id"], "bot:cpo", "step_status") == []
        assert SA.sweep(c, later(4.5)) == {"late": [row["id"]], "untold": [], "failed": []}
        # Once: later sweeps neither tell the manager again nor ask the owner.
        assert SA.sweep(c, later(9)) == {"late": [], "untold": [], "failed": []}
        assert wakes(c, row["id"], "bot:cpo", "step_status") == []
        [late] = wakes(c, row["id"], "bot:coo", "blocker")
        assert "In Progress" in late["body"]
        assert queued(c, late)


def test_time_away_from_work_restarts_the_clock(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        for ts in c.execute("SELECT id FROM history_events WHERE subject_type='task' AND subject_id=?", (row["id"],)).fetchall():
            c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND id=?", (later(-6), ts[0]))
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (later(-6), row["id"]))
        H.task_update(c, "bot:cpo", row["id"], status="waiting", note="Held at the gate")
        H.task_update(c, "bot:cpo", row["id"], status="doing")
        back = SA.since_map(c, [H.task(c, row["id"])])[row["id"]]
        assert back > later(-1)
        assert SA.sweep(c, later(1))["late"] == []
        assert SA.sweep(c, later(4.5))["late"] == [row["id"]]


def test_a_late_ticket_with_no_manager_is_nobody_told_and_stays_retryable(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c, "Fix the unowned connection", manager=None)
        assert SA._manager(c, H.task(c, row["id"])) is None
        result = SA.sweep(c, later(4.5))
        assert result["late"] == [] and result["untold"] == [row["id"]]
        assert late_events(c, row["id"]) == []
        assert wakes(c, row["id"], "bot:coo", "blocker") == []
        # A manager appears for the same stay: exactly one notice, then silence.
        c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (row["type_id"],))
        result = SA.sweep(c, later(4.6))
        assert result["late"] == [row["id"]] and result["untold"] == []
        [late] = wakes(c, row["id"], "bot:coo", "blocker")
        assert "In Progress" in late["body"]
        assert queued(c, late)
        assert SA.sweep(c, later(9)) == {"late": [], "untold": [], "failed": []}


def test_a_late_private_ticket_the_manager_cannot_read_is_not_recorded(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c, "Fix the private connection")
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (row["id"],))
        assert not H.task_private_readable(c, "bot:coo", H.task(c, row["id"]))
        assert SA.sweep(c, later(4.5)) == {"late": [], "untold": [row["id"]], "failed": []}
        assert late_events(c, row["id"]) == []
        assert wakes(c, row["id"], "bot:coo", "blocker") == []
        # Readable again for the same stay: exactly one notice, then silence.
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (row["id"],))
        result = SA.sweep(c, later(4.6))
        assert result["late"] == [row["id"]] and result["untold"] == []
        [late] = wakes(c, row["id"], "bot:coo", "blocker")
        assert "In Progress" in late["body"]
        assert queued(c, late)
        assert SA.sweep(c, later(9)) == {"late": [], "untold": [], "failed": []}


def test_a_manager_who_is_the_owner_is_never_told(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c, "Fix the own-managed connection", manager="cpo")
        assert SA._manager(c, H.task(c, row["id"])) is None
        result = SA.sweep(c, later(4.5))
        assert result["late"] == [] and result["untold"] == [row["id"]]
        assert late_events(c, row["id"]) == []
        assert wakes(c, row["id"], "bot:cpo", "blocker") == []


def test_a_late_notice_that_fails_is_rolled_back_and_the_next_ticket_is_still_told(api, monkeypatch):
    with api.app.state.store.transaction() as c:
        bad, good = ticket(c, "Fix the broken connection"), ticket(c, "Fix the other connection")
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (later(-6), bad["id"]))
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (later(-5), good["id"]))
    failing = {"on": True}
    wake = H._wake

    def flaky(c, row, to, body, refs=None, **kw):
        if failing["on"] and row["id"] == bad["id"] and (refs or {}).get("step_late"):
            raise RuntimeError("synthetic bad row")
        return wake(c, row, to, body, refs, **kw)

    monkeypatch.setattr(H, "_wake", flaky)
    with api.app.state.store.transaction() as c:
        assert SA.sweep(c, later(4.5)) == {"late": [good["id"]], "untold": [], "failed": [bad["id"]]}
        # The failed row's notice went with its savepoint: no sent marker for it.
        assert late_events(c, bad["id"]) == []
        assert wakes(c, bad["id"], "bot:coo", "blocker") == []
        failing["on"] = False
        assert SA.sweep(c, later(4.6)) == {"late": [bad["id"]], "untold": [], "failed": []}
        assert len(wakes(c, bad["id"], "bot:coo", "blocker")) == 1
        assert SA.sweep(c, later(9)) == {"late": [], "untold": [], "failed": []}


def test_the_scheduler_tick_runs_the_step_age_sweep(api, monkeypatch):
    from datetime import datetime, timezone

    from backend import step_age
    from backend.scheduler import Scheduler
    calls = []

    def spy(c, at):
        calls.append(at)
        return {"late": [], "untold": [], "failed": []}

    monkeypatch.setattr(step_age, "sweep", spy)
    Scheduler(api.app.state.store, api.app.state.execution).tick(datetime(2026, 9, 10, 17, tzinfo=timezone.utc))
    assert len(calls) == 1


def test_the_board_reads_when_a_working_ticket_entered_its_step(api):
    with api.app.state.store.transaction() as c:
        working = ticket(c)
        queued = H.task_create(c, "bot:ops", "Next", "Later.", "bot:cpo", type=working["type_id"], step="In QA",
                               lint=False)
    rows = {t["id"]: t for t in get(api, "tasks?status=all&brief=true")["tasks"]}
    assert rows[working["id"]]["step_since"]
    assert "step_since" not in rows[queued["id"]]
    assert get(api, "tasks/" + working["id"])["task"]["step_since"] == rows[working["id"]]["step_since"]


def test_a_steps_own_hours_time_it_on_any_board_and_override_the_dev_default(api):
    from backend import step_flow
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        c.execute("UPDATE task_steps SET flow=? WHERE id=?", ('{"status_hours": 6, "late_hours": 8}', row["step_id"]))
        assert SA.sweep(c, later(4.5)) == {"late": [], "untold": [], "failed": []}, "not late at 4.5 h: its own 8 h, not the default 4"
        assert SA.sweep(c, later(8.5))["late"] == [row["id"]]
        # A step of a type that is not engineering is timed only by its own setting, at whatever status it has.
        typ = H.type_create(c, H.KEEPER, "Content", [{"name": "Drafting", "status": "open"}], bots="work")
        step_flow.apply(c, typ["id"], {"Drafting": {"late_hours": 1}})
        draft = H.task_create(c, "bot:ops", "Draft the launch mail", "x", "bot:cpo", type=typ["id"], lint=False)
        result = SA.sweep(c, later(1.5))
        assert draft["id"] in result["late"] + result["untold"]


def test_a_manager_who_has_just_been_at_a_late_ticket_is_told_only_if_it_stays_late(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        # The manager's own note on it just before it turned late.
        c.execute("INSERT INTO history_events(subject_type,id,subject_id,created_at,actor_id,kind,old,new,note) VALUES('task','m1',?,?,'bot:coo','note','','',"
                  "'What does this wait on?')", (row["id"], later(3.8)))
        assert SA.sweep(c, later(4.2))["late"] == [], "they know: no notice now"
        assert SA.sweep(c, later(5))["late"] == [row["id"]], "an hour on and still late: now they hear"
