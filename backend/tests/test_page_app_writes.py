"""What a page app writes (backend/page_app_writes.py, docs/pages.md#apps): only what its version declares; its own data
in the collections it opens, as the viewer; Tico writes only after the viewer's own tap on a sheet Tico draws, once, as
the viewer, while the version is still current."""

from backend import page_apps, visuals
from backend.tests.helpers.api import headers, post
from backend.tests.helpers.files import turn
from backend.tests.helpers.pages import HTML, page

WRITES = "reads=tasks writes=comment,move,store:notes"


def app_page(api, app=WRITES, data=HTML):
    """An app page on a task its writer (ops) works and the viewer (ben) reads: (attempt, page, task id)."""
    tid = post(api, "tasks", {"title": "Refresh the summer ads", "owner": "ops"})["id"]
    _, attempt = turn(api)
    made = page(api, attempt, app=app, data=data, task=tid)
    assert made.status_code == 200, made.text
    return attempt, made.json()["page"], tid


def call(api, fid, version, op, args, token="ben-test", actor="human:ben", expected=200):
    key = visuals.key_for(api.app, api.app.state.store.settings)
    body = {"version": version, "nonce": page_apps.nonce(key, actor, fid, version), "op": op, "args": args}
    r = api.post(f"/api/v2/pages/{fid}/app", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def comments(api, tid):
    found = api.get(f"/api/v2/tasks/{tid}/comments", headers=headers("ben-test")).json()["comments"]
    return [(m["sender_id"], m["body"]) for m in found]


def test_writes_are_declared_with_the_version_and_a_change_of_them_is_a_new_version(api):
    assert page_apps.parse("reads=tasks writes=store:notes,comment;writes=move") == (
        '{"reads":["tasks"],"writes":["comment","move","store:notes"]}'
    )
    assert page_apps.parse("reads=tasks") == '{"reads":["tasks"]}'  # a reads-only app keeps its stored form
    for bad in ("writes=delete", "writes=approve", "writes=store:Bad!", "writes=email"):
        try:
            page_apps.parse(bad)
            raise AssertionError(bad)
        except page_apps.Problem as exc:
            assert exc.status == 422
    attempt, first, tid = app_page(api)
    assert first["app"] == {"reads": ["tasks"], "writes": ["comment", "move", "store:notes"]}
    again = page(api, attempt, app="reads=tasks writes=comment,store:notes", task=tid).json()["page"]
    assert again["version"] == first["version"] + 1  # same bytes, other writes: its own version and approval


def test_viewers_write_the_page_data_the_version_opens_and_each_row_names_who_wrote_it(api):
    _, made, _ = app_page(api)
    fid, v = made["file_id"], made["version"]
    row = call(api, fid, v, "store.set", {"collection": "notes", "id": "n1", "data": {"text": "Tired creative"}})
    assert row["result"]["row"]["updated_by"] == "human:ben"
    stored = api.get(f"/api/v2/pages/{fid}/data/notes/n1", headers=headers("ben-test")).json()["row"]
    assert stored["data"] == {"text": "Tired creative"} and stored["updated_by"] == "human:ben"
    theirs = call(api, fid, v, "store.set", {"collection": "notes", "id": "n2", "data": 1}, "cara-test", "human:cara")
    assert theirs["result"]["row"]["updated_by"] == "human:cara"
    # A collection the version did not open, and another viewer's frame key, write nothing.
    refused = call(api, fid, v, "store.set", {"collection": "votes", "id": "x", "data": 1}, expected=403)
    assert refused["error"]["code"] == "not_declared"
    call(api, fid, v, "store.set", {"collection": "notes", "id": "n3", "data": 1}, "cara-test", "human:ben", 403)
    assert call(api, fid, v, "store.rm", {"collection": "notes", "id": "n1"})["result"] == {"removed": True}


def test_an_act_writes_nothing_until_the_viewer_taps_then_once_as_the_viewer(api):
    _, made, tid = app_page(api)
    fid, v = made["file_id"], made["version"]
    act = {"actions": [{"kind": "comment", "payload": {"task": tid, "text": "Angle B is tired"}}]}
    held = call(api, fid, v, "act", act)["result"]
    # The sheet's line is Tico's own name for the task, with exactly the text that will be posted.
    (line,) = held["lines"]
    assert line["kind"] == "comment" and line["detail"] == "Angle B is tired"
    assert line["label"].startswith("Comment on #") and line["label"].endswith(" Refresh the summer ads")
    assert ("human:ben", "Angle B is tired") not in comments(api, tid)
    # Someone else's tap, and a cancel, run nothing; the cancelled key is spent.
    other = api.post(f"/api/v2/pages/{fid}/app/act", json={"key": held["key"], "confirm": True}, headers=headers())
    assert other.status_code == 409
    done = api.post(
        f"/api/v2/pages/{fid}/app/act", json={"key": held["key"], "confirm": True}, headers=headers("ben-test")
    )
    assert done.json() == {"status": "done", "items": [{"ok": True, "error": None}]}, done.text
    assert ("human:ben", "Angle B is tired") in comments(api, tid)
    again = api.post(
        f"/api/v2/pages/{fid}/app/act", json={"key": held["key"], "confirm": True}, headers=headers("ben-test")
    )
    assert again.status_code == 409  # a key works once
    assert comments(api, tid).count(("human:ben", "Angle B is tired")) == 1


def test_only_declared_kinds_are_asked_and_a_new_version_cancels_a_waiting_sheet(api):
    attempt, made, tid = app_page(api)
    fid, v = made["file_id"], made["version"]
    undeclared = {"actions": [{"kind": "task", "payload": {"title": "Make more ads"}}]}
    assert call(api, fid, v, "act", undeclared, expected=403)["error"]["code"] == "not_declared"
    unknown = {"actions": [{"kind": "approve", "payload": {"task": tid}}]}
    assert call(api, fid, v, "act", unknown, expected=403)["error"]["code"] == "not_declared"
    act = {"actions": [{"kind": "comment", "payload": {"task": tid, "text": "Ship it"}}]}
    held = call(api, fid, v, "act", act)["result"]
    page(api, attempt, app=WRITES, data=HTML.replace(b"42", b"43"), task=tid)  # a new version is out
    late = tap(api, fid, held["key"])
    assert late.json() == {"status": "cancelled", "items": []}, late.text
    assert ("human:ben", "Ship it") not in comments(api, tid)


def tap(api, fid, key, who="ben-test"):
    return api.post(f"/api/v2/pages/{fid}/app/act", json={"key": key, "confirm": True}, headers=headers(who))


def test_a_move_never_closes_and_a_task_changed_after_the_sheet_is_not_moved_blind(api):
    _, made, tid = app_page(api)
    fid, v = made["file_id"], made["version"]
    with api.app.state.store.read() as c:
        stages = c.execute(
            "SELECT s.id, s.name, s.kind FROM stages s JOIN tasks t ON t.pipeline_id=s.pipeline_id WHERE t.id=?", (tid,)
        ).fetchall()
    closed = next(s for s in stages if s["kind"] == "closed")
    done = next(s for s in stages if s["kind"] == "done")
    close = {"actions": [{"kind": "move", "payload": {"task": tid, "to": closed["name"]}}]}
    assert call(api, fid, v, "act", close, expected=403)["error"]["code"] == "forbidden"
    move = {"actions": [{"kind": "move", "payload": {"task": tid, "to": done["name"].upper()}}]}
    held = call(api, fid, v, "act", move)["result"]
    assert held["lines"][0]["label"].endswith(" to " + done["name"])
    with api.app.state.store.transaction() as c:  # the task changes while the sheet waits
        c.execute("UPDATE tasks SET version=version+1 WHERE id=?", (tid,))
    late = tap(api, fid, held["key"])
    assert late.status_code == 409 and late.json()["error"]["code"] == "changed"


def test_a_rejected_ask_records_nothing(api):
    _, made, _ = app_page(api, app="reads=tasks writes=task")
    fid, v = made["file_id"], made["version"]
    with api.app.state.store.read() as c:
        before = c.execute("SELECT COUNT(*) FROM refusals").fetchone()[0]
    ask = {"actions": [{"kind": "task", "payload": {"title": "More ads", "pipeline": "No such pipeline"}}]}
    assert call(api, fid, v, "act", ask, expected=404)["error"]["code"] == "not_found"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM refusals").fetchone()[0] == before


def test_a_tag_is_named_as_tico_names_it_and_never_a_template(api):
    _, made, _ = app_page(api, app="reads=tasks writes=tag")
    fid, v = made["file_id"], made["version"]
    post(api, "tags", {"key": "client:acme", "label": "Acme"})
    post(api, "tags", {"key": "launch-kit", "is_template": True})
    asking = lambda name: {"actions": [{"kind": "tag", "payload": {"file": fid, "tag": name}}]}  # noqa: E731
    assert call(api, fid, v, "act", asking("client:nobody"), expected=404)["error"]["code"] == "not_found"
    assert call(api, fid, v, "act", asking("launch-kit"), expected=422)["error"]["code"] == "validation"
    held = call(api, fid, v, "act", asking("Client:Acme"))["result"]
    assert held["lines"][0]["label"].endswith(" with client:acme")
    done = tap(api, fid, held["key"])
    assert done.json()["status"] == "done", done.text
    tags = api.get(f"/api/v2/files/{fid}/info", headers=headers("ben-test")).json()["file"]["tags"]
    assert [t["key"] for t in tags] == ["client:acme"]
