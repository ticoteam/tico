"""POST /api/v2/sql: read-only SQL whose visibility rules live in the database layer.

The rules are the ones `backend/auth.py` applies to the JSON API, expressed once more as temp
views and an authorizer; these tests hold the two to the same answers.
"""

import json
import re

import pytest

from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, headers, post, ready, runner

SECRET_TABLES = ("credentials", "credential_keys", "credential_grants", "idempotency", "runners",
                 "enrollments", "session_epochs", "settings_changes", "backup_verified_blobs",
                 "sqlite_temp_master", "sqlite_sequence", "_litestream_seq")
SECRET_COLUMNS = (("attempts", "token_hash"), ("bots", "token_hash"), ("service_jobs", "token_hash"))
REJECTED = ("PRAGMA table_info(messages)", "DELETE FROM messages", "ATTACH ':memory:' AS other",
            "CREATE TEMP VIEW v AS SELECT 1", "SELECT 1; DELETE FROM messages")


def query(api, sql, token="ana-test", expected=200, **body):
    r = api.post("/api/v2/sql", json={"sql": sql, **body}, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def column(api, sql, token="ana-test"):
    return sorted(row[0] for row in query(api, sql, token)["rows"])


def error(api, sql, token="ana-test"):
    return query(api, sql, token, expected=422)["error"]["detail"]


@pytest.fixture
def world(api):
    """Two people's private Tico rooms, a private bot's task, and a ops turn granted Ana's room."""
    with api.app.state.store.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS _litestream_seq(id INTEGER PRIMARY KEY, seq INTEGER)")
        c.execute("INSERT INTO _litestream_seq VALUES(1, 1)")
    ana_room = post(api, "chat/ops", {"text": "Ana private context."})["conversation_id"]
    steven_room = post(api, "chat/ops", {"text": "Ben private context."}, token="ben-test")["conversation_id"]
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine)
    with api.app.state.store.transaction() as c:   # a person's Mac: its credential reads as them
        c.execute("UPDATE runners SET platform='darwin' WHERE id=?", (machine["runner_id"],))
    private = post(api, "tasks", {"owner": "inbox", "title": "Sort the inbox", "body": "Private to Ana."})
    coo_task = post(api, "tasks", {"owner": "ops", "title": "Summarize the week", "body": "For ops."})
    finance_task = post(api, "tasks", {"owner": "finance", "title": "Close the books", "body": "For finance."})
    return {"ana_room": ana_room, "steven_room": steven_room, "machine": machine, "attempt": attempt,
            "private": private, "coo_task": coo_task, "finance_task": finance_task}


def test_only_one_read_statement_is_accepted(api):
    for sql in REJECTED:
        detail = error(api, sql)
        assert "statement" in detail, (sql, detail)
    assert query(api, "-- a comment\n/* another */ SELECT 1 AS one")["rows"] == [[1]]
    assert query(api, "select 1 as one")["columns"] == ["one"]
    assert query(api, "EXPLAIN QUERY PLAN SELECT * FROM tasks")["columns"] == ["id", "parent", "notused", "detail"]
    assert "no such table: nope" in error(api, "SELECT * FROM nope")
    assert query(api, "SELECT x'0102' AS b") == {"columns": ["b"], "rows": [["AQI="]], "row_count": 1,
                                                 "truncated": False, "ms": 0, "note": "BLOB values are base64"}


def test_secret_tables_and_columns_are_denied_for_everyone(api, world):
    for token in ("ana-test", world["attempt"]["token"]):
        for table in SECRET_TABLES:
            assert "prohibited" in error(api, f"SELECT * FROM {table}", token), (table, token)
            assert error(api, f"SELECT count(*) FROM {table}", token) == "not authorized", (table, token)
        for table, name in SECRET_COLUMNS:
            assert f"no such column: {name}" in error(api, f"SELECT {name} FROM {table}", token)
            assert name not in query(api, f"SELECT * FROM {table}", token)["columns"]
        # The schema is readable; the connection's own view definitions are not.
        assert "credentials" in column(api, "SELECT name FROM sqlite_master WHERE type='table'", token)
        assert "prohibited" in error(api, "SELECT * FROM temp.sqlite_temp_master", token)
    # Bots never read people's email addresses; people do.
    assert "no such column: email" in error(api, "SELECT email FROM humans", world["attempt"]["token"])
    assert column(api, "SELECT id FROM humans", world["attempt"]["token"]) == ["ana", "ben", "cara"]
    assert query(api, "SELECT email FROM humans WHERE id='ben'", "ben-test")["rows"] == [["ben@acme.example"]]


