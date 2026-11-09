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
from backend.tests.helpers.pipeline_engine import BEN, requirement, states
from backend.tests.helpers.pipeline_foundation import ANA, boot, hub, rows, step_id


@pytest.fixture(scope="module")
def booted(tmp_path_factory) -> Path:
    """One boot for the module (Store.initialize is the slow part); each test copies it."""
    path = tmp_path_factory.mktemp("pipeline-links") / "booted.sqlite"
    boot(path)
    return path


def two(tmp_path, booted=None):
    """Two pipelines on a fresh hub: a copy of the module's boot, or (from other modules) a boot of its own."""
    path = tmp_path / "hub.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    if booted is None:
        boot(path)
    else:
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
    from backend.tests.helpers.pipeline_engine import decide

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


def leave_pipeline(c, name, stages):
    """A pipeline with the given (name, status) stages, owned by ANA so the test may edit its links."""
    t = H.type_create(c, ANA, name, [{"name": n, "status": s} for n, s in stages], mover=True)
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    return t


def test_a_spawn_that_fires_on_leave_into_a_done_stage_keeps_the_child_it_files_open(tmp_path, booted):
    """Spec 871 AT1: a card that leaves a stage with a spawn link into a done stage files its child, and the same move's finish
    leaves that child open (it used to close it with the card)."""
    c, sales, _, other = two(tmp_path, booted)
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), other["id"], "spawn")
    card = H.task_create(c, ANA, "Acme lead", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    H.task_update(c, ANA, card, step=step_id(c, sales["id"], "Won"))
    assert rows(c, "SELECT status FROM tasks WHERE id=?", card) == [{"status": "done"}]
    [child] = rows(c, "SELECT id, status, note FROM tasks WHERE type_id=?", other["id"])
    assert child["status"] == "open" and not child["note"]


def test_a_bot_manager_move_that_files_a_leave_spawn_into_a_done_stage_is_not_refused(tmp_path, booted):
    """Spec 871 AT2: a bot that may move a card its manager's report owns files the child and finishes the card in one move.
    The child is the report's, so the bot that moved it does not close it, and the move is not refused with 'close'."""
    c, sales, _, other = two(tmp_path, booted)
    H.sync_registry(c, {"coo": {}, "ops": {}}, {"people": []})
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", ("bot:ops", sales["id"]))
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), other["id"], "spawn")
    card = H.task_create(c, ANA, "Acme lead", "", "bot:coo", type=sales["id"], step="Lead", lint=False)["id"]
    H.task_update(c, "bot:ops", card, step=step_id(c, sales["id"], "Won"), mover=True)
    assert rows(c, "SELECT status FROM tasks WHERE id=?", card) == [{"status": "done"}]
    assert rows(c, "SELECT status, requester_id FROM tasks WHERE type_id=?", other["id"]) == [
        {"status": "open", "requester_id": "bot:coo"}
    ]


def test_a_child_filed_on_leave_earlier_stays_open_when_the_card_later_finishes_as_done(tmp_path, booted):
    """Spec 871 AT4 (broad fix A1): a live leave-spawn child filed by an earlier move out of a working stage is not closed
    when the card is later moved into a done stage. A close (a cancel) still takes it."""
    c, _, _, other = two(tmp_path, booted)
    pipe = leave_pipeline(
        c, "Pipeline A", [("Working", "doing"), ("Review", "doing"), ("Won", "done"), ("Lost", "closed")]
    )
    L.create_link(c, ANA, step_id(c, pipe["id"], "Working"), other["id"], "spawn")
    card = H.task_create(c, ANA, "Acme lead", "", ANA, type=pipe["id"], step="Working", lint=False)["id"]
    H.task_update(c, ANA, card, step=step_id(c, pipe["id"], "Review"))
    [child] = rows(c, "SELECT id, status FROM tasks WHERE type_id=?", other["id"])
    assert child["status"] == "open"
    H.task_update(c, ANA, card, step=step_id(c, pipe["id"], "Won"))
    assert rows(c, "SELECT status FROM tasks WHERE id=?", card) == [{"status": "done"}]
    assert rows(c, "SELECT status FROM tasks WHERE id=?", child["id"]) == [{"status": "open"}]
    H.task_close(c, ANA, card, note="cancelled")
    assert rows(c, "SELECT status FROM tasks WHERE id=?", child["id"]) == [{"status": "closed"}]


