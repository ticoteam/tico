"""A ticket's whole journey on the default company, in one Product task held by its feature owner (docs/pipelines-v3.md
"Product"): a person types a request, the owner pitches it and the person OKs the pitch, the owner writes the spec,
the bots it touches comment, and the person OKs the spec; the owner builds it, two reviewers review it and the owner
fixes what they found; the QA Owner builds the QA page and the person OKs it; the person OKs the release, the Release
Owner merges it, and the owner checks it in production.

The company is the one setup makes (one Manager per team, the built-ins under the General Manager, reach Nearby). Every
bot acts through the real `tico` CLI inside a run the computer claimed for it; the person acts through the API; only
GitHub and the model behind Jev are doubles (helpers/ticket_journey.py). One journey per test."""

import pytest

from backend.tests.helpers import ticket_journey as J
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.sample_company import booted, environment  # noqa: F401
from backend.tests.live import live  # noqa: F401
from evals.never_stuck.fake_jev import FakeJev

REPO = J.REPO
PR = f"https://github.com/{REPO}/pull/1"


@pytest.fixture
def made(environment):
    return J.default_company(environment)


@pytest.fixture
def api(made):
    return made[0]


@pytest.fixture
def co(made, live, tmp_path, monkeypatch, time_machine):
    """The default company with its seat bots live, GitHub connected with one repository, and a clock that can move."""
    jev = FakeJev()
    with jev.installed():
        # The legacy PR read behind `tico task link` would call GitHub itself: it finds nothing there.
        service = made[0].app.state.github_app
        nothing = type("Nothing", (), {"status_code": 404, "text": "", "json": lambda self: {}})
        monkeypatch.setattr(service, "mint", lambda *a, **k: ("t", "later"))
        monkeypatch.setattr(service, "_call", lambda *a, **k: nothing())
        (tmp_path / "ws").mkdir()
        company = J.Company(made[0], made[1], live, tmp_path / "ws", FakeGitHub(), jev, time_machine)
        company.go_live()
        company.github_connected()
        # The repository has made a release before.
        company.github.publish(REPO, "v1.0.0")
        yield company


def request(co, title="Add a negate function", body="negate(x) returns -x, with a test."):
    """The person types a request into Product for its feature owner. The task's body is the pitch."""
    sent = {"title": title, "body": body, "owner": co.actor(co.owner), "pipeline": "product"}
    made = co.person("POST", "tasks", sent)["task"]["id"]
    co.beat()
    return made


def pitch(co, task):
    """The pitch (the task's body, short enough) goes straight to the person, who OKs it."""
    co.sign_off(task)
    co.beat()


def spec(co, task, text="Goal, scope in and out, acceptance criteria."):
    """The owner writes the Spec page in Spec and Jev passes it. Returns the page's file id."""
    owner = co.handoff(task, "Spec", co.owner)
    page, _ = owner.page("spec.html", "Spec: negate", text)
    owner.attach("Spec page", page)
    owner.end()
    co.beat()
    return page


def related(co, task):
    """Related input never blocks: the bots Jev picks comment on the spec page, or nobody clears the bar, or 4 hours
    pass. Where the stage still asks its owner's done (before the related input kind), the owner says it."""
    if co.task(task)["stage"]["name"] != "Related input":
        return
    if any(r["kind"] == "owner_done" for r in co.task(task)["to_leave"]):
        owner = co.handoff(task, "Related input", co.owner)
        owner.cli("task", "done", task, "-m", "No comments came.", "--version", owner.version())
        owner.end()
        co.beat()
        return
    co.later(241)
    co.beat(2)


def ok_spec(co, task, page):
    """In OK spec the owner attaches the spec page as the comments left it, for the person's OK."""
    owner = co.handoff(task, "OK spec", co.owner)
    owner.attach("Spec page", page)
    owner.end()
    co.beat()


def to_spec(co, title="Add a negate function"):
    """A request pitched and its pitch OK'd: the task, in Spec."""
    task = request(co, title)
    pitch(co, task)
    return task


def to_build(co, title="Add a negate function"):
    """A request taken to Build in its one task: the task id."""
    task = to_spec(co, title)
    page = spec(co, task)
    related(co, task)
    ok_spec(co, task, page)
    co.sign_off(task, "Keep it tiny.")
    co.beat()
    return task


