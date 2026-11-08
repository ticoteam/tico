"""A members-only pipeline on every read surface (Track N): its name and stages from the task types, its tickets and their
requirements and attachments, and its links. A non-member gets nothing about it, a member gets it all, and a member whose
membership is revoked gets nothing again. A list of links is paged, a page at a time."""

import json
import uuid

from backend import hubdb as H
from backend import pipeline_links as L
from backend import pipeline_requirements as R
from backend import requirement_attachments
from backend import task_relations as TR
from backend.auth import Identity
from backend.tests.test_api import api, get, headers, post  # noqa: F401  (api is a fixture)
from backend.tests.test_pipeline_engine import decide
from backend.tests.test_pipeline_foundation import T, insert, rows

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
        # Re-parenting writes the parent's "subtask" history event, the one that could name a hidden child.
        TR.set_parent(c, "human:ana", hidden_child, parent)
        TR.set_parent(c, "human:ana", own_child, parent)
        TR._insert(c, "human:ana", parent, hidden_child, "blocks", T)

    detail = get(api, f"tasks/{parent}", "cara-test")
    assert detail["task"]["id"] == parent  # the owner reads the parent itself
    assert {child["id"] for child in detail["children"]} == {own_child}
    assert detail["task"]["parts"]["total"] == 1, detail["task"]["parts"]
    related = {ref["id"] for refs in detail["task"]["relations"].values() for ref in refs}
    assert hidden_child not in related
    # The parent's history names no hidden child either (the subtask and relation events).
    assert hidden_child not in str(detail["events"]) and own_child in str(detail["events"])
    everyone = get(api, f"tasks/{parent}", "ana-test")
    assert {child["id"] for child in everyone["children"]} == {hidden_child, own_child}
    assert hidden_child in str(everyone["events"])


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


def test_a_subtask_summary_in_a_parents_history_names_only_the_subtasks_the_reader_may_open(api):
    # Closing a readable parent closes its subtask on a members-only pipeline. Its summary names the subtask by id, so a
    # reader who may not open it reads "a subtask", and the title shows to the pipeline's owner. Deleted, too.
    tag = uuid.uuid4().hex[:8]
    legal, _ = make_pipeline(api, "Counsel" + tag, private=True)
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ben", "Launch " + tag, "", "human:ben", lint=False)
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (parent["id"],))
        child = H.task_create(
            c,
            "human:ben",
            "CONFIDENTIAL COUNSEL " + tag,
            "",
            "human:ben",
            type=legal,
            step="Doing",
            parent_id=parent["id"],
            lint=False,
        )
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (child["id"],))
        H.task_close(c, "human:ben", parent["id"], note="Cancelled")

    def summaries(token):
        events = get(api, "tasks/" + parent["id"], token)["events"]
        return [e["new"] for e in events if e["field"] == "children"], json.dumps(events)

    labels, raw = summaries("cara-test")
    assert labels == ["Closed with it: a subtask"] and "CONFIDENTIAL" not in raw
    assert summaries("ana-test")[0] == ["Closed with it: CONFIDENTIAL COUNSEL " + tag]
    post(api, "tasks/" + child["id"] + "/delete", {}, token="ana-test")
    assert summaries("ana-test")[0] == ["Closed with it: a subtask"]


def spawn_pair(api, tag, *, child_owner="human:ana"):
    """A members-only Legal pipeline whose Doing stage spawns onto a company-wide Engineering pipeline. Its child is owned by
    `child_owner` (the Engineering manager, when it is not the filer). Returns (legal, legal steps, link, engineering)."""
    legal, lsteps = make_pipeline(api, "Legal" + tag, private=True)
    eng, _ = make_pipeline(api, "Engineering" + tag)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", (child_owner, eng))
        link = L.create_link(c, "human:ana", lsteps["Doing"], eng, "spawn")
    return legal, lsteps, link, eng


