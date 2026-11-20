"""A pull request closed without merging after its task finished leaves Health's aged_prs in one maintenance pass
(Engineering #2398: PR #4381 duplicated #4382, which merged and finished the task; #4381 closed later and Health kept
telling the Release Owner to merge it, because only open tasks' links were read again)."""

import json
from types import SimpleNamespace

from backend import github as G
from backend import task_engine
from backend.store import H
from backend.tests.helpers.api import headers
from backend.tests.helpers.github import post

OLD = H.shift(H.now(), hours=-5)
PR_OLD = H.shift(H.now(), hours=-13)
LIST = "/repos/acme/app/pulls"


def _named(api):
    response = api.get("/api/v2/health", headers=headers())
    assert response.status_code == 200, response.text
    found = next((x for x in response.json()["checks"] if x["id"] == "aged_prs"), None)
    return sorted((i["name"] for i in found["items"]), key=lambda n: int(n.split("#")[1])) if found else []


def _github(api, monkeypatch, open_now, answers=None):
    """Fake GitHub: `open_now` is the set of open PR numbers the list answers (None: the list fails), `answers` maps
    a PR number to its read (status code, body), closed by default. Returns the paths read."""
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute(
            "INSERT OR IGNORE INTO repositories(full_name,reachable,enabled,added_by,updated_at,default_branch) "
            "VALUES('acme/app',1,1,'human:ana',?,'main')",
            (H.now(),),
        )
    seen = []

    def fetch(method, path, headers=None, params=None, **kw):
        seen.append(path)
        if path == LIST:
            if open_now is None:
                return SimpleNamespace(status_code=502, json=lambda: {})
            page = sorted(open_now)[(params["page"] - 1) * 100 : params["page"] * 100]
            return SimpleNamespace(status_code=200, json=lambda: [{"number": n} for n in page])
        code, body = (answers or {}).get(int(path.rsplit("/", 1)[1]), (200, {"state": "closed", "merged": False}))
        return SimpleNamespace(status_code=code, json=lambda: body)

    monkeypatch.setattr(service, "mint", lambda *a, **k: ("t", "later"))
    monkeypatch.setattr(service, "_call", fetch)
    monkeypatch.setattr(G, "_finished_retry", {})
    monkeypatch.setattr(G, "_finished_list_due", {})
    return service, seen


def _refresh(service, monkeypatch):
    """One pass, as the installed app for acme (only for the pass: Health reads the real app row)."""
    with monkeypatch.context() as m:
        m.setattr(service, "row", lambda c=None: {"slug": "test-app", "org": "acme"})
        return G.refresh_finished_task_prs(service)


def waiting(api, statuses, numbers=None, read_at=OLD):
    """One bot task per status, each linked to an acme/app PR (#1, #2, ... unless `numbers` says), green and waiting
    for review for 13 hours, last read at `read_at`. A finished task is made open and finished through the engine's
    move(), the only way its status changes. Returns the task ids."""
    numbers = numbers or list(range(1, len(statuses) + 1))
    made = [post(api, "tasks", {"owner": "cpo", "title": f"PR task {n}", "body": "x"}) for n in range(len(statuses))]
    with api.app.state.store.transaction() as c:
        for n, (status, number) in enumerate(zip(statuses, numbers)):
            tid = made[n]["id"]
            detail = {"head_sha": f"h{n}", "health_wait": {"reason": "review", "since": PR_OLD}}
            c.execute(
                "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,mergeable,checks,"
                "review_state,detail,number,repo,branch,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"link-{tid}",
                    tid,
                    "pr",
                    f"https://github.com/acme/app/pull/{number}",
                    "PR",
                    "open",
                    "bot:few-00",
                    OLD,
                    "clean",
                    "passing",
                    "",
                    json.dumps(detail),
                    number,
                    "acme/app",
                    f"b{n}",
                    read_at,
                ),
            )
            if status != "open":
                end = task_engine.DONE if status == "done" else task_engine.CLOSED
                task_engine.move(c, tid, end, "human:ana", "test: the task finished")
    return [t["id"] for t in made]


def _link(api, tid):
    with api.app.state.store.read() as c:
        row = dict(c.execute("SELECT * FROM task_links WHERE task_id=? AND kind='pr'", (tid,)).fetchone())
    return {**row, "detail": H._json(row["detail"], {})}


