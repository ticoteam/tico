"""Track C of the Product and Engineering pipelines: GitHub's facts (webhooks, the 5-minute re-read), the four GitHub
standings, and the Merge stage's queue (docs/pipelines-v3.md "GitHub"; pe-contract sections 2, 3 and 7). GitHub is
backend/tests/helpers/fake_github.py; nothing here reaches the real one."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from backend import github as G
from backend import heartbeat as HB
from backend.item_sources import github as MQ
from backend import never_stuck as NS
from backend import task_engine as E
from backend.item_sources import github as F
from backend import task_requirements as R
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import (
    EM,
    REPO,
    RM,
    B,
    another_in_merge,
    back,
    effects,
    engineering,
    in_merge,
    later,
    new,
    seen,
    stage,
)
from backend.tests.helpers.github import SECRET
from backend.tests.helpers.github import api as hook_api  # noqa: F401 - a fixture: an app with the webhook secret

REF = "acme/app#1"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def stand(c, task_id, kind):
    row = E.task(c, task_id)
    return next(s for s in R.standings(c, row) if (s.requirement["condition"] or s.requirement["kind"]) == kind)


def line(c, task_id, kind):
    s = stand(c, task_id, kind)
    return (s.state, s.on, s.line)


def age(c, field, at, number=None):
    """Move one time of the saved PR metadata back (every PR, or one): `read_at` and `updated_at` are the item's,
    the others its fields."""
    where = "source='github' AND item_type='pr'" + (" AND item_key LIKE ?" if number else "")
    args = (f"%#{number}",) if number else ()
    if field in ("read_at", "updated_at"):
        c.execute(f"UPDATE item_metadata SET {field}=? WHERE {where}", (at, *args))
    else:
        c.execute(f"UPDATE item_metadata SET fields=json_set(fields, '$.{field}', ?) WHERE {where}", (at, *args))


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
    assert line(c, task_id, "pr_open") == (R.WAITING, "github", "Reading the PR from GitHub")
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
    assert line(c, task_id, "checks_pass") == (R.WAITING, "github", "Checks running")
    age(c, "checks_started_at", H.shift(H.now(), hours=-3))
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "Checks still running after 2 hours")
    F.save_pr(c, REPO, 1, {"head_sha": "b"}, 3)  # a new commit: no checks known on it yet
    assert line(c, task_id, "checks_pass") == (R.WAITING, "github", "Waiting for checks")
    age(c, "head_seen_at", H.shift(H.now(), minutes=-11))
    assert line(c, task_id, "checks_pass") == (R.MET, None, "No checks on this repo")  # C13
    F.save_pr(
        c,
        REPO,
        1,
        {"checks": [{"name": "unit", "state": "success"}, {"name": "t", "state": "failure", "tico": True}]},
        4,
    )
    assert line(c, task_id, "checks_pass") == (R.MET, None, "Checks pass")


def test_no_checks_counts_as_met_only_on_a_draft_or_a_repo_that_runs_none(c):
    """C13 is for a repo with no CI. On a repo whose PRs have run checks, a ready PR with none (an Actions outage, a
    `[skip ci]` push) waits for them, then is the owner's turn: never a pass with no CI."""
    engineering(c)
    new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "merged", "head_sha": "a", "merged_sha": "m", "checks": PASSING}, 1)  # ran checks
    task_id = new(c, 2)
    F.save_pr(c, REPO, 2, {"state": "open", "draft": True, "head_sha": "b"}, 1)
    age(c, "head_seen_at", H.shift(H.now(), minutes=-11), 2)
    assert line(c, task_id, "checks_pass") == (R.MET, None, "No checks on this repo")  # a draft runs nothing
    F.save_pr(c, REPO, 2, {"draft": False}, 2)  # marked ready: its checks start now
    assert line(c, task_id, "checks_pass") == (R.WAITING, "github", "Waiting for checks")
    age(c, "head_seen_at", H.shift(H.now(), hours=-3), 2)
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "No checks on acme/app#2 after 2 hours")
    other = X.new_task(c, X.ANA, "Ship it elsewhere", "eng")["id"]  # a repo none of whose PRs ran checks
    H.task_link(c, X.ANA, other, "https://github.com/acme/no_ci/pull/7")
    F.save_pr(c, "acme/no_ci", 7, {"state": "open", "head_sha": "c"}, 1)
    age(c, "head_seen_at", H.shift(H.now(), minutes=-11), 7)
    assert line(c, other, "checks_pass") == (R.MET, None, "No checks on this repo")
    assert not F.runs_checks(c, "acme/no%ci") and F.runs_checks(c, "ACME/app")


PASSING = [{"name": "checks", "state": "success"}]


def read(c, gh, number=1):
    """GitHub's PR and the checks at its head, saved as the re-read saves them, without moving the task on."""
    F.save_pr(c, REPO, number, F.read_pr(gh, REPO, number), F.version_now())


FAILED = (R.NOT_MET, B, "Checks failed on acme/app#1: checks")
PASSED = (R.MET, None, "Checks pass")
SKIPPED = (R.NOT_MET, B, "No checks ran on acme/app#1: every check was skipped")


@pytest.mark.parametrize("first", ["success", "failure"])
def test_two_checks_with_one_name_in_two_suites_fail_when_either_is_red(c, first):
    """ticoteam/pipeline-sandbox PR #15: two workflows each make a check run named `checks`; GitHub blocks the PR on
    the red one whichever it lists first."""
    engineering(c)
    task_id = new(c, 1)
    gh = FakeGitHub()
    head = gh.open_pr(REPO, 1)
    gh.run(REPO, head, "checks", first, suite=1)
    gh.run(REPO, head, "checks", "failure" if first == "success" else "success", suite=2)
    read(c, gh)
    assert line(c, task_id, "checks_pass") == FAILED


@pytest.mark.parametrize(
    ("runs", "want"),
    [
        (("failure", "success"), PASSED),  # a re-run that went green
        (("success", "failure"), FAILED),  # a re-run that went red
        (("success", "pending"), (R.WAITING, "github", "Checks running")),
    ],
)
def test_a_re_run_in_the_same_suite_is_the_same_check_and_its_newest_run_counts(c, runs, want):
    engineering(c)
    task_id = new(c, 1)
    gh = FakeGitHub()
    head = gh.open_pr(REPO, 1)
    for state in runs:
        gh.run(REPO, head, "checks", state, suite=1)
    read(c, gh)
    assert line(c, task_id, "checks_pass") == want


@pytest.mark.parametrize(
    ("states", "want"),
    [
        (("skipped",), SKIPPED),
        (("success", "skipped"), PASSED),
        (("skipped", "failure"), (R.NOT_MET, B, "Checks failed on acme/app#1: check-2")),
        (("skipped", "pending"), (R.WAITING, "github", "Checks running")),
    ],
)
def test_a_skipped_check_fails_nothing_but_alone_it_is_not_passing(c, states, want):
    """ticoteam/pipeline-sandbox PR #11: GitHub counts a skipped check as passing; Tico wants one that ran and passed."""
    engineering(c)
    task_id = new(c, 1)
    gh = FakeGitHub()
    gh.set_checks(REPO, gh.open_pr(REPO, 1), *states)
    read(c, gh)
    assert line(c, task_id, "checks_pass") == want


def test_neutral_and_skipped_runs_read_as_skipped_and_a_tico_run_does_not_rescue_them(c):
    assert [F.check_state("completed", r) for r in ("success", "neutral", "skipped")] == [
        "success",
        "skipped",
        "skipped",
    ]
    engineering(c)
    task_id = new(c, 1)
    checks = [{"name": "lint", "state": "skipped"}, {"name": "t", "state": "success", "tico": True}]
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": checks}, 1)
    assert line(c, task_id, "checks_pass") == SKIPPED


CANCELLED = (
    R.NOT_MET,
    B,
    "Checks on acme/app#1 were cancelled, not failed: checks. Re-run them; there is no code to fix",
)


def test_a_cancelled_or_stale_run_is_not_a_failure_and_a_failed_or_timed_out_one_is():
    """Nov 12 stress run, I38: a run GitHub cancelled (a concurrency group's duplicate) read as a failed check."""
    states = ("cancelled", "stale", "failure", "timed_out", "action_required", "startup_failure")
    assert [F.check_state("completed", r) for r in states] == ["cancelled", "cancelled"] + ["failure"] * 4


