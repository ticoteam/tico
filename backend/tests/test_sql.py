"""POST /api/v2/sql: read-only SQL whose visibility rules live in the database layer.

The rules are the ones `backend/auth.py` applies to the JSON API, expressed once more as temp
views and an authorizer; these tests hold the two to the same answers.
"""

import json
import re
from pathlib import Path
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


def test_sql_private_task_inventory_reuses_snapshot_rights_and_sees_revocation(api, monkeypatch):
    from backend import sql as S, views
    from backend.auth import Identity
    from backend.tests.test_api import unrelated_task_managers

    store, auth = api.app.state.store, api.app.state.auth
    who = Identity("human:cara", "human", email="cara@acme.example")
    with store.transaction() as c:
        unrelated_task_managers(c)
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='finance'")
    builds, roster = [], views._roster

    def counted(c):
        builds.append(type(c).__name__)
        return roster(c)

    monkeypatch.setattr(views, "_roster", counted)
    expected, guests, counts = set(), set(), {}

    def open_read():
        statements, before = [], len(builds)
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, who, trace=statements.append)
        return conn, statements, len(builds) - before

    def ids(conn):
        return {row[0] for row in S.run(conn, "SELECT id FROM tasks", [], 500, 5)["rows"]}

    for start, size in ((0, 3), (3, 30)):
        with store.transaction() as c:
            for n in range(start, size):
                row = H.task_create(c, "human:ben", f"Private source {n}", "Secret", "bot:finance" if n % 3 == 0
                                    else "bot:ops", private=True, lint=False)
                if n % 3 == 0:
                    expected.add(row["id"])
                elif n % 3 == 2:
                    c.execute("UPDATE tasks SET guest='human:cara' WHERE id=?", (row["id"],))
                    expected.add(row["id"])
                    guests.add(row["id"])
        conn, statements, built = open_read()
        try:
            assert ids(conn) == expected
            counts[size] = len(statements), built
        finally:
            conn.close()
    print(f"SQL private tasks 3 -> 30: statements/builds {counts[3]} -> {counts[30]}")
    assert counts[30][0] - counts[3][0] <= 8, counts
    assert counts[30][1] <= counts[3][1] <= 2, counts

    # One in-flight WAL snapshot keeps its rights; a new request must observe the changed org chart.
    held, _, _ = open_read()
    try:
        assert ids(held) == expected
        with store.transaction() as c:
            c.execute("UPDATE bot_config SET reports_to='human:privacy-lead' WHERE bot='finance'")
        assert ids(held) == expected
        fresh, _, _ = open_read()
        try:
            assert ids(fresh) == guests
        finally:
            fresh.close()
    finally:
        held.close()


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
    private = post(api, "tasks", {"owner_id": "inbox", "title": "Sort the inbox", "body": "Private to Ana."})
    coo_task = post(api, "tasks", {"owner_id": "ops", "title": "Summarize the week", "body": "For ops."})
    finance_task = post(api, "tasks", {"owner_id": "finance", "title": "Close the books", "body": "For finance."})
    return {"ana_room": ana_room, "steven_room": steven_room, "machine": machine, "attempt": attempt,
            "private": private, "coo_task": coo_task, "finance_task": finance_task}


def test_the_sql_page_examples_all_run(api):
    page = (Path(__file__).resolve().parents[2] / "ui/app/sql.js").read_text()
    examples = re.findall(r"""^  \['[^']+', "(.+)"\],?$""", page, re.M)
    assert len(examples) >= 6
    for sql in examples:
        query(api, sql)


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


def test_recruiting_seats_are_readable_by_everyone_like_the_org_chart(api, world):
    seat = post(api, "seats", {"title": "Bookkeeper", "description": "Keeps the ledgers.", "reports_to": "finance"})
    for token in ("ana-test", "cara-test", world["attempt"]["token"]):
        assert query(api, "SELECT title,reports_to FROM seats WHERE closed_at IS NULL", token)["rows"] == [
            ["Bookkeeper", "finance"]], token
        assert column(api, "SELECT count(*) FROM seats", token) == [1]
    assert seat["id"]


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
    assert column(api, "SELECT key FROM registry_metadata WHERE key NOT LIKE 'computer-repositories:%' AND key "
                       "NOT LIKE 'computer-profiles:%'") == [
        "access", "access_bot_limit", "bot_access", "ceo-bot", "company-turn-incident",
        "credential-file-migration-v1", "credential-file-migration-v2-hub", "credential-use-backfill", "docs_migrated",
        "idempotency_messages.ready", "inbox-only-runners", "librarian_wording35", "librarian_wording36",
        "message_links.ready", "onboarding", "owner", "people", "redesign_cutover", "release-history",
        "repositories-access-migrated", "roadmap_ready_to_ship_step", "roadmaps_copies_cleared_by_field",
        "roadmaps_explicit_public_text", "strategy-bot", "task_session_default", "usage-count"]


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
    jobs = query(api, "SELECT id, bot_id FROM jobs", token)["rows"]
    assert len(jobs) == 3 and [world["attempt"]["job_id"], "ops"] in jobs and {row[1] for row in jobs} == {"ops", "finance"}
    assert column(api, "SELECT key FROM registry_metadata", token) == []
    assert column(api, "SELECT DISTINCT actor FROM events", token) in ([], ["bot:ops"])
    # Delegations do not widen the already readable ordinary task set or the hidden bot's activity.
    with api.app.state.store.transaction() as c:
        for task, by in ((world["finance_task"], "bot:finance"), (world["private"], "bot:inbox")):
            wake = c.execute("SELECT id FROM messages WHERE conversation_id=?", (task["conversation_id"],)).fetchone()[0]
            c.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) VALUES(?,?,?,?,?)",
                      (task["id"], "bot:ops", by, wake, H.shift(H.now(), hours=1)))
    assert column(api, "SELECT id FROM tasks", token) == sorted([world["coo_task"]["id"], world["finance_task"]["id"]])
    # Another person's private room stays out of reach even when named directly.
    assert query(api, "SELECT body FROM messages WHERE conversation_id=:room", token,
                 params={"room": world["steven_room"]})["rows"] == []


