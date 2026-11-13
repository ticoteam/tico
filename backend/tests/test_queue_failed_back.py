"""A task sent back from Merge at the head the merge queue just ran waits for the Builder (task #344).

Tico Team, Nov 12-13: the merge queue failed a PR, the Release Owner moved the task back to the stage before Merge, and
Tico moved it straight back to Merge (0.4 to 8 s later) and queued the same head again, four times in two days. The
person's OK from before Merge was carried back with the task (C22, `task_requirements.page_for`) and only lapses when
the head moves, so nothing was missing. Now the carried OK does not count while a linked PR is still at the head it was
last queued at; new commits bring it back under C22's rules. Real Engineering preset (QA is the stage before Merge);
synthetic rows only (backend/tests/helpers/engine.py).
"""

import json

import pytest

from backend import answers as A
from backend import source_actions as SA
from backend import task_engine as E
from backend import task_requirements as R
from backend import waiting_on as W
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers import stage_inputs as T

PR, PR2 = f"{T.REPO}#12", f"{T.REPO}#13"
BUILDER, REVIEWER = "bot:builder", "bot:rev-b"
WHY = "Checks failed in the merge queue"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _page(c, eng, file_id, item_versions, by, verdict="met"):
    """The open stage's page, attached with the PR heads it was for, and Jev's verdict on it."""
    row = E.task(c, eng)
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL", (row["stage_id"],)
    ).fetchone()[0]
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "rubric_hash, created_at) VALUES (?,?,?,?,1,?,?,'',?)",
        (H.new_id(), row["visit"]["id"], rid, file_id, json.dumps(item_versions), by, H.now()),
    )
    c.execute(
        "INSERT INTO page_verdicts (id, file_id, version, rubric_hash, verdict, reasons, created_at, updated_at) "
        "VALUES (?,?,1,'',?,'',?,?)",
        (H.new_id(), file_id, verdict, H.now(), H.now()),
    )
    return rid


def _checks(c, number, head, state="success"):
    from backend import item_metadata as IM

    key = f"{T.REPO}#{number}"
    old = IM.get(c, "github", "pr", key)["fields"]
    IM.save(c, "github", "pr", key, {**old, "head_sha": head, "checks_state": state}, F.version_now())


def _queued(c, eng):
    """GitHub accepts every queue the Merge visit owes, as the effects run records it (a `pr_queued` row each)."""
    owed = c.execute(
        "SELECT e.*, v.task_id FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=? "
        "AND e.kind='act' "
        "AND e.state='owed'",
        (eng,),
    ).fetchall()
    for effect in owed:
        SA._record(c, F, dict(effect), {"ok": True, "how": "queued"}, F.version_now())
    return len(owed)


def _dropped(c, number, why=WHY, check="e2e"):
    """GitHub takes the PR out of the queue, as the webhook or re-read saves it (one `pr_queue_removed` row)."""
    F.save_and_settle(
        c,
        T.REPO,
        number,
        {"queue_state": "removed", "removed_reason": why, "failing_check": check, "removed_at": H.now()},
        F.version_now(),
    )
    _checks(c, number, F.pr(c, T.REPO, number)["head_sha"])  # the PR's own checks stay green at that head


def _queue_acts(c, eng):
    return [
        (r[0], r[1])
        for r in c.execute(
            "SELECT e.pinned, e.state FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=? "
            "AND e.kind='act' ORDER BY e.created_at, e.rowid",
            (eng,),
        )
    ]


def _qa_page(c, eng):
    (standing,) = [s for s in R.standings(c, E.task(c, eng)) if s.requirement["kind"] == "page"]
    return standing


def _ana_asked(c, eng):
    return [n for n in W.needs(c, X.ANA) if n["task_id"] == eng]


def _merge_failed(c, numbers=(12,)):
    """#266's sequence up to the hand move: both reviews read aaa (bbb for a second PR), QA's page OK'd by Ana, Merge
    queues the PRs at those heads and GitHub takes the first one out for a failed check. Returns (task, QA page id)."""
    heads = {12: "aaa", 13: "bbb"}
    _product, eng = T._filed_engineering(c)
    T._move(c, eng, "engineering-build")
    for number in numbers:
        T._link_pr(c, eng, number, heads[number])
    T._move(c, eng, "engineering-review-2")
    _page(c, eng, "f-rev2", {f"{T.REPO}#{n}": heads[n] for n in numbers}, REVIEWER)
    T._move(c, eng, "engineering-qa")
    for number in numbers:
        _checks(c, number, heads[number])
    rid = _page(c, eng, "f-qa", {}, BUILDER)
    A.answer(c, eng, rid, X.ANA, "ok")
    E.advance(c, eng)
    assert E.task(c, eng)["stage"]["name"] == "Merge"
    assert _queued(c, eng) == len(numbers)
    _dropped(c, numbers[0])
    return eng, rid


def _back_to_qa(c, eng, note="The queue run failed in e2e: fix it."):
    """The Release Owner moves it back, then whatever nudges the task runs (as the keeper did 0.4 s later)."""
    row = E.task(c, eng)
    E.move(c, eng, "engineering-qa", row["owner_id"] or X.ANA, note, seen_version=row["version"])
    E.advance(c, eng)
    return E.task(c, eng)


