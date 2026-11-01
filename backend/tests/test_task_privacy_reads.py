"""Revocable task privacy fences secondary reads and previously queued context."""
import json
import time

import pytest

from backend import read_cache, sql as SQL, task_privacy, views
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401



@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def task(api, *, requester="human:cara", owner="human:ben", private=True, title="Sensitive packet", **fields):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, requester, title, "Confidential attachment and comment.", owner,
                             private=private, lint=False, **fields)


def sql(api, text, token="ana-test"):
    response = api.post("/api/v2/sql", json={"sql": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def legacy_message(api, tid, *, body="Private legacy comment"):
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ben", ["human:ana", "bot:ops"], kind="chat", subject="General")
        msg = H.say(c, "human:ben", "bot:ops", body, conversation_id=conv["id"])
        c.execute("UPDATE messages SET refs=? WHERE id=?", (encode({"task": tid}), msg["id"]))
        return conv, H.message(c, msg["id"])


def test_private_relations_tags_and_events_do_not_leak_to_owner(api):
    hidden = task(api, labels=["sensitive-only"])
    public = task(api, private=False, requester="human:ana", owner="human:ana", title="Public parent")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,'now')", (hidden["id"], public["id"], "parent"))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,'now')", (hidden["id"], public["id"], "blocks"))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,'now')", (*sorted((hidden["id"], public["id"])), "related"))
        H.event(c, "human:ana", "task.inspect", hidden["id"], {"title": hidden["title"]})
    for suffix in ("", "/comments", "/links", "/files", "/answers", "/tree"):
        get(api, "tasks/" + hidden["id"] + suffix, expected=404)
    page = get(api, "tasks/" + public["id"])
    assert not page["children"] and page["task"]["parts"]["total"] == 0
    assert page["task"]["relations"] == {} and hidden["id"] not in json.dumps(page)
    listed = get(api, "tasks")
    assert hidden["id"] not in json.dumps(listed)
    assert "sensitive-only" not in json.dumps(get(api, "tags"))
    assert not sql(api, "SELECT id FROM tags WHERE key='sensitive-only'")
    assert not sql(api, "SELECT detail FROM events WHERE action='task.inspect'")
    assert sql(api, "SELECT from_task FROM task_relations") == []
    assert len(sql(api, "SELECT from_task FROM task_relations", "ben-test")) == 3
    assert any(t[0] == hidden["id"] for t in sql(api, "SELECT id FROM tasks", "ben-test"))


def test_shared_room_message_snapshot_sql_and_inbox_filter_private_refs(api):
    hidden = task(api, requester="human:ben", owner="bot:ops")
    conv, msg = legacy_message(api, hidden["id"])
    for path in (f"conversations/{conv['id']}/messages", f"conversations/{conv['id']}/snapshot", "inbox"):
        assert "Private legacy comment" not in json.dumps(get(api, path))
    get(api, "messages/" + msg["id"], expected=404)
    assert not sql(api, "SELECT body FROM messages WHERE id='" + msg["id"] + "'")
    assert get(api, "messages/" + msg["id"], "ben-test")["body"] == "Private legacy comment"
    with api.app.state.store.transaction() as c:
        H.say(c, "human:ben", "bot:ops", "Public chat message", conversation_id=conv["id"])
    page = api.get(f"/api/v2/conversations/{conv['id']}/messages", headers=headers()).json()
    assert [m["body"] for m in page["messages"]] == ["Public chat message"]
    assert page["has_more"] is False and page["next_before"] is None
    response = api.get(f"/api/v2/conversations/{conv['id']}/messages?before={msg['id']}", headers=headers())
    assert response.status_code == 422


def test_uploader_file_access_and_cached_comment_are_revoked(api):
    row = task(api, requester="human:ana")
    added = post(api, f"tasks/{row['id']}/files", {"name": "private.md", "text": "Private bytes"}, "ben-test")
    fid = added["file_id"]
    with api.app.state.store.read() as c:
        bid = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]
    response = api.get(f"/api/v2/files/{fid}?v=1", headers=headers("ben-test"))
    assert response.content == b"Private bytes" and "no-store" in response.headers["cache-control"]
    key = "private-comment-retry"
    post(api, f"tasks/{row['id']}/comments", {"text": "Private cached comment"}, "ben-test", key=key)
    current = get(api, "tasks/" + row["id"])["task"]
    post(api, "tasks/" + row["id"], {"version": current["version"], "owner": "cara"})
    for ident in (fid, bid):
        for suffix in ("", "/meta", "/poster", "/thumb", "/versions/1"):
            response = api.get("/api/v2/files/" + ident + suffix, headers=headers("ben-test"))
            assert response.status_code in (403, 404), response.text
    post(api, f"tasks/{row['id']}/comments", {"text": "Private cached comment"}, "ben-test", key=key, expected=404)
    assert not sql(api, "SELECT name FROM blobs WHERE id='" + bid + "'", "ben-test")
    assert api.get(f"/api/v2/files/{fid}", headers=headers("cara-test")).content == b"Private bytes"