@pytest.mark.parametrize(
    ("runs", "want"),
    [
        # A concurrency group cancels the older duplicate run; the newer one, in its own suite, is the check.
        ((("cancelled", 1), ("success", 2)), PASSED),
        ((("cancelled", 1), ("pending", 2)), (R.WAITING, "github", "Checks running")),
        ((("cancelled", 1), ("failure", 2)), FAILED),
        # The newest run was cancelled with none after it: say so, so the owner re-runs it instead of fixing code.
        ((("cancelled", 1),), CANCELLED),
        ((("success", 1), ("cancelled", 2)), CANCELLED),
        ((("stale", 1),), CANCELLED),
        # A red run of the same name in another suite is never hidden behind a newer cancel.
        ((("failure", 1), ("cancelled", 2)), FAILED),
        ((("timed_out", 1),), FAILED),
    ],
)
def test_a_cancelled_run_is_not_a_failed_check(c, runs, want):
    engineering(c)
    task_id = new(c, 1)
    gh = FakeGitHub()
    head = gh.open_pr(REPO, 1)
    for conclusion, suite in runs:
        gh.run(REPO, head, "checks", F.check_state("completed", conclusion), suite=suite)
    read(c, gh)
    assert line(c, task_id, "checks_pass") == want


def test_a_cancelled_run_wakes_its_owner_to_re_run_it_and_a_newer_run_supersedes_it(c, monkeypatch):
    engineering(c)
    new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": []}, 1)

    def hook(run_id, suite, conclusion):
        run = {
            "id": run_id,
            "name": "checks",
            "head_sha": "h1",
            "status": "completed",
            "conclusion": conclusion,
            "app": {"slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        F.webhook(c, "check_run", {"check_run": run, "repository": {"full_name": REPO}})
        return F.pr(c, REPO, 1)["checks_state"]

    assert hook(10, 1, "cancelled") == "cancelled"
    after = F.pr(c, REPO, 1)
    assert F.CHECKS_DONE.count("cancelled") == 1 and F.runs_checks(c, REPO)
    told: list[str] = []
    monkeypatch.setattr(F, "snooze_news", lambda conn, task_id, key, lead: told.append(lead) or True)
    assert F.checks_news(c, ["t"], after, None) == 1
    assert told == [
        "The checks on acme/app#1 were cancelled at h1, not failed: checks. Re-run them; there is no code to fix."
    ]
    assert hook(11, 2, "success") == "success"  # the newer run of `checks`, in its own suite
    assert hook(10, 1, "cancelled") == "success"  # the cancelled one, delivered again late, stays superseded
    assert [x["state"] for x in F.pr(c, REPO, 1)["checks"]] == ["success"]


def test_check_run_webhooks_keep_two_suites_apart_and_a_late_older_run_changes_nothing(c):
    engineering(c)
    new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": []}, 1)

    def hook(run_id, suite, conclusion):
        run = {
            "id": run_id,
            "name": "checks",
            "head_sha": "h1",
            "status": "completed",
            "conclusion": conclusion,
            "app": {"slug": "github-actions"},
            "check_suite": {"id": suite},
        }
        F.webhook(c, "check_run", {"check_run": run, "repository": {"full_name": REPO}})
        return F.pr(c, REPO, 1)["checks_state"]

    assert hook(10, 1, "success") == "success"
    assert hook(11, 2, "failure") == "failure"  # another workflow's `checks`, red
    assert hook(12, 1, "success") == "failure"  # suite 1 re-run green: suite 2 is still red
    assert hook(13, 2, "success") == "success"  # suite 2 re-run green
    assert hook(11, 2, "failure") == "success"  # suite 2's old red run, delivered late, is older than its re-run
    assert len(F.pr(c, REPO, 1)["checks"]) == 2


def test_the_app_reads_every_check_run_and_never_keeps_only_the_first_of_a_name():
    """backend/github.py AppGitHub.read_checks on GitHub's answer for sandbox PR #15: ci's green `checks` listed first,
    shadow's red `checks` second (two suites), plus a re-run in ci's suite and a commit status."""
    from backend.github import AppGitHub

    asked = []

    class Answer:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def run(run_id, suite, conclusion, slug="github-actions"):
        return {
            "id": run_id,
            "name": "checks",
            "status": "completed",
            "conclusion": conclusion,
            "app": {"slug": slug},
            "check_suite": {"id": suite},
            "html_url": f"https://github.com/acme/app/runs/{run_id}",
        }

    class Service:
        def row(self):
            return {"slug": "tico-app"}

        def mint(self, repos, permissions):
            return "token", None

        def _call(self, method, path, headers=None, params=None):
            asked.append((path, params))
            if path.endswith("/check-runs"):
                return Answer({"check_runs": [run(5, 1, "failure"), run(7, 1, "success"), run(6, 2, "failure")]})
            return Answer({"statuses": [{"id": 9, "context": "checks", "state": "success"}]})

    checks = AppGitHub(Service()).read_checks(REPO, "h1")
    assert asked[0][1]["filter"] == "latest"
    assert [(c["app"], c["suite"], c["state"]) for c in checks] == [
        ("github-actions", 1, "success"),
        ("github-actions", 2, "failure"),
        ("status", None, "success"),
    ]
    assert F._checks_state(checks) == "failure"


def test_with_many_prs_the_first_unmet_in_link_order_gives_the_line(c):
    engineering(c)
    task_id = new(c, 1, 2)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": [{"name": "u", "state": "success"}]}, 1)
    F.save_pr(c, REPO, 2, {"state": "open", "head_sha": "b", "checks": [{"name": "e2e", "state": "failure"}]}, 1)
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "Checks failed on acme/app#2: e2e")


def test_a_failed_pr_is_the_line_before_an_earlier_pr_whose_checks_still_run(c):
    engineering(c)
    task_id = new(c, 1, 2)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a", "checks": [{"name": "u", "state": "pending"}]}, 1)
    F.save_pr(c, REPO, 2, {"state": "open", "head_sha": "b", "checks": [{"name": "e2e", "state": "failure"}]}, 1)
    assert line(c, task_id, "checks_pass") == (R.NOT_MET, B, "Checks failed on acme/app#2: e2e")


def test_in_release_waits_for_a_release_that_holds_the_merge(c):
    engineering(c)
    task_id = new(c, 1)
    row = E.task(c, task_id)
    requirement = c.execute("SELECT * FROM requirements WHERE condition='in_release'").fetchone()
    req = R.get(c, requirement[0])
    assert R.KINDS["item"](c, row, req, None).line == "Reading the PR from GitHub"
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a"}, 1)
    assert (R.KINDS["item"](c, row, req, None).state, R.KINDS["item"](c, row, req, None).line) == (
        R.WAITING,
        "Not merged yet",
    )
    F.save_pr(c, REPO, 1, {"state": "merged", "merged_sha": "m", "merged_at": H.now()}, 2)
    assert R.KINDS["item"](c, row, req, None).line == "Reading releases"
    F.saw_releases(c, REPO, 3)
    found = R.KINDS["item"](c, row, req, None)
    assert (found.state, found.line) == (R.MET, "This repo doesn't publish releases")  # C31
    F.save_release(c, REPO, {"tag": "v2.0.0-rc.1", "commit_sha": "r1", "published_at": H.now()}, 4)
    rc = F.releases(c, REPO, prereleases=True)[0]
    F.save_included(c, rc["id"], "m", False)
    found = R.KINDS["item"](c, row, req, None)
    assert (found.state, found.line) == (R.WAITING, "Waiting for the next release of acme/app")
    F.save_included(c, rc["id"], "m", True)
    assert R.KINDS["item"](c, row, req, None).line == "In v2.0.0-rc.1"  # pre-releases count by default
    found = R.KINDS["item"](c, row, {**req, "settings": {"prereleases": True}}, None)
    assert (found.state, found.line) == (R.MET, "In v2.0.0-rc.1")
    found = R.KINDS["item"](c, row, {**req, "settings": {"release_repo": "acme/server"}}, None)
    assert found.line == "Reading releases"  # the release repo's own releases, not the PR's


def test_pr_merged_off_the_merge_stage_is_the_owner_s(c):
    engineering(c)
    task_id = new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "a"}, 1)
    row = E.task(c, task_id)
    req = R.get(c, c.execute("SELECT id FROM requirements WHERE condition='pr_merged'").fetchone()[0])
    found = R.KINDS["item"](c, row, req, None)
    assert (found.state, found.on, found.line) == (R.NOT_MET, B, "Not merged yet")


