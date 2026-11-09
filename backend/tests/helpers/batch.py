"""Helpers that other tests share, moved out of test_batch.py so they import no test file."""

from backend.tests.helpers.api import post, setup_attempt, legacy_send_approval


def approval_for_ana(api, bot="finance", kind="spend", payload=None, task=None, token=None):
    if kind == "send":
        return legacy_send_approval(api, payload, actor="bot:" + bot, task_id=task)
    attempt = {"token": token} if token else setup_attempt(api, bot)[2]
    body = {"kind": kind, "payload": payload or {"amount": 1200, "account": "ads", "what": "LinkedIn ads"}}
    if task:
        body["task_id"] = task
    return post(api, "approvals", body, token=attempt["token"])