def test_history_lookups_keep_legacy_visibility_without_scanning_messages(api, world, monkeypatch):
    from backend import sql as S
    from backend.auth import Identity
    from dataclasses import replace

    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        c.execute('UPDATE tasks SET private=1 WHERE id=?', (world['private']['id'],))
        def clone(table, original, **patch):
            row = dict(c.execute(f'SELECT * FROM {table} WHERE id=?', (original,)).fetchone())
            row.update(patch)
            c.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})',
                      list(row.values()))

        original = world['attempt']
        mid = c.execute('SELECT message_id FROM jobs WHERE id=?', (original['job_id'],)).fetchone()[0]
        for name, patch in {
            'public': {'conversation_id': world['coo_task']['conversation_id'], 'refs': '{}'},
            'private-room': {'conversation_id': world['private']['conversation_id'], 'refs': '{}'},
            'private-ref': {'refs': json.dumps({'task': world['private']['id']})},
            'deleted': {'deleted_at': H.now()},
            'unknown-task': {'refs': json.dumps({'task': 'unknown-fixture-task'})},
        }.items():
            clone('messages', mid, id='message-' + name, recipient_id='human:ana', **patch)
            clone('jobs', original['job_id'], id='job-' + name, message_id='message-' + name)
            clone('attempts', original['id'], id='attempt-' + name, job_id='job-' + name,
                  token_hash='fixture-' + name, state='completed', lane='fixture-' + name)
            clone('turns', original['id'], id='attempt-' + name, message_id='message-' + name, task_id=None)

    guarded = S.guarded
    replacements = {
        'EXISTS (SELECT 1 FROM messages readable WHERE readable.id=source.message_id)':
            'message_id IN (SELECT id FROM messages)',
        'EXISTS (SELECT 1 FROM jobs readable WHERE readable.id=source.job_id)':
            'job_id IN (SELECT id FROM jobs)',
        'EXISTS (SELECT 1 FROM attempts readable WHERE readable.id=source.attempt_id)':
            'attempt_id IN (SELECT id FROM attempts)',
        'EXISTS (SELECT 1 FROM conversations readable WHERE readable.id=source.conversation_id)':
            'conversation_id IN (SELECT id FROM conversations)',
    }

    def legacy(*args, **kwargs):
        rules = guarded(*args, **kwargs)
        for table, (predicate, hidden) in rules.items():
            for new, old in replacements.items():
                predicate = predicate.replace(new, old)
            rules[table] = predicate, hidden
        return rules

    callers = [Identity('human:ana', 'owner'), Identity('human:ben', 'human'),
               Identity('bot:ops', 'bot', attempt_id=world['attempt']['id']),
               replace(Identity('human:ana', 'owner'), task_actor='bot:ops')]
    for who in callers:
        answers = []
        for rules in (legacy, guarded):
            monkeypatch.setattr(S, 'guarded', rules)
            with store.read() as c:
                conn = S.connect(store.settings.db_path, c, auth, who)
                try:
                    answers.append({table: S.run(conn, f'SELECT id FROM {table} ORDER BY id', [], 5000, 5)['rows']
                                    for table in ('messages', 'jobs', 'attempts', 'turns')})
                finally:
                    conn.close()
        assert answers[0] == answers[1], who
        for table in ('attempts', 'turns'):
            expected = {'attempt-public'}
            if who.actor == 'human:ana' and not who.task_actor:
                expected.update(('attempt-private-room', 'attempt-private-ref'))
            assert {row[0] for row in answers[1][table] if row[0].startswith('attempt-')} == expected, who
        if who.role == 'bot':
            assert ['attempt-public'] in answers[1]['turns']
            for name in ('private-room', 'private-ref', 'deleted', 'unknown-task'):
                assert ['attempt-' + name] not in answers[1]['turns']
                assert ['attempt-' + name] not in answers[1]['attempts']

    # Unfiltered LIMIT 1 must stop before authorizing the rest of history.
    # The keyed case separately checks that nested views use candidate lookups.
    # A row the SQL gate settles never reaches the Python check, so at most one call.
    checked = []
    message = S.ReadIndex.message

    def counted(index, *args):
        checked.append(args[0])
        return message(index, *args)

    monkeypatch.setattr(S.ReadIndex, 'message', counted)
    for table in ('turns', 'attempts'):
        checked.clear()
        assert len(query(api, f'SELECT id FROM {table} LIMIT 1', world['attempt']['token'])['rows']) == 1
        assert len(checked) <= 1
        checked.clear()
        assert query(api, f'SELECT id FROM {table} WHERE id=:id LIMIT 1', world['attempt']['token'],
                     params={'id': 'attempt-public'})['rows'] == [['attempt-public']]
        assert checked in ([], ['message-public'])


