"""Track C of the Product and Engineering pipelines: GitHub's facts (webhooks, the 5-minute re-read), the four GitHub
standings, and the Merge stage's queue (docs/pipelines-v3.md "GitHub"; pe-contract sections 2, 3 and 7). GitHub is
backend/tests/helpers/fake_github.py; nothing here reaches the real one."""

import hashlib
import hmac
import json

import pytest

from backend import heartbeat as HB
from backend import merge_queue as MQ
from backend import never_stuck as NS
from backend import task_engine as E
from backend import task_facts as F
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github import SECRET
from backend.tests.helpers.github import api as hook_api  # noqa: F401 - a fixture: an app with the webhook secret

REPO = "acme/app"
REF = "acme/app#1"
B, RM, EM = "bot:builder", "bot:release", "bot:eng-manager"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def engineering(c, release=None):
    """Build (PR open, Checks pass) -> Merge (queues PRs; PR merged) -> Release (In a release), managed by EM."""
    for slug in ("builder", "release", "eng-manager"):
        X.bot(c, slug)
    return X.pipeline(
        c,
        "eng",
        "Engineering",
        stages=(
            {"name": "Build", "owner": B, "requirements": [{"kind": "pr_open"}, {"kind": "checks_pass"}]},
            {"name": "Merge", "owner": RM, "on_enter": {"queue_prs": True}, "requirements": [{"kind": "pr_merged"}]},
            {"name": "Release", "owner": RM, "requirements": [{"kind": "in_release", "settings": release or {}}]},
        ),
        manager=EM,
    )


def new(c, *numbers):
    task = X.new_task(c, X.ANA, "Ship it", "eng")
    for number in numbers:
        H.task_link(c, X.ANA, task["id"], f"https://github.com/{REPO}/pull/{number}")
    return task["id"]


def seen(c, gh, number=1):
    """What GitHub says of a PR now, saved as a fact the way the re-read saves it."""
    return F.save_and_settle(c, REPO, number, F.read_pr(gh, REPO, number), F.version_now())


def stand(c, task_id, kind):
    row = E.task(c, task_id)
    return next(s for s in R.standings(c, row) if s.requirement["kind"] == kind)


def line(c, task_id, kind):
    s = stand(c, task_id, kind)
    return (s.state, s.on, s.line)


def stage(c, task_id):
    return E.task(c, task_id)["stage"]["name"]


def effects(c, task_id):
    return [
        (r[0], r[1])
        for r in c.execute(
            "SELECT e.kind, e.state FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=? "
            "ORDER BY e.created_at, e.rowid",
            (task_id,),
        )
    ]


def in_merge(c, gh, *numbers):
    """A task whose PRs are open with green checks: it moves from Build to Merge, which owes their queue_pr."""
    engineering(c)
    task_id = new(c, *numbers)
    for number in numbers:
        gh.set_checks(REPO, gh.open_pr(REPO, number), "success")
        seen(c, gh, number)
    assert stage(c, task_id) == "Merge"
    return task_id


def back(c, task_id):
    """Back to Build with a check failing, so Build holds it; pulling the Merge visit again owes nothing more."""
    merge = E.task(c, task_id)
    F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "failure"}]}, F.version_now())
    E.move(c, task_id, "Build", X.ANA, "Needs another look")
    owed = effects(c, task_id)
    assert MQ.pull(c, merge, X.ANA) == [] and effects(c, task_id) == owed


def later(minutes=10):
    return H.shift(H.now(), minutes=minutes)


def refused(fn, rule):
    with pytest.raises(H.Refused) as raised:
        fn()
    assert raised.value.rule == rule, raised.value
    return raised.value.detail


