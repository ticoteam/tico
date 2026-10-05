"""A task id a bot copies badly still works: a unique prefix resolves, an ambiguous one lists its
candidates, a one-character slip is suggested and never acted on, and none of it names a task the
caller may not see."""

from backend.tests.test_api import api, get, post  # noqa: F401  (fixtures)
from backend.tests.test_mcp import call


def make(api, owner="ops", title="Write the report", token="ana-test"):
    return post(api, "tasks", {"owner": owner, "title": title, "body": "Do it."}, token=token)


def slip(ident, at=20):
    """The id with one character changed."""
    return ident[:at] + ("a" if ident[at] != "a" else "b") + ident[at + 1:]


def renumber(api, old, new):
    c = api.app.state.store.connect()
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE tasks SET id=? WHERE id=?", (new, old))
    c.commit()
    c.close()


def test_a_unique_prefix_reaches_the_task_and_listings_show_it(api):
    task = make(api)
    assert task["short_id"] == task["id"][:8]
    assert get(api, "tasks/" + task["short_id"])["task"]["id"] == task["id"]
    assert task["short_id"] in [t["short_id"] for t in get(api, "tasks")["tasks"]]
    mine = post(api, "tasks/" + task["id"][:12], {"version": task["version"], "note": "on it", "status": "doing"})
    assert mine["id"] == task["id"] and mine["status"] == "doing"
    comment = post(api, f"tasks/{task['short_id']}/comments", {"text": "progress"})
    assert comment["comment"]["id"]
    post(api, f"tasks/{task['short_id']}/links", {"url": "https://example.com/x"})
    assert get(api, "tasks/" + task["id"])["task"]["links"]
    # Seven characters is too short to trust.
    get(api, "tasks/" + task["id"][:7], expected=404)


def test_prefixes_work_for_blocked_by_and_parent(api):
    parent, blocker, child = make(api, title="Parent"), make(api, title="Blocker"), make(api, title="Child")
    post(api, "tasks/" + child["short_id"] + "/relations", {"task": blocker["short_id"], "kind": "blocked_by"})
    done = post(api, "tasks/" + child["short_id"] + "/relations", {"task": parent["short_id"], "kind": "parent"})
    assert ([r["id"] for r in done["relations"]["blocks"]], [r["id"] for r in done["relations"]["parent"]]) \
        == ([blocker["id"]], [parent["id"]])
    sub = post(api, "tasks", {"owner": "ops", "title": "Sub", "body": "x", "relations": [{"task": parent["short_id"], "kind": "parent"}]})
    assert sub["relations"]["parent"][0]["id"] == parent["id"]


def test_an_ambiguous_prefix_lists_short_ids_and_titles(api):
    one, two = make(api, title="First thing"), make(api, title="Second thing")
    renumber(api, one["id"], "abcdef01-0000-4000-8000-000000000001")
    renumber(api, two["id"], "abcdef01-0000-4000-8000-000000000002")
    r = api.get("/api/v2/tasks/abcdef01", headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ambiguous_id"
    assert "abcdef01 (First thing)" in r.json()["error"]["detail"] and "abcdef01 (Second thing)" in r.json()["error"]["detail"]
    assert get(api, "tasks/abcdef01-0000-4000-8000-000000000002")["task"]["title"] == "Second thing"


def test_a_near_miss_is_suggested_and_never_used(api):
    task = make(api)
    wrong = slip(task["id"])
    r = api.get("/api/v2/tasks/" + wrong, headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 404
    assert r.json()["error"]["detail"] == f"No task {wrong}. Did you mean {task['id']} (Write the report)?"
    # A write through the wrong id changes nothing.
    r = api.post("/api/v2/tasks/" + wrong, json={"version": task["version"], "status": "doing"},
                 headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "k1"})
    assert r.status_code == 404 and "Did you mean" in r.json()["error"]["detail"]
    assert get(api, "tasks/" + task["id"])["task"]["status"] == task["status"]
    # A dropped character is a near miss too; three wrong characters are not.
    assert "Did you mean" in api.get("/api/v2/tasks/" + task["id"][:20] + task["id"][21:],
                                     headers={"Authorization": "Bearer ana-test"}).json()["error"]["detail"]
    far = task["id"][:20] + "".join("a" if c != "a" else "b" for c in task["id"][20:23]) + task["id"][23:]
    assert api.get("/api/v2/tasks/" + far, headers={"Authorization": "Bearer ana-test"}).json()["error"]["detail"] == "Task not found"


def test_tasks_the_caller_cannot_see_are_never_matched_or_named(api):
    private = make(api, owner="inbox", title="Review Ana's private mail")
    # Cara may not see the inbox bot: its task's prefix and near misses answer like an unknown id.
    for ident in (private["short_id"], slip(private["id"])):
        r = api.get("/api/v2/tasks/" + ident, headers={"Authorization": "Bearer cara-test"})
        assert r.status_code == 404 and r.json()["error"]["detail"] == "Task not found"
        assert "private mail" not in r.text
    # Ana may.
    assert get(api, "tasks/" + private["short_id"])["task"]["id"] == private["id"]
    # An ambiguous prefix does not count a task the caller cannot see.
    visible = make(api, title="Public one")
    renumber(api, visible["id"], "feedbee0-0000-4000-8000-000000000001")
    renumber(api, private["id"], "feedbee0-0000-4000-8000-000000000002")
    assert get(api, "tasks/feedbee0", token="cara-test")["task"]["title"] == "Public one"
    r = api.get("/api/v2/tasks/feedbee0", headers={"Authorization": "Bearer ana-test"})
    assert r.status_code == 409


def test_the_mcp_tools_take_a_short_id_and_list_it(api):
    task = make(api)
    error, listed = call(api, "hub_task_list", {})
    assert not error and task["short_id"] in [t["short_id"] for t in listed["result"]]
    error, shown = call(api, "hub_task_show", {"id": task["short_id"]})
    assert not error and shown["task"]["id"] == task["id"]
    error, label = call(api, "hub_task_label", {"id": task["short_id"], "add": ["bug"]})
    assert not error
    error, refused = call(api, "hub_task_show", {"id": slip(task["id"])})
    assert error and "Did you mean " + task["id"] in str(refused)
