"""A private task's stage bot reads it while it holds the stage (spec: private-task-stage-holder-reads, Engineering
#393). Engineering #326 stopped in Build: Review 1's bot was not on Who can see, so the move was refused and nobody
was asked by name. The bot the pipeline names for a stage now reads the task while it holds that stage; Who can see
does not change, and the read ends when the task moves on."""

import pytest

from backend import never_stuck
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA, BEN, CARA = "human:ana", "human:ben", "human:cara"  # Ana owns every bot here (X.bot): she reads it all
BUILDER, R1, R2, R3 = "bot:builder", "bot:reviewer-1", "bot:reviewer-2", "bot:reviewer-3"
MANAGER, RULES = "bot:manager", "bot:rules"
DONE = [{"kind": "owner_done"}]


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "reviewer-1", "reviewer-2", "reviewer-3", "manager", "rules"):
            X.bot(conn, slug)
        yield conn


def _engineering(c, review1=None):
    """Build, Review 1, Review 2, each held by a named bot (Review 1 by `review1`, a fixed bot by default)."""
    stages = (
        {"name": "Build", "owner": BUILDER, "requirements": DONE},
        review1 or {"name": "Review 1", "owner": R1, "requirements": DONE},
        {"name": "Review 2", "owner": R2, "requirements": DONE},
    )
    return X.pipeline(c, "eng", "Engineering", stages, manager=MANAGER)


def _private(c, pipeline="eng", by=RULES, readers=(BEN,)):
    """#326's shape: a bot files a private task for the Builder (its owner in Build, so never listed under Who can
    see) and shares it with one person."""
    row = H.task_create(
        c,
        by,
        "Delete call recordings after 30 days",
        "From the contract.",
        BUILDER,
        lint=False,
        pipeline=pipeline,
        private=True,
        readers=list(readers),
    )
    return E.task(c, row["id"])


def _done(c, row, by):
    return H.task_done(c, by, row["id"], "Done here.", H.task(c, row["id"])["version"])


def _reads(c, who, task_id):
    return H.task_private_readable(c, who, H.task(c, task_id))


def _lines(c, task_id, kind="stage_read"):
    rows = c.execute("SELECT note FROM history_events WHERE subject_id=? AND kind=? ORDER BY rowid", (task_id, kind))
    return [r[0] for r in rows]


def test_a_private_task_leaves_build_for_a_reviewer_who_is_not_on_who_can_see_and_the_reviewer_reads_it(c):
    """Criteria 1 and 2, #326 replayed: Build is met, Reviewer 1 is not a reader. The task moves to Review 1,
    Reviewer 1 opens it, the history says why, and Who can see is what it was."""
    _engineering(c)
    row = _private(c)
    assert not _reads(c, R1, row["id"])
    moved = _done(c, row, BUILDER)
    assert (moved["stage"]["name"], moved["owner_id"]) == ("Review 1", R1)
    assert _reads(c, R1, row["id"])
    assert H.task_readers(H.task(c, row["id"])) == [BEN]
    assert _lines(c, row["id"]) == [
        "builder can read this while it holds Build",
        "reviewer-1 can read this while it holds Review 1",
    ]


def test_the_stage_read_ends_when_the_task_moves_on(c):
    """Criterion 3: in Review 2, Reviewer 1 is neither requester, owner nor a listed reader, so it cannot open it."""
    _engineering(c)
    row = _private(c)
    _done(c, row, BUILDER)
    moved = _done(c, row, R1)
    assert moved["stage"]["name"] == "Review 2" and _reads(c, R2, row["id"])
    assert not _reads(c, R1, row["id"]) and not _reads(c, BUILDER, row["id"])
    assert _reads(c, BEN, row["id"]) and _reads(c, RULES, row["id"])  # a listed reader and the requester keep it
    assert _lines(c, row["id"])[-1] == "reviewer-2 can read this while it holds Review 2"


def test_a_stuck_private_task_moves_on_at_the_next_never_stuck_pass(c, monkeypatch):
    """Already stuck when it ships: a task held at a handover for a named bot moves on without anyone acting."""
    _engineering(c)
    row = _private(c)
    with monkeypatch.context() as before:  # the rule as it was: held in Build
        before.setattr(E, "shut_out", lambda *a, **k: {"who": R1, "cause": "held", "fix": "share it"})
        _done(c, row, BUILDER)
    assert E.task(c, row["id"])["stage"]["name"] == "Build"
    never_stuck.check(c)
    assert E.task(c, row["id"])["stage"]["name"] == "Review 1"


