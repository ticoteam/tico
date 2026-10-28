"""Health names the repositories online computers could not copy, reading every computer's report in one scan."""

import json

from backend.tests.test_api import api, runner  # noqa: F401
from backend import health


def test_failed_copies_on_online_computers_read_in_one_scan(api):
    made = {label: runner(api, "ana", label) for label in ("A Mac", "B Mac", "C Mac")}
    with api.app.state.store.transaction() as c:
        for label, r in made.items():
            report = [{"full_name": f"acme/{label[0].lower()}", "state": "failed", "error": "access denied"},
                      {"full_name": "acme/fine", "state": "ready"}]
            c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                      ("computer-repositories:" + r["runner_id"], json.dumps({"repositories": report})))
    online = {made["A Mac"]["runner_id"], made["B Mac"]["runner_id"]}
    statements = []
    with api.app.state.store.read_transaction() as c:
        c.set_trace_callback(statements.append)
        try:
            found = health.failed_repository_copies(c, online)
        finally:
            c.set_trace_callback(None)
    # C Mac is offline: its failure is not news.
    assert found == [("A Mac", "acme/a", "access denied"), ("B Mac", "acme/b", "access denied")]
    assert sum("registry_metadata" in s for s in statements) == 1, statements
