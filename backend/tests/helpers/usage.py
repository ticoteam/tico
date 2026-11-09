"""Helpers that other tests share, moved out of test_usage.py so they import no test file."""

from backend.tests.helpers.api import post


def complete(api, r, attempt, usage=None, **more):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "thread-1"}, token=r["token"])
    body = {"outcome": "completed", "text": "Done.", "last_seq": 0, "tokens_in": 3000, "tokens_out": 500, **more}
    if usage is not None:
        body["usage"] = usage
    return post(api, f"attempts/{attempt['id']}/complete", body, token=r["token"])
