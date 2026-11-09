"""Helpers that other tests share, moved out of test_threads_delivery.py so they import no test file."""

from backend import threads
from backend.tests.helpers.api import headers


def new_thread(api, audience="members", members=("human:ben",), title="Budget", token="ana-test"):
    payload = {"title": title, "audience": audience}
    if audience == "members":
        payload["members"] = list(members)
    response = api.post("/api/v2/threads", json=payload, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test", key=None, parent=None):
    payload = {"text": text, **({"parent_message_id": parent} if parent else {})}
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json=payload, headers=headers(token, key))
    assert response.status_code == 200, response.text
    return response.json()


def bot_post(api, bot, thread_id, text, **kwargs):
    with api.app.state.store.transaction() as c:
        return threads.post(c, bot, thread_id, text, **kwargs)


def rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def delivery(api, message_id, recipient):
    found = rows(api, "SELECT * FROM message_deliveries WHERE message_id=? AND recipient=?", message_id, recipient)
    return found[0] if found else None


def queued_job(api, carrier_id):
    found = rows(api, "SELECT state FROM jobs WHERE message_id=?", carrier_id)
    return found[0]["state"] if found else None
