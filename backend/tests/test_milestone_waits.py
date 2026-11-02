"""A task waits for a milestone, not only for another task to be done (Dev 256).

Oct 19: Dev 248 waited on Dev 245, but its real prerequisite was PR #943 merging. #943 merged at 12:42Z and 245 stayed
In QA, rightly, so nothing woke 248 for 22 minutes. The v0.3.64 checklist waited on the v0.3.63 checklist, which stays
open for its 48-hour QA watch; v0.3.63 was published at 13:41Z and v0.3.64 was restarted by hand an hour later. And Dev
237's blocked_by links were swapped for the reverse `blocks` links by a bot reporting a fix: nothing read them back.
"""

from fastapi.testclient import TestClient

from backend import hubdb as H
from backend import milestone_waits as MW
from backend.app import create_app
from backend.scheduler import Scheduler
from backend.tests.test_github import PR, api, get, headers, hook, pr_event, post  # noqa: F401

RELEASE_URL = "https://github.com/ticoteam/tico/releases/tag/v0.3.63"


def wakes(api, task):
    """The milestone wakes its owner got on this task."""
    with api.app.state.store.read() as c:
        return [r["body"] for r in c.execute(
            "SELECT m.body FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND json_extract(m.refs,'$.milestone') IS NOT NULL", (task["id"],))]


def name(task):
    """How a readback names a task: its number where it has one, else its short id, and its title."""
    return (f"#{task['number']}" if task.get("number") is not None else task["id"][:8]) + f' "{task["title"]}"'


def shown(api, task):
    return get(api, "tasks/" + task["id"])["task"]


def pair(api):
    """Dev 245 (its PR in review, then In QA) and Dev 248, which needs only that PR merged."""
    typ = post(api, "task-types", {"name": "Dev", "steps": [
        {"name": "Build", "status": "doing"}, {"name": "In QA", "status": "review"}, {"name": "Done", "status": "done"}]})["type"]
    upstream = post(api, "tasks", {"owner": "cpo", "title": "Visual replies 3/4", "body": "x", "links": [PR],
                                   "type": typ["id"], "step": "In QA"})
    waiter = post(api, "tasks", {"owner": "cmo", "title": "Visual replies 4/4", "body": "x"})
    return upstream, waiter


def wait(api, task, on, expected=200):
    r = api.post(f"/api/v2/tasks/{task['id']}/waits", json={"on": on}, headers=headers())
    assert r.status_code == expected, r.text
    return r.json()


def raw(api, path, body):
    """A write's whole reply, readback included (the shared `post` hands back only the task)."""
    r = api.post("/api/v2/" + path, json=body, headers=headers())
    assert r.status_code == 200, r.text
    return r.json()


def merge(api):
    return hook(api, "pull_request", pr_event("closed", merged=True, merge_commit_sha="abc123",
                                              merged_at="2026-10-19T12:42:19Z"))


def test_a_merge_wakes_the_waiter_once_and_the_upstream_keeps_its_step(api):
    upstream, waiter = pair(api)
    hook(api, "pull_request", pr_event("opened"))
    stored = wait(api, waiter, "pr:" + PR)
    assert stored["readback"] == [f"{name(waiter)} waits for PR #412 to merge"]
    page = shown(api, waiter)
    assert page["milestone_waits"][0]["sentence"] == "waits for PR #412 to merge"
    assert page["milestone_waits"][0]["met"] is False
    with api.app.state.store.read() as c:
        assert H.waiting_for(c, H.task(c, waiter["id"])) == "PR #412 to merge"
    assert wakes(api, waiter) == []

    result = merge(api)
    assert result["milestone_woken"] == [waiter["id"]]
    assert len(wakes(api, waiter)) == 1 and "PR #412 merged" in wakes(api, waiter)[0]
    after = shown(api, upstream)
    assert (after["status"], after["step_id"]) == (upstream["status"], upstream["step_id"]), "upstream never moves"
    assert shown(api, waiter)["milestone_waits"][0]["met"] is True

    merge(api)                                              # GitHub delivers the same event again
    Scheduler(api.app.state.store, api.app.state.execution).tick()   # and the sweep sees it too
    assert len(wakes(api, waiter)) == 1, "one wake per waiting task and milestone"


def test_a_wait_made_after_the_merge_is_woken_once_at_create(api):
    _, waiter = pair(api)
    merge(api)
    stored = wait(api, waiter, "pr:" + PR)
    assert "already happened" in stored["readback"][0]
    assert len(wakes(api, waiter)) == 1
    again = wait(api, waiter, "pr:" + PR)
    assert again["readback"][0].startswith("Already: ")
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert len(wakes(api, waiter)) == 1


