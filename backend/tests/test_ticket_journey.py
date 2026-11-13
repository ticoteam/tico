"""A ticket's whole journey on the default company: a person types a request, the Product Owner writes the spec, the
person signs it off, the Builder builds it, two reviewers review it, the person signs off before release, the Release
Owner releases it, and the Product Owner checks it in production.

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
        # The repository has made a release before, so a merged PR waits in Release for the next one.
        company.github.publish(REPO, "v1.0.0")
        yield company


def request(co, title="Add a negate function"):
    """The person types a request into Product."""
    body = {"title": title, "body": "negate(x) returns -x, with a test.", "owner": "human:owner", "pipeline": "product"}
    made = co.person("POST", "tasks", body)["task"]["id"]
    co.beat()
    return made


def spec(co, product, text="Goal, scope in and out, acceptance criteria."):
    """The Product Owner is woken in Spec, writes the Spec page and attaches it. Returns the page's file id."""
    owner = co.handoff(product, "Spec", "product-owner")
    page, _ = owner.page("spec.html", "Spec: negate", text)
    owner.attach("Spec page", page)
    owner.end()
    co.beat()
    return page


def to_build(co, title="Add a negate function"):
    """A request taken to Build: (Product task, Engineering task)."""
    product = request(co, title)
    spec(co, product)
    co.sign_off(product, "Keep it tiny.")
    co.beat()
    filed = co.task(product)["filed"]
    assert len(filed) == 1, f"the signed-off spec filed {len(filed)} Engineering tasks, not one. {co.line(product)}"
    return product, filed[0]["id"]


def build(co, engineering):
    """The Builder opens a PR, links it, and its checks pass. Returns the head commit."""
    builder = co.handoff(engineering, "Build", "builder")
    head = co.github.open_pr(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "pending")
    builder.cli("task", "link", engineering, PR)
    builder.end()
    co.heard(1)
    assert co.task(engineering)["stage"]["name"] == "Build", (
        f"it left Build before the checks passed. {co.line(engineering)}"
    )
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    return head


def review(co, engineering, stage, slug, head, found):
    """A reviewer is woken, reads the PR's commit, writes its Review page and attaches it."""
    reviewer = co.handoff(engineering, stage, slug)
    assert head[:12] in reviewer.wake + reviewer.show(), f"{slug} is not told the commit to review ({head[:12]})"
    page, _ = reviewer.page(f"{slug}.html", f"{stage} of {REPO}#1", f"Reviewed at {head}. {found}")
    reviewer.attach("Review page", page)
    reviewer.end()
    co.beat()


def fix(co, engineering, stage):
    """The Builder is woken with the review, pushes a fix whose checks pass, and says it is done. Returns the head."""
    builder = co.handoff(engineering, stage, "builder")
    head = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    builder.cli("task", "done", engineering, "-m", "Fixed what the review found.", "--version", builder.version())
    builder.end()
    co.beat()
    return head


def qa(co, engineering, head):
    """Review 2 found nothing: the Builder says done in Fixes 2 at once, then attaches the QA page in QA."""
    builder = co.handoff(engineering, "Fixes 2", "builder")
    builder.cli("task", "done", engineering, "-m", "Review 2 found nothing to fix.", "--version", builder.version())
    builder.end()
    co.beat()
    builder = co.handoff(engineering, "QA", "builder")
    built, _ = builder.page("qa.html", "QA: negate", f"Built at {head}; checks pass; both reviews done.")
    builder.attach("QA page", built)
    builder.end()
    co.beat()


