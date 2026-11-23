"""Checks that wait for someone to approve them never finish by themselves (CI that trusts only an old GitHub App holds
every agent PR at "Build not triggered: approval required", which Tico read as running for 2 hours). Checks pass says
they need approval and is the owner's turn at once; a bot owner is woken once per commit, a person gets no message. A
ready PR on a repo that runs checks, with none started, is the owner's after 15 minutes."""

import json

import pytest

from backend import github as G
from backend import task_engine as E
from backend import task_requirements as R
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import REPO, RM, B, engineering, in_merge, later, new, seen

NEEDS = (
    "Checks need approval on acme/app#1: build. Someone with access must approve the run on GitHub, or the CI must "
    "trust Tico's GitHub app"
)


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def line(c, task_id):
    row = E.task(c, task_id)
    s = next(s for s in R.standings(c, row) if s.requirement["condition"] == "checks_pass")
    return (s.state, s.on, s.line)


def run(status, conclusion=None, run_id=1):
    return {
        "id": run_id,
        "name": "build",
        "head_sha": "h1",
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": "github-actions"},
        "check_suite": {"id": 1},
    }


def _app_read(status):
    """backend/github.py AppGitHub.read_checks on GitHub's answer of no check runs and one commit status."""
    from backend.github import AppGitHub

    class Answer:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Service:
        def row(self):
            return {"slug": "tico-app"}

        def mint(self, repos, permissions):
            return "token", None

        def _call(self, method, path, headers=None, params=None):
            if path.endswith("/check-runs"):
                return Answer({"check_runs": []})
            return Answer({"statuses": [{"id": 9, **status}]})

    return AppGitHub(Service()).read_checks(REPO, "h1")


def body_of(c, task_id, actor, part):
    """The body of the one wake to `actor` whose key has `part`."""
    (key,) = [k for k, _ in X.wakes(c, task_id, actor) if part in k]
    return c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]


def stand_line(c, task_id, condition):
    row = E.task(c, task_id)
    s = next(s for s in R.standings(c, row) if s.requirement["condition"] == condition)
    return (s.state, s.on, s.line)


def approval_wakes(c, task_id, actor=B):
    return [k for k, _ in X.wakes(c, task_id, actor) if k.endswith(":approval")]


def test_a_run_waiting_action_required_or_a_status_saying_so_needs_approval():
    assert F.run_check(run("waiting"), "tico")["state"] == "approval"
    assert F.run_check(run("completed", "action_required"), "tico")["state"] == "approval"
    said = {"context": "ci/jenkins", "state": "pending", "description": "Build not triggered: approval required"}
    assert F.status_check(said)["state"] == "approval"
    assert F.status_check({**said, "description": "Build started"})["state"] == "pending"
    assert F.status_check({**said, "description": "No approval required"})["state"] == "pending"
    assert F.status_check({**said, "description": "Does not require approval"})["state"] == "pending"
    assert F.status_check({**said, "description": "Build running: no further approval required"})["state"] == "pending"
    assert F.status_check({**said, "description": "this doesn't require approval"})["state"] == "pending"
    assert F.status_check({**said, "state": "failure"})["state"] == "failure"
    assert F.run_check(run("queued"), "tico")["state"] == "pending"
    # The PR link keeps approval as its own state: never failed, never a finished head, and a failure beside it wins.
    assert G._check_state("action_required") == G._check_state("waiting") == "approval"
    assert G._check_state("pending", "Build not triggered: approval required") == "approval"
    assert G._check_state("pending", "Build running: no further approval required") == "pending"
    assert G._overall({"build": "approval", "lint": "passing"}) == "approval"
    assert G._overall({"build": "approval", "unit": "failing"}) == "failing"
    link = {"title": "acme/app#1", "url": "https://github.com/acme/app/pull/1"}
    assert G._finish(None, None, link, {}, "h1", {"build": "approval", "lint": "passing"}) == ("h1", "approval")
    assert F._checks_state([{"state": "pending"}, {"state": "approval"}]) == "approval"
    assert F._checks_state([{"state": "failure"}, {"state": "approval"}]) == "failure"  # a failure still wins


def test_checks_pass_needs_approval_at_once_and_goes_back_to_running_once_approved(c):
    engineering(c)
    task_id = new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": [F.run_check(run("waiting"), "tico")]}, 1)
    assert line(c, task_id) == (R.NOT_MET, B, NEEDS)
    assert F.pr(c, REPO, 1)["checks_started_at"] is None
    F.webhook(c, "check_run", {"check_run": run("completed", "action_required"), "repository": {"full_name": REPO}})
    assert line(c, task_id) == (R.NOT_MET, B, NEEDS)  # action required is not a failure
    failed = [{"name": "unit", "state": "failure"}, {"name": "build", "state": "approval"}]
    F.save_pr(c, REPO, 1, {"checks": failed}, F.version_now())
    assert line(c, task_id) == (R.NOT_MET, B, "Checks failed on acme/app#1: unit")
    F.save_pr(c, REPO, 1, {"checks": [F.run_check(run("in_progress", run_id=2), "tico")]}, F.version_now())
    assert line(c, task_id) == (R.WAITING, "github", "Checks running")  # approved: it runs
    assert F.pr(c, REPO, 1)["checks_started_at"]
    F.save_pr(c, REPO, 1, {"checks": [F.run_check(run("completed", "success", 3), "tico")]}, F.version_now())
    assert line(c, task_id)[0] == R.MET


