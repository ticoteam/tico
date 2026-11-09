"""The Tico Team pipeline setup (Track A): it makes the factories, pipelines, stages, links and goals, adopts what exists,
changes nothing on a second run, sets managers only where a matching bot exists, and wipes cards only with replace."""

import json

import pytest

from backend import hubdb as H
from backend import pipeline_engine as E
from backend import pipeline_links as L
from backend import pipeline_requirements as R
from backend import pipeline_setup as S
from backend import public_roadmaps
from backend.tests.test_pipeline_foundation import ANA, boot, hub, rows, step_id


def fresh(tmp_path):
    boot(tmp_path / "hub.sqlite")
    return hub(tmp_path / "hub.sqlite")


def snapshot(c):
    return {
        "types": rows(
            c,
            "SELECT id, name, prefix, goal, factory_id, manager, pipeline_visibility, numbered FROM task_types ORDER BY id",
        ),
        "steps": rows(
            c, "SELECT id, type_id, name, status, position, card_cap, public_name FROM task_steps ORDER BY id"
        ),
        "requirements": rows(
            c,
            "SELECT id, pipeline_stage_id, name, kind, needs, reviewed_by, rubric, version, retired_at "
            "FROM stage_requirements ORDER BY id",
        ),
        "factories": rows(c, "SELECT id, name, goal FROM factories ORDER BY id"),
        "links": rows(c, "SELECT id, from_pipeline_stage_id, to_pipeline_id, mode FROM pipeline_links ORDER BY id"),
    }


def bot(c, slug, name, state="active"):
    c.execute("INSERT INTO bots (id, slug, display_name, state) VALUES (?, ?, ?, ?)", (slug, slug, name, state))


def set_manager(c, pipeline, slug):
    c.execute("UPDATE task_types SET manager=? WHERE name=?", (slug, pipeline))


def manager_of(c, pipeline):
    return rows(c, "SELECT manager FROM task_types WHERE name=?", pipeline)[0]["manager"]


UNMANAGED = [
    "Marketing has no active manager: name a bot Marketing Pipeline Manager",
    "Sales has no active manager: name a bot Sales Pipeline Manager",
    "Success has no active manager: name a bot Success Pipeline Manager",
    "Legal has no active manager: name a bot Legal Pipeline Manager",
]


def test_setup_makes_the_factories_pipelines_links_and_goals(tmp_path):
    c = fresh(tmp_path)
    out = S.apply_factory_setup(c, ANA)
    assert set(out["pipelines"]) == {"Product", "Engineering", "Marketing", "Sales", "Success", "Legal"}
    by_name = {r["name"]: r for r in rows(c, "SELECT * FROM task_types")}
    for name in out["pipelines"]:
        assert by_name[name]["goal"], name
        assert by_name[name]["pipeline_owner_id"] == ANA
    assert by_name["Engineering"]["prefix"] == "D" and by_name["Product"]["prefix"] == "P"
    assert by_name["Marketing"]["prefix"] == "M" and by_name["Success"]["prefix"] == "SU"
    assert by_name["Legal"]["pipeline_visibility"] == "members" and by_name["Legal"]["factory_id"] is None
    growth = rows(c, "SELECT id FROM factories WHERE name='Growth'")[0]["id"]
    assert by_name["Marketing"]["factory_id"] == by_name["Sales"]["factory_id"] == growth
    assert [
        s["name"]
        for s in rows(c, "SELECT name FROM task_steps WHERE type_id=? ORDER BY position", by_name["Sales"]["id"])
    ] == ["Lead", "Talking", "Proposal", "Closing", "Won", "Lost", "Closed"]  # the Closed stage holds closed cards
    # Leads and customers live in the CRM, so no revenue link crosses Marketing, Sales and Success (only Product spawns).
    crossed = rows(
        c,
        "SELECT f.name AS f, t.name AS t FROM pipeline_links l JOIN task_types f ON f.id = l.from_pipeline_id "
        "JOIN task_types t ON t.id = l.to_pipeline_id WHERE l.retired_at IS NULL",
    )
    assert [(r["f"], r["t"]) for r in crossed] == [("Product", "Engineering")]
    assert all(by_name[n]["manager"] is None for n in out["pipelines"] if n not in ("Product", "Engineering"))


