"""Bot PRs outside ticket boards use the ticket pools and captured verdict handoff."""
import json
from types import SimpleNamespace

import pytest

from backend import bot_settings as BS, check_pools as P, health, hubdb as H, pr_identity as PR, verdict_moves as V
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


@pytest.mark.parametrize("second_head", [HEAD, OLD], ids=["same-head", "different-heads"])
def test_pr_1_and_12_each_need_their_own_review(api, second_head):
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        second = H.task_link(c, "human:ana", task["id"], "https://github.com/Acme/atlas/pull/1")
        first = next(p for p in H.task_links(c, task["id"]) if p["number"] == 12)
        c.execute("UPDATE task_links SET state='open',repo='Acme/atlas',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({**H._json(first["detail"], {}), "head_sha": second_head}), second["id"]))
    dispatch(api)
    dispatch(api)
    reviews = children(api, task)
    assert {r["title"] for r in reviews} == {"Review 1: PR #12", "Review 1: PR #1"}
    dispatch(api)
    dispatch(api)
    assert {r["id"] for r in children(api, task)} == {r["id"] for r in reviews}
    finish(api, next(r for r in reviews if r["title"] == "Review 1: PR #12"))
    [notice] = handoffs(api, task, "merge")
    assert PR.matches(notice, "https://github.com/Acme/atlas/pull/12")
    assert not PR.matches(notice, "https://github.com/Acme/atlas/pull/1")
    finish(api, next(r for r in reviews if r["title"] == "Review 1: PR #1"), head=second_head)
    dispatch(api)
    assert len(handoffs(api, task, "merge")) == 2


def engineering_pr_pair(api):
    from backend import review_gate, step_flow
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, "Dev", [{"name": "Build", "status": "doing"}])
        c.execute("UPDATE tasks SET type_id=?,step_id=? WHERE id=?", (typ["id"], typ["steps"][0]["id"], task["id"]))
        task = H.task(c, task["id"])
        assert review_gate.engineering(c, task) and not step_flow.checks(c, typ["id"])
        first = H.task_links(c, task["id"])[0]
        second = H.task_link(c, "human:ana", task["id"], "https://github.com/Acme/atlas/pull/13")
        c.execute("UPDATE task_links SET state='open',repo='Acme/atlas',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (json.dumps({**H._json(first["detail"], {}), "head_sha": OLD}), second["id"]))
        return task, H.task_links(c, task["id"])


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued"])
def test_engineering_without_check_steps_reviews_each_green_pr_once(api, monkeypatch, queued):
    from backend import review_gate
    task, prs = engineering_pr_pair(api)
    snapshots = {p["id"]: {**H._json(p["detail"], {}), "at": H.now()} for p in prs}
    monkeypatch.setattr(review_gate, "snapshot", lambda service, links: snapshots)
    if queued:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runner_states SET last_seen_at=?", (H.shift(H.now(), minutes=-10),))
        P.retry(api.app.state.store, service=api.app.state.github_app)
        with api.app.state.store.transaction() as c:
            assert len(P.waiting(c, task["id"])) == 2
            c.execute("UPDATE runner_states SET last_seen_at=?", (H.now(),))
    filed = P.retry(api.app.state.store, service=api.app.state.github_app)
    assert len(filed) == 2
    reviews = children(api, task)
    assert len(reviews) == 2
    with api.app.state.store.read() as c:
        for pr in prs:
            [child] = [r for r in reviews if PR.matches(r, pr)]
            heads = [r[0] for r in c.execute("SELECT new FROM history_events WHERE subject_id=? AND kind='review_head'",
                                            (child["id"],))]
            assert heads == [H._json(pr["detail"], {})["head_sha"]]
    assert P.retry(api.app.state.store, service=api.app.state.github_app) == []
    assert {r["id"] for r in children(api, task)} == set(filed)
    assert all(not detail.get("error") for detail in snapshots.values())


@pytest.mark.parametrize("queued", [False, True], ids=["direct", "queued"])
def test_detached_review_gate_checks_its_own_pr_and_keeps_other_evidence(api, monkeypatch, queued):
    from backend import review_gate
    task, prs = engineering_pr_pair(api)
    snapshots = {p["id"]: {**H._json(p["detail"], {}), "at": H.now()} for p in prs}
    broken = next(p for p in prs if p["number"] == 13)
    snapshots[broken["id"]]["checks"] = {"required": "failing"}
    monkeypatch.setattr(review_gate, "snapshot", lambda service, links: snapshots)
    if queued:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runner_states SET last_seen_at=?", (H.shift(H.now(), minutes=-10),))
        P.retry(api.app.state.store, service=api.app.state.github_app)
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE runner_states SET last_seen_at=?", (H.now(),))
    P.retry(api.app.state.store, service=api.app.state.github_app)
    [child] = children(api, task)
    assert PR.matches(child, next(p for p in prs if p["number"] == 12))
    assert not PR.matches(child, broken)
    assert P.retry(api.app.state.store, service=api.app.state.github_app) == []
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused, match="CI failing"):
            H.task_create(c, "human:ana", "Review 1: PR #13", broken["url"], "bot:rev-b",
                          parent_id=task["id"], review_prs=snapshots)
    assert snapshots[broken["id"]]["checks"] == {"required": "failing"}


