"""One rule for "computer online": Health, operations (Settings and the computer pop-up), readiness and the list the claim
reads agree on a computer at the edge of the window, and a computer one second outside it is offline in all of them."""

import pytest

from backend import readiness, runner_online
from backend.store import H
from backend.tests.helpers.api import get, runner
from backend.tests.helpers.getting_started import SIGNED_IN, add_bot, heartbeat


@pytest.mark.parametrize(
    ("seconds_ago", "online"),
    [(runner_online.ONLINE_SECONDS - 1, True), (runner_online.ONLINE_SECONDS + 1, False)],
)
def test_health_and_readiness_agree_on_a_computer_at_the_online_boundary(api, seconds_ago, online):
    rid = runner(api)["runner_id"]
    add_bot(api, "helper")
    heartbeat(api, rid, seconds_ago=seconds_ago, runtimes=SIGNED_IN)
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES('helper',?,1,?,'t')",
            (rid, H.now()),
        )
    with store.read() as c:
        verdict = readiness.can_run(c, "helper", "codex")
        listed = {r["id"] for r in runner_online.online_rows(c)}
    [computer] = [row for row in get(api, "health")["computers"] if row["id"] == rid]
    [machine] = [row for row in get(api, "operations")["computers"] if row["id"] == rid]
    assert computer["online"] is online
    assert machine["online"] is online
    assert verdict["online"] is online
    assert (rid in listed) is online