def test_private_execution_output_context_and_grants_revoke_on_reassignment(api):
    row = task(api, requester="human:ben", owner="bot:ops")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    key = "private-claim"
    attempt = claim(api, machine, "ops", key=key)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "private-thread"}, machine["token"])
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "message", "payload": {"text": "Private runtime output"}}]}, machine["token"])
    get(api, f"turns/{attempt['id']}/steps", expected=404)
    get(api, f"turns/{attempt['id']}/steps", "ben-test")
    post(api, "tasks/" + row["id"], {"version": attempt["task"]["version"], "owner_id": "finance"}, "ben-test")
    get(api, "messages/" + attempt["message"]["id"], attempt["token"], expected=404)
    assert api.get(f"/api/v2/conversations/{row['conversation_id']}/messages", headers=headers(attempt["token"])).status_code in (403, 404)
    assert api.post("/api/v2/jobs/claim", json={"bot": "ops", "start_places": True}, headers=headers(machine["token"], key)).status_code in (403, 404)
    assert api.post(f"/api/v2/attempts/{attempt['id']}/inputs", json={}, headers=headers(machine["token"])).status_code in (403, 409)
    assert not sql(api, "SELECT payload FROM attempt_events WHERE attempt_id='" + attempt["id"] + "'")
    assert "Private runtime output" not in json.dumps(get(api, "bots/ops/execution-review"))


def test_private_execution_cannot_publish_public_task_or_wider_reply(api):
    row = task(api, requester="human:ben", owner="bot:ops")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    public = task(api, private=False, requester="bot:ops", owner="bot:ops", title="Public work")
    for ident in (public["id"], public["id"][:8]):
        post(api, "tasks/" + ident, {"version": public["version"], "body": "Private derived output"}, attempt["token"], expected=403)
    post(api, "tasks", {"owner_id": "ops", "title": "Private follow-up", "body": "Keep this private", "private": False}, attempt["token"], expected=403)
    post(api, "messages", {"to": "ana", "text": "Private derived output"}, attempt["token"], expected=403)
    post(api, "updates", {"body": "Private derived output"}, attempt["token"], expected=403)
    post(api, "integrations/github/learnings", {"text": "Private derived output"}, attempt["token"], expected=403)
    assert not sql(api, "SELECT id FROM tasks WHERE id='" + row["id"] + "'")


def test_live_events_stop_when_private_owner_loses_access(api):
    from backend import events
    row = task(api, requester="human:ana")
    store, auth, ben = api.app.state.store, api.app.state.auth, Identity("human:ben", "human", "ben@acme.example")

    def seen(after):
        with store.read() as c:
            sent, cursor, *_ = events.read(c, auth, ben, after, topics=("tasks", "messages"),
                                           task_views=lambda rows, *a: rows, task_view=lambda r, *a, **k: r)
        return json.dumps([data for *_, data in sent]), cursor
    with store.read() as c:
        start = events.latest(c)
    post(api, "tasks/" + row["id"] + "/comments", {"text": "First packet note"})
    before, cursor = seen(start)
    assert "First packet note" in before and row["id"] in before
    with store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='human:cara' WHERE id=?", (row["id"],))
    post(api, "tasks/" + row["id"] + "/comments", {"text": "Second packet note"})
    after, _ = seen(cursor)
    assert "Second packet note" not in after and row["id"] not in after and "Sensitive" not in after


def test_context_search_and_cached_fleet_do_not_disclose_revoked_sources(api):
    row = task(api, requester="human:cara", owner="human:ben")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO linked_docs(id,title,url,kind,description,added_by,created_at,updated_at) "
                  "VALUES(?,? ,?,'website',?,'human:ben',?,?)",
                  ("private-reference", "Sensitive packet reference", "https://example.test/#/task/" + row["id"],
                   "Sensitive packet", H.now(), H.now()))
        who = Identity("human:ben", "human", email="ben@acme.example")
        before = views.fleet_snapshot_cached(c, api.app.state.auth, who, lambda row, **k: row)
        assert row["id"] in {t["id"] for t in before["tasks"]}
        c.execute("UPDATE tasks SET owner_id='human:ana' WHERE id=?", (row["id"],))
        after = views.fleet_snapshot_cached(c, api.app.state.auth, who, lambda row, **k: row)
        assert row["id"] not in {t["id"] for t in after["tasks"]}
    result = get(api, "context/search?q=Sensitive&source=docs", "ben-test")
    assert "private-reference" not in json.dumps(result)


@pytest.mark.parametrize("requester", ["bot:ops"])
def test_verified_legal_requester_is_kept_without_forged_human_grants(api, requester):
    with api.app.state.store.transaction() as c:
        config = H._json(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0], {})
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (encode({**config, "template": "general-counsel"}),))
    source = task(api, requester=requester, owner="bot:ops")
    machine = runner(api)
    with api.app.state.store.read() as c:
        assigned = c.execute("SELECT * FROM assignments WHERE bot='ops'").fetchone()
    if not assigned or assigned["runner_id"] != machine["runner_id"]:
        assign(api, machine, "ops", generation=assigned["generation"] if assigned else 0)
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    followup = post(api, "tasks", {"owner_id": "ops", "title": "Review the agreement", "body": "Review its terms."}, attempt["token"])
    assert followup["private"] and followup["requester_id"] == requester
    assert followup["owner_id"] == "bot:ops"
    get(api, "tasks/" + followup["id"], expected=404)
    get(api, "tasks/" + followup["id"], "ben-test", expected=200 if H.is_human(requester) else 404)
    assert get(api, "tasks/" + source["id"], attempt["token"])["task"]["private"]
    employees = api.get("/api/employees", headers=headers()).json()
    assert next(e for e in employees if e["name"] == "ops")["private_tasks_default"]


def test_actual_bot_membership_intersects_acted_human_in_every_raw_helper(api):
    row = task(api)
    _, msg = legacy_message(api, row["id"])
    machine, _, attempt = setup_attempt(api, "finance")
    acted = Identity("human:ben", "human", email="ben@acme.example", task_actor="bot:finance",
                     runner_id=machine["runner_id"], attempt_id=attempt["id"])
    with api.app.state.store.transaction() as c:
        api.app.state.store.settings.test_identities["acted-test"] = acted
        assert not task_privacy.task_readable(c, acted, row)
        assert not task_privacy.message_readable(c, task_privacy.actor(acted), msg)
    get(api, "tasks/" + row["id"], "acted-test", expected=404)
    get(api, "messages/" + msg["id"], "acted-test", expected=404)
    assert not sql(api, "SELECT id FROM tasks WHERE id='" + row["id"] + "'", "acted-test")
    assert row["id"] not in json.dumps(get(api, "context/search?q=Sensitive&source=docs", "acted-test"))


