"""A Dev ticket long at a working step is not left silent (backend/step_age.py): two quiet hours wake its owner once
for a status, even when the plain status check is held at its checkpoint, and four hours at the step reach the type's
manager once. Time away from work (waiting) is not counted."""

from backend import step_age as SA
from backend import wake_rules as W
from backend.store import H
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_dev_ticket_rules import dev_type


def ticket(c, title="Fix the connection", owner="bot:cpo"):
    found = c.execute("SELECT id FROM task_types WHERE name='Dev tickets'").fetchone()
    typ = {"id": found[0]} if found else dev_type(c)
    c.execute("UPDATE task_types SET manager='coo' WHERE id=?", (typ["id"],))
    row = H.task_create(c, "bot:ops", title, "Build.", owner, type=typ["id"], step="In Progress",
                        lint=False)
    return H.task(c, row["id"])


def at_checkpoint(c, row):
    """The owner's last turn settled here, so the plain stall and status checks are held (wake_rules.same_state)."""
    c.execute("UPDATE tasks SET checkpoint_state=?,checkpoint_by=?,checkpoint_at=updated WHERE id=?",
              (W.state_of(c, row), row["owner"], row["id"]))


def later(hours):
    return H.shift(H.now(), hours=hours)


def wakes(c, tid, to, kind):
    return [m for m in c.execute(
        "SELECT * FROM messages WHERE to_actor=? AND json_extract(refs_json,'$.task')=? "
        "AND json_extract(refs_json,'$.wake')=?", (to, tid, kind)).fetchall()]


def queued(c, message):
    return bool(c.execute("SELECT 1 FROM jobs WHERE message_id=? AND state='queued'", (message["id"],)).fetchone())


def test_a_quiet_ticket_at_its_checkpoint_wakes_its_owner_once_and_reaches_the_manager_once_late(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        at_checkpoint(c, row)
        assert W.at_checkpoint(c, H.task(c, row["id"]), "bot:cpo")
        assert SA.sweep(c, later(1)) == {"asked": [], "late": []}
        assert SA.sweep(c, later(2.5)) == {"asked": [row["id"]], "late": []}
        [asked] = wakes(c, row["id"], "bot:cpo", "step_status")
        assert W.MARK not in H._json(asked["refs_json"], {}), "the status wake is not held as same_state"
        assert queued(c, asked)
        # Once: later sweeps neither ask again nor tell the manager twice.
        assert SA.sweep(c, later(4.5)) == {"asked": [], "late": [row["id"]]}
        assert SA.sweep(c, later(9)) == {"asked": [], "late": []}
        assert len(wakes(c, row["id"], "bot:cpo", "step_status")) == 1
        [late] = wakes(c, row["id"], "bot:coo", "blocker")
        assert "In Progress" in late["body"]
        assert queued(c, late)


def test_recent_activity_waiting_and_snoozed_tickets_get_no_status_wake(api):
    with api.app.state.store.transaction() as c:
        active, waiting, snoozed = ticket(c, "Fix Cursor"), ticket(c, "Fix Muse"), ticket(c, "Fix Grok")
        # A person's comment an hour and a half in: still under two quiet hours at the sweep.
        c.execute("UPDATE tasks SET updated=? WHERE id IN (?,?,?)",
                  (later(-3), active["id"], waiting["id"], snoozed["id"]))
        H.task_comment(c, "human:ana", active["id"], "How is it going?", wake=False)
        H.task_update(c, "bot:cpo", waiting["id"], status="waiting", note="Waits on Review 1")
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (later(24), snoozed["id"]))
        assert SA.sweep(c, later(1.5))["asked"] == []
        # Two quiet hours after the comment, the working one is asked; the waiting and snoozed ones still are not.
        assert SA.sweep(c, later(2.5))["asked"] == [active["id"]]


def test_time_away_from_work_restarts_the_clock(api):
    with api.app.state.store.transaction() as c:
        row = ticket(c)
        for ts in c.execute("SELECT id FROM task_events WHERE task_id=?", (row["id"],)).fetchall():
            c.execute("UPDATE task_events SET ts=? WHERE id=?", (later(-6), ts[0]))
        c.execute("UPDATE tasks SET created=? WHERE id=?", (later(-6), row["id"]))
        H.task_update(c, "bot:cpo", row["id"], status="waiting", note="Held at the gate")
        H.task_update(c, "bot:cpo", row["id"], status="doing")
        back = SA.since_map(c, [H.task(c, row["id"])])[row["id"]]
        assert back > later(-1)
        assert SA.sweep(c, later(1))["late"] == []
        assert SA.sweep(c, later(4.5))["late"] == [row["id"]]


def test_the_board_reads_when_a_working_ticket_entered_its_step(api):
    with api.app.state.store.transaction() as c:
        working = ticket(c)
        queued = H.task_create(c, "bot:ops", "Next", "Later.", "bot:cpo", type=working["type_id"], step="In QA",
                               lint=False)
    rows = {t["id"]: t for t in get(api, "tasks?status=all&brief=true")["tasks"]}
    assert rows[working["id"]]["step_since"]
    assert "step_since" not in rows[queued["id"]]
    assert get(api, "tasks/" + working["id"])["task"]["step_since"] == rows[working["id"]]["step_since"]
