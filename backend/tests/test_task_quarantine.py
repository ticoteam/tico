"""A refusal loop holds its task/chat; unrelated work of the same bot keeps running."""
import pytest

from backend import hubdb as H
from backend.tests.test_api import api, claim, get, post, ready  # noqa: F401 (fixture)
from backend.tests.test_execution_review import job_state
from backend.tests.test_held_runs import botops
from backend.tests.test_task_lanes import new_task, task_bot


def started(api, machine):
    attempt = claim(api, machine)
    assert attempt is not None
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    return attempt


def refusals(api, attempt, task, count=H.QUARANTINE_AT + 1):
    for _ in range(count):
        row = get(api, "tasks/" + task["id"])["task"]
        result = post(api, "tasks/" + task["id"], {"version": row["version"], "number": 993},
                      attempt["token"], expected=403)
        assert result["error"]["code"] == "identity"


def holds(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM quarantine_holds WHERE released_at IS NULL")]


def test_one_tasks_refusals_stop_only_its_turn_and_other_work_claims_and_runs(api):
    botops(api)
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    bad = started(api, machine)
    other = new_task(api, "Check the archive")
    good = started(api, machine)
    waiting = new_task(api, "Check the summary")
    refusals(api, bad, task)
    [hold] = holds(api)
    assert hold["task_id"] == task["id"] and hold["conversation_id"] is None
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"
        assert H.task(c, task["id"])["status"] == "open"
        assert c.execute("SELECT owner_id FROM tasks WHERE title=?", ("Review ops's refused writes",)).fetchone()[0] == H.bot_actor(H.FLEET_MAINTAINER)
    stopped = post(api, f"attempts/{bad['id']}/renew", {}, machine["token"])
    assert stopped["stop"] and stopped["stop_reason"].startswith("Quarantined: 10 repeated refusals today")
    assert not post(api, f"attempts/{good['id']}/renew", {}, machine["token"])["stop"]
    post(api, f"attempts/{bad['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    assert job_state(api, bad["job_id"]) == "queued"
    post(api, f"tasks/{task['id']}/comments", {"text": "Please check it again."})
    next_run = started(api, machine)
    assert next_run["task"]["id"] == waiting["id"]
    for attempt in (good, next_run):
        post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Checked."}, machine["token"])
    assert claim(api, machine) is None
    assert any("Held for" in m["body"] for m in get(api, f"conversations/{task['conversation_id']}/messages"))
    assert other["id"] != task["id"]


@pytest.mark.parametrize("lift", ["cooldown", "clear", "person"])
def test_task_hold_lifts_and_its_count_restarts(api, time_machine, lift):
    time_machine.move_to("2026-11-04T12:00:00Z", tick=False)
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    assert claim(api, machine) is None
    if lift == "cooldown":
        with api.app.state.store.transaction() as c:
            assert H.lift_cooled_quarantines(c) == []
        time_machine.shift(H.QUARANTINE_COOLDOWN_S + 1)
        with api.app.state.store.transaction() as c:
            assert H.lift_cooled_quarantines(c) == ["ops"]
    elif lift == "clear":
        post(api, "bots/ops/quarantine/clear", {}, "cara-test", expected=403)
        assert holds(api)
        time_machine.shift(1)
        post(api, "bots/ops/quarantine/clear", {})
    else:
        time_machine.shift(1)
        with api.app.state.store.transaction() as c:
            H.status_set(c, "human:ana", "ops", state="active", reason="Reviewed")
    assert holds(api) == []
    ready(api, machine, ["ops"])
    resumed = started(api, machine)
    assert resumed["job_id"] == attempt["job_id"]
    assert "by a quarantine" in resumed["message"]["body"]
    refusals(api, resumed, task, count=2)
    assert holds(api) == []


def test_refusals_from_several_tasks_still_stop_every_turn(api):
    machine = task_bot(api, lanes=True)
    first = new_task(api, "Check the export")
    one = started(api, machine)
    second = new_task(api, "Check the archive")
    two = started(api, machine)
    refusals(api, one, first, count=6)
    refusals(api, two, second, count=6)
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "quarantined"
        assert not H.quarantine_is_escape(c, "ops")
    assert holds(api) == []
    for attempt in (one, two):
        assert post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])["stop"]