def test_direct_messages_reuse_room_checks_and_match_recursive_privacy(api, world, monkeypatch):
    from collections import Counter
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    store = api.app.state.store
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden direct source', '', 'bot:finance', private=True, lint=False)
    who = Identity('bot:ops', 'bot', attempt_id=world['attempt']['id'])
    room = world['ana_room']
    public = world['coo_task']['id']
    cases = [
        ({'task': public}, True),
        ({'task': [public]}, True),
        ({'task': hidden['id']}, False),
        ({'task': 'unknown-fixture-task'}, False),
        ({'payload': {'source': 'task:' + hidden['id']}}, False),
        ({'payload': 'https://synthetic.invalid/#/task/' + hidden['id']}, False),
        ({'payload': json.dumps({'nested': hidden['id']})}, False),
        ({'payload': 'x' * 5000 + '/tasks/' + hidden['id']}, False),
        ({'live_meetings': ['unknown-fixture-meeting']}, False),
        (['unsupported'], False),
        ({'inputs': ['missing-fixture-message'], 'task': public}, True),
        ({'turn_id': world['attempt']['id']}, True),
    ]
    with store.read() as c:
        fast, recursive = ReadIndex(c, who), ReadIndex(c, who)
        recursive.direct_message = lambda *args: None
        for i, (refs, expected) in enumerate(cases):
            args = (f'direct-fixture-{i}', room, json.dumps(refs), None)
            assert bool(fast.message(*args)) == bool(recursive.message(*args)) == expected, refs

        checks = Counter()
        # A new request has no previously authorized rooms.
        fast = ReadIndex(c, who)
        readable = fast.meetings.conversation_readable
        def counted(actor, cid):
            checks[cid] += 1
            return readable(actor, cid)
        monkeypatch.setattr(fast.meetings, 'conversation_readable', counted)
        for i in range(100):
            cid = (room, world['coo_task']['conversation_id'])[i % 2]
            # Distinct refs prevent a per-message cache hiding repeated room work.
            assert fast.message(f'progress-{i}', cid, json.dumps({'task': public, 'progress': i}), None)
            assert fast.conversation(cid)  # SQL's separate room predicate
        assert checks == {room: 1, world['coo_task']['conversation_id']: 1}

    # A fresh request must rebuild decisions after a task becomes private.
    with store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,owner_id='bot:finance',requester_id='human:ben' WHERE id=?", (public,))
    with store.read() as c:
        assert not ReadIndex(c, who).message('progress-after-revocation', room, json.dumps({'task': public}), None)


def test_direct_content_prefilter_keeps_escaped_and_nested_provenance(api, world):
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    store = api.app.state.store
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden escaped source', '', 'bot:finance', private=True, lint=False)
    escaped = ''.join('\\u%04x' % ord(char) for char in hidden['id'])
    payloads = [
        json.dumps({'payload': {'nested': ['task:' + hidden['id']]}}),
        json.dumps({'payload': 'https://synthetic.invalid/tasks/' + hidden['id']}),
        json.dumps({'payload': hidden['id']}).replace(hidden['id'], escaped),
        json.dumps({'payload': json.dumps({'nested': hidden['id']})}),
        json.dumps({'payload': {'live_meeting': {'meeting_id': 'unknown-fixture-meeting'}}}),
        '{"payload":{"live_\\u006deeting":{"meeting_id":"unknown-fixture-meeting"}}}',
        json.dumps({'payload': [json.dumps({'live_meetings': ['unknown-fixture-meeting']})]}),
    ]
    with store.read() as c:
        who = Identity('bot:ops', 'bot', attempt_id=world['attempt']['id'])
        fast, recursive = ReadIndex(c, who), ReadIndex(c, who)
        recursive.direct_message = lambda *args: None
        for refs in payloads:
            args = ('synthetic-encoded', world['ana_room'], refs, None)
            assert not recursive.message(*args), refs
            assert not fast.message(*args), refs
        # Noncanonical task ids keep the structured authorization path.
        fast = ReadIndex(c, who)
        fast.uuid_denied = False
        for refs in payloads:
            assert not fast.message('synthetic-uncached', world['ana_room'], refs, None), refs


def test_direct_reply_gate_matches_recursive_sources_and_keeps_depth_bound(api, world):
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    store = api.app.state.store
    room, public = world['ana_room'], world['coo_task']['id']
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden reply source', '', 'bot:finance', private=True, lint=False)
        parents = [H.say(c, 'human:ana', 'human:ben', 'Synthetic parent', conversation_id=room, refs=refs)
                   for refs in ({'task': public}, {'task': hidden['id']}, {'task': 'unknown-fixture-task'},
                                {'live_meetings': ['unknown-fixture-meeting']}, {'turn_id': world['attempt']['id']})]
        chain = []
        for i in range(100):
            chain.append(H.say(c, 'human:ana', 'human:ben', 'Synthetic chain', conversation_id=room,
                               in_reply_to=chain[-1]['id'] if chain else None))
    with store.read() as c:
        who = Identity('bot:ops', 'bot', attempt_id=world['attempt']['id'])
        fast, recursive = ReadIndex(c, who), ReadIndex(c, who)
        recursive.direct_message = lambda *args: None
        for parent, expected in zip(parents, (True, False, False, False, True)):
            args = ('synthetic-reply', room, json.dumps({'task': public}), parent['id'])
            assert bool(fast.message(*args)) == bool(recursive.message(*args)) == expected
        for parent, expected in ((chain[98], True), (chain[99], False)):
            args = ('synthetic-depth', room, json.dumps({'task': public}), parent['id'])
            assert bool(fast.message(*args)) == bool(recursive.message(*args)) == expected