def test_a_leave_spawn_into_a_closed_stage_files_no_child(tmp_path, booted):
    """Spec 871 AT5: a move from a spawn stage into a closed or declined stage files nothing (no change from before)."""
    c, sales, _, other = two(tmp_path, booted)
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), other["id"], "spawn")
    card = H.task_create(c, ANA, "Acme lead", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    H.task_update(c, ANA, card, step=step_id(c, sales["id"], "Lost"))
    assert rows(c, "SELECT id FROM tasks WHERE type_id=?", other["id"]) == []


def test_a_link_fires_on_enter_or_leave_and_only_a_spawn_can_fire_on_enter(tmp_path, booted):
    """Spec 871 AT6-AT9: a spawn link may fire on enter (saved and read back as enter), a spawn link without fires fires on
    leave as before, and a move link can't fire on enter."""
    c, sales, _, other = two(tmp_path, booted)
    lead = step_id(c, sales["id"], "Lead")
    enter = L.create_link(c, ANA, lead, other["id"], "spawn", fires="enter")
    assert enter["fires"] == "enter"
    assert [r["fires"] for r in L.links(c, sales["id"])] == ["enter"]
    L.retire_link(c, ANA, enter["id"])
    assert L.create_link(c, ANA, lead, other["id"], "spawn")["fires"] == "leave"
    won = step_id(c, sales["id"], "Won")
    assert refused(lambda: L.create_link(c, ANA, won, other["id"], "move", fires="enter")).rule == "link"
    assert rows(
        c, "SELECT COUNT(*) AS n FROM pipeline_links WHERE from_pipeline_stage_id=? AND retired_at IS NULL", won
    ) == [{"n": 0}]


def test_the_link_route_accepts_fires_enter_or_leave_and_shows_it_back(tmp_path):
    """Spec 871 AT6, AT7, AT8, AT9 through the route: a spawn link saved with fires enter reads back as enter, one without
    fires fires on leave, and a move link can't fire on enter (refused, nothing created)."""
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.auth import Identity
    from backend.config import Settings

    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            registry_dir=tmp_path,
            test_identities={"ana-test": Identity("human:ana", "owner", "ana@acme.example")},
        )
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(c, {}, {"people": [{"id": "ana", "email": "ana@acme.example"}]})
            sales = H.type_create(
                c, ANA, "Sales", [{"name": "Lead", "status": "open"}, {"name": "Won", "status": "done"}], mover=True
            )
            other = H.type_create(c, ANA, "Support", [{"name": "Inbox", "status": "open"}], mover=True)
            for t in (sales, other):
                c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
            lead = step_id(c, sales["id"], "Lead")
            won = step_id(c, sales["id"], "Won")
            other_id = other["id"]
        headers = {"Authorization": "Bearer ana-test"}
        calls = iter(range(1, 100))

        def post(body):
            return client.post(
                "/api/v2/pipeline-links", json=body, headers={**headers, "Idempotency-Key": f"k{next(calls)}"}
            )

        enter = post({"from_stage_id": lead, "to_pipeline_id": other_id, "mode": "spawn", "fires": "enter"})
        assert enter.status_code == 200, enter.text
        assert enter.json()["link"]["fires"] == "enter"
        assert [r["fires"] for r in client.get("/api/v2/pipeline-links", headers=headers).json()["links"]] == ["enter"]
        bad = post({"from_stage_id": won, "to_pipeline_id": other_id, "mode": "move", "fires": "enter"})
        assert bad.status_code == 422 and "Only a spawn link" in bad.text, bad.text
        assert (
            post({"from_stage_id": won, "to_pipeline_id": other_id, "mode": "move"}).json()["link"]["fires"] == "leave"
        )