# ----------------------------------------------------------------------------- standings
def test_pr_open_says_what_is_missing_and_a_closed_pr_counts_only_alone(c):
    engineering(c)
    task_id = new(c)
    assert line(c, task_id, "pr_open") == (R.NOT_MET, B, "No PR linked")
    H.task_link(c, X.ANA, task_id, f"https://github.com/{REPO}/pull/1")
    assert line(c, task_id, "pr_open") == (R.WAITING, R.GITHUB, "Reading the PR from GitHub")
    F.save_pr(c, REPO, 1, {"state": "closed", "head_sha": "a"}, 1)
    assert line(c, task_id, "pr_open") == (R.NOT_MET, B, "acme/app#1 was closed without merging")
    H.task_link(c, X.ANA, task_id, f"https://github.com/{REPO}/pull/2")
    F.save_pr(c, REPO, 2, {"state": "open", "head_sha": "b"}, 1)
    assert line(c, task_id, "pr_open") == (R.MET, None, "PR open")


def test_checks_pass_reads_the_checks_at_the_newest_commit(c):
    engineering(c)
    task_id = new(c, 1)
    checks = [{"name": "unit", "state": "failure"}, {"name": "lint", "state": "success"}]
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": checks}, 1)
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "Checks failed on acme/app#1: unit")
    F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "pending"}]}, 2)
    assert line(c, task_id, "checks_pass") == (R.WAITING, R.GITHUB, "Checks running")
    c.execute("UPDATE pr_facts SET checks_started_at=?", (H.shift(H.now(), hours=-3),))
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "Checks still running after 2 hours")
    F.save_pr(c, REPO, 1, {"head_sha": "b"}, 3)  # a new commit: no checks known on it yet
    assert line(c, task_id, "checks_pass") == (R.WAITING, R.GITHUB, "Waiting for checks")
    c.execute("UPDATE pr_facts SET head_seen_at=?", (H.shift(H.now(), minutes=-11),))
    assert line(c, task_id, "checks_pass") == (R.MET, None, "No checks on this repo")  # C13
    F.save_pr(
        c,
        REPO,
        1,
        {"checks": [{"name": "unit", "state": "success"}, {"name": "t", "state": "failure", "tico": True}]},
        4,
    )
    assert line(c, task_id, "checks_pass") == (R.MET, None, "Checks pass")


def test_with_many_prs_the_first_unmet_in_link_order_gives_the_line(c):
    engineering(c)
    task_id = new(c, 1, 2)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": [{"name": "u", "state": "success"}]}, 1)
    F.save_pr(c, REPO, 2, {"state": "open", "head_sha": "b", "checks": [{"name": "e2e", "state": "failure"}]}, 1)
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "Checks failed on acme/app#2: e2e")


def test_in_release_waits_for_a_release_that_holds_the_merge(c):
    engineering(c)
    task_id = new(c, 1)
    row = E.task(c, task_id)
    requirement = c.execute("SELECT * FROM requirements WHERE kind='in_release'").fetchone()
    req = R.get(c, requirement[0])
    assert R.KINDS["in_release"](c, row, req, None).line == "Reading the PR from GitHub"
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a"}, 1)
    assert (R.KINDS["in_release"](c, row, req, None).state, R.KINDS["in_release"](c, row, req, None).line) == (
        R.WAITING,
        "Not merged yet",
    )
    F.save_pr(c, REPO, 1, {"state": "merged", "merged_sha": "m", "merged_at": H.now()}, 2)
    assert R.KINDS["in_release"](c, row, req, None).line == "Reading releases"
    F.saw_releases(c, REPO, 3)
    found = R.KINDS["in_release"](c, row, req, None)
    assert (found.state, found.line) == (R.MET, "This repo doesn't publish releases")  # C31
    F.save_release(c, REPO, {"tag": "v2.0.0-rc.1", "commit_sha": "r1", "published_at": H.now()}, 4)
    rc = F.releases(c, REPO, prereleases=True)[0]
    F.save_included(c, rc["id"], "m", True)
    assert R.KINDS["in_release"](c, row, req, None).line == "Waiting for a release of acme/app"  # a dash: pre-release
    found = R.KINDS["in_release"](c, row, {**req, "settings": {"prereleases": True}}, None)
    assert (found.state, found.line) == (R.MET, "In v2.0.0-rc.1")
    found = R.KINDS["in_release"](c, row, {**req, "settings": {"release_repo": "acme/server"}}, None)
    assert found.line == "Reading releases"  # the release repo's own releases, not the PR's


