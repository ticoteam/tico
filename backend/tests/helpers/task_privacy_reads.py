"""Helpers that other tests share, moved out of test_task_privacy_reads.py so they import no test file."""

from backend.tests.helpers.api import get, headers


def sql(api, text, token="ana-test"):
    response = api.post("/api/v2/sql", json={"sql": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def traced_get(api, path, token):
    """The response to one GET and the number of SQL statements it ran on every pooled connection."""
    response, statements = traced(api, lambda: get(api, path, token))
    return response, len(statements)


def traced(api, request):
    """The result of `request()` and the SQL statements it ran on every pooled connection."""
    store = api.app.state.store
    statements, take, give = [], store._take, store._give

    def traced():
        c = take()
        c.set_trace_callback(statements.append)
        return c

    def untraced(c):
        c.set_trace_callback(None)
        give(c)

    store._take, store._give = traced, untraced
    try:
        return request(), statements
    finally:
        store._take, store._give = take, give