def test_a_wait_on_a_task_with_one_pr_link_waits_for_that_pr_and_comes_off_the_same_way(api):
    upstream, waiter = pair(api)
    wait(api, waiter, "pr:" + upstream["id"])
    assert shown(api, waiter)["milestone_waits"][0]["target"] == "ticoteam/tico#412"
    r = api.post(f"/api/v2/tasks/{waiter['id']}/waits", json={"on": "pr:" + upstream["id"], "remove": True},
                 headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["readback"] == [f"Removed: {name(waiter)} waits for PR #412 to merge"]
    merge(api)
    assert wakes(api, waiter) == [] and shown(api, waiter)["milestone_waits"] == []
    wait(api, waiter, "pr:" + upstream["id"])
    other = post(api, "tasks", {"owner": "cpo", "title": "No PR here", "body": "x"})
    refused = wait(api, waiter, "pr:" + other["id"], expected=422)
    assert "has 0 pull request links" in str(refused), refused


def test_a_merge_while_the_server_was_down_wakes_once_after_restart(api, tmp_path):
    _, waiter = pair(api)
    wait(api, waiter, "pr:" + PR)
    # The merge reached the link but its wake was never written (the process stopped in between, or an older server).
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged', pr_sha='abc123' WHERE task_id<>? AND kind='pr'", (waiter["id"],))
    assert wakes(api, waiter) == []
    restarted = create_app(api.app.state.store.settings)
    with TestClient(restarted) as client:
        Scheduler(restarted.state.store, restarted.state.execution).tick()
        Scheduler(restarted.state.store, restarted.state.execution).tick()
        with restarted.state.store.read() as c:
            woke = c.execute("SELECT count(*) FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                             f"WHERE {H.MESSAGE_TASK_SQL}=? AND json_extract(m.refs,'$.milestone') IS NOT NULL",
                             (waiter["id"],)).fetchone()[0]
        assert woke == 1
        assert client  # the restarted app served while the sweep ran


def test_a_published_release_wakes_the_next_checklist_while_the_previous_stays_open(api):
    previous = post(api, "tasks", {"owner": "cpo", "title": "v0.3.63 release checklist", "body": "48 h QA watch"})
    following = post(api, "tasks", {"owner": "cmo", "title": "v0.3.64 release checklist", "body": "cut after v0.3.63"})
    stored = wait(api, following, "release:v0.3.63")
    assert stored["readback"] == [f"{name(following)} waits for release v0.3.63 to be published"]
    draft = {"action": "created", "release": {"tag_name": "v0.3.63", "draft": True, "html_url": RELEASE_URL},
             "repository": {"full_name": "ticoteam/tico"}}
    assert hook(api, "release", draft)["woken"] == []
    elsewhere = {"action": "published", "release": {"tag_name": "v0.3.63", "html_url": RELEASE_URL},
                 "repository": {"full_name": "ticoteam/other"}}
    assert hook(api, "release", elsewhere)["woken"] == [], "a tag in another repository is another release"
    published = {**elsewhere, "repository": {"full_name": "ticoteam/tico"}}
    assert hook(api, "release", published)["woken"] == [following["id"]]
    hook(api, "release", published)
    assert len(wakes(api, following)) == 1
    assert shown(api, previous)["status"] == previous["status"], "the watch stays open"


def test_a_relation_write_reads_back_its_direction(api):
    first = post(api, "tasks", {"owner": "cpo", "title": "Dev 233", "body": "x"})
    second = post(api, "tasks", {"owner": "cpo", "title": "Dev 237", "body": "x"})
    a, b = name(first), name(second)
    right = raw(api, f"tasks/{second['id']}/relations", {"task": first["id"], "kind": "blocked_by"})
    assert right["readback"] == [f"{b} waits for {a}", f"{a} blocks {b}"]
    post(api, f"tasks/{second['id']}/relations", {"task": first["id"], "kind": "blocked_by", "remove": True})
    reversed_ = raw(api, f"tasks/{second['id']}/relations", {"task": first["id"], "kind": "blocks"})
    assert reversed_["readback"] == [f"{a} waits for {b}", f"{b} blocks {a}"], "the reversed write is plain to see"
    same = wait(api, first, "task:" + second["id"])
    assert same["readback"] == [f"Already: {a} waits for {b}", f"Already: {b} blocks {a}"]
    made = raw(api, "tasks", {"owner": "cpo", "title": "Dev 238", "body": "x",
                               "relations": [{"task": first["id"], "kind": "parent"},
                                             {"task": second["id"], "kind": "blocked_by"}]})
    assert made["readback"][0] == f'{name(made["task"])} is a subtask of {a}', "the parent reads back too"
    assert made["readback"][2] == f'{name(made["task"])} waits for {b}'
    child = raw(api, "tasks", {"owner": "cpo", "title": "Dev 239", "body": "x",
                               "relations": [{"task": first["id"], "kind": "parent"}]})
    assert child["readback"] == [f'{name(child["task"])} is a subtask of {a}', f'{a} is the parent of {name(child["task"])}']


def test_every_way_to_block_a_task_on_its_own_subtask_reads_back_and_warns(api):
    """Oct 22 (PR #1095 review): the create, relation and `wait --on task:` writes all say so."""
    parent = post(api, "tasks", {"owner": "cpo", "title": "Release window", "body": "x"})
    p = name(parent)
    made = raw(api, "tasks", {"owner": "cpo", "title": "Hold merges", "body": "x",
                              "relations": [{"task": parent["id"], "kind": "parent"},
                                            {"task": parent["id"], "kind": "blocks"}]})
    hold = made["task"]
    assert made["readback"][2:] == [f"{p} waits for {name(hold)}", f"{name(hold)} blocks {p}"]
    assert "is a subtask of" in made["warning"] and hold["id"] in made["warning"]
    post(api, f"tasks/{parent['id']}/relations", {"task": hold["id"], "kind": "blocked_by", "remove": True})
    waited = wait(api, parent, "task:" + hold["id"])
    assert waited["readback"] == [f"{p} waits for {name(hold)}", f"{name(hold)} blocks {p}"]
    assert "is a subtask of" in waited["warning"]
    again = wait(api, parent, "task:" + hold["id"])
    assert again["readback"][0].startswith("Already: ") and "warning" in again, "still a block on its own subtask"
    r = api.post(f"/api/v2/tasks/{parent['id']}/waits", json={"on": "task:" + hold["id"], "remove": True},
                 headers=headers())
    assert r.status_code == 200 and "warning" not in r.json(), r.text
    other = post(api, "tasks", {"owner": "cpo", "title": "Unrelated", "body": "x"})
    assert "warning" not in wait(api, parent, "task:" + other["id"]), "only its own subtask warns"


def test_a_milestone_wait_is_awaited_and_parks_the_task():
    from backend import wake_rules
    assert wake_rules.awaited({"milestone": "pr_merged:ticoteam/tico#412"}) == "milestone"
    assert MW.release_target("0.3.64", "TicoTeam/Tico") == ("ticoteam/tico@v0.3.64", "release v0.3.64")
    assert MW.release_target("v0.3.64") is None, "no repository to name: refused"
    assert MW.pr_target(PR + "/") == ("ticoteam/tico#412", "PR #412", PR)


class FakeGitHub:
    """GitHub as the recovery pass reads it: a merged pull request, a published release, nothing else."""
    def __init__(self):
        self.merged, self.released, self.calls = set(), set(), []

    def call(self, method, path, headers=None, **kw):
        from types import SimpleNamespace
        self.calls.append(path)
        if "/pulls/" in path:
            number = int(path.rsplit("/", 1)[1])
            body = {"merged": number in self.merged, "html_url": f"https://github.com/ticoteam/tico/pull/{number}"}
            return SimpleNamespace(status_code=200, json=lambda: body)
        tag = path.rsplit("/", 1)[1]
        if tag in self.released:
            body = {"tag_name": tag, "draft": False, "published_at": "2026-10-19T13:41:25Z", "html_url": RELEASE_URL}
            return SimpleNamespace(status_code=200, json=lambda: body)
        return SimpleNamespace(status_code=404, json=lambda: {})


def recover(app, monkeypatch, github):
    import threading
    service = app.state.github_app
    with service.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
                  "VALUES('ticoteam/tico',1,1,'human:ana',?,'main')", (H.now(),))
    monkeypatch.setattr(service, "row", lambda c=None: {"slug": "test-app", "org": "ticoteam"})
    monkeypatch.setattr(service, "mint", lambda *a, **kw: ("test-token", "later"))
    monkeypatch.setattr(service, "_call", github.call)
    service.milestone_recover_next = 0
    service.__dict__.get("milestone_recover_cache", {}).clear()
    MW.recover(service)
    for worker in threading.enumerate():
        if worker.name == "tico-milestone-recover":
            worker.join(5)