@pytest.mark.parametrize("webhooks", [True, False], ids=["webhooks", "github-webhooks-down"])
def test_a_request_goes_from_typed_to_released_with_two_sign_offs(co, webhooks):
    """The whole happy journey; once with GitHub's webhooks arriving and once with none (their outage), when only the
    5-minute re-read tells Tico what the PR did."""
    co.webhooks = webhooks
    before = co.needs_you()

    # Product: the request reaches the Product Owner, the spec reaches the person, and nothing else does.
    product = request(co)
    spec_page = spec(co, product)
    assert co.new_in_list(before) == [("ask", product)], "the person's list should hold the spec sign-off and no more"
    co.sign_off(product, "Keep it tiny.")
    co.beat()
    assert co.new_in_list(before) == [], "the spec sign-off is still in the person's list after they signed"
    filed = co.task(product)["filed"]
    assert len(filed) == 1, f"the signed-off spec filed {len(filed)} Engineering tasks, not one. {co.line(product)}"
    engineering = filed[0]["id"]

    # Build: the Builder reads the spec, asks the Product Owner about it and gets the answer.
    builder = co.handoff(engineering, "Build", "builder")
    assert spec_page in builder.wake + builder.show(), "the Builder is not told where the Spec page is"
    builder.cli(
        "task", "ask", engineering, "--to", "product-owner", "Should negate(0) be 0?", "--version", builder.version()
    )
    builder.end()
    owner = co.turn_on("product-owner", engineering, "to answer the Builder's question")
    assert "Should negate(0) be 0?" in owner.wake + owner.show(engineering), (
        "the Product Owner is not shown the question"
    )
    asked = [r for r in co.task(engineering)["to_leave"] if r["kind"] == "answer"]
    owner.cli(
        "task", "answer", engineering, asked[0]["requirement_id"], "Yes, 0.", "--version", owner.version(engineering)
    )
    owner.end()
    builder = co.turn_on("builder", engineering, "with the Product Owner's answer")
    assert "Yes, 0." in builder.wake + builder.show(), "the Builder is not shown the Product Owner's answer"
    head = co.github.open_pr(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "pending")
    builder.cli("task", "link", engineering, PR)
    builder.end()
    co.heard(1)
    co.github.set_checks(REPO, head, "success")
    co.heard(1)

    # Two reviews, each sent back to the Builder.
    review(co, engineering, "Review 1", "reviewer-1", head, "One blocking issue: no test for negate(0).")
    head = fix(co, engineering, "Fixes 1")
    review(co, engineering, "Review 2", "reviewer-2", head, "Nothing blocking.")
    qa(co, engineering, head)

    # The second sign-off, then the merge queue.
    assert co.new_in_list(before) == [("ask", engineering)], (
        "the person's list should hold the pre-release sign-off only"
    )
    co.sign_off(engineering, "Ship it.")
    co.beat()
    assert co.task(engineering)["stage"]["name"] == "Merge", (
        f"the signed-off PR is not in Merge. {co.line(engineering)}"
    )
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not put the signed-off PR in the merge queue"
    co.github.merge(REPO, 1)
    co.heard(1)

    # Release: with no "Release with" set (the default) the task waits for the repository's next release, on nobody.
    waiting = co.task(engineering)
    assert waiting["stage"]["name"] == "Release", f"the merged PR's task is not in Release. {co.line(engineering)}"
    assert waiting["owner_id"] == co.actor("release-owner"), (
        f"Release is not the Release Owner's. {co.line(engineering)}"
    )
    assert "next release" in waiting["turn"]["line"], f"Release does not say what it waits for. {co.line(engineering)}"
    co.github.publish(REPO, "v1.0.1")
    co.later(6)
    co.beat(2)
    assert co.task(engineering)["status"] == "done", f"the released task is not done. {co.line(engineering)}"

    # Back in Product: the Product Owner checks it in production and the request is done.
    owner = co.handoff(product, "Validate", "product-owner")
    wanted = [r for r in co.task(product)["to_leave"] if r["kind"] == "page"]
    checked, _ = owner.page("checked.html", "Checked in production: negate", "negate(2) returned -2 in production.")
    owner.attach(wanted[0]["line"], checked)
    owner.end()
    co.beat(2)
    assert co.task(product)["status"] == "done", f"the request is not done after it was checked. {co.line(product)}"
    assert co.new_in_list(before) == [], "the person's list holds something new after the request was released"
    co.clean()


