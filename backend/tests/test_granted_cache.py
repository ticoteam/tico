"""granted() reuses one answer per subject within a read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import credentials


def test_granted_reuses_the_same_frozenset_within_a_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        first = credentials.granted(c, credentials.COMPUTERS)
        second = credentials.granted(c, credentials.COMPUTERS)
        assert first is second
        assert isinstance(first, frozenset)
