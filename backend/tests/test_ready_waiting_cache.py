"""ready_waiting reuses one answer per runner for the read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import start_places


def test_ready_waiting_reuses_answer_within_one_read_snapshot(api):
    with api.app.state.store.read_transaction() as c:
        first = start_places.ready_waiting(c, "runner-ready-waiting-probe")
        assert first is start_places.ready_waiting(c, "runner-ready-waiting-probe")
        assert first is False
