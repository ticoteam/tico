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
