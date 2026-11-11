"""A merge conflict before Merge is the Builder's (backend/pr_conflicts.py, docs/pipelines-v3.md "GitHub").

Acme rc.3 (Nov 11, 08:48): the Engineering task's PR went conflicting while Reviewer 2 held Review 2 (its page waiting
on a person then; on a bot now, #3283). The conflict went to Reviewer 2 as a quiet note (it cannot push) and nothing
moved for 40 minutes. Now the bot that built the PR is woken at once, once per PR and conflicting commit; the task stays
in its stage, and its turn and its "to leave" say it is the Builder's. Synthetic rows only
(backend/tests/helpers/engine.py).
"""

import json

import pytest

from backend import github as G
from backend import pr_conflicts as PC
from backend import task_engine as E
from backend import task_requirements as R
from backend import waiting_on as W
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers import stage_inputs as T

URL = f"https://github.com/{T.REPO}/pull/12"
PR = f"{T.REPO}#12"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _github(c, head, mergeable, action="synchronize"):
    """GitHub's pull_request webhook for PR 12, as backend/github.py takes it (False: it conflicts)."""
    pr = {"html_url": URL, "number": 12, "state": "open", "head": {"sha": head, "ref": "saved-carts"}}
    if mergeable is not None:
        pr["mergeable"] = mergeable
        pr["mergeable_state"] = "clean" if mergeable else "dirty"
    G.pull_request(c, {"action": action, "pull_request": pr}, read_refresh=True)


def _review_page_jev_unsure(c, eng):
    """Reviewer 2's page at the PR's newest commit; Jev is unsure, so it is back to Reviewer 2 to fix or explain."""
    row = E.task(c, eng)
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL", (row["stage_id"],)
    ).fetchone()[0]
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,1,?,?,?)",
        (H.new_id(), row["visit"]["id"], rid, "f-rev2", json.dumps(F.heads(c, eng)), "bot:rev-b", H.now()),
    )
    c.execute(
        "INSERT INTO page_verdicts (id, file_id, version, rubric_hash, verdict, reasons, created_at, updated_at) "
        "VALUES (?,?,1,'','unsure','The empty cart case is not covered',?,?)",
        (H.new_id(), "f-rev2", H.now(), H.now()),
    )
    return rid


def _in_review_2(c):
    """An Engineering task in Review 2 with PR 12 open at commit aaa, Reviewer 2's page back with it (Jev unsure)."""
    _product, eng = T._filed_engineering(c)
    T._move(c, eng, "engineering-build")
    T._link_pr(c, eng, 12, "aaa")
    _github(c, "aaa", None, action="opened")
    T._move(c, eng, "engineering-review-2")
    _review_page_jev_unsure(c, eng)
    return eng


def _conflict_wakes(c, task_id):
    return [(k, to) for k, to in X.wakes(c, task_id) if ":conflict:" in k]


def test_a_conflict_while_a_reviewer_holds_the_stage_wakes_the_builder_once_per_commit(c):
    eng = _in_review_2(c)
    row = E.task(c, eng)
    assert [(w.state, w.who) for w in W.of(c, row)] == [(W.BOT, "bot:rev-b")]  # Jev is unsure: the reviewer's
    visit = row["visit"]["id"]

    _github(c, "aaa", False)
    row = E.task(c, eng)
    assert row["stage"]["name"] == "Review 2"  # the task stays where it is
    key = f"{eng}:{visit}:conflict:{PR}@aaa"
    assert _conflict_wakes(c, eng) == [(key, "bot:builder"), (key + ":holder", "bot:rev-b")]
    wake = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
    assert wake.startswith(f"{PR} conflicts with its base branch. You built it: rebase its branch on the default")
    assert "push, and say so on this task. The task stays in Review 2" in wake
    note = c.execute("SELECT body, refs FROM messages WHERE wake_key=?", (key + ":holder",)).fetchone()
    assert f"{PR} conflicts with its base branch. builder is rebasing it: it is not yours to fix." in note[0]
    assert "once the new one is pushed, review that one" in note[0]
    assert json.loads(note[1])["quiet"] is True
    # Not the stage owner's grouped GitHub notice, as before.
    assert not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", ("github-task-wake:" + eng,)).fetchone()

    # What it waits on: the Builder first (the ladder's turn), the reviewer's page still there.
    waits = W.of(c, row)
    assert [(w.state, w.who) for w in waits] == [(W.BOT, "bot:builder"), (W.BOT, "bot:rev-b")]
    assert waits[1].line.startswith(R.FIX_OR_EXPLAIN)
    assert W.turn(c, row) == "bot:builder"
    assert "bot:builder" in W.turns(c, row)  # its run is not held (task_wakes.may_start)
    line = next(w.line for w in waits if w.who == "bot:builder")
    assert line.startswith(f"{PR} conflicts with its base branch: ") and "rebases it on the default branch" in line
    # To leave: not met, the Builder's.
    view = H.task_view(c, eng, X.ANA)
    conflict = next(t for t in view["to_leave"] if t.get("requirement_id") == f"conflict:{eng}")
    assert conflict["met"] is False
    assert not E.advance(c, eng)
    assert [s.requirement["kind"] for s in R.unmet(c, row)][0] == PC.KIND

    # The same commit again (the hourly read of PRs whose base moved): no second wake.
    _github(c, "aaa", False, action="refresh")
    assert len(_conflict_wakes(c, eng)) == 2

    # The Builder pushes the rebase: GitHub does not know yet whether it conflicts, and the conflict is gone. The
    # review is of the old commit: it is the reviewer's to do again, at the new one.
    T._push(c, 12, "bbb")
    _github(c, "bbb", None)
    row = E.task(c, eng)
    assert PC.standing(c, row) is None
    page = next(s for s in R.unmet(c, row) if s.requirement["kind"] == "page")
    assert (page.line, page.on) == ("New commits since the review", "bot:rev-b")
    assert [(w.state, w.who) for w in W.of(c, row)] == [(W.BOT, "bot:rev-b")]

    # A conflict on the new commit is a new wake.
    _github(c, "bbb", False)
    assert (f"{eng}:{visit}:conflict:{PR}@bbb", "bot:builder") in _conflict_wakes(c, eng)


def test_a_conflict_in_merge_is_left_to_the_merge_queue(c):
    eng = _in_review_2(c)
    T._move(c, eng, "engineering-merge")
    _github(c, "aaa", False)
    assert not _conflict_wakes(c, eng)
    assert PC.standing(c, E.task(c, eng)) is None


def test_a_pipeline_with_no_merge_stage_keeps_the_owners_notice(c):
    product = T._product_task(c)
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, created_at) VALUES (?,?,'pr',?,?,12,?)",
        (H.new_id(), product["id"], URL, T.REPO, H.now()),
    )
    _github(c, "aaa", None, action="opened")
    _github(c, "aaa", False)
    assert not _conflict_wakes(c, product["id"])
    assert PC.standing(c, E.task(c, product["id"])) is None
    queued = c.execute("SELECT value FROM registry_metadata WHERE key=?", ("github-task-wake:" + product["id"],))
    assert "Merge conflict" in queued.fetchone()[0]


def test_with_no_bot_that_built_it_the_conflict_is_the_owners(c):
    eng = _in_review_2(c)
    c.execute("UPDATE stages SET owner=? WHERE id='engineering-build'", (X.ANA,))  # a person builds
    _github(c, "aaa", False)
    row = E.task(c, eng)
    standing = PC.standing(c, row)
    assert standing and standing.on == "bot:rev-b"  # the task's owner in Review 2
    assert [to for _, to in _conflict_wakes(c, eng)] == ["bot:rev-b"]
