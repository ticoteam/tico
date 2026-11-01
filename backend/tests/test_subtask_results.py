"""Delegated results return to the waiting task, with no requester session on the child's task."""
from backend import task_relations as TR
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401
from backend.tests.test_task_lanes import task_bot


def finish(api, machine, attempt, text="Work recorded."):
    post(api, f"attempts/{attempt['id']}/complete",
         {"outcome": "completed", "last_seq": 0, "text": text}, machine["token"])


def start(api, machine, task, thread):
    turn = claim(api, machine)
    assert (turn["task"]["id"], turn["lane"]) == (task, "task:" + task)
    post(api, f"attempts/{turn['id']}/started", {"thread_id": thread}, machine["token"])
    return turn


def queued(api, bot):
    with api.app.state.store.read() as c:
        return queued_in(c, bot)


def queued_in(c, bot):
    return [H.message(c, r[0]) for r in c.execute(
        "SELECT message_id FROM jobs WHERE bot_id=? AND state='queued' ORDER BY rowid", (bot,))]


def test_nested_reviews_return_once_to_the_live_builder_then_builder_returns_to_the_lead(api):
    machine = task_bot(api, lanes=True, others=("coo", "finance", "cpo"))
    with api.app.state.store.transaction() as c:
        lead = H.task_create(c, "human:ana", "Deliver the import", "Plan and delegate.", "bot:coo", lint=False)
    planning = start(api, machine, lead["id"], "lead-task")
    with api.app.state.store.transaction() as c:
        build = H.task_create(c, "bot:coo", "Implement the import", "Build and review.", "bot:ops",
                              parent_id=lead["id"], lint=False)
    finish(api, machine, planning)
    builder = start(api, machine, build["id"], "builder-task")
    for reviewer in ("finance", "cpo"):
        with api.app.state.store.transaction() as c:
            review = H.task_create(c, "bot:ops", "Review the import", "Review this change.", "bot:" + reviewer,
                                   parent_id=build["id"], lint=False)
            TR.relate(c, "bot:ops", review["id"], build["id"], "blocks")
            H.task_update(c, "bot:ops", build["id"], status="waiting")
        checking = start(api, machine, review["id"], reviewer + "-review")
        verdict = "No blocking findings.\nVERDICT: approve PR #12 head " + "a" * 40
        said = post(api, f"tasks/{review['id']}/comments", {"text": verdict}, checking["token"])
        assert said["woke"] is True, "the forwarded result queued a bot job on the parent"
        assert [m["refs"]["task"] for m in queued(api, "ops")] == [build["id"]]
        assert claim(api, machine) is None, "the result joins the active Builder task instead of a child session"
        inputs = post(api, f"attempts/{builder['id']}/inputs", {}, machine["token"])["messages"]
        assert len(inputs) == 1 and verdict in inputs[0]["body"]
        assert inputs[0]["refs"]["subtask"] == review["id"]
        post(api, f"attempts/{builder['id']}/inputs/{inputs[0]['id']}/ack", {}, machine["token"])
        post(api, f"tasks/{review['id']}", {"status": "done", "note": verdict if reviewer == "cpo" else "Review recorded.",
                                           "version": checking["task"]["version"]},
             checking["token"])
        finish(api, machine, checking, text=verdict)
        assert not queued(api, "ops"), "finishing after an already delivered verdict starts no second turn"
        assert not queued(api, "coo"), "review progress never wakes the grandparent"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", build["id"], status="done", note="Implemented and reviewed.")
    finish(api, machine, builder)
    back = start(api, machine, lead["id"], "lead-task")
    assert "Implemented and reviewed." in back["message"]["body"]
    assert back["message"]["refs"]["subtask"] == build["id"]
    assert not queued(api, "ops")


def test_completion_resumes_the_idle_parent_and_reopening_allows_a_new_verdict(api):
    machine = task_bot(api, lanes=True, others=("finance",))
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Implement the import", "Build it.", "bot:ops", lint=False)
    builder = start(api, machine, parent["id"], "builder-task")
    with api.app.state.store.transaction() as c:
        review = H.task_create(c, "bot:ops", "Review the import", "Review it.", "bot:finance",
                               parent_id=parent["id"], lint=False)
    finish(api, machine, builder)
    checking = start(api, machine, review["id"], "review-task")
    verdict = "VERDICT: approve PR #12 head " + "b" * 40
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:finance", review["id"], status="done", note=verdict)
    finish(api, machine, checking)
    back = start(api, machine, parent["id"], "builder-task")
    assert verdict in back["message"]["body"]
    assert back["task"]["checkpoint"] == "Work recorded."
    finish(api, machine, back)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:finance", review["id"], status="done", note=verdict)
    assert not queued(api, "ops"), "repeating Done does not create a delivery session"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", review["id"], status="open")
    checking = start(api, machine, review["id"], "review-task")
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:finance", review["id"], status="done", note=verdict)
    finish(api, machine, checking)
    again = start(api, machine, parent["id"], "builder-task")
    assert verdict in again["message"]["body"]


def test_forwarded_verdict_retains_its_source_privacy(api):
    from backend import task_privacy as P
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Implement the import", "Build it.", "bot:ops", lint=False)
        review = H.task_create(c, "bot:ops", "Review the import", "Review it.", "bot:finance",
                               parent_id=parent["id"], lint=False)
        source = H.task_create(c, "human:ana", "Review the source", "Evidence.", "bot:finance", lint=False)
        verdict = "VERDICT: approve PR #12 head " + "c" * 40
        H.say(c, "bot:finance", "bot:ops", verdict, conversation_id=review["conversation_id"],
              refs={"task": review["id"], "source": source["id"]})
        [forwarded] = [m for m in queued_in(c, "ops") if m["refs"].get("subtask") == review["id"]]
        assert P.message_readable(c, "bot:ops", forwarded)
        H.task_update(c, "human:ana", source["id"], private=True)
        assert not P.message_readable(c, "bot:ops", forwarded)
        assert not P.message_readable(c, "human:ben", forwarded)


def test_private_child_results_stay_off_a_wider_parent_and_closed_parents_do_not_wake(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Deliver the import", "Company work.", "bot:ops", lint=False)
        child = H.task_create(c, "bot:ops", "Private review", "Restricted evidence.", "bot:finance",
                              parent_id=parent["id"], private=True, lint=False)
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_update(c, "bot:finance", child["id"], status="done", note="Private finding.")
        messages = [dict(r) for r in c.execute("SELECT * FROM messages WHERE rowid>?", (since,))]
        assert all("Private finding." not in r["body"] for r in messages
                   if r["conversation_id"] == parent["conversation_id"])
        closed = H.task_create(c, "bot:ops", "Another review", "Review it.", "bot:finance",
                               parent_id=parent["id"], lint=False)
        H.task_close(c, "human:ana", parent["id"])
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_update(c, "bot:finance", closed["id"], status="done", note="Late result.")
        assert not c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                             "WHERE j.bot_id='ops' AND j.state='queued' AND m.rowid>?", (since,)).fetchone()