def test_deleted_comment_receipt_is_canonical_empty_and_currently_authorized(api):
    row = task(api)
    who = Identity("human:ben", "human", email="ben@acme.example")
    path = f"tasks/{row['id']}/comments"
    original = post(api, path, {"text": "Withdrawn private comment"}, "ben-test", key="withdrawn-comment")
    mid = original["comment"]["id"]
    deleted = post(api, f"{path}/{mid}/delete", {}, "ben-test")
    assert deleted["comment"]["body"] == "" and deleted["comments"] == []
    retried = post(api, path, {"text": "Withdrawn private comment"}, "ben-test", key="withdrawn-comment")
    assert retried["comment"] == deleted["comment"]
    get(api, "messages/" + mid, "ben-test", expected=404)
    assert not sql(api, f"SELECT body FROM messages WHERE id='{mid}'", "ben-test")
    with api.app.state.store.transaction() as c:
        receipt = H.message(c, mid, include_deleted=True)
        assert task_privacy.require_payload(c, who, receipt) == receipt
        for forged in ({**receipt, "body": "Withdrawn private comment"}, {**receipt, "extra": "Private words"}):
            with pytest.raises(Problem):
                task_privacy.require_payload(c, who, forged)
        c.execute("UPDATE tasks SET owner_id='human:ana' WHERE id=?", (row["id"],))
        with pytest.raises(Problem):
            task_privacy.require_payload(c, who, receipt)


@pytest.mark.parametrize("sensitive,owner,explicit,expected", [
    ("finance", "finance", False, 200),
    ("ops", "ops", False, 403),
])
def test_delegated_creation_keeps_defaults_and_requires_actual_bot_audience(api, sensitive, owner, explicit, expected):
    machine, _, attempt = setup_attempt(api, "finance")
    acted = Identity("human:ben", "human", email="ben@acme.example", task_actor="bot:finance",
                     runner_id=machine["runner_id"], attempt_id=attempt["id"])
    api.app.state.store.settings.test_identities["acted-test"] = acted
    with api.app.state.store.transaction() as c:
        if sensitive:
            config = H._json(c.execute("SELECT config FROM bot_config WHERE bot=?", (sensitive,)).fetchone()[0], {})
            c.execute("UPDATE bot_config SET config=? WHERE bot=?",
                      (encode({**config, "private_tasks_default": True}), sensitive))
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
        rooms_before = c.execute("SELECT count(*) FROM conversations").fetchone()[0]
    created = post(api, "tasks", {"owner_id": owner, "title": "Review delegated work", "body": "Review it.",
                                   "private": explicit}, "acted-test", expected=expected)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before + (expected == 200)
        if expected != 200:
            assert c.execute("SELECT count(*) FROM conversations").fetchone()[0] == rooms_before
    if expected == 200:
        assert created["private"] and created["requester_id"] == "human:ben" and created["owner_id"] == "bot:finance"
        assert get(api, "tasks/" + created["id"], "acted-test")["task"]["private"]
        # A signed-in person keeps their explicit choice to publish.
        public = post(api, "tasks", {"owner_id": owner, "title": "Review public work", "body": "Review it.",
                                     "private": False}, "ben-test")
        assert public["private"] is False


@pytest.mark.slow
def test_sql_provenance_work_does_not_scale_with_unrelated_historical_blobs(api):
    hidden = task(api)
    who = Identity("human:ana", "owner", email="ana@acme.example")
    def measure(query):
        statements = []
        started = time.monotonic()
        with api.app.state.store.read() as c:
            conn = SQL.connect(api.app.state.store.settings.db_path, c, api.app.state.auth, who, trace=statements.append)
            try:
                rows = SQL.run(conn, query, [], 500, 20)["rows"]
            finally:
                conn.close()
        return rows, len(statements), time.monotonic() - started
    before = measure("SELECT id FROM tasks")
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO blobs VALUES(?,'human:ana',?,0,'old.txt','text/plain',?)",
                      ((f"historical-{n}", f"{n:064x}", H.now()) for n in range(54_000)))
    after = measure("SELECT id FROM tasks")
    blobs = measure("SELECT count(*) FROM blobs")
    assert before[0] == after[0] == [] and after[1] <= before[1] + 5
    assert blobs[0] == [[54_000]] and blobs[1] <= after[1] + 10
    with api.app.state.store.transaction() as c:
        c.executemany("INSERT INTO task_assets VALUES(?,?)",
                      ((hidden["id"], f"historical-{n}") for n in range(2000)))
    linked = measure("SELECT count(*) FROM blobs")
    assert linked[0] == [[52_000]] and linked[1] == blobs[1]
    print(f"privacy scaling: tasks before {before[1]} source queries/{before[2]:.4f}s, "
          f"54k blobs {after[1]} queries/{after[2]:.4f}s; count blobs {blobs[1]} queries/{blobs[2]:.4f}s; "
          f"2000 private associations {linked[1]} queries/{linked[2]:.4f}s")


