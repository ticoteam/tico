"""Pull request checks give one event per head, and the rules decide who it wakes (backend/github.py, rules.py)."""

from backend import github as G
from backend import hubdb as H
from backend.repositories import metadata, save_metadata
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_rules import plain_task
from backend.tests.test_wake_rules import jobs, setup


def due(c, task):
    key = "github-task-wake:" + task["id"]
    save_metadata(c, key, {**metadata(c, key), "due": H.shift(H.now(), seconds=-1)})


def sent(c, task, item="Checks finished on tico#1: passed", armed=False, head="a" * 40, result="pass"):
    """Queue a head's finished checks, send the burst, and return the message it wrote to the owner."""
    G.queue_checks_done(c, task, head, result, item, armed)
    due(c, task)
    assert G.flush_wakes(c) == [task["id"]]
    return H.message(c, c.execute("SELECT id FROM messages WHERE recipient_id=? ORDER BY rowid DESC LIMIT 1",
                                  (task["owner_id"],)).fetchone()[0])


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


def test_a_review_that_is_not_a_persons_is_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        G.queue_note(c, task, "2 review comments on tico#1")
        due(c, task)
        assert G.flush_wakes(c) == [task["id"]]
        msg = H.message(c, c.execute("SELECT id FROM messages WHERE recipient_id='bot:cpo' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        assert msg["refs"]["rule"] == "pr_review_note" and jobs(c, "cpo") == 0