def test_legacy_number_only_review_is_bound_before_backfill_or_hand_filing(api):
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        body = f"Check PR #12 at head {HEAD}."
        legacy = H.task_create(c, "human:ana", "Review 1: PR #12", body, "bot:rev-a", parent_id=task["id"])
        c.execute("UPDATE tasks SET body=? WHERE id=?", (body, legacy["id"]))
    assert dispatch(api) == []
    [bound] = children(api, task)
    assert bound["id"] == legacy["id"]
    assert PR.matches(bound, "https://github.com/Acme/atlas/pull/12")
    assert dispatch(api) == []
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM history_events WHERE subject_id=? AND kind='review_pr'",
                         (bound["id"],)).fetchone()[0] == 1
        # A hand-filed check must bind old children before applying the same duplicate rule.
        c.execute("UPDATE tasks SET body=? WHERE id=?", (body, bound["id"]))
        with pytest.raises(H.Refused, match="already open"):
            H.task_create(c, "human:ana", "Review 1: PR #12", body, "bot:rev-b", parent_id=task["id"])


def test_ambiguous_legacy_number_is_logged_and_bound_review_is_reused(api, caplog):
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        first = H.task_links(c, task["id"])[0]
        body = f"Check PR #12 at head {HEAD}."
        legacy = H.task_create(c, "human:ana", "Review 1: PR #12", body, "bot:rev-a", parent_id=task["id"])
        c.execute("UPDATE tasks SET body=? WHERE id=?", (body, legacy["id"]))
        other = H.task_link(c, "human:ana", task["id"], "https://github.com/Acme/other/pull/12")
        c.execute("UPDATE task_links SET state='open',repo='Acme/other',checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (first["detail"], other["id"]))
        bound = H.task_create(c, "human:ana", "Review 1: PR #12", first["url"], "bot:rev-b", parent_id=task["id"])
    filed = dispatch(api)
    assert len(filed) == 1
    reviews = children(api, task)
    assert [r["id"] for r in reviews if PR.matches(r, first)] == [bound["id"]]
    assert [r["id"] for r in reviews if PR.matches(r, other)] == filed
    with api.app.state.store.read() as c:
        assert H.task(c, legacy["id"])["body"] == body
    assert any(record.name == "tico.review_gate" and legacy["id"] in record.message for record in caplog.records)
    assert dispatch(api) == []


@pytest.mark.parametrize("reviews", [1, 2], ids=["merge-next", "review-2-next"])
@pytest.mark.parametrize("case,head", [("backfill", HEAD), ("backfill-done", HEAD), ("hold", HEAD), ("old-head", OLD)])
def test_undelivered_approval_replays_once_at_current_head(api, monkeypatch, reviews, case, head):
    setup(api, reviews=reviews)
    task = task_pr(api)
    dispatch(api)
    [first] = children(api, task)
    if case == "hold":
        with api.app.state.store.transaction() as c:
            blocker = H.task_create(c, "human:ana", "Choose the review window", "Pick a time.", "human:ana")
            H.TR.add_blocker(c, "human:ana", task["id"], blocker["id"])
            H.task_update(c, "human:ana", task["id"], status="waiting", note="Wait for the owner.")
        finish(api, first, head=head)
    else:
        # A historical check has a settled attempt, but this route did not record its delivery.
        with monkeypatch.context() as patch:
            patch.setattr(V, "after_verdict", lambda *args: None)
            finish(api, first, head=head)
    with api.app.state.store.read() as c:
        assert not (V.delivery(c, H.task(c, first["id"])) or {}).get("consumed")
    assert not handoffs(api, task, "merge")
    if case == "backfill-done":
        with api.app.state.store.transaction() as c:
            H.task_update(c, H.KEEPER, task["id"], status="done", strict=False)
    if case == "hold":
        dispatch(api)
        assert children(api, task) == []
        with api.app.state.store.transaction() as c:
            H.task_update(c, "human:ana", blocker["id"], status="done", note="The window is set.")
        # Clearing the hold routes General checks immediately, without a board check step.
        assert bool(handoffs(api, task, "merge")) == (reviews == 1)
        if reviews == 2:
            assert children(api, task)[0]["title"] == "Review 2: PR #12"
    else:
        dispatch(api)
    expected_title = "Review 1: PR #12" if head == OLD else "Review 2: PR #12"
    active = children(api, task)
    assert [r["title"] for r in active] == ([expected_title] if head == OLD or reviews == 2 else [])
    assert len(handoffs(api, task, "merge")) == int(head == HEAD and reviews == 1)
    with api.app.state.store.transaction() as c:
        assert bool((V.delivery(c, H.task(c, first["id"])) or {}).get("consumed")) == (head == HEAD)
        V.resumed(c, task["id"])
        V.resumed(c, task["id"])
    dispatch(api)
    dispatch(api)
    assert {r["id"] for r in children(api, task)} == {r["id"] for r in active}
    assert len(handoffs(api, task, "merge")) == int(head == HEAD and reviews == 1)


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/Acme/atlas/pull/1", True),
    ("https://GITHUB.com/ACME/ATLAS/pull/1/?tab=files#review-12", True),
    ("https://github.com/Acme/atlas/pull/12", False),
    ("https://github.example/Acme/atlas/pull/1", False),
    ("https://github.com/Other/atlas/pull/1", False),
    ("https://github.com/Acme/other/pull/1", False),
    ("https://github.com/Acme/atlas/pull/1extra", False),
    ("", False),
])
def test_review_reuse_requires_the_full_identity_even_when_title_matches(url, expected):
    check = {"title": "Review 1: PR #1", "body": f"Check this PR: ({url})."}
    assert V.for_pr(check, {"url": "https://github.com/Acme/atlas/pull/1", "number": 1}) is expected


