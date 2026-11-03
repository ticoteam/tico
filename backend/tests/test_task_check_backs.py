"""Follow-ups retain outcome ownership across waits, delivery failures and task lifecycle changes."""
import json

import pytest

from backend import task_check_backs as CB
from backend import wake_rules as W
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401


@pytest.fixture
def followups(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('system_routines',?)",
                  (json.dumps({CB.KEY: {"on": True}}),))
    return api


def task(c, **kw):
    return H.task_create(c, "human:ana", "Inspect the result", "Build the change.", "bot:ops", **kw)


def due(c, row):
    at = H.shift(H.now(), seconds=-1)
    c.execute("UPDATE tasks SET check_back_at=? WHERE id=?", (at, row["id"]))
    return H.task(c, row["id"])


def wakes(c, tid):
    return c.execute("SELECT m.*,j.state job_state FROM messages m LEFT JOIN jobs j ON j.message_id=m.id "
                     "WHERE json_extract(m.refs,'$.task')=? AND json_extract(m.refs,'$.wake')='check_back'",
                     (tid,)).fetchall()


def test_creation_defaults_human_exclusion_and_assignment_scope(followups):
    bot = post(followups, "tasks", {"owner": "ops", "title": "Inspect", "body": "x"})
    assert bot["version"] == 1  # the automatic date is part of creation, not a separate edit
    assert bot["check_back_at"] > H.now() and bot["check_back"]["by"] == "bot:ops"
    human = post(followups, "tasks", {"owner": "ana", "title": "Decide", "body": "x"})
    assert human["check_back_at"] is None
    moved = post(followups, "tasks/" + human["id"], {"version": human["version"], "owner": "ops"})
    assert moved["check_back_at"] > H.now()
    moved = post(followups, "tasks/" + moved["id"], {"version": moved["version"], "owner": "ana"})
    assert moved["check_back_at"] is None


def test_api_setter_dates_privacy_and_atomic_validation(followups):
    before = get(followups, "tasks?status=open")["tasks"]
    post(followups, "tasks", {"owner": "ops", "title": "Bad date", "body": "x", "check_back_at": "yesterday"}, expected=422)
    assert len(get(followups, "tasks?status=open")["tasks"]) == len(before)
    row = post(followups, "tasks", {"owner": "ops", "title": "Inspect", "body": "x", "check_back_at": "45m"})
    updated = post(followups, f"tasks/{row['id']}/check-back", {"at": "2h", "note": "Verify the review was assigned."})
    assert updated["check_back_at"] > row["check_back_at"]
    assert updated["check_back_note"] == "Verify the review was assigned."
    for wrong in ("1m", "2020-01-01T00:00:00Z", "2027-01-01T00:00:00"):
        post(followups, f"tasks/{row['id']}/check-back", {"at": wrong}, expected=422)
    post(followups, f"tasks/{row['id']}/check-back", {"at": "2h"}, token="cara-test", expected=403)


def test_legacy_date_only_deadline_cannot_break_creation_or_backfill(followups):
    with followups.app.state.store.transaction() as c:
        row = task(c, due="2030-01-01")
        assert row["due_at"] == "2030-01-01" and row["check_back_at"] > H.now()
        c.execute("UPDATE tasks SET check_back_at=NULL WHERE id=?", (row["id"],))
        assert CB.sweep(c)["repaired"] == [row["id"]]
        assert H.task(c, row["id"])["check_back_at"] < H.shift(H.now(), hours=1)


@pytest.mark.parametrize("status", CB.ACTIVE)
def test_missing_appointments_repair_in_every_state_despite_unrelated_busy_work(followups, status):
    with followups.app.state.store.transaction() as c:
        row = task(c)
        c.execute("UPDATE tasks SET check_back_at=NULL,status=? WHERE id=?", (status, row["id"]))
        H.task_create(c, "human:ana", "Inspect another result", "Different work.", "bot:ops")
        assert row["id"] in CB.sweep(c)["repaired"]
        assert H.task(c, row["id"])["check_back_at"] > H.now()
        assert row["id"] not in CB.sweep(c)["repaired"]


