"""Unversioned legacy attachment links retain their meaning after file adoption."""
from backend.auth import Identity
from backend.blobs import register
from backend.tests.test_api import api, headers, post  # noqa: F401


def test_adopted_legacy_urls_serve_original_and_new_ids_serve_latest(api):
    tid = post(api, "tasks", {"title": "Review versions", "owner": "ops", "body": "Read the report."})["id"]
    blobs = api.app.state.blobs
    digest = blobs.put(b"original")
    with api.app.state.store.transaction() as c:
        old = register(c, Identity("bot:ops", "bot"), digest, 8, "legacy.txt", "text/plain")
        c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, old["id"]))
    path = "/api/v2/files/" + old["id"]
    assert api.get(path, headers=headers()).content == b"original"
    adopted = post(api, f"tasks/{tid}/files", {"name": "legacy.txt", "text": "revised"})
    assert adopted["file_id"] == old["id"] and adopted["version"] == 2
    assert api.get(path, headers=headers()).content == b"original"
    assert api.get(path + "?v=1", headers=headers()).content == b"original"
    assert api.get(path + "?v=2", headers=headers()).content == b"revised"
    listing = api.get("/api/v2/bots/ops/files", headers=headers()).json()
    opened = next(file for file in listing["files"] if file["id"] == old["id"])["open"]["url"]
    assert opened == path + "?v=2"
    assert api.get(opened, headers=headers()).content == b"revised"
    assert api.head(path, headers=headers()).headers["content-length"] == "8"
    assert api.get(path + "/meta", headers=headers()).json()["size"] == 8
    assert api.get(path + "/meta?v=2", headers=headers()).json()["size"] == 7
    assert api.get(path).status_code == 401
    assert api.get(path + "?v=3", headers=headers()).status_code == 404
    made = post(api, f"tasks/{tid}/files", {"name": "new.txt", "text": "first"})
    updated = post(api, f"tasks/{tid}/files", {"name": "new.txt", "text": "latest"})
    assert made["file_id"] == updated["file_id"] and made["file_id"].startswith("file-")
    url = "/api/v2/files/" + made["file_id"]
    assert api.get(url, headers=headers()).content == b"latest"
    assert api.get(url + "?v=1", headers=headers()).content == b"first"
    # Each file added is in the task's activity, a later version saying which.
    events = api.get(f"/api/v2/tasks/{tid}", headers=headers()).json()["events"]
    added = [(e["new"], e["note"]) for e in events if e["field"] == "file"]
    assert added == [("legacy.txt", "version 2"), ("new.txt", ""), ("new.txt", "version 2")]
    # Removing a file takes it off the task and says so in the activity, by name.
    removed = api.patch(url, json={"archived": True}, headers=headers())
    assert removed.status_code == 200, removed.text
    detail = api.get(f"/api/v2/tasks/{tid}", headers=headers()).json()
    assert [(e["old"], e["new"]) for e in detail["events"] if e["field"] == "file"][-1] == ("new.txt", None)
    assert "new.txt" not in [a["name"] for a in detail["task"]["attachments"]]