def test_the_filed_task_names_its_repository_before_any_pr(co):
    """A hosted company (Nov 11): the Engineering task the spec filed named no repository, so the Builder worked in
    its own checkout, asked the Engineering Manager, who asked the person which repository to use. The filed task is
    in the one repository In use from the start: the Builder's first turn is handed it, with the command that makes a
    worktree of it, and nobody asks the person."""
    _, engineering = to_build(co)
    before = co.needs_you()
    builder = co.handoff(engineering, "Build", "builder")
    assert f"Repository: {REPO}. Work on it in a worktree: `tico task worktree add {REPO}`." in builder.wake, (
        f"the Builder's first wake does not name the repository: {builder.wake[:600]}"
    )
    assert builder.attempt.get("github_repository") == REPO, "the Builder's first turn is not given the repository"
    assert f"Repository: {REPO}" in builder.show(), "`tico task show` does not name the repository"
    assert co.task(engineering)["repositories"] == [REPO]
    # The Builder may read which repositories it can use.
    assert REPO in builder.cli("bot", "repos", "builder")[1]
    builder.end()
    co.beat()
    assert co.new_in_list(before) == [], "the person was asked something on the Builder's first turn"
    co.clean()


def test_a_new_commit_after_the_review_page_goes_back_to_the_same_reviewer(co):
    """The Builder pushes right after Reviewer 1 attached its page: the review of the old commit does not carry the PR
    on. The reviewer is woken at once, told the new commit, and its second page moves the task."""
    _, engineering = to_build(co)
    head = build(co, engineering)
    reviewer = co.handoff(engineering, "Review 1", "reviewer-1")
    page, _ = reviewer.page("reviewer-1.html", f"Review 1 of {REPO}#1", f"Reviewed at {head}. Nothing blocking.")
    reviewer.attach("Review page", page)
    reviewer.end()
    newer = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, newer, "success")
    co.heard(1)
    co.beat()
    assert co.task(engineering)["stage"]["name"] == "Review 1", (
        f"a review of the old commit carried the PR past Review 1. {co.line(engineering)}"
    )
    review(co, engineering, "Review 1", "reviewer-1", newer, "Nothing blocking.")
    co.handoff(engineering, "Fixes 1", "builder").end()
    co.clean()


def test_the_person_sends_the_spec_back_and_the_product_owner_writes_it_again(co):
    before = co.needs_you()
    product = request(co)
    spec(co, product)
    asks = [i for i in co.needs_you() if i["task_id"] == product and i["kind"] == "ask"]
    assert "no" in asks[0]["ask"]["may"], f"the person cannot send the spec back: {asks[0]['ask']}"
    co.person(
        "POST",
        f"tasks/{product}/asks/{asks[0]['requirement_id']}/answer",
        {"value": "no", "note": "Say what is out of scope."},
    )
    co.beat()
    assert co.new_in_list(before) == [], "a spec sent back is still in the person's list"
    owner = co.handoff(product, "Spec", "product-owner")
    assert "Say what is out of scope." in owner.wake + owner.show(), "the Product Owner is not shown why it came back"
    page, _ = owner.page("spec.html", "Spec: negate", "Goal, scope in and out (no floats), acceptance criteria.")
    owner.attach("Spec page", page)
    owner.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", product)], "the rewritten spec did not come back to the person once"
    co.sign_off(product)
    co.beat()
    filed = co.task(product)["filed"]
    assert len(filed) == 1, f"the spec signed off on its second round filed {len(filed)} Engineering tasks, not one"
    co.handoff(filed[0]["id"], "Build", "builder").end()
    co.clean()


