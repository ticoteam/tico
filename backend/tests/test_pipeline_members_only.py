"""A members-only pipeline on every read surface (Track N): its name and stages from the task types, its tickets and their
requirements and attachments, and its links. A non-member gets nothing about it, a member gets it all, and a member whose
membership is revoked gets nothing again. A list of links is paged, a page at a time."""

import uuid

from backend import hubdb as H
from backend import pipeline_links as L
from backend import pipeline_requirements as R
from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
from backend.tests.test_pipeline_foundation import T

STEPS = [{"name": "Doing", "status": "doing"}, {"name": "Done", "status": "done"}]


def make_pipeline(api, name, *, private=False):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, name, STEPS)
        c.execute(
            "UPDATE task_types SET pipeline_owner_id=?, pipeline_visibility=? WHERE id=?",
            ("human:ana", "members" if private else "company", typ["id"]),
        )
    return typ["id"], {s["name"]: s["id"] for s in typ["steps"]}


def surfaces(api, token, ids):
    """The status each read surface gives this caller for one ticket, its requirement and their pipeline."""
    paths = {
        "task type": f"task-types/{ids['type']}",
        "ticket": f"tasks/{ids['task']}",
        "ticket requirements": f"tasks/{ids['task']}/requirements",
        "stage requirements": f"pipeline-stages/{ids['stage']}/requirements",
        "requirement": f"requirements/{ids['req']}",
        "attachments": f"tasks/{ids['task']}/requirements/{ids['req']}/attachments",
    }
    return {name: api.get("/api/v2/" + path, headers=headers(token)).status_code for name, path in paths.items()}


def test_a_members_only_pipeline_closes_its_tickets_and_requirements_to_a_non_member_and_a_revoked_member(api):
    tag = uuid.uuid4().hex[:8]
    typ, steps = make_pipeline(api, "Counsel" + tag)  # company-wide, as it is before it goes private
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Ship " + tag, "", "human:ana", type=typ, step="Doing", lint=False)
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (task["id"],))
        req = R.add(
            c,
            "human:ana",
            steps["Doing"],
            {"name": "Terms reviewed", "kind": "approval", "reviewed_by": "person", "reviewer_id": "human:ana"},
        )
    ids = {"type": typ, "task": task["id"], "stage": steps["Doing"], "req": req["id"]}
    assert set(surfaces(api, "cara-test", ids).values()) == {200}

    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET pipeline_visibility='members' WHERE id=?", (typ,))
    closed = surfaces(api, "cara-test", ids)
    assert set(closed.values()) == {404}, closed
    assert set(surfaces(api, "ana-test", ids).values()) == {200}

    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pipeline_members (pipeline_id, actor_id, created_at) VALUES (?,?,?)", (typ, "human:cara", T)
        )
    assert set(surfaces(api, "cara-test", ids).values()) == {200}

    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM pipeline_members WHERE pipeline_id=? AND actor_id=?", (typ, "human:cara"))
    assert set(surfaces(api, "cara-test", ids).values()) == {404}


def test_task_types_leave_out_a_members_only_pipeline_for_someone_outside_it(api):
    tag = uuid.uuid4().hex[:8]
    legal, _ = make_pipeline(api, "Legal" + tag, private=True)
    public, _ = make_pipeline(api, "Sales" + tag)

    listed = {t["id"] for t in get(api, "task-types", "cara-test")["types"]}
    assert public in listed and legal not in listed
    assert legal in {t["id"] for t in get(api, "task-types", "ana-test")["types"]}
    get(api, f"task-types/{legal}", "cara-test", expected=404)
    get(api, "task-types/Legal" + tag, "cara-test", expected=404)  # named by name, the same answer
    assert get(api, f"task-types/{legal}", "ana-test")["type"]["id"] == legal


def test_requirements_of_a_members_only_pipeline_are_read_by_its_members_only(api):
    tag = uuid.uuid4().hex[:8]
    _, steps = make_pipeline(api, "Counsel" + tag, private=True)
    with api.app.state.store.transaction() as c:
        req = R.add(
            c,
            "human:ana",
            steps["Doing"],
            {"name": "Terms reviewed", "kind": "approval", "reviewed_by": "person", "reviewer_id": "human:ana"},
        )

    get(api, f"pipeline-stages/{steps['Doing']}/requirements", "cara-test", expected=404)
    get(api, f"requirements/{req['id']}", "cara-test", expected=404)
    names = [r["name"] for r in get(api, f"pipeline-stages/{steps['Doing']}/requirements", "ana-test")["requirements"]]
    assert names == ["Terms reviewed"]
    assert get(api, f"requirements/{req['id']}", "ana-test")["name"] == "Terms reviewed"


def test_links_leave_out_a_members_only_pipeline_and_list_a_page_at_a_time(api):
    tag = uuid.uuid4().hex[:8]
    legal, _ = make_pipeline(api, "Legal" + tag, private=True)
    target, _ = make_pipeline(api, "Support" + tag)
    lanes = [make_pipeline(api, f"Lane{n}{tag}")[1]["Doing"] for n in range(3)]
    intake = make_pipeline(api, "Intake" + tag)[1]["Doing"]
    with api.app.state.store.transaction() as c:
        made = [L.create_link(c, "human:ana", stage, target)["id"] for stage in lanes]
        hidden = L.create_link(c, "human:ana", intake, legal)["id"]

    def every_page(token, limit):
        seen, cursor = [], None
        for _ in range(100):
            params = {"limit": limit, **({"after": cursor} if cursor else {})}
            page = api.get("/api/v2/pipeline-links", params=params, headers=headers(token))
            assert page.status_code == 200, page.text
            body = page.json()
            assert len(body["links"]) <= limit
            seen += [link["id"] for link in body["links"]]
            cursor = body["next"]
            if not cursor:
                return seen
        raise AssertionError("the links never ended")

    outsider = every_page("cara-test", 2)
    assert len(outsider) == len(set(outsider))
    assert set(made) <= set(outsider) and hidden not in outsider
    insider = every_page("ana-test", 2)
    assert set(made) <= set(insider) and hidden in insider
    assert get(api, f"pipeline-links?pipeline={legal}", "cara-test")["links"] == []
    assert [link["id"] for link in get(api, f"pipeline-links?pipeline={legal}", "ana-test")["links"]] == [hidden]
