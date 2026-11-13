"""A PR that conflicts with its base branch is never put in the merge queue (docs/pipelines-v3.md, Merge): the queue
reads GitHub's `mergeable` first, a queue-again is refused while the PR's link says it conflicts, and every refusal sends
the task back to Fixes 2 for the Builder to rebase."""

import pytest

from backend import task_engine as E
from backend import task_requirements as R
from backend.item_sources import github as MQ
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import REPO, RM, effects, in_merge, later, stage

REF = "acme/app#1"


def line(c, task_id, kind):
    row = E.task(c, task_id)
    s = next(s for s in R.standings(c, row) if (s.requirement["condition"] or s.requirement["kind"]) == kind)
    return (s.state, s.on, s.line)


def refused(fn, rule):
    with pytest.raises(H.Refused) as raised:
        fn()
    assert raised.value.rule == rule, raised.value
    return raised.value.detail


def conflict_link(c, task_id, mergeable):
    """What backend/github.py keeps on the task's PR link from GitHub: `conflict`, `clean` or `unknown`."""
    c.execute("UPDATE task_links SET state='open', mergeable=? WHERE task_id=? AND kind='pr'", (mergeable, task_id))


def test_a_conflicting_pr_is_never_queued_and_goes_back_to_fixes_2(company):
    """Acme drill: eight PRs edited one line, the Release Owner queued them all, and GitHub showed all but one
    UNMERGEABLE. A PR GitHub says conflicts is not queued (by the stage nor by a queue-again); while GitHub is still
    working it out, the queue waits and reads it again."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    now = H.now()
    gh.mergeable(REPO, 1, "UNKNOWN")  # GitHub has not worked it out yet: not queued on a guess, read again
    assert MQ.run(company, gh, now) == 0
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "owed")]
    gh.mergeable(REPO, 1, "CONFLICTING")
    assert MQ.run(company, gh, H.shift(now, seconds=61)) == 1
    assert [call[0] for call in gh.calls].count("enqueue") == 2 and gh.prs[(REPO, 1)]["queue_state"] == "none"
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "failed")]
        assert line(c, task_id, "pr_merged") == (R.NOT_MET, RM, f"GitHub refused the merge: {MQ.CONFLICTS}")
        (key,) = [key for key, _ in X.wakes(c, task_id, RM) if ":queue_refused:" in key]
        body = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
        assert "Fixes 2" in body and "Queue it again" not in body
        # The Release Owner (or anyone) asking to queue it as it is gets the same send-back while it conflicts.
        conflict_link(c, task_id, "conflict")
        sent_back = MQ.SENT_BACK_FOR_CONFLICT.format(ref=REF, task=task_id)
        for who in (RM, X.ANA):
            version = E.task(c, task_id)["version"]
            assert refused(lambda: MQ.requeue(c, task_id, who, seen_version=version), "task_requirement") == sent_back
        assert stage(c, task_id) == "Merge"
        # GitHub says it merges cleanly again (the PR it clashed with was reverted): queued again at the same head.
        conflict_link(c, task_id, "clean")
        gh.mergeable(REPO, 1, "MERGEABLE")
        owed = MQ.requeue(c, task_id, RM, seen_version=E.task(c, task_id)["version"])
        assert [e["state"] for e in owed] == ["owed"]
    MQ.run(company, gh, H.shift(now, seconds=120))
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"


def test_the_app_reads_mergeable_before_it_queues():
    """backend/github.py AppGitHub.enqueue on GitHub's answers: CONFLICTING or DIRTY is a no with CONFLICTS and no
    enqueue mutation, UNKNOWN a failed call (read again later), MERGEABLE queued."""
    from backend.github import AppGitHub, FACT_ENQUEUE, GitHubRefused

    app = AppGitHub(service=None)
    head = "a" * 40
    sent = []

    def answer(**pr):
        found = {"id": "PR_1", "state": "OPEN", "headRefOid": head, "isMergeQueueEnabled": True, **pr}
        app._pull = lambda repo, number, permissions: ({}, found)

    def graphql(repo, permissions, query, variables):
        sent.append(query)
        return {}, ""

    app._graphql = graphql
    answer(mergeable="CONFLICTING", mergeStateStatus="DIRTY")
    assert app.enqueue(REPO, 1, head) == {"ok": False, "how": None, "reason": MQ.CONFLICTS}
    answer(mergeable="MERGEABLE", mergeStateStatus="DIRTY")
    assert app.enqueue(REPO, 1, head)["reason"] == MQ.CONFLICTS
    answer(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
    with pytest.raises(GitHubRefused, match=MQ.MERGEABLE_UNKNOWN):
        app.enqueue(REPO, 1, head)
    assert sent == []
    answer(mergeable="MERGEABLE", mergeStateStatus="BLOCKED")
    assert app.enqueue(REPO, 1, head) == {"ok": True, "how": "queued", "reason": ""}
    assert sent == [FACT_ENQUEUE]


def test_a_removal_for_a_conflict_sends_the_task_back_not_into_the_queue(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.drop(REPO, 1, MQ.CONFLICTS)
    F.refresh(company, gh, later())
    with company.transaction() as c:
        (key,) = [key for key, _ in X.wakes(c, task_id, RM) if ":queue_removed:" in key]
        body = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
        assert "Fixes 2" in body and "rebase" in body and "Queue it again" not in body
