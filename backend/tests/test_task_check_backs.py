"""Follow-up times on tasks: who may set one, what a change records, and that finished or deferred work gets none. A queued
check-back wake from before its removal is obsolete when its bot claims it."""
import pytest

from backend import task_check_backs as CB
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


def task(c, **kw):
    return H.task_create(c, "human:ana", "Inspect the result", "Build the change.", "bot:ops", **kw)


def test_creation_sets_no_follow_up_of_its_own_and_human_exclusion_holds(api):
    bot = post(api, "tasks", {"owner": "ops", "title": "Inspect", "body": "x"})
    assert bot["version"] == 1
    assert bot["check_back_at"] is None  # Tico sets no follow-up time of its own: only a person or a bot does
    human = post(api, "tasks", {"owner": "ana", "title": "Decide", "body": "x"})
    assert human["check_back_at"] is None
    moved = post(api, "tasks/" + human["id"], {"version": human["version"], "owner": "ops"})
    assert moved["check_back_at"] is None
    moved = post(api, "tasks/" + moved["id"], {"version": moved["version"], "owner": "ana"})
    assert moved["check_back_at"] is None


def test_api_setter_dates_privacy_and_atomic_validation(api):
    before = get(api, "tasks?status=open")["tasks"]
    post(api, "tasks", {"owner": "ops", "title": "Bad date", "body": "x", "check_back_at": "yesterday"}, expected=422)
    assert len(get(api, "tasks?status=open")["tasks"]) == len(before)
    row = post(api, "tasks", {"owner": "ops", "title": "Inspect", "body": "x", "check_back_at": "45m"})
    updated = post(api, f"tasks/{row['id']}/check-back", {"at": "2h", "note": "Verify the review was assigned."})
    assert updated["check_back_at"] > row["check_back_at"]
    assert updated["check_back_note"] == "Verify the review was assigned."
    for wrong in ("1m", "2020-01-01T00:00:00Z", "2027-01-01T00:00:00"):
        post(api, f"tasks/{row['id']}/check-back", {"at": wrong}, expected=422)
    post(api, f"tasks/{row['id']}/check-back", {"at": "2h"}, token="cara-test", expected=403)


def test_legacy_date_only_deadline_cannot_break_creation(api):
    with api.app.state.store.transaction() as c:
        row = task(c, due="2030-01-01")
        assert row["due_at"] == "2030-01-01" and row["check_back_at"] is None


def test_human_snooze_wins_over_a_bot_follow_up_set_inside_it(api):
    with api.app.state.store.transaction() as c:
        row = task(c)
        H.TS.snooze(c, "human:ana", row["id"], H.shift(H.now(), hours=2))
        assert H.task(c, row["id"])["check_back_at"] is None  # a snooze sets no follow-up time of its own
        with pytest.raises(H.Refused, match="snoozed"):
            CB.set_time(c, H.task(c, row["id"]), "30m", actor="bot:ops")
    post(api, f"tasks/{row['id']}/snooze", {"until": H.shift(H.now(), hours=3)})
    post(api, f"tasks/{row['id']}/check-back", {"at": "30m"}, expected=422)


def test_a_queued_check_back_wake_is_obsolete_whatever_its_task_says_next(api):
    with api.app.state.store.transaction() as c:
        row = task(c)
        wake = H._wake(c, row, "bot:ops", "Check on it.", {"wake": "check_back", "task": row["id"]}, trigger="check_back")
        message = H.message(c, wake["id"])
        job = c.execute("SELECT * FROM jobs WHERE message_id=?", (wake["id"],)).fetchone()
        assert CB.stale(c, message, job)
        CB.set_time(c, H.task(c, row["id"]), "2h", actor="bot:ops")
        assert CB.stale(c, message, job)
        c.execute("UPDATE tasks SET owner_id='bot:finance' WHERE id=?", (row["id"],))
        assert CB.stale(c, message, job)


def test_an_unstarted_check_back_wake_is_obsolete_at_claim(api):
    from backend.tests.test_api import assign, claim, ready, runner
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        row = task(c)
        # Under way, not open: an open task of an active bot is an assigned start the claim would hand out instead.
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (row["id"],))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
        wake = H._wake(c, row, "bot:ops", "Check on it.", {"wake": "check_back", "task": row["id"]}, trigger="check_back")
    assert claim(api, r, "ops") is None, "a legacy check-back nobody has started is obsolete, whatever its task says"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (wake["id"],)).fetchone()[0] == "canceled"


