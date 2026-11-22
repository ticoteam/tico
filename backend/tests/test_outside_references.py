"""Outside references (docs/dev/pipelines-v3.md "Outside items"): an item outside Tico is (connection, source, item type,
key). A Jira issue link is typed with the connection serving its site while a GitHub PR link is typed exactly as before;
two connections holding the same key never read, find or wake each other's tasks; a task holds one typed row per item;
one task at most holds an item's primary link; a connection id (a tool's name, not a secret) is never given to a new
tool and its endpoint stays put; a Contacts record in another system is saved once per connection, names no hidden entity and honors erasure;
and the migration keeps the records an install already has."""

import shutil
import sqlite3
from contextlib import closing

import pytest

from backend import api_tools
from backend import hubdb as H
from backend import mailbox_access as MA
from backend import item_metadata as IM
from backend import migrations
from backend import tool_services as TS
from backend.entities import erase as ER
from backend.entities import store as ES
from backend.item_sources import github as GS
from backend.store import digest, encode
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.mcp import call
from backend.tests.helpers.migrations import at_floor

ACME = "https://acme.atlassian.net/browse/proj-7"
BETA = "https://beta.atlassian.net/browse/PROJ-7"
PR = "https://github.com/ticoteam/tico/pull/12"
SITES = {"tool_a": "https://acme.atlassian.net", "tool_b": "https://beta.atlassian.net"}


def _tools(api, *ids, sites=None):
    with api.app.state.store.transaction() as c:
        for tid in ids:
            c.execute(
                "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
                "updated_at,sites) VALUES(?,?,'Jira',?,'bearer','','proxy','human:ana',?,?,?)",
                (tid, tid, SITES.get(tid, "https://acme.atlassian.net"), H.now(), H.now(), sites),
            )


def _task(api, title):
    return post(api, "tasks", {"owner": "ben", "title": title, "body": "Please."})["id"]


def _link(api, task_id, url, expected=200, token="ana-test", **more):
    return post(api, f"tasks/{task_id}/links", {"url": url, **more}, token=token, expected=expected)


def _rows(api, task_id):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM task_links WHERE task_id=? ORDER BY created_at", (task_id,))]


def _row(api, task_id, url):
    return next(r for r in _rows(api, task_id) if r["url"] == url)


def _found(api, ref, token="ana-test"):
    r = api.get("/api/v2/tasks", params={"status": "all", "link": ref}, headers=headers(token))
    return [row["id"] for row in r.json()["tasks"]] if r.status_code == 200 else r.json()["error"]["code"]


def test_a_pr_link_is_typed_as_before_and_a_jira_link_takes_its_sites_connection(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO github_app(id,app_id,slug,client_id,org,administration,ciphertext,nonce,created_at,created_by) "
            "VALUES('app',1,'example-app','client','ticoteam',0,X'00',X'00',?,?)",
            (H.now(), "human:ana"),
        )
    _tools(api, "tool_a", "tool_b")
    task = _task(api, "Ship the importer")

    _link(api, task, PR)
    pr = _row(api, task, PR)
    assert (pr["kind"], pr["source"], pr["item_type"], pr["item_key"]) == ("pr", "github", "pr", "ticoteam/tico#12")
    assert (pr["connection_id"], pr["is_primary"]) == ("", 0)
    _link(api, task, "https://github.com/other/app/pull/3")
    assert _row(api, task, "https://github.com/other/app/pull/3")["source"] is None

    _link(api, task, ACME)  # no connection named: the one serving acme.atlassian.net
    issue = _row(api, task, ACME)
    assert (issue["kind"], issue["source"], issue["item_type"], issue["item_key"], issue["connection_id"]) == (
        "url",
        "jira",
        "issue",
        "PROJ-7",
        "tool_a",
    )
    _link(api, task, "https://gamma.atlassian.net/browse/OPS-1")  # a site nothing serves: a plain link
    assert _row(api, task, "https://gamma.atlassian.net/browse/OPS-1")["source"] is None

    # The connection of another site, one that does not exist, or no item at all: refused.
    for url, connection in (
        (BETA.replace("PROJ-7", "PROJ-8"), "tool_a"),
        (ACME, "tool_nope"),
        ("https://d.example", "tool_a"),
    ):
        assert _link(api, task, url, 422, connection_id=connection)["error"]["code"] == "connection"


