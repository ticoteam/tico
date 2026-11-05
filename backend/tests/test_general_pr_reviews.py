"""Bot PRs outside ticket boards use the ticket pools and captured verdict handoff."""
import json
from types import SimpleNamespace

import pytest

from backend import bot_settings as BS, check_pools as P, health, hubdb as H, verdict_moves as V
from backend.tests.test_check_pools import team
from backend.tests.test_github import api, headers  # noqa: F401
from backend.tests.test_review_gate import HEAD, OLD


def setup(api, reviews=1, qa=False, limit=20):
    team(api)
    with api.app.state.store.transaction() as c:
        steps = [{"name": "Build", "status": "doing"}]
        for i in range(reviews):
            steps.append({"name": f"Review {i + 1}", "status": "review", "check_pool": ["rev-a", "rev-b"],
                          "check_limit": limit, "flow": {"check": "review", "on_pass": "merge"}})
        if qa:
            steps.append({"name": "In QA", "status": "review", "check_pool": ["rev-c"],
                          "flow": {"check": "qa", "on_pass": "merge"}})
        typ = H.type_create(c, H.KEEPER, "Dev tickets", steps)
        BS.update(c, "cpo", {"github_login": "acme-writer"})
        c.execute("UPDATE bot_config SET repo='Acme/atlas' WHERE bot='cpo'")
    return typ


def task_pr(api, number=12, head=HEAD, private=False, conflict=False, stale=False, labels=None, owner="bot:cpo"):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", f"Update the guide {number}", "Private text" if private else "Docs and code",
                             owner, private=private, labels=labels, mover=True)
        pr = H.task_link(c, "human:ana", task["id"], f"https://github.com/Acme/atlas/pull/{number}")
        detail = {"head_sha": head, "author_login": "acme-writer", "checks": {"required": "passing"},
                  "mergeable_state": "behind" if stale else "clean"}
        c.execute("UPDATE task_links SET state='open',repo='Acme/atlas',checks='passing',mergeable=?,detail=? WHERE id=?",
                  ("conflict" if conflict else "clean", json.dumps(detail), pr["id"]))
    return task


def children(api, task, active=True):
    with api.app.state.store.read() as c:
        rows = [H.task(c, cid) for cid in H.TR.child_ids(c, task["id"])]
    return [r for r in rows if not active or r["status"] in H.ACTIVE_STATUSES]


def dispatch(api):
    return P.retry(api.app.state.store)


def finish(api, child, verdict="approve", head=HEAD, capture=True):
    with api.app.state.store.transaction() as c:
        attempt = H.new_id()
        c.execute("INSERT INTO attempts(id,bot_id,created_at,task_id) VALUES(?,?,?,?)",
                  (attempt, H.actor_id(child["owner_id"]), H.now(), child["id"]))
        H.task_update(c, child["owner_id"], child["id"], status="done",
                      **({"verdict": verdict, "verdict_head": head} if verdict else {}),
                      note="Fix the broken link." if verdict == "changes" else "Reviewed the current diff.")
        if capture:
            c.execute("UPDATE attempts SET settled_at=? WHERE id=?", (H.now(), attempt))
            P.capture_attempt(c, attempt)
    return attempt


def handoffs(api, task, kind):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT body,recipient_id FROM messages WHERE conversation_id=? "
                                          "AND json_extract(refs,'$.handoff')=?", (task["conversation_id"], kind))]


def test_general_pr_uses_pool_once_and_new_push_supersedes_open_review(api):
    setup(api)
    task = task_pr(api)
    assert len(dispatch(api)) == 1
    [child] = children(api, task)
    assert child["title"] == "Review 1: PR #12"
    assert child["owner_id"] == "bot:rev-a" and child["requester_id"] == H.KEEPER
    assert "https://github.com/Acme/atlas/pull/12" in child["body"] and HEAD in child["body"]
    assert dispatch(api) == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.head_sha',?) WHERE task_id=?", (OLD, task["id"]))
        V.new_head(c, H.task(c, task["id"]))
    [new] = children(api, task)
    assert new["id"] != child["id"] and OLD in new["body"]
    assert len(children(api, task, active=False)) == 2