def test_a_pick_from_named_bots_takes_a_bot_that_is_not_a_reader_and_it_reads_the_task(c):
    """Criterion 4: "One of several" counts its named bots as able to read a private task."""
    pick = {"bots": [R1, R3], "how": "turns"}
    _engineering(c, {"name": "Review 1", "owner": "pick", "pick": pick, "requirements": DONE})
    row = _private(c)
    moved = _done(c, row, BUILDER)
    assert moved["stage"]["name"] == "Review 1" and moved["owner_id"] in (R1, R3)
    assert _reads(c, moved["owner_id"], row["id"])
    assert H.task_readers(H.task(c, row["id"])) == [BEN]
    assert _lines(c, row["id"])[-1] == f"{moved['owner_id'].split(':')[1]} can read this while it holds Review 1"


@pytest.mark.parametrize(
    "review1",
    [
        {"name": "Review 1", "owner": "assignee", "requirements": DONE},
        {"name": "Review 1", "owner": CARA, "requirements": DONE},
        {"name": "Review 1", "owner": R1, "requirements": [{"kind": "ok", "person": CARA, "line": "OK it"}]},
        {"name": "Review 1", "owner": H.GENERAL_MANAGER, "requirements": DONE},
    ],
    ids=["assignee", "person", "asks-a-person", "general-manager"],
)
def test_a_stage_not_held_by_a_named_bot_still_waits_for_someone_to_share_it(c, review1):
    """Criteria 5 and 10: an assignee's stage, a person's, a person asked for an OK, and the General Manager's keep
    today's check: the task waits and names who can share it."""
    X.bot(c, "general-manager")
    _engineering(c, review1)
    row = _private(c)
    if review1["owner"] == "assignee":
        c.execute("UPDATE tasks SET assignee_id=? WHERE id=?", (R3, row["id"]))
    _done(c, row, BUILDER)
    held = E.task(c, row["id"])
    assert held["stage"]["name"] == "Build"
    assert _lines(c, row["id"]) == ["builder can read this while it holds Build"]
    with pytest.raises(H.Refused, match="task_not_shared"):
        H.task_move(c, BUILDER, row["id"], "Review 1", None, H.task(c, row["id"])["version"])


def test_a_bot_that_reads_only_while_it_holds_the_stage_cannot_share_the_task(c):
    """Criterion 6: reading is not sharing. The reviewer cannot add a reader, publish the task or give it away."""
    _engineering(c)
    row = _private(c)
    _done(c, row, BUILDER)
    reason = "You can read this while you hold Review 1; only someone it is shared with can share it"
    with pytest.raises(H.Refused, match=reason):
        H.set_task_readers(c, R1, row["id"], add=[R3])
    with pytest.raises(H.Refused):
        H.task_update(c, R1, row["id"], private=False)
    with pytest.raises(H.Refused, match=reason):
        H.task_reassign(c, R1, row["id"], R3, "Yours.", H.task(c, row["id"])["version"])
    after = H.task(c, row["id"])
    assert H.task_readers(after) == [BEN] and H.task_private(c, after) and after["owner_id"] == R1
    H.set_task_readers(c, RULES, row["id"], add=[R3])  # its requester still shares it
    assert H.task_readers(H.task(c, row["id"])) == [BEN, R3]


def test_a_task_the_stage_bot_files_from_the_private_task_is_private_with_the_same_readers(c):
    """Criterion 7."""
    _engineering(c)
    row = _private(c)
    _done(c, row, BUILDER)
    child = H.task_create(c, R1, "Follow-up", "From review.", R1, lint=False, parent_id=row["id"], private=False)
    child = H.task(c, child["id"])
    assert H.task_private(c, child) and H.task_readers(child) == [BEN]


def test_a_bot_cannot_change_who_holds_a_stage_of_a_pipeline_with_a_private_task_and_a_person_can(c):
    """Criterion 8: on a pipeline that holds an open private task only a person changes a stage's owner bot, and
    their change is in the pipeline's history."""
    _engineering(c)
    _private(c)
    record = PS.read(c, "eng")
    for stage in record["stages"]:
        if stage["name"] == "Review 1":
            stage["owner"] = R3
    with pytest.raises(H.Refused, match="Only a person changes who holds Review 1"):
        PS.save(c, MANAGER, record, record["settings_version"])
    saved = PS.save(c, ANA, record, record["settings_version"])
    assert next(s for s in saved["stages"] if s["name"] == "Review 1")["owner"] == R3
    found = c.execute("SELECT actor FROM events WHERE action='pipeline.save' AND target='eng'").fetchall()
    assert [r[0] for r in found][-1] == ANA


def test_with_no_private_task_a_bot_still_changes_a_stage_owner(c):
    """The guard is for pipelines that hold a private task: elsewhere a bot's edit is as before."""
    _engineering(c)
    record = PS.read(c, "eng")
    for stage in record["stages"]:
        if stage["name"] == "Review 1":
            stage["owner"] = R3
    saved = PS.save(c, MANAGER, record, record["settings_version"])
    assert next(s for s in saved["stages"] if s["name"] == "Review 1")["owner"] == R3