def test_sql_audit_keeps_no_literals_and_provenance_uses_the_result_snapshot(api):
    row = task(api, requester="human:cara", owner="human:ben")
    marker = "PRIVATE-SQL-LITERAL"
    assert sql(api, f"SELECT '{marker}' FROM tasks WHERE id='{row['id']}'", "ben-test") == [[marker]]
    api.post("/api/v2/sql", json={"sql": f"SELECT '{marker}' FROM missing_table"}, headers=headers("ben-test"))
    with api.app.state.store.read() as c:
        logs = list(c.execute("SELECT detail FROM events WHERE action='sql.query'"))
        assert logs and all(marker not in r[0] and row["id"] not in r[0] for r in logs)
        conn = SQL.connect(api.app.state.store.settings.db_path, c, api.app.state.auth,
                           Identity("human:ben", "human", email="ben@acme.example"))
        try:
            with api.app.state.store.transaction() as write:
                write.execute("UPDATE tasks SET owner_id='human:ana' WHERE id=?", (row["id"],))
            # An in-flight read has one coherent snapshot; the next request sees revocation.
            assert SQL.run(conn, f"SELECT id FROM tasks WHERE id='{row['id']}'", [], 500, 5)["rows"] == [[row["id"]]]
            with pytest.raises(Problem):
                SQL.run(conn, "SELECT * FROM main.messages", [], 500, 5)
        finally:
            conn.close()
    assert not sql(api, f"SELECT id FROM tasks WHERE id='{row['id']}'", "ben-test")


@pytest.mark.slow
def test_sql_event_privacy_scales_with_matching_attempts_not_their_cross_product(api):
    private = task(api, requester="human:ben", owner="bot:ops")
    machine = runner(api)
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ana", ["bot:ops"], subject="Public work")
        private_mid = next(r["id"] for r in c.execute("SELECT * FROM messages WHERE conversation_id=?", (private["conversation_id"],)))
        private_job = c.execute("SELECT id FROM jobs WHERE message_id=?", (private_mid,)).fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,finished_at) "
                  "VALUES('synthetic-private',?,'ops',?,1,'synthetic-private','completed',?,?,?)",
                  (private_job, machine["runner_id"], H.now(), H.shift(H.now(), minutes=-1), H.now()))
        c.execute("INSERT INTO events VALUES('synthetic-private-event',?,'bot:ops','synthetic.privacy','','{}')",
                  (H.shift(H.now(), seconds=-30),))
    def seed(first, last):
        with api.app.state.store.transaction() as c:
            for n in range(first, last):
                started = H.shift("2026-01-01T00:00:00Z", minutes=n * 2)
                finished = H.shift(started, minutes=1)
                mid, jid, aid = (f"synthetic-{kind}-{n}" for kind in ("message", "job", "attempt"))
                c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
                          "VALUES(?,?,'human:ana','bot:ops','say','Public request','{}',?)", (mid, conv["id"], started))
                jid = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()[0]
                c.execute("UPDATE jobs SET state='completed',attempt_id=? WHERE id=?", (aid, jid))
                c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,finished_at) "
                          "VALUES(?,?,'ops',?,1,?,'completed',?,?,?)", (aid, jid, machine["runner_id"], aid, finished, started, finished))
                c.execute("INSERT INTO events VALUES(?,?,'bot:ops','synthetic.privacy','','{}')", (f"synthetic-event-{n}", started))
    def measure():
        statements = []
        started = time.monotonic()
        with api.app.state.store.read() as c:
            conn = SQL.connect(api.app.state.store.settings.db_path, c, api.app.state.auth,
                               Identity("human:ana", "owner", email="ana@acme.example"), trace=statements.append)
            try:
                count = SQL.run(conn, "SELECT count(*) FROM events WHERE action='synthetic.privacy'", [], 500, 20)["rows"][0][0]
            finally:
                conn.close()
        return count, len(statements), time.monotonic() - started
    seed(0, 500)
    before = measure()
    seed(500, 3000)
    after = measure()
    assert before[0] == 500 and after[0] == 3000
    assert after[1] <= before[1] * 6 + 20
    print(f"event scaling: 500 attempts/events {before[1]} SQLite statements/{before[2]:.4f}s; "
          f"3000 attempts/events {after[1]} statements/{after[2]:.4f}s")


def traced_get(api, path, token):
    """The response to one GET and the number of SQL statements it ran on every pooled connection."""
    response, statements = traced(api, lambda: get(api, path, token))
    return response, len(statements)


def traced(api, request):
    """The result of `request()` and the SQL statements it ran on every pooled connection."""
    store = api.app.state.store
    statements, take, give = [], store._take, store._give

    def traced():
        c = take()
        c.set_trace_callback(statements.append)
        return c

    def untraced(c):
        c.set_trace_callback(None)
        give(c)
    store._take, store._give = traced, untraced
    try:
        return request(), statements
    finally:
        store._take, store._give = take, give


