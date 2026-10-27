"""start_places.held reuses one map per runner for the read snapshot."""

import pytest

from backend.tests.test_api import api  # noqa: F401
from backend import start_places


def test_held_reuses_the_map_within_one_read_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        first = start_places.held(c, "runner-cache-probe")
        assert first is start_places.held(c, "runner-cache-probe")
        # Shared by every caller in the snapshot, so nobody can change it.
        with pytest.raises(TypeError):
            first["ops"] = 1
