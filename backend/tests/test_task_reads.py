"""Task lists that cost only what changed (backend/task_reads.py): a delta read from a cursor says
what changed and what left, never naming a task the reader could not have seen; a stale cursor
resets; an unchanged full read is a 304."""

import base64
import json
import time
import uuid

from backend import events as E
from backend.auth import Identity
from backend import hubdb as H
from backend import task_reads
from backend.tests.helpers.api import get, headers, post, restrict  # noqa: F401

ACTIVE = "status=open"


def tasks(api, query, token="ana-test"):
    return get(api, "tasks?" + query, token=token)


def delta(api, query, cursor, token="ana-test"):
    return tasks(api, f"{query}&changed_after={cursor}", token=token)


def test_a_delta_returns_what_changed_and_what_left(api):
    keep = post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    finish = post(api, "tasks", {"owner": "ben", "title": "Book the room", "body": "Please."})
    trash = post(api, "tasks", {"owner": "ben", "title": "Order lunch", "body": "Please."})
    quiet = post(api, "tasks", {"owner": "ben", "title": "Print the badges", "body": "Please."})
    cursor = tasks(api, ACTIVE)["cursor"]
    assert delta(api, ACTIVE, cursor) == {"tasks": [], "gone": [], "cursor": cursor, "next_offset": None}

    post(api, "tasks/" + keep["id"], {"version": keep["version"], "title": "Draft the agenda today"})
    post(api, "tasks/" + finish["id"] + "/done", {"note": "Booked."})
    post(api, "tasks/" + trash["id"] + "/delete", {})
    got = delta(api, ACTIVE, cursor)
    assert [t["title"] for t in got["tasks"]] == ["Draft the agenda today"]
    assert sorted(got["gone"]) == sorted([finish["id"], trash["id"]])
    assert quiet["id"] not in str(got) and got["cursor"] != cursor
    # Nothing since the new cursor.
    assert delta(api, ACTIVE, got["cursor"])["tasks"] == []


def test_a_task_made_private_is_gone_only_for_who_could_see_it(api):
    shared = post(api, "tasks", {"owner": "ben", "title": "Review the launch copy", "body": "Please."})
    secret = post(
        api, "tasks", {"owner": "ben", "title": "Review the salary bands", "body": "Please.", "private": True}
    )
    before = tasks(api, ACTIVE, token="cara-test")
    cara, ana = before["cursor"], tasks(api, ACTIVE)["cursor"]
    assert shared["id"] in str(before) and secret["id"] not in str(before)

    post(api, "tasks/" + shared["id"], {"version": shared["version"], "private": True})
    post(api, "tasks/" + secret["id"], {"version": secret["version"], "title": "Review the salary bands now"})
    got = delta(api, ACTIVE, cara, token="cara-test")
    assert got["gone"] == [shared["id"]] and got["tasks"] == []
    assert secret["id"] not in str(got)
    # Its parties still get it, private and changed.
    ana = delta(api, ACTIVE, ana)
    assert {t["id"] for t in ana["tasks"]} == {shared["id"], secret["id"]} and ana["gone"] == []


def test_a_cursor_the_log_no_longer_covers_or_other_access_resets(api):
    old = tasks(api, ACTIVE, token="cara-test")["cursor"]
    post(api, "tasks", {"owner": "ben", "title": "Clear the old work", "body": "Please."})
    assert E.sweep(api.app.state.store, H.shift(H.now(), hours=E.KEEP_HOURS + 1)) > 0
    post(api, "tasks", {"owner": "ben", "title": "Plan the new work", "body": "Please."})
    got = delta(api, ACTIVE, old, token="cara-test")
    assert got["reset"] is True and got["tasks"] == [] and got["cursor"] != old
    assert delta(api, ACTIVE, "999999." + old.split(".")[1], token="cara-test")["reset"] is True
    assert delta(api, ACTIVE, "nonsense", token="cara-test")["reset"] is True
    # Access changed since the cursor (ops's activity closed to Cara): read in full.
    fresh = tasks(api, ACTIVE, token="cara-test")["cursor"]
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    assert delta(api, ACTIVE, fresh, token="cara-test")["reset"] is True


