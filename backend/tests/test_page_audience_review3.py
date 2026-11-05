"""Page provenance survives deletion, revocation, legacy blob records and denied scan boundaries."""
import base64
import json

import pytest

from backend import counsel, events, files, org_rules, task_privacy
from backend.auth import Identity, bot_reads_task
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401
from backend.tests.test_api import api, get, headers, post, restrict  # noqa: F401
from backend.tests.test_counsel import counsel_on, source
from backend.tests.test_files import turn
from backend.tests.test_events import live  # noqa: F401
from backend.tests.test_org_rules import org
from backend.tests.test_page_audience import edit, promoted
from backend.tests.test_pages import HTML, page
from backend.tests.test_task_review import ask


def test_deletion_retains_denials_for_counsel_copies_and_citations(api):
    org(api)
    counsel_on(api)
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "bots": [H.COUNSEL]})
    tid = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    cite = {"file_id": fid, "version": 1, "quote": "Pipeline grew to 42 deals."}
    outsider = Identity("bot:finance", "bot")
    with api.app.state.store.read() as c:
        assert not H.task_private_readable(c, outsider.actor, H.task(c, tid))
        assert org_rules._cite(c, api.app.state.auth, outsider, cite) == {"hidden": True}
    response = api.delete("/api/v2/files/" + fid, headers=headers())
    assert response.status_code == 200, response.text
    with api.app.state.store.read() as c:
        tombstone = c.execute("SELECT * FROM pages WHERE file_id=?", (fid,)).fetchone()
        assert tombstone["deleted_at"] and json.loads(tombstone["audience"])["people"] == ["cara"]
        assert not c.execute("SELECT 1 FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()
        assert fid in {r["file_id"] for r in counsel.page_copies(c).values()}
        assert H.task(c, tid)["body"] == counsel.REMOVED_PAGE
        assert not H.task_private_readable(c, outsider.actor, H.task(c, tid))
        for viewer in (outsider, Identity("human:cara", "human"), Identity("human:ana", "owner")):
            assert not counsel.page_copy_readable(c, counsel.page_copies(c)[tid], viewer)
            assert org_rules._cite(c, api.app.state.auth, viewer, cite) == {"removed": True, "label": "A deleted page"}
            assert files.readable_page_refs(c, api.app.state.auth, viewer, cite) == {"removed": True, "label": "A deleted page"}
    denied = api.get("/api/v2/tasks/" + tid, headers=headers("cara-test"))
    assert denied.status_code == 404 and "42 deals" not in denied.text


def test_native_and_runner_gate_recheck_askers_inherited_read(api):
    org(api)
    counsel_on(api)
    fid, _ = promoted(api)
    edit(api, fid, {"people": ["cara"], "bots": [H.COUNSEL]})
    tid = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="cara-test")["task_id"]
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", see={"everyone": True}, read={"people": ["ben"]}, write={"everyone": True})
    with api.app.state.store.read() as c:
        task = H.task(c, tid)
        assert "42 deals" in task["body"]  # Queued text must be gated even before any scrubber runs.
        copied = counsel.page_copies(c)[tid]
        assert not counsel.page_copy_readable(c, copied, auth=api.app.state.auth)
        assert not counsel.page_copy_readable(c, copied)
        assert not H.task_private_readable(c, "bot:" + H.COUNSEL, task)
        assert not bot_reads_task(c, "bot:" + H.COUNSEL, task)
    denied = api.get("/api/v2/tasks/" + tid, headers=headers("counsel-test"))
    assert denied.status_code in (403, 404) and "42 deals" not in denied.text


def test_same_name_outsider_upload_creates_separate_file(api):
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Shared review", "body": "Review", "private": False})["id"]
    fid = page(api, attempt, task=tid, title="Audience secret").json()["page"]["file_id"]
    with api.app.state.store.read() as c:
        original = dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone())
    edit(api, fid, {"people": ["ben"]})
    made = post(api, f"tasks/{tid}/files", {"name": "weekly.html", "text": "An independent upload"}, token="cara-test")
    assert made["file_id"] != fid and made["version"] == 1 and fid not in json.dumps(made)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT current_version FROM bot_files WHERE id=?", (fid,)).fetchone()[0] == 1
        assert dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()) == original


