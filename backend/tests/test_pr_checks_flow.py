"""Pull request checks give one event per head, and the engine decides whether it wakes the owner (backend/github.py,
task_engine.tell_owner): a wake on the owner bot's turn, a quiet note for its next run while the task waits on a
person first."""

from backend import github as G
from backend.repositories import metadata, save_metadata
from backend import hubdb as H
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


def woke(c, msg):
    """(a run was queued for the owner, the message is a quiet note)."""
    return jobs(c, "cpo") == 1, bool(msg["refs"].get("quiet"))


def asked(c, task, person="human:ana", kind="answer"):
    """The owner bot asks `person` on its task, and nothing is queued."""
    H.task_ask(c, "bot:cpo", task["id"], person, "Which color?", kind=kind, seen_version=task["version"])
    c.execute("DELETE FROM jobs")
    return H.task(c, task["id"])


def test_finished_checks_wake_the_owner_on_its_turn_once_per_head(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        msg = sent(c, task)
        assert woke(c, msg) == (True, False)
        assert (msg["refs"]["pr_head"], msg["refs"]["pr_result"]) == ("a" * 40, "pass")
        c.execute("DELETE FROM jobs")
        # A link the bot armed with wake_once is the same event, and says it was asked for.
        msg = sent(c, H.task(c, task["id"]), head="c" * 40, armed=True)
        assert woke(c, msg) == (True, False) and msg["refs"]["pr_armed"] is True


def test_finished_checks_on_a_task_that_asks_a_person_are_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = asked(c, plain_task(c))
        msg = sent(c, task)
        # The person's answer is what wakes it: the result waits for the owner's next run, as a note.
        assert woke(c, msg) == (False, True)


def test_finished_checks_on_a_task_waiting_on_an_ok_are_a_note(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = asked(c, plain_task(c), kind="ok")
        assert woke(c, sent(c, task)) == (False, True)


def test_finished_checks_after_the_person_answers_wake_again(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = asked(c, plain_task(c))
        assert woke(c, sent(c, task)) == (False, True)
        [ask] = [r for r in H.task_view(c, task["id"])["to_leave"] if r["kind"] == "answer"]
        H.task_answer(c, "human:ana", task["id"], ask["requirement_id"], "Blue")
        c.execute("DELETE FROM jobs")
        assert woke(c, sent(c, H.task(c, task["id"]), head="b" * 40)) == (True, False)


def test_finished_checks_on_a_task_that_is_not_open_send_nothing(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = plain_task(c)
        H.task_close(c, "bot:ops", task["id"], "Not needed.", task["version"])
        G.queue_checks_done(c, task, "a" * 40, "pass", "Checks finished on tico#1: passed", False)
        due(c, task)
        assert G.flush_wakes(c) == [] and jobs(c, "cpo") == 0


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
        assert msg["refs"]["quiet"] and jobs(c, "cpo") == 0


def snoozed(c, task, by, why="wakes when its checks finish"):
    """`by` sets the task aside until tomorrow, and nothing is queued."""
    H.task_snooze(c, by, task["id"], why, H.shift(H.now(), hours=20), seen_version=task["version"], mover=True)
    c.execute("DELETE FROM jobs")
    return H.task(c, task["id"])


def snooze_of(c, task):
    return c.execute("SELECT by_id, ended_at, ended_by_id FROM snoozes WHERE task_id=?", (task["id"],)).fetchone()


def test_finished_checks_end_the_owner_bots_own_snooze_and_wake_it(api):
    """Stress run, Nov 12: the Builder re-ran a flaky job and snoozed its task until 08:00 next day, "wakes when its
    checks finish". The checks passed 20 minutes later and it was only noted: the snooze held it 40 minutes, until a
    person ended it. A snooze the owner bot set on its own task is how it waits for CI: the head's result ends it."""
    setup(api)
    with api.app.state.store.transaction() as c:
        task = snoozed(c, plain_task(c), "bot:cpo")
        assert woke(c, sent(c, task, result="fail")) == (True, False)
        by, ended, ended_by = snooze_of(c, task)
        assert by == "bot:cpo" and ended and ended_by == H.KEEPER
        assert H.task(c, task["id"]).get("snooze") is None


def test_finished_checks_keep_a_snooze_someone_else_set(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        for who in ("human:ana", "bot:ops"):
            task = snoozed(c, plain_task(c, title=f"Set aside by {who}"), who)
            assert woke(c, sent(c, task)) == (False, True)
            assert snooze_of(c, task)[1] is None


def test_finished_checks_keep_the_owners_snooze_while_it_waits_on_a_person(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = snoozed(c, asked(c, plain_task(c)), "bot:cpo")
        assert woke(c, sent(c, task)) == (False, True)
        assert snooze_of(c, task)[1] is None