@pytest.mark.parametrize("source", ["input", "carried", "event"])
def test_direct_run_messages_keep_denied_attempt_provenance(api, world, source):
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    store, aid = api.app.state.store, world['attempt']['id']
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden run source', '', 'bot:finance', private=True, lint=False)
        if source == 'input':
            msg = H.say(c, 'human:ben', 'human:ana', 'Synthetic input', conversation_id=world['ana_room'],
                        refs={'task': hidden['id']})
            c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)', (aid, msg['id']))
        elif source == 'carried':
            c.execute('UPDATE tasks SET carried_by=? WHERE id=?', (aid, hidden['id']))
        else:
            H.event(c, H.KEEPER, 'task.next-run.carried', aid, {'tasks': [hidden['id']]})
    with store.read() as c:
        who = Identity('bot:ops', 'bot', attempt_id=aid)
        fast, recursive = ReadIndex(c, who), ReadIndex(c, who)
        recursive.direct_message = lambda *args: None
        recursive._direct_attempt = lambda *args: None
        assert not fast.attempt(aid)
        assert not recursive.attempt(aid)
        for refs in ({'turn_id': aid}, {'run': {'attempt_id': aid}}):
            args = ('synthetic-run-message', world['ana_room'], json.dumps(refs), None)
            assert not fast.message(*args)
            assert not recursive.message(*args)


@pytest.mark.parametrize("unknown", [False, True])
def test_direct_refs_cannot_override_denied_conversation_task(api, world, unknown):
    from backend.auth import Identity
    from backend.privacy_index import ReadIndex

    store, room = api.app.state.store, world['ana_room']
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden conversation source', '', 'bot:finance', private=True, lint=False)
        c.execute('UPDATE conversations SET task_id=? WHERE id=?',
                  ('unknown-fixture-task' if unknown else hidden['id'], room))
    # The attempt's room grant is still valid, isolating the provenance denial.
    assert query(api, 'SELECT id FROM conversations WHERE id=?', world['attempt']['token'], params=[room])['rows'] == [[room]]
    with store.read() as c:
        who = Identity('bot:ops', 'bot', attempt_id=world['attempt']['id'])
        fast, recursive = ReadIndex(c, who), ReadIndex(c, who)
        recursive.direct_message = lambda *args: None
        for refs in ({'task': world['coo_task']['id']}, {'task_id': world['coo_task']['id']}, {'progress': 1}):
            args = ('synthetic-room-task', room, json.dumps(refs), None)
            assert not fast.message(*args)
            assert not recursive.message(*args)


def test_attempt_time_index_upgrades_existing_history_without_changing_rows(api, world):
    store = api.app.state.store
    with store.transaction() as c:
        before = [dict(row) for row in c.execute('SELECT * FROM attempts ORDER BY id')]
        c.execute('DROP INDEX attempts_created')
        c.execute('DELETE FROM cloud_migrations WHERE version=62')
    store.initialize()
    store.initialize()  # Existing installs and repeated starts take the same additive path.
    with store.read() as c:
        assert [dict(row) for row in c.execute('SELECT * FROM attempts ORDER BY id')] == before
        assert [row[2] for row in c.execute('PRAGMA index_info(attempts_created)')] == ['created_at']
        assert c.execute('SELECT count(*) FROM cloud_migrations WHERE version=62').fetchone()[0] == 1
        plan = c.execute("EXPLAIN QUERY PLAN SELECT count(*) FROM attempts WHERE created_at>=?", (H.now(),))
        assert any('attempts_created (created_at>?)' in row[3] for row in plan)


def test_attempt_provenance_uses_keyed_inputs_and_retained_run_events(api, world, monkeypatch):
    from backend import sql as S, task_privacy
    from backend.auth import Identity

    def no_history(index):
        raise AssertionError('SQL candidate lookup loaded all attempts')

    monkeypatch.setattr(S.ReadIndex, 'attempts', no_history)
    store, auth = api.app.state.store, api.app.state.auth
    aid = world['attempt']['id']
    who = Identity('bot:ops', 'bot', attempt_id=aid)
    # The initial run is public to this caller; its only private source arrives
    # as an input, then survives solely in the moved-input event after removal.
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden fixture', '', 'bot:finance', private=True, lint=False)
        msg = H.say(c, 'human:ben', 'human:ana', 'Hidden input',
                    conversation_id=world['ana_room'], refs={'task': hidden['id']})

    def readable():
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, who)
            try:
                return S.run(conn, 'SELECT id FROM attempts WHERE id=?', [aid], 500, 5)['rows']
            finally:
                conn.close()

    assert readable() == [[aid]]
    with store.transaction() as c:
        c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)', (aid, msg['id']))
    assert readable() == []
    with store.transaction() as c:
        task_privacy.record_moved_input(c, aid, 'other-fixture-run', msg)
        c.execute('DELETE FROM attempt_inputs WHERE message_id=?', (msg['id'],))
    assert readable() == []


