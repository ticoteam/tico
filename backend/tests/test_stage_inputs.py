"""What each stage of the Product and Engineering presets is handed (backend/stage_inputs.py, docs/pipelines-v3.md
"What a stage is handed"): the wake that wakes a stage's bot, and the task's own read, list the Spec page, the PR, each
earlier Review page and what was said at done; a page an earlier stage never attached is named as missing.

Acme rc.3 (case 1): in Fixes 1 the Builder could not see Review 1's page, said done with nothing changed, and the
engine took it. Synthetic rows only (backend/tests/helpers/engine.py).
"""

import pytest

from backend import stage_inputs as SI
from backend import task_engine as E
from backend import task_wakes as TW
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.stage_inputs import (
    ANA,
    REPO,
    _attach,
    _filed_engineering,
    _link_pr,
    _move,
    _product_task,
    _push,
)
from clients import hubtools


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _done(c, task_id, notes):
    row = E.task(c, task_id)
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='owner_done' AND withdrawn_at IS NULL",
        (row["stage_id"],),
    ).fetchone()[0]
    X.answer(c, row, rid, "done", row["owner_id"], notes=notes)


def _wake(c, task_id, to):
    """The newest wake to `to` for the task's open visit: what its bot reads."""
    row = E.task(c, task_id)
    found = c.execute(
        "SELECT body FROM messages WHERE wake_key LIKE ? AND recipient_id=? ORDER BY created_at DESC, rowid DESC",
        (f"{task_id}:{row['visit']['id']}:%", to),
    ).fetchone()
    assert found, (row["stage"]["name"], X.wakes(c, task_id))
    return found[0]


def _handed(c, task_id):
    return [e["line"] for e in SI.of(c, E.task(c, task_id))]


SPEC = "Spec page from Spec (Product): page:f-spec@3, read it with `tico file read f-spec`"
PR_OPEN = f"PR {REPO}#12 https://github.com/{REPO}/pull/12: open, newest commit "
REVIEW_1 = "Review page from Review 1: page:f-rev1@1, read it with `tico file read f-rev1`"
REVIEW_2 = "Review page from Review 2: page:f-rev2@1, read it with `tico file read f-rev2`"
FIXES_1 = "Fixes 1 was said done: bbb: fixed the empty cart; declined the rename (out of scope)"
BUILT = "QA page from QA: page:f-built@2, read it with `tico file read f-built`"


