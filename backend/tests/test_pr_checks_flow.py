"""Pull request checks give one event per head, and the rules decide who it wakes (backend/github.py, rules.py)."""

import json

from backend import github as G
from backend import hubdb as H
from backend import task_relations as TR
from backend.repositories import metadata, save_metadata
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.rules import plain_task
from backend.tests.helpers.wake_rules import jobs, setup


def due(c, task):
    key = "github-task-wake:" + task["id"]
    save_metadata(c, key, {**metadata(c, key), "due": H.shift(H.now(), seconds=-1)})


def sent(c, task, item="Checks finished on tico#1: passed", armed=False, head="a" * 40, result="pass"):
    """Queue a head's finished checks, send the burst, and return the message it wrote to the owner."""
    G.queue_checks_done(c, task, head, result, item, armed)
    due(c, task)
    assert G.flush_wakes(c) == [task["id"]]
    return H.message(
        c,
        c.execute(
            "SELECT id FROM messages WHERE recipient_id=? ORDER BY rowid DESC LIMIT 1", (task["owner_id"],)
        ).fetchone()[0],
    )


def test_finished_checks_wake_the_owner_at_a_working_step_and_are_a_note_elsewhere(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_done", 1) and not msg["refs"].get("quiet")
        c.execute("DELETE FROM jobs")
        c.execute("UPDATE tasks SET status='review' WHERE id=?", (task["id"],))
        msg = sent(c, H.task(c, task["id"]), head="b" * 40)
        # In review someone else is working: the result waits for the owner's next run, as a note.
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0) and msg["refs"]["quiet"] is True
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (msg["id"],)).fetchone()[0] == 1
        # A link the bot armed with wake_once is the same event, and it asked to hear it wherever the task is.
        msg = sent(c, H.task(c, task["id"]), head="c" * 40, armed=True)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("awaited_wake_once", 1)


def test_finished_checks_wake_an_owner_whose_card_waits_on_them_and_not_one_waiting_on_a_person(api):
    # A manager sets its card to waiting while CI runs: the result is what it waits for, so it wakes the owner at once.
    # A card waiting on a person's answer stays a note: the person's answer is what wakes it.
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (task["id"],))
        msg = sent(c, H.task(c, task["id"]))
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_done", 1)
        c.execute("DELETE FROM jobs")
        H.task_update(c, "bot:cpo", task["id"], status="waiting", note="Which color?", waiting_on="human:ana")
        assert H.task(c, task["id"])["status"] == "waiting"
        msg = sent(c, H.task(c, task["id"]), head="b" * 40)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0)


def parked(c):
    """A bot's card set to waiting with nothing of its own tracked, and no jobs queued."""
    task = plain_task(c)
    c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (task["id"],))
    return H.task(c, task["id"])


def linked(c, task, owner, kind="blocks", private=0, question=False):
    """A task `owner` holds, linked to `task` as its blocker (or subtask, kind 'parent')."""
    other = H.task_create(c, "bot:cpo", "Sign the form " + kind + owner, "Sign it", owner, lint=False)
    c.execute(
        "UPDATE tasks SET private=?, requester_id='bot:ops', questions=? WHERE id=?",
        (private, '[{"id":"q1","question":"Which color?","options":[]}]' if question else None, other["id"]),
    )
    c.execute("DELETE FROM task_relations WHERE from_task=?", (other["id"],))
    TR._insert(c, "bot:cpo", other["id"], task["id"], kind, H.now())
    c.execute("DELETE FROM jobs")
    return other


def test_finished_checks_on_a_card_waiting_on_an_approval_are_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = parked(c)
        c.execute(
            "INSERT INTO approvals(id,kind,task_id,payload,payload_hash,requested_by_id,created_at) "
            "VALUES('ap-1','publish',?,'{}','h1','bot:cpo',?)",
            (task["id"], H.now()),
        )
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0)


def test_finished_checks_on_a_card_a_persons_task_blocks_are_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = parked(c)
        linked(c, task, "human:ben")
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0)


def test_finished_checks_on_a_card_a_private_question_blocks_are_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = parked(c)
        linked(c, task, "human:ben", private=1, question=True)
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0)


def test_finished_checks_on_a_card_a_bots_task_blocks_still_wake(api):
    # A bot's blocker is not a person's answer: the wake goes in, and the claim holds it while the blocker is open.
    setup(api)
    with api.app.state.store.transaction() as c:
        task = parked(c)
        linked(c, task, "bot:ops")
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_done", 1)


def test_finished_checks_on_a_card_with_a_confirm_card_waiting_are_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = parked(c)
        c.execute(
            "INSERT INTO assistant_actions(id,owner,summary,method,path,body,status,created_at) "
            "VALUES('act-1','human:ana','Close it','POST',?,'{}','pending',?)",
            ("/api/v2/tasks/" + task["id"], H.now()),
        )
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0)
        # A card a day old that nobody confirmed holds nothing: it can no longer run.
        c.execute("UPDATE assistant_actions SET created_at=? WHERE id='act-1'", (H.shift(H.now(), hours=-25),))
        msg = sent(c, H.task(c, task["id"]), head="b" * 40)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_done", 1)
        c.execute("DELETE FROM jobs")
        # A step of a bulk card is a Confirm card on its task too.
        steps = {"actions": [{"method": "POST", "path": f"/api/v2/tasks/{task['id']}/comments", "body": {}}]}
        c.execute(
            "INSERT INTO assistant_actions(id,owner,summary,method,path,body,status,created_at) "
            "VALUES('act-2','human:ana','Comment','POST','/api/v2/assistant/batch',?,'pending',?)",
            (json.dumps(steps), H.now()),
        )
        msg = sent(c, H.task(c, task["id"]), head="c" * 40)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("pr_checks_note", 0)


def test_a_review_that_is_not_a_persons_is_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        G.queue_note(c, task, "2 review comments on tico#1")
        due(c, task)
        assert G.flush_wakes(c) == [task["id"]]
        msg = H.message(
            c,
            c.execute("SELECT id FROM messages WHERE recipient_id='bot:cpo' ORDER BY rowid DESC LIMIT 1").fetchone()[0],
        )
        assert msg["refs"]["rule"] == "pr_review_note" and jobs(c, "cpo") == 0
