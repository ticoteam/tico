"""Handoff links between pipelines (Track A): a move link lands the ticket in the first stage of the target pipeline, a link
that would branch is refused, only rule editors link, and a handoff row goes to the trash and back with its ticket."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend import hubdb as H
from backend import pipeline_engine as E
from backend import pipeline_links as L
from backend import task_delete
from backend.tests.test_pipeline_engine import BEN, requirement, states
from backend.tests.test_pipeline_foundation import ANA, boot, hub, rows, step_id


@pytest.fixture(scope="module")
def booted(tmp_path_factory) -> Path:
    """One boot for the module (Store.initialize is the slow part); each test copies it."""
    path = tmp_path_factory.mktemp("pipeline-links") / "booted.sqlite"
    boot(path)
    return path


def two(tmp_path, booted):
    path = tmp_path / "hub.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(booted)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    c = hub(path)
    sales = H.type_create(
        c,
        ANA,
        "Sales",
        [
            {"name": "Lead", "status": "open"},
            {"name": "Won", "status": "done"},
            {"name": "Lost", "status": "closed"},
        ],
        mover=True,
    )
    success = H.type_create(
        c,
        ANA,
        "Success",
        [{"name": "Onboarding", "status": "doing"}, {"name": "Active", "status": "doing"}],
        mover=True,
    )
    other = H.type_create(c, ANA, "Support", [{"name": "Inbox", "status": "open"}], mover=True)
    for t in (sales, success, other):
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    return c, sales, success, other


def refused(fn):
    with pytest.raises(H.Refused) as raised:
        fn()
    return raised.value


def test_a_move_link_sends_the_ticket_to_the_targets_first_stage_and_records_it(tmp_path, booted):
    c, sales, success, _ = two(tmp_path, booted)
    link = L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"], "move")
    assert L.link_for_stage(c, step_id(c, sales["id"], "Won"))["id"] == link["id"]
    requirement(c, success, "Onboarding", "Kickoff held", reviewed_by="person")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    moved = E.advance(c, ANA, task)
    assert moved["moved"] and moved["handoff"] == link["id"]
    row = c.execute("SELECT type_id, step_id FROM tasks WHERE id=?", (task,)).fetchone()
    assert (row[0], row[1]) == (success["id"], step_id(c, success["id"], "Onboarding"))
    handoffs = L.handoffs(c, link["id"])
    assert [(h["source_task_id"], h["target_task_id"]) for h in handoffs] == [(task, task)]
    assert [r["direction"] for r in rows(c, "SELECT direction FROM stage_visits WHERE task_id=? ORDER BY rowid", task)][
        -1
    ] == "handoff"


def test_advance_without_a_link_goes_to_the_next_stage_and_stops_at_the_end(tmp_path, booted):
    c, sales, _, _ = two(tmp_path, booted)
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    assert E.advance(c, ANA, task)["to"] == step_id(c, sales["id"], "Won")
    assert E.advance(c, ANA, task) == {
        "moved": False,
        "to": None,
        "pipeline": None,
        "handoff": None,
        "reason": "There is no next stage",
    }


def test_advance_obeys_requirements_and_undoes_only_itself(tmp_path, booted):
    c, sales, _, _ = two(tmp_path, booted)
    requirement(c, sales, "Lead", "Qualified", reviewed_by="person")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    out = E.advance(c, ANA, task)
    assert not out["moved"] and "Qualified" in out["reason"]
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (task,)).fetchone()[0] == step_id(c, sales["id"], "Lead")
    assert states(c, task) == {"Qualified": "waiting"}


def test_a_link_that_branches_loops_or_has_nowhere_to_go_is_refused(tmp_path, booted):
    c, sales, success, other = two(tmp_path, booted)
    won = step_id(c, sales["id"], "Won")
    L.create_link(c, ANA, won, success["id"], "move")
    assert "no branches" in refused(lambda: L.create_link(c, ANA, won, other["id"], "spawn")).detail
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), sales["id"])).rule == "link"
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lost"), other["id"])).rule == "link"
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), "nowhere")).rule == "link"
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), other["id"], "copy")).rule == "link"
    assert len(L.links(c)) == 1


def test_only_rule_editors_change_links_and_a_retired_link_frees_the_stage(tmp_path, booted):
    c, sales, success, other = two(tmp_path, booted)
    won = step_id(c, sales["id"], "Won")
    assert refused(lambda: L.create_link(c, BEN, won, success["id"])).rule == "link"
    link = L.create_link(c, ANA, won, success["id"])
    assert refused(lambda: L.retire_link(c, BEN, link["id"])).rule == "link"
    L.retire_link(c, ANA, link["id"])
    assert L.link_for_stage(c, won) is None
    assert L.create_link(c, ANA, won, other["id"], "spawn")["mode"] == "spawn"
    assert refused(lambda: L.retire_link(c, ANA, link["id"])).rule == "link"


def test_a_handoff_row_follows_its_ticket_through_delete_and_restore(tmp_path, booted):
    c, sales, success, _ = two(tmp_path, booted)
    link = L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"])
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    other = H.task_create(c, ANA, "Beta deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    L.record_handoff(c, link["id"], task, other)
    assert task_delete.delete_tasks(c, [task], apply=True, actor=ANA)["applied"]
    assert L.handoffs(c, link["id"]) == []
    assert task_delete.restore_tasks(c, [task], actor=ANA)["restored"] == [task]
    assert [(h["source_task_id"], h["target_task_id"]) for h in L.handoffs(c, link["id"])] == [(task, other)]
    c.close()


def test_a_move_link_handoff_keeps_the_source_approval_in_force_once_the_work_changes(tmp_path, booted):
    """The source pipeline's approval, given before the handoff, still gates the target: new work makes it out of date
    and the ticket cannot move on in the target pipeline until it is approved again."""
    from backend.tests.test_pipeline_engine import decide

    c, sales, success, _ = two(tmp_path, booted)
    L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"], "move")
    signed = requirement(c, sales, "Won", "Signed off")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    decide(c, task, signed)
    assert E.advance(c, ANA, task)["moved"]
    assert states(c, task) == {}  # met at the source: its dot is hidden on the target stage
    E.work_changed(c, task)
    assert states(c, task) == {"Signed off": "out_of_date"}
    error = refused(lambda: H.task_update(c, ANA, task, step=step_id(c, success["id"], "Active")))
    assert error.rule == "requirement" and "Signed off" in error.detail


def test_a_refused_parent_move_after_a_subtask_finishes_keeps_the_subtasks_change(tmp_path, monkeypatch, booted):
    """Track Y: a subtask's finish moves its parent on from inside the subtask's own write. When the parent's move is
    refused (here a spawn that could not be filed), only the parent's move rolls back: the subtask's change stands, and the
    refusal is logged on the parent with its reason."""
    from backend.store import Problem

    c, _, _, _ = two(tmp_path, booted)
    product = H.type_create(
        c,
        ANA,
        "Spec pipeline",
        [{"name": "Review", "status": "review"}, {"name": "Build", "status": "doing"}],
        mover=True,
    )
    eng = H.type_create(
        c, ANA, "Build pipeline", [{"name": "Dev", "status": "doing"}, {"name": "Done", "status": "done"}], mover=True
    )
    for t in (product, eng):
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    L.create_link(c, ANA, step_id(c, product["id"], "Review"), eng["id"], "spawn")
    requirement(
        c, product, "Review", "Engineering shipped", kind="run", needs={"source": "subtasks"}, reviewed_by="nobody"
    )
    parent = H.task_create(c, ANA, "Spec", "", ANA, type=product["id"], step="Review", lint=False)["id"]
    child = H.task_create(c, ANA, "Build it", "", ANA, type=eng["id"], step="Dev", parent_id=parent, lint=False)["id"]

    def refuse_spawn(*args, **kwargs):
        raise Problem("spawn_refused", "The linked ticket could not be filed: test", 409)

    monkeypatch.setattr(L, "spawn", refuse_spawn)
    H.task_update(c, ANA, child, step=step_id(c, eng["id"], "Done"))

    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (child,)).fetchone()[0] == step_id(c, eng["id"], "Done")
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (parent,)).fetchone()[0] == step_id(
        c, product["id"], "Review"
    )
    [logged] = rows(c, "SELECT note FROM history_events WHERE subject_id=? AND kind='auto_move_refused'", parent)
    assert "could not be filed" in logged["note"]


def test_a_blob_shared_by_many_tasks_is_skipped_in_a_few_statements(tmp_path, booted):
    """Codex r6 blocker 2: the provenance walk counted blobs and files but not tasks, and read every row of each lookup, so
    one blob on 10,000 tasks ran 20,000 statements under the writer. Tasks count toward the budget and each lookup reads
    at most the budget left plus one: past it the item is unsure (skipped), after a handful of statements."""
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    c.execute("PRAGMA foreign_keys=OFF")  # bare rows: only the walk's reads matter here
    c.executemany(
        "INSERT INTO task_assets VALUES(?,?)", [(f"t{n}", "shared-blob") for n in range(L.PROVENANCE_LIMIT + 300)]
    )
    statements: list[str] = []
    c.set_trace_callback(statements.append)
    assert L._pipeline_tasks(c, {"blob_id": "shared-blob"}) is None
    c.set_trace_callback(None)
    assert len(statements) < 10
