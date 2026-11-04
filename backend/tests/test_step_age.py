"""A Dev ticket long at a working step is late at four hours (backend/step_age.py), and its manager's list gets the line: the task
roundup lists it (test_task_roundup.py), so nothing is sent here. Time away from work (waiting) is not counted, and a step's
own hours set when it is late."""

from backend import step_age as SA
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
        assert (item["to"], item["step"]) == ("bot:coo", "In Progress")


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
        assert late_ids(c, 1) == []
        assert late_ids(c, 4.5) == [row["id"]]


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
        assert late_ids(c, 4.5) == [], "not late at 4.5 h: its own 8 h, not the default 4"
        assert late_ids(c, 8.5) == [row["id"]]
        # A step of a type that is not engineering is timed only by its own setting, at whatever status it has.
        typ = H.type_create(c, H.KEEPER, "Content", [{"name": "Drafting", "status": "open"}], bots="work")
        step_flow.apply(c, typ["id"], {"Drafting": {"late_hours": 1}})
        draft = H.task_create(c, "bot:ops", "Draft the launch mail", "x", "bot:cpo", type=typ["id"], lint=False)
        assert SA.late_limit(c, H.task(c, draft["id"])) == 1


def test_a_manager_who_has_just_been_at_a_late_ticket_is_told_only_if_it_stays_late(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        # The manager's own note on it just before it turned late.
        c.execute("INSERT INTO history_events(subject_type,id,subject_id,created_at,actor_id,kind,old,new,note) VALUES('task','m1',?,?,'bot:coo','note','','',"
                  "'What does this wait on?')", (row["id"], later(3.8)))
        assert late_ids(c, 4.2) == [], "they know: no listing now"
        assert late_ids(c, 5) == [row["id"]], "an hour on and still late: now they hear"