def test_task_page_reads_a_fixed_number_of_statements_and_hides_private_provenance(api):
    """A task page checks every comment, event and run against the tasks they name. Those checks read together, so a
    long task costs what a short one does, and whatever names a private task stays hidden from who cannot read it."""
    store = api.app.state.store
    with store.transaction() as c:
        hidden = H.task_create(c, "human:cara", "Private plan", "Secret", "human:cara", private=True, lint=False)
        c.execute("INSERT INTO runners(id,label,owner_id,token_hash,created_at) VALUES('rn','Mac','ana','th',?)", (H.now(),))

    def task_with(size):
        with store.transaction() as c:
            secret = H.task_comment(c, "human:cara", hidden["id"], f"Secret input {size}", wake=False)
            parent = H.task_create(c, "human:ana", f"Release {size}", "Checklist", "bot:ops", lint=False)
            for n in range(size):
                H.task_create(c, "human:ana", f"Step {size}-{n}", "Do it.", "human:ben", parent_id=parent["id"], lint=False)
                asked = secret if n == 1 else H.task_comment(c, "human:ana", parent["id"], f"Please do {n}", wake=False)
                aid = f"run-{size}-{n}"
                c.execute("INSERT INTO jobs(id,message_id,bot_id,state,created_at,attempt_id) VALUES(?,?,'ops','completed',?,?)",
                          ("job-" + aid, asked["id"], H.now(), aid))
                c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) "
                          "VALUES(?,?,'ops','rn',0,?,'completed',?,?)", (aid, "job-" + aid, aid, H.now(), H.now()))
                c.execute("INSERT INTO turns(id,bot,started_at,task_id,exit) VALUES(?,'ops',?,?,'ok')", (aid, H.now(), parent["id"]))
                H.task_comment(c, "bot:ops", parent["id"], f"Did {n}", wake=False, extra_refs={"turn_id": aid})
                H.task_comment(c, "human:ana", parent["id"], f"See {n}", wake=False,
                               extra_refs={"see": "task:" + hidden["id"]} if n == 2 else None)
                H._task_event(c, parent["id"], "human:ana", "note", None, hidden["id"] if n == 2 else f"note {n}", "")
        return parent["id"]

    counts = {}
    for size in (3, 30):
        tid = task_with(size)
        (ana, counts[size, "ana"]), (cara, counts[size, "cara"]) = (traced_get(api, "tasks/" + tid, "ana-test"),
                                                                    traced_get(api, "tasks/" + tid, "cara-test"))
        assert len(ana["children"]) == len(cara["children"]) == size
        # The run that took in Cara's private comment, the comment naming her task and that event are hers alone.
        assert len(cara["runs"]) == size and len(ana["runs"]) == size - 1
        assert len(cara["comments"]) == len(ana["comments"]) + 2 and len(cara["events"]) == len(ana["events"]) + 1
        assert hidden["id"] not in json.dumps(ana)
    assert counts[30, "ana"] <= counts[3, "ana"] + 5 and counts[30, "cara"] <= counts[3, "cara"] + 5


def test_needs_you_reads_a_fixed_number_of_statements_and_hides_private_items(api):
    """Each Needs you card reads its task, asks, origin and the tasks above and behind it. Those rows are read for all
    cards together, so a long list costs what a short one does, and a private task its reader is not a party to stays
    off the list and out of every card that holds it up."""
    store = api.app.state.store
    with store.transaction() as c:
        # Cara's private task, waiting on Ben, who is no party to it.
        hidden = H.task_create(c, "human:cara", "Private plan", "Secret", "human:cara", private=True, lint=False)
        c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ben' WHERE id=?", (hidden["id"],))

    def cards(size):
        with store.transaction() as c:
            parent = H.task_create(c, "human:ana", f"Release {size}", "Checklist", "bot:ops", lint=False)
            for n in range(size):
                step = H.task_create(c, "bot:ops", f"Step {size}-{n}", "Do it.", "human:ben", parent_id=parent["id"],
                                     due=H.shift(H.now(), days=3), lint=False)
                c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks','now')",
                          (step["id"], hidden["id"]))
                H.event(c, H.KEEPER, "task.origin", step["id"], {"bot": "bot:ops"})
                if n == 0:
                    H.say(c, "bot:ops", "human:ben", "Which region?", kind="ask",
                          conversation_id=step["conversation_id"], refs={"task": step["id"]})
                wait = H.task_create(c, "human:ana", f"Wait {size}-{n}", "x", "bot:ops", parent_id=parent["id"],
                                     lint=False)
                c.execute("UPDATE tasks SET status='waiting', waiting_on='human:ben' WHERE id=?", (wait["id"],))
        return parent

    counts, total = {}, 0
    for size in (3, 30):
        parent = cards(size)
        total += 2 * size
        page, counts[size] = traced_get(api, "needs-you", "ben-test")
        assert len(page["items"]) == total
        assert {x["holds_up"]["title"] for x in page["items"] if x["title"].startswith(f"Step {size}-")} == {parent["title"]}
        assert hidden["id"] not in json.dumps(page) and "Private plan" not in json.dumps(page)
    assert counts[30] <= counts[3] + 5


def test_task_list_reads_a_fixed_number_of_statements_and_hides_private_tasks(api):
    """A board reads each row's origin (the person up its chain of parents), quiet state and question for the whole
    page together, so 300 tasks cost what 30 do. A private task stays off another member's list and stops the chain:
    a bot's subtask under it names no person to a reader who cannot open it."""
    store = api.app.state.store
    with store.transaction() as c:
        hidden = H.task_create(c, "human:cara", "Private plan", "Secret", "human:cara", private=True, lint=False)
        shared = H.task_create(c, "human:ana", "Release", "Checklist", "bot:ops", lint=False)

    def tasks_for(size):
        with store.transaction() as c:
            for n in range(size):
                row = H.task_create(c, "bot:ops", f"Step {size}-{n}", "Do it.", "bot:ops", lint=False,
                                    next_run=n % 3 == 0)
                c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'parent','now')",
                          (row["id"], (hidden if n % 2 else shared)["id"]))
                if n % 5 == 0:
                    H.say(c, "bot:ops", "human:ben", "Which region?", kind="ask",
                          conversation_id=row["conversation_id"], refs={"task": row["id"]})

    path, counts = "tasks?status=open,doing,waiting,declined", {}
    for size in (30, 300):
        tasks_for(size)
        for brief in ("&brief=true", ""):
            ben, counts[size, "ben", brief] = traced_get(api, path + brief, "ben-test")
            cara, counts[size, "cara", brief] = traced_get(api, path + brief, "cara-test")
            assert hidden["id"] not in json.dumps(ben) and "Private plan" not in json.dumps(ben)
            assert hidden["id"] in {t["id"] for t in cara["tasks"]}
            under = {int(t["title"].split("-")[1]) % 2: t["from_person"] for t in ben["tasks"] if t["title"].startswith("Step ")}
            assert under == {1: None, 0: "human:ana"}
            assert {t["from_person"] for t in cara["tasks"] if t["title"].startswith("Step ")} == {"human:ana", "human:cara"}
    for key in ("ben", "cara"):
        for brief in ("&brief=true", ""):
            assert counts[300, key, brief] <= counts[30, key, brief] + 5, counts