def test_asks_raw_task_attachments_and_blob_history_are_scrubbed_after_narrowing(api):
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Shared review", "body": "Review", "private": False})["id"]
    fid = page(api, attempt, task=tid, title="Audience secret").json()["page"]["file_id"]
    question_ask = ask("cara")
    question_ask["questions"][0]["options"][0]["file"] = fid + "@1"
    question = post(api, f"tasks/{tid}/comments", {"text": "Review", "ask": question_ask, "attachments": [fid + "@1"]})["comment"]
    with api.app.state.store.transaction() as c:
        version = c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()
        blob = dict(c.execute("SELECT * FROM blobs WHERE id=?", (version["blob_id"],)).fetchone())
        c.execute("INSERT OR IGNORE INTO task_assets VALUES(?,?)", (tid, blob["id"]))
        from backend.task_review import file_added
        file_added(c, Identity("bot:ops", "bot"), tid, blob, 1)
    assert fid in json.dumps(get(api, "tasks/" + tid, token="cara-test")["task"]["ask"])
    edit(api, fid, {"people": ["ben"]})
    for path in ("tasks/" + tid, "tasks", "tasks/" + tid + "/comments", "messages/" + question["id"]):
        result = get(api, path, token="cara-test")
        for secret in (fid, blob["id"], "Audience secret", "weekly.html", version["digest"]):
            assert secret not in json.dumps(result), (path, secret, result)
    with api.app.state.store.read() as c:
        who = Identity("human:cara", "human", "cara@acme.example")
        record = dict(c.execute("SELECT * FROM events WHERE action='task.file' AND target=? ORDER BY rowid DESC", (tid,)).fetchone())
        assert not files.page_event_readable(c, api.app.state.auth, who, tid, record["detail"])
        assert not task_privacy.event_readable(c, who.actor, record)
        for identity in (blob["id"], blob["id"] + "@1", fid + "@1", "/api/v2/files/" + blob["id"],
                         "/api/v2/files/" + fid + "/versions/1", "/api/v2/files/" + blob["id"] + "/thumb?v=1",
                         "/api/v2/files/" + fid + "/poster?v=1", version["digest"]):
            assert files.page_references(identity, c) == {fid}
    query = "SELECT * FROM history_events WHERE subject_id='" + tid + "' AND kind='file'"
    assert post(api, "sql", {"sql": query}, token="cara-test")["rows"] == []


@pytest.mark.parametrize("endpoint,field", [("files", "files"), ("pages", "pages")])
def test_encrypted_cursor_passes_five_thousand_hidden_rows(api, endpoint, field):
    fid, _ = promoted(api)
    stamp = "2099-09-09T09:09:09.999999Z"
    with api.app.state.store.transaction() as c:
        for number in range(5000):
            hidden = "file-" + format(number, "024x")
            c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,current_version,first_activity_at,last_activity_at) "
                      "VALUES(?,'ops','bot',?,'Hidden folio','page','text/html','tico_blob',1,?,?)", (hidden, hidden, stamp, stamp))
            c.execute("INSERT INTO pages(file_id,audience,created_at,updated_at) VALUES(?,?,?,?)",
                      (hidden, json.dumps({"people": ["ben"]}), stamp, stamp))
    first = get(api, endpoint, token="cara-test")
    assert first[field] == [] and first["has_more"]
    raw = base64.urlsafe_b64decode(first["next_cursor"])
    assert b"file-" not in raw and stamp.encode() not in raw
    second = get(api, endpoint + "?cursor=" + first["next_cursor"], token="cara-test")
    assert fid in json.dumps(second[field]) and not second["has_more"]
    invalid = first["next_cursor"][:-8] + "AAAAAAAA"
    assert api.get("/api/v2/" + endpoint, params={"cursor": invalid}, headers=headers("cara-test")).status_code == 422


