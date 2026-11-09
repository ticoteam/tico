"""Pipeline engine F0: the schema and the hooks with no behaviour behind them. The F0 migrations backfill a booted
database as they should and a second boot changes nothing; the stage and factory tables refuse what the engine must
never write; the rights helper follows the owner and admins; and a stage with no requirements moves exactly as before."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend import access
from backend import hubdb as H
from backend import pipeline_engine, pipeline_rights
from backend.config import Settings
from backend.store import Store

ANA = "human:ana"
T = "2026-11-07T09:00:00.000000Z"
FIXTURES = Path(__file__).parent / "fixtures"
F0 = (
    "20261107163621_pipeline_columns",
    "20261107163622_pipeline_tables",
    "20261107163623_pipeline_stage_visits",
    "20261107164247_pipeline_factories",
)


def boot(path):
    Store(Settings(db_path=path, registry_dir=FIXTURES / "registry")).initialize(seed_market=False)


def hub(path):
    c = H.connect(path)
    H.sync_registry(
        c,
        {},
        {
            "people": [
                {"id": "ana", "email": "ana@acme.example"},
                {"id": "ben", "email": "ben@acme.example"},
                {"id": "chris", "email": "chris@acme.example"},
            ]
        },
    )
    return c


def rows(c, sql, *args):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def step_id(c, type_id, name):
    return c.execute("SELECT id FROM task_steps WHERE type_id=? AND name=?", (type_id, name)).fetchone()[0]


def insert(c, table, **values):
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    c.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))


def state(path):
    with closing(sqlite3.connect(path)) as raw:
        raw.row_factory = sqlite3.Row
        return {
            "prefixes": {r["id"]: r["prefix"] for r in rows(raw, "SELECT id, prefix FROM task_types")},
            "limits": {r["id"]: r["limit_minutes"] for r in rows(raw, "SELECT id, limit_minutes FROM task_steps")},
            "visits": rows(
                raw,
                "SELECT task_id, pipeline_id, pipeline_stage_id, entered_from_stage_id, direction, entered_by, "
                "entered_at, left_at FROM stage_visits ORDER BY task_id",
            ),
        }


def test_the_f0_backfills_fill_a_booted_database_and_a_second_boot_changes_nothing(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    dev = H.type_create(
        c,
        ANA,
        "Dev tickets",
        [
            {"name": "Building", "status": "doing"},
            {"name": "Review", "status": "doing"},
            {"name": "Shipped", "status": "done"},
        ],
        numbered=True,
        mover=True,
    )
    content = H.type_create(
        c,
        ANA,
        "Content",
        [{"name": "Drafting", "status": "doing"}, {"name": "Published", "status": "done"}],
        mover=True,
    )
    c.execute("UPDATE task_steps SET flow=? WHERE id=?", ('{"late_hours": 2}', step_id(c, dev["id"], "Review")))
    c.execute("UPDATE task_steps SET flow=? WHERE id=?", ('{"late_hours": 1.5}', step_id(c, content["id"], "Drafting")))
    active = H.task_create(c, ANA, "Fix the login", "", ANA, type=dev["id"], step="Building", lint=False)
    H.task_update(c, ANA, active["id"], step="Review")
    draft = H.task_create(c, ANA, "Write the launch post", "", ANA, type=content["id"], step="Drafting", lint=False)
    closed = H.task_create(c, ANA, "Drop the old banner", "", ANA, type=content["id"], step="Drafting", lint=False)
    H.task_close(c, ANA, closed["id"], note="Not needed")
    # The Product roadmap type boot creates has no role until the next start (roadmap_flow.ensure runs after its claim).
    product = c.execute("SELECT id FROM task_types WHERE name='Product roadmap'").fetchone()[0]
    c.execute("UPDATE task_types SET role='product-roadmap' WHERE id=?", (product,))

    # The file as it was before F0: its migration rows gone, the values they filled back to empty.
    c.execute("UPDATE task_types SET prefix=NULL")
    c.execute("UPDATE task_steps SET limit_minutes=NULL")
    c.execute("DELETE FROM stage_visits")
    c.close()
    with closing(sqlite3.connect(path)) as raw:
        raw.executemany("DELETE FROM schema_migrations WHERE version=?", [(v,) for v in F0])
        raw.commit()
    boot(path)

    first = state(path)
    assert first["prefixes"][dev["id"]] == "D"
    assert first["prefixes"][product] == "P"
    assert first["prefixes"][content["id"]] is None
    c = H.connect(path)
    assert first["limits"][step_id(c, dev["id"], "Building")] == 240  # an engineering working step: 4 hours
    assert first["limits"][step_id(c, dev["id"], "Review")] == 120  # its flow's late_hours 2, in minutes
    assert first["limits"][step_id(c, dev["id"], "Shipped")] is None
    assert first["limits"][step_id(c, content["id"], "Drafting")] == 90  # late_hours 1.5 on a content step

    moved = c.execute(
        "SELECT * FROM history_events WHERE subject_id=? AND kind='step' ORDER BY created_at DESC, rowid DESC",
        (active["id"],),
    ).fetchone()
    visits = {v["task_id"]: v for v in first["visits"]}
    assert set(visits) == {active["id"], draft["id"]}  # the closed ticket has no visit
    assert visits[active["id"]]["pipeline_stage_id"] == step_id(c, dev["id"], "Review")
    assert visits[active["id"]]["entered_from_stage_id"] == moved["old"]
    assert visits[active["id"]]["entered_at"] == moved["created_at"]
    assert visits[active["id"]]["direction"] == "initial" and visits[active["id"]]["left_at"] is None
    assert visits[draft["id"]]["pipeline_stage_id"] == step_id(c, content["id"], "Drafting")
    assert c.execute("SELECT work_revision FROM tasks WHERE id=?", (active["id"],)).fetchone()[0] == 0
    assert (
        c.execute(
            "SELECT factory_id, prefix, manager_every_minutes FROM task_types WHERE id=?", (dev["id"],)
        ).fetchone()["manager_every_minutes"]
        == 10
    )
    assert (
        c.execute("SELECT limit_minutes FROM task_steps WHERE id=?", (step_id(c, dev["id"], "Review"),)).fetchone()[0]
        == 120
    )
    c.close()

    boot(path)
    assert state(path) == first


def test_the_stage_and_factory_tables_refuse_what_the_engine_must_never_write(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    with closing(sqlite3.connect(path, isolation_level=None)) as c:
        requirement = dict(
            id="r1",
            pipeline_id="p",
            pipeline_stage_id="s",
            name="Screenshot",
            kind="attachment",
            reviewed_by="person",
            created_at=T,
            updated_at=T,
        )
        insert(c, "stage_requirements", **requirement)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            insert(
                c,
                "stage_requirements",
                **{**requirement, "id": "r2", "name": "Sign off", "kind": "approval", "reviewed_by": "bot"},
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(c, "stage_requirements", **{**requirement, "id": "r3"})
        c.execute("UPDATE stage_requirements SET retired_at=? WHERE id='r1'", (T,))
        insert(c, "stage_requirements", **{**requirement, "id": "r4"})

        decision = dict(id="d1", task_id="t", pipeline_stage_id="s", judge="person", verdict="met", created_at=T)
        insert(c, "stage_decisions", **decision)
        c.execute("UPDATE stage_decisions SET outcome='advanced', outcome_at=? WHERE id='d1'", (T,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("UPDATE stage_decisions SET verdict='failed' WHERE id='d1'")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            insert(c, "stage_decisions", **{**decision, "id": "d2", "reason": "x" * 501})

        insert(c, "stage_requirement_versions", stage_requirement_id="r1", version=1, definition="{}", created_at=T)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("UPDATE stage_requirement_versions SET definition='{\"n\": 2}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute("DELETE FROM stage_requirement_versions")

        visit = dict(id="v1", task_id="t", pipeline_id="p", pipeline_stage_id="s", direction="initial", entered_at=T)
        insert(c, "stage_visits", **visit)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(c, "stage_visits", **{**visit, "id": "v2"})
        c.execute("UPDATE stage_visits SET left_at=? WHERE id='v1'", (T,))
        insert(c, "stage_visits", **{**visit, "id": "v3"})

        link = dict(
            id="l1", from_pipeline_id="p", from_pipeline_stage_id="s", to_pipeline_id="q", mode="move", created_at=T
        )
        insert(c, "pipeline_links", **link)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(c, "pipeline_links", **{**link, "id": "l2"})

        claim = dict(id="c1", task_id="t", stage_requirement_id="r4", claimed_by=ANA, claimed_at=T)
        insert(c, "review_claims", **claim)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(c, "review_claims", **{**claim, "id": "c2"})

        factory = dict(id="f1", name="Product factory", created_at=T, updated_at=T)
        insert(c, "factories", **factory)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert(c, "factories", **{**factory, "id": "f2"})
        c.execute("UPDATE factories SET retired_at=? WHERE id='f1'", (T,))
        insert(c, "factories", **{**factory, "id": "f3"})


def test_only_the_pipeline_owner_and_admins_may_change_its_rules(tmp_path, monkeypatch):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    board = H.type_create(c, ANA, "Content", [{"name": "Drafting", "status": "doing"}], mover=True)
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, board["id"]))
    people = {"human:ben": {"email": "ben@acme.example"}, "human:chris": {"email": "chris@acme.example"}}
    monkeypatch.setattr(H, "human", lambda conn, pid: people.get(f"human:{pid}"))
    monkeypatch.setattr(access, "owner_and_admin_emails", lambda conn: ["chris@acme.example"])
    assert pipeline_rights.may_edit_rules(c, ANA, board["id"])
    assert pipeline_rights.may_edit_rules(c, "human:chris", board["id"])
    assert not pipeline_rights.may_edit_rules(c, "human:ben", board["id"])
    assert not pipeline_rights.may_edit_rules(c, "bot:cmo", board["id"])
    assert not pipeline_rights.may_edit_rules(c, ANA, "no-such-pipeline")
    assert not pipeline_rights.may_edit_rules(c, "", board["id"])
    c.close()


def test_a_stage_with_no_requirements_moves_exactly_as_before(tmp_path):
    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    board = H.type_create(
        c, ANA, "Content", [{"name": "Drafting", "status": "doing"}, {"name": "Editing", "status": "doing"}], mover=True
    )
    task = H.task_create(c, ANA, "Write the launch post", "", ANA, type=board["id"], step="Drafting", lint=False)
    drafting, editing = step_id(c, board["id"], "Drafting"), step_id(c, board["id"], "Editing")

    after = H.task_update(c, ANA, task["id"], step="Editing")
    assert after["step_id"] == editing and after["status"] == "doing"
    moved = H.task_update(c, ANA, task["id"], step="Drafting", override_requirements=True)
    assert moved["step_id"] == drafting
    steps = rows(
        c,
        "SELECT old, new FROM history_events WHERE subject_id=? AND kind='step' ORDER BY created_at, rowid",
        task["id"],
    )
    assert [(s["old"], s["new"]) for s in steps][-2:] == [(drafting, editing), (editing, drafting)]
    for table in ("stage_visits", "stage_decisions", "requirement_attachments", "review_claims", "pipeline_links"):
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert pipeline_engine.check_move(c, ANA, dict(task), editing) is False  # no cap, so no move is over one
    assert pipeline_engine.dots_many(c, [dict(task)]) == {}
    c.close()


def test_deleting_a_ticket_takes_its_stage_rows_to_the_trash_and_a_restore_brings_them_back(tmp_path):
    from backend import task_delete

    path = tmp_path / "hub.sqlite"
    boot(path)
    c = hub(path)
    board = H.type_create(c, ANA, "Content", [{"name": "Drafting", "status": "doing"}], mover=True)
    task = H.task_create(c, ANA, "Write the launch post", "", ANA, type=board["id"], step="Drafting", lint=False)
    drafting = step_id(c, board["id"], "Drafting")
    insert(
        c,
        "stage_visits",
        id="v1",
        task_id=task["id"],
        pipeline_id=board["id"],
        pipeline_stage_id=drafting,
        direction="initial",
        entered_at=T,
    )
    insert(
        c,
        "stage_decisions",
        id="d1",
        task_id=task["id"],
        pipeline_stage_id=drafting,
        stage_visit_id="v1",
        judge="person",
        judge_actor=ANA,
        verdict="override",
        reason="Checked it myself",
        created_at=T,
    )
    assert task_delete.delete_tasks(c, [task["id"]], apply=True, actor=ANA)["applied"]
    for table in ("stage_visits", "stage_decisions"):
        assert c.execute(f"SELECT COUNT(*) FROM {table} WHERE task_id=?", (task["id"],)).fetchone()[0] == 0, table
    assert task_delete.restore_tasks(c, [task["id"]], actor=ANA)["restored"] == [task["id"]]
    assert [r["id"] for r in rows(c, "SELECT id FROM stage_visits WHERE task_id=?", task["id"])] == ["v1"]
    assert [r["verdict"] for r in rows(c, "SELECT verdict FROM stage_decisions WHERE task_id=?", task["id"])] == [
        "override"
    ]
    c.close()