def test_an_unchanged_full_read_is_a_304_until_a_task_or_a_grant_changes(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Check the backups", "body": "Please."})

    def read(path, tag=None):
        return api.get("/api/v2/" + path, headers={**headers("cara-test"), **({"If-None-Match": tag} if tag else {})})

    for path in ("tasks?" + ACTIVE, "tasks/labels"):
        first = read(path)
        tag = first.headers["etag"]
        again = read(path, tag)
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag, path

    tag = read("tasks?" + ACTIVE).headers["etag"]
    assert read("tasks?" + ACTIVE + "&brief=true", tag).status_code == 200  # another query, another tag
    post(api, "tasks/" + task["id"], {"version": task["version"], "title": "Check the backups twice"})
    changed = read("tasks?" + ACTIVE, tag)
    assert changed.status_code == 200 and "Check the backups twice" in changed.text
    tag = changed.headers["etag"]
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    hidden = read("tasks?" + ACTIVE, tag)
    assert hidden.status_code == 200 and task["id"] not in hidden.text


def test_one_change_to_many_tasks_is_one_bulk_change_and_resets_lists(api):
    seed = post(api, "tasks", {"owner": "ben", "title": "Tag the launch work", "body": "Please."})
    store = api.app.state.store
    with store.transaction() as c:
        c.execute(
            "INSERT INTO tags(id,key,label,created_at,updated_at) VALUES('tg1','launch','Launch',?,?)",
            (H.now(), H.now()),
        )
        # A copy starts in no stage, as every new row does (tasks_enter_only).
        columns = [
            row[1]
            for row in c.execute("PRAGMA table_info(tasks)")
            if row[1] not in ("id", "number", "pipeline_id", "stage_id")
        ]
        for n in range(E.FAN_CAP + 1):
            c.execute(
                f"INSERT INTO tasks(id,{','.join(columns)}) SELECT ?,{','.join(columns)} FROM tasks WHERE id=?",
                (f"bulk{n}", seed["id"]),
            )
            c.execute("INSERT INTO task_tags(task_id,tag_id) VALUES(?, 'tg1')", (f"bulk{n}",))
    cursor = tasks(api, ACTIVE)["cursor"]
    with store.transaction() as c:
        before = E.latest(c)
        c.execute("UPDATE tags SET label='Launch week' WHERE id='tg1'")
        assert [tuple(r) for r in c.execute("SELECT topic, kind, subject_id FROM changes WHERE seq>?", (before,))] == [
            ("tasks", "bulk", None)
        ]
    assert delta(api, ACTIVE, cursor)["reset"] is True
    # The stream sends it as one event, not one per task.
    with store.read() as c:
        ana = Identity("human:ana", "owner", "ana@acme.example")
        sent, *_ = E.read(c, api.app.state.auth, ana, before, topics=("tasks",))
    assert [data for _, _, data in sent if data.get("bulk")] and len(sent) == 1


def test_a_bot_list_stays_304_when_only_other_tasks_change(api):
    mine = post(api, "tasks", {"owner": "ops", "title": "Check the backups", "body": "Please."})
    other = post(api, "tasks", {"owner": "assistant", "title": "Plan the offsite", "body": "Soon."})

    def read(tag=None):
        return api.get(
            "/api/v2/tasks?owner=ops&status=open&brief=true",
            headers={**headers("ana-test"), **({"If-None-Match": tag} if tag else {})},
        )

    first = read()
    assert first.status_code == 200 and "body" not in first.json()["tasks"][0], "brief rows carry no body"
    tag = first.headers["etag"]
    post(api, "tasks/" + other["id"], {"version": other["version"], "title": "Plan the offsite in May"})
    assert read(tag).status_code == 304, "a change outside the rows sends no body"
    post(api, "tasks/" + mine["id"], {"version": mine["version"], "title": "Check the backups twice"})
    changed = read(tag)
    assert changed.status_code == 200 and "Check the backups twice" in changed.text


def test_a_batch_read_returns_only_readable_tasks_and_names_the_rest_missing(api):
    seen = post(api, "tasks", {"owner": "assistant", "title": "Plan the offsite", "body": "Soon."})
    hidden = post(api, "tasks", {"owner": "ops", "title": "Check the backups", "body": "Please."})
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", people=["ana"])
    got = tasks(api, f"ids={seen['id']},{hidden['id']},nope&brief=true", token="cara-test")
    assert [t["id"] for t in got["tasks"]] == [seen["id"]] and "body" not in got["tasks"][0]
    assert got["missing"] == [hidden["id"], "nope"], "a task the reader cannot see is missing, as unknown ids are"
    both = tasks(api, f"ids={seen['id']},{hidden['id']}")
    assert {t["id"] for t in both["tasks"]} == {seen["id"], hidden["id"]} and both["missing"] == []


def test_task_list_keeps_one_snapshot_and_next_read_sees_revocation(api, monkeypatch):
    store = api.app.state.store
    with store.transaction() as c:
        private = H.task_create(
            c, "human:cara", "Private agenda", "Sensitive details", "human:ben", private=True, lint=False
        )
    original = H.resolve_actor
    changed = False

    def revoke_after_owner_read(c, actor):
        nonlocal changed
        resolved = original(c, actor)
        if actor == "ben" and not changed:
            changed = True
            with store.transaction() as write:
                H.task_reassign(write, H.KEEPER, private["id"], "human:ana", "Ana takes it")
        return resolved

    monkeypatch.setattr(H, "resolve_actor", revoke_after_owner_read)
    first = tasks(api, ACTIVE + "&owner=ben", token="ben-test")
    assert changed
    rows = [t for t in first["tasks"] if t["id"] == private["id"]]
    assert len(rows) == 1 and rows[0]["owner_id"] == "human:ben"
    assert private["id"] not in str(tasks(api, ACTIVE, token="ben-test"))
    assert private["id"] in {t["id"] for t in tasks(api, ACTIVE, token="ana-test")["tasks"]}


def test_hot_read_helpers_do_not_write_to_their_snapshot(api, monkeypatch):
    from contextlib import contextmanager
    from backend.tests.helpers.api import assign, runner

    store = api.app.state.store
    with store.transaction() as c:
        H.task_create(c, "human:cara", "Private agenda", "Sensitive details", "human:ben", private=True, lint=False)
        H.status_set(c, "bot:ops", "ops", "idle", "Ready")
    computer = runner(api)
    assign(api, computer, "ops")
    original = store.read_transaction
    loans = []
    reads = []

    @contextmanager
    def read_only():
        with original() as c:
            loans.append(c)
            before = c.total_changes
            yield c
            # A pooled connection can already have changes from an earlier writer.
            assert c.total_changes == before, "a hot read helper wrote to its snapshot"
            reads.append(c.total_changes - before)

    monkeypatch.setattr(store, "read_transaction", read_only)
    for token in ("ana-test", "ben-test", "cara-test"):
        for path in ("tasks?owner=ops", "status", "status?bot=ops", "config"):
            get(api, path, token=token)
    get(api, "runners/assignments", token=computer["token"])
    first = api.get("/api/v2/tasks", headers=headers())
    unchanged = api.get("/api/v2/tasks", headers={**headers(), "If-None-Match": first.headers["etag"]})
    assert unchanged.status_code == 304
    get(api, "tasks?limit=0", expected=422)
    assert len(loans) == 16
    assert reads == [0] * 15, "the rejected request exits through the exception path"
    assert all(not c.in_transaction for c in loans)
    assert all(not c.in_transaction for c in store.pool)


def test_a_task_list_leaves_its_connection_to_the_pool(api, monkeypatch):
    # A changed connection (a SQL function, an authorizer) is closed after its read: a list that did that
    # made every list open a new connection and parse the whole schema again.
    post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    store = api.app.state.store
    get(api, "tasks")
    opened = []
    connect = store.connect
    monkeypatch.setattr(store, "connect", lambda *a, **k: opened.append(1) or connect(*a, **k))
    for token in ("ana-test", "ben-test"):
        get(api, "tasks", token=token)
    assert opened == []


def test_a_deleted_task_with_no_trash_row_is_reported_gone_to_its_reader(api):
    # Its trash snapshot purged, the change row's own snapshot says who could read it: its reader is told it is gone.
    made = post(api, "tasks", {"owner": "ben", "title": "Order lunch", "body": "Please."})
    cursor = tasks(api, ACTIVE)["cursor"]
    post(api, "tasks/" + made["id"] + "/delete", {})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_trash WHERE task_id=?", (made["id"],))
    assert made["id"] in delta(api, ACTIVE, cursor)["gone"]


def test_an_unknown_filter_is_refused_not_ignored(api):
    """A filter the list does not take answers 422 naming it, never the list without it (#150: `assignee=` read as
    no filter and answered every open task)."""
    post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    r = api.get("/api/v2/tasks?assignee=bot:x&status=open", headers=headers())
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "filter" and "assignee" in error["detail"] and "owner=" in error["detail"]
    assert "status" in error["detail"] and "waiting_on" in error["detail"]  # the filters it does take
    # A delta read is refused the same way.
    cursor = tasks(api, ACTIVE)["cursor"]
    r = api.get(f"/api/v2/tasks?status=open&changed_after={cursor}&colour=red", headers=headers())
    assert r.status_code == 422 and r.json()["error"]["code"] == "filter" and "colour" in r.text, r.text
    assert api.get("/api/v2/tasks?owner=bot:x", headers=headers()).status_code == 200
    assert get(api, "tasks?owner=bot:ops&status=open")["tasks"] == []  # owner= still filters


def test_every_filter_the_list_takes_still_answers(api):
    from backend import task_list_routes

    made = post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    cursor = tasks(api, ACTIVE)["cursor"]
    values = {
        "owner": "ben",
        "requester": "ana",
        "status": "open",
        "lane": "company",
        "label": "area-a",
        "limit": "10",
        "offset": "0",
        "sort": "queue",
        "number": "1",
        "updated_since": "2026-01-01T00:00:00Z",
        "brief": "true",
        "member": "ana",
        "role": "requester&member=ana",  # role narrows member
        "changed_after": cursor,
        "ids": made["id"],
        "snoozed": "all",
        "q": "agenda",
        "pipeline": made.get("pipeline_id") or "engineering",
        "stage": made.get("stage_id") or "engineering-build",
        "waiting_on": "ben",
        "pushed_past": "false",
        "search": "agenda",
        "owners": "human:ben",
        "stages": made.get("stage_id") or "general-todo",
        "requesters": "human:ana",
        "labels": "area-a",
        "turn": "human:ben",
        "due": "any",
        "late": "false",
        "after": base64.urlsafe_b64encode(json.dumps([0, 1, "", ""]).encode()).decode().rstrip("="),  # a queue key
        "on_behalf_of": "default",
        "type": "bug",  # installed Recorder builds send it; accepted and ignored
    }
    assert set(values) == task_list_routes.LIST_FILTERS | {"on_behalf_of", "type"}
    for name, value in values.items():
        r = api.get(f"/api/v2/tasks?{name}={value}", headers=headers())
        assert r.status_code == 200, (name, r.text)


def test_the_filter_list_is_what_the_route_declares(api):
    """LIST_FILTERS is kept by hand next to the handler; it must name exactly the route's query parameters."""
    from backend import task_list_routes

    route = next(r for r in api.app.routes if getattr(r, "path", "") == "/api/v2/tasks" and "GET" in r.methods)
    assert {p.alias for p in route.dependant.query_params} == task_list_routes.LIST_FILTERS


def test_an_installed_recorder_type_search_still_answers(api):
    """Recorder builds from 0.5.181 send `type=<task type>` with a picker search: it answers the search, unnarrowed."""
    made = post(api, "tasks", {"owner": "ben", "title": "Review pricing", "body": "Please."})
    r = api.get("/api/v2/tasks?q=pricing&limit=30&brief=true&offset=0&type=bug", headers=headers())
    assert r.status_code == 200, r.text
    assert [t["id"] for t in r.json()["tasks"]] == [made["id"]]