def test_pr_merged_off_the_merge_stage_is_the_owner_s(c):
    engineering(c)
    task_id = new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a"}, 1)
    row = E.task(c, task_id)
    req = R.get(c, c.execute("SELECT id FROM requirements WHERE kind='pr_merged'").fetchone()[0])
    found = R.KINDS["pr_merged"](c, row, req, None)
    assert (found.state, found.on, found.line) == (R.NOT_MET, B, "Not merged yet")


# ----------------------------------------------------------------------------- the merge queue
def test_entering_merge_owes_one_queue_per_pr_and_github_queues_it(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        assert effects(c, task_id) == [("queue_pr", "owed")]
        assert MQ.queue(c, E.task(c, task_id), E.SYSTEM) == []  # owed once per key
        assert line(c, task_id, "pr_merged") == (R.WAITING, R.GITHUB, "Queuing the PR")
    assert MQ.run(company, gh) == 1
    assert MQ.run(company, gh) == 0
    assert [call[0] for call in gh.calls].count("enqueue") == 1
    with company.transaction() as c:
        assert effects(c, task_id) == [("queue_pr", "done")]
        assert F.pr(c, REPO, 1)["queue_state"] == "queued"
        assert line(c, task_id, "pr_merged") == (R.WAITING, R.GITHUB, "In the merge queue")
        assert "pr_queued" in X.events(c, task_id)
    gh.merge(REPO, 1)
    gh.publish(REPO, "v1.0.0")
    assert F.refresh(company, gh, later()) >= 2
    with company.transaction() as c:
        assert stage(c, task_id) == "Done"  # merged, then in v1.0.0: nothing holds it


def test_a_pr_whose_fact_comes_late_is_queued_when_it_comes(c):
    engineering(c)
    E_ = c.execute  # noqa: N806
    task_id = new(c, 1)
    E_("UPDATE requirements SET can_push_past=1")
    E.move(c, task_id, "next", X.ANA, "Reviewed by hand")
    assert stage(c, task_id) == "Merge" and effects(c, task_id) == []
    gh = FakeGitHub()
    gh.open_pr(REPO, 1)
    seen(c, gh)
    assert effects(c, task_id) == [("queue_pr", "owed")]


def test_failed_calls_are_tried_three_times_then_github_refused_is_the_owner_s(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    now = H.now()
    gh.fail_next("enqueue")
    assert MQ.run(company, gh, now) == 0
    assert MQ.run(company, gh, H.shift(now, seconds=30)) == 0  # not due again yet
    assert [call[0] for call in gh.calls].count("enqueue") == 1
    gh.fail_next("enqueue")
    assert MQ.run(company, gh, H.shift(now, seconds=61)) == 0
    gh.fail_next("enqueue")
    assert MQ.run(company, gh, H.shift(now, seconds=400)) == 1
    with company.transaction() as c:
        assert effects(c, task_id) == [("queue_pr", "failed")]
        assert line(c, task_id, "pr_merged") == (R.NOT_MET, RM, "GitHub refused the merge: GitHub is unavailable")
        assert any(":queue_refused:" in key for key, _ in X.wakes(c, task_id, RM))


def test_github_saying_no_fails_the_effect_at_once(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    gh.push(REPO, 1)  # the head moved since Tico saw it
    assert MQ.run(company, gh) == 1
    with company.transaction() as c:
        assert line(c, task_id, "pr_merged") == (R.NOT_MET, RM, "GitHub refused the merge: The head moved")


def test_a_removal_wakes_the_owner_once_and_is_never_queued_again_by_tico(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.drop(REPO, 1, "Checks failed in the merge queue", check="e2e", url="https://ci/9")
    F.refresh(company, gh, later())
    F.refresh(company, gh, later(20))  # the same removal read again
    with company.transaction() as c:
        assert line(c, task_id, "pr_merged") == (
            R.NOT_MET,
            RM,
            "Dropped from the merge queue: Checks failed in the merge queue (e2e)",
        )
        woken = [key for key, _ in X.wakes(c, task_id, RM) if ":queue_removed:" in key]
        assert len(woken) == 1 and woken[0].startswith(f"{task_id}:")
        body = c.execute("SELECT body FROM messages WHERE wake_key=?", (woken[0],)).fetchone()[0]
        assert "e2e" in body and "https://ci/9" in body and f"tico task queue {task_id}" in body
        assert X.events(c, task_id).count("pr_queue_removed") == 1
        assert effects(c, task_id) == [("queue_pr", "done")]
    gh.push(REPO, 1)  # a fix pushed in Merge: the task stays in Merge and nothing queues it by itself
    F.refresh(company, gh, later(30))
    with company.transaction() as c:
        assert stage(c, task_id) == "Merge" and effects(c, task_id) == [("queue_pr", "done")]
        assert any(":changed_after_ok:" in key for key, _ in X.wakes(c, task_id, EM))


def test_the_third_removal_in_a_visit_goes_up_the_ladder_and_a_fourth_to_a_person(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)

    def drop(n, by=RM):
        gh.drop(REPO, 1, "The merge group's checks failed", check="e2e")
        F.refresh(company, gh, later(10 * n))
        with company.transaction() as c:
            visit = E.task(c, task_id)["visit"]
            told = [key for key, _ in X.wakes(c, task_id, RM) if ":queue_removed:" in key]
            out = (visit["level"], len(told), MQ.drops_in_visit(c, task_id, visit["id"]))
            if n < 4:
                MQ.requeue(c, task_id, by, seen_version=E.task(c, task_id)["version"])
        MQ.run(company, gh)
        return out

    assert drop(1) == ("owner", 1, 1)
    assert drop(2) == ("owner", 2, 2)
    assert drop(3, by=EM) == ("manager", 2, 3)  # the Release Manager is not told a third time: its manager has it
    with company.transaction() as c:
        assert any(":manager:1:" in key for key, _ in X.wakes(c, task_id, EM))
        assert stage(c, task_id) == "Merge" and F.pr(c, REPO, 1)["queue_state"] == "queued"  # EM queued it again
    assert drop(4) == ("person", 2, 4)
    with company.transaction() as c:
        assert F.pr(c, REPO, 1)["queue_state"] == "removed"  # Tico never queues it by itself
        moved_back = E.move(c, task_id, "Build", X.ANA, "Needs the Builder")
        E.move(c, task_id, "Merge", X.ANA, "Again", seen_version=moved_back["version"])
        assert MQ.drops_in_visit(c, task_id, E.task(c, task_id)["visit"]["id"]) == 0  # a new visit counts afresh


def test_queue_again_refuses_until_it_can_and_then_owes_a_fresh_queue(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        version = E.task(c, task_id)["version"]
        assert "Nothing was taken out" in refused(
            lambda: MQ.requeue(c, task_id, RM, seen_version=version), "task_requirement"
        )
    MQ.run(company, gh)
    gh.drop(REPO, 1, "It conflicts with its base branch")
    head = gh.push(REPO, 1)
    gh.set_checks(REPO, head, "pending")
    F.refresh(company, gh, later())
    with company.transaction() as c:
        version = E.task(c, task_id)["version"]
        go = lambda by=RM, v=version: MQ.requeue(c, task_id, by, seen_version=v)  # noqa: E731
        assert refused(go, "task_requirement") == "The checks on acme/app#1 are still running"
        F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "failure"}]}, F.version_now())
        assert refused(go, "task_requirement") == "Checks failed on acme/app#1: unit"
        F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "success"}]}, F.version_now())
        refused(lambda: go(v=version - 1), "task_changed")
        refused(lambda: go(by=B), "task_requirement")  # not the owner, the manager or a person
        owed = go()
        assert [(e["state"], e["head_sha"]) for e in owed] == [("owed", head)]
        assert effects(c, task_id) == [("queue_pr", "moot"), ("queue_pr", "owed")]
        assert line(c, task_id, "pr_merged") == (R.WAITING, R.GITHUB, "Queuing the PR")
        assert E.task(c, task_id)["version"] == version + 1
    MQ.run(company, gh)
    with company.transaction() as c:
        assert F.pr(c, REPO, 1)["queue_state"] == "queued"
        assert line(c, task_id, "pr_merged") == (R.WAITING, R.GITHUB, "In the merge queue")