def test_a_spawn_from_a_members_only_pipeline_gives_its_child_neutral_content_a_reader_of_the_child_cannot_open_the_source(
    api,
):
    """Codex F2: Legal (members-only) spawns an Engineering ticket that Cal, who is not on Legal, can read. The child's title,
    body, history and API detail never carry the source's title or label; the handoff still links both tickets."""
    tag = uuid.uuid4().hex[:8]
    legal, lsteps, link, _ = spawn_pair(api, tag)
    secret = "CONFIDENTIAL COUNSEL " + tag
    source = ticket(api, legal, secret, owner="human:ana")
    with api.app.state.store.transaction() as c:
        label = "".join(
            str(v)
            for v in c.execute(
                "SELECT t.prefix || k.number FROM tasks k JOIN task_types t ON t.id = k.type_id WHERE k.id = ?",
                (source,),
            ).fetchone()
        )
        H.task_update(c, "human:ana", source, step=lsteps["Done"])
        [handoff] = L.handoffs(c, link["id"])
    child = handoff["target_task_id"]
    assert (handoff["source_task_id"], child) == (source, child) and child != source
    assert label and label.isalnum()

    detail = get(api, f"tasks/{child}", "cara-test")  # Cal reads the child
    assert detail["task"]["title"] == "New request"
    assert secret not in json.dumps(detail) and "COUNSEL" not in json.dumps(detail) and label not in json.dumps(detail)
    assert secret in json.dumps(get(api, f"tasks/{source}", "ana-test"))  # the source keeps its full detail

    with api.app.state.store.read() as c:
        history = rows(c, "SELECT kind, new, note FROM history_events WHERE subject_id = ?", child)
    assert "carry_skipped" in [h["kind"] for h in history]
    assert "COUNSEL" not in json.dumps(history) and label not in json.dumps(history)
    with api.app.state.store.read() as c:
        body = c.execute("SELECT body FROM tasks WHERE id = ?", (child,)).fetchone()[0]
    assert "COUNSEL" not in body and label not in body and "may not open" in body


def test_a_spawn_carries_no_restricted_page_its_child_owner_or_readers_may_not_open_through_the_real_spawn(api):
    """Codex F3: a company-wide Legal ticket holds a page restricted to Ana. Its Engineering child is owned by Cal, who may not
    open the page. Neither the page's version URL nor its title reaches the child; the skip is logged by requirement name only.
    The restriction is the page's audience, which the carry must check through the file policy (files.file_visible)."""
    tag = uuid.uuid4().hex[:8]
    legal, lsteps = make_pipeline(api, "Legal" + tag)
    eng, _ = make_pipeline(api, "Engineering" + tag)
    memo = "Restricted memo " + tag
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", ("human:cara", eng))
        link = L.create_link(c, "human:ana", lsteps["Doing"], eng, "spawn")
        req = R.add(
            c,
            "human:ana",
            lsteps["Doing"],
            {
                "name": "Memo",
                "kind": "attachment",
                "needs": {"formats": ["page"]},
                "reviewed_by": "person",
                "reviewer_id": "human:ana",
            },
        )
    source = ticket(api, legal, "Merger " + tag, owner="human:ana")
    with api.app.state.store.transaction() as c:
        insert(c, "bots", id="b-memo", slug="memo-bot", display_name="Memo Bot", state="active", created_at=T)
        insert(
            c,
            "bot_files",
            id="file-memo-" + tag,
            bot_id="b-memo",
            scope="task:" + source,
            identity="memo",
            title=memo,
            kind="file",
            mime="text/html",
            locator="tico_blob",
            current_version=1,
            first_activity_at=T,
            last_activity_at=T,
        )
        insert(
            c,
            "blobs",
            id="blob-memo-" + tag,
            owner="human:ana",
            digest="sha-" + tag,
            size=10,
            name="memo.html",
            content_type="text/html",
            created_at=T,
        )
        insert(
            c,
            "bot_file_versions",
            file_id="file-memo-" + tag,
            version=1,
            blob_id="blob-memo-" + tag,
            digest="sha-" + tag,
            size=10,
            name="memo.html",
            mime="text/html",
            actor="human:ana",
            created_at=T,
        )
        insert(c, "pages", file_id="file-memo-" + tag, created_at=T, updated_at=T)
        c.execute(
            "UPDATE pages SET audience=? WHERE file_id=?",
            (json.dumps({"everyone": False, "people": ["human:ana"], "teams": [], "bots": []}), "file-memo-" + tag),
        )
        requirement_attachments.attach(
            c,
            api.app.state.auth,
            Identity("human:ana", "human"),
            source,
            req["id"],
            {"source": "file", "file_id": "file-memo-" + tag},
        )
        decide(c, source, req["id"])
        H.task_update(c, "human:ana", source, step=lsteps["Done"])
        [handoff] = L.handoffs(c, link["id"])
    child = handoff["target_task_id"]
    with api.app.state.store.read() as c:
        assert H.task(c, child)["owner_id"] == "human:cara"
    listed = json.dumps(get(api, f"tasks/{child}", "cara-test"))
    assert "file-memo-" + tag not in listed and memo not in listed
    with api.app.state.store.read() as c:
        assert rows(c, "SELECT url FROM task_links WHERE task_id = ?", child) == []
        skipped = rows(c, "SELECT new FROM history_events WHERE subject_id = ? AND kind = 'carry_skipped'", child)
    assert [s["new"] for s in skipped] == ["Memo"]
    assert memo not in json.dumps(skipped)