def test_sent_back_from_merge_at_the_head_the_queue_failed_it_waits_for_the_builder(c):
    """#266, Nov 13: merge_queue_failed at 463f2e7 01:57:30, the Release Owner's move to Fixes 2 at 01:58:55.04, Tico's
    move back to Merge at 01:58:55.42 and its queue of the same head at 01:58:59. Now it stays, nothing is queued, the
    QA page waits on the Builder and says why, and the Builder is woken once with the move note."""
    eng, rid = _merge_failed(c)
    acts = _queue_acts(c, eng)
    assert acts == [("aaa", "done")]
    row = _back_to_qa(c, eng)
    assert row["stage"]["name"] == "QA", "it went back to Merge on the carried OK"
    assert [a for a in _queue_acts(c, eng) if a[1] in ("owed", "done")] == acts, "the failed head was queued again"
    assert X.events(c, eng).count("pr_queued") == 1
    standing = _qa_page(c, eng)
    assert (standing.state, standing.on) == (R.NOT_MET, BUILDER)
    assert standing.line == (
        f"Merge queue ran aaa and it failed ({WHY}, e2e): push a fix. Sent back from Merge: The queue run failed in "
        "e2e: fix it. The OK from before Merge comes back once there are new commits."
    )
    assert W.turn(c, row) == BUILDER
    assert not _ana_asked(c, eng), "the person is not asked to OK the head that failed"
    visit = row["visit"]["id"]
    woken = [(k, to) for k, to in X.wakes(c, eng) if k.startswith(f"{eng}:{visit}:")]
    assert [to for _, to in woken] == [BUILDER], woken
    body = c.execute("SELECT body FROM messages WHERE wake_key=?", (woken[0][0],)).fetchone()[0]
    assert "The queue run failed in e2e: fix it." in body
    before = X.wakes(c, eng)
    E.advance(c, eng)  # read again: nothing more
    assert E.task(c, eng)["stage"]["name"] == "QA" and X.wakes(c, eng) == before


def test_a_fix_that_changes_the_same_lines_goes_to_merge_without_asking_the_person(c):
    eng, _rid = _merge_failed(c)
    _back_to_qa(c, eng)
    T._push(c, 12, "ccc")
    for sha in ("aaa", "ccc"):
        F.save_diff(c, T.REPO, sha, "same-lines")
    _checks(c, 12, "ccc")
    E.advance(c, eng)
    assert E.task(c, eng)["stage"]["name"] == "Merge"
    assert not _ana_asked(c, eng)
    assert [a for a in _queue_acts(c, eng) if a[1] == "owed"] == [("ccc", "owed")], "the fix is queued"


def test_a_fix_that_changes_other_lines_is_read_by_the_reviewer_whose_ok_takes_it_to_merge(c):
    eng, rid = _merge_failed(c)
    _back_to_qa(c, eng)
    T._push(c, 12, "ccc")
    F.save_diff(c, T.REPO, "aaa", "d1")
    F.save_diff(c, T.REPO, "ccc", "d2")
    _checks(c, 12, "ccc")
    standing = _qa_page(c, eng)
    assert (standing.state, standing.on) == (R.WAITING, REVIEWER)
    assert not _ana_asked(c, eng)
    seen = E.task(c, eng)["version"]
    A.answer(c, eng, rid, REVIEWER, "ok", "Read ccccccc: the e2e fix only.", seen_version=seen)
    E.advance(c, eng)
    assert E.task(c, eng)["stage"]["name"] == "Merge"
    assert not _ana_asked(c, eng)


def test_a_new_page_at_the_same_head_that_the_person_oks_lets_it_go(c):
    """A flaky queue run: the Builder attaches a page saying so at the same head, and the person OKs it on purpose."""
    eng, _rid = _merge_failed(c)
    _back_to_qa(c, eng)
    rid = _page(c, eng, "f-qa-2", {}, BUILDER)
    assert E.task(c, eng)["stage"]["name"] == "QA"
    assert [n["requirement_id"] for n in _ana_asked(c, eng)] == [rid]
    A.answer(c, eng, rid, X.ANA, "ok")
    E.advance(c, eng)
    assert E.task(c, eng)["stage"]["name"] == "Merge"


def test_with_two_prs_it_waits_while_either_is_at_the_head_it_was_queued_at(c):
    eng, _rid = _merge_failed(c, (12, 13))
    _back_to_qa(c, eng)
    T._push(c, 13, "ddd")  # the other PR moves; the failed one did not
    for sha in ("bbb", "ddd"):
        F.save_diff(c, T.REPO, sha, "same-lines")
    _checks(c, 13, "ddd")
    E.advance(c, eng)
    assert E.task(c, eng)["stage"]["name"] == "QA"
    standing = _qa_page(c, eng)
    assert (standing.state, standing.on) == (R.NOT_MET, BUILDER)
    assert standing.line.startswith(f"Merge queue ran {PR} at aaa and it failed ({WHY}, e2e): push a fix.")


def test_sent_back_with_new_commits_since_the_queue_keeps_c22(c):
    """A commit pushed in Merge after the queue ran (a conflict fix): the carried OK is decided by C22 as before."""
    eng, _rid = _merge_failed(c)
    T._push(c, 12, "ccc")
    for sha in ("aaa", "ccc"):
        F.save_diff(c, T.REPO, sha, "same-lines")
    _checks(c, 12, "ccc")
    row = _back_to_qa(c, eng, "Rebased in Merge.")
    assert row["stage"]["name"] == "Merge", "same lines since the OK: C22 keeps it"
