"""Helpers that other tests share, moved out of test_task_quarantine.py so they import no test file."""

from backend import hubdb as H
from backend.tests.helpers.api import claim, get, post

# The open quarantines on one task or chat (backend/task_quarantine.py), with the task or chat each holds as its own
# column.
OPEN_SQL = (
    "SELECT *, CASE holdable_type WHEN 'task' THEN holdable_id END AS task_id, "
    "CASE holdable_type WHEN 'conversation' THEN holdable_id END AS conversation_id FROM holds "
    "WHERE kind='quarantine' AND holdable_type IN ('task','conversation') AND released_at IS NULL"
)


def started(api, machine):
    attempt = claim(api, machine)
    assert attempt is not None
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, machine["token"])
    return attempt


def refusals(api, attempt, task, count=H.QUARANTINE_AT + 1):
    for _ in range(count):
        row = get(api, "tasks/" + task["id"])["task"]
        result = post(
            api, "tasks/" + task["id"], {"version": row["version"], "number": 993}, attempt["token"], expected=403
        )
        assert result["error"]["code"] == "identity"