def add_company_file(c, file_id, scope, title, blob_id, attempt_id=None):
    """A company file with one version, its blob owned by Ana, under `scope` (a task's or a company one). `attempt_id` is the
    run that made the version, if any."""
    bot = "b-" + file_id
    insert(c, "bots", id=bot, slug=bot, display_name=bot, state="active", created_at=T)
    insert(
        c,
        "bot_files",
        id=file_id,
        bot_id=bot,
        scope=scope,
        identity=file_id,
        title=title,
        kind="file",
        mime="text/html",
        locator="tico_blob",
        current_version=1,
        first_activity_at=T,
        last_activity_at=T,
    )
    insert(
        c,
        "blobs",
        id=blob_id,
        owner="human:ana",
        digest="sha-" + blob_id,
        size=10,
        name="memo.html",
        content_type="text/html",
        created_at=T,
    )
    insert(
        c,
        "bot_file_versions",
        file_id=file_id,
        version=1,
        blob_id=blob_id,
        digest="sha-" + blob_id,
        size=10,
        name="memo.html",
        mime="text/html",
        actor="human:ana",
        created_at=T,
        attempt_id=attempt_id,
    )


def ship_with_file(api, ssteps, link, source, file_id):
    """Ana attaches the company file to `source`, the requirement is decided, and the source ships to its link's target.
    Returns the spawned child's id."""
    return ship_with(api, ssteps, link, source, {"source": "file", "file_id": file_id})


def ship_with(api, ssteps, link, source, body):
    """Ana attaches what `body` names (a file or a blob) to `source`, the requirement is decided, and the source ships to its
    link's target. Returns the spawned child's id."""
    with api.app.state.store.transaction() as c:
        req = R.add(
            c,
            "human:ana",
            ssteps["Doing"],
            {
                "name": "Memo",
                "kind": "attachment",
                "needs": {"formats": ["file"]},
                "reviewed_by": "person",
                "reviewer_id": "human:ana",
            },
        )
        requirement_attachments.attach(
            c,
            api.app.state.auth,
            Identity("human:ana", "human"),
            source,
            req["id"],
            body,
        )
        decide(c, source, req["id"])
        H.task_update(c, "human:ana", source, step=ssteps["Done"])
        [handoff] = L.handoffs(c, link["id"])
    return handoff["target_task_id"]


def test_a_spawn_carries_no_file_of_a_members_only_pipeline_to_a_child_a_non_member_reads(api, monkeypatch):
    """Codex B2: a public Sales ticket attaches a members-only Legal file. Ana (a Legal member) owns the public Engineering
    child, so Cal, who is not on Legal, reads the child. Neither the file's name nor its version URL reaches Cal through the
    child; the skip is logged by requirement name only."""
    monkeypatch.setattr(L, "hub_origin", "https://hub.example.test")
    tag = uuid.uuid4().hex[:8]
    legal, _ = make_pipeline(api, "Legal" + tag, private=True)
    sales, ssteps = make_pipeline(api, "Sales" + tag)
    eng, _ = make_pipeline(api, "Engineering" + tag)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", ("human:ana", eng))
        link = L.create_link(c, "human:ana", ssteps["Doing"], eng, "spawn")
    legal_task = ticket(api, legal, "Legal work " + tag, owner="human:ana")
    memo = "Privileged memo " + tag
    with api.app.state.store.transaction() as c:
        add_company_file(c, "file-legal-" + tag, "task:" + legal_task, memo, "blob-legal-" + tag)
    source = ticket(api, sales, "Public " + tag, owner="human:ana")
    child = ship_with_file(api, ssteps, link, source, "file-legal-" + tag)

    with api.app.state.store.read() as c:
        assert H.task(c, child)["owner_id"] == "human:ana"
    listed = json.dumps(get(api, f"tasks/{child}", "cara-test"))
    assert "file-legal-" + tag not in listed and memo not in listed and "hub.example.test" not in listed
    with api.app.state.store.read() as c:
        assert rows(c, "SELECT url FROM task_links WHERE task_id = ?", child) == []
        skipped = rows(c, "SELECT new FROM history_events WHERE subject_id = ? AND kind = 'carry_skipped'", child)
    assert [s["new"] for s in skipped] == ["Memo"]
    assert memo not in json.dumps(skipped)


def test_a_spawn_still_carries_a_company_file_every_reader_of_the_child_may_open(api, monkeypatch):
    """A company-wide file on a company-wide ticket is carried to a public child as a link to its version."""
    monkeypatch.setattr(L, "hub_origin", "https://hub.example.test")
    tag = uuid.uuid4().hex[:8]
    sales, ssteps = make_pipeline(api, "Sales" + tag)
    eng, _ = make_pipeline(api, "Engineering" + tag)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", ("human:ana", eng))
        link = L.create_link(c, "human:ana", ssteps["Doing"], eng, "spawn")
    source = ticket(api, sales, "Public " + tag, owner="human:ana")
    with api.app.state.store.transaction() as c:
        add_company_file(c, "file-open-" + tag, "task:" + source, "Open memo " + tag, "blob-open-" + tag)
    child = ship_with_file(api, ssteps, link, source, "file-open-" + tag)

    with api.app.state.store.read() as c:
        carried = rows(c, "SELECT url, title FROM task_links WHERE task_id = ?", child)
    assert len(carried) == 1 and carried[0]["url"].startswith("https://hub.example.test/")
    assert carried[0]["title"] == "memo.html"  # a company file's link is titled by its version's name


