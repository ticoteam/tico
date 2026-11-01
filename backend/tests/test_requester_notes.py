"""A Dev ticket's requester hears step moves as notes and is woken only when the ticket is declined, blocked or done."""
from backend import task_relations as TR
from backend.store import H
from backend.tests.test_api import api  # noqa: F401


def dev_ticket(api, owner="bot:cpo", requester="bot:coo"):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Dev tickets", [
            {"name": "In Progress", "status": "doing"}, {"name": "Review 1", "status": "review"},
            {"name": "Done", "status": "done"}])
        task = H.task_create(c, requester, "Improve messages", "Build messages.", owner, type=typ["id"],
                             step="In Progress", lint=False)
        c.execute("DELETE FROM jobs")
    return task


def heard(api, bot):
    with api.app.state.store.read() as c:
        jobs = c.execute("SELECT count(*) FROM jobs WHERE bot=?", (bot,)).fetchone()[0]
        notes = c.execute("SELECT count(*) FROM messages WHERE to_actor=? AND json_extract(refs,'$.quiet')=1 "
                          "AND json_extract(refs,'$.rule') IS NOT NULL", ("bot:" + bot,)).fetchone()[0]
    return jobs, notes


def test_a_step_move_is_a_note_to_the_requester_and_wakes_nobody(api):
    task = dev_ticket(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", task["id"], step="Review 1")
    assert heard(api, "coo")[0] == 0 and heard(api, "cpo")[0] == 0
    assert heard(api, "coo")[1] >= 1


def test_declined_wakes_the_requester(api):
    task = dev_ticket(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", task["id"], status="declined", note="Not building this.")
    assert heard(api, "coo")[0] == 1


def test_blocked_on_someone_else_wakes_the_requester_once_and_a_wait_on_the_owner_does_not(api):
    task = dev_ticket(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="human:ana", note="Needs the API key.")
        H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="human:ana")       # still waiting: no news
    assert heard(api, "coo")[0] == 1                    # the Dev ticket's own Blocked notice carries the wake
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        plain = H.task_create(c, "bot:coo", "Draft the note", "x", "bot:cpo", lint=False)
        own = H.task_create(c, "bot:cpo", "Set up the repo", "x", "bot:cpo", lint=False)
        TR.add_blocker(c, "bot:cpo", plain["id"], own["id"])                 # its owner's own work: no news
        review = H.task_create(c, "bot:cpo", "Review the draft", "x", "bot:ops", parent_id=plain["id"], lint=False)
        TR.add_blocker(c, "bot:cpo", plain["id"], review["id"])              # work it split off to a bot: no news
        c.execute("DELETE FROM jobs WHERE bot='cpo'")
    assert heard(api, "coo")[0] == 0
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", plain["id"], status="waiting", waiting_on="human:ana", note="Which date?")
    assert heard(api, "coo")[0] == 1


def test_done_wakes_a_requester_that_waits_on_the_ticket_once(api):
    with api.app.state.store.transaction() as c:
        lead = H.task_create(c, "human:ana", "Ship messages", "Plan it.", "bot:coo", lint=False)
        ticket = H.task_create(c, "bot:coo", "Improve messages", "Build messages.", "bot:cpo", parent_id=lead["id"], lint=False)
        H.task_update(c, "bot:coo", lead["id"], status="waiting", note="Waiting on the ticket.")
        c.execute("DELETE FROM jobs")
        H.task_update(c, "bot:cpo", ticket["id"], status="doing")
    assert heard(api, "coo")[0] == 0, "progress on the ticket is not news that wakes"
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cpo", ticket["id"], status="done", note="Shipped.")
    assert heard(api, "coo")[0] == 1