@pytest.mark.parametrize("source", ["input", "carried", "event"])
def test_bot_replies_whose_run_names_a_denied_task_stay_out_of_full_scans(api, world, source):
    # A bot's reply in a public task room, shaped as execution.stamp_reply records it, names its run and the
    # message it answers. The private task reaches it only through that run, which unkeyed scans must still see.
    store, room, aid = api.app.state.store, world['coo_task']['conversation_id'], 'attempt-reply-fixture'
    with store.transaction() as c:
        def clone(table, original, **patch):
            row = dict(c.execute(f'SELECT * FROM {table} WHERE id=?', (original,)).fetchone())
            row.update(patch)
            c.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})',
                      list(row.values()))

        job = world['attempt']['job_id']
        asked = c.execute('SELECT message_id FROM jobs WHERE id=?', (job,)).fetchone()[0]
        clone('messages', asked, id='message-asked-fixture', conversation_id=room, refs='{}', in_reply_to=None)
        queued = c.execute("SELECT id FROM jobs WHERE message_id='message-asked-fixture'").fetchone()
        if not queued:
            clone('jobs', job, id=aid, message_id='message-asked-fixture')
        job = queued[0] if queued else aid
        clone('attempts', world['attempt']['id'], id=aid, job_id=job, token_hash='fixture-reply', state='completed',
              lane='fixture-reply')
        clone('turns', world['attempt']['id'], id=aid, message_id='message-asked-fixture', task_id=None)
        clone('messages', asked, id='message-reply-fixture', conversation_id=room, sender_id='bot:ops',
              recipient_id='human:ana', in_reply_to='message-asked-fixture',
              refs=json.dumps({'turn_id': aid, 'task': world['coo_task']['id'],
                                    'run': {'job_id': job, 'attempt_id': aid}, 'answers': ['message-asked-fixture']}))
    callers = (world['attempt']['token'], 'ben-test')

    def seen(token):
        ids = {row[0] for row in query(api, 'SELECT id FROM messages', token)['rows']}
        assert query(api, 'SELECT count(*) FROM messages', token)['rows'] == [[len(ids)]]
        runs = {row[0] for table in ('attempts', 'turns')
                for row in query(api, f'SELECT id FROM {table}', token)['rows']}
        return 'message-reply-fixture' in ids, aid in runs

    for token in callers:
        assert seen(token) == (True, True), token
    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ana', 'Hidden run source', '', 'bot:inbox', private=True, lint=False)
        if source == 'input':
            msg = H.say(c, 'human:ana', 'bot:inbox', 'Synthetic input', conversation_id=room,
                        refs={'task': hidden['id']})
            c.execute('INSERT INTO attempt_inputs(attempt_id,message_id) VALUES(?,?)', (aid, msg['id']))
        elif source == 'carried':
            c.execute('UPDATE tasks SET carried_by=? WHERE id=?', (aid, hidden['id']))
        else:
            H.event(c, H.KEEPER, 'task.next-run.carried', aid, {'bot': 'ops', 'tasks': [hidden['id']]})
    for token in callers:
        assert seen(token) == (False, False), token


def test_carried_private_tasks_and_unknown_plain_rooms_stay_hidden(api, world):
    from backend import sql as S
    from backend.auth import Identity

    store, auth = api.app.state.store, api.app.state.auth
    aid = world['attempt']['id']
    who = Identity('bot:ops', 'bot', attempt_id=aid)

    def rows(table, rid):
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, who)
            try:
                return S.run(conn, f'SELECT id FROM {table} WHERE id=?', [rid], 500, 5)['rows']
            finally:
                conn.close()

    with store.transaction() as c:
        hidden = H.task_create(c, 'human:ben', 'Hidden carried fixture', '', 'bot:finance',
                               private=True, lint=False)
    assert rows('attempts', aid) == [[aid]]
    assert rows('turns', aid) == [[aid]]

    # The job message stays readable; only the carried task hides this run.
    with store.transaction() as c:
        c.execute('UPDATE tasks SET carried_by=? WHERE id=?', (aid, hidden['id']))
    assert rows('attempts', aid) == []
    assert rows('turns', aid) == []
    with store.transaction() as c:
        c.execute('UPDATE tasks SET carried_by=NULL WHERE id=?', (hidden['id'],))
    assert rows('attempts', aid) == [[aid]]

    # Retained next-run provenance survives independently of tasks.carried_by.
    with store.transaction() as c:
        event = H.event(c, H.KEEPER, 'task.next-run.carried', aid,
                        {'bot': 'ops', 'tasks': [hidden['id']]})
    assert rows('attempts', aid) == []
    assert rows('turns', aid) == []
    with store.transaction() as c:
        c.execute('DELETE FROM events WHERE id=?', (event['id'],))
    assert rows('attempts', aid) == [[aid]]

    with store.transaction() as c:
        msg = H.say(c, 'human:ana', 'bot:ops', 'Plain room fixture',
                    conversation_id=world['ana_room'])
    assert rows('messages', msg['id']) == [[msg['id']]]
    with store.transaction() as c:
        c.execute('UPDATE conversations SET task_id=? WHERE id=?',
                  (hidden['id'], world['ana_room']))
    # A valid room grant cannot expose plain messages from a denied private task.
    assert rows('conversations', world['ana_room']) == [[world['ana_room']]]
    assert rows('messages', msg['id']) == []
    with store.transaction() as c:
        c.execute('UPDATE conversations SET task_id=? WHERE id=?',
                  ('unknown-fixture-task', world['ana_room']))
    # The room grant remains valid, so the plain-message provenance gate must deny.
    assert rows('conversations', world['ana_room']) == [[world['ana_room']]]
    assert rows('messages', msg['id']) == []


