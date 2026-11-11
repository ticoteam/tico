"""Shared by the merge queue tests (test_github_facts_merge_queue.py, test_merge_ok_check.py): an Engineering pipeline
whose Merge stage queues PRs, a task moved into it, and what its queue owes."""

from backend import task_engine as E
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X

REPO = "acme/app"
B, RM, EM = "bot:builder", "bot:release", "bot:eng-manager"


def engineering(c, release=None):
    """Build (PR open, Checks pass) -> Merge (queues PRs; PR merged) -> Release (In a release), managed by EM."""
    for slug in ("builder", "release", "eng-manager"):
        X.bot(c, slug)
    return X.pipeline(
        c,
        "eng",
        "Engineering",
        stages=(
            {"name": "Build", "owner": B, "requirements": [X.github("pr_open"), X.github("checks_pass")]},
            {
                "name": "Merge",
                "owner": RM,
                "on_enter": {"act": "github.queue"},
                "requirements": [X.github("pr_merged")],
            },
            {"name": "Release", "owner": RM, "requirements": [X.github("in_release", settings=release or {})]},
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
    assert F.pull(c, merge, X.ANA) == [] and effects(c, task_id) == owed


def later(minutes=10):
    return H.shift(H.now(), minutes=minutes)


def another_in_merge(c, gh, number):
    task_id = new(c, number)
    gh.set_checks(REPO, gh.open_pr(REPO, number), "success")
    seen(c, gh, number)
    assert stage(c, task_id) == "Merge"
    return task_id
