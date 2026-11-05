"""The three sections of a meeting, and Push.

The task, doc and feature pushes go through the real hub task path, because that is the point of them.
"""

import pytest

from backend.store import H
from backend.tests.test_api import restrict
from backend.tests.test_bot_access import bot, bot_id
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
    body = {"version": next(i["version"] for rows in items(api, rid, token)["sections"].values() for i in rows if i["id"] == iid), **body}
    r = api.post(f"/api/meetings/{rid}/items/{iid}", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def push(api, rid, iid, body=None, token="ana-test", expected=200, key=None):
    body = {"version": next(i["version"] for rows in items(api, rid, token)["sections"].values() for i in rows if i["id"] == iid), **(body or {})}
    r = api.post(f"/api/meetings/{rid}/items/{iid}/push", json=body, headers=headers(token, key))
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
    assert items(api, rid, "cara-test")["can_send"] is True


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


def without_doc_bot(api):
    """A roster with no `doc-updater` bot, as on most teams."""
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM bot_config WHERE bot='doc-updater'")
        c.execute("DELETE FROM bot_settings WHERE bot_id IN (SELECT id FROM bots WHERE slug='doc-updater')")
        c.execute("DELETE FROM bots WHERE slug='doc-updater'")


def test_a_doc_update_on_a_team_with_no_doc_bot_is_left_for_a_person_to_choose(api):
    """Without a doc-updater bot the push asks for an owner and the item stays proposed; it never says "Unknown recipient"."""
    without_doc_bot(api)
    rid = record(api, {"title": "Docs call"})
    detail = {"document": "docs/pricing.md", "change": "Ship on Friday"}
    iid = add(api, rid, {"section": "doc", "text": "Pricing page says Friday", "detail": detail})["item"]["id"]
    refused = push(api, rid, iid, expected=422)
    assert refused["error"]["code"] == "owner" and "Unknown recipient" not in refused["error"]["detail"]
    assert items(api, rid)["sections"]["doc"][0]["status"] == "proposed"
    # A named owner the roster does not have is refused again, and nothing is created for it.
    change(api, rid, iid, {"detail": {**detail, "owner": "human:nobody"}})
    push(api, rid, iid, expected=422)
    change(api, rid, iid, {"detail": {**detail, "owner": H.KEEPER}})
    push(api, rid, iid, expected=422)
    change(api, rid, iid, {"detail": {**detail, "owner": "human:ben"}})
    pushed = push(api, rid, iid)
    task = get(api, "tasks/" + pushed["item"]["result_ref"])["task"]
    assert task["owner_id"] == "human:ben" and "Document: docs/pricing.md" in task["body"]


def test_a_doc_update_cannot_be_assigned_to_a_bot_the_sender_cannot_write(api):
    bot(api, "docs-worker")
    canonical = bot_id(api, "docs-worker")
    with api.app.state.store.transaction() as c:
        restrict(c, canonical, see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    rid = record(api, {"title": "Docs call"}, token="cara-test")
    iid = add(api, rid, {"section": "doc", "text": "Correct the guide",
                         "detail": {"owner": "bot:docs-worker"}}, token="cara-test")["item"]["id"]
    refused = push(api, rid, iid, token="cara-test", expected=403)
    assert refused["error"]["code"] == "forbidden"
    assert "Unknown recipient" not in refused["error"]["detail"]
    assert items(api, rid, token="cara-test")["sections"]["doc"][0]["status"] == "proposed"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before


def test_a_doc_update_goes_to_the_doc_bot_unless_the_push_names_someone_else(api):
    rid = record(api, {"title": "Docs call"})
    default = add(api, rid, {"section": "doc", "text": "Fix the pricing page"})["item"]["id"]
    named = add(api, rid, {"section": "doc", "text": "Fix the onboarding page", "detail": {"owner": "human:ben"}})["item"]["id"]
    owners = {iid: get(api, "tasks/" + push(api, rid, iid)["item"]["result_ref"])["task"]["owner_id"]
              for iid in (default, named)}
    assert owners == {default: "bot:doc-updater", named: "human:ben"}


@pytest.mark.parametrize("access", ["hidden", "see_only", "write"])
def test_doc_default_checks_the_canonical_bot_id_for_a_non_admin(api, access):
    """A renamed label must not bypass the stored bot's access configuration."""
    without_doc_bot(api)
    bot(api, "docs-worker")
    canonical = bot_id(api, "docs-worker")
    assert canonical != "doc-updater"
    with api.app.state.store.transaction() as c:
        H.rename_bot(c, canonical, "doc-updater")
        if access == "hidden":
            restrict(c, canonical, people=["ana"])
        elif access == "see_only":
            restrict(c, canonical, see={"everyone": True}, read={"everyone": True}, write={"people": ["ana"]})
        else:
            restrict(c, canonical, everyone=True)
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
    rid = record(api, {"title": "Cara's docs call"}, token="cara-test")
    iid = add(api, rid, {"section": "doc", "text": "Correct the guide"}, token="cara-test")["item"]["id"]
    if access == "write":
        pushed = push(api, rid, iid, token="cara-test")
        task = get(api, "tasks/" + pushed["item"]["result_ref"], token="cara-test")["task"]
        assert task["owner_id"] == "bot:" + canonical
        # Naming the same bot by its label uses the same canonical identity.
        named = add(api, rid, {"section": "doc", "text": "Correct the FAQ",
                              "detail": {"owner": "bot:doc-updater"}}, token="cara-test")["item"]["id"]
        pushed = push(api, rid, named, token="cara-test")
        assert get(api, "tasks/" + pushed["item"]["result_ref"], token="cara-test")["task"]["owner_id"] == "bot:" + canonical
    else:
        refused = push(api, rid, iid, token="cara-test", expected=422)
        assert refused["error"]["code"] == "owner"
        assert "Unknown recipient" not in refused["error"]["detail"]
        item = items(api, rid, token="cara-test")["sections"]["doc"][0]
        assert item["status"] == "proposed" and item["result_ref"] is None
        with api.app.state.store.read() as c:
            assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before
        change(api, rid, iid, {"detail": {"owner": "human:cara"}}, token="cara-test")
        pushed = push(api, rid, iid, token="cara-test")
        assert get(api, "tasks/" + pushed["item"]["result_ref"], token="cara-test")["task"]["owner_id"] == "human:cara"