def test_the_base_tables_cannot_be_reached_around_the_views(api, world):
    for token in ("ana-test", world["attempt"]["token"]):
        for sql in ("SELECT * FROM main.messages", 'SELECT * FROM "main"."messages"', "SELECT * FROM MAIN.tasks",
                    "SELECT * FROM messages, main.credentials",
                    "WITH messages AS (SELECT * FROM main.messages) SELECT * FROM messages",
                    "WITH conversations AS (SELECT * FROM main.conversations) SELECT * FROM conversations",
                    "WITH c AS (SELECT id FROM main.conversations) SELECT * FROM messages WHERE conversation_id IN (SELECT id FROM c)",
                    "SELECT (SELECT count(*) FROM main.messages) FROM messages"):
            detail = error(api, sql, token)
            assert "prohibited" in detail or detail == "not authorized", (sql, detail)
    # A CTE that shadows a view name cannot widen it either: it still reads through the view.
    rows = query(api, "WITH conversations AS (SELECT id FROM conversations) SELECT count(*) FROM messages",
                 world["attempt"]["token"])["rows"]
    assert rows == [[len(column(api, "SELECT id FROM messages", world["attempt"]["token"]))]]
    # Nothing in a response names the connection's inner views.
    plan = json.dumps(query(api, "EXPLAIN QUERY PLAN SELECT * FROM messages m JOIN tasks t ON t.conversation_id=m.conversation_id"))
    assert not re.search(r"v[0-9a-f]{24}_", plan), plan


def test_owner_sees_everything_but_other_peoples_rooms(api, world):
    tasks = column(api, "SELECT id FROM tasks")
    assert tasks == sorted(world[k]["id"] for k in ("private", "coo_task", "finance_task"))
    assert "inbox" in column(api, "SELECT slug FROM bots")
    bodies = column(api, "SELECT body FROM messages")
    assert "Ana private context." in bodies and "New task from human:ana: Sort the inbox" in bodies
    assert "Ben private context." not in bodies
    personal = column(api, "SELECT id FROM conversations WHERE scope='personal'")
    assert world["ana_room"] in personal and world["steven_room"] not in personal
    assert world["coo_task"]["conversation_id"] == world["ana_room"]
    assert world["private"]["conversation_id"] in personal
    assert world["finance_task"]["conversation_id"] in personal
    assert column(api, "SELECT id FROM attempts") == [world["attempt"]["id"]]
    assert column(api, "SELECT id FROM turns") == [world["attempt"]["id"]]
    with api.app.state.store.read() as c:
        jobs = c.execute("SELECT count(*) FROM jobs").fetchone()[0]
    # Every queued job but the one Ben's private room raised.
    assert jobs == 5 and len(column(api, "SELECT id FROM jobs")) == 4
    # Clone and subscription profile reports have per-computer keys; the rest are fixed.
    assert column(api, "SELECT key FROM registry_metadata WHERE key NOT LIKE 'computer-repositories:%' AND key NOT LIKE 'computer-profiles:%'") == ["access", "access_bot_limit", "bot_access", "credential-file-migration-v1", "credential-file-migration-v2-hub", "docs_migrated", "librarian_wording35", "librarian_wording36", "onboarding", "owner", "people", "release-history", "repositories-access-migrated", "usage-count"]


def test_a_person_sees_the_company_but_not_private_bots_or_other_rooms(api, world):
    as_member(api, "ben@acme.example")
    ben = "ben-test"
    tasks = column(api, "SELECT id FROM tasks", ben)
    assert tasks == sorted(world[k]["id"] for k in ("coo_task", "finance_task"))
    assert "inbox" not in column(api, "SELECT slug FROM bots", ben)
    assert column(api, "SELECT bot FROM bot_status", ben) == sorted(
        b for b in column(api, "SELECT bot FROM bot_status") if b != "inbox")
    assert column(api, "SELECT body FROM messages", ben) == ["Ben private context."]
    assert column(api, "SELECT id FROM conversations", ben) == [world["steven_room"]]
    assert column(api, "SELECT id FROM attempts", ben) == []
    assert column(api, f"SELECT task_id FROM task_events WHERE task_id='{world['private']['id']}'", ben) == []
    assert column(api, f"SELECT task_id FROM task_events WHERE task_id='{world['coo_task']['id']}'", ben)


