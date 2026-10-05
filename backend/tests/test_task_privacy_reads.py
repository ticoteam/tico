"""Revocable task privacy fences secondary reads and previously queued context."""
import asyncio
import json
import time

import pytest

from backend import sql as SQL, task_privacy, views
from backend.auth import Identity
from backend.store import H, Problem, encode
from backend.tests.test_api import api, assign, claim, get, headers, post, ready, runner, setup_attempt  # noqa: F401


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
        c.execute("UPDATE messages SET refs_json=? WHERE id=?", (encode({"task": tid}), msg["id"]))
        return conv, H.message(c, msg["id"])


def test_private_relations_tags_and_events_do_not_leak_to_owner(api):
    hidden = task(api, labels=["sensitive-only"])
    public = task(api, private=False, requester="human:ana", owner="human:ana", title="Public parent")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created) VALUES(?,?,?,'now')", (hidden["id"], public["id"], "parent"))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created) VALUES(?,?,?,'now')", (hidden["id"], public["id"], "blocks"))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created) VALUES(?,?,?,'now')", (*sorted((hidden["id"], public["id"])), "related"))
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
    assert not sql(api, "SELECT detail_json FROM events WHERE action='task.inspect'")
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
    post(api, "tasks/" + row["id"], {"version": row["version"], "owner": "finance"}, "ben-test")
    get(api, "messages/" + attempt["message"]["id"], attempt["token"], expected=404)
    assert api.get(f"/api/v2/conversations/{row['conversation_id']}/messages", headers=headers(attempt["token"])).status_code in (403, 404)
    assert api.post("/api/v2/jobs/claim", json={"bot": "ops"}, headers=headers(machine["token"], key)).status_code in (403, 404)
    assert api.post(f"/api/v2/attempts/{attempt['id']}/inputs", json={}, headers=headers(machine["token"])).status_code in (403, 409)
    assert not sql(api, "SELECT payload_json FROM attempt_events WHERE attempt_id='" + attempt["id"] + "'")
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
    post(api, "tasks", {"owner": "ops", "title": "Private follow-up", "body": "Keep this private", "private": False}, attempt["token"], expected=403)
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
        c.execute("UPDATE tasks SET owner='human:cara' WHERE id=?", (row["id"],))
    post(api, "tasks/" + row["id"] + "/comments", {"text": "Second packet note"})
    after, _ = seen(cursor)
    assert "Second packet note" not in after and row["id"] not in after and "Sensitive" not in after


def test_context_search_and_cached_fleet_do_not_disclose_revoked_sources(api):
    row = task(api, requester="human:cara", owner="human:ben")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO linked_docs(id,title,url,kind,description,added_by,created,updated) "
                  "VALUES(?,? ,?,'website',?,'human:ben',?,?)",
                  ("private-reference", "Sensitive packet reference", "https://example.test/#/task/" + row["id"],
                   "Sensitive packet", H.now(), H.now()))
        who = Identity("human:ben", "human", email="ben@acme.example")
        before = views.fleet_snapshot_cached(c, api.app.state.auth, who, lambda row: row)
        assert row["id"] in {t["id"] for t in before["tasks"]}
        c.execute("UPDATE tasks SET owner='human:ana' WHERE id=?", (row["id"],))
        after = views.fleet_snapshot_cached(c, api.app.state.auth, who, lambda row: row)
        assert row["id"] not in {t["id"] for t in after["tasks"]}
    result = get(api, "context/search?q=Sensitive&source=docs", "ben-test")
    assert "private-reference" not in json.dumps(result)


