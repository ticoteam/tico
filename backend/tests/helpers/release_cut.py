"""A task in an Engineering pipeline whose PR merged and that now waits in Release (In a release), for the tests of
releases: backend/tests/test_release_cut.py and backend/tests/test_release_lookups.py. GitHub is
backend/tests/helpers/fake_github.py."""

from backend import task_engine as E
from backend import task_requirements as R
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X

REPO = "acme/app"
B, RM, EM, ANA = "bot:builder", "bot:release", "bot:eng-manager", X.ANA
WORKFLOW = {"release_with": "workflow:release.yml"}


def engineering(c, release=WORKFLOW, sign_off=True):
    """Build -> Sign off (a person's OK, the pre-release sign-off) -> Merge (queues PRs) -> Release (In a release)."""
    for slug in ("builder", "release", "eng-manager"):
        X.bot(c, slug)
    stages = [
        {"name": "Build", "owner": B, "requirements": [X.github("pr_open"), X.github("checks_pass")]},
        {
            "name": "Sign off",
            "owner": B,
            "requirements": [{"kind": "ok", "person": ANA, "who_oks": "people_only", "can_push_past": 0}],
        },
        {"name": "Merge", "owner": RM, "on_enter": {"act": "github.queue"}, "requirements": [X.github("pr_merged")]},
        {"name": "Release", "owner": RM, "requirements": [X.github("in_release", settings=release)]},
    ]
    if not sign_off:
        stages.pop(1)
    return X.pipeline(c, "eng", "Engineering", stages=tuple(stages), manager=EM)


def new(c, number):
    task = X.new_task(c, ANA, f"Ship {number}", "eng")
    H.task_link(c, ANA, task["id"], f"https://github.com/{REPO}/pull/{number}")
    return task["id"]


def seen(c, gh, number):
    return F.save_and_settle(c, REPO, number, F.read_pr(gh, REPO, number), F.version_now())


def stage(c, task_id):
    return E.task(c, task_id)["stage"]["name"]


def line(c, task_id):
    row = E.task(c, task_id)
    found = next(s for s in R.standings(c, row) if s.requirement["condition"] == "in_release")
    return (found.state, found.on, found.line)


def version(c, task_id):
    return E.task(c, task_id)["version"]


def to_release(company, gh, number=1, sign_off=True, **settings):
    """A task whose PR merged, now in Release; the repo publishes releases and none holds the merge yet."""
    with company.transaction() as c:
        if not c.execute("SELECT 1 FROM pipelines WHERE id='eng'").fetchone():
            engineering(c, settings.get("release", WORKFLOW), sign_off)
            for release in gh.published[REPO]:  # Tico has read the repo's releases before
                F.save_release(c, REPO, release, F.version_now())
            F.saw_releases(c, REPO, F.version_now())
        task_id = new(c, number)
        gh.set_checks(REPO, gh.open_pr(REPO, number), "success")
        seen(c, gh, number)
        if sign_off:
            assert stage(c, task_id) == "Sign off"
            row = E.task(c, task_id)
            rid = c.execute("SELECT id FROM requirements WHERE stage_id='eng-sign-off'").fetchone()[0]
            X.answer(c, row, rid, "ok", ANA)
            E.advance(c, task_id)
        assert stage(c, task_id) == "Merge"
    F.run(company, gh)
    gh.merge(REPO, number)
    F.refresh(company, gh, H.shift(H.now(), minutes=10))
    with company.transaction() as c:
        assert stage(c, task_id) == "Release"
    return task_id
