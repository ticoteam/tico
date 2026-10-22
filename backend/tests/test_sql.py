"""POST /api/v2/sql: read-only SQL whose visibility rules live in the database layer.

The rules are the ones `backend/auth.py` applies to the JSON API, expressed once more as temp
views and an authorizer; these tests hold the two to the same answers.
"""

import json
import re
from types import SimpleNamespace

import pytest

from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, headers, post, ready, runner

SECRET_TABLES = ("credentials", "credential_keys", "credential_grants", "idempotency", "runners",
                 "enrollments", "session_epochs", "bot_session_epochs", "settings_changes", "backup_verified_blobs",
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


def test_only_one_read_statement_is_accepted(api, monkeypatch):
    for sql in REJECTED:
        detail = error(api, sql)
        assert "statement" in detail, (sql, detail)
    assert query(api, "-- a comment\n/* another */ SELECT 1 AS one")["rows"] == [[1]]
    assert query(api, "select 1 as one")["columns"] == ["one"]
    assert query(api, "EXPLAIN QUERY PLAN SELECT * FROM tasks")["columns"] == ["id", "parent", "notused", "detail"]
    assert "no such table: nope" in error(api, "SELECT * FROM nope")
    blob = query(api, "SELECT x'0102' AS b")
    # `ms` is measured: a loaded computer takes a millisecond or two.
    ms = blob.pop("ms")
    assert isinstance(ms, int) and ms >= 0, ms
    assert blob == {"columns": ["b"], "rows": [["AQI="]], "row_count": 1, "truncated": False,
                    "note": "BLOB values are base64"}
    # A query that takes 5 ms reports it.
    clock = iter(range(1000))
    monkeypatch.setattr("backend.sql.time", SimpleNamespace(monotonic=lambda: next(clock) * 0.005))
    assert query(api, "SELECT 1 AS one")["ms"] == 5


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
    commented = json.dumps(query(api, "-- comment\n/* comment */ EXPLAIN QUERY PLAN SELECT * FROM tasks"))
    assert not re.search(r"v[0-9a-f]{24}_", commented), commented


def test_owner_sees_everything_but_other_peoples_rooms(api, world):
    tasks = column(api, "SELECT id FROM tasks")
    assert tasks == sorted(world[k]["id"] for k in ("private", "coo_task", "finance_task"))
    assert "inbox" in column(api, "SELECT slug FROM bots")
    bodies = column(api, "SELECT body FROM messages")
    assert "Ana private context." in bodies and "New task from human:ana: Sort the inbox" in bodies
    assert "Ben private context." not in bodies
    personal = column(api, "SELECT id FROM conversations WHERE scope='personal'")
    assert world["ana_room"] in personal and world["steven_room"] not in personal
    # A bot's task has its own room; the chat it came from has a "Started task" line.
    rooms = column(api, "SELECT id FROM conversations WHERE kind='task'")
    assert all(world[k]["conversation_id"] in rooms for k in ("private", "coo_task", "finance_task"))
    assert "Started task: Summarize the week" in bodies
    assert column(api, "SELECT id FROM attempts") == [world["attempt"]["id"]]
    assert column(api, "SELECT id FROM turns") == [world["attempt"]["id"]]
    with api.app.state.store.read() as c:
        jobs = c.execute("SELECT count(*) FROM jobs").fetchone()[0]
    # Every queued job but the one Ben's private room raised.
    assert jobs == 5 and len(column(api, "SELECT id FROM jobs")) == 4
    # Clone and subscription profile reports have per-computer keys; the rest are fixed.
    assert column(api, "SELECT key FROM registry_metadata WHERE key NOT LIKE 'computer-repositories:%' AND key NOT LIKE 'computer-profiles:%'") == ["access", "access_bot_limit", "bot_access", "ceo-bot", "credential-file-migration-v1", "credential-file-migration-v2-hub", "docs_migrated", "idempotency_messages.ready", "inbox-only-runners", "librarian_wording35", "librarian_wording36", "message_links.ready", "onboarding", "owner", "people", "release-history", "repositories-access-migrated", "roadmap_ready_to_ship_step", "roadmaps_copies_cleared_by_field", "roadmaps_explicit_public_text", "task_session_default", "usage-count"]


def test_a_person_sees_the_company_but_not_private_bots_or_other_rooms(api, world):
    as_member(api, "ben@acme.example")
    ben = "ben-test"
    tasks = column(api, "SELECT id FROM tasks", ben)
    assert tasks == sorted(world[k]["id"] for k in ("coo_task", "finance_task"))
    assert "inbox" not in column(api, "SELECT slug FROM bots", ben)
    assert column(api, "SELECT bot FROM bot_status", ben) == sorted(
        b for b in column(api, "SELECT bot FROM bot_status") if b != "inbox")
    # Their own room, and the own rooms of the public tasks; not Ana's room or its "Started task" line.
    assert column(api, "SELECT body FROM messages", ben) == ["Ben private context.", "New task from human:ana: Close the books",
                                                             "New task from human:ana: Summarize the week"]
    assert column(api, "SELECT id FROM conversations", ben) == sorted(
        [world["steven_room"], world["coo_task"]["conversation_id"], world["finance_task"]["conversation_id"]])
    assert column(api, "SELECT id FROM attempts", ben) == []
    assert column(api, f"SELECT task_id FROM task_events WHERE task_id='{world['private']['id']}'", ben) == []
    assert column(api, f"SELECT task_id FROM task_events WHERE task_id='{world['coo_task']['id']}'", ben)


def test_a_bot_reads_ordinary_tasks_but_only_its_granted_rooms_and_no_private_data(api, world):
    token = world["attempt"]["token"]
    assert query(api, "SELECT 1", token)["rows"] == [[1]]
    bodies = column(api, "SELECT body FROM messages", token)
    # Its granted room, and the own rooms of the ordinary tasks it may read.
    assert bodies == ["Ana private context.", "New task from human:ana: Close the books",
                      "New task from human:ana: Summarize the week", "Started task: Summarize the week"]
    assert column(api, "SELECT id FROM conversations", token) == sorted(
        [world["ana_room"], world["coo_task"]["conversation_id"], world["finance_task"]["conversation_id"]])
    assert column(api, "SELECT id FROM tasks", token) == sorted([world["coo_task"]["id"], world["finance_task"]["id"]])
    assert "inbox" not in column(api, "SELECT slug FROM bots", token)
    assert column(api, "SELECT id FROM attempts", token) == [world["attempt"]["id"]]
    # Its running job, the queued one behind its own task, and the one behind a public task it may read.
    jobs = query(api, "SELECT id, bot FROM jobs", token)["rows"]
    assert len(jobs) == 3 and [world["attempt"]["job_id"], "ops"] in jobs and {row[1] for row in jobs} == {"ops", "finance"}
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


def test_materialized_visibility_matches_the_predicate_and_never_survives_a_request(api, world):
    """A private subtree, delegation or cycle must not widen a caller's task set."""
    from dataclasses import replace
    from backend import sql as S
    from backend.auth import Identity
    auth, store = api.app.state.auth, api.app.state.store
    with store.transaction() as c:
        for i, (owner, requester, private) in enumerate([
            ('bot:ops', 'human:ana', 0), ('bot:ops', 'human:ana', 1),
            ('bot:finance', 'human:cara', 1), ('bot:inbox', 'human:ana', 0),
            ('human:ben', 'human:ana', 1), ('bot:ops', 'human:cara', 0),
        ]):
            c.execute('INSERT INTO tasks(id,title,owner,requester,private,status,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                      (f'tree-{i}', 'Synthetic tree', owner, requester, private, 'open', H.now(), H.now()))
        for child, parent in [(1, 0), (2, 1), (3, 0), (4, 2), (0, 4)]:
            c.execute("INSERT INTO task_relations(from_task,to_task,kind,created,created_by) VALUES(?,?,'parent',?,?)",
                      (f'tree-{child}', f'tree-{parent}', H.now(), 'human:ana'))
        c.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires) VALUES(?,?,?,?,?)",
                  ('tree-1', 'human:cara', 'human:ana', c.execute('SELECT id FROM messages LIMIT 1').fetchone()[0],
                   H.shift(H.now(), seconds=3600)))
    callers = [Identity('human:ana', 'owner'), Identity('human:ben', 'human'),
               Identity('human:cara', 'human'), Identity('bot:ops', 'bot'), Identity('bot:doc-updater', 'bot'),
               replace(Identity('human:ana', 'owner'), task_actor='bot:ops')]
    # Use the pre-materialization predicate as the independent reference, including full rows.
    def compare(who):
        with store.read() as c:
            predicate = auth.task_sql(c, who)
            expected = json.dumps([dict(r) for r in c.execute('SELECT * FROM tasks WHERE ' + predicate + ' ORDER BY id')], sort_keys=True)
            conn = S.connect(store.settings.db_path, c, auth, who)
            try:
                found = S.run(conn, 'SELECT * FROM tasks ORDER BY id', [], 5000, 20)
                actual = json.dumps([dict(zip(found['columns'], row)) for row in found['rows']], sort_keys=True)
                assert actual == expected
                with pytest.raises(__import__("sqlite3").DatabaseError):
                    conn.execute('SELECT * FROM "' + conn.private_prefix + 'visible_tasks"').fetchall()
            finally:
                conn.close()
    for who in callers:
        compare(who)
    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner='bot:finance',requester='human:ben',private=1 WHERE id='tree-5'")
        c.execute("UPDATE bot_config SET access_json=? WHERE bot='ops'", (json.dumps({
            level: {'everyone': False, 'people': [], 'teams': [], 'bots': []} for level in ('see','read','write')}),))
    for who in callers:
        compare(who)
    # Cached columns are metadata only: schema changes cannot leave a newly added column hidden/stale.
    query(api, 'SELECT * FROM task_types')
    with store.transaction() as c:
        c.execute('ALTER TABLE task_types ADD COLUMN synthetic_fixture TEXT')
    assert 'synthetic_fixture' in query(api, 'SELECT * FROM task_types')['columns']


