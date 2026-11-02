"""People on a task by role (backend/task_roles.py): any role name, one person in several roles,
`member`/`role` list the tasks someone is on, each change is in the history, and the rules on who
may name people hold."""
from backend import task_roles as TRo
from backend.tests.test_api import api, get, post  # noqa: F401  (fixtures)


def test_people_join_a_task_under_any_role_and_the_list_finds_them(api):
    t = post(api, "tasks", {"owner": "ben", "title": "(B) Fix the refund page", "body": "x",
                            "roles": {"developer": ["ben"], "reviewer": ["cara"]}}, token="ben-test")
    assert t["roles"] == {"developer": ["human:ben"], "reviewer": ["human:cara"]}, "the owner may hold a role too"
    # A role named replaces its people; one person may hold several roles; a role not named stays.
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "roles": {"qa": ["ana", "ana", "cara"], "designer": ["cara"]}},
             token="ben-test")
    assert t["roles"] == {"developer": ["human:ben"], "reviewer": ["human:cara"], "qa": ["human:ana", "human:cara"],
                          "designer": ["human:cara"]}
    ids = lambda query: [x["id"] for x in get(api, "tasks?" + query)["tasks"]]
    assert t["id"] in ids("member=cara") and t["id"] in ids("member=cara&role=designer")
    assert t["id"] not in ids("member=ana&role=reviewer") and t["id"] not in ids("member=ben&role=qa")
    get(api, "tasks?member=nobody-here", expected=422)
    get(api, "tasks?role=reviewer", expected=422)
    # [] clears a role; each change is in the history and the list carries the same field.
    t = post(api, "tasks/" + t["id"], {"version": t["version"], "roles": {"qa": []}}, token="ben-test")
    assert "qa" not in t["roles"] and t["id"] not in ids("member=ana")
    history = {(e["field"], e["old"], e["new"]) for e in get(api, "tasks/" + t["id"])["events"]}
    assert ("role:reviewer", None, "human:cara") in history and ("role:qa", "human:ana, human:cara", None) in history
    row = next(x for x in get(api, "tasks?member=cara")["tasks"] if x["id"] == t["id"])
    assert row["roles"]["designer"] == ["human:cara"]


def test_only_someone_who_may_change_the_task_names_its_people_and_only_people_on_the_roster(api):
    t = post(api, "tasks", {"owner": "ben", "title": "(B) Fix the refund page", "body": "x"}, token="ben-test")
    post(api, "tasks/" + t["id"], {"version": t["version"], "roles": {"reviewer": ["cara"]}}, token="cara-test", expected=403)
    post(api, "tasks/" + t["id"], {"version": t["version"], "roles": {"reviewer": ["nobody"]}}, token="ben-test", expected=422)
    post(api, "tasks/" + t["id"], {"version": t["version"], "roles": {"Code Reviewer!": ["cara"]}}, token="ben-test", expected=422)
    assert get(api, "tasks/" + t["id"])["task"]["roles"] == {}
    # A private task names only people who can read it.
    secret = post(api, "tasks", {"owner": "ben", "title": "Review the salary file", "body": "x", "private": True}, token="ana-test")
    post(api, "tasks/" + secret["id"], {"version": secret["version"], "roles": {"reviewer": ["cara"]}}, token="ben-test", expected=422)
    s = post(api, "tasks/" + secret["id"], {"version": secret["version"], "roles": {"reviewer": ["ana"]}}, token="ben-test")
    assert s["roles"]["reviewer"] == ["human:ana"]
    # A deleted task takes its people to the trash and back.
    post(api, "tasks/" + s["id"] + "/delete", {}, token="ana-test")
    with api.app.state.store.transaction() as c:
        assert TRo.roles_of(c, s["id"]) == {}
    post(api, "tasks/" + s["id"] + "/restore", {}, token="ana-test")
    assert get(api, "tasks/" + s["id"], token="ana-test")["task"]["roles"]["reviewer"] == ["human:ana"]
