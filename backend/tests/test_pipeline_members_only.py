"""A members-only pipeline on every read surface (Track N): its name and stages from the task types, its tickets and their
requirements and attachments, and its links. A non-member gets nothing about it, a member gets it all, and a member whose
membership is revoked gets nothing again. A list of links is paged, a page at a time."""

import json
import uuid

from backend import hubdb as H
from backend import pipeline_links as L
from backend import pipeline_requirements as R
from backend import task_relations as TR
from backend.auth import Identity
from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
from backend.tests.test_pipeline_foundation import T, insert

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


def ticket(api, typ, title, owner, requester="human:ana"):
    """A ticket on a pipeline, public to the company but for the pipeline's own privacy."""
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, requester, title, "", owner, type=typ, step="Doing", lint=False)
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (task["id"],))
    return task["id"]


def test_a_tickets_owner_and_requester_read_it_on_a_members_only_pipeline_they_are_not_on(api):
    tag = uuid.uuid4().hex[:8]
    typ, _ = make_pipeline(api, "Counsel" + tag, private=True)
    with api.app.state.store.transaction() as c:
        insert(c, "bots", id="b-42", slug="counsel-bot", display_name="Counsel Bot", state="active", created_at=T)
    owned_by_bot = ticket(api, typ, "Bot's " + tag, owner="bot:b-42")
    asked_by_cara = ticket(api, typ, "Cara asked " + tag, owner="human:ben", requester="human:cara")
    theirs = ticket(api, typ, "Ben's " + tag, owner="human:ben")

    # A bot reads its own work on a pipeline it is not on (the API takes runner tokens for bots, so call the auth layer).
    with api.app.state.store.read() as c:
        assert api.app.state.auth.task(c, Identity("bot:b-42", "bot", ""), owned_by_bot)["id"] == owned_by_bot
    get(api, f"tasks/{asked_by_cara}", "cara-test")  # so does the person who asked for it
    get(api, f"tasks/{theirs}", "cara-test", expected=404)
    get(api, f"tasks/{theirs}", "ben-test")  # its owner, who is on no pipeline roster
    get(api, f"tasks/{theirs}", "ana-test")  # the pipeline's owner


def test_ticket_lists_search_and_the_pipeline_view_leave_out_a_members_only_pipeline_but_keep_a_readers_own(api):
    tag = uuid.uuid4().hex[:8]
    typ, _ = make_pipeline(api, "Counsel" + tag, private=True)
    theirs = ticket(api, typ, "Ben's " + tag, owner="human:ben")
    mine = ticket(api, typ, "Cara's " + tag, owner="human:cara")
    asked = ticket(api, typ, "Asked " + tag, owner="human:ben", requester="human:cara")

    def listed(token, path):
        return {t["id"] for t in get(api, path, token)["tasks"]}

    everything, readers_own = {theirs, mine, asked}, {mine, asked}
    assert listed("ana-test", f"tasks?type={typ}&limit=500") == everything
    get(api, f"tasks?type={typ}&limit=500", "cara-test", expected=422)  # a hidden type is no type to her
    assert theirs not in listed("cara-test", "tasks?limit=500") and readers_own <= listed(
        "cara-test", "tasks?limit=500"
    )
    assert listed("ana-test", f"tasks?q={tag}&limit=500") == everything
    assert listed("cara-test", f"tasks?q={tag}&limit=500") == readers_own
    get(api, f"pipeline?type={typ}", "cara-test", expected=422)  # a hidden type is no type to her
    assert get(api, f"pipeline?type={typ}", "ana-test")["total"] == 3


def test_the_manager_rail_and_the_review_queues_leave_out_a_members_only_pipeline(api):
    tag = uuid.uuid4().hex[:8]
    typ, steps = make_pipeline(api, "Counsel" + tag)  # company-wide until it goes private
    task = ticket(api, typ, "Review " + tag, owner="human:ben")
    with api.app.state.store.transaction() as c:
        R.add(
            c,
            "human:ana",
            steps["Doing"],
            {"name": "Terms reviewed", "kind": "approval", "reviewed_by": "person", "reviewer_id": "human:cara"},
        )

    def queued(path):
        return {item["task_id"] for item in get(api, path, "cara-test")["items"]}

    assert task in queued("pipelines/review/queue")  # the reviewer sees it while the pipeline is company-wide
    assert task in queued(f"pipelines/{typ}/review/queue")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET pipeline_visibility='members' WHERE id=?", (typ,))
    assert task not in queued("pipelines/review/queue")
    get(api, f"pipelines/{typ}/review/queue", "cara-test", expected=404)
    get(api, f"pipelines/{typ}/stages/{steps['Doing']}/review/queue", "cara-test", expected=404)
    get(api, f"pipelines/{typ}/manager", "cara-test", expected=404)
    get(api, f"pipelines/{typ}/manager", "ana-test")


def test_the_affected_count_of_a_members_only_stage_is_not_found_to_a_non_member(api):
    tag = uuid.uuid4().hex[:8]
    typ, steps = make_pipeline(api, "Legal" + tag, private=True)
    ticket(api, typ, "Open " + tag, owner="human:ben")
    path = f"pipeline-stages/{steps['Doing']}/affected"
    get(api, path, "cara-test", expected=404)  # the same answer as a stage that does not exist
    assert get(api, path, "ana-test")["count"] == 1
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pipeline_members (pipeline_id, actor_id, created_at) VALUES (?,?,?)", (typ, "human:cara", T)
        )
    assert get(api, path, "cara-test")["count"] == 1