def test_a_missed_merge_and_release_are_asked_of_github_and_wake_once(api, monkeypatch):
    """No webhook ever came and nothing local records either: an unlinked pull request merged while the server was
    down, and a release published before the wait was made. GitHub is asked; each waiter wakes once."""
    unlinked = "https://github.com/ticoteam/tico/pull/977"
    upstream = post(api, "tasks", {"owner": "cpo", "title": "v0.3.63 release checklist", "body": "watch"})
    on_pr = post(api, "tasks", {"owner": "cmo", "title": "Stacked change", "body": "x"})
    on_release = post(api, "tasks", {"owner": "cmo", "title": "v0.3.64 release checklist", "body": "x"})
    wait(api, on_pr, "pr:" + unlinked)
    wait(api, on_release, "release:v0.3.63")
    github = FakeGitHub()
    recover(api.app, monkeypatch, github)
    assert wakes(api, on_pr) == [] and wakes(api, on_release) == [], "not merged, not published: nothing"
    github.merged.add(977)
    github.released.add("v0.3.63")
    recover(api.app, monkeypatch, github)
    assert len(wakes(api, on_pr)) == 1 and len(wakes(api, on_release)) == 1
    recover(api.app, monkeypatch, github)
    Scheduler(api.app.state.store, api.app.state.execution).tick()
    assert len(wakes(api, on_pr)) == 1 and len(wakes(api, on_release)) == 1, "asked again, still one wake"
    assert shown(api, upstream)["status"] == upstream["status"]