def test_waiting_self_snoozed_checkpointed_parent_gets_a_real_handoff_inspection(followups):
    with followups.app.state.store.transaction() as c:
        row = task(c)
        child = H.task_create(c, "bot:ops", "Inspect the change", "Review it.", "bot:finance", parent_id=row["id"], lint=False)
        H.task_update(c, "bot:ops", row["id"], status="waiting")
        H.TS.snooze(c, "bot:ops", row["id"], H.shift(H.now(), hours=2))
        fresh = H.task(c, row["id"])
        c.execute("UPDATE tasks SET checkpoint_state=?,checkpoint_by_id='bot:ops' WHERE id=?", (W.state_of(c, fresh), row["id"]))
        due(c, fresh)
        assert CB.sweep(c)["woke"] == [row["id"]]
        [wake] = wakes(c, row["id"])
        assert wake["job_state"] == "queued" and not json.loads(wake["refs"]).get("quiet")
        assert "Linked pull requests: none" in wake["body"] and child["id"][:8] in wake["body"]
        assert not CB.sweep(c)["woke"]


def test_human_snooze_wins_and_its_end_coalesces_the_followup(followups):
    with followups.app.state.store.transaction() as c:
        row = task(c)
        until = H.shift(H.now(), hours=2)
        H.TS.snooze(c, "human:ana", row["id"], until)
        assert H.task(c, row["id"])["check_back_at"] == until
        assert not CB.sweep(c)["woke"]
        post_date = H.shift(until, seconds=1)
        H.TS.wake_ended(c, post_date)
        assert CB.sweep(c, post_date)["woke"] == []
        [wake] = wakes(c, row["id"])
        assert wake["job_state"] == "queued" and "Snooze over" in wake["body"]
    post(followups, f"tasks/{row['id']}/snooze", {"until": H.shift(H.now(), hours=3)})
    post(followups, f"tasks/{row['id']}/check-back", {"at": "30m"}, expected=422)


def test_dropped_wake_is_repaired_without_refiring_the_same_occurrence(followups):
    with followups.app.state.store.transaction() as c:
        row = due(c, task(c))
        CB.sweep(c)
        [wake] = wakes(c, row["id"])
        c.execute("UPDATE jobs SET state='canceled' WHERE message_id=?", (wake["id"],))
        result = CB.sweep(c)
        assert result["repaired"] == [row["id"]]
        assert H.task(c, row["id"])["check_back_at"] > H.now()
        assert len(wakes(c, row["id"])) == 1
        assert c.execute("SELECT count(*) FROM task_check_back_wakes WHERE task_id=?", (row["id"],)).fetchone()[0] == 1


def test_repair_backoff_stops_unbounded_original_task_runs(followups):
    with followups.app.state.store.transaction() as c:
        row = task(c, private=True)
        intervals = []
        for _ in range(5):
            row = CB.repair(c, H.task(c, row["id"]), H.now())
            intervals.append(round((H.parse_ts(row["check_back_at"]) - H.parse_ts(H.now())).total_seconds() / 60))
        assert intervals == [30, 60, 120, 240, 480]
        due(c, row)
        assert CB.sweep(c)["woke"] == []
        assert c.execute("SELECT count(*) FROM task_events WHERE task_id=? AND field='check_back_blocked'", (row["id"],)).fetchone()[0]
        CB.set_time(c, H.task(c, row["id"]), "2h", actor="bot:ops")
        assert CB._repairs(c, H.task(c, row["id"]))[0] == 0


def test_returned_result_follows_the_requester_not_an_unrelated_parent(followups):
    with followups.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Choose a plan", "x", "human:ana", lint=False)
        child = H.task_create(c, "bot:finance", "Inspect the result", "x", "bot:ops", lint=False)
        c.execute("INSERT INTO task_relations VALUES(?,?,'parent','human:ana',?)", (child["id"], parent["id"], H.now()))
        H.task_update(c, "bot:ops", child["id"], status="done")
        after = H.task(c, child["id"])
        assert CB.actor_for(after) == "bot:finance" and after["check_back_at"]
        assert H.task(c, parent["id"])["check_back_at"] is None
        due(c, after)
        CB.sweep(c)
        assert wakes(c, child["id"])[0]["recipient_id"] == "bot:finance"
        H.task_close(c, "bot:finance", child["id"])
        assert H.task(c, child["id"])["check_back_at"] is None