def test_a_jira_url_resolves_to_its_sites_connection_and_an_ambiguous_site_is_refused(api):
    _tools(api, "tool_a", "tool_b")
    first, second = _task(api, "Refund for site A"), _task(api, "Refund for site B")
    _link(api, first, ACME)
    _link(api, second, BETA)

    assert _found(api, ACME) == [first]
    assert _found(api, BETA) == [second]
    assert _found(api, "jira:issue:proj-7@tool_a") == [first]
    assert _found(api, "jira:issue:PROJ-7") == "link"  # a Jira item is one connection's
    assert _found(api, "jira:epic:PROJ-7@tool_a") == "link"

    _tools(api, "tool_c")  # a second connection on acme.atlassian.net
    third = _task(api, "Refund again")
    assert _link(api, third, ACME.replace("7", "9"), 422)["error"]["code"] == "connection"
    assert _found(api, ACME) == "link"
    _link(api, third, ACME.replace("7", "9"), connection_id="tool_c")
    assert _row(api, third, ACME.replace("7", "9"))["connection_id"] == "tool_c"


def test_a_task_holds_one_typed_row_per_item_whatever_url_names_it(api):
    task = _task(api, "Customer escalation")
    _link(api, task, ACME)  # before acme has a connection: a plain link
    assert _row(api, task, ACME)["source"] is None
    _tools(api, "tool_a")
    _link(api, task, ACME)  # the same URL again, now that the site has one: that row becomes the item's
    board = "https://acme.atlassian.net/jira/software/projects/PROJ/boards/1?selectedIssue=PROJ-7"
    _link(api, task, board)  # another URL for the same item: the same row
    rows = _rows(api, task)
    assert [(r["url"], r["source"], r["connection_id"]) for r in rows] == [(ACME, "jira", "tool_a")]
    assert _found(api, board) == [task]


def test_two_connections_holding_one_jira_key_never_cross(api):
    _tools(api, "tool_a", "tool_b")
    first, second = _task(api, "Refund for site A"), _task(api, "Refund for site B")
    _link(api, first, ACME)
    _link(api, second, BETA)
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
        assert IM.linked(c, "jira", "issue", "PROJ-7", "tool_b") and not IM.linked(c, "jira", "issue", "PROJ-7")
        now = H.now()
        assert IM.due_reads(c, "jira", "issue", now, connection_id="tool_a") == ["PROJ-7"]
        assert IM.due_reads(c, "jira", "issue", now, connection_id="tool_b") == []

    err, listed = call(api, "tico_task_list", {"link": "jira:issue:PROJ-7@tool_b", "snoozed": "all"})
    assert not err and [row["id"] for row in listed["result"]] == [second]


def test_githubs_own_reads_ignore_another_connections_metadata(api):
    with api.app.state.store.transaction() as c:
        fields = {"state": "open", "head_sha": "a" * 40, "checks_state": "success"}
        IM.save(c, "github", "pr", "ticoteam/tico#5", fields, 1, connection_id="tool_x")
    with api.app.state.store.read_transaction() as c:
        assert GS.runs_checks(c, "ticoteam/tico") is False
        assert GS._open_at_head(c, "ticoteam/tico", "a" * 40) == []


def test_one_task_at_most_holds_the_primary_link_to_an_item_of_one_connection(api):
    _tools(api, "tool_a", "tool_b")
    tasks = [_task(api, f"Customer ask {n}") for n in range(3)]
    for task, url in zip(tasks, (ACME, BETA, ACME), strict=True):
        _link(api, task, url)
    with api.app.state.store.transaction() as c:
        primary = "UPDATE task_links SET is_primary=1 WHERE task_id=?"
        c.execute(primary, (tasks[0],))
        c.execute(primary, (tasks[1],))  # the same key through another connection is another item
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(primary, (tasks[2],))