@pytest.mark.parametrize("verdict,head,green,capture,merge,changes", [
    ("approve", HEAD, True, True, True, False),
    ("approve", OLD, True, True, False, False),
    ("approve", HEAD, False, True, False, False),
    ("approve", HEAD, True, False, False, False),
    ("changes", HEAD, True, True, False, True),
])
def test_verdict_handoff_requires_current_head_capture_and_green_checks(api, verdict, head, green, capture, merge, changes):
    setup(api)
    task = task_pr(api)
    dispatch(api)
    [child] = children(api, task)
    if not green:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE task_links SET checks='pending' WHERE task_id=?", (task["id"],))
    finish(api, child, verdict, head, capture)
    dispatch(api)
    assert bool(handoffs(api, task, "merge")) == merge
    assert bool(handoffs(api, task, "verdict")) == changes
    if merge:
        assert HEAD in handoffs(api, task, "merge")[0]["body"]
        dispatch(api)
        assert len(handoffs(api, task, "merge")) == 1
    if changes:
        assert "Fix the broken link" in handoffs(api, task, "verdict")[-1]["body"]


def test_green_after_approval_wakes_owner_and_qa_and_second_review_are_required(api):
    setup(api, reviews=2, qa=True)
    task = task_pr(api, labels=["qa"])
    dispatch(api)
    [first] = children(api, task)
    finish(api, first)
    [second] = children(api, task)
    assert second["title"].startswith("Review 2:") and second["owner_id"] != first["owner_id"]
    assert not handoffs(api, task, "merge")
    finish(api, second)
    [qa] = children(api, task)
    assert qa["title"].startswith("QA:")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET checks='pending' WHERE task_id=?", (task["id"],))
    finish(api, qa, "pass")
    assert not handoffs(api, task, "merge")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET checks='passing' WHERE task_id=?", (task["id"],))
        V.checks_done(c, H.task(c, task["id"]), HEAD, "pass")
    assert len(handoffs(api, task, "merge")) == 1


def test_review_two_excludes_the_current_heads_first_reviewer(api):
    setup(api, reviews=2)
    task = task_pr(api)
    dispatch(api)
    [first] = children(api, task)
    finish(api, first)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET last_turn_at=? WHERE id='rev-a'", (H.now(),))
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.head_sha',?) WHERE task_id=?", (OLD, task["id"]))
        V.new_head(c, H.task(c, task["id"]))
    [next_first] = children(api, task)
    assert next_first["owner_id"] == "bot:rev-b" and next_first["title"].startswith("Review 1:")
    finish(api, next_first, head=OLD)
    [second] = children(api, task)
    assert second["title"].startswith("Review 2:") and second["owner_id"] == "bot:rev-a"


def test_private_task_is_not_shared_with_pool(api):
    setup(api)
    task = task_pr(api, private=True)
    dispatch(api)
    assert children(api, task) == []
    with api.app.state.store.read() as c:
        assert "private" in P.waiting(c, task["id"])[0]["reason"]
        assert not c.execute("SELECT 1 FROM messages WHERE recipient_id LIKE 'bot:rev-%' AND body LIKE '%Private text%'").fetchone()


