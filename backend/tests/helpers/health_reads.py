"""Helpers that other tests share, moved out of test_health_reads.py so they import no test file."""

import contextlib
from backend.tests.helpers.api import headers


def counted(api, monkeypatch, path, token="ana-test", trace=None):
    """GET `path` as `token`: the body, and the statements it took on the one snapshot it read."""
    store, seen = api.app.state.store, []
    if trace is not None:
        seen = trace
    store.roster_rows.clear()  # per-store caches start empty, so every count is the same count
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction"):

            def traced(opened=getattr(store, name)):
                @contextlib.contextmanager
                def borrowed():
                    with opened() as c:
                        c.set_trace_callback(lambda q: seen.append(q))
                        try:
                            yield c
                        finally:
                            c.set_trace_callback(None)

                return borrowed()

            patched.setattr(store, name, traced)
        response = api.get(path, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)