def test_a_second_run_changes_nothing(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    before = snapshot(c)
    again = S.apply_factory_setup(c, ANA)
    assert again["changes"] == []
    assert snapshot(c) == before


def test_existing_dev_and_product_types_are_renamed_not_duplicated(tmp_path):
    c = fresh(tmp_path)
    before = {r["id"]: r for r in rows(c, "SELECT id, name, role FROM task_types WHERE role IS NOT NULL")}
    steps = {
        r["id"]: r["name"]
        for r in rows(c, "SELECT id, name FROM task_steps WHERE type_id IN ('engineering','product-roadmap')")
    }
    out = S.apply_factory_setup(c, ANA)
    assert out["pipelines"]["Engineering"] == "engineering" and out["pipelines"]["Product"] == "product-roadmap"
    assert {r["id"]: r["name"] for r in rows(c, "SELECT id, name FROM task_types WHERE role IS NOT NULL")} == {
        "engineering": "Engineering",
        "product-roadmap": "Product",
    }
    assert before["engineering"]["role"] == "engineering"
    after = {r["id"] for r in rows(c, "SELECT id FROM task_steps WHERE type_id IN ('engineering','product-roadmap')")}
    # Stages are renamed in place, never recreated; only the retired Approved and Ready to ship stages go.
    assert {i for i, name in steps.items() if name not in ("Approved", "Ready to ship")} <= after
    assert len(rows(c, "SELECT id FROM task_types WHERE name='Engineering'")) == 1


def test_a_dev_tickets_type_tagged_engineering_at_boot_is_renamed_and_a_rerun_changes_nothing(tmp_path):
    """A company's engineering type starts life as "Dev tickets": boot tags it with the role and keeps its name, so the
    phone shows "Dev tickets" until setup runs. Setup renames it, and a second run changes nothing."""
    c = fresh(tmp_path)
    c.execute("UPDATE task_types SET name = 'Dev tickets' WHERE id = 'engineering'")
    out = S.apply_factory_setup(c, ANA)
    assert "renamed Dev tickets to Engineering" in out["changes"]
    assert _type(c, "Engineering") == "engineering"
    assert rows(c, "SELECT id FROM task_types WHERE name='Dev tickets'") == []
    assert S.apply_factory_setup(c, ANA)["changes"] == []


def test_a_manager_bot_with_a_matching_name_is_set_and_no_other(tmp_path):
    c = fresh(tmp_path)
    c.execute(
        "INSERT INTO bots (id, slug, display_name, state) VALUES ('b1', 'sales-manager', 'Sales Manager', 'active')"
    )
    S.apply_factory_setup(c, ANA)
    managers = {r["name"]: r["manager"] for r in rows(c, "SELECT name, manager FROM task_types")}
    assert managers["Sales"] == "sales-manager" and managers["Marketing"] is None


def test_replace_moves_an_open_card_on_a_removed_stage_to_the_first_stage_and_keeps_it_open(tmp_path):
    c = fresh(tmp_path)
    old = H.type_create(
        c,
        ANA,
        "Sales",
        [
            {"name": "Prospect", "status": "open"},
            {"name": "Signed", "status": "done"},
            {"name": "Dropped", "status": "closed"},
        ],
        mover=True,
    )
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=old["id"], step="Prospect", lint=False)
    S.apply_factory_setup(c, ANA)
    assert rows(c, "SELECT status FROM tasks WHERE id=?", task["id"])[0]["status"] == "open"
    out = S.apply_factory_setup(c, ANA, replace=True)
    assert "moved 1 cards off removed stages of Sales" in out["changes"]
    now = rows(c, "SELECT k.status, s.name FROM tasks k JOIN task_steps s ON s.id = k.step_id WHERE k.id=?", task["id"])
    assert now == [{"status": "open", "name": "Lead"}]


def test_replace_on_a_booted_hub_makes_every_declared_link_and_a_second_run_changes_nothing(tmp_path):
    c = fresh(tmp_path)
    # A booted hub's Product and Engineering name managers that do not exist yet; their setup-named bots resolve them.
    bot(c, "product-pipeline-manager", "Product Pipeline Manager")
    bot(c, "engineering-pipeline-manager", "Engineering Pipeline Manager")
    # Only Product and Engineering have a bot; the other four preset pipelines have no manager to name.
    assert S.apply_factory_setup(c, ANA)["unresolved"] == UNMANAGED
    out = S.apply_factory_setup(c, ANA, replace=True)
    assert out["unresolved"] == UNMANAGED
    ids = {r["name"]: r["id"] for r in rows(c, "SELECT id, name FROM task_types")}
    for link in S.PRESETS["tico_default"]["links"]:
        stage = step_id(c, ids[link["from"]], link["stage"])
        assert L.link_for_stage(c, stage)["to_pipeline_id"] == ids[link["to"]]
    before = snapshot(c)
    again = S.apply_factory_setup(c, ANA, replace=True)
    assert again["changes"] == [] and again["unresolved"] == UNMANAGED
    assert snapshot(c) == before


