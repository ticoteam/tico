"""Helpers that other tests share, moved out of test_health_queries.py so they import no test file."""

import contextlib
from backend.tests.helpers.api import headers


def counted(api, monkeypatch, path, token="ana-test"):
    """GET `path` as `token`: the body, and the statements on the page's read snapshot. Each request starts from cold
    roster caches, as a poll from a new process would."""
    store, seen = api.app.state.store, []
    store.roster_rows.clear()
    with monkeypatch.context() as patched:
        opened = store.read_transaction

        @contextlib.contextmanager
        def traced():
            with opened() as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        # Background workers borrow store.read too; their periodic reads are not the page's work.
        patched.setattr(store, "read_transaction", traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)