def test_each_engineering_and_product_stage_is_handed_what_came_before(c):
    product, eng = _filed_engineering(c)
    # Build: the Spec, as the Product task OK'd it.
    _move(c, eng, "engineering-build")
    assert _handed(c, eng) == [SPEC]
    assert "Spec page from Spec (Product): page:f-spec@3" in _wake(c, eng, "bot:builder")
    _link_pr(c, eng, 12, "aaa")

    # Review 1: the PR (the number its playbook's `gh pr view <n> -R <repo>` needs) and the Spec.
    _move(c, eng, "engineering-review-1")
    wake = _wake(c, eng, "bot:rev-a")
    assert "From earlier stages (read each before you act):" in wake
    assert f"- {SPEC} (" in wake and f"- {PR_OPEN}aaa" in wake
    assert SI.IF_MISSING in wake
    _attach(c, eng, "f-rev1", "bot:rev-a")

    # Fixes 1: Review 1's page, said by whom; the Acme case.
    _move(c, eng, "engineering-fixes-1")
    wake = _wake(c, eng, "bot:builder")
    assert f"- {REVIEW_1} (" in wake, wake
    assert _handed(c, eng) == [SPEC, PR_OPEN + "aaa", REVIEW_1]
    _push(c, 12, "bbb")
    _done(c, eng, "bbb: fixed the empty cart;\n declined the rename (out of scope)")

    # Review 2: Review 1's page and what the Builder said it fixed, at the new commit.
    _move(c, eng, "engineering-review-2")
    assert _handed(c, eng) == [SPEC, PR_OPEN + "bbb", REVIEW_1, FIXES_1]
    assert FIXES_1 in _wake(c, eng, "bot:rev-b")
    _attach(c, eng, "f-rev2", "bot:rev-b")

    # Fixes 2: both reviews, in the order they were written, and the Fixes 1 note between them.
    _move(c, eng, "engineering-fixes-2")
    assert _handed(c, eng) == [SPEC, PR_OPEN + "bbb", REVIEW_1, FIXES_1, REVIEW_2]
    wake = _wake(c, eng, "bot:builder")
    assert REVIEW_1 in wake and REVIEW_2 in wake
    _done(c, eng, "bbb: nothing to fix")

    # QA: the same, with what was said at Fixes 2's done, for the QA page.
    _move(c, eng, "engineering-qa")
    assert _handed(c, eng)[-2:] == [REVIEW_2, "Fixes 2 was said done: bbb: nothing to fix"]
    _attach(c, eng, "f-built", "bot:builder", version=1)
    _attach(c, eng, "f-built", "bot:builder", version=2)  # sent back once: only the newest counts

    # Merge: the PR and the page the person OK'd. The Release Owner is woken only when GitHub takes the PR out of the
    # queue; that wake is the same body.
    _move(c, eng, "engineering-merge")
    assert E.task(c, eng)["owner_id"] == "bot:rm"
    assert _handed(c, eng)[-1] == BUILT
    wake = TW.body(c, E.task(c, eng), to="bot:rm")
    assert f"- {PR_OPEN}bbb" in wake and BUILT in wake

    # Release: the merge commit.
    _push(c, 12, "bbb", state="merged", merged_sha="ccc111")
    _move(c, eng, "engineering-release")
    merged = f"PR {REPO}#12 https://github.com/{REPO}/pull/12: merged as ccc111"
    assert merged in TW.body(c, E.task(c, eng), to="bot:rm")

    # Validate (Product): its own Spec page and the filed work's PR, with the release it went out in.
    _move(c, product, "product-validate")
    filed_pr = f"PR {REPO}#12 https://github.com/{REPO}/pull/12: merged as ccc111 (work this task filed)"
    assert _handed(c, product) == [
        "Spec page from Spec: page:f-spec@3, read it with `tico file read f-spec`",
        filed_pr + "; in no release yet",
    ]
    assert filed_pr + "; in no release yet" in _wake(c, product, "bot:pm")
    at = "2026-11-05T00:00:00Z"
    c.execute(
        "INSERT INTO github_releases (id, repo, tag, commit_sha, published_at, metadata_version, read_at, created_at) "
        "VALUES ('r1',?,'v1.2.0','x',?,1,?,?)",
        (REPO, at, at, at),
    )
    c.execute(
        "INSERT INTO github_release_commits (release_id, commit_sha, included, checked_at) VALUES ('r1','ccc111',1,?)",
        (at,),
    )
    assert H.task_view(c, product, ANA)["inputs"] == []  # a list of many tasks (Needs you) leaves it out
    view = H.task_view(c, product, ANA, inputs=True)
    assert view["inputs"][-1]["line"] == filed_pr + "; in release v1.2.0"
    assert view["inputs"][-1]["releases"] == ["v1.2.0"]


def test_a_page_an_earlier_stage_never_attached_is_named_missing_and_the_bot_is_told_to_send_it_back(c):
    _product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    _link_pr(c, eng, 12, "aaa")
    _move(c, eng, "engineering-review-1")
    _move(c, eng, "engineering-fixes-1")  # pushed past Review 1 with no Review page
    missing = "Review page from Review 1: none was attached"
    assert _handed(c, eng)[-1] == missing
    wake = _wake(c, eng, "bot:builder")
    assert f"- {missing}" in wake and SI.IF_MISSING in wake
    assert 'tico task move <id> "<stage>"' in SI.IF_MISSING
    # Once a later visit to Review 1 attaches one, nothing is missing.
    E.move(c, eng, "engineering-review-1", ANA, "No review page")
    _attach(c, eng, "f-rev1", "bot:rev-a")
    _move(c, eng, "engineering-fixes-1")
    assert _handed(c, eng)[-1] == REVIEW_1


def test_the_open_visits_own_page_and_done_are_not_earlier_inputs(c):
    _product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    _move(c, eng, "engineering-review-1")
    _attach(c, eng, "f-rev1", "bot:rev-a")
    assert _handed(c, eng) == [SPEC]  # its own page is under "To leave", not handed from before
    _move(c, eng, "engineering-fixes-1")
    _done(c, eng, "nothing to fix")
    assert _handed(c, eng)[-1] == REVIEW_1