def test_tag_list_reads_a_fixed_number_of_statements_and_hides_tags_only_on_private_tasks(api):
    """The tag list reads every tag's links and attach events together, so a tag on 100 tasks costs what one on 10
    does, and passes exactly the tags tag_readable passes one by one: a tag only on another's private tasks, still
    named by an attach event after it came off, stays hidden."""
    store = api.app.state.store

    def attach(size):
        with store.transaction() as c:
            for n in range(size):
                hidden = H.task_create(c, "human:cara", f"Plan {size}-{n}", "Secret", "human:cara", private=True,
                                       lint=False)
                H._set_task_tags(c, "human:cara", hidden["id"], [f"secret-{size}", "mixed"])
                H._set_task_tags(c, "human:cara", hidden["id"], ["mixed"])        # the attach event stays
                shared = H.task_create(c, "human:ana", f"Step {size}-{n}", "Do it.", "bot:ops", lint=False)
                H._set_task_tags(c, "human:ana", shared["id"], ["mixed", f"open-{size}"])

    counts = {}
    for size in (10, 100):
        attach(size)
        for token, person in (("ben-test", "human:ben"), ("cara-test", "human:cara")):
            listed, counts[size, person] = traced_get(api, "tags", token)
            with store.read() as c:
                one_by_one = [t["id"] for t in H.tags(c) if task_privacy.tag_readable(c, Identity(person, "human"), t)]
            assert [t["id"] for t in listed["tags"]] == one_by_one
            keys = {t["key"] for t in listed["tags"]}
            assert {"mixed", f"open-{size}"} <= keys
            assert (f"secret-{size}" in keys) == (person == "human:cara")
    assert counts[100, "human:ben"] <= counts[10, "human:ben"] + 5, counts
    assert counts[100, "human:cara"] <= counts[10, "human:cara"] + 5, counts


def test_tag_attach_events_are_read_from_a_covering_partial_index(api):
    """The tag list reads every tag's attach events with one IN(...) over events. Those rows are a sliver of a table
    that holds millions of events, so the statement must seek a small covering index (target, detail) for
    action='tag.attach', not the general target/action index that fetches each event row from the big table."""
    with api.app.state.store.read() as c:
        plan = " ".join(row[3] for row in c.execute(
            "EXPLAIN QUERY PLAN " + task_privacy.TAG_ATTACHES_SQL.format(marks="?,?"), ("a", "b")))
    assert "events_tag_attach" in plan and "COVERING" in plan, plan


def test_bot_list_reads_a_fixed_number_of_statements_and_seeks_each_bots_last_run(api):
    """GET /api/employees reads each bot's computer, open tasks, declared settings and last finished run for every
    bot at once, so 40 more bots add no statements (each open task still has its own start checks); the last run is found by an index seek per bot, not a sort of
    every run, and is the one the old window query picked (latest finish, then highest id)."""
    store = api.app.state.store
    with store.transaction() as c:
        source = dict(c.execute("SELECT * FROM bots WHERE slug='ops'").fetchone())
        config = dict(c.execute("SELECT * FROM bot_config WHERE bot='ops'").fetchone())
        for n in range(40):
            for table, row in (("bots", {**source, "id": f"crew{n}", "slug": f"crew{n}"}), ("bot_config", {**config, "bot": f"crew{n}"})):
                c.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
            H.task_create(c, "human:ana", f"Step {n}", "Do it.", f"bot:crew{n}", lint=False)
            for turn, finished in (("a", "2026-01-02"), ("b", "2026-01-03"), ("c", "2026-01-03"), ("d", None)):
                c.execute("INSERT INTO turns(id,bot,started_at,finished_at) VALUES(?,?,?,?)",
                          (f"t{n}{turn}", f"crew{n}", "2026-01-01", finished))
    before = {}
    for token in ("ana-test", "ben-test"):
        _, before[token] = traced(api, lambda: api.get("/api/employees", headers=headers(token)))
    with store.transaction() as c:
        for n in range(40, 80):
            for table, row in (("bots", {**source, "id": f"crew{n}", "slug": f"crew{n}"}), ("bot_config", {**config, "bot": f"crew{n}"})):
                c.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    for token in ("ana-test", "ben-test"):
        response, statements = traced(api, lambda: api.get("/api/employees", headers=headers(token)))
        assert response.status_code == 200
        assert len(statements) <= len(before[token]) + 5, (len(before[token]), len(statements))
        flows = {e["name"]: e.get("task_flow") for e in response.json()}
        assert {flows.get(f"crew{n}")["waiting"] for n in range(40) if flows.get(f"crew{n}")} == {1}
        assert {flows.get(f"crew{n}")["waiting"] for n in range(40, 80) if flows.get(f"crew{n}")} == {0}
    with store.read() as c:
        latest = dict(c.execute("SELECT bot,id FROM (SELECT bot,id,row_number() OVER(PARTITION BY bot ORDER BY "
                                "finished_at DESC,id DESC) AS n FROM turns WHERE finished_at IS NOT NULL) WHERE n=1"))
        assert latest["crew3"] == "t3c"
        sought = dict(row for row in c.execute(
            "SELECT b.slug,(SELECT t.id FROM turns t WHERE t.bot=b.slug AND t.finished_at IS NOT NULL "
            "ORDER BY t.finished_at DESC,t.id DESC LIMIT 1) FROM bots b") if row[1])
        assert sought == {bot: tid for bot, tid in latest.items() if bot in sought} and "crew3" in sought
        plan = " ".join(r[3] for r in c.execute(
            "EXPLAIN QUERY PLAN SELECT b.slug,(SELECT t.id FROM turns t WHERE t.bot=b.slug AND t.finished_at IS NOT NULL "
            "ORDER BY t.finished_at DESC,t.id DESC LIMIT 1) FROM bots b"))
        assert "turns_bot_finished" in plan and "TEMP B-TREE" not in plan, plan


