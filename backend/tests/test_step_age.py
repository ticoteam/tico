"""A Dev ticket long at a working step is late at four hours (backend/step_age.py), and its manager's list gets the line: the task
roundup lists it (test_task_roundup.py), so nothing is sent here. Time away from work (waiting) is not counted, and a step's
own hours set when it is late."""

from backend import step_age as SA
from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type
from backend.tests.hold_rows import set_holds


def ticket(c, title="Fix the connection", owner="bot:cpo", manager="coo"):
    found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
    typ = {"id": found[0]} if found else dev_type(c)
    if manager is None:
        c.execute("UPDATE task_types SET manager=NULL WHERE id=?", (typ["id"],))
    else:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", (manager, typ["id"]))
    # The Warn after the boot gives an engineering type's working step (task_steps.limit_minutes, 4 hours).
    c.execute(
        "UPDATE task_steps SET limit_minutes=240 WHERE type_id=? AND name='Build' AND limit_minutes IS NULL",
        (typ["id"],),
    )
    row = H.task_create(c, "bot:ops", title, "Build.", owner, type=typ["id"], step="Build", lint=False)
    return H.task(c, row["id"])


def later(hours):
    return H.shift(H.now(), hours=hours)


def late_ids(c, hours):
    return [item["row"]["id"] for item in SA.late_items(c, later(hours))]


def test_a_working_ticket_is_late_once_past_four_hours_at_its_step_and_its_manager_is_the_one_it_is_for(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        assert late_ids(c, 2.5) == []
        assert late_ids(c, 4.5) == [row["id"]]
        [item] = SA.late_items(c, later(4.5))
        assert (item["to"], item["step"]) == ("bot:coo", "Build")


def test_time_away_from_work_is_not_counted_toward_the_limit(api):
    """D40 (Fix V-15): the step is entered 6 h ago and the ticket waits from 5 h ago to 1 h ago. Only the hour before the
    wait and the hour since count, so it is 2 h in: not late 1.5 h from now, late 2.5 h from now."""
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        for ts in c.execute(
            "SELECT id FROM history_events WHERE subject_type='task' AND subject_id=?", (row["id"],)
        ).fetchall():
            c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND id=?", (later(-6), ts[0]))
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (later(-6), row["id"]))
        c.execute("UPDATE stage_visits SET entered_at=? WHERE task_id=? AND left_at IS NULL", (later(-6), row["id"]))
        H.history_event(c, "task", row["id"], "bot:cpo", "status", "doing", "waiting", created_at=later(-5))
        H.history_event(c, "task", row["id"], "bot:cpo", "status", "waiting", "doing", created_at=later(-1))
        assert late_ids(c, 1.5) == []
        assert late_ids(c, 2.5) == [row["id"]]


def test_a_late_ticket_with_no_manager_is_late_for_nobody_until_one_appears(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c, "Fix the unowned connection", manager=None)
        assert SA._manager(c, H.task(c, row["id"])) is None
        assert late_ids(c, 4.5) == []
        c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (row["type_id"],))
        assert late_ids(c, 4.6) == [row["id"]]


def test_a_manager_who_is_the_owner_is_never_late_for(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c, "Fix the own-managed connection", manager="cpo")
        assert SA._manager(c, H.task(c, row["id"])) is None
        assert late_ids(c, 4.5) == []


def test_the_board_reads_when_a_working_ticket_entered_its_step(api):
    with api.app.state.store.transaction() as c:
        working = ticket(c)
        queued = H.task_create(
            c, "bot:ops", "Next", "Later.", "bot:cpo", type=working["type_id"], step="Review 2", lint=False
        )
    rows = {t["id"]: t for t in get(api, "tasks?status=all&brief=true")["tasks"]}
    assert rows[working["id"]]["step_since"]
    assert "step_since" not in rows[queued["id"]]
    assert get(api, "tasks/" + working["id"])["task"]["step_since"] == rows[working["id"]]["step_since"]


def test_a_steps_own_warn_after_replaces_the_default_and_a_step_without_one_is_never_late(api):
    from backend import pipeline_manager as PM

    with api.app.state.store.transaction() as c:
        row = ticket(c)
        c.execute("UPDATE task_steps SET limit_minutes=480 WHERE id=?", (row["step_id"],))
        assert late_ids(c, 4.5) == [], "not late at 4.5 h: its own 8 h, not the default 4"
        assert late_ids(c, 8.5) == [row["id"]]
        # "Don't warn" (no Warn after) is authoritative: a legacy flow's late_hours does not bring the warning back.
        c.execute("UPDATE task_steps SET limit_minutes=NULL, flow=? WHERE id=?", ('{"late_hours": 8}', row["step_id"]))
        assert late_ids(c, 20) == []
        assert PM.late_tickets(c, [H.task(c, row["id"])], later(20)) == {}


def test_a_manager_who_has_just_been_at_a_late_ticket_is_told_only_if_it_stays_late(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        # The manager's own note on it just before it turned late.
        c.execute(
            "INSERT INTO history_events(subject_type,id,subject_id,created_at,actor_id,kind,old,new,note) VALUES('task','m1',?,?,'bot:coo','note','','',"
            "'What does this wait on?')",
            (row["id"], later(3.8)),
        )
        assert late_ids(c, 4.2) == [], "they know: no listing now"
        assert late_ids(c, 5) == [row["id"]], "an hour on and still late: now they hear"


def test_a_steps_limit_column_is_its_warn_after_and_replaces_the_default(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        c.execute("UPDATE task_steps SET limit_minutes=30 WHERE id=?", (row["step_id"],))
        assert late_ids(c, 0.4) == []
        assert late_ids(c, 0.6) == [row["id"]]


def test_a_late_hours_a_step_save_changes_becomes_the_stages_warn_after(api):
    # One stored threshold: a flow's late_hours sent in a step save sets limit_minutes, which the Late clock reads; a save
    # that leaves late_hours as it was keeps a Warn after set since.
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        steps = [
            dict(s) for s in c.execute("SELECT id, name, status FROM task_steps WHERE type_id=?", (row["type_id"],))
        ]
        for s in steps:
            if s["id"] == row["step_id"]:
                s["flow"] = {"late_hours": 6}
        H._type_steps(c, H.KEEPER, row["type_id"], steps)  # pyright: ignore[reportPrivateUsage]
        assert c.execute("SELECT limit_minutes FROM task_steps WHERE id=?", (row["step_id"],)).fetchone()[0] == 360
        assert late_ids(c, 4.5) == [] and late_ids(c, 6.5) == [row["id"]]
        c.execute("UPDATE task_steps SET limit_minutes=120 WHERE id=?", (row["step_id"],))  # Warn after set since
        H._type_steps(c, H.KEEPER, row["type_id"], steps)  # pyright: ignore[reportPrivateUsage]
        assert c.execute("SELECT limit_minutes FROM task_steps WHERE id=?", (row["step_id"],)).fetchone()[0] == 120
