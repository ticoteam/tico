"""Helpers that other tests share, moved out of test_thread_events.py so they import no test file."""

from backend import events as E
from backend.tests.helpers.api import headers


def who(api, token):
    return api.app.state.auth.settings.test_identities[token]


def new_thread(api, title="Budget", members=("human:ben",), body=None, parent_message_id=None):
    payload = {"title": title, "audience": "members", "members": list(members)}
    if body:
        payload["body"] = body
    if parent_message_id:
        payload["parent_message_id"] = parent_message_id
    response = api.post("/api/v2/threads", json=payload, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def cursor(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def read_as(api, person, after, topics, threads_scope=()):
    """The events `person` may be sent after `after`: (seq, topic, data) as events.read gives them."""
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        events, _, _, _ = E.read(
            c,
            auth,
            person,
            after,
            topics=topics,
            threads_scope=threads_scope,
            task_views=lambda rows, c, who, visible_sql="1": [],
            task_view=None,
        )
    return events