# ----------------------------------------------------------------------------- the merge queue
def test_entering_merge_owes_one_queue_per_pr_and_github_queues_it(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        assert effects(c, task_id) == [("act", "owed")]
        assert MQ.queue(c, E.task(c, task_id), E.SYSTEM) == []  # owed once per key
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "Queuing the PR")
    assert MQ.run(company, gh) == 1
    assert MQ.run(company, gh) == 0
    assert [call[0] for call in gh.calls].count("enqueue") == 1
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "done")]
        assert F.pr(c, REPO, 1)["queue_state"] == "queued"
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "In the merge queue")
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
    assert effects(c, task_id) == [("act", "owed")]


def test_a_draft_that_reaches_merge_is_marked_ready_and_queued(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        engineering(c)
        task_id = new(c, 1)
        gh.set_checks(REPO, gh.open_pr(REPO, 1, draft=True), "success")  # Tico opened it as a draft
        seen(c, gh)
        assert stage(c, task_id) == "Merge"
    assert MQ.run(company, gh) == 1
    assert [call[0] for call in gh.calls if call[0] in ("ready", "enqueue")] == ["ready", "enqueue"]
    assert gh.prs[(REPO, 1)]["draft"] is False and gh.prs[(REPO, 1)]["queue_state"] == "queued"
    with company.transaction() as c:
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "In the merge queue")


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
        assert effects(c, task_id) == [("act", "failed")]
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


def test_a_merge_the_branch_rules_refuse_is_told_once_and_not_tried_again(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    gh.rule(REPO, 1)  # a required approving review is missing
    now = H.now()
    assert MQ.run(company, gh, now) == 1
    for minutes in (2, 10, 30):
        assert MQ.run(company, gh, H.shift(now, minutes=minutes)) == 0
    assert [call[0] for call in gh.calls].count("enqueue") == 1  # a permanent refusal is not retried
    reason = "At least 1 approving review is required by reviewers with write access."
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "failed")]
        assert line(c, task_id, "pr_merged") == (R.NOT_MET, RM, f"GitHub refused the merge: {reason}")
        assert len([key for key, _ in X.wakes(c, task_id, RM) if ":queue_refused:" in key]) == 1


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
        assert "e2e" in body and "https://ci/9" in body and "QA" in body
        assert f"tico task queue {task_id}" not in body  # a check that really failed is not queued again as it is
        assert X.events(c, task_id).count("pr_queue_removed") == 1
        assert effects(c, task_id) == [("act", "done")]
    gh.push(REPO, 1)  # a fix pushed in Merge: the task stays in Merge and nothing queues it by itself
    F.refresh(company, gh, later(30))
    with company.transaction() as c:
        assert stage(c, task_id) == "Merge" and effects(c, task_id) == [("act", "done")]
        assert any(":changed_after_ok:" in key for key, _ in X.wakes(c, task_id, EM))


def test_a_drop_of_one_pr_is_the_line_while_another_pr_of_the_task_is_still_queued(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1, 2)
    MQ.run(company, gh)
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "In the merge queue")
    gh.push(REPO, 2)  # a new commit on the second-linked PR: GitHub takes it out; PR 1 stays queued
    F.refresh(company, gh, later(20))
    with company.transaction() as c:
        assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
        assert line(c, task_id, "pr_merged") == (
            R.NOT_MET,
            RM,
            "Dropped from the merge queue: A new commit was pushed",
        )
        assert W.turns(c, E.task(c, task_id)) == {RM}


def test_links_made_in_the_same_millisecond_keep_the_order_they_were_made_in(c):
    engineering(c)
    task_id = new(c, 9, 3)
    same = "SELECT MIN(created_at) FROM task_links WHERE task_id=?"
    c.execute(f"UPDATE task_links SET created_at=({same}) WHERE task_id=?", (task_id, task_id))
    # Ids are random: make the first link's sort after the second's, as a tie broken by id may do
    c.execute("UPDATE task_links SET id=CASE WHEN url LIKE '%/9' THEN 'z' ELSE 'a' END WHERE task_id=?", (task_id,))
    assert [pr["number"] for pr in F.prs_of(c, task_id)] == [9, 3]


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
    assert drop(3, by=EM) == ("manager", 2, 3)  # the Release Owner is not told a third time: its manager has it
    with company.transaction() as c:
        assert any(":manager:1:" in key for key, _ in X.wakes(c, task_id, EM))
        assert stage(c, task_id) == "Merge" and F.pr(c, REPO, 1)["queue_state"] == "queued"  # EM queued it again
    assert drop(4) == ("person", 2, 4)
    with company.transaction() as c:
        assert F.pr(c, REPO, 1)["queue_state"] == "removed"  # Tico never queues it by itself
        moved_back = E.move(c, task_id, "Build", X.ANA, "Needs the Builder")
        E.move(c, task_id, "Merge", X.ANA, "Again", seen_version=moved_back["version"])
        assert MQ.drops_in_visit(c, task_id, E.task(c, task_id)["visit"]["id"]) == 0  # a new visit counts afresh


QUEUE_FAILED = "Checks failed in the merge queue"
REAL_LEG = "browser-2: Run selected tests"


def queue_removal_bodies(c, task_id):
    keys = [key for key, _ in X.wakes(c, task_id, RM) if ":queue_removed:" in key]
    return [c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0] for key in keys]