def test_parallel_audits_share_a_commit_without_early_ack_or_lost_context(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading
    from backend.config import Settings
    from backend.store import Store
    from backend.sql import Audit
    store = Store(Settings(db_path=tmp_path / 'synthetic.sqlite'))
    # Keep this fixture independent of a full API boot.
    with store.transaction() as c:
        c.execute('CREATE TABLE receipts(actor TEXT, detail TEXT)')
    started, release = threading.Event(), threading.Event()
    original = store.transaction
    commits = []
    @contextmanager
    def held():
        with original() as c:
            commits.append(1)
            if len(commits) == 1:
                started.set()
                assert release.wait(30)
            yield c
    store.transaction = held
    audit = Audit(store)
    from unittest.mock import patch
    def event(c, actor, action, target, detail):
        c.execute('INSERT INTO receipts VALUES(?,?)', (actor, json.dumps({**detail, 'via': H.VIA.get()})))
    def record(i):
        token = H.VIA.set(f'assistant-{i}')
        try:
            audit.record(f'human:fixture-{i}', {'i': i})
        finally:
            H.VIA.reset(token)
    with patch('backend.sql.H.event', event), ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(record, 0)]
        assert started.wait(30)
        futures.extend(pool.submit(record, i) for i in range(1, 8))
        # A condition, not a sleep: wait until all seven callers are actually queued.
        with audit.condition:
            assert audit.condition.wait_for(lambda: len(audit.pending) == 7, timeout=30)
        assert not any(f.done() for f in futures)
        release.set()
        for f in futures:
            f.result(timeout=30)
    assert len(commits) == 2
    with store.read() as c:
        receipts = {r[0]: json.loads(r[1]) for r in c.execute('SELECT * FROM receipts')}
    assert receipts == {f'human:fixture-{i}': {'i': i, 'via': f'assistant-{i}'} for i in range(8)}
    @contextmanager
    def failed():
        with original() as c:
            yield c
            raise RuntimeError('synthetic commit failure')
    store.transaction = failed
    with patch('backend.sql.H.event', event), pytest.raises(RuntimeError, match='synthetic commit failure'):
        record(9)
    with store.read() as c:
        assert c.execute('SELECT count(*) FROM receipts').fetchone()[0] == 8


