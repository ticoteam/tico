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
        for viewer in (outsider, Identity("human:cara", "human"), Identity("human:ana", "owner")):
            assert not counsel.page_copy_readable(c, counsel.page_copies(c)[tid], viewer)
            assert org_rules._cite(c, api.app.state.auth, viewer, cite) == {"removed": True, "label": "A deleted page"}
            assert files.readable_page_refs(c, api.app.state.auth, viewer, cite) == {"removed": True, "label": "A deleted page"}
    assert "42 deals" not in json.dumps(get(api, "tasks/" + tid, token="cara-test"))


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
    assert api.get("/api/v2/tasks/" + tid, headers=headers("counsel-test")).status_code == 404


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
    question = post(api, f"tasks/{tid}/comments", {"text": "Review", "ask": ask("cara"), "attachments": [fid + "@1"]})["comment"]
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
                         "/api/v2/files/" + fid + "/versions/1", version["digest"]):
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
            c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,mime,locator,first_activity_at,last_activity_at) "
                      "VALUES(?,'ops','bot',?,'Hidden folio','page','text/html','tico_blob',?,?)", (hidden, hidden, stamp, stamp))
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
