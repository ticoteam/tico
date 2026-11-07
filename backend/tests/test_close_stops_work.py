"""Closing or declining a task stops the bot work on it (docs/tasks.md): its queued runs are canceled, the turns running
on it are asked to stop, the turn that makes the close is left alone, and a stopped turn's answer is filed on its run
rather than posted into the closed task's room."""

import pytest

from backend.store import H
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401


def task_turn(api, title="Count the words"):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Count them."})
    attempt = claim(api, machine)
    with api.app.state.store.read() as c:
        conversation = c.execute("SELECT id FROM conversations WHERE task_id=?", (task["id"],)).fetchone()[0]
    return machine, task, attempt, conversation


def requester(api, task_id):
    # Only the requester may close a task a person asked for, so the tests close it as its requester.
    with api.app.state.store.read() as c:
        return c.execute("SELECT requester_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]


def stop_flag(api, attempt_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT stop_requested_at FROM attempts WHERE id=?", (attempt_id,)).fetchone()[0]


def work_events(api, task_id):
    with api.app.state.store.read() as c:
        return [
            row[0]
            for row in c.execute(
                "SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='work'",
                (task_id,),
            )
        ]


@pytest.mark.parametrize("status", ["closed", "declined"])
def test_closing_or_declining_asks_the_running_turn_to_stop(api, status):
    machine, task, attempt, conversation = task_turn(api)
    who = requester(api, task["id"])
    with api.app.state.store.transaction() as c:
        H.task_update(c, who, task["id"], status=status, note="Not needed.")
    assert stop_flag(api, attempt["id"]) == who
    assert work_events(api, task["id"]) == ["Stopped 1 running turn."]


def test_closing_cancels_the_queued_runs_on_it(api):
    machine, task, attempt, conversation = task_turn(api)
    who = requester(api, task["id"])
    post(
        api, "messages", {"to": "ops", "kind": "say", "text": "Also check the footer.", "conversation_id": conversation}
    )
    with api.app.state.store.read() as c:
        queued = [row[0] for row in c.execute("SELECT id FROM jobs WHERE state='queued' AND bot_id='ops'")]
    assert len(queued) == 1
    with api.app.state.store.transaction() as c:
        H.task_update(c, who, task["id"], status="closed", note="Done.")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (queued[0],)).fetchone()[0] == "canceled"
    assert stop_flag(api, attempt["id"]) == who
    assert work_events(api, task["id"]) == ["Stopped 1 running turn; canceled 1 queued run."]


def test_the_turn_making_the_close_is_not_stopped(api):
    machine, task, attempt, conversation = task_turn(api)
    who = requester(api, task["id"])
    # H.RUN is the run whose own tool call makes the close: that turn is the one never stopped by it.
    token = H.RUN.set(attempt["id"])
    try:
        with api.app.state.store.transaction() as c:
            H.task_update(c, who, task["id"], status="closed", note="Counted.")
    finally:
        H.RUN.reset(token)
    assert stop_flag(api, attempt["id"]) is None
    assert work_events(api, task["id"]) == []


def test_closing_a_parent_stops_the_turn_running_on_its_subtask(api):
    machine, parent, attempt, conversation = task_turn(api, "Launch")
    who = requester(api, parent["id"])
    with api.app.state.store.transaction() as c:
        child = H.task_create(c, who, "Write the notes", "", "ops", parent_id=parent["id"], lint=False)
    with api.app.state.store.transaction() as c:
        H.task_close(c, who, parent["id"], note="Launch cancelled")
    with api.app.state.store.read() as c:
        assert H.task(c, child["id"])["status"] == "closed"
        assert (
            c.execute(
                "SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE j.state='queued' AND m.refs LIKE ?",
                (f'%{child["id"]}%',),
            ).fetchone()[0]
            == 0
        )
    assert stop_flag(api, attempt["id"]) == who
    assert any(said.startswith("Stopped 1 running turn") for said in work_events(api, parent["id"]))


def answer_after_close(api, close):
    machine, task, attempt, conversation = task_turn(api)
    who = requester(api, task["id"])
    # Filed from a chat, the task answers in its room as the chat would (execution.complete): without the stop or the
    # close, this answer posts; so the room gets nothing only because the stopped turn's answer is filed on its run.
    with api.app.state.store.transaction() as c:
        H.event(c, H.KEEPER, "task.room", task["id"], {})
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread"}, machine["token"])
    if close:
        with api.app.state.store.transaction() as c:
            H.task_update(c, who, task["id"], status="closed", note="Counted elsewhere.")
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "The footer has 12 words."},
        machine["token"],
    )
    with api.app.state.store.read() as c:
        posted = c.execute(
            "SELECT count(*) FROM messages WHERE conversation_id=? AND sender_id='bot:ops' AND body=?",
            (conversation, "The footer has 12 words."),
        ).fetchone()[0]
        final = c.execute("SELECT final_text FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
    return posted, final


def test_a_stopped_turns_answer_is_filed_not_posted_to_the_closed_task(api):
    posted, final = answer_after_close(api, close=True)
    assert posted == 0
    assert final == "The footer has 12 words."


def test_an_unstopped_turn_still_posts_its_answer(api):
    posted, final = answer_after_close(api, close=False)
    assert posted == 1
