"""Helpers that other tests share, moved out of test_session_clear.py so they import no test file."""

from backend.tests.helpers.api import headers, post


def clear(api, bot="ops", token="ana-test"):
    return api.post(f"/api/employees/{bot}/session/clear", json={}, headers=headers(token))


def finish(api, machine, turn):
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "t-" + turn["id"][:8]}, machine["token"])
    post(
        api,
        f"attempts/{turn['id']}/complete",
        {"outcome": "completed", "last_seq": 0, "text": "Done."},
        machine["token"],
    )
