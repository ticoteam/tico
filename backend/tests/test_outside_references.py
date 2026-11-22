"""Outside references (docs/dev/pipelines-v3.md "Outside items"): an item outside Tico is (connection, source, item type,
key). A Jira issue link is stored typed with the connection it came through while a GitHub PR link is typed exactly as
before; two connections holding the same key never read, find or wake each other's tasks; one task at most holds an
item's primary link; a Contacts record in another system is saved once per connection; and the migration keeps the
records an install already has."""

import shutil
import sqlite3
from contextlib import closing

import pytest

from backend import hubdb as H
from backend import item_metadata as IM
from backend import migrations
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.mcp import call
from backend.tests.helpers.migrations import at_floor

ISSUE = "https://acme.atlassian.net/browse/proj-7"
PR = "https://github.com/ticoteam/tico/pull/12"


def _tools(api, *ids):
    with api.app.state.store.transaction() as c:
        for tid in ids:
            c.execute(
                "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
                "updated_at) VALUES(?,?,'Jira','','bearer','','proxy','human:ana',?,?)",
                (tid, tid, H.now(), H.now()),
            )


def _task(api, title):
    return post(api, "tasks", {"owner": "ben", "title": title, "body": "Please."})["id"]


def _link(api, task_id, url, expected=200, **more):
    return post(api, f"tasks/{task_id}/links", {"url": url, **more}, expected=expected)


def _row(api, task_id, url):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM task_links WHERE task_id=? AND url=?", (task_id, url)).fetchone())


def test_a_pr_link_is_typed_as_before_and_a_jira_link_is_typed_with_its_connection(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
            "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)",
            (H.now(), "human:ana"),
        )
    _tools(api, "tool_a")
    task = _task(api, "Ship the importer")

    _link(api, task, PR)
    pr = _row(api, task, PR)
    assert (pr["kind"], pr["source"], pr["item_type"], pr["item_key"]) == ("pr", "github", "pr", "ticoteam/tico#12")
    assert (pr["connection_id"], pr["is_primary"]) == ("", 0)
    outside_org = "https://github.com/other/app/pull/3"
    _link(api, task, outside_org)
    assert (_row(api, task, outside_org)["kind"], _row(api, task, outside_org)["source"]) == ("url", None)

    _link(api, task, ISSUE, connection_id="tool_a")
    issue = _row(api, task, ISSUE)
    assert (issue["kind"], issue["source"], issue["item_type"], issue["item_key"], issue["connection_id"]) == (
        "url",
        "jira",
        "issue",
        "PROJ-7",
        "tool_a",
    )
    board = "https://acme.atlassian.net/jira/software/projects/OPS/boards/1?selectedIssue=OPS-3"
    _link(api, task, board)
    assert (_row(api, task, board)["item_key"], _row(api, task, board)["connection_id"]) == ("OPS-3", "")

    refused = _link(api, task, "https://acme.atlassian.net/browse/PROJ-8", 422, connection_id="tool_nope")
    assert refused["error"]["code"] == "connection"
    refused = _link(api, task, "https://docs.acme.example/plan", 422, connection_id="tool_a")
    assert refused["error"]["code"] == "connection"


def test_two_connections_holding_one_jira_key_never_cross(api):
    _tools(api, "tool_a", "tool_b")
    first, second = _task(api, "Refund for site A"), _task(api, "Refund for site B")
    _link(api, first, ISSUE, connection_id="tool_a")
    _link(api, second, ISSUE, connection_id="tool_b")
    store = api.app.state.store
    with store.transaction() as c:
        assert IM.save(c, "jira", "issue", "PROJ-7", {"state": "open"}, 1, connection_id="tool_a")
        assert IM.save(c, "jira", "issue", "PROJ-7", {"state": "done"}, 1, connection_id="tool_b")
        IM.mark_due(c, "jira", "issue", "PROJ-7", connection_id="tool_a")

    with store.read_transaction() as c:
        assert IM.get(c, "jira", "issue", "proj-7", "tool_a")["fields"] == {"state": "open"}
        assert IM.get(c, "jira", "issue", "PROJ-7", "tool_b")["fields"] == {"state": "done"}
        assert IM.get(c, "jira", "issue", "PROJ-7") is None
        IM.prime_of_task(c, [first, second], "jira", "issue")  # the batched read lists use
        states = {
            task: [link["metadata"]["fields"]["state"] for link in IM.of_task(c, task, "jira", "issue")]
            for task in (first, second)
        }
        assert states == {first: ["open"], second: ["done"]}
        assert IM.linking(c, "jira", "issue", "PROJ-7", "tool_a") == [first]
        assert IM.linked(c, "jira", "issue", "PROJ-7", "tool_b") and not IM.linked(c, "jira", "issue", "PROJ-7")
        now = H.now()
        assert IM.due_reads(c, "jira", "issue", now, connection_id="tool_a") == ["PROJ-7"]
        assert IM.due_reads(c, "jira", "issue", now, connection_id="tool_b") == []

    def found(query):
        return [row["id"] for row in get(api, "tasks?status=all&" + query)["tasks"]]

    assert found("link=jira:issue:proj-7@tool_a") == [first]
    assert found("link=jira:issue:PROJ-7@tool_b") == [second]
    assert found("link=jira:issue:PROJ-7") == []
    assert found("link=" + ISSUE) == []  # a URL names no connection
    bad = api.get("/api/v2/tasks?link=jira:epic:PROJ-7", headers=headers())
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "link"
    err, listed = call(api, "tico_task_list", {"link": "jira:issue:PROJ-7@tool_b", "snoozed": "all"})
    assert not err and [row["id"] for row in listed["result"]] == [second]