def test_an_escape_over_a_task_hold_stays_bot_wide_and_needs_a_person(api, time_machine):
    time_machine.move_to("2026-11-04T12:00:00Z", tick=False)
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    one = started(api, machine)
    new_task(api, "Check the archive")
    two = started(api, machine)
    refusals(api, one, task)
    with api.app.state.store.transaction() as c:
        H.quarantine(c, "ops", "escape: the task note reaches outside the hub: secrets/mail.env",
                     context={"what": "task note", "found": "secrets/", "task": task["id"]})
    for attempt in (one, two):
        stop = post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
        assert stop["stop"] and "until a person clears it" in stop["stop_reason"]
    time_machine.shift(H.QUARANTINE_COOLDOWN_S + 1)
    with api.app.state.store.transaction() as c:
        H.lift_cooled_quarantines(c)
        assert H.bot(c, "ops")["state"] == "quarantined" and H.quarantine_is_escape(c, "ops")
    post(api, "bots/ops/quarantine/clear", {})
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "active"


def test_a_chat_loop_holds_its_chat_while_tasks_still_run(api):
    machine = task_bot(api, lanes=True)
    post(api, "chat/ops", {"text": "Check the export."})
    chat = started(api, machine)
    target = new_task(api, "Check the archive")
    refusals(api, chat, target)
    [hold] = holds(api)
    assert hold["task_id"] is None and hold["conversation_id"] == chat["message"]["conversation_id"]
    post(api, f"attempts/{chat['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{chat['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    post(api, "chat/ops", {"text": "Try again."})
    assert started(api, machine)["task"]["id"] == target["id"]
    assert claim(api, machine) is None
    post(api, "bots/ops/quarantine/clear", {})
    assert claim(api, machine)["job_id"] == chat["job_id"]


def test_unattributed_refusals_keep_the_bot_wide_stop(api):
    with api.app.state.store.transaction() as c:
        for _ in range(H.QUARANTINE_AT + 1):
            with pytest.raises(H.Refused):
                H.refuse(c, "bot:ops", "identity", "Only a person may do this")
        assert H.bot(c, "ops")["state"] == "quarantined"
    assert holds(api) == []


def test_a_scoped_stop_keeps_the_named_outside_action_for_review_after_clear(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": 1, "kind": "status", "payload": {"state": "active", "tool_events": True}},
        {"seq": 2, "kind": "tool", "payload": {"name": "Bash", "status": "started", "summary": "gh pr merge 42 --squash"}}]}, machine["token"])
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 2}, machine["token"])
    assert job_state(api, attempt["job_id"]) == "uncertain"
    post(api, "bots/ops/quarantine/clear", {})
    assert claim(api, machine) is None
    assert job_state(api, attempt["job_id"]) == "uncertain"


def test_a_task_hold_keeps_other_task_holds_and_clear_does_not_resume_a_paused_bot(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    with api.app.state.store.transaction() as c:
        from backend import task_holds
        task_holds.put(c, task["id"], "snoozed", H.now(), until_at=H.shift(H.now(), days=2), reason="Waiting for data")
    refusals(api, attempt, task)
    with api.app.state.store.transaction() as c:
        H.status_set(c, "human:ana", "ops", state="paused", reason="Maintenance")
    post(api, "bots/ops/quarantine/clear", {})
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "paused"
        assert H.task(c, task["id"])["snoozed_note"] == "Waiting for data"
    assert holds(api) == []


def test_a_held_task_cannot_ride_along_with_another_tasks_next_run(api):
    machine = task_bot(api, lanes=True)
    task = new_task(api, "Check the export")
    attempt = started(api, machine)
    refusals(api, attempt, task)
    post(api, f"attempts/{attempt['id']}/renew", {}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, machine["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET next_run=1 WHERE id=?", (task["id"],))
    other = new_task(api, "Check the archive")
    attempt = post(api, "jobs/claim", {"next_run": True, "start_places": True}, machine["token"])["attempt"]
    assert attempt["task"]["id"] == other["id"] and attempt["next_run"] == []