@pytest.mark.slow
def test_restricted_page_leak_matrix_across_stored_shapes_transports_and_deletion(api, live):
    """One canary follows every persisted attachment form through both outsider identities and a real SSE socket."""
    from backend.tests.test_api import assign, ready, runner
    from backend.tests.test_events import ready as stream_ready
    from backend.tests.test_assistant import room
    from backend.task_review import file_added

    org(api)
    counsel_on(api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO humans(id,name,email) VALUES('reader','Reader','reader@acme.example')")
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        roster["people"].append({"id": "reader", "email": "reader@acme.example"})
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
    api.app.state.store.settings.test_identities["reader-test"] = Identity("human:reader", "human", "reader@acme.example")
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "finance", "title": "Review folios", "body": "Review", "private": False}, token="ops-test")["id"]
    name, title = "canary-folio-private.html", "Canary folio confidential"
    document = b"<html><body><p>Canary confidential quote.</p>" + b"x" * 81917 + b"</body></html>"
    made = page(api, attempt, task=tid, name=name, title=title, data=document)
    assert made.status_code == 200, made.text
    fid = made.json()["page"]["file_id"]
    ordinary = page(api, attempt, task=tid, name="ordinary.html", title="Ordinary folio", data=HTML).json()["page"]["file_id"]
    question = ask("reader")
    question["questions"][0]["options"][0]["file"] = fid + "@1"
    comment = post(api, f"tasks/{tid}/comments", {"text": "Review the folio", "attachments": [fid + "@1"], "ask": question}, token="cara-test")["comment"]
    patched = api.patch(f"/api/v2/files/{fid}/versions/1", json={"note": "Review", "ask": ask("reader")}, headers=headers(attempt["token"]))
    assert patched.status_code == 200, patched.text
    answered = post(api, f"tasks/{tid}/answers", {"target": {"file": fid, "version": 1}, "answers": {"verdict": ["Approve"]}}, token="reader-test")
    # Keep a separate unanswered ask and its file review: task summaries must scrub its raw refs too.
    assistant = post(api, "assistant/messages", {"text": "Review folio", "page": {"path": "#/page/" + fid, "title": title}}, token="cara-test")["message"]
    assistant_room = room(api, "cara-test")["room_id"]
    post(api, "pins", {"place": "dashboard", "item": "page:" + fid})
    post(api, "pins", {"place": "person:cara", "item": "page:" + fid}, token="cara-test")
    read_task = post(api, "rules/read", {"file_id": fid, "source_id": source(api)}, token="reader-test")["task_id"]
    rule = post(api, "rules", {"kind": "external", "text": "Check the folio before publishing.", "who": {"everyone": True},
                               "source_id": source(api), "cite": {"file_id": fid, "version": 1, "quote": "Canary confidential quote."}})
    rid = rule["id"]
    with api.app.state.store.transaction() as c:
        version = dict(c.execute("SELECT * FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone())
        bid = version["blob_id"]
        blob = dict(c.execute("SELECT * FROM blobs WHERE id=?", (bid,)).fetchone())
        metadata = {"file_id": fid, "ref": fid + "@1", "title": title, "name": name, "size": version["size"],
                    "version": 1, "digest": version["digest"], "kind": "page"}
        c.execute("INSERT OR IGNORE INTO task_assets VALUES(?,?)", (tid, bid))
        file_added(c, Identity("bot:ops", "bot"), tid, blob, 1)
        for mid in (comment["id"], answered["comment"]["id"], assistant["id"]):
            refs = H.message(c, mid)["refs"]
            refs.update({"review": {"files": [metadata]}, "visual": metadata,
                         "blob_reference": {**metadata, "file_id": bid, "ref": bid + "@1"},
                         "version_reference": {**metadata, "file_id": fid + "@1"}})
            c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(refs), mid))
        H.history_event(c, "task", tid, "bot:ops", "file", new=name, data={"file": bid, "version": 1})
        H.event(c, "bot:ops", "message.visual", tid, {"visual": metadata})
        cid = H.task(c, tid)["conversation_id"]
        secrets = (fid, bid, title, name, str(version["size"]), fid + "@1", version["digest"], version["source_digest"], "Canary confidential quote.")
    api.app.state.store.settings.visual_origin = "https://visuals.acme.example"
    signed = get(api, "pages/" + fid, token="cara-test")["page"]["src"]
    assert api.get(signed).status_code == 200
    # Before restriction all these are ordinary task references, including the no-audience control page.
    for token in ("cara-test", "finance-test", "reader-test", "ben-test"):
        assert fid in json.dumps(get(api, "pages/" + fid, token=token))
        assert ordinary in json.dumps(get(api, "pages/" + ordinary, token=token))
    edit(api, fid, {"people": ["reader"], "bots": [H.COUNSEL]})
    for token in ("reader-test", "ben-test", attempt["token"]):
        assert fid in json.dumps(get(api, "pages/" + fid, token=token))
    machine = runner(api)
    assign(api, machine, "finance")
    ready(api, machine, ["finance"])
    failures = []

    def clean(label, value):
        serialized = json.dumps(value, default=str)
        found = [secret for secret in secrets if secret and secret in serialized]
        if found:
            failures.append((label, found))

    def response(label, result):
        if result.status_code not in (200, 403, 404):
            failures.append((label, result.status_code, result.text))
        clean(label, {"body": result.text, "headers": dict(result.headers)})

    common = ["pages", "pages?ids=" + fid, "pages?task=" + tid, "pages/" + fid,
              "files", "files?kind=page", "files?task=" + tid, "files/search?q=Canary", "files/search?q=Canary&kind=page",
              "bots/ops/files", "tasks", "tasks?owner=finance", "tasks/" + tid, "tasks/" + read_task,
              "tasks/" + tid + "/files", "tasks/" + tid + "/comments", "tasks/" + tid + "/answers", "tasks/" + tid + "/tree",
              "tasks/" + tid + "/links", "places/dashboard", "places/task:" + tid, "places/person:cara",
              "messages/" + comment["id"], "messages/" + answered["comment"]["id"], "messages/" + assistant["id"],
              "conversations", "conversations/" + cid + "/messages", "conversations/" + cid + "/outline",
              "threads", "threads/search?q=Review", "threads/search?q=Canary", "context/search?q=Canary",
              "threads/" + cid, "threads/" + cid + "/timeline", "threads/" + cid + "/members",
              "rules?status=any", "rules/" + rid, "rules/counts", "bots/ops/rules", "bots/finance/rules", "audit",
              "needs-you", "needs-you/summary", "updates", "updates/unread", "updates/tree", "inbox", "answers?ids=" + comment["id"], "status", "live/brief",
              "bots/ops/history", "bots/finance/history", "bots/ops/turns", "bots/finance/turns",
              "threads/catch-up", "threads/rail", "threads/browse", "threads/home/bot:finance"]
    for ident in (fid, bid):
        common.extend("files/" + ident + suffix for suffix in ("", "/meta", "/versions", "/versions/1", "/activity", "/thumb", "/poster"))
    sql_queries = ["SELECT * FROM " + table for table in ("bot_files", "bot_file_versions", "blobs", "messages", "events", "history_events", "task_assets", "task_file_reviews")]
    for deleted in (False, True):
        if deleted:
            removed = api.delete("/api/v2/files/" + fid, headers=headers())
            assert removed.status_code == 200, removed.text
        state = "deleted" if deleted else "restricted"
        for token, who in (("cara-test", Identity("human:cara", "human", "cara@acme.example")),
                           ("finance-test", Identity("bot:finance", "bot", agent="test"))):
            for path in common:
                response(state + ":" + token + ":" + path, api.get("/api/v2/" + path, headers=headers(token)))
            for ident in (fid, bid):
                for suffix in ("", "/versions/1", "/thumb", "/poster"):
                    result = api.head("/api/v2/files/" + ident + suffix, headers=headers(token))
                    assert result.status_code in (403, 404, 405), result.status_code
                    clean(state + ":HEAD:" + token + ":" + suffix, dict(result.headers))
            for query in sql_queries:
                response(state + ":" + token + ":" + query, api.post("/api/v2/sql", json={"sql": query}, headers=headers(token)))
            response(state + ":claim rejected:" + token, api.post("/api/v2/jobs/claim", json={}, headers=headers(token)))
            if token == "cara-test":
                for path in ("assistant", "assistant/decisions", "threads/" + assistant_room + "/timeline"):
                    response(state + ":" + path, api.get("/api/v2/" + path, headers=headers(token)))
            # A new committed message exercises task/list/message hydration on the same event stream as the app.
            with api.app.state.store.read() as c:
                before = events.latest(c)
            marker = post(api, f"tasks/{tid}/comments", {"text": "Visibility marker " + state})["comment"]["id"]
            wanted = "messages,threads" if who.role == "bot" else "tasks,messages,bots,runs"
            stream = live(token, "?topics=" + wanted + "&after=" + str(before))
            assert stream.status == 200
            stream_ready(stream)
            stream.until(lambda frame: frame["event"] == "messages" and frame["data"].get("id") == marker)
            clean(state + ":SSE:" + token, {"frames": stream.seen, "headers": stream.response.getheaders()})
            stream.close()
            with api.app.state.store.read() as c:
                delivered, _, _, _ = events.read(c, api.app.state.auth, who, before, topics=("messages", "bots", "runs"))
                clean(state + ":events.read:" + token, delivered)
                for record in c.execute("SELECT * FROM events WHERE target IN (?,?)", (fid, tid)):
                    if task_privacy.event_readable(c, who.actor, record):
                        clean(state + ":event guard:" + token, dict(record))
            assert ordinary in json.dumps(get(api, "pages/" + ordinary, token=token))
        response(state + ":signed visual", api.get(signed))
        response(state + ":runner claim", api.post("/api/v2/jobs/claim",
                 json={"bot": "finance", "start_places": True}, headers=headers(machine["token"], "matrix-claim")))
    assert not failures, json.dumps(failures, indent=2)


