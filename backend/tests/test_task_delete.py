"""Deleting tasks offline removes them and their conversations, and refuses a task carrying work."""

import json

from backend.task_delete import delete_tasks
from backend import hubdb as H
from backend import task_relations as TR
from backend.store import encode
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


def test_a_new_numbered_task_never_takes_a_deleted_tasks_number(api):
    store = api.app.state.store
    from backend import hubdb as H
    first = post(api, "tasks", {"owner": "ben", "title": "Fix the login page", "body": "x"}, token="priya-test")
    second = post(api, "tasks", {"owner": "ben", "title": "Fix the logout page", "body": "x"}, token="priya-test")
    with store.transaction() as c:
        c.execute("INSERT INTO task_types(id,name,created,updated,numbered) VALUES('t1','Ticket',?,?,1)", (H.now(), H.now()))
        c.execute("UPDATE tasks SET type_id='t1', number=7 WHERE id=?", (first["id"],))
        c.execute("UPDATE tasks SET type_id='t1' WHERE id=?", (second["id"],))
    post(api, f"tasks/{first['id']}/delete", {}, token="priya-test")
    with store.transaction() as c:
        H._next_number(c, "human:priya", second["id"], "t1")
        assert c.execute("SELECT number FROM tasks WHERE id=?", (second["id"],)).fetchone()[0] == 8


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


def test_relations_go_to_the_trash_from_either_end_and_come_back(api):
    from backend.task_delete import restore_tasks
    store = api.app.state.store
    one = post(api, "tasks", {"owner": "ben", "title": "Fix the card import", "body": "x"})
    blocker = post(api, "tasks", {"owner": "ben", "title": "Fix the card export", "body": "x"})
    twin = post(api, "tasks", {"owner": "ben", "title": "Fix the card import too", "body": "x",
                               "relations": [{"task": one["id"], "kind": "duplicate_of"}]})
    post(api, "tasks/" + one["id"] + "/relations", {"task": blocker["id"], "kind": "blocked_by"})
    with store.transaction() as c:
        before = {tuple(r) for r in c.execute("SELECT from_task,to_task,kind FROM task_relations")}
        assert len(before) == 2
        assert delete_tasks(c, [one["id"]], apply=True)["applied"] is True
        assert c.execute("SELECT count(*) FROM task_relations").fetchone()[0] == 0
        assert restore_tasks(c, [one["id"]])["unlinked"] == {}
        assert {tuple(r) for r in c.execute("SELECT from_task,to_task,kind FROM task_relations")} == before
        # A trash entry written before task_relations kept the blocker on the task row: it still relinks.
        assert delete_tasks(c, [one["id"]], apply=True)["applied"] is True
        snap = json.loads(c.execute("SELECT rows_json FROM task_trash WHERE task_id=?", (one["id"],)).fetchone()[0])
        snap["rows"].pop("task_relations")
        snap["rows"]["tasks"][0]["blocked_by"] = blocker["id"]
        c.execute("UPDATE task_trash SET rows_json=? WHERE task_id=?", (json.dumps(snap), one["id"]))
        assert restore_tasks(c, [one["id"]])["unlinked"] == {}
        assert TR.blocker_ids(c, one["id"]) == [blocker["id"]]
        assert TR.parent_of(c, one["id"]) is None and twin["id"] not in TR.blocker_ids(c, one["id"])
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []



def test_deleting_a_bots_task_leaves_the_room_it_shares_and_restores_into_a_new_one(api):
    store = api.app.state.store
    with store.transaction() as c:     # an external harness: messages to it queue no job, so nothing refuses
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'",
                  (encode({"name": "ops", "runtime": "fake", "status": "active", "harness": "hermes"}),))
    other = post(api, "tasks", {"owner": "ops", "title": "Plan the offsite", "body": "x"}, token="ben-test")
    assert api.post("/api/v2/chat/ops", json={"text": "Unrelated chat in the room"}, headers=headers("ben-test")).status_code == 200
    ticket = post(api, "tasks", {"owner": "ops", "title": "Draft the newsletter", "body": "x"}, token="ben-test")
    with store.read() as c:
        room = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (ticket["id"],)).fetchone()[0]
        before = c.execute("SELECT count(*) FROM messages WHERE conversation_id=?", (room,)).fetchone()[0]
    post(api, f"tasks/{ticket['id']}/delete", {}, token="ben-test")
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE conversation_id=?", (room,)).fetchone()[0] == before
    get(api, "tasks/" + other["id"], token="ben-test")
    assert api.post("/api/v2/chat/ops", json={"text": "hello again"}, headers=headers("ben-test")).status_code == 200
    assert post(api, f"tasks/{ticket['id']}/restore", {}, token="ben-test")["restored"] == ticket["id"]
    with store.read() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_private_deleted_task_stays_hidden_from_who_could_not_open_it(api):
    ticket = post(api, "tasks", {"owner": "priya", "title": "Secret salary review", "body": "x", "private": True},
                  token="ben-test")
    get(api, "tasks/" + ticket["id"], token="ana-test", expected=404)
    post(api, f"tasks/{ticket['id']}/delete", {}, token="ben-test")
    assert "Secret salary review" not in [t["title"] for t in get(api, "deleted-tasks", token="ana-test")["tasks"]]
    assert post(api, f"tasks/{ticket['id']}/restore", {}, token="ana-test", expected=404)["error"]["code"] == "not_found"
    assert [t["title"] for t in get(api, "deleted-tasks", token="ben-test")["tasks"]] == ["Secret salary review"]


def test_restore_leaves_out_a_mapping_something_newer_took_and_wakes_nobody(api):
    from backend.task_delete import restore_tasks
    store = api.app.state.store
    ticket = post(api, "tasks", {"owner": "ben", "title": "Fix the login page", "body": "x"})
    with store.transaction() as c:
        c.execute("INSERT INTO service_keys(id,label,key_hash,created,created_by) VALUES('k1','Board sync','h1',?,"
                  "'human:ana')", (H.now(),))
        c.execute("INSERT INTO service_key_tasks VALUES('k1','EXT-1',?,?)", (ticket["id"], H.now()))
        assert delete_tasks(c, [ticket["id"]], apply=True)["applied"]
    again = post(api, "tasks", {"owner": "ben", "title": "Fix the login page again", "body": "x"})
    with store.transaction() as c:
        c.execute("INSERT INTO service_key_tasks VALUES('k1','EXT-1',?,?)", (again["id"], H.now()))
        jobs = c.execute("SELECT count(*) FROM jobs").fetchone()[0]
        report = restore_tasks(c, [ticket["id"]])
        assert report["restored"] == [ticket["id"]] and report["skipped"] == {"service_key_tasks": 1}
        assert c.execute("SELECT task_id FROM service_key_tasks WHERE key_id='k1'").fetchone()[0] == again["id"]
        assert c.execute("SELECT count(*) FROM jobs").fetchone()[0] == jobs


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


def test_a_number_given_by_hand_cannot_take_a_deleted_tasks_number(api):
    store = api.app.state.store
    with store.transaction() as c:
        c.execute("INSERT INTO task_types(id,name,created,updated,numbered) VALUES('t3','Ticket',?,?,1)", (H.now(), H.now()))
    first = post(api, "tasks", {"owner": "ben", "title": "Fix the cart page", "body": "x", "type": "t3", "number": 41})
    post(api, f"tasks/{first['id']}/delete", {})
    r = api.post("/api/v2/tasks", json={"owner": "ben", "title": "Fix the cart page again", "body": "x", "type": "t3",
                                       "number": 41}, headers=headers())
    assert r.status_code >= 400 and r.json()["error"]["code"] == "duplicate", r.text
    assert post(api, f"tasks/{first['id']}/restore", {})["unlinked"] == []