def test_private_reviewer_cannot_send_a_private_verdict_to_a_public_task(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        for slug in ("rev-a", "rev-b"):
            BS.update(c, slug, {"private_tasks_default": True})
    task = task_pr(api)
    dispatch(api)
    assert not children(api, task)
    with api.app.state.store.read() as c:
        assert "private" in P.waiting(c, task["id"])[0]["reason"]


@pytest.mark.parametrize("private", [True, False], ids=["private-findings", "public-findings"])
@pytest.mark.parametrize("title,bad,good", [
    ("Review 1", "changes", "approve"), ("QA", "fail", "pass"), ("Review 1", None, "approve"),
])
def test_later_public_pass_keeps_the_findings_source_audience(api, private, title, bad, good):
    from backend import task_privacy
    from backend.auth import Identity
    from backend.tests.test_api import unrelated_task_managers

    setup(api, qa=True)
    task = task_pr(api, labels=["qa"])
    observer = Identity("bot:rev-c", "bot", agent="test")
    api.app.state.auth.settings.test_identities["observer-test"] = observer
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES(?,?,?,?,?)",
                  ("rev-c", "test", "observer-credential", H.now(), "human:ana"))
        first = H.task_create(c, "bot:cpo", f"{title}: PR #12", f"Check head {HEAD}", "bot:rev-a",
                              parent_id=task["id"], private=private, lint=False)
    finish(api, first, bad)
    with api.app.state.store.transaction() as c:
        second = H.task_create(c, "bot:cpo", f"{title}: PR #12", f"Check head {HEAD}", "bot:rev-b",
                               parent_id=task["id"], private=False, lint=False)
    finish(api, second, good)
    with api.app.state.store.transaction() as c:
        outcome = V.delivery(c, H.task(c, second["id"]))
        assert outcome["source_tasks"] == [first["id"], second["id"]]
        assert "only a new head clears it" in outcome["say"]
        assert task_privacy.content_readable(c, observer.actor, outcome) is not private
        wake = H.message(c, c.execute("SELECT id FROM messages WHERE conversation_id=? "
                                      "AND json_extract(refs,'$.handoff')='verdict' ORDER BY rowid DESC LIMIT 1",
                                      (task["conversation_id"],)).fetchone()[0])
        assert wake["refs"]["trigger_source"] == first["id"]
        # A later Done note/final reply reuses the saved outcome, not just the public child's audience.
        H._deliver_subtask_result(c, H.task(c, second["id"]), H.task(c, task["id"]), "Later result.", quiet=True)
        replay = H.message(c, c.execute("SELECT id FROM messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1",
                                        (task["conversation_id"],)).fetchone()[0])
        for msg in (wake, replay):
            assert first["id"] in msg["refs"]["source_tasks"]
            assert ("Fix the broken link." if bad == "changes" else "Reviewed the current diff.") in msg["body"]
            assert task_privacy.message_readable(c, observer.actor, msg) is not private
            assert task_privacy.message_readable(c, "bot:cpo", msg)
        assert H.task_private_readable(c, observer.actor, H.task(c, first["id"])) is not private
    for msg in (wake, replay):
        response = api.get("/api/v2/messages/" + msg["id"], headers={"Authorization": "Bearer observer-test"})
        assert response.status_code == (404 if private else 200), response.text


@pytest.mark.parametrize("private", [True, False], ids=["private-approval", "public-approval"])
@pytest.mark.parametrize("other_merger", [False, True], ids=["parent-wake", "merge-task"])
def test_delayed_merge_notice_keeps_its_approval_sources(api, private, other_merger):
    from backend import task_privacy
    from backend.tests.test_api import unrelated_task_managers

    typ = setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        if other_merger:
            c.execute("UPDATE bot_config SET repo='' WHERE bot='cpo'")
            c.execute("UPDATE bot_config SET repo='Acme/atlas' WHERE bot='cmo'")
            c.execute("UPDATE task_types SET manager='cmo' WHERE id=?", (typ["id"],))
        c.execute("UPDATE task_links SET checks='pending' WHERE task_id=?", (task["id"],))
        review = H.task_create(c, "bot:cpo", "Review 1: PR #12", f"Check head {HEAD}", "bot:rev-a",
                               parent_id=task["id"], private=private, lint=False)
    finish(api, review)
    with api.app.state.store.transaction() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.handoff')='merge'").fetchone()
        c.execute("UPDATE task_links SET checks='passing' WHERE task_id=?", (task["id"],))
        assert V.merge_wake(c, H.task(c, task["id"]))
        [notice] = [H.message(c, r[0]) for r in c.execute(
            "SELECT id FROM messages WHERE json_extract(refs,'$.handoff')='merge'")]
        assert notice["refs"]["source_tasks"] == [review["id"]]
        assert notice["refs"]["trigger_source"] == review["id"]
        assert "Approved at" in notice["body"]
        assert task_privacy.message_readable(c, "bot:rev-c", notice) is not private
        assert task_privacy.message_readable(c, "bot:cpo", notice)
        if other_merger:
            merge_task = H.task(c, notice["refs"]["task"])
            assert merge_task["owner_id"] == "bot:cmo" and merge_task["id"] != task["id"]
            assert "Approved" not in merge_task["body"] and "--match-head-commit" in merge_task["body"]
        else:
            assert notice["refs"]["task"] == task["id"]