def test_a_commit_status_saying_approval_required_needs_approval(c):
    engineering(c)
    task_id = new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": []}, 1)
    status = {
        "sha": "h1",
        "context": "build",
        "state": "pending",
        "description": "Build not triggered: approval required",
    }
    F.webhook(c, "status", {**status, "repository": {"full_name": REPO}})
    assert line(c, task_id) == (R.NOT_MET, B, NEEDS)
    assert F._checks_state(_app_read(status)) == "approval"  # the REST read of the same status
    assert F._checks_state(_app_read({**status, "description": "No approval required"})) == "pending"
    F.webhook(c, "status", {**status, "description": "Build started", "repository": {"full_name": REPO}})
    assert line(c, task_id) == (R.WAITING, "github", "Checks running")


def test_the_bot_owner_is_woken_once_per_commit_and_a_snooze_ends(c):
    engineering(c)
    task_id = new(c, 1)
    waiting = [{"name": "build", "state": "approval"}]
    F.save_and_settle(c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": waiting}, F.version_now())
    (key,) = approval_wakes(c, task_id)
    body = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
    assert "Checks need approval on acme/app#1: build" in body
    F.save_and_settle(c, REPO, 1, {"checks": waiting}, F.version_now())  # read again: nobody is woken again
    assert len(approval_wakes(c, task_id)) == 1
    running = [{"name": "build", "state": "pending"}]
    F.save_and_settle(c, REPO, 1, {"checks": running}, F.version_now())  # approved: it runs
    E.snooze(c, task_id, B, "Waiting for checks", later(90), seen_version=E.task(c, task_id)["version"])
    F.save_and_settle(c, REPO, 1, {"checks": waiting}, F.version_now())  # the same head needs approval again
    assert len(approval_wakes(c, task_id)) == 1
    assert E.task(c, task_id).get("snooze")  # told already: the bot's snooze stands
    F.save_and_settle(c, REPO, 1, {"head_sha": "h2", "checks": waiting}, F.version_now())  # a new commit
    assert len(approval_wakes(c, task_id)) == 2
    assert not E.task(c, task_id).get("snooze")


def test_with_two_prs_the_second_ones_approval_is_told_once_it_is_what_checks_pass_stands_on(c):
    engineering(c)
    task_id = new(c, 1, 2)
    waiting = [{"name": "build", "state": "approval"}]
    F.save_and_settle(c, REPO, 1, {"state": "open", "head_sha": "a1", "checks": waiting}, F.version_now())
    F.save_and_settle(c, REPO, 2, {"state": "open", "head_sha": "b1", "checks": waiting}, F.version_now())
    assert [k.split(":checks:")[1] for k in approval_wakes(c, task_id)] == ["acme/app#1:a1:approval"]
    F.save_and_settle(c, REPO, 1, {"checks": [{"name": "build", "state": "success"}]}, F.version_now())
    assert line(c, task_id)[2].startswith("Checks need approval on acme/app#2")
    assert [k.split(":checks:")[1] for k in approval_wakes(c, task_id)] == [
        "acme/app#1:a1:approval",
        "acme/app#2:b1:approval",
    ]


def test_a_person_owner_gets_no_message(c):
    X.github_installed(c)
    X.pipeline(
        c, "eng", "Engineering", stages=({"name": "Build", "owner": X.ANA, "requirements": [X.github("checks_pass")]},)
    )
    task_id = new(c, 1)
    F.save_and_settle(
        c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": [{"name": "build", "state": "approval"}]}, 1
    )
    assert line(c, task_id)[:2] == (R.NOT_MET, X.ANA)
    assert approval_wakes(c, task_id, X.ANA) == []


def _approval_on_head(company, gh, told=True):
    """The checks on the PR's signed-off head (the one the task came into Merge with) turn to needing approval on
    GitHub; `told`: Tico has read it (else what it saved still says they passed: no webhook came yet)."""
    with company.transaction() as c:
        head = F.pr(c, REPO, 1)["head_sha"]
        gh.set_checks(REPO, head, "approval")
        if told:
            seen(c, gh)
        assert F.pr(c, REPO, 1)["checks_state"] == ("approval" if told else "success")
    return head


def test_the_first_queue_is_refused_while_the_checks_need_approval(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    head = _approval_on_head(company, gh, told=False)
    F.run(company, gh)
    assert not [call for call in gh.calls if call[0] in ("post_check", "enqueue")]
    assert (REPO, head) not in gh.merge_ok
    with company.transaction() as c:
        assert stand_line(c, task_id, "pr_merged") == (R.NOT_MET, RM, "The checks on acme/app#1 need approval")
        assert "need approval, so Tico did not queue it" in body_of(c, task_id, RM, ":queue_refused:")


def test_queue_again_is_refused_while_the_checks_on_the_signed_off_head_need_approval(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    F.run(company, gh)
    gh.drop(REPO, 1, "Taken out by hand")
    _approval_on_head(company, gh)
    F.refresh(company, gh, later())
    with company.transaction() as c:
        version = E.task(c, task_id)["version"]
        with pytest.raises(H.Refused) as raised:
            F.requeue(c, task_id, RM, seen_version=version)
        assert raised.value.detail == "The checks on acme/app#1 need approval"


def test_a_stage_that_does_not_ask_for_passing_checks_gets_no_approval_wake(c):
    X.bot(c, "reviewer")
    X.github_installed(c)
    X.pipeline(
        c,
        "eng",
        "Engineering",
        stages=({"name": "Review", "owner": "bot:reviewer", "requirements": [X.github("pr_open")]},),
    )
    task_id = new(c, 1)
    E.snooze(c, task_id, "bot:reviewer", "Reading the diff", later(90), seen_version=E.task(c, task_id)["version"])
    F.save_and_settle(
        c, REPO, 1, {"state": "open", "head_sha": "h1", "checks": [{"name": "build", "state": "approval"}]}, 1
    )
    assert approval_wakes(c, task_id, "bot:reviewer") == []
    assert E.task(c, task_id).get("snooze")


def test_no_checks_started_on_a_repo_that_runs_them_is_the_owners_after_15_minutes(c):
    engineering(c)
    new(c, 3)
    F.save_pr(c, REPO, 3, {"state": "merged", "head_sha": "a", "merged_sha": "m", "checks": [{"state": "approval"}]}, 1)
    assert F.runs_checks(c, REPO)  # a PR that needed approval counts as running checks
    task_id = new(c, 1)
    F.save_pr(c, REPO, 1, {"state": "open", "head_sha": "b"}, 1)
    c.execute(
        "UPDATE item_metadata SET fields=json_set(fields, '$.head_seen_at', ?) WHERE item_key=?",
        (H.shift(H.now(), minutes=-14), f"{REPO}#1"),
    )
    assert line(c, task_id) == (R.WAITING, "github", "Waiting for checks")
    c.execute(
        "UPDATE item_metadata SET fields=json_set(fields, '$.head_seen_at', ?) WHERE item_key=?",
        (H.shift(H.now(), minutes=-16), f"{REPO}#1"),
    )
    assert line(c, task_id) == (R.NOT_MET, B, "No checks started on acme/app#1 after 15 minutes")


def _link_detail(c, task_id):
    return json.loads(
        c.execute("SELECT detail FROM task_links WHERE task_id=? AND kind='pr'", (task_id,)).fetchone()[0]
    )


def test_the_pr_link_reads_a_waiting_run_as_approval_and_finishes_the_head_once_approved_and_green(c):
    """The link path (github.pr_signal): a run waiting for approval has no conclusion, its `status` says so; and an
    approval-pending status that turns green finishes the head, as a pending one does."""
    X.bot(c, "builder")
    task_id = X.new_task(c, "bot:builder", "Fix it")["id"]
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, detail, created_at) "
        "VALUES (?,?,'pr','https://github.com/acme/app/pull/7','acme/app',7,'github','pr','acme/app#7',?,?)",
        (H.new_id(), task_id, json.dumps({"head_sha": "h7"}), H.now()),
    )
    repo = {"full_name": "acme/app"}
    waiting = {
        "name": "build",
        "head_sha": "h7",
        "status": "waiting",
        "conclusion": None,
        "pull_requests": [{"number": 7}],
    }
    G.pr_signal(c, "check_run", {"check_run": waiting, "repository": repo})
    assert _link_detail(c, task_id)["checks"]["build"] == "approval"
    said = {"sha": "h7", "context": "ci/jenkins", "description": "Build not triggered: approval required"}
    G.pr_signal(c, "status", {**said, "state": "pending", "repository": repo})
    assert _link_detail(c, task_id)["checks"]["ci/jenkins"] == "approval"
    G.pr_signal(
        c, "check_run", {"check_run": {**waiting, "status": "completed", "conclusion": "success"}, "repository": repo}
    )
    G.pr_signal(c, "status", {**said, "state": "success", "description": "Build passed", "repository": repo})
    assert "h7:pass" in _link_detail(c, task_id).get("checks_done", [])


def test_a_check_left_out_of_the_stored_copy_keeps_its_approval():
    assert G._dropped({"a": "approval", "b": "passing"}, {"b": "passing"}) == "approval"
    assert G._dropped({"a": "approval", "c": "failing"}, {}) == "failing"
    assert G._with_dropped("pending", "approval") == "approval"
    assert G._with_dropped("passing", None) == "passing"


def test_the_queue_is_refused_while_a_check_needs_approval_beside_a_failed_one(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
        head = F.pr(c, REPO, 1)["head_sha"]
    gh.set_checks(REPO, head, "failure", "approval")
    F.run(company, gh)
    assert not [call for call in gh.calls if call[0] in ("post_check", "enqueue")]
    with company.transaction() as c:
        assert stand_line(c, task_id, "pr_merged")[2] == "The checks on acme/app#1 need approval"
