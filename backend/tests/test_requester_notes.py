"""A task's requester bot is not woken by its stage moves or by how it ends; the owner of the task that filed it is
woken once when what it filed has ended (task_engine._ended, task_effects._tell_requester)."""

from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

DEV = (
    {"name": "In Progress", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
    {"name": "Review 1", "owner": "bot:ops", "requirements": [{"kind": "owner_done"}]},
)


def dev_task(api, owner="bot:cpo", requester="bot:coo"):
    with api.app.state.store.transaction() as c:
        X.pipeline(c, "dev", "Dev", DEV)
        task = X.new_task(c, requester, "Improve messages", "dev", assignee=owner)
        c.execute("DELETE FROM jobs")
    return task


def heard(api, bot):
    """The bot's queued runs, and the quiet notes it was written."""
    with api.app.state.store.read() as c:
        jobs = c.execute("SELECT count(*) FROM jobs WHERE bot_id=?", (bot,)).fetchone()[0]
        notes = c.execute(
            "SELECT count(*) FROM messages WHERE recipient_id=? AND json_extract(refs,'$.quiet')=1",
            ("bot:" + bot,),
        ).fetchone()[0]
    return jobs, notes


def test_a_stage_move_wakes_no_requester(api):
    task = dev_task(api)
    with api.app.state.store.transaction() as c:
        moved = E.move(c, task["id"], E.NEXT, "bot:cpo", "Built it", seen_version=task["version"])
        assert moved["stage"]["name"] == "Review 1"
    assert heard(api, "coo")[0] == 0 and heard(api, "cpo")[0] == 0


def test_a_close_is_a_note_to_the_requester_and_wakes_nobody(api):
    task = dev_task(api)
    with api.app.state.store.transaction() as c:
        E.move(c, task["id"], E.CLOSED, "bot:cpo", "Not building this.", seen_version=task["version"])
    assert heard(api, "coo") == (0, 1)


def test_done_wakes_the_owner_of_the_task_that_filed_it_once(api):
    with api.app.state.store.transaction() as c:
        lead = H.task_create(c, "human:ana", "Ship messages", "Plan it.", "bot:coo", lint=False)
        sub = H.task_create(
            c, "bot:coo", "Improve messages", "Build messages.", "bot:cpo", parent_id=lead["id"], lint=False
        )
        c.execute("DELETE FROM jobs")
        E.move(c, sub["id"], E.DONE, "bot:cpo", "Shipped.", seen_version=E.task(c, sub["id"])["version"])
    assert heard(api, "coo")[0] == 1
    with api.app.state.store.read() as c:
        keys = [k for k, who in X.wakes(c, lead["id"]) if who == "bot:coo"]
    assert len([k for k in keys if ":filed:" in k]) == 1
