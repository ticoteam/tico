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


def test_profiles_by_runner_reads_sign_in_cutoffs_refreshes_and_names_like_each_computer(api):
    from backend import subscription_refresh as refresh
    from backend.store import H
    from backend.subscription_identity import connection_id, display_names
    a = runner(api, "ana", "A Mac")
    rid, at = a["runner_id"], H.shift(H.now(), minutes=-30)
    rows = {
        refresh.signin_key(rid, "work"): {"codex": H.shift(H.now(), minutes=-20)},
        refresh.key(rid, "work", "codex"): {"state": "requested", "requested_at": at, "expires_at": H.shift(H.now(), hours=1)},
        subscriptions.weekly_key(rid, "work", "codex"): {"used_percent": 40, "reported_at": H.shift(H.now(), minutes=-10)},
        connection_id(rid, "work"): {"display_name": "Work login"},
    }
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO computer_profiles VALUES(?,?,?,?)",
                  (rid, "work", json.dumps({"codex": {"signed_in": True, "weekly": {"used_percent": 10, "reported_at": at}}}), at))
        c.executemany("INSERT INTO registry_metadata VALUES(?,?)", [(k, json.dumps(v)) for k, v in rows.items()])
    with api.app.state.store.read_transaction() as c:
        runner_row = c.execute("SELECT * FROM runners WHERE id=?", (rid,)).fetchone()
        [profile] = subscriptions.profiles_by_runner(c)[rid]
        assert [profile] == subscriptions.computer_profiles(c, runner_row, True)
        codex = profile["runtimes"]["codex"]
        # The refresh asked before the sign-in changed is outdated; the reading before it is dropped, the manual one kept.
        assert codex["refresh"]["state"] == "outdated"
        assert codex["weekly"]["used_percent"] == 40
        assert profile["display_name"] == "Work login"
        assert display_names(c) == {connection_id(rid, "work"): "Work login"}