def test_a_private_product_task_hands_on_none_of_its_pages(c):
    product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    assert SPEC in _handed(c, eng)
    assert SI.of(c, E.task(c, eng), readable=lambda filer: False) == []
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (product,))
    _move(c, eng, "engineering-review-1")
    assert "Spec page from Spec (Product)" not in _wake(c, eng, "bot:rev-a")


def test_a_filed_task_the_reader_may_not_name_is_left_out(c):
    product, eng = _filed_engineering(c)
    _link_pr(c, eng, 12, "aaa")
    assert [e["line"] for e in SI.of(c, E.task(c, product)) if e["kind"] == "pr"] == [
        PR_OPEN + "aaa (work this task filed)"
    ]
    assert not [e for e in SI.of(c, E.task(c, product), shown=lambda task_id: False) if e["kind"] == "pr"]


def test_tico_task_show_prints_what_earlier_stages_handed_on():
    task = {
        "id": "t1",
        "title": "Saved carts",
        "version": 4,
        "stage": {"name": "Fixes 1"},
        "to_leave": [],
        "inputs": [
            {"kind": "pr", "line": PR_OPEN + "aaa"},
            {"kind": "page", "line": REVIEW_1, "by": "bot:rev-a"},
            {"removed": True, "label": "A page you can't open"},
        ],
    }
    text = hubtools.task_text(task, {"bot:rev-a": "Reviewer 1"})
    lines = text.splitlines()
    at = lines.index("From earlier stages:")
    assert lines[at + 1 : at + 4] == [
        f"  {PR_OPEN}aaa",
        f"  {REVIEW_1} (Reviewer 1)",
        "  A page you can't open",
    ]
    assert "From earlier stages" not in hubtools.task_text({**task, "inputs": []})


def _sign_off(c, task_id, answer, by, version, notes):
    """A person's OK or send back of the open stage's page at `version`, as answers.answer leaves it."""
    row = E.task(c, task_id)
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL", (row["stage_id"],)
    ).fetchone()[0]
    c.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, notes, page_version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (H.new_id(), row["visit"]["id"], rid, by, answer, notes, version, H.now()),
    )


SENT_BACK = (
    "QA page from QA (Saved carts, work this task filed): sent back at page:f-built@1: "
    "the empty cart still shows a total"
)
SIGNED = "QA page from QA (Saved carts, work this task filed): OK'd at page:f-built@2: ship it; watch the totals"


def test_the_product_task_is_handed_the_pre_release_sign_offs_of_the_work_it_filed(c):
    """Acme rc.3: a person's notes when they sent back and then OK'd the pre-release page never reached Product · Validate."""
    product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    _link_pr(c, eng, 12, "aaa")
    _move(c, eng, "engineering-qa")
    _attach(c, eng, "f-built", "bot:builder", version=1)
    _sign_off(c, eng, "no", ANA, 1, "the empty cart\n still shows a total")
    _attach(c, eng, "f-built", "bot:builder", version=2)
    _sign_off(c, eng, "ok", "bot:rev-b", 2, "a bot's OK is not a person's sign-off")
    _sign_off(c, eng, "ok", ANA, 2, "ship it; watch the totals")

    # In development, while the Product task waits on it: after the filed work's PR, oldest first, each by its person.
    handed = SI.of(c, E.task(c, product))
    signoffs = [e for e in handed if e["kind"] == "signoff"]
    assert [e["line"] for e in handed[-3:]] == [PR_OPEN + "aaa (work this task filed)", SENT_BACK, SIGNED]
    assert [(e["answer"], e["by"], e["version"], e["task_id"]) for e in signoffs] == [
        ("no", ANA, 1, eng),
        ("ok", ANA, 2, eng),
    ]
    assert signoffs[1]["notes"] == "ship it; watch the totals"
    assert signoffs[1]["ref"] == "page:f-built@2"

    # Validate: in the PM's wake, said by whom, and in the task's own read.
    _move(c, product, "product-validate")
    wake = _wake(c, product, "bot:pm")
    assert f"- {SENT_BACK} (" in wake and f"- {SIGNED} (" in wake
    view = H.task_view(c, product, ANA, inputs=True)
    assert [e["line"] for e in view["inputs"] if e["kind"] == "signoff"] == [SENT_BACK, SIGNED]

    # A reader who may not name the filed task (a private one, task_wakes.told) is handed none of it.
    assert not [e for e in SI.of(c, E.task(c, product), shown=lambda task_id: False) if e["kind"] == "signoff"]