@pytest.mark.parametrize("other_url", [
    "https://github.com/Acme/other/pull/12",
    "https://github.com/Other/atlas/pull/12",
    "https://github.example/Acme/atlas/pull/12",
])
def test_queued_reviews_keep_equal_numbers_on_distinct_prs(api, other_url):
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        first = H.task_links(c, task["id"])[0]
        other = H.task_link(c, "human:ana", task["id"], other_url, kind="pr")
        identity = PR.identity(other_url)
        c.execute("UPDATE task_links SET state='open',repo=?,number=12,checks='passing',mergeable='clean',detail=? WHERE id=?",
                  (f"{identity[1]}/{identity[2]}", first["detail"], other["id"]))
        c.execute("UPDATE runner_states SET last_seen_at=?", (H.shift(H.now(), minutes=-10),))
    dispatch(api)
    dispatch(api)
    with api.app.state.store.read() as c:
        waits = P.waiting(c, task["id"])
        assert {w["pr_url"] for w in waits} == {first["url"], other_url}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET last_seen_at=?", (H.now(),))
    dispatch(api)
    reviews = children(api, task)
    assert len(reviews) == 2
    own = next(r for r in reviews if V.for_pr(r, first))
    other_review = next(r for r in reviews if V.for_pr(r, other))
    assert own["id"] != other_review["id"]
    finish(api, own)
    [notice] = handoffs(api, task, "merge")
    assert PR.matches(notice, first) and not PR.matches(notice, other)
    dispatch(api)
    assert [r["id"] for r in children(api, task)] == [other_review["id"]]


def test_hand_filed_review_with_equal_numbers_needs_a_full_link(api):
    setup(api)
    task = task_pr(api)
    with api.app.state.store.transaction() as c:
        other = H.task_link(c, "human:ana", task["id"], "https://github.com/Acme/other/pull/12")
        first = next(p for p in H.task_links(c, task["id"]) if PR.same(p, "https://github.com/Acme/atlas/pull/12"))
        c.execute("UPDATE task_links SET checks='passing',detail=? WHERE id=?", (first["detail"], other["id"]))
        with pytest.raises(H.Refused, match="full PR link"):
            H.task_create(c, "human:ana", "Review 1: PR #12", "Check the PR.", "bot:rev-a", parent_id=task["id"])
        own = H.task_create(c, "human:ana", "Review 1: PR #12", first["url"], "bot:rev-a", parent_id=task["id"])
        second = H.task_create(c, "human:ana", "Review 1: PR #12", other["url"], "bot:rev-b", parent_id=task["id"])
        with pytest.raises(H.Refused, match="already open"):
            H.task_create(c, "human:ana", "Review 1: PR #12", other["url"], "bot:rev-c", parent_id=task["id"])
    assert own["id"] != second["id"]


@pytest.mark.parametrize("owner", ["bot:cpo", "human:ana"])
def test_replayed_approval_keeps_the_fresh_ci_read(api, monkeypatch, owner):
    setup(api)
    task = task_pr(api, owner=owner)
    dispatch(api)
    [first] = children(api, task)
    with monkeypatch.context() as patch:
        patch.setattr(V, "after_verdict", lambda *args: None)
        finish(api, first)
    from backend import review_gate
    monkeypatch.setattr(review_gate, "snapshot", lambda service, prs: {
        p["id"]: {"head_sha": HEAD, "checks": {"required": "failing"}} for p in prs})
    P.retry_prs(api.app.state.store, service=api.app.state.github_app)
    assert not handoffs(api, task, "merge")
    with api.app.state.store.read() as c:
        assert V.delivery(c, H.task(c, first["id"]))["consumed"]
    monkeypatch.setattr(review_gate, "snapshot", lambda service, prs: {
        p["id"]: {"head_sha": HEAD, "checks": {"required": "passing"}} for p in prs})
    P.retry_prs(api.app.state.store, service=api.app.state.github_app)
    P.retry_prs(api.app.state.store, service=api.app.state.github_app)
    assert len(handoffs(api, task, "merge")) == 1


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
