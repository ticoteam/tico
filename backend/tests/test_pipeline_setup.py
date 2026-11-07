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
    ] == ["Lead", "Talking", "Proposal", "Closing", "Won", "Lost"]
    won = step_id(c, by_name["Sales"]["id"], "Won")
    assert L.link_for_stage(c, won)["to_pipeline_id"] == by_name["Success"]["id"]
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
    steps = rows(c, "SELECT id FROM task_steps WHERE type_id IN ('engineering','product-roadmap') ORDER BY id")
    out = S.apply_factory_setup(c, ANA)
    assert out["pipelines"]["Engineering"] == "engineering" and out["pipelines"]["Product"] == "product-roadmap"
    assert {r["id"]: r["name"] for r in rows(c, "SELECT id, name FROM task_types WHERE role IS NOT NULL")} == {
        "engineering": "Engineering",
        "product-roadmap": "Product",
    }
    assert before["engineering"]["role"] == "engineering"
    assert rows(c, "SELECT id FROM task_steps WHERE type_id IN ('engineering','product-roadmap') ORDER BY id") == steps
    assert len(rows(c, "SELECT id FROM task_types WHERE name='Engineering'")) == 1


def test_a_manager_bot_with_a_matching_name_is_set_and_no_other(tmp_path):
    c = fresh(tmp_path)
    c.execute(
        "INSERT INTO bots (id, slug, display_name, state) VALUES ('b1', 'sales-manager', 'Sales Manager', 'active')"
    )
    S.apply_factory_setup(c, ANA)
    managers = {r["name"]: r["manager"] for r in rows(c, "SELECT name, manager FROM task_types")}
    assert managers["Sales"] == "sales-manager" and managers["Marketing"] is None


def test_replace_clears_open_cards_only_when_asked(tmp_path):
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
    assert "cleared Sales" in out["changes"]
    assert rows(c, "SELECT status FROM tasks WHERE id=?", task["id"])[0]["status"] == "closed"


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
        assert len(links) == 2
        retired = post("ana-test", f"/api/v2/pipeline-links/{links[0]['id']}/retire")
        assert retired.status_code == 200, retired.text
        with app.state.store.transaction() as c:
            task = H.task_create(
                c, "human:ana", "Acme deal", "", "human:ana", type=done.json()["pipelines"]["Sales"], lint=False
            )
        shown = client.get(f"/api/v2/tasks/{task['id']}/requirements", headers={"Authorization": "Bearer ana-test"})
        assert shown.status_code == 200 and shown.json() == {"requirements": []}