def test_private_status_and_pending_slack_posts_are_not_disclosed(api):
    row = task(api, requester="human:ben", owner="bot:ops")
    _, msg = legacy_message(api, row["id"])
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, "ops", focus="Sensitive packet", task_id=row["id"])
        c.execute("INSERT INTO slack_posts(message_id,channel,thread_ts,bot,text,state,created,updated) "
                  "VALUES(?,'Dprivate','','ops','Private queued output','ready',?,?)", (msg["id"], H.now(), H.now()))
    value = get(api, "status?bot=ops")["status"]
    assert value["task_id"] is None and value["focus"] == "" and value["open_tasks"] == 0
    assert not sql(api, "SELECT focus FROM bot_status WHERE bot='ops'")
    from backend.slack_gateway import Gateway
    from backend.tests.test_slack_gateway import FakeSlack
    slack = FakeSlack()
    gateway = Gateway(api.app.state.store, slack)
    assert gateway.deliver() == [] and slack.posts == []
    with api.app.state.store.read() as c:
        stored = c.execute("SELECT state,text FROM slack_posts WHERE message_id=?", (msg["id"],)).fetchone()
        assert tuple(stored) == ("cancelled", "")


@pytest.mark.parametrize("requester", ["human:ben", "bot:ops"])
def test_verified_legal_requester_is_kept_without_forged_human_grants(api, requester):
    with api.app.state.store.transaction() as c:
        config = H._json(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0], {})
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (encode({**config, "template": "general-counsel"}),))
    source = task(api, requester=requester, owner="bot:ops")
    machine = runner(api)
    with api.app.state.store.read() as c:
        assigned = c.execute("SELECT * FROM assignments WHERE bot='ops'").fetchone()
    if not assigned or assigned["runner_id"] != machine["runner_id"]:
        assign(api, machine, "ops", generation=assigned["generation"] if assigned else 0)
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    followup = post(api, "tasks", {"owner": "ops", "title": "Review the agreement", "body": "Review its terms."}, attempt["token"])
    assert followup["private"] and followup["requester"] == requester
    assert followup["owner"] == "bot:ops"
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
        c.execute("UPDATE tasks SET owner='human:ana' WHERE id=?", (row["id"],))
        with pytest.raises(Problem):
            task_privacy.require_payload(c, who, receipt)


@pytest.mark.parametrize("sensitive,owner,explicit,expected", [
    ("finance", "finance", False, 200),
    ("finance", "ops", False, 403),
    ("ops", "ops", False, 403),
    (None, "ops", True, 403),
])
def test_delegated_creation_keeps_defaults_and_requires_actual_bot_audience(api, sensitive, owner, explicit, expected):
    machine, _, attempt = setup_attempt(api, "finance")
    acted = Identity("human:ben", "human", email="ben@acme.example", task_actor="bot:finance",
                     runner_id=machine["runner_id"], attempt_id=attempt["id"])
    api.app.state.store.settings.test_identities["acted-test"] = acted
    with api.app.state.store.transaction() as c:
        if sensitive:
            config = H._json(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (sensitive,)).fetchone()[0], {})
            c.execute("UPDATE bot_config SET config_json=? WHERE bot=?",
                      (encode({**config, "private_tasks_default": True}), sensitive))
        before = c.execute("SELECT count(*) FROM tasks").fetchone()[0]
        rooms_before = c.execute("SELECT count(*) FROM conversations").fetchone()[0]
    created = post(api, "tasks", {"owner": owner, "title": "Review delegated work", "body": "Review it.",
                                   "private": explicit}, "acted-test", expected=expected)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == before + (expected == 200)
        if expected != 200:
            assert c.execute("SELECT count(*) FROM conversations").fetchone()[0] == rooms_before
    if expected == 200:
        assert created["private"] and created["requester"] == "human:ben" and created["owner"] == "bot:finance"
        assert get(api, "tasks/" + created["id"], "acted-test")["task"]["private"]
        # A signed-in person keeps their explicit choice to publish.
        public = post(api, "tasks", {"owner": owner, "title": "Review public work", "body": "Review it.",
                                     "private": False}, "ben-test")
        assert public["private"] is False