def test_the_setup_and_requirements_routes(tmp_path):
    from fastapi.testclient import TestClient

    from backend.app import create_app
    from backend.auth import Identity
    from backend.config import Settings

    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            registry_dir=tmp_path,
            test_identities={
                "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
                "ben-test": Identity("human:ben", "human", "ben@acme.example"),
            },
        )
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(c, {}, {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]})

        calls = []

        def post(token, path, body=None):
            calls.append(path)
            headers = {"Authorization": "Bearer " + token, "Idempotency-Key": f"k{len(calls)}"}
            return client.post(path, json=body or {}, headers=headers)

        assert post("ben-test", "/api/v2/pipelines/setup").status_code == 403
        done = post("ana-test", "/api/v2/pipelines/setup")
        assert done.status_code == 200, done.text
        assert "Sales" in done.json()["pipelines"]
        assert post("ana-test", "/api/v2/pipelines/setup").json()["changes"] == []
        links = client.get("/api/v2/pipeline-links", headers={"Authorization": "Bearer ana-test"}).json()["links"]
        assert len(links) == 1  # the Product to Engineering spawn only
        retired = post("ana-test", f"/api/v2/pipeline-links/{links[0]['id']}/retire")
        assert retired.status_code == 200, retired.text
        with app.state.store.transaction() as c:
            task = H.task_create(
                c, "human:ana", "Acme deal", "", "human:ana", type=done.json()["pipelines"]["Sales"], lint=False
            )
        shown = client.get(f"/api/v2/tasks/{task['id']}/requirements", headers={"Authorization": "Bearer ana-test"})
        assert shown.status_code == 200 and shown.json() == {"requirements": []}


def test_task_types_say_each_steps_warn_time_and_who_may_edit_rules(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import access
    from backend.app import create_app
    from backend.auth import Identity
    from backend.config import Settings

    app = create_app(
        Settings(
            db_path=tmp_path / "hub.db",
            registry_dir=tmp_path,
            test_identities={
                "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
                "ben-test": Identity("human:ben", "human", "ben@acme.example"),
                "cara-test": Identity("human:cara", "human", "cara@acme.example"),
            },
        )
    )
    # Ben is an Admin here; Ana owns the pipeline; Cara is neither.
    monkeypatch.setattr(access, "owner_and_admin_emails", lambda conn: ["ben@acme.example"])
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(
                c, {}, {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]}
            )
            board = H.type_create(
                c,
                "human:ana",
                "Content",
                [{"name": "Drafting", "status": "doing"}, {"name": "Review", "status": "review"}],
                mover=True,
            )
            c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", ("human:ana", board["id"]))
            c.execute("UPDATE task_steps SET limit_minutes=2880 WHERE type_id=? AND name='Drafting'", (board["id"],))
            task = H.task_create(
                c, "human:ana", "Launch post", "", "human:ana", type=board["id"], step="Drafting", lint=False
            )

        def listed(token):
            r = client.get("/api/v2/task-types", headers={"Authorization": "Bearer " + token})
            assert r.status_code == 200, r.text
            return {t["name"]: t for t in r.json()["types"]}

        types = listed("cara-test")
        steps = {s["name"]: s for s in types["Content"]["steps"]}
        assert steps["Drafting"]["limit_minutes"] == 2880
        assert steps["Review"]["limit_minutes"] is None
        assert types["Content"]["can_edit_rules"] is False
        assert listed("ana-test")["Content"]["can_edit_rules"] is True
        assert listed("ben-test")["Content"]["can_edit_rules"] is True

        shown = client.get(f"/api/v2/tasks/{task['id']}", headers={"Authorization": "Bearer ana-test"})
        assert shown.status_code == 200, shown.text
        view = shown.json().get("task", shown.json())
        assert view["step"]["name"] == "Drafting" and view["step"]["limit_minutes"] == 2880
        assert "check_pool" not in view["step"] and "flow" not in view["step"]


