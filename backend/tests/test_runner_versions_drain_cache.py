"""holding_since and drain_progress share one version row per read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import runner_versions
from backend import read_cache


def test_drain_helpers_share_version_row_within_one_read_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        assert runner_versions.drain_progress(c, "runner-drain-probe") == ""
        assert runner_versions.holding_since(c, "runner-drain-probe") is None
        values = read_cache.cache(c)
        key = ("one", runner_versions.VERSION_SQL, ("runner-drain-probe",))
        assert values is not None and key in values
