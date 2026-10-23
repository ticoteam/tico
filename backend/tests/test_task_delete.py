"""Deleting tasks offline removes them and their conversations, and refuses a task carrying work."""


from backend.task_delete import delete_tasks
from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_tasks_board import api, bot_token, get, headers, post  # noqa: F401  (the fixture)


def test_delete_removes_imported_tickets_and_refuses_one_with_work_outside_the_list(api):
    store = api.app.state.store
    ticket = post(api, "tasks", {"owner": "ben", "title": "Fix the account page", "body": "Imported."})
    post(api, f"tasks/{ticket['id']}/links", {"url": "https://example.com/c/18945"})
    post(api, f"tasks/{ticket['id']}/comments", {"text": "Copied from the old board."})
    parent = post(api, "tasks", {"owner": "ben", "title": "Plan the launch", "body": "x"})
    post(api, "tasks", {"owner": "priya", "title": "Write the post", "body": "x", "relations": [{"task": parent["id"], "kind": "parent"}]})

    with store.transaction() as c:
        conversation = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (ticket["id"],)).fetchone()[0]
        dry = delete_tasks(c, [ticket["id"]])
        assert dry["applied"] is False and dry["tasks"] == 1 and dry["messages"] >= 1 and not dry["refused"]
        refused = delete_tasks(c, [parent["id"]], apply=True)
        assert refused["refused"] == {"subtasks outside the list": 1} and refused["applied"] is False
        assert delete_tasks(c, ["no-such-task"], apply=True)["missing"] == ["no-such-task"]
        assert delete_tasks(c, [ticket["id"]], apply=True)["applied"] is True

    with store.read() as c:
        for table, column, value in (("tasks", "id", ticket["id"]), ("task_links", "task_id", ticket["id"]),
                                     ("task_events", "task_id", ticket["id"]),
                                     ("conversations", "id", conversation),
                                     ("messages", "conversation_id", conversation)):
            assert c.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", (value,)).fetchone()[0] == 0, table
        assert c.execute("SELECT count(*) FROM events WHERE action='task.deleted' AND target=?",
                         (ticket["id"],)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM tasks WHERE id=?", (parent["id"],)).fetchone()[0] == 1
    get(api, "tasks/" + ticket["id"], expected=404)


def test_a_person_deletes_a_task_they_asked_for_and_nobody_else_can(api):
    mine = post(api, "tasks", {"owner": "ben", "title": "Fix the duplicate page", "body": "x"}, token="priya-test")
    theirs = post(api, "tasks", {"owner": "ben", "title": "Fix the pricing page", "body": "x"}, token="ben-test")
    parent = post(api, "tasks", {"owner": "ben", "title": "Plan the launch", "body": "x"}, token="priya-test")
    post(api, "tasks", {"owner": "ben", "title": "Write the post", "body": "x", "relations": [{"task": parent["id"], "kind": "parent"}]}, token="priya-test")

    refused = post(api, f"tasks/{theirs['id']}/delete", {}, token="priya-test", expected=403)
    assert refused["error"]["code"] == "forbidden"
    assert post(api, f"tasks/{parent['id']}/delete", {}, token="priya-test", expected=409)["error"]["code"] == "has_work"
    assert post(api, f"tasks/{mine['id']}/delete", {}, token="priya-test") == {"deleted": mine["id"]}
    get(api, "tasks/" + mine["id"], expected=404)
    # A mover may delete anyone's task; a bot never deletes, even its own.
    assert post(api, f"tasks/{theirs['id']}/delete", {})["deleted"] == theirs["id"]
    bots = post(api, "tasks", {"owner": "ops", "title": "Draft the newsletter", "body": "x"})
    r = api.post(f"/api/v2/tasks/{bots['id']}/delete", json={}, headers=headers(bot_token(api)))
    assert r.status_code == 403


def test_a_deleted_task_comes_back_whole_and_keeps_its_number(api):
    from backend.task_delete import purge_trash, restore_tasks
    store = api.app.state.store
    ticket = post(api, "tasks", {"owner": "priya", "title": "Fix the signup page", "body": "Imported."}, token="ben-test")
    post(api, f"tasks/{ticket['id']}/links", {"url": "https://example.com/c/77"}, token="ben-test")
    post(api, f"tasks/{ticket['id']}/comments", {"text": "Copied from the old board."}, token="ben-test")
    before = get(api, "tasks/" + ticket["id"], token="ben-test")
    with store.transaction() as c:
        c.execute("UPDATE tasks SET number=41 WHERE id=?", (ticket["id"],))

    assert post(api, f"tasks/{ticket['id']}/delete", {}, token="ben-test") == {"deleted": ticket["id"]}
    get(api, "tasks/" + ticket["id"], token="ben-test", expected=404)
    listed = get(api, "deleted-tasks", token="ben-test")["tasks"]
    assert [t["id"] for t in listed] == [ticket["id"]] and listed[0]["number"] == 41
    assert get(api, "deleted-tasks", token="priya-test")["tasks"] == []       # neither deleted nor asked for it
    with store.read() as c:                                                 # the trash holds its number
        assert c.execute("SELECT COALESCE(MAX(number),0) FROM tasks").fetchone()[0] < 41
    # Someone who neither deleted nor asked for it cannot restore it; a bot never can.
    assert post(api, f"tasks/{ticket['id']}/restore", {}, token="priya-test", expected=404)["error"]["code"] == "not_found"
    r = api.post(f"/api/v2/tasks/{ticket['id']}/restore", json={}, headers=headers(bot_token(api)))
    assert r.status_code == 403

    assert post(api, "tasks/41/restore", {}, token="ben-test")["restored"] == ticket["id"]
    after = get(api, "tasks/" + ticket["id"], token="ben-test")
    for key in ("title", "body", "requester", "owner", "status", "conversation_id"):
        assert after["task"][key] == before["task"][key] if "task" in before else after[key] == before[key], key
    with store.read() as c:
        assert c.execute("SELECT number FROM tasks WHERE id=?", (ticket["id"],)).fetchone()[0] == 41
        assert c.execute("SELECT count(*) FROM task_links WHERE task_id=?", (ticket["id"],)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id "
                         "WHERE t.id=? AND m.body='Copied from the old board.'", (ticket["id"],)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM events WHERE action='task.restored' AND target=?",
                         (ticket["id"],)).fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM task_trash").fetchone()[0] == 0

    # Purging lists unless applied, and only what is older than the cutoff.
    post(api, f"tasks/{ticket['id']}/delete", {}, token="ben-test")
    with store.transaction() as c:
        assert purge_trash(c, 30, apply=True)["tasks"] == 0
        c.execute("UPDATE task_trash SET deleted_at='2020-01-01T00:00:00.000000Z'")
        assert purge_trash(c, 30)["applied"] is False
        purged = purge_trash(c, 30, apply=True)
        assert purged["tasks"] == 1 and purged["applied"] is True
        assert c.execute("SELECT count(*) FROM events WHERE action='task.purged'").fetchone()[0] == 1
        assert restore_tasks(c, [ticket["id"]])["missing"] == [ticket["id"]]


def test_a_task_with_attached_files_deletes_hides_them_and_restores_them(api):
    api.app.state.store.settings.assistant_bot = "ops"      # a person's upload is filed under the assistant
    ticket = post(api, "tasks", {"owner": "priya", "title": "Try the coworkers flow", "body": "x"}, token="ben-test")
    attached = post(api, f"tasks/{ticket['id']}/files", {"name": "report.md", "text": "# Report"}, token="ben-test")
    url = f"/api/v2/files/{attached['file_id']}"
    assert api.get(url, headers=headers("priya-test")).content == b"# Report"
    assert post(api, f"tasks/{ticket['id']}/delete", {}, token="ben-test") == {"deleted": ticket["id"]}
    # While its task is in the trash nobody may open the file, not even who attached it.
    for token in ("ben-test", "priya-test", "ana-test"):
        assert api.get(url, headers=headers(token)).status_code == 404
    post(api, f"tasks/{ticket['id']}/restore", {}, token="ben-test")
    assert api.get(url, headers=headers("priya-test")).content == b"# Report"
    assert [f["name"] for f in get(api, f"tasks/{ticket['id']}/files", token="ben-test")["files"]] == ["report.md"]


def test_purging_a_deleted_task_reports_the_files_it_leaves_behind(api):
    import json
    from backend.task_delete import purge_trash
    store = api.app.state.store
    store.settings.assistant_bot = "ops"
    wrong = post(api, "tasks", {"owner": "priya", "title": "Send the contract", "body": "x"}, token="ben-test")
    attached = post(api, f"tasks/{wrong['id']}/files", {"name": "contract.md", "text": "# Wrong one"}, token="ben-test")
    post(api, f"tasks/{wrong['id']}/delete", {}, token="ben-test")
    with store.transaction() as c:
        c.execute("UPDATE task_trash SET deleted_at='2020-01-01T00:00:00.000000Z'")
        listed = purge_trash(c, 30)
        assert listed["files_left"] == {"files": 1, "versions": 1, "bytes": len(b"# Wrong one"),
                                        "ids": [attached["file_id"]], "attachments": []}
        assert purge_trash(c, 30, apply=True)["files_left"] == listed["files_left"]
        detail = c.execute("SELECT detail_json FROM events WHERE action='task.purged' AND target=?",
                           (wrong["id"],)).fetchone()[0]
        assert json.loads(detail)["files_left"] == [attached["file_id"]]
    assert api.get(f"/api/v2/files/{attached['file_id']}", headers=headers("ben-test")).status_code == 404


def test_a_file_attached_when_the_task_was_made_is_locked_in_the_trash_and_counted_by_the_purge(api):
    from backend.task_delete import purge_trash
    store = api.app.state.store
    made = api.post("/api/v2/uploads/tasks", data={"owner": "priya", "title": "Sign the contract", "body": "x"},
                    files={"files": ("contract.pdf", b"%PDF wrong one", "application/pdf")}, headers=headers("ben-test"))
    assert made.status_code == 200, made.text
    task, attachment = made.json()["task"], made.json()["task"]["attachments"][0]
    for token in ("ben-test", "priya-test"):
        assert api.get(attachment["url"], headers=headers(token)).content == b"%PDF wrong one"
    post(api, f"tasks/{task['id']}/delete", {}, token="ben-test")
    with store.transaction() as c:          # a task deleted before the table existed is found in its trash row
        from backend.task_delete import ensure
        c.execute("DELETE FROM task_trash_assets")
        ensure(c)
        assert [r[0] for r in c.execute("SELECT blob_id FROM task_trash_assets")] == [attachment["id"]]
    # Not even its uploader opens it while the task is in the trash; a restore gives it back.
    for token in ("ben-test", "priya-test"):
        assert api.get(attachment["url"], headers=headers(token)).status_code in (403, 404)
    post(api, f"tasks/{task['id']}/restore", {}, token="ben-test")
    assert api.get(attachment["url"], headers=headers("priya-test")).content == b"%PDF wrong one"
    post(api, f"tasks/{task['id']}/delete", {}, token="ben-test")
    with store.transaction() as c:
        c.execute("UPDATE task_trash SET deleted_at='2020-01-01T00:00:00.000000Z'")
        left = purge_trash(c, 30, apply=True)["files_left"]
    assert left == {"files": 1, "versions": 0, "bytes": len(b"%PDF wrong one"), "ids": [],
                    "attachments": [attachment["id"]]}
    assert api.get(attachment["url"], headers=headers("ben-test")).status_code in (403, 404)


def test_a_task_file_a_bot_made_refuses_the_delete(api):
    # The bot's turn is on a chat, not this task: its file alone is the work.
    report = post(api, "tasks", {"owner": "priya", "title": "Draft the report", "body": "x", "private": False}, token="ben-test")
    made = api.post("/api/v2/files/uploads", json={"name": "draft.md", "text": "# Draft", "task": report["id"]},
                    headers=headers(bot_token(api)))
    assert made.status_code == 200, made.text
    r = api.post(f"/api/v2/tasks/{report['id']}/delete", json={}, headers=headers("ben-test"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "has_work"


def test_a_bulk_delete_and_restore_keeps_links_inside_the_list(api):
    from backend.task_delete import restore_tasks
    store = api.app.state.store
    parent = post(api, "tasks", {"owner": "ben", "title": "Plan the import", "body": "x"})
    child = post(api, "tasks", {"owner": "ben", "title": "Copy the cards", "body": "x", "relations": [{"task": parent["id"], "kind": "parent"}]})
    with store.transaction() as c:
        assert delete_tasks(c, [parent["id"], child["id"]], apply=True)["applied"] is True
        assert c.execute("SELECT count(*) FROM tasks WHERE id IN (?,?)", (parent["id"], child["id"])).fetchone()[0] == 0
        assert restore_tasks(c, [parent["id"], child["id"]])["unlinked"] == {}
        assert TR.parent_of(c, child["id"]) == parent["id"]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        # Restoring only the subtask leaves its link to the still-deleted parent unset, and says so.
        assert delete_tasks(c, [parent["id"], child["id"]], apply=True)["applied"] is True
        assert restore_tasks(c, [child["id"]])["unlinked"] == {child["id"]: ["parent"]}
        assert TR.parent_of(c, child["id"]) is None
        # Restoring the parent later files the subtask under it again.
        assert restore_tasks(c, [parent["id"]])["unlinked"] == {}
        assert TR.parent_of(c, child["id"]) == parent["id"]


def test_a_private_deleted_task_stays_hidden_from_who_could_not_open_it(api):
    ticket = post(api, "tasks", {"owner": "priya", "title": "Secret salary review", "body": "x", "private": True},
                  token="ben-test")
    get(api, "tasks/" + ticket["id"], token="ana-test", expected=404)
    post(api, f"tasks/{ticket['id']}/delete", {}, token="ben-test")
    assert "Secret salary review" not in [t["title"] for t in get(api, "deleted-tasks", token="ana-test")["tasks"]]
    assert post(api, f"tasks/{ticket['id']}/restore", {}, token="ana-test", expected=404)["error"]["code"] == "not_found"
    assert [t["title"] for t in get(api, "deleted-tasks", token="ben-test")["tasks"]] == ["Secret salary review"]


def test_a_purged_tasks_number_is_never_given_out_again(api):
    from backend.task_delete import purge_trash
    store = api.app.state.store
    first = post(api, "tasks", {"owner": "ben", "title": "Fix the about page", "body": "x"}, token="priya-test")
    second = post(api, "tasks", {"owner": "ben", "title": "Fix the terms page", "body": "x"}, token="priya-test")
    with store.transaction() as c:
        c.execute("INSERT INTO task_types(id,name,created,updated,numbered) VALUES('t2','Bug',?,?,1)", (H.now(), H.now()))
        c.execute("UPDATE tasks SET type_id='t2', number=90 WHERE id=?", (first["id"],))
        c.execute("UPDATE tasks SET type_id='t2' WHERE id=?", (second["id"],))
    post(api, f"tasks/{first['id']}/delete", {}, token="priya-test")
    with store.transaction() as c:
        c.execute("UPDATE task_trash SET deleted_at='2020-01-01T00:00:00.000000Z'")
        assert purge_trash(c, 30, apply=True)["applied"] is True
        H._next_number(c, "human:priya", second["id"], "t2")
        assert c.execute("SELECT number FROM tasks WHERE id=?", (second["id"],)).fetchone()[0] == 91
    assert get(api, "deleted-tasks", token="priya-test")["tasks"] == []


def test_a_milestone_wait_takes_the_blocker_gate_and_comes_back_with_a_restored_task(api):
    from backend.task_delete import restore_tasks
    task = post(api, "tasks", {"owner": "ben", "title": "Cut v1.3.0", "body": "x"}, token="priya-test")
    # A requester outside the mover teams may not park someone else's task, by a blocker or by a milestone alike.
    refused = post(api, f"tasks/{task['id']}/waits", {"on": "release:acme/tico@v1.2.0"}, token="priya-test",
                   expected=403)
    assert refused["error"]["code"] == "identity"
    post(api, f"tasks/{task['id']}/waits", {"on": "release:acme/tico@v1.2.0"}, token="ben-test")
    store = api.app.state.store
    with store.transaction() as c:
        assert delete_tasks(c, [task["id"]], apply=True)["applied"] is True
        assert c.execute("SELECT count(*) FROM task_milestone_waits WHERE task_id=?", (task["id"],)).fetchone()[0] == 0
        restore_tasks(c, [task["id"]])
        assert [r[0] for r in c.execute("SELECT target FROM task_milestone_waits WHERE task_id=?",
                                        (task["id"],))] == ["acme/tico@v1.2.0"]