def test_one_task_at_most_holds_the_primary_link_to_an_item_of_one_connection(api):
    _tools(api, "tool_a", "tool_b")
    tasks = [_task(api, f"Customer ask {n}") for n in range(3)]
    for task, connection in zip(tasks, ("tool_a", "tool_b", "tool_a"), strict=True):
        _link(api, task, ISSUE, connection_id=connection)
    with api.app.state.store.transaction() as c:
        primary = "UPDATE task_links SET is_primary=1 WHERE task_id=? AND url=?"
        c.execute(primary, (tasks[0], ISSUE))
        c.execute(primary, (tasks[1], ISSUE))  # the same key through another connection is another item
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(primary, (tasks[2], ISSUE))


def test_a_contacts_record_is_saved_once_per_connection(api):
    _tools(api, "tool_a", "tool_b")
    with api.app.state.store.transaction() as c:
        from backend.entities import store as ES

        acme = ES.upsert(c, "human:ana", "organization", match={"domain": "acme.example"}, name="Acme")["id"]
        other = ES.upsert(c, "human:ana", "organization", match={"domain": "other.example"}, name="Other")["id"]
    record = {"ref": acme, "system": "jira", "external_type": "customer", "external_id": "10042"}

    first = post(api, "entities/external-refs", {**record, "connection_id": "tool_a"}, key="ref-1")
    again = post(api, "entities/external-refs", {**record, "connection_id": "tool_a", "url": "https://x.example/1"})
    assert again["id"] == first["id"] and again["url"] == "https://x.example/1" and again["entity"] == acme
    err, through_b = call(api, "tico_contacts_record", {**record, "connection_id": "tool_b"})
    assert not err and through_b["id"] != first["id"]
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT connection_id FROM entity_external_refs ORDER BY connection_id").fetchall()
    assert [r[0] for r in rows] == ["tool_a", "tool_b"]

    taken = post(api, "entities/external-refs", {**record, "ref": other, "connection_id": "tool_a"}, expected=422)
    assert taken["error"]["code"] == "conflict" and acme in taken["error"]["detail"]
    card = get(api, f"entities/card?ref={acme}")
    assert sorted(ref["connection_id"] for ref in card["refs"]) == ["tool_a", "tool_b"]


def test_the_migration_keeps_records_and_scopes_the_unique_keys_to_the_connection(tmp_path):
    version = next(m.version for m in migrations.files() if m.version.endswith("_outside_references"))
    directory = tmp_path / "migrations"
    directory.mkdir()
    for migration in migrations.files():
        if migration.version < version:
            shutil.copy(migration.path, directory)
    shutil.copy(migrations.DIRECTORY / migrations.RETIRED_LIST, directory)
    with closing(at_floor(tmp_path / "hub.sqlite")) as conn:
        migrations.apply(conn, directory)
        conn.execute(
            "INSERT INTO entities (id, type, name, created_by, created_at, updated_at) "
            "VALUES ('organization/acme', 'organization', 'Acme', 'human:ana', 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO entity_external_refs (rowid, id, entity_id, connection_id, system, external_type, external_id, "
            "url, revision, synced_at, status) VALUES (7, 'x1', 'organization/acme', 'tool_a', 'hubspot', 'company', "
            "'99', 'https://h.example/99', 'r3', '2026-11-01', 'active')"
        )
        refs_sql = "SELECT rowid, * FROM entity_external_refs"
        index_sql = "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='entity_external_refs'"
        before = conn.execute(refs_sql).fetchall()
        indexes = sorted(row for row in conn.execute(index_sql).fetchall() if row[1])

        migrations.apply(conn, migrations.DIRECTORY)

        assert conn.execute(refs_sql).fetchall() == before
        assert sorted(row for row in conn.execute(index_sql).fetchall() if row[1]) == indexes
        add = (
            "INSERT INTO entity_external_refs (id, entity_id, connection_id, system, external_type, external_id) "
            "VALUES (?, 'organization/acme', ?, 'hubspot', 'company', '99')"
        )
        conn.execute(add, ("x2", "tool_b"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(add, ("x3", "tool_a"))
        for table, column in (
            ("tools", "record_owner"),
            ("task_links", "is_primary"),
            ("item_metadata", "connection_id"),
        ):
            assert column in [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        named = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index'").fetchall())
        assert "connection_id" in named["item_metadata_item"] and "connection_id" in named["task_links_item"]
        assert "is_primary = 1" in named["task_links_primary_item"]