def test_two_replace_runs_in_a_row_change_nothing_the_second_time(tmp_path):
    """Replace is idempotent: the stages a pipeline is created with are the ones a replace makes, so the replace that
    follows the one that made the pipelines keeps every stage id, moves no card and writes no event."""
    c = fresh(tmp_path)
    bot(c, "product-pipeline-manager", "Product Pipeline Manager")
    bot(c, "engineering-pipeline-manager", "Engineering Pipeline Manager")
    S.apply_factory_setup(c, ANA, "tico_default", replace=True)
    before = snapshot(c)
    events = rows(c, "SELECT COUNT(*) AS n FROM history_events")[0]["n"]
    cards = rows(c, "SELECT id, step_id, status FROM tasks ORDER BY id")
    again = S.apply_factory_setup(c, ANA, "tico_default", replace=True)
    assert again["changes"] == [] and again["unresolved"] == UNMANAGED
    assert snapshot(c) == before
    assert rows(c, "SELECT COUNT(*) AS n FROM history_events")[0]["n"] == events
    assert rows(c, "SELECT id, step_id, status FROM tasks ORDER BY id") == cards


def test_replace_keeps_cards_on_kept_stages_and_moves_only_cards_on_removed_stages(tmp_path):
    """A replace rewrites no finished history: a card on a stage the preset keeps stays put with its status. On a removed
    stage an open card goes to the first stage, a done or closed one goes to Closed with its status kept, and each move
    is a step event."""
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA, "tico_default")
    won = H.task_create(c, ANA, "Acme deal", "", ANA, type=_type(c, "Sales"), step="Won", lint=False)["id"]
    working = H.task_create(c, ANA, "Spring push", "", ANA, type=_type(c, "Marketing"), step="Working", lint=False)[
        "id"
    ]
    dropped = H.task_create(c, ANA, "Old list", "", ANA, type=_type(c, "Marketing"), step="Dropped", lint=False)["id"]
    from backend import pipeline_engine

    assert pipeline_engine.ensure_visit(c, working) is not None  # its Late clock runs on Working
    c.commit()
    before = {t: rows(c, "SELECT step_id, status FROM tasks WHERE id=?", t)[0] for t in (won, working, dropped)}
    S.apply_factory_setup(c, ANA, "tico_default", replace=True)
    # The visit follows the card: it is open at Targets now, and no open visit sits on a stage the replace removed.
    targets = rows(c, "SELECT step_id FROM tasks WHERE id=?", working)[0]["step_id"]
    assert rows(c, "SELECT pipeline_stage_id AS s FROM stage_visits WHERE task_id=? AND left_at IS NULL", working) == [
        {"s": targets}
    ]
    assert (
        rows(
            c,
            "SELECT v.id FROM stage_visits v LEFT JOIN task_steps s ON s.id = v.pipeline_stage_id "
            "WHERE v.left_at IS NULL AND s.id IS NULL",
        )
        == []
    )

    def now(task):
        return rows(c, "SELECT k.status, s.name FROM tasks k JOIN task_steps s ON s.id=k.step_id WHERE k.id=?", task)[0]

    assert now(won) == {"status": before[won]["status"], "name": "Won"}
    assert now(working)["name"] == "Targets" and now(working)["status"] == before[working]["status"] == "doing"
    assert now(dropped) == {"status": "closed", "name": "Closed"}
    moved = rows(
        c,
        "SELECT subject_id FROM history_events WHERE subject_type='task' AND kind='step' AND subject_id IN (?,?)",
        working,
        dropped,
    )
    assert {r["subject_id"] for r in moved} == {working, dropped}


def _type(c, name):
    return rows(c, "SELECT id FROM task_types WHERE name=? COLLATE NOCASE", name)[0]["id"]


def test_visitor_suggestions_land_on_the_product_first_stage_before_and_after_setup(tmp_path):
    """Public suggestions are filed at the Product roadmap's first stage: Suggested until the setup renames it to Ideas."""
    from backend import public_roadmaps

    c = fresh(tmp_path)
    assert public_roadmaps._ideas_step(c) == "Suggested"
    S.apply_factory_setup(c, ANA)
    assert public_roadmaps._ideas_step(c) == "Ideas"