def test_a_failed_checks_removal_that_names_its_leg_goes_to_qa_not_the_queue(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.drop(REPO, 1, QUEUE_FAILED, check=REAL_LEG, url="https://ci/1")
    F.refresh(company, gh, later(10))
    with company.transaction() as c:
        (first,) = queue_removal_bodies(c, task_id)
        assert REAL_LEG in first and "QA" in first and f"tico task queue {task_id}" not in first
        version = E.task(c, task_id)["version"]
        detail = refused(lambda: MQ.requeue(c, task_id, RM, seen_version=version), "task_requirement")
        assert REAL_LEG in detail and "QA" in detail
        assert stage(c, task_id) == "Merge"
    head = gh.push(REPO, 1)  # a fix pushed: a bot may not queue that commit, a person may
    F.refresh(company, gh, later(30))
    with company.transaction() as c:
        assert "QA" in refused(
            lambda: MQ.requeue(c, task_id, RM, seen_version=E.task(c, task_id)["version"]), "task_requirement"
        )
        owed = MQ.requeue(c, task_id, X.ANA, seen_version=E.task(c, task_id)["version"])
        assert [e["pinned"] for e in owed] == [head]


@pytest.mark.parametrize("check", [MQ.CANCELLED_FROM_OUTSIDE, None])
def test_an_outside_cancel_is_queued_once_at_a_head_and_a_second_removal_goes_to_qa(company, check):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.drop(REPO, 1, QUEUE_FAILED, check=check, url="https://ci/1")
    F.refresh(company, gh, later(10))
    with company.transaction() as c:
        (first,) = queue_removal_bodies(c, task_id)
        assert f"tico task queue {task_id}" in first
        MQ.requeue(c, task_id, RM, seen_version=E.task(c, task_id)["version"])  # once, after the outside cancel
    MQ.run(company, gh)
    gh.drop(REPO, 1, QUEUE_FAILED, check=check, url="https://ci/2")  # the same head is taken out again
    F.refresh(company, gh, later(20))
    with company.transaction() as c:
        later_bodies = queue_removal_bodies(c, task_id)
        assert len(later_bodies) == 2
        (second,) = [body for body in later_bodies if body != first]
        assert "QA" in second and "Queue it again" not in second
        version = E.task(c, task_id)["version"]
        assert "QA" in refused(lambda: MQ.requeue(c, task_id, RM, seen_version=version), "task_requirement")
        assert stage(c, task_id) == "Merge"


def job(name, conclusion, failed_steps=(), url=None):
    """One job of a merge queue run as the Actions API lists it: its steps, the failed ones given by name."""
    steps = [{"name": "Set up", "conclusion": "success"}] + [{"name": s, "conclusion": "failure"} for s in failed_steps]
    return {
        "name": name,
        "conclusion": conclusion,
        "html_url": url or f"https://ci/{name}",
        "check_run_url": "https://api.github.com/repos/acme/app/check-runs/77",
        "steps": steps,
    }


FAIL_FAST = [job("checks", "failure"), job("python-1", "cancelled"), job("browser-1", "cancelled")]


def test_a_removal_names_the_leg_that_failed_and_not_the_legs_a_fail_fast_cancelled():
    jobs = [
        FAIL_FAST[0],
        job("python-1", "cancelled"),
        job("browser-2", "failure", ["Run selected tests"], "https://ci/b2"),
    ]

    def no_line(_checks):
        raise AssertionError("the checks line is read only when no leg failed")

    assert MQ.failing_leg(jobs + [job("browser-1", "cancelled")], no_line) == (REAL_LEG, "https://ci/b2")


def test_every_failed_leg_is_named_with_its_failed_steps_and_a_timed_out_leg_counts():
    jobs = [
        job("checks", "failure"),
        job("python-1", "failure", ["pytest"]),
        job("browser-2", "failure", ["Run selected tests", "Upload"]),
        job("e2e", "timed_out"),
    ]
    assert MQ.failing_leg(jobs, lambda _checks: None) == (
        "python-1: pytest; browser-2: Run selected tests, Upload; e2e",
        "https://ci/python-1",
    )


def test_a_startup_failure_is_the_runners_not_a_failing_leg():
    runner = job("python-5", "startup_failure")
    checks = job("checks", "failure")
    assert MQ.failing_leg([checks, runner], lambda _checks: None) == (MQ.CANCELLED_FROM_OUTSIDE, "https://ci/checks")
    real = job("browser-2", "failure", ["Run selected tests"], "https://ci/b2")
    assert MQ.failing_leg([checks, runner, real], lambda _checks: None) == (REAL_LEG, "https://ci/b2")


@pytest.mark.parametrize(
    "line, want",
    [
        (
            "Failed: browser-2: Run selected tests. Fail fast then cancelled the other legs.",
            REAL_LEG,
        ),
        (
            "Failed: python-1: pytest; browser-2: Run selected tests. Fail fast then cancelled the other legs.",
            "python-1: pytest; browser-2: Run selected tests",
        ),
        (
            "No step failed: the run was cancelled from outside (a superseded queue group, or by hand).",
            MQ.CANCELLED_FROM_OUTSIDE,
        ),
        (None, MQ.CANCELLED_FROM_OUTSIDE),
    ],
)
def test_with_no_failed_leg_the_checks_line_names_it_or_the_run_was_cancelled_from_outside(line, want):
    assert MQ.failing_leg(FAIL_FAST, lambda _checks: line) == (want, "https://ci/checks")


def test_no_jobs_means_the_run_is_not_known():
    assert MQ.failing_leg([], lambda _checks: "Failed: x: y. Fail fast") is None


class Answers:
    """The GitHub App's HTTP service for `AppGitHub._queue_failure`: each path answers (status, json) and `asked` keeps
    the paths read."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def mint(self, repos, permissions):
        return "token", None

    def _call(self, method, path, **kwargs):
        self.asked.append(path)
        status, body = self.answers.get(path, (404, {}))
        return SimpleNamespace(status_code=status, json=lambda: body)


RUNS = "/repos/acme/app/actions/runs"
JOBS = "https://api.github.com/repos/acme/app/actions/runs/9/jobs"
ANNOTATIONS = "https://api.github.com/repos/acme/app/check-runs/77/annotations"


def queue_failure(jobs, notes=None, notes_status=200, runs=None):
    """(what `AppGitHub._queue_failure` names for PR 1, the paths it read) for a merge queue run with these jobs and the
    checks job's annotations."""
    if runs is None:
        runs = [
            {
                "head_branch": "gh-readonly-queue/main/pr-1-abc123",
                "created_at": "2026-11-10T10:00:00Z",
                "jobs_url": JOBS,
            }
        ]
    service = Answers(
        {
            RUNS: (200, {"workflow_runs": runs}),
            JOBS: (200, {"jobs": jobs}),
            ANNOTATIONS: (notes_status, [{"message": line} for line in notes or []]),
        }
    )
    return G.AppGitHub(service)._queue_failure("acme/app", 1, ""), service.asked


def test_the_app_names_the_failed_leg_and_reads_no_annotation():
    jobs = [
        job("checks", "failure"),
        job("python-1", "cancelled"),
        job("browser-2", "failure", ["Run selected tests"], "https://ci/b2"),
    ]
    named, asked = queue_failure(jobs)
    assert named == (REAL_LEG, "https://ci/b2")
    assert ANNOTATIONS not in asked


def test_the_app_reads_the_checks_line_only_when_no_leg_failed():
    jobs = [job("checks", "failure"), job("python-1", "cancelled")]
    named, asked = queue_failure(jobs, ["Failed: python-1: pytest. Fail fast then cancelled the other legs."])
    assert named == ("python-1: pytest", "https://ci/checks")
    assert ANNOTATIONS in asked


def test_the_app_says_cancelled_from_outside_when_the_checks_line_says_no_step_failed():
    jobs = [job("checks", "failure"), job("python-1", "cancelled")]
    named, _ = queue_failure(jobs, ["No step failed: the run was cancelled from outside (by hand)."])
    assert named == (MQ.CANCELLED_FROM_OUTSIDE, "https://ci/checks")


def test_the_app_still_names_the_run_when_its_annotations_cannot_be_read():
    jobs = [job("checks", "failure"), job("python-1", "cancelled")]
    named, _ = queue_failure(jobs, notes_status=500)
    assert named == (MQ.CANCELLED_FROM_OUTSIDE, "https://ci/checks")


def test_the_app_names_no_run_when_none_was_queued_for_the_pr():
    named, asked = queue_failure([], runs=[])
    assert named is None
    assert JOBS not in asked


def test_a_queue_entry_that_vanishes_without_a_word_is_a_drop(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.vanish(REPO, 1)  # no webhook, no removal event: only a read shows it is out
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert line(c, task_id, "pr_merged") == (R.NOT_MET, RM, "Dropped from the merge queue: GitHub gave no reason")
        assert len([key for key, _ in X.wakes(c, task_id, RM) if ":queue_removed:" in key]) == 1
        MQ.requeue(c, task_id, RM, seen_version=E.task(c, task_id)["version"])
    assert gh.prs[(REPO, 1)]["queue_state"] == "none"  # only the owner's queue again queues it


def test_a_wait_on_github_with_no_word_for_two_hours_is_the_owner_s(company):
    gh = FakeGitHub()
    hours_ago = H.shift(H.now(), hours=-3)
    with company.transaction() as c:
        merging = in_merge(c, gh, 2)
        building = new(c, 1)  # PR 1 was never read: GitHub has been down since it was linked
        c.execute("UPDATE task_links SET created_at=? WHERE task_id=?", (hours_ago, building))
        assert line(c, building, "pr_open") == (R.NOT_MET, B, "GitHub hasn't answered about acme/app#1 for 2 hours")
    MQ.run(company, gh)
    with company.transaction() as c:
        assert line(c, merging, "pr_merged") == (R.WAITING, "github", "In the merge queue")
        age(c, "read_at", hours_ago, 2)  # every read since failed
        assert line(c, merging, "pr_merged") == (R.NOT_MET, RM, "GitHub hasn't answered about acme/app#2 for 2 hours")
    gh.merge(REPO, 2)
    with company.transaction() as c:
        seen(c, gh, 2)
        assert stage(c, merging) == "Release"
        assert line(c, merging, "in_release") == (R.WAITING, "github", "Reading releases")
        age(c, "updated_at", hours_ago, 2)  # merged 3 hours ago, never read
        assert line(c, merging, "in_release")[:2] == (R.NOT_MET, RM)
        release = {"tag": "v1.0.0", "commit_sha": "0" * 40, "prerelease": False, "published_at": H.now()}
        F.save_release(c, REPO, release, F.version_now())
        F.saw_releases(c, REPO, F.version_now())  # the releases were read just now: GitHub answers again
        assert line(c, merging, "in_release") == (R.WAITING, "github", "Waiting for the next release of acme/app")
        c.execute("UPDATE github_release_reads SET read_at=?", (hours_ago,))
        assert line(c, merging, "in_release")[:2] == (R.NOT_MET, RM)


def hooked(c, action, gh, number=1, **over):
    """GitHub's pull_request webhook as the server takes it: the engine's facts first, then the link's own pass."""
    from backend import github as G

    pr = gh.prs[(REPO, number)]
    snapshot = {
        "number": number,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "state": "closed" if pr["state"] != "open" else "open",
        "merged": pr["state"] == "merged",
        "merge_commit_sha": pr["merged_sha"],
        "draft": pr["draft"],
        "head": {"sha": pr["head_sha"], "ref": "feature"},
    }
    payload = {"action": action, "pull_request": snapshot, "repository": {"full_name": REPO}, **over}
    F.webhook(c, "pull_request", payload)
    G.pull_request(c, payload)
    return snapshot


def told(c, task_id):
    """Every message the task's owner has about it: the engine's wakes and the older GitHub burst's items."""
    from backend.repositories import metadata

    burst = metadata(c, "github-task-wake:" + task_id).get("items", [])
    return [key for key, _ in X.wakes(c, task_id)] + burst


def test_one_message_per_removal_merge_and_close_on_a_queueing_pipeline(company):
    from backend import github as G

    gh = FakeGitHub()
    with company.transaction() as c:
        removal = in_merge(c, gh, 1)
    MQ.run(company, gh)
    with company.transaction() as c:
        before = told(c, removal)
    gh.drop(REPO, 1, "failed_checks")
    with company.transaction() as c:
        pr = hooked(c, "dequeued", gh, reason="failed_checks")
        link = c.execute("SELECT id FROM task_links WHERE task_id=?", (removal,)).fetchone()[0]
    F.refresh(company, gh)
    with company.transaction() as c:
        G._settle_removal(c, link, pr)  # the older pass's read of the same removal
        added = [m for m in told(c, removal) if m not in before]
        assert len(added) == 1 and ":queue_removed:" in added[0], added

    with company.transaction() as c:
        merged = another_in_merge(c, gh, 2)
    MQ.run(company, gh)
    gh.merge(REPO, 2)
    with company.transaction() as c:
        before = told(c, merged)
        hooked(c, "closed", gh, 2)
        assert stage(c, merged) == "Release"
        added = [m for m in told(c, merged) if m not in before]
        # The Release Owner owns Release too and waits on GitHub there: no "carry this task on" wake on top.
        assert len(added) <= 1 and not any("Carry this task on" in m for m in added), added

    with company.transaction() as c:
        closed = another_in_merge(c, gh, 3)
    MQ.run(company, gh)
    gh.close(REPO, 3)
    with company.transaction() as c:
        before = told(c, closed)
        hooked(c, "closed", gh, 3)
        assert stage(c, closed) == "Merge"
        added = [m for m in told(c, closed) if m not in before]
        assert len(added) <= 1 and not any("closed without merging" in m for m in added), added


def owner_wakes(c, task_id, who):
    """(wake key, body) of every wake the engine wrote for the task to `who`, oldest first."""
    return [
        (str(key), str(body or ""))
        for key, body in c.execute(
            "SELECT wake_key, body FROM messages WHERE wake_key LIKE ? AND recipient_id=? ORDER BY created_at, rowid",
            (task_id + ":%", who),
        ).fetchall()
    ]


@pytest.mark.parametrize("where", ["Build", "Merge"])
def test_a_pr_closed_by_hand_is_the_stage_owner_s_turn_and_wakes_them_once(company, where):
    """Spec "The ladder": a GitHub wait that turns Not met is the owner's turn; the never-stuck check wakes them once
    with what is missing (after its first gap, as for a failed check), and not again before its next try."""
    gh = FakeGitHub()
    with company.transaction() as c:
        if where == "Merge":
            task_id, owner, kind = in_merge(c, gh, 1), RM, "pr_merged"
        else:
            engineering(c)
            task_id, owner, kind = new(c, 1), B, "pr_open"
            gh.set_checks(REPO, gh.open_pr(REPO, 1), "pending")
            seen(c, gh)
    MQ.run(company, gh)
    now = H.now()
    with company.transaction() as c:
        X.ran(c, task_id, owner)  # the Builder's entry wake had its run: it opened the PR, and checks are running
        NS.check(c, now)
        waiting = "pr_merged" if where == "Merge" else "checks_pass"
        assert stage(c, task_id) == where and line(c, task_id, waiting)[:2] == (R.WAITING, "github")
        before = owner_wakes(c, task_id, owner)
    gh.close(REPO, 1)
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert line(c, task_id, kind) == (R.NOT_MET, owner, f"{REF} was closed without merging")
        for seconds in (10, 20, 30, 150, 160, 600):  # the check's passes, every 10 s, then up to 10 minutes on
            NS.check(c, H.shift(now, seconds=seconds))
        added = [w for w in owner_wakes(c, task_id, owner) if w not in before]
        assert len(added) == 1, added
        assert f"{REF} was closed without merging" in added[0][1]
        from backend.repositories import metadata

        assert not metadata(c, "github-task-wake:" + task_id).get("items")  # and nothing from the older link pass


def test_a_merge_moves_the_task_to_release_where_github_s_wait_wakes_nobody(company):
    """Spec "Entering a stage" wakes the owner with what is missing, and "It only wakes whoever has the turn": a bot is
    never woken about a task that waits on GitHub. Release waits on GitHub for a release, so its owner hears nothing
    until it is their turn."""
    gh = FakeGitHub()
    gh.history[REPO] = [gh.sha()]
    gh.publish(REPO, "v0.9.0")  # the repo publishes releases; none holds the merge yet
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    now = H.now()
    with company.transaction() as c:
        NS.check(c, now)
        before = owner_wakes(c, task_id, RM)
    gh.merge(REPO, 1)
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert stage(c, task_id) == "Release"
        assert line(c, task_id, "in_release")[:2] == (R.WAITING, "github")
        for seconds in (10, 150, 600):
            NS.check(c, H.shift(now, seconds=seconds))
        assert stage(c, task_id) == "Release"
        assert owner_wakes(c, task_id, RM) == before
        from backend.repositories import metadata

        assert not metadata(c, "github-task-wake:" + task_id).get("items")
    gh.publish(REPO, "v1.0.0")
    F.refresh(company, gh, later(20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Done" and owner_wakes(c, task_id, RM) == before


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
        go = lambda by=X.ANA, v=version: MQ.requeue(c, task_id, by, seen_version=v)  # noqa: E731
        assert refused(go, "task_requirement") == "The checks on acme/app#1 are still running"
        F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "failure"}]}, F.version_now())
        assert refused(go, "task_requirement") == "Checks failed on acme/app#1: unit"
        F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "success"}]}, F.version_now())
        refused(lambda: go(by=RM, v=version - 1), "task_changed")
        refused(lambda: go(by=B), "task_requirement")  # not the owner, the manager or a person
        # The new commit changes other lines than the one the person OK'd: the Release Owner sends the task back to
        # QA, where Reviewer 2 reads them; it may not queue it as it is (an OK on an old commit never merges a
        # new one).
        assert refused(lambda: go(by=RM), "task_requirement") == MQ.SENT_BACK_FOR_NEW.format(ref=REF)
        assert refused(lambda: go(by=EM), "task_requirement") == MQ.SENT_BACK_FOR_NEW.format(ref=REF)
        owed = go()  # a person queues it as it is now: that is their OK of the new commit
        assert [(e["state"], e["pinned"]) for e in owed] == [("owed", head)]
        assert effects(c, task_id) == [("act", "moot"), ("act", "owed")]
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "Queuing the PR")
        assert E.task(c, task_id)["version"] == version + 1
    MQ.run(company, gh)
    with company.transaction() as c:
        assert F.pr(c, REPO, 1)["queue_state"] == "queued"
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "In the merge queue")


