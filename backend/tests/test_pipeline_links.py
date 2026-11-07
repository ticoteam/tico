"""Handoff links between pipelines (Track A): a move link lands the ticket in the first stage of the target pipeline, a link
that would branch is refused, only rule editors link, and a handoff row goes to the trash and back with its ticket."""

import pytest

from backend import hubdb as H
from backend import pipeline_engine as E
from backend import pipeline_links as L
from backend import task_delete
from backend.tests.test_pipeline_engine import BEN, requirement, states
from backend.tests.test_pipeline_foundation import ANA, boot, hub, rows, step_id


def two(tmp_path):
    boot(tmp_path / "hub.sqlite")
    c = hub(tmp_path / "hub.sqlite")
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


def test_a_move_link_sends_the_ticket_to_the_targets_first_stage_and_records_it(tmp_path):
    c, sales, success, _ = two(tmp_path)
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


def test_advance_without_a_link_goes_to_the_next_stage_and_stops_at_the_end(tmp_path):
    c, sales, _, _ = two(tmp_path)
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    assert E.advance(c, ANA, task)["to"] == step_id(c, sales["id"], "Won")
    assert E.advance(c, ANA, task) == {
        "moved": False,
        "to": None,
        "pipeline": None,
        "handoff": None,
        "reason": "There is no next stage",
    }


def test_advance_obeys_requirements_and_undoes_only_itself(tmp_path):
    c, sales, _, _ = two(tmp_path)
    requirement(c, sales, "Lead", "Qualified", reviewed_by="person")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    out = E.advance(c, ANA, task)
    assert not out["moved"] and "Qualified" in out["reason"]
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (task,)).fetchone()[0] == step_id(c, sales["id"], "Lead")
    assert states(c, task) == {"Qualified": "waiting"}


def test_a_link_that_branches_loops_or_has_nowhere_to_go_is_refused(tmp_path):
    c, sales, success, other = two(tmp_path)
    won = step_id(c, sales["id"], "Won")
    L.create_link(c, ANA, won, success["id"], "move")
    assert "no branches" in refused(lambda: L.create_link(c, ANA, won, other["id"], "spawn")).detail
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), sales["id"])).rule == "link"
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lost"), other["id"])).rule == "link"
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), "nowhere")).rule == "link"
    assert refused(lambda: L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), other["id"], "copy")).rule == "link"
    assert len(L.links(c)) == 1


def test_only_rule_editors_change_links_and_a_retired_link_frees_the_stage(tmp_path):
    c, sales, success, other = two(tmp_path)
    won = step_id(c, sales["id"], "Won")
    assert refused(lambda: L.create_link(c, BEN, won, success["id"])).rule == "link"
    link = L.create_link(c, ANA, won, success["id"])
    assert refused(lambda: L.retire_link(c, BEN, link["id"])).rule == "link"
    L.retire_link(c, ANA, link["id"])
    assert L.link_for_stage(c, won) is None
    assert L.create_link(c, ANA, won, other["id"], "spawn")["mode"] == "spawn"
    assert refused(lambda: L.retire_link(c, ANA, link["id"])).rule == "link"


def test_a_handoff_row_follows_its_ticket_through_delete_and_restore(tmp_path):
    c, sales, success, _ = two(tmp_path)
    link = L.create_link(c, ANA, step_id(c, sales["id"], "Won"), success["id"])
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    other = H.task_create(c, ANA, "Beta deal", "", ANA, type=sales["id"], step="Won", lint=False)["id"]
    L.record_handoff(c, link["id"], task, other)
    assert task_delete.delete_tasks(c, [task], apply=True, actor=ANA)["applied"]
    assert L.handoffs(c, link["id"]) == []
    assert task_delete.restore_tasks(c, [task], actor=ANA)["restored"] == [task]
    assert [(h["source_task_id"], h["target_task_id"]) for h in L.handoffs(c, link["id"])] == [(task, other)]
    c.close()