def test_a_deleted_tools_id_is_never_given_to_a_new_tool_while_references_name_it(api):
    _tools(api, "tool_a")
    _link(api, _task(api, "Old site"), ACME)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM tools WHERE id='tool_a'")
        assert api_tools._unique_id(c, "tool_a") != "tool_a"
        assert TS.insert_tool(c, "", "tool_a", "proxy", "human:ana", H.now(), tid=None) != "tool_a"


def test_a_contacts_record_is_saved_once_per_connection_and_names_no_other_entity(api):
    _tools(api, "tool_a", "tool_b")
    with api.app.state.store.transaction() as c:
        acme = ES.upsert(c, "human:ana", "organization", match={"domain": "acme.example"}, name="Acme")["id"]
        other = ES.upsert(c, "human:ana", "organization", match={"domain": "other.example"}, name="Other")["id"]
    record = {"ref": acme, "system": "jira", "external_type": "customer", "external_id": "10042"}

    first = post(api, "entities/external-refs", {**record, "connection_id": "tool_a"}, key="ref-1")
    assert set(first) == {"id", "entity"}  # the stored receipt holds no provider id or URL
    again = post(api, "entities/external-refs", {**record, "connection_id": "tool_a", "url": "https://x.example/1"})
    assert again == first
    err, through_b = call(api, "tico_contacts_record", {**record, "connection_id": "tool_b"})
    assert not err and through_b["id"] != first["id"]
    with api.app.state.store.read() as c:
        assert [r[0] for r in c.execute("SELECT url FROM entity_external_refs ORDER BY connection_id")] == [
            "https://x.example/1",
            "",
        ]

    taken = post(api, "entities/external-refs", {**record, "ref": other, "connection_id": "tool_a"}, expected=422)
    assert taken["error"]["code"] == "conflict" and acme not in taken["error"]["detail"]
    card = get(api, f"entities/card?ref={acme}")
    assert sorted(ref["connection_id"] for ref in card["refs"]) == ["tool_a", "tool_b"]


def test_an_erased_record_is_not_written_again_nor_replayed(api):
    with api.app.state.store.transaction() as c:
        jane = ES.upsert(c, "human:ana", "person", match={"email": "jane@example.test"}, name="Jane")["id"]
        kim = ES.upsert(c, "human:ana", "person", match={"email": "kim@example.test"}, name="Kim")["id"]
    record = {"ref": jane, "system": "close", "external_type": "contact", "external_id": "cl_4411"}
    post(api, "entities/external-refs", record, key="jane-ref")
    with api.app.state.store.transaction() as c:
        ER.erase(c, "human:ana", jane, "request")

    replay = api.post("/api/v2/entities/external-refs", json=record, headers=headers("ana-test", "jane-ref"))
    assert replay.status_code == 404
    refused = post(api, "entities/external-refs", {**record, "ref": kim}, expected=422)
    assert refused["error"]["code"] == "suppressed"


def test_a_link_retried_from_before_connections_existed_replays(api):
    task = _task(api, "Read the brief")
    body = {"url": "https://docs.acme.example/brief"}
    post(api, f"tasks/{task}/links", body, key="link-1")
    legacy = {"url": body["url"], "title": None, "remove": None, "kind": None, "wake_once": None}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE idempotency SET request_hash=? WHERE key='link-1'", (digest(encode(legacy)),))
    assert post(api, f"tasks/{task}/links", body, key="link-1")["links"][0]["url"] == body["url"]


def test_a_connection_on_two_sites_resolves_no_jira_link(api):
    _tools(api, "tool_a", sites='["https://beta.atlassian.net"]')
    task = _task(api, "Two sites, one tool")
    _link(api, task, ACME)
    _link(api, task, BETA)
    assert [r["source"] for r in _rows(api, task)] == [None, None]
    assert _found(api, ACME) == "link"


