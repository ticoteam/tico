"""Helpers that other tests share, moved out of test_claim_refusals.py so they import no test file."""

import pytest

from backend import execution, health, maintenance_health
from backend.store import H
from backend.tests.helpers.api import post


def held(api, machine, asks=True, **busy):
    """The runner's claim with its own busy lists (it takes nothing), then Health's row for ops and the Health
    Monitor's findings, with ops's queued work made 30 minutes old. `asks`: False for a runner that never claimed it."""
    if asks:
        assert post(api, "jobs/claim", busy, token=machine["token"])["attempt"] is None
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'", (H.shift(H.now(), minutes=-30),)
        )
        online = {r["id"] for r in health._online_runners(c)}
        rows = [r for r in health._waiting(c, online, store.settings)[2] if r["bot"] == "ops"]
        found = {f["check_id"] for f in maintenance_health.findings(c, store.settings)}
    return rows, found


def health_admin(api):
    from backend.auth import Identity

    return Identity("human:ana", "owner")


@pytest.fixture(autouse=True)
def forget_claims():
    execution.CLAIMED_BUSY.clear()
    yield
    execution.CLAIMED_BUSY.clear()
