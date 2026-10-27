"""ready_waiting asks the database once per computer for the read snapshot."""

from backend.tests.test_api import api  # noqa: F401
from backend import start_places


def test_ready_waiting_reads_once_within_one_read_snapshot(api):
    statements = []
    with api.app.state.store.read_transaction() as c:
        c.set_trace_callback(statements.append)
        try:
            answers = [start_places.ready_waiting(c, "runner-ready-waiting-probe") for _ in range(3)]
        finally:
            c.set_trace_callback(None)
    assert answers == [False] * 3
    assert sum("JOIN start_places p" in s for s in statements) == 1, statements
