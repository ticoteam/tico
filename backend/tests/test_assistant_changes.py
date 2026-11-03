"""What the Assistant did for you: listed with the words it acted on, undone once and only once, and only by you."""

from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post  # noqa: F401


def assistant_closes(api, title="Old release notes", actor="human:ana"):
    """A task the person owns, closed by their Assistant the way a request from the person's own chat is recorded."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, actor, title, "x", actor)
        via, deleg = H.VIA.set("assistant"), H.DELEGATION.set({"for": actor, "cited": "", "run": ""})
        try:
            H.task_update(c, actor, task["id"], status="done")
        finally:
            H.VIA.reset(via)
            H.DELEGATION.reset(deleg)
    return task["id"]


def test_a_change_the_assistant_made_is_listed_and_can_be_undone_once(api):
    task_id = assistant_closes(api)
    listed = get(api, "assistant/changes?days=30")["changes"]
    mine = [c for c in listed if c["object"] == f"task:{task_id}"]

    assert mine and "done" in mine[0]["what"].lower() or mine and mine[0]["what"].startswith("Closed")
    assert len(mine[0]["words"]) <= 200
    assert mine[0]["undoable"] is True

    cid = mine[0]["id"]
    first = post(api, f"assistant/changes/{cid}/restore", {})
    assert first["id"] == cid and first["restored"] is True and first["already"] is False
    with api.app.state.store.read() as c:
        assert H.task(c, task_id)["status"] != "done"

    again = post(api, f"assistant/changes/{cid}/restore", {})
    assert again.get("already") is True


def test_someone_elses_change_is_not_found(api):
    task_id = assistant_closes(api, title="Ana's own")
    cid = [c for c in get(api, "assistant/changes?days=30")["changes"] if c["object"] == f"task:{task_id}"][0]["id"]
    r = api.post(f"/api/v2/assistant/changes/{cid}/restore", json={}, headers=headers("ben-test"))
    assert r.status_code == 404


def test_the_list_pages_and_stays_small(api):
    for n in range(3):
        assistant_closes(api, title=f"Stale item {n}")
    page = get(api, "assistant/changes?days=30&limit=2")
    assert len(page["changes"]) <= 2
    assert all(len(c["what"]) <= 200 and len(c["words"]) <= 200 for c in page["changes"])
    if page["next"]:
        rest = get(api, "assistant/changes?days=30&limit=2&before=" + page["next"])
        assert {c["id"] for c in rest["changes"]}.isdisjoint({c["id"] for c in page["changes"]})