def test_a_builder_that_keeps_stopping_goes_to_the_engineering_manager_who_can_settle_it(co):
    """The Builder ends run after run without doing the work: it is woken again after 2 minutes, 15 minutes and an
    hour, then the task goes to the pipeline's Manager, who can reassign it, snooze it, move it on, move it back and
    close it."""
    before = co.needs_you()
    product, engineering = to_build(co)
    co.handoff(engineering, "Build", "builder").end("I could not do it.")
    for minutes in (3, 16, 61):
        co.later(minutes)
        co.beat()
        again = co.turn_on("builder", engineering, f"again {minutes} minutes after it stopped")
        again.end("I could not do it.")
    co.later(3)
    co.beat()
    woken = [bot for bot, task in co.queued() if task == engineering]
    assert woken == ["engineering-manager"], f"after the Builder stopped for good, the queued wakes are {woken}"
    manager = co.turn_on("engineering-manager", engineering, "after the Builder stopped")
    assert "Builder" in manager.wake, f"the Manager is not told who stopped: {manager.wake[:300]}"
    assert co.new_in_list(before) == [], "a stopped Builder reached the person before the Manager had a try"

    def settle(*argv):
        manager.cli("task", *argv, "--version", manager.version(engineering))

    settle("reassign", engineering, "reviewer-1", "-m", "The Builder is stuck.")
    assert co.task(engineering)["owner_id"] == co.actor("reviewer-1"), (
        f"the reassign did not take. {co.line(engineering)}"
    )
    settle("reassign", engineering, "builder", "-m", "Back to the Builder.")
    settle("snooze", engineering, "--until", "2099-01-01", "-m", "Waits for a grant.")
    manager.cli("task", "unsnooze", engineering)
    settle("move", engineering, "next", "-m", "Built elsewhere.")
    assert co.task(engineering)["stage"]["name"] == "Review 1", f"the move on did not take. {co.line(engineering)}"
    settle("move", engineering, "Build", "-m", "Build it again.")
    assert co.task(engineering)["stage"]["name"] == "Build", f"the move back did not take. {co.line(engineering)}"
    settle("close", engineering, "-m", "Duplicate.")
    manager.end()
    assert co.task(engineering)["status"] != "open", f"the close did not take. {co.line(engineering)}"
    co.beat()
    # The request's Product task hears of it: the Product Owner is woken to decide what becomes of the request.
    owner = co.turn_on("product-owner", product, "after its Engineering task was closed")
    assert "Duplicate" in owner.wake + owner.show(product), "the Product Owner is not told why the build was closed"
    owner.end()
    co.clean()


def test_qa_fails_a_build_and_its_report_reaches_the_builder(co):
    """The QA Owner finds a failure and files it in Engineering for the Builder, who is woken on it in Build."""
    qa = co.turn("qa-owner")
    assert qa, "the QA Owner has no run on a new company"
    qa.cli(
        "task",
        "create",
        "--pipeline",
        "engineering",
        "--owner",
        "builder",
        "--title",
        "negate(0) returns -0",
        "--body",
        "test_negate_zero fails on main.",
    )
    qa.end()
    found = co.rows("SELECT id FROM tasks WHERE title='negate(0) returns -0'")
    assert len(found) == 1, f"QA's report made {len(found)} tasks, not one"
    report = found[0]["id"]
    co.beat(2)
    co.handoff(report, "Build", "builder").end()
    co.clean()


def test_a_question_to_the_person_waits_in_their_list_and_the_answer_wakes_the_asker(co):
    """The Product Owner needs an answer only the person has: the task waits on "Answer from someone", the person
    sees one item, and their answer wakes the Product Owner with it."""
    before = co.needs_you()
    product = request(co)
    owner = co.handoff(product, "Spec", "product-owner")
    owner.cli("task", "ask", product, "--to", "owner", "Integers only?", "--version", owner.version())
    owner.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", product)], "the question did not reach the person's list once"
    assert not [bot for bot, task in co.queued() if task == product], "a bot is woken on a task that waits on a person"
    ask = next(i for i in co.needs_you() if i["task_id"] == product)
    co.person("POST", f"tasks/{product}/asks/{ask['requirement_id']}/answer", {"value": "Integers only."})
    co.beat()
    assert co.new_in_list(before) == [], "the answered question is still in the person's list"
    owner = co.handoff(product, "Spec", "product-owner")
    assert "Integers only." in owner.wake + owner.show(), "the Product Owner is not shown the person's answer"
    owner.end()
    co.clean()