def busy(api):
    """Another bot's write: it moves the provenance clock, so the next request rebuilds what it caches by it."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE provenance_clock SET value=value+1 WHERE id=1")


def running_turn(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    work = post(api, "tasks", {"owner_id": "ops", "title": "Release", "body": "Ship it."})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t"}, r["token"])
    with api.app.state.store.read() as c:
        cid = c.execute("SELECT conversation_id FROM tasks WHERE id=?", (work["id"],)).fetchone()[0]
    return r, work, attempt, cid


def queue(c, mid, cid, refs, *, sender="human:ana", in_reply_to=None):
    """A message to bot:ops as stored; the jobs trigger queues it."""
    c.execute("INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,created_at,refs,in_reply_to) "
              "VALUES(?,?,?,'bot:ops','say','More',?,?,?)", (mid, cid, sender, H.now(), encode(refs), in_reply_to))


def test_long_run_reads_a_fixed_number_of_statements_and_keeps_private_provenance(api):
    """Each event batch and input poll of a running turn checks the run against every task its inputs name, through
    the messages they reply to and answer. Those are read together, so a run that took in 50 inputs costs what one
    that took in 5 does, and a private task named only by a message an input replies to still fences the run."""
    store = api.app.state.store
    r, work, attempt, cid = running_turn(api)
    aid = attempt["id"]
    hidden = task(api, requester="human:cara", owner="bot:ops")         # Cara's; Ana is no party to it
    with store.transaction() as c:
        queue(c, "plan", cid, {"see": "task:" + hidden["id"]}, sender="human:cara")
        c.execute("UPDATE jobs SET state='completed' WHERE message_id='plan'")   # never an input of this run

    def took(size):
        with store.transaction() as c:
            start = c.execute("SELECT count(*) FROM attempt_inputs WHERE attempt_id=?", (aid,)).fetchone()[0]
            for i in range(start, size):
                ref = H.task_create(c, "human:ana", f"Ref {i}", "x", "human:ben", lint=False)["id"]
                # Chains of five: each input replies to the one before it and answers the two before that.
                queue(c, f"input-{i}", cid, {"task": work["id"], "see": "task:" + ref,
                                             "answers": [f"input-{k}" for k in range(max(i - i % 5, i - 2), i)]},
                      in_reply_to=f"input-{i - 1}" if i % 5 else ("plan" if i == 0 else None))
                c.execute("UPDATE jobs SET state='input',attempt_id=? WHERE message_id=?", (aid, f"input-{i}"))
                c.execute("INSERT INTO attempt_inputs(attempt_id,message_id,acked_at) VALUES(?,?,?)",
                          (aid, f"input-{i}", H.now()))

    counts = {}
    for size in (5, 50):
        took(size)
        busy(api)
        _, events = traced(api, lambda: post(api, f"attempts/{aid}/events",
                                             {"events": [{"seq": len(counts) + 1, "kind": "delta", "payload": {"text": "x"}}]},
                                             r["token"]))
        busy(api)
        polled, inputs = traced(api, lambda: post(api, f"attempts/{aid}/inputs", {}, r["token"]))
        counts[size] = len(events), len(inputs)
        assert polled["messages"] == []
        busy(api)
        with store.read() as c:
            assert hidden["id"] in task_privacy.attempt_tasks(c, aid)
            assert task_privacy.attempt_readable(c, "human:cara", aid)
            assert not task_privacy.attempt_readable(c, "human:ana", aid)
    assert counts[50][0] <= counts[5][0] + 5 and counts[50][1] <= counts[5][1] + 5, counts


def test_input_poll_passes_over_private_backlog_without_the_write_lock(api, monkeypatch):
    """A running turn polls for inputs a few times a second. A message queued in its room that names another private
    task is never folded into the run (it would make the run private) and waits for its own. The poll that passes over
    such a backlog reads only: no write transaction, and no more statements for 30 of them than for 3. A message the
    run may take still goes through the write transaction and reaches it."""
    store = api.app.state.store
    r, work, attempt, cid = running_turn(api)
    aid = attempt["id"]
    hidden = task(api, requester="human:cara", owner="bot:ops")
    writes, transaction = [], store.transaction

    def counted():
        writes.append(1)
        return transaction()
    monkeypatch.setattr(store, "transaction", counted)
    counts = {}
    for size in (3, 30):
        with transaction() as c:
            for i in range(len(counts) * 3, size):
                queue(c, f"held-{i}", cid, {"see": "task:" + hidden["id"]}, sender="human:cara")
        busy(api)
        writes.clear()
        polled, statements = traced(api, lambda: post(api, f"attempts/{aid}/inputs", {}, r["token"]))
        counts[size] = len(statements)
        assert polled["messages"] == [] and not writes
    assert counts[30] <= counts[3] + 5, counts
    with transaction() as c:
        queue(c, "follow-up", cid, {})
    polled = post(api, f"attempts/{aid}/inputs", {}, r["token"])
    assert [m["id"] for m in polled["messages"]] == ["follow-up"] and writes
    with store.read() as c:
        assert [row[0] for row in c.execute("SELECT message_id FROM attempt_inputs WHERE attempt_id=?", (aid,))] == [
            "follow-up"]
        assert "queued" in {row[0] for row in c.execute("SELECT state FROM jobs WHERE message_id LIKE 'held-%'")}


def test_status_reads_a_fixed_number_of_statements_and_hides_private_queued_work(api):
    """Status lists every queued and running job whose message its reader may read. Each job's room, with the meeting
    it may have come from, is read with the rest, so 100 queued tasks cost what 10 do, and a private task's job stays
    off the list of a member who is no party to it."""
    store = api.app.state.store

    def queue_tasks(size, start):
        with store.transaction() as c:
            for n in range(start, size):
                H.task_create(c, "human:cara" if n % 2 else "human:ana", f"Step {n}", "Do it.", "bot:ops",
                              private=bool(n % 2), lint=False)

    counts, start = {}, 0
    for size in (10, 100):
        queue_tasks(size, start)
        start = size
        busy(api)
        (ana, counts[size]), (cara, _) = (traced(api, lambda: api.get("/api/status", headers=headers()).json()),
                                          traced(api, lambda: api.get("/api/status", headers=headers("cara-test")).json()))
        counts[size] = len(counts[size])
        mine = {item["title"] for item in ana["queued"]}
        assert {f"Step {n}" for n in range(0, size, 2)} <= mine and not {f"Step {n}" for n in range(1, size, 2)} & mine
        assert {f"Step {n}" for n in range(size)} <= {item["title"] for item in cara["queued"]}
    assert counts[100] <= counts[10] + 5, counts


def test_bot_inbox_reads_a_fixed_number_of_statements_and_hides_private_messages(api):
    """A bot that never marks messages read keeps a long unread backlog, checked on every inbox read. The newest are
    read together, so 100 unread cost what 10 do, and a message naming a private task the bot is no party to stays
    out of its inbox."""
    store = api.app.state.store
    r, work, attempt, cid = running_turn(api)
    hidden = task(api, requester="human:cara", owner="human:ben")       # nothing to do with bot:ops

    def unread(size, start):
        with store.transaction() as c:
            for n in range(start, size):
                queue(c, f"note-{n}", cid, {"see": "task:" + hidden["id"]} if n % 2 else {"task": work["id"]})

    counts, start = {}, 0
    for size in (10, 100):
        unread(size, start)
        start = size
        busy(api)
        inbox, statements = traced(api, lambda: get(api, "inbox", attempt["token"]))
        counts[size] = len(statements)
        shown = {m["id"] for m in inbox["messages"]}
        assert {f"note-{n}" for n in range(0, size, 2)} <= shown and not {f"note-{n}" for n in range(1, size, 2)} & shown
    assert counts[100] <= counts[10] + 5, counts


def test_person_origin_rows_are_checked_in_one_row_and_match_the_one_row_read(api):
    # Tico Team's Needs you: `SELECT *, (rule) AS visible_here` stepped every card and ancestor again after prime_tasks
    # had read them, each row the GIL given up and taken back (50 ms to 1.4 s on a busy server). The rule is now
    # checked in one row; the primed rows must be what the one-row read gives, for every reader.
    rows = [task(api), task(api, requester="human:ben", owner="bot:ops"), task(api, requester="bot:ops", owner="human:cara"),
            task(api, requester="human:ben", owner="human:ben", title="Guest packet"),
            task(api, private=False, requester="human:ana", owner="bot:ops", title="Company parent"),
            task(api, private=False, requester="bot:ops", owner="bot:ops", title="Bot work")]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET guest='human:cara' WHERE id=?", (rows[3]["id"],))
        c.execute("UPDATE tasks SET status='done' WHERE id IN (?,?)", (rows[1]["id"], rows[5]["id"]))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'parent','now')",
                  (rows[1]["id"], rows[4]["id"]))
    ids = [row["id"] for row in rows] + ["no-such-task"]
    store, auth, people = api.app.state.store, api.app.state.auth, api.app.state.store.settings.test_identities
    readers = [(people["ana-test"], None), (people["ben-test"], None), (people["ben-test"], {"ops"}),
               (people["cara-test"], None), (Identity("bot:ops", "bot"), None)]
    outcomes = set()
    for who, unreadable in readers:
        for read_first in (True, False):        # Needs you reads its chain first; a task list's ancestors are not read
            with store.read_transaction() as c:
                seen = []
                c.set_trace_callback(seen.append)      # set first: setting it empties the snapshot's cache
                visible = auth.task_sql(c, who, unreadable=unreadable)
                if read_first:
                    H.prime_tasks(c, ids)
                before = len(seen)
                readable = views._prime_person_origins(c, visible, ids)
                statements = seen[before:]
                one = views._VISIBLE_TASK_SQL % visible
                for tid in ids:
                    primed, row = read_cache.one(c, one, (tid,)), c.execute(one, (tid,)).fetchone()
                    assert (primed is None) == (row is None), (who.actor, tid)
                    assert row is None or list(dict(primed).items()) == list(dict(row).items())
                assert readable == {tid for tid in ids if c.execute(one, (tid,)).fetchone()}
                if read_first:
                    assert statements and all(len(c.execute(sql).fetchall()) <= 1 for sql in statements), statements
                c.set_trace_callback(None)
            outcomes.add(frozenset(readable))
    assert len(outcomes) >= 3                   # the readers see different tasks: the rule is exercised, not trivial