def _link_other_pr(c, task_id, repo, number):
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,'pr',?,?,?,'github','pr',?,?)",
        (H.new_id(), task_id, f"https://github.com/{repo}/pull/{number}", repo, number, f"{repo}#{number}", H.now()),
    )


def test_a_task_in_one_repository_names_it_for_gh_and_its_wake_says_how_to_read_checks(c):
    """Acme rc.4 and rc.5: the Product Owner ran a bare `gh pr checks 46` where no git remote names the repository and
    wrote that it could not read CI. Its turn is handed the repository (GH_REPO) and its wake says to pass the link."""
    product, eng = _filed_engineering(c)
    everyone = lambda _task_id: True  # noqa: E731
    assert SI.repository(c, product, everyone) is None  # no PR yet: nothing to name
    _move(c, eng, "engineering-build")
    assert SI.READ_CHECKS not in _wake(c, eng, "bot:builder")
    _link_pr(c, eng, 12, "aaa")
    assert SI.repository(c, eng, everyone) == REPO
    # The Product task names the repository of the work it filed, but not from a filed task its bot may not read.
    assert SI.repository(c, product, everyone) == REPO
    assert SI.repository(c, product, lambda _task_id: False) is None
    _move(c, eng, "engineering-review-1")
    assert SI.READ_CHECKS in _wake(c, eng, "bot:rev-a")

    _push(c, 12, "aaa", state="merged", merged_sha="ccc111")
    _move(c, product, "product-validate")
    wake = _wake(c, product, "bot:pm")
    assert f"https://github.com/{REPO}/pull/12" in wake and SI.READ_CHECKS in wake

    # PRs in two repositories: no one repository to name; each PR's link is in the wake.
    _link_other_pr(c, eng, "acme/site", 3)
    assert SI.repository(c, product, everyone) is None
    _link_other_pr(c, eng, REPO.upper(), 13)  # the same repository, however it is spelled, is still one
    c.execute("DELETE FROM task_links WHERE repo='acme/site'")
    assert SI.repository(c, product, everyone) == REPO


def test_the_turn_is_handed_the_one_repository_its_task_is_in(api):
    from backend.tests.helpers.api import assign, claim, post, ready, runner

    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Ship the refund page", "body": "x"})
    with api.app.state.store.transaction() as c:
        _link_other_pr(c, task["id"], "acme/app", 46)
    assert claim(api, r, "ops")["github_repository"] == "acme/app"


def test_a_builder_handed_a_task_with_an_open_pr_is_told_to_push_to_it_not_open_a_second(c):
    """#171: a task reassigned to a second Builder mid-Build got a second PR beside the first. The new owner's wake
    names the task's open PR and says to push to its branch; a merged PR, or a PR of the work a task filed, says
    nothing of the kind."""
    product, eng = _filed_engineering(c)
    _move(c, eng, "engineering-build")
    assert "Never open a second PR" not in _wake(c, eng, "bot:builder")  # no PR yet
    _link_pr(c, eng, 12, "aaa")
    X.bot(c, "builder-2")
    E.reassign(c, eng, "bot:builder-2", ANA, "Builder 2 takes it", E.task(c, eng)["version"])
    wake = TW.body(c, E.task(c, eng), "This task is yours now.", to="bot:builder-2")  # what its wake says
    url = f"https://github.com/{REPO}/pull/12"
    assert SI.one_pr(SI.of(c, E.task(c, eng))) in wake
    assert f"`gh pr checkout {url}`" in wake and "Never open a second PR" in wake
    _move(c, product, "product-validate")
    assert "Never open a second PR" not in _wake(c, product, "bot:pm")  # the filed work's PR is not its own
    _push(c, 12, "aaa", state="merged", merged_sha="ccc111")
    assert SI.one_pr(SI.of(c, E.task(c, eng))) == ""