def test_replace_relocating_a_card_off_a_spawn_stage_files_no_linked_work(tmp_path):
    """Replace moves cards off removed stages without running the step hook: a removed stage with a spawn link files
    nothing when its card is relocated, and the card's visit still follows it."""
    from backend import pipeline_engine

    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA, "tico_default")
    working = step_id(c, _type(c, "Marketing"), "Working")
    L.create_link(c, ANA, working, _type(c, "Engineering"), "spawn")
    card = H.task_create(c, ANA, "Spring push", "", ANA, type=_type(c, "Marketing"), step="Working", lint=False)["id"]
    assert pipeline_engine.ensure_visit(c, card) is not None
    c.commit()
    engineering = rows(c, "SELECT COUNT(*) AS n FROM tasks WHERE type_id=?", _type(c, "Engineering"))[0]["n"]

    S.apply_factory_setup(c, ANA, "tico_default", replace=True)

    assert rows(c, "SELECT COUNT(*) AS n FROM tasks WHERE type_id=?", _type(c, "Engineering"))[0]["n"] == engineering
    assert rows(c, "SELECT COUNT(*) AS n FROM pipeline_handoffs WHERE source_task_id=?", card)[0]["n"] == 0
    now = rows(c, "SELECT step_id FROM tasks WHERE id=?", card)[0]["step_id"]
    assert rows(c, "SELECT pipeline_stage_id AS s FROM stage_visits WHERE task_id=? AND left_at IS NULL", card) == [
        {"s": now}
    ]


def test_replace_retires_the_revenue_links_an_older_setup_made_and_keeps_their_handoffs(tmp_path):
    """Fix V-10: an older setup linked Marketing to Sales and Sales to Success. Replacing with the current preset retires
    both links (their handoff rows stay on record) and keeps the Product to Engineering link live."""
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA, "tico_default")
    marketing_done = step_id(c, _type(c, "Marketing"), "Done")
    sales_won = step_id(c, _type(c, "Sales"), "Won")
    to_sales = L.create_link(c, ANA, marketing_done, _type(c, "Sales"), "move")
    to_success = L.create_link(c, ANA, sales_won, _type(c, "Success"), "move")
    card = H.task_create(c, ANA, "Acme lead", "", ANA, type=_type(c, "Sales"), step="Won", lint=False)["id"]
    L.record_handoff(c, to_success["id"], card, card)
    L.record_handoff(c, to_sales["id"], card, card)
    c.commit()

    S.apply_factory_setup(c, ANA, "tico_default", replace=True)

    retired = {r["id"] for r in rows(c, "SELECT id FROM pipeline_links WHERE retired_at IS NOT NULL")}
    assert {to_sales["id"], to_success["id"]} <= retired
    assert len(L.handoffs(c, to_sales["id"])) == 1 and len(L.handoffs(c, to_success["id"])) == 1
    review = step_id(c, _type(c, "Product"), "Review")
    assert L.link_for_stage(c, review)["to_pipeline_id"] == _type(c, "Engineering")
    assert L.link_for_stage(c, marketing_done) is None and L.link_for_stage(c, sales_won) is None


def _engineering_shipped(c):
    """The preset's "Engineering shipped" requirement row (its needs, decoded) on Product's In development stage."""
    row = c.execute(
        "SELECT r.id, r.needs FROM stage_requirements r JOIN task_steps s ON s.id = r.pipeline_stage_id "
        "JOIN task_types t ON t.id = s.type_id WHERE t.name = 'Product' AND s.name = 'In development' "
        "AND r.name = 'Engineering shipped' AND r.retired_at IS NULL"
    ).fetchone()
    assert row is not None
    return row[0], json.loads(row[1])


def test_setup_names_engineering_as_the_pipeline_engineering_shipped_waits_on(tmp_path):
    """D64, Codex blocker 5: the subtasks run names the pipeline it counts, and a second setup run keeps that name and
    changes nothing."""
    c = fresh(tmp_path)
    out = S.apply_factory_setup(c, ANA)
    _, needs = _engineering_shipped(c)
    assert needs["source"] == "subtasks" and needs["pipeline"] == out["pipelines"]["Engineering"]
    before = snapshot(c)
    again = S.apply_factory_setup(c, ANA)
    assert again["changes"] == [] and snapshot(c) == before
    assert _engineering_shipped(c)[1]["pipeline"] == out["pipelines"]["Engineering"]


