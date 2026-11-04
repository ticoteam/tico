"""Task page links keep both resources' read rules and invalidate the task's Pages section."""

from backend import events as E, file_library
from backend.auth import Identity
from backend.tests.test_api import api, get, headers, post, restrict  # noqa: F401
from backend.tests.test_cards import delete, page as seeded_page, place, task
from backend.tests.test_files import publish, turn
from backend.tests.test_pages import page, read


def test_a_task_reader_links_a_page_without_sharing_it_with_other_task_readers(api):
    tid = task(api, "human:ben", "Review the plan")
    _, attempt = turn(api, who="cara-test")
    fid = page(api, attempt, title="Private plan").json()["page"]["file_id"]
    linked = post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid}, "cara-test")
    assert linked["pins"][0]["page"]["file_id"] == fid
    assert linked["pins"][0]["can_unpin"] is True
    assert [p["file_id"] for p in get(api, "pages?task=" + tid, "cara-test")["pages"]] == [fid]
    for who in ("ana-test", "ben-test"):
        assert place(api, "task:" + tid, who) == {
            "place": "task:" + tid, "kind": "task", "saved": True, "can_edit": True, "pins": []}
        assert get(api, "pages?task=" + tid, who) == {"pages": [], "has_more": False, "next_cursor": None}
        assert get(api, "files/search?q=Private&kind=page", who) == {"results": []}
        assert read(api, fid, who).status_code == 404
        get(api, "files/" + fid, who, expected=404)
        post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid}, who, expected=404)


def test_unpinning_is_for_the_linker_task_parties_or_org_owner(api):
    tid = task(api, "human:ana", "Review the page")
    fid = seeded_page(api, "ops")
    body = {"place": "task:" + tid, "item": "page:" + fid}
    linked = post(api, "pins", body, "cara-test")
    assert place(api, body["place"], "ben-test")["pins"][0]["can_unpin"] is False
    delete(api, "pins/" + linked["id"], "ben-test", expected=403)  # bot admins have no extra unpin right
    delete(api, "pins/" + linked["id"], "cara-test")
    linked = post(api, "pins", body, "cara-test")
    delete(api, "pins/" + linked["id"], "ana-test")

    tid = task(api, "human:ben", "Another review", requester_id="human:cara")
    body["place"] = "task:" + tid
    for who in ("ben-test", "cara-test", "ana-test"):
        linked = post(api, "pins", body)
        delete(api, "pins/" + linked["id"], who)
    assert place(api, body["place"])["pins"] == []


def test_task_read_access_is_required_for_linking_and_listing_pinned_pages(api):
    tid = task(api, "bot:ops", "Restricted work")
    fid = seeded_page(api, "cpo")
    post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid})
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid}, "cara-test", expected=404)
    get(api, "pages?task=" + tid, "cara-test", expected=404)
    place(api, "task:" + tid, "cara-test", expected=404)


def test_a_bot_can_unpin_its_page_after_handing_the_task_to_a_person(api):
    tid = task(api, "bot:ops", "Prepare the plan")
    _, attempt = turn(api)
    fid = seeded_page(api, "ops")
    linked = post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid}, attempt["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:ana' WHERE id=?", (tid,))
    delete(api, "pins/" + linked["id"], attempt["token"])


def test_task_page_listing_pages_over_filed_and_linked_pages_once(api):
    tid = task(api, "human:ben", "All project pages")
    _, attempt = turn(api)
    filed = page(api, attempt, name="filed.html", task=tid).json()["page"]["file_id"]
    wanted = {filed}
    for i in range(4):
        fid = page(api, attempt, name=f"linked-{i}.html").json()["page"]["file_id"]
        wanted.add(fid)
        post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid}, "ben-test")
    page(api, attempt, name="unlinked.html")
    found, cursor = [], ""
    while True:
        response = api.get("/api/v2/pages", params={"task": tid, "limit": 2, "cursor": cursor},
                           headers=headers("ben-test"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert "total" not in body and len(body["pages"]) <= 2
        found += [p["file_id"] for p in body["pages"]]
        if not body["has_more"]:
            break
        assert body["next_cursor"] and body["next_cursor"] != cursor
        cursor = body["next_cursor"]
    assert len(found) == len(set(found)) == 5 and set(found) == wanted


def test_page_search_filters_before_its_candidate_cap_and_supports_title_fallback(api, monkeypatch):
    _, attempt = turn(api)
    fid = page(api, attempt, title="Weekly plan").json()["page"]["file_id"]
    doc = publish(api, attempt, name="Weekly plan.md", text="Weekly plan").json()["file"]["id"]
    monkeypatch.setattr(file_library, "SCAN_MAX", 1)
    for fallback in (False, True):
        if fallback:
            monkeypatch.setattr(file_library, "has_fts", lambda c: False)
        assert [r["id"] for r in get(api, "files/search?q=Weekly&kind=page", "ben-test")["results"]] == [fid]
        assert [r["id"] for r in get(api, "files/search?q=Weekly&kind=document", "ben-test")["results"]] == [doc]
    get(api, "files/search?q=Weekly&kind=everything", "ben-test", expected=422)


def test_pin_insert_and_delete_invalidate_only_task_places_without_page_metadata(api):
    tid = task(api, "human:ben", "Read the linked page")
    fid = seeded_page(api, "inbox", "Private page title")
    store, auth = api.app.state.store, api.app.state.auth
    with store.read() as c:
        cursor = E.latest(c)
    pin = post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid})
    with store.read() as c:
        rows = c.execute("SELECT topic,kind,subject_id FROM changes WHERE seq>?", (cursor,)).fetchall()
        assert [tuple(r) for r in rows] == [("tasks", "files", tid)]
        events, cursor, _, _ = E.read(c, auth, Identity("human:cara", "human"), cursor, topics=("tasks",),
                                    task_views=lambda rows, *_: [{"id": row["id"]} for row in rows])
        assert len(events) == 1
        assert events[0][1] == "tasks" and events[0][2]["kind"] == "files"
        assert fid not in str(events) and "Private page title" not in str(events)
    delete(api, "pins/" + pin["id"])
    with store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT topic,kind,subject_id FROM changes WHERE seq>?", (cursor,))] == [
            ("tasks", "files", tid)]
        cursor = E.latest(c)
    pin = post(api, "pins", {"place": "person:me", "item": "page:" + fid})
    delete(api, "pins/" + pin["id"])
    with store.read() as c:
        assert not c.execute("SELECT 1 FROM changes WHERE seq>? AND topic='tasks'", (cursor,)).fetchone()