def build(co, task):
    """The owner opens a PR, links it, and its checks pass. Returns the head commit."""
    owner = co.handoff(task, "Build", co.owner)
    head = co.github.open_pr(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "pending")
    owner.cli("task", "link", task, PR)
    owner.end()
    co.heard(1)
    assert co.task(task)["stage"]["name"] == "Build", f"it left Build before the checks passed. {co.line(task)}"
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    return head


def review(co, task, stage, slug, head, found):
    """A reviewer is woken, reads the PR's commit, writes its Review page and attaches it."""
    reviewer = co.handoff(task, stage, slug)
    assert head[:12] in reviewer.wake + reviewer.show(), f"{slug} is not told the commit to review ({head[:12]})"
    page, _ = reviewer.page(f"{slug}.html", f"{stage} of {REPO}#1", f"Reviewed at {head}. {found}")
    reviewer.attach("Review page", page)
    reviewer.end()
    co.beat()


def fix(co, task, stage):
    """The owner is woken with the review, pushes a fix whose checks pass, and says it is done. Returns the head."""
    owner = co.handoff(task, stage, co.owner)
    head = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    owner.cli("task", "done", task, "-m", "Fixed what the review found.", "--version", owner.version())
    owner.end()
    co.beat()
    return head


def qa(co, task, head):
    """Review 2 found nothing: the owner says done in Fixes 2 at once, then the QA Owner attaches the QA page in QA."""
    owner = co.handoff(task, "Fixes 2", co.owner)
    owner.cli("task", "done", task, "-m", "Review 2 found nothing to fix.", "--version", owner.version())
    owner.end()
    co.beat()
    checker = co.handoff(task, "QA", "qa-owner")
    built, _ = checker.page("qa.html", "QA: negate", f"Built at {head}; checks pass; both reviews done.")
    checker.attach("QA page", built)
    checker.end()
    co.beat()


def to_release(co, task):
    """A task taken from Build through QA and the person's QA OK, to OK release. Returns the PR's head commit."""
    head = build(co, task)
    review(co, task, "Review 1", "reviewer-1", head, "One blocking issue: no test for negate(0).")
    head = fix(co, task, "Fixes 1")
    review(co, task, "Review 2", "reviewer-2", head, "Nothing blocking.")
    qa(co, task, head)
    co.sign_off(task, "Looks right.")
    co.beat()
    assert co.task(task)["stage"]["name"] == "OK release", f"the QA OK did not reach OK release. {co.line(task)}"
    return head


@pytest.mark.parametrize("webhooks", [True, False], ids=["webhooks", "github-webhooks-down"])
def test_a_request_goes_from_typed_to_production_in_one_task_with_the_persons_oks(co, webhooks):
    """The whole happy journey; once with GitHub's webhooks arriving and once with none (their outage), when only the
    5-minute re-read tells Tico what the PR did."""
    co.webhooks = webhooks
    before = co.needs_you()

    # Pitch: the body is the pitch; it reaches the person, who OKs it, and nothing else reaches them.
    task = request(co)
    assert co.new_in_list(before) == [("ask", task)], "the person's list should hold the pitch OK and no more"
    assert not [bot for bot, queued in co.queued() if queued == task], (
        "a bot is woken on a pitch that waits on a person"
    )
    co.sign_off(task)
    co.beat()
    assert co.new_in_list(before) == [], "the pitch OK is still in the person's list after they OK'd it"

    # Spec: the same owner writes it; the bots it touches comment; the person OKs it.
    spec_page = spec(co, task)
    related(co, task)
    ok_spec(co, task, spec_page)
    assert co.new_in_list(before) == [("ask", task)], "the person's list should hold the spec OK and no more"
    co.sign_off(task, "Keep it tiny.")
    co.beat()
    assert co.new_in_list(before) == [], "the spec OK is still in the person's list after they OK'd it"

    # Build: the same owner, in the same task, builds what it specified. No other task is filed.
    owner = co.handoff(task, "Build", co.owner)
    assert spec_page in owner.wake + owner.show(), "the owner is not told where the Spec page is"
    head = co.github.open_pr(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "pending")
    owner.cli("task", "link", task, PR)
    owner.end()
    co.heard(1)
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    assert not co.task(task)["filed"], "Product filed a second task: one task goes from pitch to production"

    # Two reviews, each sent back to the owner; the QA Owner's QA page, which the person OKs.
    review(co, task, "Review 1", "reviewer-1", head, "One blocking issue: no test for negate(0).")
    head = fix(co, task, "Fixes 1")
    review(co, task, "Review 2", "reviewer-2", head, "Nothing blocking.")
    qa(co, task, head)
    assert co.new_in_list(before) == [("ask", task)], "the person's list should hold the QA OK only"
    co.sign_off(task, "Looks right.")
    co.beat()

    # The release OK, then the merge queue.
    assert co.task(task)["stage"]["name"] == "OK release", f"the QA OK did not reach OK release. {co.line(task)}"
    assert co.new_in_list(before) == [("ask", task)], "the person's list should hold the release OK only"
    co.sign_off(task, "Ship it.")
    co.beat()
    assert co.task(task)["stage"]["name"] == "Merge", f"the OK'd release is not in Merge. {co.line(task)}"
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not put the OK'd PR in the merge queue"
    co.github.merge(REPO, 1)
    co.heard(1)

    # Validate: the owner checks it in production and the request is done.
    owner = co.handoff(task, "Validate", co.owner)
    wanted = [r for r in co.task(task)["to_leave"] if r["kind"] == "page"]
    checked, _ = owner.page("checked.html", "Checked in production: negate", "negate(2) returned -2 in production.")
    owner.attach(wanted[0]["line"], checked)
    owner.end()
    co.beat(2)
    assert co.task(task)["status"] == "done", f"the request is not done after it was checked. {co.line(task)}"
    assert co.new_in_list(before) == [], "the person's list holds something new after the request was released"
    co.clean()


