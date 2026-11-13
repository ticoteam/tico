"""`tico/merge-ok`: the check only the server posts, so a repo ruleset that requires it lets no PR into the merge queue
(or merged) unless Tico's Merge stage queued it after the person sign-off (docs/pipelines-v3.md "GitHub")."""

import pytest

from backend import task_engine as E
from backend.hubdb import Refused
from backend.item_sources import github as MQ
from backend.item_sources import github as F
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github import api as hook_api  # noqa: F401 - a fixture: an app with the webhook secret
from backend.tests.helpers.github import hook
from backend.tests.helpers.github_facts_merge_queue import (
    REPO,
    RM,
    back,
    effects,
    in_merge,
    later,
    new,
    another_in_merge,
    engineering,
    seen,
    stage,
)

REFUSED = 'Required status check "tico/merge-ok" is expected.'


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(MQ, "GROUP_READ_WAITS", ())


def posted(gh):
    """Each check Tico posted: (sha, ok), oldest first."""
    return [(call[2], call[3]) for call in gh.calls if call[0] == "post_check"]


def test_the_merge_stage_posts_the_check_on_the_pinned_head_then_queues_it(company):
    gh = FakeGitHub()
    gh.require_merge_ok(REPO)
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    head = gh.prs[(REPO, 1)]["head_sha"]
    gh.calls.clear()
    assert MQ.run(company, gh) == 1
    assert [call[0] for call in gh.calls] == ["ready", "post_check", "enqueue"]
    assert posted(gh) == [(head, True)] and gh.merge_ok_on(REPO, head) is True
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    summary = gh.merge_ok[(REPO, head)][-1][1]
    assert task_id in summary and "Ship it" not in summary  # the task by id only: its title stays in Tico


def test_a_push_after_sign_off_has_no_check_until_a_person_queues_it_again(company):
    gh = FakeGitHub()
    gh.require_merge_ok(REPO)
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    signed = gh.prs[(REPO, 1)]["head_sha"]
    MQ.run(company, gh)
    pushed = gh.push(REPO, 1)  # leaves the queue, as on GitHub
    gh.set_checks(REPO, pushed, "success")
    F.refresh(company, gh, later())
    MQ.run(company, gh, later(20))
    assert gh.merge_ok_on(REPO, pushed) is None and all(sha != pushed for sha, _ in posted(gh))
    assert gh.enqueue(REPO, 1, pushed)["reason"] == REFUSED  # a bot queuing its new commit itself
    with company.transaction() as c:
        assert stage(c, task_id) == "Merge"
        version = E.task(c, task_id)["version"]
        with pytest.raises(Refused, match="Move the task back to QA"):  # C22: a bot may not queue a moved head
            MQ.requeue(c, task_id, RM, seen_version=version)
    with company.transaction() as c:
        MQ.requeue(c, task_id, X.ANA, seen_version=E.task(c, task_id)["version"])  # a person may
    MQ.run(company, gh, later(30))
    assert gh.merge_ok_on(REPO, pushed) is True and gh.prs[(REPO, 1)]["queue_state"] == "queued"
    assert gh.merge_ok_on(REPO, signed) is True  # the old head keeps its success: GitHub has no undo for it


def test_taking_the_pr_out_fails_the_check_on_its_head(company):
    gh = FakeGitHub()
    gh.require_merge_ok(REPO)
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    head = gh.prs[(REPO, 1)]["head_sha"]
    MQ.run(company, gh)
    with company.transaction() as c:
        back(c, task_id)
    gh.calls.clear()
    assert MQ.run(company, gh) == 1
    assert [call[0] for call in gh.calls] == ["dequeue", "post_check"]
    assert gh.merge_ok_on(REPO, head) is False
    assert gh.enqueue(REPO, 1, head)["reason"] == REFUSED  # nor can a bot put it back with Tico's old success


def test_a_check_that_fails_to_post_queues_nothing_and_is_tried_again(company):
    gh = FakeGitHub()
    gh.require_merge_ok(REPO)
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    gh.fail_next("post_check")
    gh.calls.clear()
    assert MQ.run(company, gh) == 0
    assert "enqueue" not in [call[0] for call in gh.calls]
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "owed")]
    assert MQ.run(company, gh, later(30)) == 1
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "done")]


