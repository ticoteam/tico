"""company_waiter reads each waiting computer's row once per read snapshot, however many of its jobs wait."""

from backend.auth import Identity
from backend.models import Claim
from backend.store import H
from backend.tests.test_api import api, assign, post, ready, runner  # noqa: F401


def test_company_waiter_reads_a_computer_with_two_waits_once(api):
    here, other = runner(api, label="Claiming Mac"), runner(api, label="Waiting Mac")
    for bot in ("finance", "doc-updater"):
        assign(api, other, bot)
        post(api, "chat/" + bot, {"text": "Synthetic waiting work"})
    ready(api, other, ["finance", "doc-updater"])
    store = api.app.state.store
    with store.transaction() as c:
        jobs = [r[0] for r in c.execute("SELECT id FROM jobs WHERE state='queued' AND bot IN ('finance','doc-updater')")]
        c.executemany("INSERT INTO org_turn_waits(job_id,created_at) VALUES(?,?)", [(job, H.now()) for job in jobs])
    assert len(jobs) >= 2
    statements = []
    with store.read_transaction() as c:
        row = c.execute("SELECT * FROM runners WHERE id=?", (here["runner_id"],)).fetchone()
        c.set_trace_callback(statements.append)
        try:
            who = Identity("runner:" + here["runner_id"], "runner", runner_id=here["runner_id"])
            # The waiting computer has not claimed lately, so neither wait is handed out.
            assert api.app.state.execution.company_waiter(c, who, Claim(), row) is None
        finally:
            c.set_trace_callback(None)
    assert sum("FROM runners WHERE id=" in s for s in statements) == 1, statements
