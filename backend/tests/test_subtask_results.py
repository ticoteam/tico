"""A task waits on the sub-tasks it filed; when the last one ends its owner is woken once, in the parent's own lane,
with how each ended (task_engine._ended). Nothing about a sub-task's progress wakes the parent or the task above it."""

from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.task_lanes import task_bot
from backend.tests.helpers.subtask_results import finish, queued_in, start


def queued(api, bot):
    with api.app.state.store.read() as c:
        return queued_in(c, bot)


def done(api, tid, by, note):
    with api.app.state.store.transaction() as c:
        H.task_done(c, by, tid, note, H.task(c, tid)["version"])


def test_nested_sub_tasks_wake_only_the_task_that_filed_them_once_each(api):
    machine = task_bot(api, lanes=True, others=("coo", "finance", "cpo"))
    with api.app.state.store.transaction() as c:
        lead = H.task_create(c, "human:ana", "Deliver the import", "Plan and delegate.", "bot:coo", lint=False)
    planning = start(api, machine, lead["id"], "lead-task")
    with api.app.state.store.transaction() as c:
        build = H.task_create(
            c, "bot:coo", "Implement the import", "Build and review.", "bot:ops", parent_id=lead["id"], lint=False
        )
    finish(api, machine, planning)
    builder = start(api, machine, build["id"], "builder-task")
    with api.app.state.store.transaction() as c:
        reviews = [
            H.task_create(
                c,
                "bot:ops",
                f"Review the import ({who})",
                "Review it.",
                "bot:" + who,
                parent_id=build["id"],
                lint=False,
            )
            for who in ("finance", "cpo")
        ]
    finish(api, machine, builder)
    for review, who in zip(reviews, ("finance", "cpo")):
        checking = start(api, machine, review["id"], who + "-review")
        with api.app.state.store.transaction() as c:
            H.task_comment(c, "bot:" + who, review["id"], "No blocking findings.")
        assert not queued(api, "ops"), "a comment on a sub-task is not news that wakes its filer"
        done(api, review["id"], "bot:" + who, "Review recorded.")
        finish(api, machine, checking)
        assert not queued(api, "coo"), "review progress never wakes the task above the builder"
    # Both reviews ended: the builder is woken once, in its own task's lane, with how each ended.
    back = start(api, machine, build["id"], "builder-task")
    assert "The tasks this one filed have ended" in back["message"]["body"]
    assert "Review the import (finance): Done" in back["message"]["body"]
    assert "Review the import (cpo): Done" in back["message"]["body"]
    finish(api, machine, back)
    assert not queued(api, "ops")
    done(api, build["id"], "bot:ops", "Implemented and reviewed.")
    up = start(api, machine, lead["id"], "lead-task")
    assert "Implement the import: Done" in up["message"]["body"]
    with api.app.state.store.read() as c:
        filed = [k for k, _ in X.wakes(c, lead["id"], "bot:coo") if ":filed:" in k]
    assert len(filed) == 1 and filed[0].endswith(":filed:" + build["id"])


def test_private_child_results_stay_off_a_wider_parent_and_closed_parents_do_not_wake(api):
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Deliver the import", "Company work.", "bot:ops", lint=False)
        child = H.task_create(
            c,
            "bot:ops",
            "Private review",
            "Restricted evidence.",
            "bot:finance",
            parent_id=parent["id"],
            private=True,
            lint=False,
        )
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_done(c, "bot:finance", child["id"], "Private finding.", H.task(c, child["id"])["version"])
        messages = [dict(r) for r in c.execute("SELECT * FROM messages WHERE rowid>?", (since,))]
        assert all(
            "Private finding." not in r["body"] for r in messages if r["conversation_id"] == parent["conversation_id"]
        )
        # The filer is woken, but the wake is kept in the parent's thread, which Ana reads too and the private sub-task
        # is not hers to open: it is "A sub-task", never its title.
        kept = [r for r in messages if r["conversation_id"] == parent["conversation_id"]]
        assert all("Private review" not in r["body"] for r in kept), kept
        assert any(
            r["recipient_id"] == "bot:ops" and "The tasks this one filed have ended: A sub-task: Done" in r["body"]
            for r in kept
        ), kept
        late = H.task_create(
            c, "bot:ops", "Another review", "Review it.", "bot:finance", parent_id=parent["id"], lint=False
        )
        H.task_close(c, "human:ana", parent["id"], "Not needed.", H.task(c, parent["id"])["version"])
        assert H.task(c, late["id"])["status"] == "closed", "a closed parent closes its open sub-tasks"
        H.task_move(c, "human:ana", late["id"], H.TASK_REOPEN, "Reopened by hand", H.task(c, late["id"])["version"])
        since = c.execute("SELECT max(rowid) FROM messages").fetchone()[0]
        H.task_done(c, "bot:finance", late["id"], "Late result.", H.task(c, late["id"])["version"])
        assert not c.execute(
            "SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
            "WHERE j.bot_id='ops' AND j.state='queued' AND m.rowid>?",
            (since,),
        ).fetchone()
