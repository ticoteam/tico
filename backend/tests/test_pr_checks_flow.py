"""Pull request checks give one event per head, and the engine decides whether it wakes the owner (backend/github.py,
task_engine.tell_owner): a wake on the owner's turn, a quiet note for its next run anywhere else."""

from backend import github as G
from backend import hubdb as H
from backend import task_engine as E
from backend.repositories import metadata, save_metadata
from backend.tests.helpers import engine as X
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


def quiet(c, msg):
    """A note the owner reads on its next run: its writer made it quiet, and no run is queued for it."""
    assert (msg["refs"].get("quiet"), msg["refs"]["rule"], jobs(c, "cpo")) == (True, "caller_quiet", 0)


def test_finished_checks_wake_the_owner_on_its_turn_and_are_a_note_while_set_aside(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = sent(c, task)
        assert (msg["refs"]["rule"], jobs(c, "cpo")) == ("task_turn", 1) and not msg["refs"].get("quiet")
        assert (msg["refs"]["pr_head"], msg["refs"]["pr_result"]) == ("a" * 40, "pass")
        c.execute("DELETE FROM jobs")
        # Snoozed, it is nobody's turn: the result waits for the owner's next run, as a note.
        row = E.task(c, task["id"])
        E.snooze(c, task["id"], "bot:cpo", "Back after the freeze", H.shift(H.now(), hours=4), row["version"])
        c.execute("DELETE FROM jobs")
        quiet(c, sent(c, H.task(c, task["id"]), head="b" * 40))
        # A link the bot armed with wake_once is the same event: it still runs only on the owner's turn.
        quiet(c, sent(c, H.task(c, task["id"]), head="c" * 40, armed=True))


def test_finished_checks_are_a_note_while_the_task_waits_on_a_persons_ok(api):
    # A task waiting on a person's OK first is theirs to move: the person's answer is what wakes the bot.
    setup(api)
    with api.app.state.store.transaction() as c:
        sign = {"name": "Sign", "owner": "bot:cpo", "requirements": [{"kind": "ok", "person": X.ANA}]}
        X.pipeline(c, "legal", "Legal", (sign,))
        task = X.new_task(c, "bot:ops", "Sign the form", "legal")
        assert task["owner_id"] == "bot:cpo"
        c.execute("DELETE FROM jobs")
        quiet(c, sent(c, H.task(c, task["id"])))


def test_finished_checks_on_an_ended_task_write_nothing(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        E.move(c, task["id"], E.CLOSED, "human:ana", "Not needed")
        c.execute("DELETE FROM jobs")
        before = c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:cpo'").fetchone()[0]
        G.queue_checks_done(c, task, "a" * 40, "pass", "Checks finished on tico#1: passed", False)
        due(c, task)
        assert G.flush_wakes(c) == []
        assert c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:cpo'").fetchone()[0] == before
        assert jobs(c, "cpo") == 0


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
        assert msg["refs"]["quiet"] and msg["refs"]["rule"] == "caller_quiet" and jobs(c, "cpo") == 0