def test_queue_again_only_on_a_stage_that_queues_and_an_open_task(c):
    engineering(c)
    task_id = new(c, 1)
    assert refused(lambda: MQ.requeue(c, task_id, X.ANA), "task_stage") == "Build doesn't queue PRs"
    E.move(c, task_id, "closed", X.ANA, "Not needed")
    refused(lambda: MQ.requeue(c, task_id, X.ANA), "task_ended")


def test_moving_back_takes_the_pr_out_of_the_queue_once(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    with company.transaction() as c:
        back(c, task_id)
        assert effects(c, task_id) == [("queue_pr", "done"), ("dequeue_pr", "owed")]
    assert MQ.run(company, gh) == 1
    with company.transaction() as c:
        assert effects(c, task_id)[-1] == ("dequeue_pr", "done")
        assert F.pr(c, REPO, 1)["queue_state"] == "none"
        assert "pr_dequeued" in X.events(c, task_id)
        assert not [b for b in NS.rules_hold(c) if b.startswith("5:")]


def test_closing_before_github_queued_it_makes_the_queue_moot(c):
    gh = FakeGitHub()
    task_id = in_merge(c, gh, 1)
    E.move(c, task_id, "closed", X.ANA, "Not needed")
    assert effects(c, task_id) == [("queue_pr", "moot")]
    assert not [b for b in NS.rules_hold(c) if b.startswith("5:")]


def test_a_pr_github_merged_first_stays_merged_and_the_manager_hears_once(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.merge(REPO, 1)
    with company.transaction() as c:
        E.move(c, task_id, "closed", X.ANA, "Not needed")
    MQ.run(company, gh)
    with company.transaction() as c:
        assert [key for key, _ in X.wakes(c, task_id, EM) if ":merged_anyway:" in key] != []


def test_leaving_while_github_queues_it_takes_it_out_again(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    enqueue = gh.enqueue

    def slow(repo, number, head):
        with company.transaction() as c:  # the task moves back while the call is out
            back(c, task_id)
        return enqueue(repo, number, head)

    gh.enqueue = slow
    MQ.run(company, gh)
    with company.transaction() as c:
        assert effects(c, task_id) == [("queue_pr", "moot"), ("dequeue_pr", "owed")]
    MQ.run(company, gh)
    assert gh.prs[(REPO, 1)]["queue_state"] == "none"


# ----------------------------------------------------------------------------- facts
def test_a_github_outage_keeps_the_fact_and_waits_on_github(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        engineering(c)
        task_id = new(c, 1)
    gh.open_pr(REPO, 1)
    gh.fail_next("read_pr")
    assert F.refresh(company, gh) == 0
    with company.transaction() as c:
        assert F.pr(c, REPO, 1) is None
        assert line(c, task_id, "pr_open") == (R.WAITING, R.GITHUB, "Reading the PR from GitHub")
    assert F.refresh(company, gh) == 1
    assert F.refresh(company, gh) == 0  # read: not due again for 5 minutes
    with company.transaction() as c:
        assert line(c, task_id, "pr_open")[0] == R.MET


def test_webhooks_keep_only_linked_prs_and_each_check_on_the_head(c):
    engineering(c)
    task_id = new(c, 1)
    repo = {"full_name": REPO}
    pr = {"number": 1, "state": "open", "draft": False, "head": {"sha": "a"}}
    assert (
        F.webhook(c, "pull_request", {"action": "opened", "pull_request": {**pr, "number": 9}, "repository": repo})
        == []
    )
    assert F.webhook(c, "pull_request", {"action": "opened", "pull_request": pr, "repository": repo}) == [REF]
    run = {"head_sha": "a", "name": "unit", "status": "in_progress", "app": {"slug": "ci"}}
    F.webhook(c, "check_run", {"check_run": run, "repository": repo})
    assert line(c, task_id, "checks_pass") == (R.WAITING, R.GITHUB, "Checks running")
    F.webhook(
        c, "check_run", {"check_run": {**run, "status": "completed", "conclusion": "success"}, "repository": repo}
    )
    own = {"head_sha": "a", "name": "Tico", "status": "completed", "conclusion": "failure", "app": {"slug": "tico-app"}}
    F.webhook(c, "check_run", {"check_run": own, "repository": repo}, app_slug="tico-app")
    F.webhook(c, "status", {"sha": "a", "context": "ci/lint", "state": "success", "repository": repo})
    assert [ch["name"] for ch in F.pr(c, REPO, 1)["checks"]] == ["unit", "Tico", "ci/lint"]
    assert stage(c, task_id) == "Merge"  # checks pass (Tico's own check doesn't count)
    gone = {"action": "dequeued", "reason": "CI_FAILURE", "pull_request": pr, "repository": repo}
    F.webhook(c, "pull_request", gone)  # read in full on the next pass, with the check that failed
    assert F.pr(c, REPO, 1)["read_at"] == "" and F.pr(c, REPO, 1)["queue_state"] == "none"
    F.webhook(c, "release", {"action": "published", "release": {"tag_name": "v1.2.0"}, "repository": repo})
    assert [r["tag"] for r in F.releases(c, REPO)] == ["v1.2.0"]


def test_a_webhook_delivered_twice_is_taken_once(hook_api):
    def send(delivery):
        body = json.dumps({"zen": "x", "repository": {"full_name": REPO}}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        headers = {"X-Hub-Signature-256": sig, "X-GitHub-Event": "status", "X-GitHub-Delivery": delivery}
        return hook_api.post("/api/v2/github/webhook", content=body, headers=headers).json()

    assert "repeat" not in send("d-1")
    assert send("d-1") == {"ok": True, "repeat": True}


def test_queue_again_over_http(hook_api):
    store = hook_api.app.state.store
    gh = FakeGitHub()
    with store.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(store, gh)
    gh.drop(REPO, 1)
    F.refresh(store, gh, later())
    headers = {"Authorization": "Bearer ana-test", "Idempotency-Key": "q-1"}
    r = hook_api.post(f"/api/v2/tasks/{task_id}/queue", json={}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == [{"repo": REPO, "number": 1, "head": gh.prs[(REPO, 1)]["head_sha"]}]
    r = hook_api.post(f"/api/v2/tasks/{task_id}/queue", json={}, headers={**headers, "Idempotency-Key": "q-2"})
    assert r.status_code == 409 and r.json()["error"] == "task_requirement", r.text


def test_the_heartbeat_steps_are_quiet_without_a_github_app():
    class Ctx:
        github_app = None

    assert HB.pr_facts(Ctx()) == ("No GitHub app", True)
    assert HB.merge_queue_effects(Ctx()) == ("No GitHub app", True)