def test_a_granted_bot_reads_outcome_totals_but_no_proposal_row_and_cannot_write(api, world):
    from backend import assistant
    from backend.tests.test_api import setup_attempt
    with api.app.state.store.transaction() as c:
        assistant.ensure_schema(c)
        for n, (status, proposer) in enumerate([("done", "assistant"), ("cancelled", "assistant"), ("done", "bot:botops")]):
            c.execute("INSERT INTO assistant_actions(id,owner,summary,method,path,body_json,status,created,proposer) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (f"a{n}", "human:ana", "Pay Dana's invoice", "POST",
                                                    "/api/v2/tasks/xyz/close", '{"note":"secret"}', status, H.now(), proposer))
        H.event(c, "human:ana", "mcp.call", "hub_task_show", {"tool": "hub_task_show", "ms": 40, "via": "ana-laptop"})
    token = world["attempt"]["token"]
    totals = "SELECT status, sum(actions) FROM assistant_action_weekly GROUP BY status"
    # Without the grant the bot has neither the totals nor the rows.
    assert "no such table" in error(api, totals, token)
    assert "prohibited" in error(api, "SELECT status FROM assistant_actions", token)
    assert api.get("/api/v2/live/stats", headers=headers(token)).status_code == 403
    # Only the owner, signed in, grants it; the bot cannot grant itself.
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": True}, headers=headers(token)).status_code == 403
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": True}, headers=headers("ana-test")).json() == {"readers": ["ops"]}
    assert query(api, totals, token)["rows"] == [["cancelled", 1], ["done", 2]]
    assert query(api, "SELECT tool, calls FROM mcp_call_weekly", token)["rows"] == [["hub_task_show", 1]]
    stats = api.get("/api/v2/live/stats", headers=headers(token))
    assert stats.status_code == 200 and stats.json()["tools"][0]["tool"] == "hub_task_show"
    assert api.get("/api/v2/live/stats?via=ana-laptop", headers=headers(token)).status_code == 403
    # Still no row: not the table, not a hidden column, not the base events the totals are built from.
    for sql in ("SELECT * FROM assistant_actions", "SELECT summary FROM assistant_action_weekly",
                "SELECT owner FROM assistant_action_weekly", "SELECT detail_json FROM mcp_call_weekly"):
        assert query(api, sql, token, expected=422)
    assert query(api, "DELETE FROM assistant_actions", token, expected=422)
    # A grant to one bot never opens totals, rows or writes to another bot.
    _, _, other = setup_attempt(api, bot="finance")
    assert "no such table" in error(api, totals, other["token"])
    assert "no such table" in error(api, "SELECT * FROM mcp_call_weekly", other["token"])
    assert "prohibited" in error(api, "SELECT * FROM assistant_actions", other["token"])
    assert api.get("/api/v2/live/stats", headers=headers(other["token"])).status_code == 403
    assert api.put("/api/v2/outcome-stats/readers/finance", json={"read": True},
                   headers=headers(other["token"])).status_code == 403
    # Revoking takes it away at once.
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": False}, headers=headers("ana-test")).json() == {"readers": []}
    assert "no such table" in error(api, totals, token)
