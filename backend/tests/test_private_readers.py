"""Whoever files a private task into a pipeline is told who else reads it as it moves (Product #449, spec:
private-readers-notice). The stage bots a pipeline names read a private task while they hold its stage, and so does
everyone above them on the org chart; a stage that waits on an open PR puts the work in GitHub. Nothing here changes
who reads, and nothing is refused: the filer is told, and the task's history keeps what they were told."""

import pytest

from backend import private_readers as PR
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import headers

RULES = "bot:rules"
B1, B2, R1, R2, RELEASE, MANAGER = (
    "bot:builder",
    "bot:builder-2",
    "bot:reviewer-1",
    "bot:reviewer-2",
    "bot:release",
    "bot:manager",
)
DONE = [{"kind": "owner_done"}]
PR_OPEN = [{"kind": "item", "source": "github", "condition": "pr_open"}]


def _bots(c):
    for slug in ("builder", "builder-2", "reviewer-1", "reviewer-2", "release", "manager", "rules"):
        X.bot(c, slug)
    for slug in ("builder", "builder-2", "reviewer-1", "reviewer-2", "release"):
        c.execute("UPDATE bot_config SET reports_to='manager' WHERE bot=?", (slug,))


def _engineering(c, repo=""):
    """Engineering's shape: a pick of two builders, Build waiting on an open PR, two reviews by a pick of the same two
    reviewers, and Merge by a fixed bot."""
    builders, reviewers = {"bots": [B1, B2], "how": "turns"}, {"bots": [R1, R2], "how": "turns"}
    pr = [dict(PR_OPEN[0], settings={"release_repo": repo})] if repo else PR_OPEN
    return X.pipeline(
        c,
        "eng",
        "Engineering",
        (
            {"name": "Build", "owner": "pick", "pick": builders, "requirements": pr},
            {"name": "Review 1", "owner": "pick", "pick": reviewers, "requirements": DONE},
            {"name": "Review 2", "owner": "pick", "pick": reviewers, "requirements": DONE},
            {"name": "Merge", "owner": RELEASE, "requirements": DONE},
        ),
        manager=MANAGER,
    )


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        _bots(conn)
        yield conn


def _file(c, private=True, pipeline="eng", by=RULES, readers=(), title="Fix the payroll export rounding"):
    return H.task_create(
        c,
        by,
        title,
        "The export rounds pay to the dollar.",
        B1,
        lint=False,
        pipeline=pipeline,
        private=private,
        readers=list(readers),
    )


def _told(c, task_id):
    rows = c.execute(
        "SELECT actor_id, new FROM history_events WHERE subject_id=? AND kind=? ORDER BY rowid", (task_id, PR.KIND)
    )
    return [tuple(r) for r in rows]


def test_a_private_engineering_task_is_filed_and_its_filer_told_every_stage_reader_their_managers_and_github(c):
    """Criteria 4, 5 and 7: filed as ever, the reply names each stage's bots once ("Reviewer 1 or Reviewer 2" for
    both reviews), the managers above them, and GitHub; the history keeps the same line."""
    _engineering(c)
    made = _file(c)
    line = made["private_readers"]
    assert H.task(c, made["id"])["stage_id"] == "eng-build"
    assert line.startswith(
        "Private, but these can read it as it moves through Engineering: builder or builder-2, "
        "reviewer-1 or reviewer-2, release, and their managers, manager and"
    )
    assert line.endswith(
        "Its code and pull request go to GitHub, where anyone with access to the repository can read them."
    )
    assert line.count("reviewer-1") == 1
    assert _told(c, made["id"]) == [(RULES, line)]


def test_the_github_line_names_the_repository_a_pipeline_sets(c):
    _engineering(c, repo="acme/payroll")
    assert "GitHub (acme/payroll)" in _file(c)["private_readers"]


def test_a_task_that_is_not_private_tells_nobody_anything(c):
    """Criterion 3: no line, no history."""
    _engineering(c)
    made = _file(c, private=False)
    assert "private_readers" not in made and _told(c, made["id"]) == []


def test_a_private_task_in_a_pipeline_that_names_no_bot_and_opens_no_pr_says_nothing(c):
    X.pipeline(c, "plain", "Plain", ({"name": "Do", "requirements": DONE},))
    made = _file(c, pipeline="plain")
    assert "private_readers" not in made and _told(c, made["id"]) == []


def test_whoever_already_reads_it_is_not_listed_again(c):
    """A reader the filer named reads it anyway: the line names only those who read it by the stage rule."""
    _engineering(c)
    found = PR.readers(c, "eng", [RULES, R1, MANAGER])
    assert found is not None
    assert found["stages"] == [["builder", "builder-2"], ["reviewer-2"], ["release"]]
    assert "manager" not in found["managers"]


def test_a_pipeline_that_is_not_there_or_retired_says_nothing(c):
    _engineering(c)
    assert PR.readers(c, "nope") is None
    c.execute("UPDATE pipelines SET retired_at=? WHERE id='eng'", (H.now(),))
    assert PR.readers(c, "eng") is None


def test_only_github_says_so_without_naming_any_bot(c):
    X.pipeline(c, "code", "Code", ({"name": "Build", "requirements": PR_OPEN},))
    found = PR.readers(c, "code")
    assert found is not None and found["stages"] == []
    assert found["line"].startswith("Private, but its code and pull request go to GitHub")


def test_will_be_private_follows_the_create(c):
    """The preview decides as the create does: asked, a private parent, or a bot's default; a bot cannot ask for
    company-visible."""
    _engineering(c)
    parent = H.task(c, _file(c)["id"])
    assert PR.will_be_private(c, "human:ana", B1, None, parent, "eng")
    assert PR.will_be_private(c, "human:ana", B1, True, None, "eng")
    assert not PR.will_be_private(c, "human:ana", B1, None, None, "eng")
    assert not PR.will_be_private(c, RULES, B1, False, None, "eng")


def test_the_app_and_the_dry_run_say_it_before_anything_is_filed(api):
    """Criteria 1, 2, 5 and 6 through the API: the new-task window's read for a sub-task of a private task, the same
    read for a task that will not be private, a team task's Visible to, and the dry run, which files nothing."""
    with api.app.state.store.transaction() as c:
        _bots(c)
        _engineering(c)
        X.pipeline(c, "plain", "Plain", ({"name": "Do", "requirements": DONE},))
        parent = _file(c, by="human:ana")["id"]
        team = _file(c, private=False, by="human:ana", title="Fix the payroll export totals")["id"]
        before = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    ana = headers("ana-test")
    url = "/api/v2/pipelines/eng/private-readers"
    got = api.get(url, params={"owner": B1, "parent": parent}, headers=ana).json()
    assert got["private"] and "Reviewer" not in got["line"] and "reviewer-1 or reviewer-2" in got["line"]
    assert api.get(url, params={"owner": B1}, headers=ana).json() == {"private": False, "line": None}
    plain = api.get("/api/v2/pipelines/plain/private-readers", params={"owner": B1, "parent": parent}, headers=ana)
    assert plain.json() == {"private": True, "line": None}
    assert "reviewer-1" in api.get(url, params={"task": team}, headers=ana).json()["line"]
    assert api.get("/api/v2/pipelines/nope/private-readers", headers=ana).status_code == 404
    dry = api.post(
        "/api/v2/tasks/dry-run",
        headers=ana,
        json={"title": "Fix it", "body": "Rounds pay.", "owner": B1, "pipeline": "eng", "private": True},
    ).json()
    assert "reviewer-1 or reviewer-2" in dry["private_readers"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before
