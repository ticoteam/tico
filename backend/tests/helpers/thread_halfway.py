"""Helpers that other tests share, moved out of test_thread_halfway.py so they import no test file."""

from backend.tests.helpers.api import headers, post


def new_thread(api, members=("human:ben", "bot:ops"), title="Budget", token="ana-test"):
    return post(api, "threads", {"title": title, "audience": "members", "members": list(members)}, token=token)


def say(api, thread_id, text, token="ana-test", expected=200, **more):
    return post(api, f"threads/{thread_id}/messages", {"text": text, **more}, token=token, expected=expected)


def bot_status(api, thread_id, token="ana-test"):
    r = api.get(f"/api/v2/threads/{thread_id}/bot-status", headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["bots"]