def test_a_bot_reads_ordinary_tasks_but_only_its_granted_rooms_and_no_private_data(api, world):
    token = world["attempt"]["token"]
    assert query(api, "SELECT 1", token)["rows"] == [[1]]
    bodies = column(api, "SELECT body FROM messages", token)
    assert bodies == ["Ana private context.", "New task from human:ana: Summarize the week"]
    assert column(api, "SELECT id FROM conversations", token) == [world["ana_room"]]
    assert column(api, "SELECT id FROM tasks", token) == sorted([world["coo_task"]["id"], world["finance_task"]["id"]])
    assert "inbox" not in column(api, "SELECT slug FROM bots", token)
    assert column(api, "SELECT id FROM attempts", token) == [world["attempt"]["id"]]
    # Its running job and the queued one behind its own task; nothing of another bot's.
    jobs = query(api, "SELECT id, bot FROM jobs", token)["rows"]
    assert len(jobs) == 2 and [world["attempt"]["job_id"], "ops"] in jobs and {row[1] for row in jobs} == {"ops"}
    assert column(api, "SELECT key FROM registry_metadata", token) == []
    assert column(api, "SELECT DISTINCT actor FROM events", token) in ([], ["bot:ops"])
    # Delegations do not widen the already readable ordinary task set or the hidden bot's activity.
    with api.app.state.store.transaction() as c:
        for task, by in ((world["finance_task"], "bot:finance"), (world["private"], "bot:inbox")):
            wake = c.execute("SELECT id FROM messages WHERE conversation_id=?", (task["conversation_id"],)).fetchone()[0]
            c.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) VALUES(?,?,?,?,?)",
                      (task["id"], "bot:ops", by, wake, H.shift(H.now(), hours=1)))
    assert column(api, "SELECT id FROM tasks", token) == sorted([world["coo_task"]["id"], world["finance_task"]["id"]])
    # Another person's private room stays out of reach even when named directly.
    assert query(api, "SELECT body FROM messages WHERE conversation_id=:room", token,
                 params={"room": world["steven_room"]})["rows"] == []


def test_current_docs_and_files_follow_api_visibility(api, world):
    doc = post(api, "docs", {"title": "Release guide", "body": "Read the release notes.", "path": "release-guide.md"})["doc"]
    linked = post(api, "linked-docs", {"title": "Release reference", "url": "https://example.com/release"})["linked"]
    with api.app.state.store.transaction() as c:
        for fid, scope in (("public-file", "bot"), ("ana-file", "conversation:" + world["ana_room"]),
                           ("ben-file", "conversation:" + world["steven_room"]),
                           ("task-file", "task:" + world["coo_task"]["id"])):
            c.execute("INSERT INTO bot_files(id,bot,scope,identity,title,kind,locator,first_activity_at,last_activity_at) "
                      "VALUES(?,'ops',?,?,'Release notes','document','remote_link',?,?)", (fid, scope, fid, H.now(), H.now()))
    assert column(api, "SELECT id FROM docs") == [doc["id"]]
    assert column(api, "SELECT doc_id FROM doc_versions") == [doc["id"]]
    assert column(api, "SELECT id FROM linked_docs") == [linked["id"]]
    assert column(api, "SELECT id FROM bot_files") == ["ana-file", "public-file", "task-file"]
    assert column(api, "SELECT id FROM bot_files", "ben-test") == ["ben-file", "public-file", "task-file"]
    assert column(api, "SELECT id FROM bot_files", world["attempt"]["token"]) == ["ana-file", "public-file", "task-file"]
    assert query(api, "SELECT count(*) FROM docs")["rows"] == [[1]]
    assert query(api, "SELECT count(*) FROM bot_files")["rows"] == [[3]]
    assert "prohibited" in error(api, "SELECT * FROM main.docs")
    assert "prohibited" in error(api, "SELECT * FROM main.bot_files")
    for table in ("bot_file_versions", "bot_file_activity"):
        assert query(api, "SELECT count(*) FROM " + table)["rows"] == [[0]]


def test_json_table_functions_are_read_only_and_keep_source_visibility(api, world):
    for token in (world["attempt"]["token"],):
        assert query(api, "SELECT value FROM json_each('[1,2]')", token)["rows"] == [[1], [2]]
        assert query(api, "SELECT key,value,type FROM json_tree('{\"qa\":1}') WHERE key='qa'", token)["rows"] == [["qa", 1, "integer"]]
        assert query(api, "SELECT j.value FROM messages m, json_each(m.refs_json) j "
                          "WHERE m.conversation_id=:room", token,
                     params={"room": world["steven_room"]})["rows"] == []
        assert "prohibited" in error(api, "SELECT j.value FROM main.messages m, json_each(m.refs_json) j", token)
        assert "prohibited" in error(api, "SELECT j.value FROM credentials c, json_each(c.id) j", token)
        assert "not authorized" in error(api, "SELECT load_extension('missing')", token)
    assert query(api, "SELECT count(*) FROM messages m, json_tree(m.refs_json) j",
                 world["attempt"]["token"])["rows"][0][0] >= 1