def test_a_pitch_over_280_characters_goes_to_its_owner_to_shorten_before_a_person_sees_it(co):
    """The task's body is the pitch: over 280 characters it is its owner's to shorten, and nobody is asked to OK it."""
    before = co.needs_you()
    task = request(co, body="negate(x) returns -x. " * 20)
    assert co.new_in_list(before) == [], "a long pitch reached the person"
    owner = co.handoff(task, "Pitch", co.owner)
    assert "over 280" in owner.wake + owner.show(), "the owner is not told the pitch is too long"
    owner.cli("task", "update", task, "--body", "negate(x) returns -x, with a test.", "--version", owner.version())
    owner.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", task)], "the short pitch did not reach the person once"
    co.clean()


def test_the_task_names_its_repository_when_the_owner_starts_building(co):
    """A hosted company (Nov 11): a build task named no repository, so its builder worked in its own checkout and a
    person was asked which repository to use. The task is in the one repository In use: the owner's first turn in
    Build is handed it, with the command that makes a worktree of it, and nobody asks the person."""
    task = to_build(co)
    before = co.needs_you()
    owner = co.handoff(task, "Build", co.owner)
    assert f"Repository: {REPO}. Work on it in a worktree: `tico task worktree add {REPO}`." in owner.wake, (
        f"the owner's first wake in Build does not name the repository: {owner.wake[:600]}"
    )
    assert owner.attempt.get("github_repository") == REPO, "the owner's turn is not given the repository"
    assert f"Repository: {REPO}" in owner.show(), "`tico task show` does not name the repository"
    assert co.task(task)["repositories"] == [REPO]
    owner.end()
    co.beat()
    assert co.new_in_list(before) == [], "the person was asked something on the owner's first turn in Build"
    co.clean()


def test_a_new_commit_after_the_review_page_goes_back_to_the_same_reviewer(co):
    """The owner pushes right after Reviewer 1 attached its page: the review of the old commit does not carry the PR
    on. The reviewer is woken at once, told the new commit, and its second page moves the task."""
    task = to_build(co)
    head = build(co, task)
    reviewer = co.handoff(task, "Review 1", "reviewer-1")
    page, _ = reviewer.page("reviewer-1.html", f"Review 1 of {REPO}#1", f"Reviewed at {head}. Nothing blocking.")
    reviewer.attach("Review page", page)
    reviewer.end()
    newer = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, newer, "success")
    co.heard(1)
    co.beat()
    assert co.task(task)["stage"]["name"] == "Review 1", (
        f"a review of the old commit carried the PR past Review 1. {co.line(task)}"
    )
    review(co, task, "Review 1", "reviewer-1", newer, "Nothing blocking.")
    co.handoff(task, "Fixes 1", co.owner).end()
    co.clean()


