"""Task bug-hunt regressions, using only disposable fictional team data."""
import threading
from types import SimpleNamespace

import pytest

from backend import github as G, hubdb as H, milestone_waits as MW
from backend import task_delete as TD, task_relations as TR, task_rules as R, worktree_waits as WW
from backend.tests.test_github import api, get, post, pr_event, PR  # noqa: F401

AT = "2026-10-26T12:00:00.000000Z"
OLD = "2026-10-25T11:00:00.000000Z"


@pytest.fixture
def c(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: AT)
    with api.app.state.store.transaction() as conn:
        yield conn


def create(c, title, owner="bot:cmo", **kw):
    return H.task_create(c, "human:ana", title, "Synthetic regression fixture", owner, lint=False, **kw)


def eligible(c, task_id):
    cutoff = H.shift(AT, hours=-24)
    return task_id in {r["id"] for r in c.execute(R.DUE_SQL, (*([cutoff] * 5), 50))}


def age(c, task_id):
    c.execute("UPDATE tasks SET created_at=?,updated_at=?,done_at=? WHERE id=?", (OLD, OLD, OLD, task_id))
    c.execute("UPDATE task_events SET ts=? WHERE task_id=?", (OLD, task_id))
    c.execute("UPDATE messages SET created_at=? WHERE conversation_id=(SELECT conversation_id FROM tasks WHERE id=?)",
              (OLD, task_id))


def test_keeper_notices_do_not_restart_auto_close_but_team_messages_do(c):
    task = create(c, "Finished export")
    H.task_update(c, "bot:cmo", task["id"], status="done")
    age(c, task["id"])
    H._wake(c, task, task["owner"], "Release validation finished", quiet=True)
    H.task_comment(c, H.KEEPER, task["id"], "Seen again by the watcher", wake=False)
    row = H.task(c, task["id"])
    assert row["updated_at"] == OLD
    assert R.untouched(c, row, 24, H.parse_ts(AT))
    assert eligible(c, task["id"])
    for actor in ("human:ana", "bot:cpo"):
        msg = H.say(c, actor, "bot:cmo", "Please check the export", conversation_id=task["conversation_id"],
                    refs={"task": task["id"], "quiet": True})
        assert not R.untouched(c, row, 24, H.parse_ts(AT))
        assert not eligible(c, task["id"])
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (OLD, msg["id"]))
    R.close(c, H.task(c, task["id"]), {"id": "synthetic-rule", "name": "Close 24h after done"})
    assert H.task(c, task["id"])["status"] == "closed"


def test_declined_blocker_releases_waiter_with_one_wake_after_last_blocker(c):
    waiter = create(c, "Build export")
    blockers = [create(c, title, owner="bot:cpo") for title in ("Pick columns", "Pick format")]
    for blocker in blockers:
        TR.add_blocker(c, "human:ana", waiter["id"], blocker["id"], mover=True)
    H.task_update(c, "bot:cmo", waiter["id"], status="waiting")
    def wakes():
        return c.execute("SELECT count(*) FROM messages WHERE json_extract(refs,'$.blocker_done') IS NOT NULL "
                         "AND json_extract(refs,'$.task')=?", (waiter["id"],)).fetchone()[0]
    H.task_update(c, "bot:cpo", blockers[0]["id"], status="declined")
    assert TR.blocker_ids(c, waiter["id"]) == [blockers[1]["id"]]
    assert wakes() == 0
    H.task_update(c, "bot:cpo", blockers[1]["id"], status="declined")
    assert not TR.blocker_ids(c, waiter["id"])
    assert wakes() == 1
    notice = H._one(c, "SELECT refs FROM messages WHERE json_extract(refs,'$.blocker_done')=?",
                    (blockers[1]["id"],))
    assert H._json(notice["refs"], {})["blocker_status"] == "declined"
    H.task_update(c, "bot:cpo", blockers[1]["id"], status="declined")
    assert wakes() == 1


@pytest.mark.parametrize("kind", ["blocks", "blocked_by"])
def test_child_cannot_wait_for_its_own_parent(c, kind):
    parent = create(c, "Prepare launch")
    child = create(c, "Prepare launch copy", parent_id=parent["id"])
    a, b = (parent, child) if kind == "blocks" else (child, parent)
    assert TR.own_subtask_warning(c, a["id"], b["id"], kind)
    with pytest.raises(H.Refused, match="parent|ancestor"):
        TR.relate(c, "human:ana", a["id"], b["id"], kind, mover=True)
    assert not TR.blocker_ids(c, child["id"])
    # A parent waiting for its child's answer remains a valid delegation.
    TR.add_blocker(c, "human:ana", parent["id"], child["id"], mover=True)
    assert TR.blocker_ids(c, parent["id"]) == [child["id"]]


def test_reparenting_cannot_turn_an_existing_blocker_into_an_ancestor(c):
    parent = create(c, "Prepare launch")
    child = create(c, "Prepare launch copy")
    grandchild = create(c, "Review launch copy", parent_id=child["id"])
    TR.add_blocker(c, "human:ana", grandchild["id"], parent["id"], mover=True)
    with pytest.raises(H.Refused, match="parent|ancestor"):
        TR.set_parent(c, "human:ana", child["id"], parent["id"], mover=True)
    assert TR.parent_of(c, child["id"]) is None