def test_migration_links_name_only_history_before_page_content_is_purged(api):
    import importlib
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Legacy review", "body": "Review", "private": False})["id"]
    fid = page(api, attempt, task=tid).json()["page"]["file_id"]
    with api.app.state.store.transaction() as c:
        legacy = H.history_event(c, "task", tid, "bot:ops", "file", new="weekly.html")
        migration = importlib.import_module("backend.migrations.20261105050905_page_tombstones")
        migration.up(c)
        migration.up(c)
        data = json.loads(c.execute("SELECT data FROM history_events WHERE id=?", (legacy,)).fetchone()[0])
        assert data["page_files"] == [fid]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        result = get(api, "tasks/" + tid, token="cara-test")
        assert "weekly.html" not in json.dumps(result) and fid not in json.dumps(result)


def test_same_name_upload_never_adopts_deleted_page_bytes_retained_by_a_meeting(api):
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Retained review", "body": "Review", "private": False})["id"]
    fid = page(api, attempt, task=tid).json()["page"]["file_id"]
    with api.app.state.store.transaction() as c:
        bid = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]
        c.execute("INSERT OR IGNORE INTO task_assets VALUES(?,?)", (tid, bid))
        meeting = H.new_id()
        c.execute("INSERT INTO meetings(id,title,owner,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  (meeting, "Retained source", "human:ben", '{}', H.now(), H.now()))
        c.execute("INSERT INTO media_assets(meeting_id,blob_id,kind) VALUES(?,?,'attachment')", (meeting, bid))
    edit(api, fid, {"people": ["ben"]})
    assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM blobs WHERE id=?", (bid,)).fetchone()
        assert not c.execute("SELECT 1 FROM bot_file_versions WHERE blob_id=?", (bid,)).fetchone()
    made = post(api, f"tasks/{tid}/files", {"name": "weekly.html", "text": "Independent replacement"}, token="cara-test")
    assert made["version"] == 1 and made["file_id"] not in (fid, bid)
    assert fid not in json.dumps(made) and bid not in json.dumps(made)
    with api.app.state.store.read() as c:
        versions = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=?", (made["file_id"],)).fetchall()
        assert len(versions) == 1 and versions[0][0] != bid
