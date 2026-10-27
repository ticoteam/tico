"""Operations listing builds every computer's subscription profiles in one pass."""

import json

from backend.tests.test_api import api, runner  # noqa: F401
from backend import subscriptions


def test_profiles_by_runner_matches_per_computer(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO computer_profiles VALUES(?,?,?,?)",
                  (a["runner_id"], "work", json.dumps({"codex": {"signed_in": True}}), "2026-01-01T00:00:00Z"))
        c.execute("INSERT INTO computer_profiles VALUES(?,?,?,?)",
                  (b["runner_id"], "home", json.dumps({"claude": {"signed_in": False}}), "2026-01-01T00:00:00Z"))
    with api.app.state.store.read() as c:
        by = subscriptions.profiles_by_runner(c)
        for rid, machine in ((a["runner_id"], a), (b["runner_id"], b)):
            runner_row = c.execute("SELECT * FROM runners WHERE id=?", (rid,)).fetchone()
            assert by.get(rid) == subscriptions.computer_profiles(c, runner_row, True)