def test_the_person_sends_the_spec_back_and_the_owner_writes_it_again(co):
    before = co.needs_you()
    task = to_spec(co)
    page = spec(co, task)
    related(co, task)
    ok_spec(co, task, page)
    asks = [i for i in co.needs_you() if i["task_id"] == task and i["kind"] == "ask"]
    assert "no" in asks[0]["ask"]["may"], f"the person cannot send the spec back: {asks[0]['ask']}"
    co.person(
        "POST",
        f"tasks/{task}/asks/{asks[0]['requirement_id']}/answer",
        {"value": "no", "note": "Say what is out of scope."},
    )
    co.beat()
    assert co.new_in_list(before) == [], "a spec sent back is still in the person's list"
    owner = co.handoff(task, "OK spec", co.owner)
    assert "Say what is out of scope." in owner.wake + owner.show(), "the owner is not shown why it came back"
    page, _ = owner.page("spec.html", "Spec: negate", "Goal, scope in and out (no floats), acceptance criteria.")
    owner.attach("Spec page", page)
    owner.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", task)], "the rewritten spec did not come back to the person once"
    co.sign_off(task)
    co.beat()
    co.handoff(task, "Build", co.owner).end()
    co.clean()


def test_an_owner_that_keeps_stopping_goes_to_the_manager_who_can_settle_it(co):
    """The owner ends run after run in Build without doing the work: it is woken again after 15 minutes, 15 minutes
    and an hour (a run that finished is not woken 2 minutes on), then the task goes to the pipeline's Manager, who can
    reassign it, snooze it, move it on and move it back; a person's request is theirs to close."""
    before = co.needs_you()
    task = to_build(co)
    co.handoff(task, "Build", co.owner).end("I could not do it.")
    for minutes in (16, 16, 61):
        co.later(minutes)
        co.beat()
        again = co.turn_on(co.owner, task, f"again {minutes} minutes after it stopped")
        again.end("I could not do it.")
    co.later(3)
    co.beat()
    woken = [bot for bot, queued in co.queued() if queued == task]
    assert woken == ["product-manager"], f"after the owner stopped for good, the queued wakes are {woken}"
    manager = co.turn_on("product-manager", task, "after the owner stopped")
    assert co.new_in_list(before) == [], "a stopped owner reached the person before the Manager had a try"

    def settle(*argv):
        manager.cli("task", *argv, "--version", manager.version(task))

    settle("reassign", task, "reviewer-1", "-m", "The owner is stuck.")
    assert co.task(task)["owner_id"] == co.actor("reviewer-1"), f"the reassign did not take. {co.line(task)}"
    settle("reassign", task, co.owner, "-m", "Back to its owner.")
    settle("snooze", task, "--until", "2099-01-01", "-m", "Waits for a grant.")
    manager.cli("task", "unsnooze", task)
    settle("move", task, "next", "-m", "Built elsewhere.")
    assert co.task(task)["stage"]["name"] == "Review 1", f"the move on did not take. {co.line(task)}"
    settle("move", task, "Build", "-m", "Build it again.")
    assert co.task(task)["stage"]["name"] == "Build", f"the move back did not take. {co.line(task)}"
    # A person asked for it: the Manager asks them rather than closing it.
    code, out, err = manager.cli(
        "task", "close", task, "-m", "Duplicate.", "--version", manager.version(task), ok=False
    )
    assert code != 0 and "only a person can close it" in out + err, f"the Manager closed a person's request: {out}{err}"
    manager.end()
    co.clean(expected=("only a person can close it",))


def test_qa_files_a_product_bug_for_its_areas_feature_owner(co):
    """No Builders: the QA Owner files a product bug a test exposes as a Product task for the feature owner of its
    area. A bot's pitch is not set aside: it reaches the person's list, and once they OK it the owner is woken in
    Spec."""
    before = co.needs_you()
    qa = co.turn("qa-owner")
    assert qa, "the QA Owner has no run on a new company"
    qa.cli(
        "task",
        "create",
        "--pipeline",
        "product",
        "--owner",
        co.owner,
        "--title",
        "negate(0) returns -0",
        "--body",
        "test_negate_zero fails on main.",
    )
    qa.end()
    found = co.rows("SELECT id FROM tasks WHERE title='negate(0) returns -0'")
    assert len(found) == 1, f"QA's report made {len(found)} tasks, not one"
    co.beat(2)
    assert co.new_in_list(before) == [("ask", found[0]["id"])], "the bot's pitch did not reach the person's list"
    co.sign_off(found[0]["id"])
    co.beat()
    co.handoff(found[0]["id"], "Spec", co.owner).end()
    co.clean()