def test_backfill_is_oldest_first_and_limited_per_reviewer_per_hour(api, monkeypatch):
    setup(api)
    monkeypatch.setattr(P, "PR_REVIEWS_PER_HOUR", 1)
    tasks = [task_pr(api, n) for n in range(20, 24)]
    with api.app.state.store.transaction() as c:
        for i, task in enumerate(tasks):
            c.execute("UPDATE task_links SET created_at=? WHERE task_id=?", (H.shift(H.now(), hours=-10 + i), task["id"]))
    dispatch(api)
    assert [len(children(api, t)) for t in tasks] == [1, 1, 0, 0]
    dispatch(api)
    assert [len(children(api, t)) for t in tasks] == [1, 1, 0, 0]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE history_events SET created_at=? WHERE kind='pr_review_filed'", (H.shift(H.now(), hours=-2),))
    dispatch(api)
    assert [len(children(api, t)) for t in tasks] == [1, 1, 1, 1]


def test_backfill_takes_older_waits_before_discovering_the_next_page(api, monkeypatch):
    setup(api)
    monkeypatch.setattr(P, "PR_REVIEWS_PER_HOUR", 1)
    tasks = [task_pr(api, n) for n in range(30, 35)]
    P.retry(api.app.state.store, limit=3)
    assert [len(children(api, t)) for t in tasks] == [1, 1, 0, 0, 0]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE history_events SET created_at=? WHERE kind='pr_review_filed'", (H.shift(H.now(), hours=-2),))
    P.retry(api.app.state.store, limit=3)
    assert len(children(api, tasks[2])) == 1


@pytest.mark.parametrize("conflict,stale", [(True, False), (False, True)])
def test_conflicting_or_behind_pr_returns_to_owner_once(api, conflict, stale):
    setup(api)
    task = task_pr(api, conflict=conflict, stale=stale)
    dispatch(api)
    dispatch(api)
    assert children(api, task) == []
    [notice] = handoffs(api, task, "merge_conflict")
    assert "rebase on main" in notice["body"]


@pytest.mark.parametrize("mergeable_state", ["dirty", "behind"])
def test_backfill_reads_github_before_dispatching_a_cached_clean_pr(api, monkeypatch, mergeable_state):
    setup(api)
    task = task_pr(api)
    service = api.app.state.github_app
    monkeypatch.setattr(service, "row", lambda c: {"org": "Acme"})
    monkeypatch.setattr(service, "mint", lambda *a, **kw: ("synthetic", "later"))

    def fetch(method, path, **kw):
        data = ({"head": {"sha": HEAD}, "state": "open", "mergeable_state": mergeable_state}
                if "/pulls/" in path else {"check_runs": [{"name": "required", "conclusion": "success"}]}
                if path.endswith("check-runs") else [])
        return SimpleNamespace(status_code=200, json=lambda: data)

    monkeypatch.setattr(service, "_call", fetch)
    P.retry(api.app.state.store, service=service)
    assert not children(api, task)
    assert len(handoffs(api, task, "merge_conflict")) == 1


