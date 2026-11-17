"""The claim's work path reads a runner's bots as sets: its statements do not grow with the number of bots it has queued
work for (execution.candidate, claim_loads.read). Measured on the store's connections, as the claim makes them."""

from backend.tests.helpers.api import headers, post, runner
from backend.tests.helpers.claim_statements import seed, traced


def claim(api, machine):
    """POST the claim as the computer: (answer, statements)."""
    with traced(api) as seen:
        response = api.post(
            "/api/v2/jobs/claim", json={"bot": None, "start_places": True}, headers=headers(machine["token"])
        )
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def handed_run(api, machine, answer):
    """The run the claim handed out completes, and its bot is asked again (so the next claim has work)."""
    attempt = answer.get("attempt") or {}
    assert attempt, answer
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "t-" + aid}, machine["token"])
    post(api, f"attempts/{aid}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."}, machine["token"])
    bot = attempt.get("bot_id") or attempt.get("bot")
    post(api, "chat/" + bot, {"text": "Next, please."})


def work_claim_statements(api, count, prefix):
    machine = seed(api, prefix, count)
    answer, _ = claim(api, machine)  # the first claim may do the company's first-time reads
    handed_run(api, machine, answer)
    answer, statements = claim(api, machine)
    assert answer.get("attempt"), answer  # the measured claim hands out a run: it took the work path
    return statements


def test_claim_work_path_statements_do_not_grow_with_assigned_bots(api):
    few = work_claim_statements(api, 5, "few")
    many = work_claim_statements(api, 20, "many")
    assert few == many, (few, many)


def test_runs_waiting_out_a_claim_error_add_no_statements_to_an_idle_claim(api):
    """A run a claim error holds waits up to 50 minutes while its computer polls: the claim passes its bot over in the
    query that finds bots with work (execution.CLAIM_WAITING_SQL), so the poll reads no more than with nothing queued."""
    from backend import holds
    from backend.store import H

    machine = seed(api, "held", 5)
    store = api.app.state.store
    with store.transaction() as c:
        for job, bot in c.execute("SELECT id,bot_id FROM jobs WHERE state='queued'").fetchall():
            holds.put(c, holds.CLAIM_ERROR, "job", job, actor=H.KEEPER, bot_id=bot, until_at=H.shift(H.now(), hours=1))
    claim(api, machine)  # the first claim may do the company's first-time reads
    answer, held = claim(api, machine)
    assert answer.get("attempt") is None, answer
    with store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled' WHERE state='queued'")
    answer, empty = claim(api, machine)
    assert answer.get("attempt") is None, answer
    assert held == empty, (held, empty)
