"""The Tico Team pipeline setup (Track A): it makes the factories, pipelines, stages, links and goals, adopts what exists,
changes nothing on a second run, sets managers only where a matching bot exists, and wipes cards only with replace."""

from backend import hubdb as H
from backend import pipeline_links as L
from backend import pipeline_setup as S
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
        "steps": rows(c, "SELECT id, type_id, name, status, position FROM task_steps ORDER BY id"),
        "factories": rows(c, "SELECT id, name, goal FROM factories ORDER BY id"),
        "links": rows(c, "SELECT id, from_pipeline_stage_id, to_pipeline_id, mode FROM pipeline_links ORDER BY id"),
    }


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
    # Stages are renamed in place, never recreated; only the retired Approved stage goes.
    assert {i for i, name in steps.items() if name != "Approved"} <= after
    assert len(rows(c, "SELECT id FROM task_types WHERE name='Engineering'")) == 1


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
    assert S.apply_factory_setup(c, ANA)["unresolved"] == []
    out = S.apply_factory_setup(c, ANA, replace=True)
    assert out["unresolved"] == []
    ids = {r["name"]: r["id"] for r in rows(c, "SELECT id, name FROM task_types")}
    for link in S.PRESETS["tico_default"]["links"]:
        stage = step_id(c, ids[link["from"]], link["stage"])
        assert L.link_for_stage(c, stage)["to_pipeline_id"] == ids[link["to"]]
    before = snapshot(c)
    again = S.apply_factory_setup(c, ANA, replace=True)
    assert again["changes"] == [] and again["unresolved"] == []
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
    S.apply_factory_setup(c, ANA, "tico_default", replace=True)
    before = snapshot(c)
    events = rows(c, "SELECT COUNT(*) AS n FROM history_events")[0]["n"]
    cards = rows(c, "SELECT id, step_id, status FROM tasks ORDER BY id")
    again = S.apply_factory_setup(c, ANA, "tico_default", replace=True)
    assert again["changes"] == [] and again["unresolved"] == []
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