def test_a_question_to_the_person_waits_in_their_list_and_the_answer_wakes_the_asker(co):
    """The owner needs an answer only the person has: the task waits on "Answer from someone", the person sees one
    item, and their answer wakes the owner with it."""
    task = to_spec(co)
    before = co.needs_you()
    owner = co.handoff(task, "Spec", co.owner)
    owner.cli(
        "task",
        "ask",
        task,
        "--to",
        "owner",
        "Integers only?",
        "--why",
        "It decides the input type.",
        "--version",
        owner.version(),
    )
    owner.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", task)], "the question did not reach the person's list once"
    assert not [bot for bot, queued in co.queued() if queued == task], "a bot is woken on a task that waits on a person"
    ask = next(i for i in co.needs_you() if i["task_id"] == task)
    co.person("POST", f"tasks/{task}/asks/{ask['requirement_id']}/answer", {"value": "Integers only."})
    co.beat()
    assert co.new_in_list(before) == [], "the answered question is still in the person's list"
    owner = co.handoff(task, "Spec", co.owner)
    assert "Integers only." in owner.wake + owner.show(), "the owner is not shown the person's answer"
    owner.end()
    co.clean()


def test_the_merge_queue_drops_the_pr_and_the_release_owner_queues_it_again(co):
    """GitHub takes the OK'd PR out of the merge queue because its run was cancelled from outside: the Release Owner is
    woken with why, puts it back with `tico task queue`, and it merges. A removal that names a failed check is not
    queued again as it is (test_github_facts_merge_queue)."""
    task = to_build(co)
    to_release(co, task)
    before = co.needs_you()
    co.sign_off(task, "Ship it.")
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not put the OK'd PR in the merge queue"
    co.github.drop(
        REPO, 1, "Checks failed in the merge queue", check="no step failed: the run was cancelled from outside"
    )
    co.heard(1)
    assert co.task(task)["stage"]["name"] == "Merge", f"a dropped PR's task left Merge. {co.line(task)}"
    woken = [bot for bot, queued in co.queued() if queued == task]
    assert woken == ["release-owner"], f"after the queue dropped the PR, the queued wakes are {woken}"
    releaser = co.turn_on("release-owner", task, "after the merge queue dropped its PR")
    assert "Checks failed in the merge queue" in releaser.wake, (
        f"the Release Owner is not told why: {releaser.wake[:400]}"
    )
    releaser.cli("task", "queue", task, "--version", releaser.version())
    releaser.end()
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "`tico task queue` did not put the PR back in the queue"
    co.github.merge(REPO, 1)
    co.heard(1)
    assert co.task(task)["stage"]["name"] == "Validate", f"the merged PR's task is not in Validate. {co.line(task)}"
    assert [i for i in co.new_in_list(before) if i[0] == "ask"] == [], "a PR queued again reached the person"
    co.clean()