def test_reattaching_both_plain_urls_of_an_item_leaves_one_row(api):
    task = _task(api, "Before the site was connected")
    board = "https://acme.atlassian.net/jira/software/projects/PROJ/boards/1?selectedIssue=PROJ-7"
    _link(api, task, ACME)
    _link(api, task, board)
    _tools(api, "tool_a")
    _link(api, task, ACME)
    _link(api, task, board)
    assert [(r["url"], r["source"]) for r in _rows(api, task)] == [(ACME, "jira")]


def test_a_trashed_tasks_link_keeps_its_connection_id_reserved(api):
    _tools(api, "tool_a")
    task = _task(api, "Deleted with its link")
    _link(api, task, ACME)
    post(api, f"tasks/{task}/delete", {})
    with api.app.state.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM task_links WHERE task_id=?", (task,)).fetchone()[0] == 0
        c.execute("DELETE FROM tools WHERE id='tool_a'")
        assert api_tools._unique_id(c, "tool_a") != "tool_a"


def test_a_record_retry_is_shown_only_while_its_saved_entity_is_seen(api):
    with api.app.state.store.transaction() as c:
        box = MA.ensure_mailbox(c, "ana@acme.example", person_id="ana", created_by="human:ana")
        jane = ES.upsert(c, "human:ana", "person", match={"email": "jane@example.test"}, name="Jane")["id"]
        ES.external_ref(c, "human:ana", jane, "legacy", "contact", "shared-1")
    record = {"ref": "shared-1", "system": "jira", "external_type": "customer", "external_id": "9"}
    assert post(api, "entities/external-refs", record, token="ben-test", key="ben-ref")["entity"] == jane
    with api.app.state.store.transaction() as c:
        ES.add_source(c, "human:ana", jane, "mailbox", box["id"], visibility="restricted")  # ben no longer sees Jane
        kim = ES.upsert(c, "human:ana", "person", match={"email": "kim@example.test"}, name="Kim")["id"]
        ES.external_ref(c, "human:ana", kim, "other", "contact", "shared-1")  # the alias now names Kim for ben
    retry = api.post("/api/v2/entities/external-refs", json=record, headers=headers("ben-test", "ben-ref"))
    assert retry.status_code == 404 and jane not in retry.text


def test_erasing_a_record_suppresses_it_on_its_connection_only(api):
    _tools(api, "tool_a", "tool_b")
    with api.app.state.store.transaction() as c:
        jane = ES.upsert(c, "human:ana", "person", match={"email": "jane@example.test"}, name="Jane")["id"]
        kim = ES.upsert(c, "human:ana", "person", match={"email": "kim@example.test"}, name="Kim")["id"]
    record = {"system": "jira", "external_type": "customer", "external_id": "42"}
    post(api, "entities/external-refs", {**record, "ref": jane, "connection_id": "tool_a"})
    with api.app.state.store.transaction() as c:
        ER.erase(c, "human:ana", jane, "request")
    refused = post(api, "entities/external-refs", {**record, "ref": kim, "connection_id": "tool_a"}, expected=422)
    assert refused["error"]["code"] == "suppressed"
    post(api, "entities/external-refs", {**record, "ref": kim, "connection_id": "tool_b"})


def test_a_referenced_tool_keeps_its_site(api):
    _tools(api, "tool_a")
    task = _task(api, "Acme escalation")
    moved = api.patch("/api/v2/api-tools/tool_a", json={"base_url": "https://beta.atlassian.net"}, headers=headers())
    assert moved.status_code == 200, moved.text  # nothing names it yet: it may move
    api.patch("/api/v2/api-tools/tool_a", json={"base_url": "https://acme.atlassian.net"}, headers=headers())
    _link(api, task, ACME)
    refused = api.patch("/api/v2/api-tools/tool_a", json={"base_url": "https://beta.atlassian.net"}, headers=headers())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "connection_sites"
    assert _found(api, BETA) == "link"
    path = api.patch(
        "/api/v2/api-tools/tool_a", json={"base_url": "https://acme.atlassian.net/beta"}, headers=headers()
    )
    assert path.status_code == 409  # another endpoint on the same host is another connection too