def test_flow_stats_and_ticket_filters_name_a_members_only_pipeline_to_no_one_outside_it(api):
    tag = uuid.uuid4().hex[:8]
    typ, _ = make_pipeline(api, "Legal" + tag, private=True)
    with api.app.state.store.transaction() as c:
        H.type_create(c, H.KEEPER, "Hush" + tag, [{"name": "Hush" + tag, "status": "doing"}])
        hush = H.type_get(c, "Hush" + tag)["id"]
        c.execute("UPDATE task_types SET pipeline_visibility='members' WHERE id=?", (hush,))
    ticket(api, typ, "Open " + tag, owner="human:ben")

    def answer(path, token):
        response = api.get("/api/v2/" + path, headers=headers(token))
        return response.status_code, response.text

    # A hidden type is refused as an unknown one is, with the same error (the name it was given is the only difference).
    for path, unknown in (
        (f"tasks/flow?type={typ}", "tasks/flow?type=nope" + tag),
        (f"tasks?type={typ}&limit=5", "tasks?type=nope" + tag),
        (f"tasks?step=Hush{tag}&limit=5", "tasks?step=nope" + tag),
    ):
        hidden, missing = answer(path, "cara-test"), answer(unknown, "cara-test")
        assert hidden[0] == missing[0] == 422, (path, hidden, missing)
        assert hidden[1].replace(typ, "X").replace("Hush" + tag, "X").replace("nope" + tag, "X") == missing[1].replace(
            "nope" + tag, "X"
        ), (path, hidden, missing)
        assert "Legal" not in hidden[1]
    assert answer(f"tasks/flow?type={typ}", "ana-test")[0] == 200
    assert answer(f"tasks?step=Hush{tag}&limit=5", "ana-test")[0] == 200


def test_a_non_member_who_owns_a_members_only_parent_sees_none_of_its_hidden_children_or_relations(api):
    tag = uuid.uuid4().hex[:8]
    typ, _ = make_pipeline(api, "Legal" + tag, private=True)
    parent = ticket(api, typ, "Parent " + tag, owner="human:cara", requester="human:ana")
    hidden_child = ticket(api, typ, "Ben's " + tag, owner="human:ben", requester="human:ana")
    own_child = ticket(api, typ, "Cara's " + tag, owner="human:cara", requester="human:ana")
    with api.app.state.store.transaction() as c:
        TR._insert(c, "human:ana", hidden_child, parent, "parent", T)
        TR._insert(c, "human:ana", own_child, parent, "parent", T)
        TR._insert(c, "human:ana", parent, hidden_child, "blocks", T)

    detail = get(api, f"tasks/{parent}", "cara-test")
    assert detail["task"]["id"] == parent  # the owner reads the parent itself
    assert {child["id"] for child in detail["children"]} == {own_child}
    assert detail["task"]["parts"]["total"] == 1, detail["task"]["parts"]
    related = {ref["id"] for refs in detail["task"]["relations"].values() for ref in refs}
    assert hidden_child not in related
    everyone = get(api, f"tasks/{parent}", "ana-test")
    assert {child["id"] for child in everyone["children"]} == {hidden_child, own_child}


def test_the_change_feed_reads_a_deleted_ticket_while_a_members_only_pipeline_exists(api):
    from backend.task_delete import delete_tasks

    tag = uuid.uuid4().hex[:8]
    public, _ = make_pipeline(api, "Sales" + tag)
    make_pipeline(api, "Legal" + tag, private=True)  # the hidden pipeline the list rule reads the type against
    gone_one = ticket(api, public, "Gone " + tag, owner="human:ben")
    cursor = get(api, "tasks?limit=5", "cara-test")["cursor"]
    kept = ticket(api, public, "Kept " + tag, owner="human:ben")
    with api.app.state.store.transaction() as c:
        report = delete_tasks(c, [gone_one], apply=True, actor="human:ana", keep_work=True)
    assert report["applied"] is True, report

    feed = get(api, f"tasks?changed_after={cursor}", "cara-test")
    assert not feed.get("reset")
    assert gone_one in feed["gone"]
    assert [t["id"] for t in feed["tasks"]] == [kept]


def test_a_change_to_a_ticket_nothing_records_is_not_read_when_a_members_only_pipeline_exists(api):
    from backend import task_reads
    from backend.pipeline_rights import list_sql

    tag = uuid.uuid4().hex[:8]
    make_pipeline(api, "Legal" + tag, private=True)
    ghost = json.dumps(["human:ben", "human:ana", 0, None])
    with api.app.state.store.read() as c:
        visible = list_sql(c, "human:cara", "1")
        assert task_reads._could_read(c, visible, "no-such-ticket", ghost) is False


def test_an_empty_members_only_pipeline_leaves_a_non_members_list_etag_as_it_was(api):
    first = api.get("/api/v2/tasks?limit=5", headers=headers("cara-test"))
    assert first.status_code == 200 and first.headers["etag"]
    make_pipeline(api, "Legal" + uuid.uuid4().hex[:8], private=True)  # empty, and hidden from her
    again = api.get("/api/v2/tasks?limit=5", headers={**headers("cara-test"), "If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_an_edit_to_a_ticket_a_non_member_cannot_read_leaves_her_labels_etag_as_it_was(api):
    tag = uuid.uuid4().hex[:8]
    typ, _ = make_pipeline(api, "Legal" + tag, private=True)
    hidden_ticket = ticket(api, typ, "Ben's " + tag, owner="human:ben")
    first = api.get("/api/v2/tasks/labels", headers=headers("cara-test"))
    assert first.status_code == 200 and first.headers["etag"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET title=? WHERE id=?", ("Renamed " + tag, hidden_ticket))
    again = api.get("/api/v2/tasks/labels", headers={**headers("cara-test"), "If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
