"""Helpers for the native threads e2e tests (test_threads_e2e_*.py), over the REST shapes in
docs/dev/threads-contract.md section 10.

The tests skip until the threads API is served (`require_threads_api`), so they can land before the track that builds
it. The skip says what it waits for; after the build, every skip becomes a real check. A route the contract does not
name yet is called through one helper here (home_id), so the gap is in one place.
"""

import uuid

import pytest

from backend.tests.test_api import headers

API_NOT_BUILT = "not built yet: threads API (docs/dev/threads-contract.md section 10)"


def home_id(api, slug):
    """The id of a bot's Home. Contract gap: section 10 names no HTTP route for a Home (threads.home makes it lazily),
    so the id is asked of the core here; every read of the Home after that goes through GET /api/v2/threads/{id}."""
    from backend import threads
    with api.app.state.store.transaction() as c:
        return threads.home(c, "bot:" + slug)["id"]


def call(api, method, path, token="ana-test", body=None):
    """One request as the API test client sends it, with the teammate's token and a fresh Idempotency-Key."""
    return api.request(method, "/api/v2/" + path, json=body, headers=headers(token))


def require_threads_api(api):
    """Skip the test unless the threads sidebar is served; return the owner's sidebar otherwise."""
    r = api.get("/api/v2/threads", headers=headers("ana-test"))
    if r.status_code == 404:
        pytest.skip(API_NOT_BUILT)
    assert r.status_code == 200, r.text
    return r.json()


def ok(r):
    """The reply of a write that the caller expects to succeed (200 or 201)."""
    assert r.status_code in (200, 201), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json()


def create_thread(api, token="ana-test", *, title=None, audience="members", group=None, members=(), body=None,
                  parent_message_id=None):
    """A thread, as the new-thread sheet makes it. Roots default to private (`members`) with the starter as owner."""
    payload = {"title": title or "Thread " + uuid.uuid4().hex[:6], "audience": audience,
               "audience_group_id": group, "members": list(members)}
    if body is not None:
        payload["body"] = body
    if parent_message_id is not None:
        payload["parent_message_id"] = parent_message_id
    return ok(call(api, "POST", "threads", token, payload))


def say(api, token, thread_id, text, *, parent_message_id=None, kind="say", to=None):
    """One message. With `parent_message_id` it goes into that message's sub-thread, created if it has none."""
    payload = {"text": text, "kind": kind}
    if parent_message_id is not None:
        payload["parent_message_id"] = parent_message_id
    if to is not None:
        payload["to"] = to
    return ok(call(api, "POST", f"threads/{thread_id}/messages", token, payload))


def read_status(api, token, thread_id):
    """200 when the teammate can open the thread, else the status it got. A hidden thread is never a 403."""
    r = call(api, "GET", f"threads/{thread_id}", token)
    assert r.status_code in (200, 404), f"{token}: {r.status_code} {r.text}"
    return r.status_code


def can_read(api, token, thread_id):
    return read_status(api, token, thread_id) == 200


def timeline(api, token, thread_id):
    return ok(call(api, "GET", f"threads/{thread_id}/timeline", token))["messages"]


def sidebar_ids(api, token):
    """Every thread id the sidebar lists for this teammate: attention, threads and DMs."""
    page = ok(call(api, "GET", "threads", token))
    return {row["id"] for key in ("attention", "threads", "dms") for row in page.get(key, [])}


def search_ids(api, token, query):
    return {row["id"] for row in ok(call(api, "GET", "threads/search?q=" + query, token)).get("threads", [])}


def add_member(api, token, thread_id, actor):
    return call(api, "POST", f"threads/{thread_id}/members", token, {"actor": actor})


def remove_member(api, token, thread_id, actor):
    return call(api, "DELETE", f"threads/{thread_id}/members/{actor}", token)


def make_group(api, name, people=(), bots=()):
    """A team, made by an admin (Ben is one in the api fixture)."""
    return ok(call(api, "POST", "groups", "ben-test", {"name": name, "add": {"people": list(people), "bots": list(bots)}}))