def test_a_record_erased_without_a_connection_stays_apart_from_each_connections(api):
    _tools(api, "tool_a")
    with api.app.state.store.transaction() as c:
        jane = ES.upsert(c, "human:ana", "person", match={"email": "jane@example.test"}, name="Jane")["id"]
        kim = ES.upsert(c, "human:ana", "person", match={"email": "kim@example.test"}, name="Kim")["id"]
    record = {"system": "jira", "external_type": "customer", "external_id": "42"}
    post(api, "entities/external-refs", {**record, "ref": jane})
    with api.app.state.store.transaction() as c:
        ER.erase(c, "human:ana", jane, "request")
    assert post(api, "entities/external-refs", {**record, "ref": kim}, expected=422)["error"]["code"] == "suppressed"
    post(api, "entities/external-refs", {**record, "ref": kim, "connection_id": "tool_a"})


def test_a_task_created_with_a_link_two_connections_serve_keeps_it_plain(api):
    _tools(api, "tool_a", "tool_c")  # both on acme.atlassian.net
    made = post(api, "tasks", {"owner": "ben", "title": "Filed with a link", "body": "Please.", "links": [ACME]})["id"]
    assert [(r["url"], r["source"]) for r in _rows(api, made)] == [(ACME, None)]


def test_a_url_is_linked_to_a_task_through_one_connection_only(api):
    _tools(api, "tool_a", "tool_c")
    task = _task(api, "One link per URL")
    _link(api, task, ACME, connection_id="tool_a")
    assert _link(api, task, ACME, 422, connection_id="tool_c")["error"]["code"] == "connection"
    assert [r["connection_id"] for r in _rows(api, task)] == ["tool_a"]


def test_a_referenced_tools_endpoint_path_keeps_its_case(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
            "updated_at) VALUES('crm','crm','','https://crm.example.com/Acme','bearer','','proxy','human:ana',?,?)",
            (H.now(), H.now()),
        )
        org = ES.upsert(c, "human:ana", "organization", match={"domain": "acme.example"}, name="Acme")["id"]
    post(
        api,
        "entities/external-refs",
        {"ref": org, "system": "crm", "external_type": "customer", "external_id": "42", "connection_id": "crm"},
    )
    moved = api.patch("/api/v2/api-tools/crm", json={"base_url": "https://crm.example.com/acme"}, headers=headers())
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "connection_sites"


def test_a_merge_keeps_the_kept_tasks_link_to_a_url_both_have(api):
    _tools(api, "tool_a", "tool_c")  # both on acme.atlassian.net
    dup, kept = _task(api, "Duplicate ask"), _task(api, "Ask")
    _link(api, dup, ACME, connection_id="tool_a")
    _link(api, kept, ACME, connection_id="tool_c")
    post(api, f"tasks/{dup}/merge", {"into": kept})
    assert [(r["url"], r["connection_id"]) for r in _rows(api, kept)] == [(ACME, "tool_c")]


def test_a_merge_copies_a_stored_outside_link_after_its_tool_is_gone(api):
    _tools(api, "tool_a")
    dup, kept = _task(api, "Duplicate escalation"), _task(api, "Escalation")
    _link(api, dup, ACME)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM tools WHERE id='tool_a'")
    post(api, f"tasks/{dup}/merge", {"into": kept})
    assert [(r["url"], r["source"], r["connection_id"]) for r in _rows(api, kept)] == [(ACME, "jira", "tool_a")]


def test_the_record_write_is_in_the_contract(api):
    document = api.get("/api/v2/openapi.json", headers=headers()).json()
    op = document["paths"]["/api/v2/entities/external-refs"]["post"]
    assert op["operationId"] == "recordEntityExternalRef" and op["tags"] == ["Contacts"]


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