def test_closed_and_self_completed_work_has_no_follow_up(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "bot:ops", "Inspect the result", "x", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", row["id"], status="done")
        assert H.task(c, row["id"])["check_back_at"] is None
        H.task_update(c, "bot:ops", row["id"], status="open", note="Verify the remaining result.")
        assert H.task(c, row["id"])["check_back_at"] is None  # reopening sets no follow-up time


def test_a_bot_can_snooze_a_task_without_a_follow_up_time(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "human:ana", "Inspect overdue work", "x", "bot:ops", lint=False)
        until = H.shift(H.now(), hours=2)
        after = H.TS.snooze(c, "bot:ops", row["id"], until)
        assert after["snoozed_until"] == until and after["check_back_at"] is None


def test_system_checkpoints_do_not_refresh_human_idle_time_or_edit_revision(api):
    with api.app.state.store.transaction() as c:
        row = task(c)
        old = H.shift(H.now(), days=-10)
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, row["id"]))
        CB.set_time(c, H.task(c, row["id"]), "2h", actor="bot:ops", system=True)
        after = H.task(c, row["id"])
        assert after["updated_at"] == old and after["version"] == row["version"] and after["check_back_at"]
        CB.set_time(c, H.task(c, row["id"]), "3h", actor="bot:ops")
        assert H.task(c, row["id"])["updated_at"] == old


def test_result_consumer_must_record_disposition_instead_of_reparking(api):
    with api.app.state.store.transaction() as c:
        row = H.task_create(c, "bot:finance", "Inspect a returned result", "x", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", row["id"], status="done")
        before = CB.stand(c, H.task(c, row["id"]))
        H.task_comment(c, "bot:finance", row["id"], "I have read the returned result.")
        assert CB.stand(c, H.task(c, row["id"])) == before
        with pytest.raises(H.Refused, match="disposition"):
            CB.set_time(c, H.task(c, row["id"]), "5m", actor="bot:finance")


def test_deferred_assignment_fires_at_its_deadline_and_sets_no_follow_up_of_its_own(api):
    from backend import delivery
    with api.app.state.store.transaction() as c:
        row = task(c, next_run=True)
        deadline = delivery.defer(c, "ops", "task", row["id"], task_id=row["id"], defer_for="24h")
        assert H.task(c, row["id"])["check_back_at"] is None
        assert delivery.due(c, H.shift(deadline, seconds=1))


def test_generic_update_persists_date_only_and_note_only_edits(api):
    row = post(api, "tasks", {"owner": "ops", "title": "Inspect the update", "body": "x"})
    updated = post(api, f"tasks/{row['id']}", {"version": row["version"], "check_back_at": "2h"})
    assert updated["check_back_at"] > H.now()
    note = post(api, f"tasks/{row['id']}", {"version": updated["version"], "check_back_note": "Verify the real assignment."})
    assert note["check_back_note"] == "Verify the real assignment."
    assert note["check_back_at"] == updated["check_back_at"]


def test_results_returned_get_no_follow_up(api):
    with api.app.state.store.transaction() as c:
        returned = []
        for status in ("done", "declined"):
            row = H.task_create(c, "bot:finance", "Inspect an offline result " + status, "x", "bot:ops", lint=False)
            H.task_update(c, "bot:ops", row["id"], status=status)
            returned.append(row["id"])
        assert all(H.task(c, tid)["check_back_at"] is None for tid in returned)


def test_deferred_delivery_is_due_at_its_deadline_and_stays_unsettled(api):
    from backend import delivery
    with api.app.state.store.transaction() as c:
        row = task(c, next_run=True)
        deadline = delivery.defer(c, "ops", "task", row["id"], task_id=row["id"], defer_for="24h")
        at = H.shift(deadline, seconds=1)
        assert delivery.due(c, at)
        settled = c.execute("SELECT settled_at FROM deferrals WHERE task_id=?", (row["id"],)).fetchone()[0]
        assert settled is None