def test_a_rebase_after_the_ok_is_queued_again_by_the_release_owner(company):
    """C22, velocity: a rebase onto a newer main (or a conflict fixed without changing the PR's own lines) changes the
    same lines as the commit the person OK'd. The Release Owner queues it again; until Tico has read what the new commit
    changes, it is asked to try again."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    head = gh.rebase(REPO, 1)
    gh.set_checks(REPO, head, "success")
    with company.transaction() as c:
        seen(c, gh, 1)
        version = E.task(c, task_id)["version"]
        go = lambda: MQ.requeue(c, task_id, RM, seen_version=version)  # noqa: E731
        assert refused(go, "task_requirement") == MQ.COMPARING.format(ref=REF)
    F.refresh(company, gh, later())
    assert [call[0] for call in gh.calls].count("pr_diff") == 2, "the queued commit and the new one, once each"
    with company.transaction() as c:
        owed = MQ.requeue(c, task_id, RM, seen_version=E.task(c, task_id)["version"])
        assert [(e["state"], e["pinned"]) for e in owed] == [("owed", head)]
    F.refresh(company, gh, later(20))
    assert [call[0] for call in gh.calls].count("pr_diff") == 2, "each commit is read once"


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
        assert effects(c, task_id) == [("act", "done"), ("undo", "owed")]
    assert MQ.run(company, gh) == 1
    with company.transaction() as c:
        assert effects(c, task_id)[-1] == ("undo", "done")
        assert F.pr(c, REPO, 1)["queue_state"] == "none"
        assert "pr_dequeued" in X.events(c, task_id)
        assert not [b for b in NS.rules_hold(c) if b.startswith("5:")]


def test_closing_before_github_queued_it_makes_the_queue_moot(c):
    gh = FakeGitHub()
    task_id = in_merge(c, gh, 1)
    E.move(c, task_id, "closed", X.ANA, "Not needed")
    assert effects(c, task_id) == [("act", "moot")]
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


def test_a_pr_someone_else_queued_first_still_gets_tico_s_check_so_it_does_not_stall(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        head = gh.prs[(REPO, 1)]["head_sha"]
        gh.enqueue(REPO, 1, head)  # a person queues it on GitHub before Tico's pass
        hooked(c, "enqueued", gh)
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "In the merge queue")
    gh.calls.clear()
    assert MQ.run(company, gh) == 1
    assert [call[0] for call in gh.calls] == ["ready", "post_check", "enqueue"]  # GitHub answers ok: already queued
    assert gh.merge_ok_on(REPO, head) is True
    with company.transaction() as c:
        assert effects(c, task_id) == [("act", "done")]
        assert line(c, task_id, "pr_merged") == (R.WAITING, "github", "In the merge queue")


def test_a_pr_merged_anyway_after_its_task_closed_is_read_once_more(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.merge(REPO, 1)  # no webhook gets through
    with company.transaction() as c:
        E.move(c, task_id, "closed", X.ANA, "Not needed")
    MQ.run(company, gh)  # the dequeue says merged
    F.refresh(company, gh, later())
    with company.transaction() as c:
        fact = F.pr(c, REPO, 1)
        assert (fact["state"], fact["queue_state"]) == ("merged", "none")
    gh.calls.clear()
    F.refresh(company, gh, later(30))
    assert not [call for call in gh.calls if call[0] == "read_pr"]  # once: a closed task's PR is not read again


def test_a_pr_deleted_after_it_was_read_is_the_owner_s_after_two_hours(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        engineering(c)
        task_id = new(c, 1)
        gh.set_checks(REPO, gh.open_pr(REPO, 1), "pending")
        seen(c, gh)
    del gh.prs[(REPO, 1)]  # gone: GitHub answers 404
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert line(c, task_id, "pr_open") == (R.MET, None, "PR open")  # within the 2 hours
        age(c, "read_at", H.shift(H.now(), hours=-3))  # every read since found it gone
        assert line(c, task_id, "pr_open") == (R.NOT_MET, B, "GitHub hasn't answered about acme/app#1 for 2 hours")


def test_two_requirements_missing_for_one_reason_are_one_line(c):
    from backend import waiting_on as W

    engineering(c)
    task_id = new(c)  # no PR: PR open and Checks pass both say "No PR linked"
    lines = [wait.line for wait in W.of(c, E.task(c, task_id))]
    assert lines.count("No PR linked") == 1, lines


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
        assert effects(c, task_id) == [("act", "moot"), ("undo", "owed")]
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
        assert line(c, task_id, "pr_open") == (R.WAITING, "github", "Reading the PR from GitHub")
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
    assert line(c, task_id, "checks_pass") == (R.WAITING, "github", "Checks running")
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
    assert F.pr(c, REPO, 1)["due_at"] is not None and F.pr(c, REPO, 1)["queue_state"] == "none"
    F.webhook(c, "release", {"action": "published", "release": {"tag_name": "v1.2.0"}, "repository": repo})
    assert [r["tag"] for r in F.releases(c, REPO)] == ["v1.2.0"]


def test_webhooks_out_of_order_never_unmerge_and_an_early_check_reads_the_pr(c):
    engineering(c)
    new(c, 1)
    repo = {"full_name": REPO}
    pr = {"number": 1, "state": "open", "draft": False, "merged": False, "head": {"sha": "a"}}
    F.webhook(c, "pull_request", {"action": "opened", "pull_request": pr, "repository": repo})
    # The check run on the new head b comes before its `synchronize`: the PR is read in full on the next pass.
    run = {"head_sha": "b", "name": "unit", "status": "completed", "conclusion": "success", "app": {"slug": "ci"}}
    F.webhook(c, "check_run", {"check_run": {**run, "pull_requests": [{"number": 1}]}, "repository": repo})
    assert F.pr(c, REPO, 1)["due_at"] is not None
    landed = {**pr, "state": "closed", "merged": True, "merge_commit_sha": "m", "merged_at": "2026-11-10T00:00:00Z"}
    F.webhook(c, "pull_request", {"action": "closed", "pull_request": landed, "repository": repo})
    # GitHub's `dequeued` for the merge comes after `closed`, with a snapshot taken while the PR was still open.
    late = {"action": "dequeued", "reason": "merged", "pull_request": pr, "repository": repo}
    F.webhook(c, "pull_request", late)
    F.webhook(c, "pull_request", {"action": "synchronize", "pull_request": pr, "repository": repo})
    fact = F.pr(c, REPO, 1)
    assert (fact["state"], fact["merged_sha"], fact["queue_state"]) == ("merged", "m", "none")


def test_a_pull_request_webhook_is_as_new_as_github_s_updated_at(c):
    engineering(c)
    new(c, 1)
    repo = {"full_name": REPO}
    old = {"number": 1, "state": "open", "draft": False, "head": {"sha": "a"}, "updated_at": "2026-11-10T10:00:00Z"}
    pushed = {**old, "head": {"sha": "b"}, "updated_at": "2026-11-10T10:00:05Z"}
    F.webhook(c, "pull_request", {"action": "synchronize", "pull_request": pushed, "repository": repo})
    F.webhook(c, "pull_request", {"action": "opened", "pull_request": old, "repository": repo})  # arrives late
    fact = F.pr(c, REPO, 1)
    assert fact["head_sha"] == "b" and fact["due_at"] is not None  # kept, and read in full on the next pass


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


def test_a_refused_queue_again_on_a_private_task_keeps_none_of_its_words(hook_api):
    """The company owner reads refusals and events but not a private task: "The checks on acme/app#1 are still
    running" is kept as "Private task write refused"."""
    store = hook_api.app.state.store
    gh = FakeGitHub()
    with store.transaction() as c:
        task_id = in_merge(c, gh, 1)
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task_id,))
    MQ.run(store, gh)
    gh.drop(REPO, 1)
    F.refresh(store, gh, later())
    with store.transaction() as c:
        F.save_pr(c, REPO, 1, {"checks": [{"name": "unit", "state": "pending"}]}, F.version_now())
    headers = {"Authorization": "Bearer ana-test", "Idempotency-Key": "q-private"}
    r = hook_api.post(f"/api/v2/tasks/{task_id}/queue", json={}, headers=headers)
    assert r.status_code == 409 and "still running" in r.json()["detail"], r.text
    with store.read() as c:
        refusals = [str(x[0]) for x in c.execute("SELECT detail FROM refusals").fetchall()]
        events = [" ".join(map(str, x)) for x in c.execute("SELECT * FROM events WHERE action='refused'").fetchall()]
    assert any("Private task write refused" in d for d in refusals), refusals
    assert not [line for line in refusals + events if "acme/app#1" in line or "still running" in line]


