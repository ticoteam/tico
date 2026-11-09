"""Operations listing loads every computer's own keys in one pass."""

from backend.tests.test_api import api, runner  # noqa: F401
from backend import computer_keys


def test_listing_by_runner_matches_per_runner_listing(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    # No keys yet: empty maps must match.
    with api.app.state.store.read() as c:
        by = computer_keys.listing_by_runner(c)
        assert by.get(a["runner_id"], {}) == computer_keys.listing(c, a["runner_id"])
        assert by.get(b["runner_id"], {}) == computer_keys.listing(c, b["runner_id"])
