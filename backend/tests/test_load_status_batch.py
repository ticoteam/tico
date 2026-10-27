"""load_status shares one per-snapshot fleet map instead of one samples query per computer."""

import time

from backend.tests.test_api import api, runner  # noqa: F401
from backend.tests.test_runner_load import point
from backend import performance as P
from backend import models as M


def test_load_status_by_runner_matches_per_computer_and_reuses_the_map(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    now = int(time.time()) // 60 * 60
    with api.app.state.store.transaction() as c:
        for rid in (a["runner_id"], b["runner_id"]):
            P.ingest(c, rid, [M.PerformanceSample(**point(now - 300 + i * 60)) for i in range(6)], now=now)
    with api.app.state.store.read_transaction() as c:
        by = P.load_status_by_runner(c, now)
        assert by is P.load_status_by_runner(c, now)
        assert by[a["runner_id"]] == P.load_status(c, a["runner_id"], now)
        assert by[b["runner_id"]]["overloaded"] is True
        assert by[a["runner_id"]]["overloaded"] is True