def test_a_release_published_while_the_server_was_down_wakes_once_after_restart(api, monkeypatch):
    following = post(api, "tasks", {"owner": "cmo", "title": "v0.3.64 release checklist", "body": "x"})
    wait(api, following, "release:v0.3.63")
    github = FakeGitHub()
    github.released.add("v0.3.63")          # published while the server was down: no webhook, no local record
    restarted = create_app(api.app.state.store.settings)
    with TestClient(restarted):
        recover(restarted, monkeypatch, github)
        recover(restarted, monkeypatch, github)
        with restarted.state.store.read() as c:
            woke = c.execute("SELECT count(*) FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                             f"WHERE {H.MESSAGE_TASK_SQL}=? AND json_extract(m.refs,'$.milestone') IS NOT NULL",
                             (following["id"],)).fetchone()[0]
        assert woke == 1


def test_a_wait_note_comes_back_in_the_wake_and_a_task_wait_takes_none(api):
    _, waiter = pair(api)
    hook(api, "pull_request", pr_event("opened"))
    r = api.post(f"/api/v2/tasks/{waiter['id']}/waits", json={"on": "pr:" + PR, "note": "Check the migration, then QA"},
                 headers=headers())
    assert r.status_code == 200, r.text
    assert shown(api, waiter)["milestone_waits"][0]["note"] == "Check the migration, then QA"
    merge(api)
    assert len(wakes(api, waiter)) == 1
    assert wakes(api, waiter)[0].endswith("\nYour note: Check the migration, then QA")
    # A task: wait is the blocked_by relation, which has no note of its own: refused rather than dropped.
    other = post(api, "tasks", {"owner": "cmo", "title": "Pick the logo", "body": "x"})
    r = api.post(f"/api/v2/tasks/{waiter['id']}/waits", json={"on": "task:" + other["id"], "note": "later"},
                 headers=headers())
    assert r.status_code >= 400 and "note" in r.text


def test_a_changed_wait_note_is_a_task_change_with_history_and_the_later_wake_carries_it(api):
    _, waiter = pair(api)
    hook(api, "pull_request", pr_event("opened"))
    wait(api, waiter, "pr:" + PR)                                   # first stored with no note
    version = shown(api, waiter)["version"]
    raw(api, f"tasks/{waiter['id']}/waits", {"on": "pr:" + PR, "note": "Check the migration"})
    assert shown(api, waiter)["version"] == version + 1, "a changed note bumps the task version"
    again = raw(api, f"tasks/{waiter['id']}/waits", {"on": "pr:" + PR, "note": "Check the migration"})
    assert again["readback"][0].startswith("Already: ")
    assert shown(api, waiter)["version"] == version + 1, "the same note again changes nothing"
    raw(api, f"tasks/{waiter['id']}/waits", {"on": "pr:" + PR, "note": "Check the migration, then QA"})
    with api.app.state.store.read() as c:
        notes = [(r["old"], r["new"]) for r in c.execute(
            "SELECT old,new FROM task_events WHERE task_id=? AND field='milestone_wait_note' ORDER BY ts,id",
            (waiter["id"],))]
    assert notes == [(None, "Check the migration"), ("Check the migration", "Check the migration, then QA")], notes
    merge(api)
    assert wakes(api, waiter)[-1].endswith("\nYour note: Check the migration, then QA")
