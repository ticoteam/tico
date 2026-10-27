"""company_waiter reuses RUNNER_SQL rows via read_cache.one."""

from backend.tests.test_api import api  # noqa: F401
from backend.execution import RUNNER_SQL
from backend import read_cache


def test_runner_sql_is_shared_within_one_read_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        rid = "runner-company-waiter-probe"
        first = read_cache.one(c, RUNNER_SQL, (rid,))
        second = read_cache.one(c, RUNNER_SQL, (rid,))
        assert first is second
        assert first is None
