"""live_tasks reuses one set per read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import execution


def test_live_tasks_reuses_the_set_within_one_read_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        first = execution.live_tasks(c)
        assert first is execution.live_tasks(c)
        assert isinstance(first, set)