def test_saved_private_batch_revokes_and_never_copies_into_general_bot_room(api):
    row = task(api, requester="bot:ops", owner="human:ben")
    started = post(api, "batch", {}, "ben-test")
    bid = started["id"]
    post(api, f"batch/{bid}/respond", {"kind": "instruct", "text": "Private decision"}, "ben-test")
    post(api, f"batch/{bid}/commit", {}, "ben-test")
    with api.app.state.store.read() as c:
        delivered = list(c.execute("SELECT m.*,v.kind AS room_kind,v.task_id FROM messages m "
                                   "JOIN conversations v ON v.id=m.conversation_id WHERE m.body LIKE '%Private decision%'"))
        assert delivered and all(m["room_kind"] == "task" and m["task_id"] == row["id"] for m in delivered)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE batches SET state='open' WHERE id=?", (bid,))
        c.execute("UPDATE tasks SET owner='human:cara' WHERE id=?", (row["id"],))
    for path in ("batch", f"batch/{bid}/next", f"batch/{bid}/commit"):
        response = api.get("/api/v2/" + path, headers=headers("ben-test")) if path == "batch" else api.post(
            "/api/v2/" + path, json={}, headers=headers("ben-test"))
        assert response.status_code in (403, 404) and "Sensitive packet" not in response.text


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
        logs = list(c.execute("SELECT detail_json FROM events WHERE action='sql.query'"))
        assert logs and all(marker not in r[0] and row["id"] not in r[0] for r in logs)
        conn = SQL.connect(api.app.state.store.settings.db_path, c, api.app.state.auth,
                           Identity("human:ben", "human", email="ben@acme.example"))
        try:
            with api.app.state.store.transaction() as write:
                write.execute("UPDATE tasks SET owner='human:ana' WHERE id=?", (row["id"],))
            # An in-flight read has one coherent snapshot; the next request sees revocation.
            assert SQL.run(conn, f"SELECT id FROM tasks WHERE id='{row['id']}'", [], 500, 5)["rows"] == [[row["id"]]]
            with pytest.raises(Problem):
                SQL.run(conn, "SELECT * FROM main.messages", [], 500, 5)
        finally:
            conn.close()
    assert not sql(api, f"SELECT id FROM tasks WHERE id='{row['id']}'", "ben-test")


def test_sql_event_privacy_scales_with_matching_attempts_not_their_cross_product(api):
    private = task(api, requester="human:ben", owner="bot:ops")
    machine = runner(api)
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ana", ["bot:ops"], subject="Public work")
        private_mid = next(r["id"] for r in c.execute("SELECT * FROM messages WHERE conversation_id=?", (private["conversation_id"],)))
        private_job = c.execute("SELECT id FROM jobs WHERE message_id=?", (private_mid,)).fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,finished) "
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
                c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs_json,created) "
                          "VALUES(?,?,'human:ana','bot:ops','say','Public request','{}',?)", (mid, conv["id"], started))
                jid = c.execute("SELECT id FROM jobs WHERE message_id=?", (mid,)).fetchone()[0]
                c.execute("UPDATE jobs SET state='completed',attempt_id=? WHERE id=?", (aid, jid))
                c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,finished) "
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


def test_historical_carried_context_stays_revocable_after_pointer_changes(api):
    public = task(api, private=False, requester="human:ana", owner="bot:ops", title="Public work")
    private = task(api, requester="human:ben", owner="bot:ops", next_run=True)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = claim(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=? WHERE id=?", (attempt["id"], private["id"]))
        H.event(c, H.KEEPER, "task.next-run.carried", attempt["id"], {"tasks": [private["id"]]})
        assert task_privacy.attempt_readable(c, "bot:ops", attempt["id"])
        c.execute("UPDATE tasks SET owner='bot:finance',carried_by=NULL WHERE id=?", (private["id"],))
        assert not task_privacy.attempt_readable(c, "bot:ops", attempt["id"])
    get(api, "tasks/" + public["id"])
    get(api, f"turns/{attempt['id']}/steps", expected=404)
    assert not sql(api, f"SELECT id FROM attempts WHERE id='{attempt['id']}'")


def test_aggregate_bot_kpis_do_not_reveal_private_work_even_to_manager(api):
    row = task(api, requester="human:ben", owner="bot:ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done',done_at=? WHERE id=?", (H.now(), row["id"]))
    result = get(api, "bots/ops/kpis")
    assert result["kpis"] and all(k["latest"] is None and not k["readings"] for k in result["kpis"])
