"""Helpers that other tests share, moved out of test_thread_api.py so they import no test file."""

from backend.tests.helpers.api import headers


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


def set_bot_access(api, slug, see, read, write):
    revision = api.get(f"/api/v2/bots/{slug}/access", headers=headers()).json()["revision"]
    response = api.put(
        f"/api/v2/bots/{slug}/access",
        headers=headers(),
        json={"see": see, "read": read, "write": write, "revision": revision},
    )
    assert response.status_code == 200, response.text


def sidebar(api, token="ben-test", **params):
    response = api.get("/api/v2/threads", params=params, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def row_of(listed, thread_id):
    return next((t for t in listed["threads"] + listed["dms"] if t["id"] == thread_id), None)