def _bursts(api):
    with api.app.state.store.read() as c:
        return sorted(r[0] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'github-task-wake:%'"))


def test_a_pr_closed_after_its_task_finished_leaves_aged_prs_in_one_pass(api, monkeypatch):
    t = waiting(api, ["done", "done", "open"])
    assert _named(api) == ["PR #1", "PR #2", "PR #3"]  # the stale link is still named: the bug
    service, seen = _github(api, monkeypatch, open_now={2, 3})
    before = _bursts(api)
    assert _refresh(service, monkeypatch) == 1
    assert seen == [LIST, LIST + "/1"]  # the open task's PR is refresh_conflicts' to read
    closed = _link(api, t[0])
    assert closed["state"] == "closed" and "health_wait" not in closed["detail"]
    assert _link(api, t[1])["state"] == "open" and _link(api, t[2])["state"] == "open"
    assert _named(api) == ["PR #2", "PR #3"]
    assert _bursts(api) == before  # a finished task's owner is not woken
    # Within FINISHED_LIST_INTERVAL_S the repo's open PRs are not listed again.
    assert _refresh(service, monkeypatch) == 0 and seen == [LIST, LIST + "/1"]
    # Once the 30-minute interval elapses, the listing runs again.
    G._finished_list_due.clear()
    assert _refresh(service, monkeypatch) == 0 and seen[-1] == LIST  # nothing left to read


def test_the_eleventh_and_a_just_read_link_leave_in_the_same_pass(api, monkeypatch):
    # Review of #4485: eleven due links with the closed one last in order, and a link read a minute ago.
    waiting(api, ["done"] * 11)
    recent = waiting(api, ["done"], numbers=[12], read_at=H.now())
    service, seen = _github(api, monkeypatch, open_now=set(range(1, 11)))
    assert _refresh(service, monkeypatch) == 2
    assert _named(api) == [f"PR #{n}" for n in range(1, 11)]
    assert _link(api, recent[0])["state"] == "closed"


def test_past_the_read_cap_each_gone_pr_still_leaves_health_now(api, monkeypatch):
    monkeypatch.setattr(G, "FINISHED_READS_PER_PASS", 1)
    t = waiting(api, ["done", "closed", "done"], numbers=[1, 2, 2])  # two tasks name PR #2: one read for both
    service, seen = _github(api, monkeypatch, open_now=set())
    assert _refresh(service, monkeypatch) == 1
    assert _named(api) == []  # all three left Health in this pass
    states = [_link(api, x)["state"] for x in t]
    assert states in (["closed", "open", "open"], ["open", "closed", "closed"])  # one PR read, the other waits
    assert _refresh(service, monkeypatch) == 0  # the repo is listed again only after FINISHED_LIST_INTERVAL_S
    G._finished_list_due.clear()  # the next listing
    assert _refresh(service, monkeypatch) == 1
    assert [_link(api, x)["state"] for x in t] == ["closed", "closed", "closed"]
    assert seen.count(LIST + "/2") == 1


def test_merged_missing_and_failed_reads_and_a_failed_list(api, monkeypatch):
    t = waiting(api, ["done", "closed", "done"])
    answers = {
        1: (404, {}),
        2: (200, {"state": "closed", "merged": True, "merge_commit_sha": "c" * 40, "merged_at": H.now()}),
        3: (502, {}),
    }
    service, seen = _github(api, monkeypatch, open_now=None, answers=answers)
    assert _refresh(service, monkeypatch) == 0  # the list failed: nothing is inferred
    assert _named(api) == ["PR #1", "PR #2", "PR #3"]
    service, seen = _github(api, monkeypatch, open_now=set(), answers=answers)
    assert _refresh(service, monkeypatch) == 2
    assert _link(api, t[0])["state"] == "closed"
    merged = _link(api, t[1])
    assert merged["state"] == "merged" and merged["pr_sha"] == "c" * 40
    assert _link(api, t[2])["state"] == "open"  # its read failed: read again after the wait
    assert _named(api) == []  # but it is not open on GitHub, so Health no longer names it
    reads = [p for p in seen if p != LIST]
    G._finished_list_due.clear()
    assert _refresh(service, monkeypatch) == 0
    assert [p for p in seen if p != LIST] == reads  # the failed read backs off


def test_the_github_sync_step_runs_it(api, monkeypatch):
    from backend import heartbeat

    ran = []
    monkeypatch.setattr(G, "refresh_finished_task_prs", lambda service: ran.append(service))
    heartbeat.github_sync(SimpleNamespace(app=api.app, store=api.app.state.store))
    assert ran == [api.app.state.github_app]


def test_open_pr_listing_is_rate_limited_to_thirty_minutes_per_repo(api, monkeypatch):
    waiting(api, ["done"])
    service, seen = _github(api, monkeypatch, open_now={1})
    clock = [1000.0]
    monkeypatch.setattr(G, "_monotonic", lambda: clock[0])
    assert _refresh(service, monkeypatch) == 0
    assert seen == [LIST]
    clock[0] += G.FINISHED_LIST_INTERVAL_S - 1
    assert _refresh(service, monkeypatch) == 0
    assert seen == [LIST]
    clock[0] += 1
    assert _refresh(service, monkeypatch) == 0
    assert seen == [LIST, LIST]