def test_a_spawn_carries_no_blob_of_a_members_only_file_attached_directly_to_a_child_a_non_member_reads(
    api, monkeypatch
):
    """Codex r5 blocker 2 (a): a public Sales ticket attaches the blob of a members-only Legal file directly (a blob attachment,
    no file). The blob sits on no task_assets row, so its provenance is the Legal file's version. Ana owns the public Engineering
    child, so Cal, who is not on Legal, reads it. Neither the blob's name nor its URL reaches Cal, and the skip is logged."""
    monkeypatch.setattr(L, "hub_origin", "https://hub.example.test")
    tag = uuid.uuid4().hex[:8]
    legal, _ = make_pipeline(api, "Legal" + tag, private=True)
    sales, ssteps = make_pipeline(api, "Sales" + tag)
    eng, _ = make_pipeline(api, "Engineering" + tag)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", ("human:ana", eng))
        link = L.create_link(c, "human:ana", ssteps["Doing"], eng, "spawn")
    legal_task = ticket(api, legal, "Legal work " + tag, owner="human:ana")
    blob = "blob-legal-" + tag
    with api.app.state.store.transaction() as c:
        add_company_file(c, "file-legal-" + tag, "task:" + legal_task, "Privileged " + tag, blob)
    source = ticket(api, sales, "Public " + tag, owner="human:ana")
    child = ship_with(api, ssteps, link, source, {"source": "blob", "blob_id": blob})

    with api.app.state.store.read() as c:
        assert H.task(c, child)["owner_id"] == "human:ana"
    listed = json.dumps(get(api, f"tasks/{child}", "cara-test"))
    assert blob not in listed and "memo.html" not in listed and "hub.example.test" not in listed
    with api.app.state.store.read() as c:
        assert rows(c, "SELECT url FROM task_links WHERE task_id = ?", child) == []
        skipped = rows(c, "SELECT new FROM history_events WHERE subject_id = ? AND kind = 'carry_skipped'", child)
    assert [s["new"] for s in skipped] == ["Memo"]


def test_a_spawn_carries_no_company_file_whose_version_a_members_only_attempt_made(api, monkeypatch):
    """Codex r5 blocker 2 (b): a public Sales ticket attaches a company-wide file whose only version was made by a run on a
    members-only Legal ticket (bot_file_versions.attempt_id -> attempts.task_id). Ana owns the public Engineering child, so Cal,
    who is not on Legal, reads it. Neither the file's id nor its version URL reaches Cal, and the skip is logged."""
    monkeypatch.setattr(L, "hub_origin", "https://hub.example.test")
    tag = uuid.uuid4().hex[:8]
    legal, _ = make_pipeline(api, "Legal" + tag, private=True)
    sales, ssteps = make_pipeline(api, "Sales" + tag)
    eng, _ = make_pipeline(api, "Engineering" + tag)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_types SET manager=? WHERE id=?", ("human:ana", eng))
        link = L.create_link(c, "human:ana", ssteps["Doing"], eng, "spawn")
    legal_task = ticket(api, legal, "Legal work " + tag, owner="human:ana")
    file_id = "file-pub-" + tag
    with api.app.state.store.transaction() as c:
        insert(
            c,
            "attempts",
            id="attempt-legal-" + tag,
            bot_id="b-" + file_id,
            created_at=T,
            task_id=legal_task,
        )
        add_company_file(c, file_id, "bot", "Public memo " + tag, "blob-pub-" + tag, attempt_id="attempt-legal-" + tag)
    source = ticket(api, sales, "Public " + tag, owner="human:ana")
    child = ship_with_file(api, ssteps, link, source, file_id)

    with api.app.state.store.read() as c:
        assert H.task(c, child)["owner_id"] == "human:ana"
    listed = json.dumps(get(api, f"tasks/{child}", "cara-test"))
    assert file_id not in listed and "hub.example.test" not in listed
    with api.app.state.store.read() as c:
        assert rows(c, "SELECT url FROM task_links WHERE task_id = ?", child) == []
        skipped = rows(c, "SELECT new FROM history_events WHERE subject_id = ? AND kind = 'carry_skipped'", child)
    assert [s["new"] for s in skipped] == ["Memo"]