def test_the_heartbeat_steps_are_quiet_without_a_github_app():
    class Ctx:
        github_app = None

    assert HB.item_metadata(Ctx()) == ("No GitHub app", True)
    assert HB.source_actions(Ctx()) == ("No GitHub app", True)


# ----------------------------------------------------------------------------- only signed-off PRs reach the queue
# Every way into a stage that queues PRs passes the stages before it (docs/pipelines-v3.md "GitHub", "Merge"): a bot
# can't push past the person's OK, link a new PR there, move back into it, or reopen or restore a task into it.
def preset_engineering(c):
    """The Engineering preset with the QA OK people-only (chris/rc3-signoffs makes the preset so)."""
    from backend import pipeline_settings as PS
    from backend.tests.helpers.product_engineering_presets import _answers, _team

    _answers(c)
    _team(c)
    PS.ensure_presets(c)
    pid = c.execute("SELECT id FROM pipelines WHERE name='Engineering'").fetchone()[0]
    c.execute(
        "UPDATE requirements SET who_oks='people_only' WHERE kind='page' AND stage_id IN "
        "(SELECT id FROM stages WHERE pipeline_id=? AND name='QA')",
        (pid,),
    )
    return pid


def test_a_bot_cannot_push_its_task_from_build_past_the_person_s_ok_into_merge(c):
    from backend import task_calls as TC

    pid = preset_engineering(c)
    task_id = X.new_task(c, X.ANA, "Ship it", pid)["id"]
    H.task_link(c, X.ANA, task_id, f"https://github.com/{REPO}/pull/1")
    gh = FakeGitHub()
    gh.set_checks(REPO, gh.open_pr(REPO, 1), "success")
    seen(c, gh)
    assert stage(c, task_id) == "Build"
    version = E.task(c, task_id)["version"]
    detail = refused(lambda: TC.task_move(c, "bot:builder", task_id, "Merge", "ship it", version), "task_needs_person")
    assert "needs a person" in detail
    assert stage(c, task_id) == "Build" and effects(c, task_id) == []


