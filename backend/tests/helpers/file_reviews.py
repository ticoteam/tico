"""Helpers that other tests share, moved out of test_file_reviews.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import get, headers, post


def publish(api, token, text, name="cut.md", path=None):
    r = api.post(
        "/api/v2/files/uploads",
        json={"name": name, "text": text, **({"path": path} if path else {})},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    return r.json()["file"]


def ask(api, token, review, on="ana", parent=None, question="Is this cut ready?", expected=200):
    """A bot's bound OK of one file version, on the task the work is on (POST /api/v2/tasks/{id}/oks, subject
    file:<id>): the version is pinned when it asks. Returns {task, requirement_id}."""
    [item] = review
    with api.app.state.store.read() as c:
        version = H.task(c, parent)["version"]
    r = api.post(
        f"/api/v2/tasks/{parent}/oks",
        json={
            "to": on,
            "line": question,
            "subject": f"file:{item['file']}",
            "content": {},
            "show": [f"file:{item['file']}"],
            "version": version,
        },
        headers=headers(token),
    )
    assert r.status_code == expected, r.text
    return {"task": {"id": parent}, "requirement_id": r.json().get("requirement_id")}


def answer(api, made, decision, token="ana-test", note="Not this one.", expected=200):
    """The person's own answer: "Approve" is the OK from its preview (GET /api/v2/oks/{rid}, then the OK route); any
    other is a send back with a note."""
    rid, tid = made["requirement_id"], made["task"]["id"]
    if decision == "Approve":
        shown = get(api, f"oks/{rid}", token=token)["shown_hash"]
        return post(api, f"tasks/{tid}/asks/{rid}/ok", {"shown_hash": shown}, token, expected=expected)
    return post(api, f"tasks/{tid}/asks/{rid}/answer", {"value": "no", "note": note}, token, expected=expected)


def approved(api, token, **query):
    r = api.get("/api/v2/file-approvals", params=query, headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()