def test_role_review_without_check_model_is_normal_child(api):
    setup(api)
    task = task_pr(api)
    result = api.post("/api/v2/tasks", headers=headers(), json={"owner": "rev-a", "title": "PR #12", "body": "Review the PR.",
                      "check_role": "review", "relations": [{"task": task["id"], "kind": "parent"}]})
    assert result.status_code == 200, result.text
    [child] = children(api, task)
    assert child["title"] == "Review 1: PR #12"
    with api.app.state.store.read() as c:
        assert H.check_model(c, child["id"]) is None
    assert dispatch(api) == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.head_sha',?) WHERE task_id=?", (OLD, task["id"]))
        V.new_head(c, H.task(c, task["id"]))
    [current] = children(api, task)
    assert current["id"] != child["id"] and OLD in current["body"]


def test_merge_falls_back_to_existing_repo_manager_without_granting_access(api):
    typ = setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='' WHERE bot='cpo'")
        c.execute("UPDATE bot_config SET repo='Acme/atlas' WHERE bot='cmo'")
        c.execute("UPDATE task_types SET manager='cmo' WHERE id=?", (typ["id"],))
    task = task_pr(api)
    dispatch(api)
    finish(api, children(api, task)[0])
    [merge] = children(api, task)
    assert merge["owner_id"] == "bot:cmo" and merge["title"].startswith("Merge PR #12")
    assert not handoffs(api, task, "merge")


def test_done_task_with_open_pr_returns_to_review_and_human_pr_is_skipped(api):
    setup(api)
    task = task_pr(api)
    human = task_pr(api, 13)
    with api.app.state.store.transaction() as c:
        H.task_update(c, H.KEEPER, task["id"], status="done", strict=False)
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.author_login','acme-person') WHERE task_id=?", (human["id"],))
    dispatch(api)
    assert len(children(api, task)) == 1 and children(api, human) == []
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["status"] == "review"


def test_two_prs_at_same_head_each_need_their_own_review(api):
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        second = H.task_link(c, "human:ana", task["id"], "https://github.com/Acme/atlas/pull/13")
        first = next(p for p in H.task_links(c, task["id"]) if p["number"] == 12)
        c.execute("UPDATE task_links SET state='open',repo='Acme/atlas',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (first["detail"], second["id"]))
    dispatch(api)
    dispatch(api)
    reviews = children(api, task)
    assert {r["title"] for r in reviews} == {"Review 1: PR #12", "Review 1: PR #13"}
    finish(api, next(r for r in reviews if "#12" in r["title"]))
    assert all("merge PR #13" not in m["body"] for m in handoffs(api, task, "merge"))
    finish(api, next(r for r in reviews if "#13" in r["title"]))
    assert any("merge PR #13" in m["body"] for m in handoffs(api, task, "merge"))


def test_fresh_failed_checks_do_not_allow_merge_from_a_green_cache(api, monkeypatch):
    setup(api)
    task = task_pr(api)
    dispatch(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET checks='pending' WHERE task_id=?", (task["id"],))
    finish(api, children(api, task)[0])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET checks='passing' WHERE task_id=?", (task["id"],))
    from backend import review_gate
    monkeypatch.setattr(review_gate, "snapshot", lambda service, prs: {
        p["id"]: {"head_sha": HEAD, "checks": {"required": "failing"}} for p in prs})
    P.retry_prs(api.app.state.store, service=api.app.state.github_app)
    assert not handoffs(api, task, "merge")


def test_health_names_unfiled_pr_after_a_day_without_disclosing_private_task(api):
    setup(api)
    public = task_pr(api)
    private = task_pr(api, 13, private=True)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_links SET created_at=?", (H.shift(H.now(), hours=-25),))
    from backend.auth import Identity
    with api.app.state.store.read() as c:
        auth = api.app.state.auth
        checks = health._unfiled_pr_checks(c, Identity("human:ana", "owner", "ana@acme.example"), auth, True, "owner")
        assert public["id"] in {i["task"] for i in checks[0]["items"]}
        assert private["id"] in {i["task"] for i in checks[0]["items"]}
        other = health._unfiled_pr_checks(c, Identity("bot:rev-a", "bot"), auth, False, "bot")
        assert private["id"] not in {i["task"] for check in other for i in check["items"]}
