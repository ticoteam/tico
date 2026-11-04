"""paused reuses the release row for the read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import runner_versions
from backend import read_cache


def test_paused_reuses_release_row_within_one_read_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        first = runner_versions.paused(c, "runner-paused-probe")
        second = runner_versions.paused(c, "runner-paused-probe")
        assert first == second == ""
        values = read_cache.cache(c)
        key = ("one", "SELECT release FROM runner_versions WHERE runner_id=?", ("runner-paused-probe",))
        assert values is not None and key in values