def test_child_completion_pulls_parent_forward_but_never_breaks_human_snooze(followups):
    with followups.app.state.store.transaction() as c:
        parent = task(c)
        child = H.task_create(c, "bot:ops", "Inspect the change", "x", "bot:finance", parent_id=parent["id"], lint=False)
        until = H.shift(H.now(), days=2)
        H.TS.snooze(c, "human:ana", parent["id"], until)
        H.task_update(c, "bot:finance", child["id"], status="done")
        assert H.task(c, parent["id"])["check_back_at"] == until
        assert H.task(c, child["id"])["check_back_at"] is None


def test_closed_and_self_completed_work_do_not_create_consumption_loops(followups):
    with followups.app.state.store.transaction() as c:
        row = H.task_create(c, "bot:ops", "Inspect the result", "x", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", row["id"], status="done")
        assert H.task(c, row["id"])["check_back_at"] is None
        assert not CB.sweep(c)["woke"]
        H.task_update(c, "bot:ops", row["id"], status="open", note="Verify the remaining result.")
        assert H.task(c, row["id"])["check_back_at"] > H.now()


def test_private_child_context_withholds_ids_titles_and_status(followups):
    with followups.app.state.store.transaction() as c:
        parent = task(c, private=False)
        child = H.task_create(c, "human:ana", "Secret request", "private", "bot:finance", lint=False, private=True)
        c.execute("INSERT INTO task_relations VALUES(?,?,'parent','human:ana',?)", (child["id"], parent["id"], H.now()))
        body = CB.context(c, parent, "bot:ops")
        assert "Secret request" not in body and child["id"][:8] not in body
        assert "unreadable work may exist" in body


def test_disabled_rollout_never_suppresses_existing_recovery(api):
    with api.app.state.store.transaction() as c:
        row = task(c)
        assert row["check_back_at"]
        assert not CB.enabled(c)
        assert H.recorded_wait(c, row) != "check_back"
        due(c, row)
        assert not CB.sweep(c)["woke"]


def test_reschedule_and_reassignment_make_old_queued_wakes_obsolete(followups):
    with followups.app.state.store.transaction() as c:
        row = due(c, task(c))
        CB.sweep(c)
        [wake] = wakes(c, row["id"])
        message = H.message(c, wake["id"])
        CB.set_time(c, H.task(c, row["id"]), "2h", actor="bot:ops")
        assert CB.stale(c, message, "ops")
        c.execute("UPDATE tasks SET check_back_at=?,owner_id='bot:finance' WHERE id=?", (row["check_back_at"], row["id"]))
        assert CB.stale(c, message, "ops")
        assert CB.sweep(c)["repaired"] == [row["id"]]


def test_claim_carries_the_followup_and_successful_turn_must_leave_a_next_time(followups):
    from backend.tests.test_api import assign, claim, ready, runner
    r = runner(followups)
    assign(followups, r, "ops")
    ready(followups, r, ["ops"])
    with followups.app.state.store.transaction() as c:
        row = due(c, task(c))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='ops'")
        CB.sweep(c)
        [wake] = wakes(c, row["id"])
    attempt = claim(followups, r, "ops")
    assert attempt, "a check-back must start even when ordinary assignment recovery would hold it"
    with followups.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND j.message_id=?",
                         (attempt["id"], wake["id"])).fetchone()
    post(followups, f"attempts/{attempt['id']}/started", {"thread_id": "follow-up-session"}, token=r["token"])
    post(followups, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "I inspected the handoff.", "last_seq": 0}, token=r["token"])
    with followups.app.state.store.read() as c:
        assert H.task(c, row["id"])["check_back_at"] > H.now()
        assert c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='check_back_repair'", (row["id"],)).fetchone()


def test_due_deadline_and_followup_share_one_notice(followups):
    with followups.app.state.store.transaction() as c:
        row = task(c, due=H.shift(H.now(), minutes=-5))
        due(c, row)
        CB.sweep(c)
        assert c.execute("SELECT 1 FROM task_reminders WHERE task_id=? AND due_at=?", (row["id"], row["due_at"])).fetchone()
        assert len(wakes(c, row["id"])) == 1


