"""The three sections of a meeting, and Push.

The task, doc and feature pushes go through the real hub task path, because that is the point of them.
"""

from backend.tests.test_api import api, get, headers   # noqa: F401


def record(api, body=None, token="ana-test"):
    """A finished meeting of the caller's, filed through the import API."""
    body = body or {}
    calendar = body.pop("calendar", None)
    r = api.post("/api/v2/meetings/import",
                 json={"title": "Pricing call", "transcript": "Dana: We agreed to ship on Friday.", "review": "live",
                       **({"participants": calendar["attendees"]} if calendar else {}), **body},
                 headers=headers(token))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def items(api, rid, token="ana-test", expected=200):
    r = api.get(f"/api/meetings/{rid}/items", headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def add(api, rid, body, token="ana-test", expected=200, key=None):
    r = api.post(f"/api/meetings/{rid}/items", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def change(api, rid, iid, body, token="ana-test", expected=200, key=None):
    r = api.post(f"/api/meetings/{rid}/items/{iid}", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def push(api, rid, iid, body=None, token="ana-test", expected=200, key=None):
    r = api.post(f"/api/meetings/{rid}/items/{iid}/push", json=body or {}, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def test_a_private_meeting_keeps_its_items_to_the_people_the_invite_names(api):
    """Items inherit the room's visibility; `backend/media.authorized` is the only gate."""
    rid = record(api, {"title": "Comp review", "private": True,
                       "calendar": {"event_id": "e1", "title": "Comp review",
                                    "attendees": ["Ana@Acme.example"]}}, "ben-test")
    add(api, rid, {"section": "doc", "text": "Write down the band for a senior"}, "ben-test")
    assert len(items(api, rid, "ana-test")["sections"]["doc"]) == 1         # named, and the owner
    items(api, rid, "cara-test", expected=403)                             # not on the invite
    add(api, rid, {"section": "task", "text": "Not mine to add"}, "cara-test", expected=403)
    # Making it a company meeting again opens the items with it.
    api.post(f"/api/meetings/{rid}/edit", json={"private": False, "version": 1},
             headers=headers("ben-test"))
    assert items(api, rid, "cara-test")["can_push"] is False


# ----------------------------------------------------------------------------- comments


def test_a_pushed_item_is_a_hub_task_with_the_meeting_it_came_from(api):
    rid = record(api, {"title": "Weekly sync"})
    iid = add(api, rid, {"section": "task", "text": "Send Dana the pricing sheet", "quote": "We agreed to ship on Friday.",
                         "detail": {"owner": "human:ben", "priority": "p1"}})["item"]["id"]
    pushed = push(api, rid, iid)
    assert pushed["pushed"] and pushed["item"]["status"] == "pushed"
    task = get(api, "tasks/" + pushed["item"]["result_ref"])["task"]
    assert task["owner_id"] == "human:ben" and task["title"] == "Send Dana the pricing sheet"
    assert "From meeting: Weekly sync" in task["body"] and "> We agreed to ship on Friday." in task["body"]
    assert f"#/meetings?meeting={rid}" in task["body"]
    # A second push is one result, not a second task.
    assert push(api, rid, iid)["pushed"] is False
    # A task with no owner is refused rather than guessed.
    bare = add(api, rid, {"section": "task", "text": "Someone should follow up"})["item"]["id"]
    push(api, rid, bare, expected=422)


def test_the_v2_aliases_answer_what_the_old_paths_do(api):
    """The runner hostname only routes /api/v2, so Tico Recorder's Send calls these; each answers as its old path does."""
    rid = record(api, {"title": "Old and new paths"})
    old_edit = api.post(f"/api/meetings/{rid}/edit", json={"title": "Via v1", "version": 1}, headers=headers())
    new_edit = api.post(f"/api/v2/meetings/{rid}/edit", json={"title": "Via v2", "version": 2}, headers=headers())
    assert old_edit.status_code == new_edit.status_code == 200, new_edit.text
    assert old_edit.json()["title"] == "Via v1" and new_edit.json()["title"] == "Via v2"

    detail = {"owner": "human:ben", "priority": "p1"}
    old_item = add(api, rid, {"section": "task", "text": "Send the pricing sheet", "detail": detail})["item"]
    created = api.post(f"/api/v2/meetings/{rid}/items", json={"section": "task", "text": "Send the pricing deck",
                                                               "detail": detail}, headers=headers())
    assert created.status_code == 200, created.text
    new_item = created.json()["item"]
    shape = lambda item: {k: item[k] for k in ("section", "status")}
    assert shape(new_item) == shape(old_item)

    old_push = push(api, rid, old_item["id"])
    new_push = api.post(f"/api/v2/meetings/{rid}/items/{new_item['id']}/push", json={}, headers=headers())
    assert new_push.status_code == 200, new_push.text
    assert old_push["pushed"] is True and new_push.json()["pushed"] is True
    titles = [get(api, "tasks/" + result["item"]["result_ref"])["task"]["title"] for result in (old_push, new_push.json())]
    assert titles == ["Send the pricing sheet", "Send the pricing deck"]