@pytest.mark.parametrize("state", ["waiting", "reserved"])
def test_worktree_wait_is_visible_and_survives_stranded_sweep(c, state):
    task = create(c, "Build task fix")
    H.task_update(c, "bot:cmo", task["id"], status="waiting")
    WW.ensure(c)
    c.execute("INSERT INTO worktree_waits(id,task_id,owner,repo,generation,state,reason,created_at,updated_at) "
              "VALUES('wait',?,'bot:cmo','acme/product',1,?,'No free worktree',?,?)", (task["id"], state, OLD, OLD))
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (OLD, task["id"]))
    row = H.task(c, task["id"])
    detail = H.wait_detail(c, row)
    assert detail and "worktree" in detail["why"] and "acme/product" in detail["why"]
    assert H.waiting_for(c, row) == detail["why"]
    assert H.sweep_stranded(c, at=AT) == []
    assert H.task(c, task["id"])["status"] == "waiting"
    c.execute("UPDATE worktree_waits SET state='cancelled' WHERE id='wait'")
    assert H.wait_detail(c, row) is None


@pytest.mark.parametrize("status", ["done", "declined", "closed"])
def test_finishing_bot_task_retires_only_its_generated_person_ask_cards(c, status):
    parent = create(c, "Prepare export")
    card = H._ask_in_subtask(c, "bot:cmo", parent, "human:ana", "Which format should the export use?")
    ordinary = create(c, "Independently check export", owner="human:ana", parent_id=parent["id"])
    assert card["id"] in {t["id"] for t in H.needs_you(c, "human:ana")["tasks"]}
    if status == "closed":
        H.task_close(c, "human:ana", parent["id"])
    else:
        H.task_update(c, "bot:cmo", parent["id"], status=status)
    assert H.task(c, card["id"])["status"] == "closed"
    assert card["id"] not in {t["id"] for t in H.needs_you(c, "human:ana")["tasks"]}
    assert H.task(c, ordinary["id"])["status"] == "open"
    assert not H.open_task_asks(c, H.task(c, card["id"]))


def test_shared_person_card_stays_until_every_dependent_task_finishes(c):
    root = create(c, "Prepare campaign")
    first = create(c, "Prepare campaign export", parent_id=root["id"])
    second = create(c, "Prepare campaign report", parent_id=root["id"])
    card = H._ask_in_subtask(c, "bot:cmo", first, "human:ana", "Which format should we use?")
    assert H._ask_in_subtask(c, "bot:cmo", second, "human:ana", "Confirm the report format?")["id"] == card["id"]
    H.task_update(c, "bot:cmo", first["id"], status="done")
    assert H.task(c, card["id"])["status"] == "open"
    H.task_update(c, "bot:cmo", second["id"], status="done")
    assert H.task(c, card["id"])["status"] == "closed"


def test_milestone_wake_resumes_waiter_without_later_stranded_wake(c):
    typ = H.type_create(c, "human:ana", "Delivery", [{"name": "Build", "status": "doing"}], mover=True)
    task = create(c, "Build after merge", type=typ["id"], step="Build")
    H.task_update(c, "bot:cmo", task["id"], status="waiting")
    before = H.task(c, task["id"])
    MW.add(c, "human:ana", before, "pr:https://github.com/acme/product/pull/1")
    assert MW.happened(c, "pr_merged", "acme/product#1") == [task["id"]]
    row = H.task(c, task["id"])
    assert row["status"] == "doing" and row["step_id"] == before["step_id"]
    assert row["version"] > before["version"]
    count = c.execute("SELECT count(*) FROM messages").fetchone()[0]
    assert H.sweep_stranded(c, at=H.shift(AT, hours=3)) == []
    assert MW.happened(c, "pr_merged", "acme/product#1") == []
    assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == count


@pytest.mark.parametrize("retained", ["closed", "missing"])
def test_milestone_resume_with_obsolete_step_notifies_owner_once(c, retained):
    typ = H.type_create(c, "human:ana", "Delivery edge", [
        {"name": "Ready", "status": "open"}, {"name": "Archived", "status": "closed"}], mover=True)
    task = create(c, "Resume after merge", type=typ["id"], step="Ready")
    step = typ["steps"][1]["id"] if retained == "closed" else None
    c.execute("UPDATE tasks SET status='waiting',step_id=?,requester='bot:cpo' WHERE id=?", (step, task["id"]))
    c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,then_outcome,created_by,created_at,updated_at) "
              "VALUES('resume-rule','team','','step_entered','wake','human:ana',?,?)", (AT, AT))
    MW.add(c, "human:ana", H.task(c, task["id"]), "pr:https://github.com/acme/product/pull/2")
    before = c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:cmo'").fetchone()[0]
    other = c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:cpo'").fetchone()[0]
    assert MW.happened(c, "pr_merged", "acme/product#2") == [task["id"]]
    row = H.task(c, task["id"])
    assert row["status"] == "open" and row["step_id"] == typ["steps"][0]["id"]
    assert c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:cmo'").fetchone()[0] == before + 1
    assert c.execute("SELECT count(*) FROM messages WHERE to_actor='bot:cpo'").fetchone()[0] == other + 1
    assert H.sweep_stranded(c, at=H.shift(AT, hours=3)) == []