def test_a_rerun_names_the_pipeline_on_a_legacy_engineering_shipped_row_and_the_next_run_changes_nothing(tmp_path):
    """A requirement written before the name existed has no pipeline in its needs. Re-running setup names Engineering on it,
    the change is reported, and the next run changes nothing."""
    c = fresh(tmp_path)
    out = S.apply_factory_setup(c, ANA)
    req_id, needs = _engineering_shipped(c)
    needs.pop("pipeline")
    c.execute("UPDATE stage_requirements SET needs = ? WHERE id = ?", (json.dumps(needs), req_id))
    c.commit()
    again = S.apply_factory_setup(c, ANA)
    assert again["changes"] == [f"requirement Engineering shipped on In development now names its pipeline"]
    assert _engineering_shipped(c)[1]["pipeline"] == out["pipelines"]["Engineering"]
    assert S.apply_factory_setup(c, ANA)["changes"] == []


def test_product_starts_on_ice_with_the_default_caps_and_a_second_run_changes_nothing(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    product = _type(c, "Product")
    steps = rows(c, "SELECT name, card_cap FROM task_steps WHERE type_id=? ORDER BY position, id", product)
    assert [s["name"] for s in steps][:3] == ["On ice", "Ideas", "Specifying"]
    caps = {s["name"]: s["card_cap"] for s in steps}
    assert caps["On ice"] == 0 and caps["Specifying"] == 3 and caps["In development"] == 3
    assert caps["Ideas"] is None and caps["Review"] is None
    before = snapshot(c)
    S.apply_factory_setup(c, ANA)
    assert snapshot(c) == before


def test_adopting_a_product_adds_on_ice_and_keeps_the_caps_a_person_set(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    product = _type(c, "Product")
    R.set_stage(c, ANA, step_id(c, product, "Specifying"), {"card_cap": 5})
    R.set_stage(c, ANA, step_id(c, product, "In development"), {"card_cap": None})  # a person's No cap
    kept = rows(
        c, "SELECT id, name, status FROM task_steps WHERE type_id=? AND name<>'On ice' ORDER BY position", product
    )
    H.type_update(c, ANA, product, steps=kept, mover=True)  # a Product from before On ice existed
    S.apply_factory_setup(c, ANA)
    caps = {s["name"]: s["card_cap"] for s in rows(c, "SELECT name, card_cap FROM task_steps WHERE type_id=?", product)}
    assert caps["On ice"] == 0
    assert caps["Specifying"] == 5 and caps["In development"] is None


def test_new_work_enters_product_at_ideas_and_visitor_suggestions_do_too(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    product = _type(c, "Product")
    assert E.first_stage(c, product)["name"] == "Ideas"
    assert public_roadmaps._ideas_step(c) == "Ideas"


def test_the_pipeline_manager_name_is_matched_first_and_the_older_names_still_match(tmp_path):
    c = fresh(tmp_path)
    bot(c, "sales-manager", "Sales Manager")
    bot(c, "sales-pipeline-manager", "Sales Pipeline Manager")
    bot(c, "engineering-manager", "Engineering Manager")
    bot(c, "marketing-pipeline-manager", "Marketing Pipeline Manager")
    bot(c, "success-factory-manager", "Success Factory Manager")
    S.apply_factory_setup(c, ANA)
    assert manager_of(c, "Sales") == "sales-pipeline-manager"
    assert manager_of(c, "Engineering") == "engineering-manager"
    assert manager_of(c, "Marketing") == "marketing-pipeline-manager"
    assert manager_of(c, "Success") == "success-factory-manager"


def test_an_archived_manager_is_replaced_by_the_matching_bot(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    bot(c, "sales-manager", "Sales Manager", state="archived")
    set_manager(c, "Sales", "sales-manager")
    bot(c, "sales-pipeline-manager", "Sales Pipeline Manager")
    S.apply_factory_setup(c, ANA)
    assert manager_of(c, "Sales") == "sales-pipeline-manager"


def test_a_paused_or_missing_manager_counts_as_none(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    bot(c, "success-manager", "Success Manager", state="paused")
    set_manager(c, "Success", "success-manager")
    bot(c, "success-factory-manager", "Success Factory Manager")
    set_manager(c, "Legal", "legal-manager")  # no bot of that slug exists
    bot(c, "legal-pipeline-manager", "Legal Pipeline Manager")
    S.apply_factory_setup(c, ANA)
    assert manager_of(c, "Success") == "success-factory-manager"
    assert manager_of(c, "Legal") == "legal-pipeline-manager"


def test_an_active_manager_is_never_replaced(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    bot(c, "sales-lead", "Sales Lead")
    set_manager(c, "Sales", "sales-lead")
    bot(c, "sales-pipeline-manager", "Sales Pipeline Manager")
    S.apply_factory_setup(c, ANA)
    assert manager_of(c, "Sales") == "sales-lead"


def test_a_manager_with_no_active_match_is_reported_as_unresolved_and_setup_still_runs(tmp_path):
    c = fresh(tmp_path)
    S.apply_factory_setup(c, ANA)
    bot(c, "marketing-manager", "Marketing Manager", state="archived")
    set_manager(c, "Marketing", "marketing-manager")
    out = S.apply_factory_setup(c, ANA)
    assert manager_of(c, "Marketing") == "marketing-manager"
    assert [u for u in out["unresolved"] if "Marketing" in u], out["unresolved"]
    again = S.apply_factory_setup(c, ANA, replace=True)  # a manager report never refuses a replace
    assert again["unresolved"] == out["unresolved"]


def test_ready_to_ship_is_retired_into_done_and_its_cards_move_there(tmp_path):
    c = fresh(tmp_path)
    product = _type(c, "Product roadmap")
    card = H.task_create(c, ANA, "Export the roadmap", "", ANA, type=product, step="Ready to ship", lint=False)["id"]
    S.apply_factory_setup(c, ANA)
    assert rows(c, "SELECT id FROM task_steps WHERE type_id=? AND name='Ready to ship'", product) == []
    assert rows(
        c, "SELECT s.name AS step, k.status FROM tasks k JOIN task_steps s ON s.id=k.step_id WHERE k.id=?", card
    ) == [{"step": "Done", "status": "done"}]


def test_every_stage_public_name_follows_its_stage_name_after_setup(tmp_path):
    c = fresh(tmp_path)
    # The live Product stages still carry their old public names (Exploring, Planned in production).
    c.execute("UPDATE task_steps SET public_name = 'Old ' || name WHERE type_id = 'product-roadmap'")
    S.apply_factory_setup(c, ANA)
    stale = rows(
        c,
        "SELECT t.name AS type, s.name, s.public_name FROM task_steps s JOIN task_types t ON t.id = s.type_id "
        "WHERE t.name IN ('Product', 'Engineering', 'Marketing', 'Sales', 'Success', 'Legal') AND s.public_name <> s.name",
    )
    assert stale == []
    assert rows(c, "SELECT public_name FROM task_steps WHERE type_id=? AND name='Specifying'", _type(c, "Product")) == [
        {"public_name": "Specifying"}
    ]


def test_a_card_cannot_leave_specifying_without_a_spec_page(tmp_path):
    c = fresh(tmp_path)
    product = S.apply_factory_setup(c, ANA)["pipelines"]["Product"]
    req = rows(
        c,
        "SELECT r.kind, r.needs, r.reviewed_by, r.rubric FROM stage_requirements r "
        "JOIN task_steps s ON s.id = r.pipeline_stage_id WHERE s.type_id = ? AND s.name = 'Specifying' "
        "AND r.name = 'Spec page' AND r.retired_at IS NULL",
        product,
    )
    assert [(r["kind"], r["reviewed_by"]) for r in req] == [("attachment", "bot_then_person")]
    assert json.loads(req[0]["needs"])["formats"] == ["page"]
    assert req[0]["rubric"]
    card = H.task_create(c, ANA, "Export spec", "", ANA, type=product, step="Specifying", lint=False)["id"]
    with pytest.raises(H.Refused) as refused:
        H.task_update(c, ANA, card, step="Review", mover=True)
    assert refused.value.rule == "requirement"
    assert rows(c, "SELECT s.name FROM tasks k JOIN task_steps s ON s.id = k.step_id WHERE k.id = ?", card) == [
        {"name": "Specifying"}
    ]


def test_a_run_after_the_name_fixes_changes_nothing(tmp_path):
    c = fresh(tmp_path)
    c.execute("UPDATE task_steps SET public_name = 'Old ' || name WHERE type_id = 'product-roadmap'")
    H.task_create(
        c, ANA, "Export the roadmap", "", ANA, type=_type(c, "Product roadmap"), step="Ready to ship", lint=False
    )
    S.apply_factory_setup(c, ANA)
    bot(c, "sales-manager", "Sales Manager", state="archived")
    set_manager(c, "Sales", "sales-manager")
    bot(c, "sales-pipeline-manager", "Sales Pipeline Manager")
    first = S.apply_factory_setup(c, ANA)
    before = snapshot(c)
    again = S.apply_factory_setup(c, ANA)
    assert again["changes"] == [] and again["unresolved"] == first["unresolved"]
    assert snapshot(c) == before


def test_retiring_ready_to_ship_leaves_an_open_subtask_open(tmp_path):
    """Codex GB2 finding 1: retiring Ready to ship moves its cards into Done, and a Done move closes open descendants. A card
    with an unfinished subtask stays on Ready to ship, and the subtask stays open."""
    c = fresh(tmp_path)
    product = _type(c, "Product roadmap")
    card = H.task_create(c, ANA, "Export the roadmap", "", ANA, type=product, step="Ready to ship", lint=False)["id"]
    child = H.task_create(c, ANA, "Write the export", "", ANA, parent_id=card, lint=False)["id"]
    out = S.apply_factory_setup(c, ANA)
    assert rows(c, "SELECT status FROM tasks WHERE id=?", child) == [{"status": "open"}]
    assert any("Export the roadmap" in u for u in out["unresolved"]), out["unresolved"]
    assert rows(c, "SELECT s.name AS step FROM tasks k JOIN task_steps s ON s.id=k.step_id WHERE k.id=?", card) == [
        {"step": "Ready to ship"}
    ]
    assert rows(c, "SELECT id FROM task_steps WHERE type_id=? AND name='Ready to ship'", product) != []


def test_with_no_matching_bots_every_unmanaged_pipeline_is_listed_as_unresolved(tmp_path):
    """Codex GB2 finding 3: a pipeline with no manager (NULL) is reported too, not only one whose manager was set and
    stopped working. With no bots at all, all six preset pipelines are listed."""
    c = fresh(tmp_path)
    out = S.apply_factory_setup(c, ANA)
    for name in ("Product", "Engineering", "Marketing", "Sales", "Success", "Legal"):
        assert any(u.startswith(f"{name} has no active manager") for u in out["unresolved"]), (name, out["unresolved"])
    assert manager_of(c, "Marketing") is None
    again = S.apply_factory_setup(c, ANA)
    assert again["unresolved"] == out["unresolved"]


def test_retiring_approved_moves_a_card_with_an_open_subtask_into_in_development(tmp_path):
    """Codex GB2 finding 3: only a retirement into a done or closed stage closes descendants. Approved moves into In
    development, so its card moves and its open subtask stays open."""
    c = fresh(tmp_path)
    product = _type(c, "Product roadmap")
    card = H.task_create(c, ANA, "Approved export", "", ANA, type=product, step="Approved", lint=False)["id"]
    child = H.task_create(c, ANA, "Write the export", "", ANA, parent_id=card, lint=False)["id"]
    S.apply_factory_setup(c, ANA)
    assert rows(c, "SELECT s.name AS step FROM tasks k JOIN task_steps s ON s.id=k.step_id WHERE k.id=?", card) == [
        {"step": "In development"}
    ]
    assert rows(c, "SELECT status FROM tasks WHERE id=?", child) == [{"status": "open"}]


def test_a_card_held_back_from_done_is_named_only_where_the_caller_can_read_it(tmp_path):
    """Codex GB2 finding 2: setup's report names a held card only to a caller who can read it. A private card the setup
    actor cannot read is reported generically, with no title."""
    c = fresh(tmp_path)
    product = _type(c, "Product roadmap")
    public = H.task_create(c, ANA, "Public export", "", ANA, type=product, step="Ready to ship", lint=False)["id"]
    H.task_create(c, ANA, "Finish the export", "", ANA, parent_id=public, lint=False)
    secret = H.task_create(
        c,
        "human:ben",
        "Confidential acquisition plan",
        "",
        "human:ben",
        type=product,
        step="Ready to ship",
        lint=False,
        private=True,
    )["id"]
    child = H.task_create(c, "human:ben", "Draft the plan", "", "human:ben", parent_id=secret, lint=False)["id"]
    assert rows(c, "SELECT private FROM tasks WHERE id=?", secret) == [{"private": 1}]
    out = S.apply_factory_setup(c, ANA)
    text = " | ".join(out["unresolved"])
    assert "Confidential" not in text and "Draft the plan" not in text, text
    assert "Public export" in text
    assert any("1 card you can't see is still in Ready to ship" in u for u in out["unresolved"]), out["unresolved"]
    assert rows(c, "SELECT status FROM tasks WHERE id=?", child) == [{"status": "open"}]