def test_after_the_oks_a_rebase_is_queued_again_and_a_conflict_fix_is_read_by_reviewer_2(co):
    """Velocity (~100 PRs a day, Nov 12): main moves under an OK'd PR. A rebase changes the same lines, so the Release
    Owner queues it again. A conflict fix that changes the PR's lines goes back to QA, where Reviewer 2 reads it; its OK
    keeps the person's QA OK. The release OK is asked again: it is an OK of the release, and the PR changed."""
    task = to_build(co)
    to_release(co, task)
    co.sign_off(task, "Ship it.")
    co.beat()
    before = co.needs_you()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not put the OK'd PR in the merge queue"

    rebased = co.github.rebase(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, rebased, "success")
    co.heard(1)
    releaser = co.turn_on("release-owner", task, "after the rebase took the PR out of the queue")
    releaser.cli("task", "queue", task, "--version", releaser.version())
    releaser.end()
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "the rebase was not queued again"

    fixed = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, fixed, "success")
    co.heard(1)
    releaser = co.turn_on("release-owner", task, "after the conflict fix took the PR out of the queue")
    code, out, err = releaser.cli("task", "queue", task, "--version", releaser.version(), ok=False)
    assert code != 0 and "Move the task back to QA" in out + err, f"a bot queued a changed PR: {out}{err}"
    releaser.cli("task", "move", task, "QA", "-m", "Fixed the conflict in negate.", "--version", releaser.version())
    releaser.end()
    co.beat()
    assert co.new_in_list(before) == [], "the person was asked about the conflict fix before Reviewer 2 read it"
    reviewer = co.turn_on("reviewer-2", task, "to read the conflict fix")
    assert fixed[:7] in reviewer.wake + reviewer.show(), "Reviewer 2 is not told the commit to read"
    page = [r for r in co.task(task)["to_leave"] if r["kind"] == "page"]
    reviewer.cli(
        "task",
        "answer",
        task,
        page[0]["requirement_id"],
        "ok",
        "-m",
        f"Read {fixed[:7]}: both changes kept.",
        "--version",
        reviewer.version(),
    )
    reviewer.end()
    co.beat()
    assert co.task(task)["stage"]["name"] == "OK release", (
        f"Reviewer 2's OK did not take it on to OK release. {co.line(task)}"
    )
    assert co.new_in_list(before) == [("ask", task)], "the release OK was not asked once more"
    co.sign_off(task, "Ship it again.")
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "the read conflict fix was not queued"
    co.github.merge(REPO, 1)
    co.heard(1)
    assert co.task(task)["stage"]["name"] == "Validate", f"the merged PR's task is not in Validate. {co.line(task)}"
    co.clean(expected=("Move the task back to QA",))


def test_the_person_sends_the_qa_page_back_and_the_owner_fixes_it_in_fixes_2(co):
    """The person sends the build back at the QA OK: the QA Owner is woken with the reason and moves the task back to
    Fixes 2, where its owner fixes it in the same task; the QA Owner attaches the page again, Reviewer 2 reads the new
    commit, and the person is asked once more."""
    before = co.needs_you()
    task = to_build(co)
    head = build(co, task)
    review(co, task, "Review 1", "reviewer-1", head, "One blocking issue: no test for negate(0).")
    head = fix(co, task, "Fixes 1")
    review(co, task, "Review 2", "reviewer-2", head, "Nothing blocking.")
    qa(co, task, head)
    asks = [i for i in co.needs_you() if i["task_id"] == task and i["kind"] == "ask"]
    assert len(asks) == 1 and "no" in asks[0]["ask"]["may"], f"the person cannot send the build back: {asks}"
    co.person(
        "POST",
        f"tasks/{task}/asks/{asks[0]['requirement_id']}/answer",
        {"value": "no", "note": "negate(None) must raise."},
    )
    co.beat()
    assert co.new_in_list(before) == [], "a build sent back is still in the person's list"
    checker = co.handoff(task, "QA", "qa-owner")
    assert "negate(None) must raise." in checker.wake + checker.show(), "the QA Owner is not shown why it came back"
    checker.cli("task", "move", task, "Fixes 2", "-m", "negate(None) must raise.", "--version", checker.version())
    checker.end()
    co.beat()
    owner = co.handoff(task, "Fixes 2", co.owner)
    head = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    owner.cli("task", "done", task, "-m", "negate(None) raises.", "--version", owner.version())
    owner.end()
    co.beat()
    checker = co.handoff(task, "QA", "qa-owner")
    built, _ = checker.page("qa.html", "QA: negate", f"Built at {head}; negate(None) raises.")
    checker.attach("QA page", built)
    checker.end()
    co.beat()
    assert co.new_in_list(before) == [], "the person was asked before Reviewer 2 read the new commit"
    reviewer = co.turn_on("reviewer-2", task, "to review the commit pushed after Review 2")
    assert head[:7] in reviewer.wake + reviewer.show(), "Reviewer 2 is not told the new commit to read"
    page = [r for r in co.task(task)["to_leave"] if r["kind"] == "page"]
    reviewer.cli(
        "task",
        "answer",
        task,
        page[0]["requirement_id"],
        "ok",
        "-m",
        f"Read {head[:7]}: negate(None) raises.",
        "--version",
        reviewer.version(),
    )
    reviewer.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", task)], "the second round did not reach the person once"
    co.sign_off(task, "Looks right.")
    co.beat()
    assert co.task(task)["stage"]["name"] == "OK release", f"the second QA OK did not reach OK release. {co.line(task)}"
    co.clean()