def test_milestone_keeps_waiting_when_another_wait_remains(c):
    task = create(c, "Build after both merges")
    H.task_update(c, "bot:cmo", task["id"], status="waiting")
    for n in (1, 2):
        MW.add(c, "human:ana", H.task(c, task["id"]), f"pr:https://github.com/acme/product/pull/{n}")
    MW.happened(c, "pr_merged", "acme/product#1")
    assert H.task(c, task["id"])["status"] == "waiting"
    MW.happened(c, "pr_merged", "acme/product#2")
    assert H.task(c, task["id"])["status"] != "waiting"


@pytest.mark.parametrize("edit", ["status", "remove"])
def test_trashed_task_prevents_its_step_changing_before_restore(c, edit):
    typ = H.type_create(c, "human:ana", "Editorial", [{"name": "Write", "status": "doing"}], mover=True)
    task = create(c, "Write campaign", owner="human:ana", type=typ["id"], step="Write")
    result = TD.delete_tasks(c, [task["id"]], apply=True, actor="human:ana")
    assert result["applied"], result
    step = typ["steps"][0]
    with pytest.raises(H.Refused, match="step|trash"):
        H.type_update(c, "human:ana", typ["id"], steps=[{**step, "status": "review"}] if edit == "status" else [], mover=True)
    result = TD.restore_tasks(c, [task["id"]], actor="human:ana")
    assert result["restored"] == [task["id"]], result
    restored = H.task(c, task["id"])
    assert restored["status"] == c.execute("SELECT status FROM task_steps WHERE id=?", (restored["step_id"],)).fetchone()[0]


@pytest.mark.parametrize("merged", [False, True])
def test_opening_task_refreshes_links_without_touching_tasks_or_close_timer(api, monkeypatch, merged):
    tasks = [post(api, "tasks", {"owner": "cpo", "title": title, "body": "x", "links": [PR]})
             for title in ("Ship export", "Review export")]
    service = api.app.state.github_app
    with service.store.transaction() as c:
        c.execute("INSERT INTO repositories(full_name,reachable,enabled,added_by,updated_at) VALUES(?,1,1,?,?)",
                  ("ticoteam/tico", "human:ana", H.now()))
        G.pull_request(c, pr_event("synchronize", head={"sha": "head", "ref": "export"}, state="open"))
        for task in tasks:
            H.task_update(c, "human:ana", task["id"], status="done")
            age(c, task["id"])
            assert eligible(c, task["id"])
    pr = {"state": "closed" if merged else "open", "merged": merged, "draft": False,
          "mergeable": True, "head": {"sha": "head", "ref": "export"},
          "merge_commit_sha": "merge" if merged else None, "merged_at": AT if merged else None}
    monkeypatch.setattr(service, "row", lambda c=None: {"slug": "example-app", "org": "ticoteam"})
    monkeypatch.setattr(service, "mint", lambda *a, **kw: ("synthetic-token", "later"))
    monkeypatch.setattr(service, "_call", lambda method, path, **kw:
                        SimpleNamespace(status_code=200 if "/pulls/" in path else 404, json=lambda: dict(pr)))
    get(api, "tasks/" + tasks[0]["id"])
    for worker in threading.enumerate():
        if worker.name == "tico-pr-refresh":
            worker.join(5)
    assert not service.pr_refresh_running
    with service.store.read() as c:
        for task in tasks:
            row = H.task(c, task["id"])
            link = H.task_links(c, task["id"])[0]
            assert link["state"] == ("merged" if merged else "open")
            assert link["mergeable"] == "clean"
            assert row["updated_at"] == OLD
            assert R.untouched(c, row, 24, H.parse_ts(AT))
            assert eligible(c, task["id"])
            history = c.execute("SELECT * FROM task_events WHERE task_id=? AND field='github' AND ts>?", (task["id"], OLD)).fetchall()
            assert len(history) == int(merged)
            if merged:
                assert "GitHub" in history[0]["note"] and "merged" in history[0]["new"]


@pytest.mark.parametrize("mergeable", [True, False])
def test_computed_mergeability_webhook_never_counts_as_task_activity(c, mergeable):
    task = create(c, "Build export")
    H.task_link(c, "human:ana", task["id"], PR, kind="pr", mover=True)
    G.pull_request(c, pr_event("synchronize", head={"sha": "head", "ref": "export"}))
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (OLD, task["id"]))
    G.pull_request(c, pr_event("opened", head={"sha": "head", "ref": "export"}, mergeable=mergeable))
    assert H.task_links(c, task["id"])[0]["mergeable"] == ("clean" if mergeable else "conflict")
    assert H.task(c, task["id"])["updated_at"] == OLD
    G.pull_request(c, pr_event("closed", merged=True))
    assert H.task(c, task["id"])["updated_at"] == AT
    assert c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='github'", (task["id"],)).fetchone()