def to_sign_off(co, engineering):
    """An Engineering task taken from Build to the pre-release sign-off. Returns the PR's head commit."""
    head = build(co, engineering)
    review(co, engineering, "Review 1", "reviewer-1", head, "One blocking issue: no test for negate(0).")
    head = fix(co, engineering, "Fixes 1")
    review(co, engineering, "Review 2", "reviewer-2", head, "Nothing blocking.")
    qa(co, engineering, head)
    return head


def test_the_merge_queue_drops_the_pr_and_the_release_owner_queues_it_again(co):
    """GitHub takes the signed-off PR out of the merge queue because its run was cancelled from outside: the Release Owner
    is woken with why, puts it back with `tico task queue`, and it merges. A removal that names a failed check is not
    queued again as it is (test_github_facts_merge_queue)."""
    before = co.needs_you()
    _, engineering = to_build(co)
    to_sign_off(co, engineering)
    co.sign_off(engineering, "Ship it.")
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not put the signed-off PR in the merge queue"
    co.github.drop(
        REPO, 1, "Checks failed in the merge queue", check="no step failed: the run was cancelled from outside"
    )
    co.heard(1)
    assert co.task(engineering)["stage"]["name"] == "Merge", f"a dropped PR's task left Merge. {co.line(engineering)}"
    woken = [bot for bot, task in co.queued() if task == engineering]
    assert woken == ["release-owner"], f"after the queue dropped the PR, the queued wakes are {woken}"
    releaser = co.turn_on("release-owner", engineering, "after the merge queue dropped its PR")
    assert "Checks failed in the merge queue" in releaser.wake, (
        f"the Release Owner is not told why: {releaser.wake[:400]}"
    )
    releaser.cli("task", "queue", engineering, "--version", releaser.version())
    releaser.end()
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "`tico task queue` did not put the PR back in the queue"
    co.github.merge(REPO, 1)
    co.heard(1)
    assert co.task(engineering)["stage"]["name"] == "Release", (
        f"the merged PR's task is not in Release. {co.line(engineering)}"
    )
    assert co.new_in_list(before) == [], "a PR dropped from the queue and queued again reached the person's list"
    co.clean()


def test_after_the_sign_off_a_rebase_is_queued_again_and_a_conflict_fix_is_read_by_reviewer_2_not_the_person(co):
    """Velocity (~100 PRs a day, Nov 12): main moves under a signed-off PR. A rebase changes the same lines, so the
    Release Owner queues it again. A conflict fix that changes the PR's lines goes back to QA, where Reviewer 2
    reads it; its OK keeps the person's, and the PR merges. The person is asked once, at the sign-off."""
    before = co.needs_you()
    _, engineering = to_build(co)
    to_sign_off(co, engineering)
    co.sign_off(engineering, "Ship it.")
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not put the signed-off PR in the merge queue"

    rebased = co.github.rebase(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, rebased, "success")
    co.heard(1)
    releaser = co.turn_on("release-owner", engineering, "after the rebase took the PR out of the queue")
    releaser.cli("task", "queue", engineering, "--version", releaser.version())
    releaser.end()
    co.beat()
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "the rebase was not queued again"

    fixed = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, fixed, "success")
    co.heard(1)
    releaser = co.turn_on("release-owner", engineering, "after the conflict fix took the PR out of the queue")
    code, out, err = releaser.cli("task", "queue", engineering, "--version", releaser.version(), ok=False)
    assert code != 0 and "Move the task back to QA" in out + err, f"a bot queued a changed PR: {out}{err}"
    releaser.cli(
        "task", "move", engineering, "QA", "-m", "Fixed the conflict in negate.", "--version", releaser.version()
    )
    releaser.end()
    co.beat()
    assert co.new_in_list(before) == [], "the person was asked about the conflict fix before Reviewer 2 read it"
    reviewer = co.turn_on("reviewer-2", engineering, "to read the conflict fix")
    assert fixed[:7] in reviewer.wake + reviewer.show(), "Reviewer 2 is not told the commit to read"
    page = [r for r in co.task(engineering)["to_leave"] if r["kind"] == "page"]
    reviewer.cli(
        "task",
        "answer",
        engineering,
        page[0]["requirement_id"],
        "ok",
        "-m",
        f"Read {fixed[:7]}: both changes kept.",
        "--version",
        reviewer.version(),
    )
    reviewer.end()
    co.beat()
    assert co.task(engineering)["stage"]["name"] == "Merge", (
        f"Reviewer 2's OK did not take it back to Merge. {co.line(engineering)}"
    )
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "the read conflict fix was not queued"
    co.github.merge(REPO, 1)
    co.heard(1)
    assert co.task(engineering)["stage"]["name"] == "Release", (
        f"the merged PR's task is not in Release. {co.line(engineering)}"
    )
    assert co.new_in_list(before) == [], "the person was asked again after the sign-off"
    co.clean(expected=("Move the task back to QA",))