def test_in_merge_only_a_person_links_another_pr(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        gh.open_pr(REPO, 2)
        for bot in (RM, B, H.KEEPER):
            url = f"https://github.com/{REPO}/pull/2"
            with pytest.raises(H.Refused) as raised:
                H.task_link(c, bot, task_id, url, mover=True)
            assert raised.value.detail == F.LINK_IN_MERGE, raised.value
        assert [p["number"] for p in F.prs_of(c, task_id)] == [1]
        H.task_link(c, X.ANA, task_id, f"https://github.com/{REPO}/pull/2")  # a person's link is queued as before
        seen(c, gh, 2)
        assert [e["item_key"] for e in c.execute("SELECT item_key FROM effects").fetchall()] == [REF, "acme/app#2"]


def test_a_worktree_branch_pr_is_not_linked_while_its_task_is_in_merge(hook_api):
    from backend import github as G

    gh = FakeGitHub()
    with hook_api.app.state.store.transaction() as c:
        task_id = in_merge(c, gh, 1)
        c.execute("DELETE FROM github_app")
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,installation_id,html_url,ciphertext,"
            "nonce,created_at,created_by) VALUES('app',1,'tico','c','acme',0,NULL,'',x'00',x'00','t','ana')"
        )
        branch = f"tico/{task_id[:8]}-ship-it"
        c.execute(
            "INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,branch) "
            "VALUES('wt1',?,'worktree','worktree:x','x','ready',?,?,?,?)",
            (task_id, X.ANA, H.now(), REPO, branch),
        )
        pr = {
            "html_url": f"https://github.com/{REPO}/pull/2",
            "head": {"ref": branch, "repo": {"full_name": REPO}},
        }
        assert G.register_worktree_pr(c, pr) == set()
        assert [p["number"] for p in F.prs_of(c, task_id)] == [1]
        E.move(c, task_id, "Build", X.ANA, "Another PR")
        assert len(G.register_worktree_pr(c, pr)) == 1  # out of Merge it is linked as before


def test_a_bot_does_not_move_a_task_back_into_merge_from_a_later_stage(company):
    from backend import task_calls as TC

    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.merge(REPO, 1)
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert stage(c, task_id) == "Release"
        gh.open_pr(REPO, 2)
        H.task_link(c, RM, task_id, f"https://github.com/{REPO}/pull/2")  # outside Merge a bot may link
        seen(c, gh, 2)
        version = E.task(c, task_id)["version"]
        detail = refused(lambda: TC.task_move(c, RM, task_id, "Merge", "again", version), "task_stage")
        assert detail == "Only a person moves a task back into Merge. Move it back to Build instead."
        TC.task_move(c, RM, task_id, "Build", "Review the new PR", version)
        assert stage(c, task_id) == "Build"
        assert [r[0] for r in c.execute("SELECT item_key FROM effects WHERE kind='act'")] == [REF]


@pytest.mark.parametrize("via", ["engine", "api"])
def test_a_reopened_task_lands_before_merge_and_is_asked_again(company, via):
    from backend import task_calls as TC

    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    with company.transaction() as c:
        c.execute("UPDATE tasks SET requester_id=? WHERE id=?", (EM, task_id))  # a bot's request: a bot may close it
        E.move(c, task_id, E.CLOSED, RM, "Not now", E.task(c, task_id)["version"])
        gh.set_checks(REPO, gh.push(REPO, 1), "failure")  # a commit nobody reviewed
        seen(c, gh)
        version = E.task(c, task_id)["version"]
        if via == "engine":
            E.move(c, task_id, E.REOPEN, RM, "Try again", version)
        else:
            TC.task_move(c, RM, task_id, E.REOPEN, "Try again", version)
        assert stage(c, task_id) == "Build"
        assert [r[0] for r in c.execute("SELECT state FROM effects WHERE kind='act'")] == ["done"]


def test_a_task_restored_from_the_trash_lands_before_merge(c):
    from backend import task_delete

    gh = FakeGitHub()
    task_id = in_merge(c, gh, 1)
    assert task_delete.delete_tasks(c, [task_id], apply=True, keep_work=True)["applied"]
    assert task_delete.restore_tasks(c, [task_id], X.ANA)["restored"]
    assert stage(c, task_id) == "Build" and effects(c, task_id) == []


# ----------------------------------------------------------------------------- a snooze on Merge waits for GitHub
def dropped(company, gh):
    """A task in Merge whose PR GitHub took out of the queue, with green checks: the Release Owner's turn, its removal
    wake had its run."""
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    gh.drop(REPO, 1, "It conflicts with its base branch")
    with company.transaction() as c:
        seen(c, gh)
        X.settled(c)
    return task_id


def snoozed(c, task_id, why, by=RM):
    E.snooze(c, task_id, by, why, later(90), seen_version=E.task(c, task_id)["version"])


def news(c, task_id):
    """(wake key, body) of each wake that GitHub news on the Merge stage gave the Release Owner."""
    return [(k, b) for k, b in owner_wakes(c, task_id, RM) if f":{MQ.WAKE_NEWS}:" in k]


def test_checks_finishing_on_the_prs_newest_commit_end_a_bots_snooze_once(company):
    """Tico Team, Nov 11: after rebasing its PR the Release Owner snoozed 25 minutes "re-queue once checks pass"; the
    checks took 6. Their end ends a bot's snooze on Merge and wakes it once; reading them again wakes nobody."""
    gh = FakeGitHub()
    task_id = dropped(company, gh)
    with company.transaction() as c:
        head = gh.push(REPO, 1)
        gh.set_checks(REPO, head, "pending")
        seen(c, gh)
        snoozed(c, task_id, "re-queue once checks pass")
        assert news(c, task_id) == []
        assert W.headline(c, task_id).state == W.SNOOZED  # the task's line says so while it sleeps
        gh.set_checks(REPO, head, "success")
        seen(c, gh)
        assert E.task(c, task_id)["snooze"] is None
        ended = c.execute("SELECT ended_by_id FROM snoozes WHERE task_id=?", (task_id,)).fetchone()[0]
        assert ended == E.SYSTEM
        ((key, body),) = news(c, task_id)
        assert key.endswith(f"checks:{REF}:{head}:success")
        assert f"The checks on {REF} passed" in body and "re-queue once checks pass" in body
        snoozed(c, task_id, "the queue is busy; re-queue later")
        seen(c, gh)  # the same checks read again, as a sweep does
        hooked(c, "synchronize", gh)
        assert E.task(c, task_id)["snooze"] and len(news(c, task_id)) == 1


def test_a_move_of_main_ends_a_bots_snooze_once_per_commit_and_a_named_pr_waits_for_its_merge(company):
    """Tico Team, Nov 11: the Release Owner snoozed "until 04:30, waiting for #3417 to merge". #3417 merged at 03:01
    and nothing woke it. A snooze that names a PR ends when that PR merges into the base; one that names none ends
    when the base moves, once per base commit however many webhooks report it."""
    from backend import github as G

    gh = FakeGitHub()
    task_id = dropped(company, gh)
    with company.transaction() as c:
        snoozed(c, task_id, "until 04:30, waiting for #3417 to merge; then re-queue #1 unchanged")
        G.base_moved(c, REPO, "main", "a" * 40, default_branch="main")  # the push: no PR named
        G.base_moved(c, REPO, "main", "b" * 40, default_branch="main", merged_number=3400)  # another PR
        assert E.task(c, task_id)["snooze"] and news(c, task_id) == []
        G.base_moved(c, REPO, "main", "c" * 40, default_branch="main", merged_number=3417)
        ((key, body),) = news(c, task_id)
        assert E.task(c, task_id)["snooze"] is None and f"{REPO}#3417 merged." in body

        snoozed(c, task_id, "re-queue once main has the fix")
        G.base_moved(c, REPO, "main", "c" * 40, default_branch="main", merged_number=3417)  # the same merge again
        assert E.task(c, task_id)["snooze"] and len(news(c, task_id)) == 1
        G.base_moved(c, REPO, "main", "d" * 40, default_branch="main")
        G.base_moved(c, REPO, "main", "d" * 40, default_branch="main", merged_number=3418)  # the same move again
        assert E.task(c, task_id)["snooze"] is None and len(news(c, task_id)) == 2
        snoozed(c, task_id, "re-queue once main has the fix")
        G.base_moved(c, REPO, "main", "d" * 40, default_branch="main")  # a webhook delivered again
        G.base_moved(c, REPO, "release", "e" * 40, default_branch="main")  # another branch
        assert E.task(c, task_id)["snooze"] and len(news(c, task_id)) == 2


