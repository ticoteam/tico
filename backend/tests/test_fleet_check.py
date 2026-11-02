"""hub health check / GET /api/v2/fleet/check: spend checks share one company Default."""

from backend import fleet_check, usage_limits
from backend.tests.test_api import api  # noqa: F401


def test_fleet_check_builds_company_default_once(api, monkeypatch):
    calls = []
    real = usage_limits.company

    def once(c):
        calls.append(1)
        return real(c)

    monkeypatch.setattr(usage_limits, "company", once)
    store, auth = api.app.state.store, api.app.state.auth
    who = store.settings.test_identities["ana-test"]
    with store.read() as c:
        out = fleet_check.check(c, who, auth, store.settings)
    assert "issues" in out
    assert len(calls) == 1


def test_pause_reason_batch_and_single_read_pick_the_same_newest_row_on_a_tied_time(api):
    """Two pauses with the same `since`: the batch and the single-row read both take the one inserted last."""
    from backend.fleet_check import PAUSE_REASON_SQL, prime_pause_reasons
    from backend.read_cache import one
    store = api.app.state.store
    with store.transaction() as c:
        for row_id, reason in (("older", "over limit"), ("newer", "manual")):
            c.execute("INSERT INTO bot_status_history(id,bot,state,focus,task_id,since,until,reason,by) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (row_id, "tied", "paused", "", "", "2026-11-01T10:00:00Z", None,
                                                   reason, "t"))
    with store.read_transaction() as c:
        prime_pause_reasons(c, ["tied"])
        batched = one(c, PAUSE_REASON_SQL, ("tied",))["reason"]
        single = c.execute(PAUSE_REASON_SQL, ("tied",)).fetchone()["reason"]
    assert batched == single == "manual"
