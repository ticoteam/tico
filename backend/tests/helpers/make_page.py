"""Helpers that other tests share, moved out of test_make_page.py so they import no test file."""

from backend.tests.helpers.api import assign, post, ready, runner


def start(api, tid, person="ben-test", key=None, machine=None):
    if machine is None:
        machine = runner(api)
        assign(api, machine, "coo")
        ready(api, machine, ["coo"], capabilities=["make_page"])
    sent = post(api, f"tasks/{tid}/make-page", {"text": "Make the alert flow with three scenario tabs."}, person, key)
    attempt = post(api, "jobs/claim", {"next_run": True}, machine["token"], key="claim:" + sent["message"]["id"])[
        "attempt"
    ]
    assert attempt["message"]["id"] == sent["message"]["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "make-page-test"}, machine["token"])
    return machine, attempt, sent["message"]