def test_a_persons_snooze_on_merge_is_never_ended_by_github(company):
    from backend import github as G

    gh = FakeGitHub()
    task_id = dropped(company, gh)
    with company.transaction() as c:
        head = gh.push(REPO, 1)
        gh.set_checks(REPO, head, "pending")
        seen(c, gh)
        snoozed(c, task_id, "hold it for the launch", by=X.ANA)
        gh.set_checks(REPO, head, "failure")
        seen(c, gh)
        G.base_moved(c, REPO, "main", "a" * 40, default_branch="main", merged_number=9)
        assert E.task(c, task_id)["snooze"]["by_id"] == X.ANA and news(c, task_id) == []


def test_a_removal_a_conflict_and_a_merge_end_a_bots_snooze(company):
    from backend import github as G

    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    with company.transaction() as c:
        snoozed(c, task_id, "in the queue; look again at 04:30")
    gh.drop(REPO, 1, "It conflicts with its base branch")
    F.refresh(company, gh, later())
    with company.transaction() as c:
        assert E.task(c, task_id)["snooze"] is None  # the removal's own wake is not held by it
        assert len([k for k, _ in X.wakes(c, task_id, RM) if ":queue_removed:" in k]) == 1
        X.settled(c)
        snoozed(c, task_id, "rebase after lunch")
        pr = {"number": 1, "html_url": f"https://github.com/{REPO}/pull/1", "state": "open", "mergeable": False}
        pr["head"] = {"sha": gh.prs[(REPO, 1)]["head_sha"], "ref": "feature"}
        G.pull_request(c, {"action": "refresh", "pull_request": pr}, read_refresh=True)
        ((_, body),) = news(c, task_id)
        assert E.task(c, task_id)["snooze"] is None and f"{REF} conflicts with its base branch now." in body
        G.pull_request(c, {"action": "refresh", "pull_request": pr}, read_refresh=True)  # read again: no news
        assert len(news(c, task_id)) == 1
        snoozed(c, task_id, "waiting for the queue")
        version = E.task(c, task_id)["version"]
    gh.prs[(REPO, 1)]["queue_state"] = "queued"
    gh.merge(REPO, 1)
    F.refresh(company, gh, later(20))
    with company.transaction() as c:
        assert stage(c, task_id) == "Release" and E.task(c, task_id)["version"] > version


# ----------------------------------------------------------------------------- a snooze before Merge waits for GitHub too
def failing_in_build(company, gh):
    """A task in Build whose PR's checks failed: the Builder's turn."""
    with company.transaction() as c:
        engineering(c)
        task_id = new(c, 1)
        gh.set_checks(REPO, gh.open_pr(REPO, 1), "failure")
        seen(c, gh)
        assert stage(c, task_id) == "Build"
    return task_id


def builder_news(c, task_id):
    return [(k, b) for k, b in owner_wakes(c, task_id, B) if f":{MQ.WAKE_NEWS}:" in k]


def bot_snoozes(c, task_id):
    return int(E.task(c, task_id)["visit"].get("bot_snoozes") or 0)


def merged_hook(c, number, merged=True):
    """GitHub's webhook for a PR no task links being closed, merged or not."""
    pr = {"number": number, "html_url": f"https://github.com/{REPO}/pull/{number}", "state": "closed"}
    pr |= {"merged": merged, "merge_commit_sha": "f" * 40 if merged else None, "base": {"ref": "main"}}
    repository = {"full_name": REPO, "default_branch": "main"}
    G.pull_request(c, {"action": "closed", "pull_request": pr, "repository": repository})


def test_a_snooze_before_merge_that_names_a_pr_ends_when_that_pr_merges(company):
    """Tico Team, Nov 12: the Builder snoozed in Fixes 1 "until 08:00Z, waiting for #3417 to land, then rerun checks";
    #3417 merged at 03:01 and nothing woke it for five hours. On any stage a bot's snooze that names a PR ends when that
    PR merges or closes, once; a move of main that names no PR wakes nobody before Merge, and the snooze count stays."""
    gh = FakeGitHub()
    task_id = failing_in_build(company, gh)
    with company.transaction() as c:
        snoozed(c, task_id, "until 08:00Z, waiting for #3417 to land, then rerun checks", by=B)
        count = bot_snoozes(c, task_id)
        G.base_moved(c, REPO, "main", "a" * 40, default_branch="main")  # a push: on Build it is noise
        merged_hook(c, 3400)  # a PR the snooze does not name
        assert E.task(c, task_id)["snooze"] and builder_news(c, task_id) == []
        merged_hook(c, 3417)
        ((key, body),) = builder_news(c, task_id)
        assert key.endswith(f"named:{REPO}#3417:merged") and f"{REPO}#3417 merged." in body
        assert "waiting for #3417 to land" in body
        assert E.task(c, task_id)["snooze"] is None and bot_snoozes(c, task_id) == count
        snoozed(c, task_id, "still waiting for #3417 to land", by=B)
        merged_hook(c, 3417)  # the webhook delivered again
        G.merged_by_tico(c, REPO, 3417)
        assert E.task(c, task_id)["snooze"] and len(builder_news(c, task_id)) == 1
        merged_hook(c, 3418, merged=False)  # nor does a close of a PR it does not name end it
        assert E.task(c, task_id)["snooze"]


def test_checks_finishing_before_merge_end_a_bots_snooze_once(company):
    gh = FakeGitHub()
    task_id = failing_in_build(company, gh)
    with company.transaction() as c:
        head = gh.push(REPO, 1)
        gh.set_checks(REPO, head, "pending")
        seen(c, gh)
        snoozed(c, task_id, "rerun checks at 05:00", by=B)
        gh.set_checks(REPO, head, "failure")
        seen(c, gh)
        ((key, body),) = builder_news(c, task_id)
        assert key.endswith(f"checks:{REF}:{head}:failure") and f"Checks failed on {REF}" in body
        assert E.task(c, task_id)["snooze"] is None
        snoozed(c, task_id, "rerun checks at 06:00", by=B)
        seen(c, gh)  # read again, as a sweep does
        assert E.task(c, task_id)["snooze"] and len(builder_news(c, task_id)) == 1


def test_a_persons_snooze_before_merge_is_never_ended_by_github(company):
    gh = FakeGitHub()
    task_id = failing_in_build(company, gh)
    with company.transaction() as c:
        head = gh.push(REPO, 1)
        gh.set_checks(REPO, head, "pending")
        seen(c, gh)
        snoozed(c, task_id, "hold until #3417 lands", by=X.ANA)
        gh.set_checks(REPO, head, "failure")
        seen(c, gh)
        merged_hook(c, 3417)
        assert E.task(c, task_id)["snooze"]["by_id"] == X.ANA and builder_news(c, task_id) == []


def test_a_conflict_before_merge_ends_a_bots_snooze_so_its_wake_is_not_held(company):
    gh = FakeGitHub()
    task_id = failing_in_build(company, gh)
    with company.transaction() as c:
        X.settled(c)
        before = len(X.wakes(c, task_id, B))
        snoozed(c, task_id, "rebase after lunch", by=B)
        pr = {"number": 1, "html_url": f"https://github.com/{REPO}/pull/1", "state": "open", "mergeable": False}
        pr["head"] = {"sha": gh.prs[(REPO, 1)]["head_sha"], "ref": "feature"}
        G.pull_request(c, {"action": "refresh", "pull_request": pr}, read_refresh=True)
        assert E.task(c, task_id)["snooze"] is None and len(X.wakes(c, task_id, B)) == before + 1