def test_nested_udf_interrupt_is_timeout_and_other_udf_errors_remain_sql(monkeypatch):
    import sqlite3
    from backend import sql as S
    from backend.store import Problem

    conn = sqlite3.connect(':memory:', factory=S.ReadConnection)
    conn.private_prefix = 'fixture_'
    clock = [0.0]
    monkeypatch.setattr(S, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    def nested():
        clock[0] = 10
        return conn.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000) '
                            'SELECT sum(x) FROM n').fetchone()[0]

    conn.create_function('nested', 0, nested)
    conn.create_function('broken', 0, lambda: 1 / 0)
    try:
        with pytest.raises(Problem) as failure:
            S.run(conn, 'SELECT nested()', [], 500, 5)
        assert failure.value.code == 'timeout'
        assert 'user-defined function' in str(failure.value.__cause__)
        with pytest.raises(Problem) as failure:
            S.run(conn, 'SELECT broken()', [], 500, 5)
        assert failure.value.code == 'sql'
        assert S.run(conn, 'SELECT 1', [], 500, 5)['rows'] == [[1]]
    finally:
        conn.close()


def test_current_docs_and_files_follow_api_visibility(api, world):
    doc = post(api, "docs", {"title": "Release guide", "body": "Read the release notes.", "path": "release-guide.md"})["doc"]
    linked = post(api, "linked-docs", {"title": "Release reference", "url": "https://example.com/release"})["linked"]
    with api.app.state.store.transaction() as c:
        for fid, scope in (("public-file", "bot"), ("ana-file", "conversation:" + world["ana_room"]),
                           ("ben-file", "conversation:" + world["steven_room"]),
                           ("task-file", "task:" + world["coo_task"]["id"])):
            c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,first_activity_at,last_activity_at) "
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
        assert query(api, "SELECT j.value FROM messages m, json_each(m.refs) j "
                          "WHERE m.conversation_id=:room", token,
                     params={"room": world["steven_room"]})["rows"] == []
        assert "prohibited" in error(api, "SELECT j.value FROM main.messages m, json_each(m.refs) j", token)
        assert "prohibited" in error(api, "SELECT j.value FROM credentials c, json_each(c.id) j", token)
        assert "not authorized" in error(api, "SELECT load_extension('missing')", token)
    assert query(api, "SELECT count(*) FROM messages m, json_tree(m.refs) j",
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
            c.execute('INSERT INTO tasks(id,title,owner_id,requester_id,private,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                      (f'tree-{i}', 'Synthetic tree', owner, requester, private, 'open', H.now(), H.now()))
        for child, parent in [(1, 0), (2, 1), (3, 0), (4, 2), (0, 4)]:
            c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at,created_by) VALUES(?,?,'parent',?,?)",
                      (f'tree-{child}', f'tree-{parent}', H.now(), 'human:ana'))
        c.execute("INSERT INTO task_delegations(task_id,delegate,requested_by,message_id,expires_at) VALUES(?,?,?,?,?)",
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
        c.execute("UPDATE tasks SET owner_id='bot:finance',requester_id='human:ben',private=1 WHERE id='tree-5'")
        c.execute("UPDATE bot_config SET access=? WHERE bot='ops'", (json.dumps({
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
    from backend.tests.test_mcp import call, rpc
    with api.app.state.store.transaction() as c:
        assistant.ensure_schema(c)
        for n, (status, proposer) in enumerate([("done", "assistant"), ("cancelled", "assistant"), ("done", "bot:botops")]):
            c.execute("INSERT INTO assistant_actions(id,owner,summary,method,path,body,status,created_at,proposer) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (f"a{n}", "human:ana", "Pay Dana's invoice", "POST",
                                                    "/api/v2/tasks/xyz/close", '{"note":"secret"}', status, H.now(), proposer))
        # The same status across owners/routes/methods must remain one approved-grain bucket.
        c.execute("INSERT INTO assistant_actions(id,owner,summary,method,path,body,status,created_at,proposer) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", ("a3", "human:dana", "Private category", "DELETE",
                                                "/api/v2/meetings/xyz", '{}', "done", H.now(), "assistant"))
        H.event(c, "bot:ops", "mcp.call", "hub_task_show", {"tool": "hub_task_show", "ms": 40})
        H.event(c, "human:ana", "mcp.call", "hub_assistant_send",
                {"tool": "hub_assistant_send", "ms": 900, "via": "ana-laptop"})
    token = world["attempt"]["token"]
    totals = "SELECT status, sum(actions) FROM assistant_action_weekly GROUP BY status"
    # Without the grant the bot has neither the totals nor the rows.
    assert "no such table" in error(api, totals, token)
    assert "prohibited" in error(api, "SELECT status FROM assistant_actions", token)
    assert api.get("/api/v2/live/stats", headers=headers(token)).status_code == 403
    # Only the owner, signed in, grants it; the bot cannot grant itself.
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": True}, headers=headers(token)).status_code == 403
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": True}, headers=headers("ana-test")).json() == {"readers": ["ops"]}
    assert query(api, totals, token)["rows"] == [["cancelled", 1], ["done", 3]]
    buckets = query(api, "SELECT proposer, status, actions, share_pct FROM assistant_action_weekly "
                    "ORDER BY proposer, status", token)["rows"]
    assert buckets == [["assistant", "cancelled", 1, 33.3], ["assistant", "done", 2, 66.7],
                       ["bot:botops", "done", 1, 100.0]]
    assert query(api, "SELECT tool, calls FROM mcp_call_weekly", token)["rows"] == [["hub_task_show", 1]]
    stats = api.get("/api/v2/live/stats", headers=headers(token))
    assert stats.status_code == 200
    assert {r["tool"]: r["calls"] for r in stats.json()["tools"]} == {"hub_task_show": 1}
    # People keep their existing all-caller stats; the narrow grant cannot borrow them.
    owner_stats = api.get("/api/v2/live/stats", headers=headers()).json()["tools"]
    assert {r["tool"]: r["calls"] for r in owner_stats} == {"hub_task_show": 1, "hub_assistant_send": 1}
    # Failed MCP calls record caller-typed names too, even when they resemble a hub name.
    for name in ("Ana wants to pay Dana's private invoice", "hub_ana_private_invoice"):
        assert rpc(api, "tools/call", {"name": name, "arguments": {}}, token=token)["error"]["code"] == -32602
    assert query(api, "SELECT tool, calls FROM mcp_call_weekly ORDER BY tool", token)["rows"] == [
        ["hub_task_show", 1], ["other", 2]]
    stats = api.get("/api/v2/live/stats", headers=headers(token))
    assert stats.status_code == 200
    assert {r["tool"]: r["calls"] for r in stats.json()["tools"]} == {"hub_task_show": 1, "other": 2}
    failed, via_mcp = call(api, "hub_mcp_stats", token=token)
    assert not failed and via_mcp["tools"] == stats.json()["tools"]
    assert api.get("/api/v2/live/stats?via=ana-laptop", headers=headers(token)).status_code == 403
    # Still no row: not the table, not a hidden column, not the base events the totals are built from.
    for sql in ("SELECT * FROM assistant_actions", "SELECT summary FROM assistant_action_weekly",
                "SELECT owner FROM assistant_action_weekly", "SELECT area FROM assistant_action_weekly",
                "SELECT method FROM assistant_action_weekly", "SELECT detail FROM mcp_call_weekly"):
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
    assert "no such table" in error(api, "SELECT * FROM mcp_call_weekly", token)
    assert api.get("/api/v2/live/stats", headers=headers(token)).status_code == 403


def test_outcome_grants_require_owner_sign_in_and_delegation_does_not_lend_owner_reads(api):
    import sqlite3
    from backend import assistant, sql as S
    from backend.tests.test_assistant import assistant_turn

    # Mint a real personal token, so authentication sets via_token rather than a test identity shortcut.
    personal = post(api, "me/tokens", {"label": "synthetic owner script"})["token"]
    assert api.get("/api/v2/me", headers=headers(personal)).json()["role"] == "owner"
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": True},
                   headers=headers(personal)).status_code == 403
    assert api.get("/api/v2/outcome-stats/readers", headers=headers(personal)).status_code == 403
    assert api.get("/api/v2/outcome-stats/readers", headers=headers()).json() == {"readers": []}
    with api.app.state.store.transaction() as c:
        assistant.ensure_schema(c)
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key='outcome-stats-readers'").fetchone()
        assert not c.execute("SELECT 1 FROM events WHERE action='outcome_stats.granted'").fetchone()
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": True},
                   headers=headers()).json() == {"readers": ["ops"]}
    assert api.put("/api/v2/outcome-stats/readers/ops", json={"read": False},
                   headers=headers(personal)).status_code == 403
    assert api.get("/api/v2/outcome-stats/readers", headers=headers()).json() == {"readers": ["ops"]}
    assert query(api, "SELECT count(*) FROM assistant_action_weekly")["rows"] == [[0]]
    _, attempt = assistant_turn(api, "ana-test", "Summarize proposal outcomes for me")
    delegated = attempt["token"]
    assert api.put("/api/v2/outcome-stats/readers/coo", json={"read": True},
                   headers=headers(delegated)).status_code == 403
    # The HTTP Assistant policy refuses SQL first; also exercise the database boundary itself.
    for view in ("assistant_action_weekly", "mcp_call_weekly"):
        assert query(api, "SELECT * FROM " + view, delegated, expected=403)["error"]["code"] == "confirm_required"
    auth, store = api.app.state.auth, api.app.state.store
    who = auth.authenticate({"authorization": "Bearer " + delegated}, "/api/v2/sql", "POST")
    assert who.role == "owner" and who.via == "assistant" and who.task_actor
    with store.read() as c:
        conn = S.connect(store.settings.db_path, c, auth, who)
        try:
            for view in ("assistant_action_weekly", "mcp_call_weekly"):
                with pytest.raises(sqlite3.DatabaseError, match="no such table"):
                    conn.execute("SELECT * FROM " + view).fetchall()
        finally:
            conn.close()
    assert api.get("/api/v2/live/stats", headers=headers(delegated)).status_code == 403


def test_private_task_parties_are_built_once_per_clock_not_once_per_request(api):
    """Every /api/v2/sql call reads the private-task parties through the store's shared row cache: two calls at one
    clock build them once (they were rebuilt on every call, about 4.6% of TIDY's CPU)."""
    from backend import row_clock
    from backend import sql as S
    from backend.auth import Identity
    store, auth = api.app.state.store, api.app.state.auth
    who = Identity("human:ben", "human")
    with store.transaction() as c:
        c.execute("INSERT INTO tasks(id,title,owner_id,requester_id,private,status,created_at,updated_at) "
                  "VALUES('priv-share','Private','bot:ops','human:ana',1,'open',?,?)", (H.now(), H.now()))
    builds = []
    original = row_clock._rows
    row_clock._rows = lambda c, statement: builds.append(statement) or original(c, statement)
    try:
        for _ in range(2):
            with store.read() as c:
                conn = S.connect(store.settings.db_path, c, auth, who)
                try:
                    S.run(conn, "SELECT count(*) FROM tasks", [], 5000, 20)
                finally:
                    conn.close()
    finally:
        row_clock._rows = original
    assert sum(row_clock.PARTIES in s for s in builds) == 1, builds


def test_a_person_read_checks_the_org_chart_table_once_not_once_per_private_task(api):
    """Visibility per private task reads no table-existence probe again: a member's sqlite_master reads stay flat as
    private tasks grow (they were one per private task, 184 for 184 tasks on a 6k-task hub)."""
    from backend import sql as S
    from backend.auth import Identity
    store, auth = api.app.state.store, api.app.state.auth
    who = Identity("human:ben", "human")

    def catalog_reads():
        seen = []
        with store.read() as c:
            conn = S.connect(store.settings.db_path, c, auth, who, trace=seen.append)
            try:
                S.run(conn, "SELECT id FROM tasks ORDER BY id", [], 5000, 20)
            finally:
                conn.close()
        return sum("sqlite_master" in text for text in seen)

    def add(count, start):
        with store.transaction() as c:
            c.executemany("INSERT INTO tasks(id,title,owner_id,requester_id,private,status,created_at,updated_at) "
                          "VALUES(?,?,?,?,?,?,?,?)",
                          [(f"priv-{i}", "Private", "bot:ops", "human:ana", 1, "open", H.now(), H.now())
                           for i in range(start, start + count)])

    add(20, 0)
    before = catalog_reads()
    add(200, 20)
    assert catalog_reads() == before


def test_shared_room_membership_for_every_bot_is_one_pass_and_matches_each_bot(api):
    """`sql.guarded` asks which shared rooms a person may join for every bot at once. It reads the roster, the configs
    and the access rows once for the whole list (the per-bot check copied the whole roster and configs for each bot), and
    answers exactly as the per-bot membership does: primary users, open and restricted reads, the owner, a bot actor."""
    from backend import rooms
    from backend import sql as S
    from backend.auth import Identity
    from backend.store import H, encode
    from backend.tests.test_api import restrict

    store, auth = api.app.state.store, api.app.state.auth
    people = [{"id": "ana", "email": "ana@acme.example", "primary_for": ["*"]},
              {"id": "ben", "email": "ben@acme.example", "primary_for": ["cpo", "ops"]},
              {"id": "cara", "email": "cara@acme.example", "primary_for": [f"b{i}" for i in range(6)]}]
    people += [{"id": f"p{i}", "email": f"p{i}@acme.example", "primary_for": [f"b{i}"] if i % 2 else []}
               for i in range(37)]
    fleet = [f"b{i}" for i in range(53)]
    with store.transaction() as c:
        H.sync_registry(c, {slug: {"name": slug, "runtime": "fake", "status": "active"} for slug in fleet},
                        {"people": people})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))
        for slug in fleet:
            c.execute("INSERT INTO bot_config(bot,config,owner_id) VALUES(?,?,?)", (slug, "{}", "ana"))
        for i, slug in enumerate(fleet):
            if i % 3 == 0:
                restrict(c, slug, people=[f"p{i % 37}"])
            elif i % 3 == 1:
                restrict(c, slug, see=["cara"], read=["cara"], write=["p1"])
        slugs = [row[0] for row in c.execute("SELECT bot FROM bot_config ORDER BY bot")]
    actors = ["human:ana", "human:ben", "human:cara", "human:p1", "human:p2", "human:nobody", "bot:ops"]

    answers = {}
    with store.read() as c:
        for actor in actors:
            expected = (set() if not actor.startswith("human:") else
                        {slug for slug in slugs if H.actor_id(actor) in rooms.shared_member_ids(c, auth, slug)})
            assert rooms.shared_member_bots(c, auth, actor, slugs) == expected, actor
            answers[actor] = expected
    # The mix is real: the owner joins every room, a plain member some but not all, a stranger none.
    assert answers["human:ana"] == set(slugs)
    assert answers["human:cara"] and answers["human:cara"] != set(slugs)
    assert {"b1", "b2"} <= answers["human:cara"] and "b0" not in answers["human:cara"]
    assert answers["human:nobody"] == set() and answers["bot:ops"] == set()

    # One caller's connection reads the access rows in one statement, not once per bot.
    who = Identity("human:cara", "human", email="cara@acme.example")
    statements = []
    with store.read() as c:
        conn = S.connect(store.settings.db_path, c, auth, who, trace=statements.append)
        try:
            access_reads = sum("access FROM bot_config" in text for text in statements)
        finally:
            conn.close()
    assert access_reads <= 1, access_reads