def test_the_person_says_no_before_release_and_the_second_round_is_signed_off_once_more(co):
    """The person sends the build back at the pre-release sign-off: the Builder is woken with the reason, fixes it,
    attaches the page again, and one more sign-off takes the same task to the merge queue."""
    before = co.needs_you()
    product, engineering = to_build(co)
    to_sign_off(co, engineering)
    asks = [i for i in co.needs_you() if i["task_id"] == engineering and i["kind"] == "ask"]
    assert len(asks) == 1 and "no" in asks[0]["ask"]["may"], f"the person cannot send the build back: {asks}"
    answer = f"tasks/{engineering}/asks/{asks[0]['requirement_id']}/answer"
    co.person("POST", answer, {"value": "no", "note": "negate(None) must raise."})
    co.beat()
    assert co.new_in_list(before) == [], "a build sent back is still in the person's list"
    builder = co.handoff(engineering, "QA", "builder")
    assert "negate(None) must raise." in builder.wake + builder.show(), "the Builder is not shown why it came back"
    head = co.github.push(REPO, 1, head=co.commit())
    co.github.set_checks(REPO, head, "success")
    co.heard(1)
    built, _ = builder.page("qa.html", "QA: negate", f"Built at {head}; negate(None) raises.")
    builder.attach("QA page", built)
    builder.end()
    co.beat()
    assert co.new_in_list(before) == [], "the person was asked before Reviewer 2 read the new commit"
    reviewer = co.turn_on("reviewer-2", engineering, "to review the commit pushed after Review 2")
    assert head[:7] in reviewer.wake + reviewer.show(), "Reviewer 2 is not told the new commit to read"
    page = [r for r in co.task(engineering)["to_leave"] if r["kind"] == "page"]
    reviewer.cli(
        "task",
        "answer",
        engineering,
        page[0]["requirement_id"],
        "ok",
        "-m",
        f"Read {head[:7]}: negate(None) raises.",
        "--version",
        reviewer.version(),
    )
    reviewer.end()
    co.beat()
    assert co.new_in_list(before) == [("ask", engineering)], "the second round did not reach the person once"
    co.sign_off(engineering, "Ship it.")
    co.beat()
    assert co.task(engineering)["stage"]["name"] == "Merge", (
        f"the second sign-off did not reach Merge. {co.line(engineering)}"
    )
    assert co.github.prs[(REPO, 1)]["queue_state"] == "queued", "Tico did not queue the PR after the second sign-off"
    assert len(co.task(product)["filed"]) == 1, "the second round made a second Engineering task"
    co.clean()