def test_without_checks_write_nothing_is_posted_and_the_queue_goes_on(company):
    gh = FakeGitHub()
    gh.checks_write = False  # an App made before tico/merge-ok whose Owner has not accepted Checks: write
    with company.transaction() as c:
        in_merge(c, gh, 1)
    assert MQ.run(company, gh) == 1
    assert gh.merge_ok == {} and gh.prs[(REPO, 1)]["queue_state"] == "queued"


def test_without_checks_write_a_repo_that_requires_the_check_refuses_and_the_owner_hears(company):
    gh = FakeGitHub()
    gh.checks_write = False
    gh.require_merge_ok(REPO)
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    assert MQ.run(company, gh) == 1
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "failed")]
        assert [key for key, _ in X.wakes(c, task_id, RM)], "the owner is told the queue was refused"


# ----------------------------------------------------------------------------- the merge queue's group commit
def test_a_group_of_prs_tico_queued_passes(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        in_merge(c, gh, 1)
        another_in_merge(c, gh, 2)
    MQ.run(company, gh)
    payload = gh.group(REPO, 1, 2)
    assert MQ.answer_merge_group(company, gh, payload) == {"ok": True, "posted": True}
    assert gh.merge_ok_on(REPO, payload["merge_group"]["head_sha"]) is True


def test_a_group_asked_before_tico_recorded_its_enqueue_still_passes(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        assert effects(c, task_id) == [("act", "owed")]
    assert MQ.answer_merge_group(company, gh, gh.group(REPO, 1))["ok"] is True


def test_a_group_with_a_pr_no_tico_task_links_is_neutral_and_one_with_a_task_s_pr_tico_did_not_queue_fails(company):
    """A repo shared with people (ticoteam/tico, about 95 merges a day): a person's PR is not Tico's to pass or fail, so
    its group gets neutral, which GitHub counts as passing a required check. A Tico task's PR the Merge stage did not
    queue still fails its group."""
    gh = FakeGitHub()
    with company.transaction() as c:
        in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.set_checks(REPO, gh.open_pr(REPO, 3), "success")  # queued on GitHub by a person, no task links it
    payload = gh.group(REPO, 1, 3)
    assert MQ.answer_merge_group(company, gh, payload) == {"ok": None, "posted": True}
    head = payload["merge_group"]["head_sha"]
    assert gh.merge_ok_on(REPO, head) == "neutral" and "acme/app#3" in gh.merge_ok[(REPO, head)][-1][1]
    alone = gh.group(REPO, 3)
    assert MQ.answer_merge_group(company, gh, alone)["ok"] is None
    with company.transaction() as c:
        new(c, 4)  # a Tico task's PR, still in Build: queued around the Merge stage
    gh.set_checks(REPO, gh.open_pr(REPO, 4), "success")
    payload = gh.group(REPO, 1, 3, 4)
    assert MQ.answer_merge_group(company, gh, payload) == {"ok": False, "posted": True}
    said = gh.merge_ok[(REPO, payload["merge_group"]["head_sha"])][-1][1]
    assert "acme/app#4" in said and "acme/app#3" not in said


def test_a_group_whose_pr_moved_on_or_left_merge_fails(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.prs[(REPO, 1)]["head_sha"] = gh.sha()  # a head Tico never queued
    assert MQ.answer_merge_group(company, gh, gh.group(REPO, 1))["ok"] is False
    gh.prs[(REPO, 1)]["head_sha"] = gh.sha()
    with company.transaction() as c:
        back(c, task_id)
    assert MQ.answer_merge_group(company, gh, gh.group(REPO, 1))["ok"] is False


def test_a_group_tico_cannot_read_fails_closed(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.fail_next("merge_group_prs")
    assert MQ.answer_merge_group(company, gh, gh.group(REPO, 1)) == {"ok": False, "posted": True}
    unknown = gh.group(REPO, 1)
    gh.groups.clear()  # GitHub lists no such group
    assert MQ.answer_merge_group(company, gh, unknown) == {"ok": False, "posted": True}
    gh.fail_next("post_check")
    assert MQ.answer_merge_group(company, gh, gh.group(REPO, 1)) == {"ok": True, "posted": False}
    assert MQ.answer_merge_group(company, gh, {**gh.group(REPO, 1), "action": "destroyed"}) == {}


def test_the_merge_group_webhook_answers_tico_merge_ok(hook_api, monkeypatch):  # noqa: F811
    from backend import github as G

    gh = FakeGitHub()
    gh.open_pr(REPO, 1)
    monkeypatch.setattr(G, "AppGitHub", lambda service: gh)
    payload = gh.group(REPO, 1)
    answer = hook(hook_api, "merge_group", payload)
    assert answer == {"ok": True, "merge_ok": None, "posted": True}
    assert gh.merge_ok_on(REPO, payload["merge_group"]["head_sha"]) == "neutral"  # no task links it


# ----------------------------------------------------------------------------- AppGitHub: the calls GitHub sees
class Service:
    """The GitHub App service AppGitHub calls: live permissions, the tokens it mints, and GitHub's answers."""

    def __init__(self, permissions, answer=None):
        self.permissions, self.answer, self.calls, self.minted = permissions, answer, [], []

    def live_permissions(self):
        return self.permissions

    def mint_checks(self, repo):
        self.minted.append(("checks", repo))
        return "ghs_checks"

    def mint(self, repos, permissions):
        self.minted.append((tuple(repos), dict(permissions)))
        return "ghs_read", None

    def _call(self, method, path, headers=None, json=None, params=None):
        import httpx

        self.calls.append((method, path, headers, json))
        status, body = self.answer or (201, {})
        return httpx.Response(status, json=body)


def test_app_github_posts_a_completed_check_run_with_the_server_s_checks_token():
    from backend import github as G

    service = Service({"checks": "write"})
    assert G.AppGitHub(service).post_check(REPO, "a" * 40, False, "Taken out") is True
    method, path, headers, body = service.calls[-1]
    assert (method, path, headers["Authorization"]) == ("POST", "/repos/acme/app/check-runs", "Bearer ghs_checks")
    assert body["name"] == "tico/merge-ok" and body["head_sha"] == "a" * 40
    assert (body["status"], body["conclusion"]) == ("completed", "failure")
    assert G.AppGitHub(service).post_check(REPO, "b" * 40, None, "Not Tico tasks' PRs") is True
    assert service.calls[-1][3]["conclusion"] == "neutral"  # passes a required check on GitHub
    assert G.AppGitHub(Service({"checks": "read"})).post_check(REPO, "a" * 40, True, "") is False
    with pytest.raises(G.GitHubRefused):
        G.AppGitHub(Service(None)).post_check(REPO, "a" * 40, True, "")
    with pytest.raises(G.GitHubRefused):
        G.AppGitHub(Service({"checks": "write"}, (422, {}))).post_check(REPO, "a" * 40, True, "")


def test_app_github_reads_a_group_as_its_entry_and_every_one_ahead_of_it():
    from backend import github as G

    nodes = [
        {"position": 2, "headCommit": {"oid": "G2"}, "pullRequest": {"number": 7, "headRefOid": "H7"}},
        {"position": 1, "headCommit": {"oid": "G1"}, "pullRequest": {"number": 5, "headRefOid": "H5"}},
        {"position": 3, "headCommit": {"oid": "G3"}, "pullRequest": {"number": 9, "headRefOid": "H9"}},
    ]
    service = Service({}, (200, {"data": {"repository": {"mergeQueue": {"entries": {"nodes": nodes}}}}}))
    github = G.AppGitHub(service)
    assert github.merge_group_prs(REPO, "refs/heads/main", "g2") == [
        {"number": 5, "head_sha": "h5"},
        {"number": 7, "head_sha": "h7"},
    ]
    assert service.calls[-1][3]["variables"] == {"owner": "acme", "name": "app", "branch": "main"}
    assert "write" not in service.minted[-1][1].values()
    assert github.merge_group_prs(REPO, "refs/heads/main", "elsewhere") == []