def test_scan_does_not_let_paused_tasks_starve_other_bots(followups, monkeypatch):
    monkeypatch.setattr(CB, "LIMIT", 2)
    with followups.app.state.store.transaction() as c:
        blocked = [H.task_create(c, "human:ana", "Inspect blocked result " + str(i), "x", "bot:ops", lint=False) for i in range(2)]
        eligible = H.task_create(c, "human:ana", "Inspect eligible result", "x", "bot:finance", lint=False)
        for row in blocked:
            c.execute("UPDATE tasks SET check_back_at=? WHERE id=?", (H.shift(H.now(), hours=-2), row["id"]))
        due(c, eligible)
        c.execute("UPDATE bots SET state='paused' WHERE id='ops'")
        assert set(CB.sweep(c)["blocked"]) == {r["id"] for r in blocked}
        assert CB.sweep(c)["woke"] == [eligible["id"]]
        assert not c.execute("SELECT 1 FROM task_events WHERE field='check_back_repair'").fetchone()
        c.execute("UPDATE bots SET state='active' WHERE id='ops'")
        assert set(CB.sweep(c)["woke"]) == {r["id"] for r in blocked}


def test_first_wake_hourly_cap_retains_debt_and_later_delivers(followups, monkeypatch):
    monkeypatch.setattr(CB, "FIRST_WAKES_PER_HOUR", 1)
    with followups.app.state.store.transaction() as c:
        rows = [due(c, H.task_create(c, "human:ana", "Inspect capped result " + str(i), "x", "bot:ops", lint=False)) for i in range(2)]
        checked = CB.sweep(c)
        assert len(checked["woke"]) == len(checked["blocked"]) == 1
        blocked = H.task(c, checked["blocked"][0])
        assert blocked["check_back_at"] <= H.now() and CB.view(blocked, c)["delivery"] == "pending"
        checked = CB.sweep(c, H.shift(H.now(), hours=1, seconds=1))
        assert checked["woke"] == [blocked["id"]]
        assert sum(len(wakes(c, row["id"])) for row in rows) == 2


def test_historical_results_are_not_assumed_consumed_and_legacy_waits_survive(followups):
    with followups.app.state.store.transaction() as c:
        result = H.task_create(c, "bot:finance", "Inspect historical result", "x", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", result["id"], status="done")
        c.execute("UPDATE tasks SET check_back_at=NULL WHERE id=?", (result["id"],))
        wait = task(c, due=H.shift(H.now(), hours=4))
        c.execute("UPDATE tasks SET check_back_at=NULL WHERE id=?", (wait["id"],))
        checked = CB.sweep(c)
        assert set(checked["repaired"]) == {result["id"], wait["id"]}
        assert H.task(c, result["id"])["status"] == "done"
        assert H.task(c, result["id"])["check_back_at"] > H.now()
        assert H.task(c, wait["id"])["check_back_at"] >= wait["due_at"]
        assert CB.coverage(c)["results"] == 1


def test_one_bad_task_does_not_rollback_another_followup(followups, monkeypatch):
    with followups.app.state.store.transaction() as c:
        bad = due(c, task(c))
        good = due(c, H.task_create(c, "human:ana", "Inspect another result", "x", "bot:ops", lint=False))
        original = CB.context
        def context(conn, row, recipient):
            if row["id"] == bad["id"]:
                raise ValueError("Invalid task context")
            return original(conn, row, recipient)
        monkeypatch.setattr(CB, "context", context)
        failures = []
        assert CB.sweep(c, failures=failures)["woke"] == [good["id"]]
        assert failures == [{"check_back": bad["id"], "error": "ValueError"}]
        assert not wakes(c, bad["id"])
        assert c.execute("SELECT 1 FROM service_health WHERE service=?", ("background:check_back:" + bad["id"],)).fetchone()


def test_deferred_assignment_deadline_and_followup_use_one_job(followups):
    from backend import delivery
    with followups.app.state.store.transaction() as c:
        row = task(c)
        deadline = delivery.defer(c, "ops", "task", row["id"], task_id=row["id"], defer_for="24h")
        assert H.task(c, row["id"])["check_back_at"] == deadline
        at = H.shift(deadline, seconds=1)
        assert CB.sweep(c, at)["woke"] == [row["id"]]
        [wake] = wakes(c, row["id"])
        assert c.execute("SELECT fired_job FROM deferrals WHERE task_id=?", (row["id"],)).fetchone()[0] == wake["id"]
        assert not delivery.due(c, at)
        assert len(wakes(c, row["id"])) == 1
