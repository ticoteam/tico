"""An agent's new draft of a task file is that file's next version, whatever its name: the task keeps one file, and
every older version still opens. An archived file stays on the task, restorable, and nothing is deleted."""

import pytest

from backend.auth import Identity
from backend.blobs import register
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.automation_cli import Client
from clients import hubcli, hubtools, remotecli
from clients.tico import MultipartBody

attach = hubtools.BY_NAME["tico_task_attach"]["fn"]


class Agent(Client):
    """The agent's CLI client over the test app, on a server that takes streamed uploads."""

    def features(self):
        return {"task_files_multipart": True}

    def post_multipart(self, path, files, fields=None, key=None):
        body = MultipartBody(files, fields or {})
        sent = {**headers(self.token), "Content-Type": body.content_type}
        r = self.api.post("/api/v2/" + path, content=b"".join(body), headers=sent)
        if r.status_code != 200:
            raise remotecli.APIError(str(r.status_code), r.text, r.status_code)
        return r.json()


def cli(api, token, monkeypatch, *argv):
    monkeypatch.setenv("HUB_API_URL", "http://testserver/api/v2")
    monkeypatch.setattr(remotecli, "Client", lambda *_, **__: Agent(api, token))
    return remotecli.run(hubcli.parser().parse_args(["task", *argv]))


def listed(api, tid):
    return {f["id"]: f for f in get(api, f"tasks/{tid}/files?all=1")["files"]}


def read(api, fid, version=None):
    return api.get(f"/api/v2/files/{fid}" + (f"?v={version}" if version else ""), headers=headers()).content


def test_an_agent_replaces_a_file_in_place_and_its_older_versions_stay(api, tmp_path, monkeypatch):
    tid = post(api, "tasks", {"title": "Demand letter", "owner": "ops", "body": "Draft the letter."})["id"]
    token = as_bot(api, "ops")
    agent = Agent(api, token)
    fid = attach(agent, {"id": tid, "name": "letter-v1.md", "text": "First draft"})["file_id"]
    # Archived by a person, the file comes back with the agent's next draft, streamed by the CLI under a new name.
    assert api.patch(f"/api/v2/files/{fid}", json={"archived": True}, headers=headers()).status_code == 200
    draft = tmp_path / "letter-v2.md"
    draft.write_text("Second draft")
    second = cli(api, token, monkeypatch, "attach", tid, str(draft), "--replaces", fid)
    assert (second["file_id"], second["version"]) == (fid, 2)
    third = attach(agent, {"id": tid, "name": "letter-final.md", "text": "Final", "replaces": fid})
    assert (third["file_id"], third["version"]) == (fid, 3)

    [file] = listed(api, tid).values()
    assert file["id"] == fid and not file["archived"] and file["name"] == "letter-final.md"
    assert {v["n"]: v["name"] for v in file["versions"]} == {1: "letter-v1.md", 2: "letter-v2.md", 3: "letter-final.md"}
    assert (read(api, fid), read(api, fid, 1), read(api, fid, 2)) == (b"Final", b"First draft", b"Second draft")
    # The archive took its versions off the task; coming back puts them on again for anyone who reads the task.
    with api.app.state.store.read() as c:
        first = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=1", (fid,)).fetchone()[0]
    assert api.get(f"/api/v2/files/{first}", headers=headers("ben-test")).content == b"First draft"

    # Only a file on this very task is replaced, and a document continues its own file.
    other = post(api, "tasks", {"title": "Other matter", "owner": "ops", "body": "Elsewhere."})["id"]
    elsewhere = {"name": "letter.md", "text": "Third", "replaces": fid}
    r = api.post(f"/api/v2/tasks/{other}/files", json=elsewhere, headers=headers(token))
    assert r.status_code == 404, r.text
    document = {"document": "Spec", "text": "<p>Spec</p>", "replaces": fid}
    r = api.post(f"/api/v2/tasks/{tid}/files", json=document, headers=headers(token))
    assert r.status_code == 422, r.text
    # The clients refuse it before anything is sent.
    for value in (fid, ""):  # an empty replaces is refused alike
        with pytest.raises(ValueError, match="replaces is for a file"):
            attach(agent, {"id": tid, "document": "Spec", "text": "<p>Spec</p>", "replaces": value})
        page = tmp_path / "spec.html"
        page.write_text("<p>Spec</p>")
        with pytest.raises(remotecli.APIError, match="replaces is for a file"):
            cli(api, token, monkeypatch, "attach", tid, str(page), "--doc", "Spec", "--replaces", value)
    assert len(listed(api, tid)) == 1


def test_an_archived_file_is_restored_by_a_participant_with_its_versions(api):
    tid = post(api, "tasks", {"title": "Warrant", "owner": "ops", "body": "Collect the files."})["id"]
    fid = post(api, f"tasks/{tid}/files", {"name": "index.md", "text": "Items 1-9"})["file_id"]
    post(api, f"tasks/{tid}/files", {"name": "index.md", "text": "Items 1-10"})
    token = as_bot(api, "ops")  # the task's owner
    archived = api.patch(f"/api/v2/files/{fid}", json={"archived": True}, headers=headers(token))
    assert archived.json() == {"file": {"id": fid, "archived": True}}
    assert listed(api, tid)[fid]["archived"]
    restored = api.patch(f"/api/v2/files/{fid}", json={"archived": False}, headers=headers(token))
    assert restored.json() == {"file": {"id": fid, "archived": False}}, restored.text
    file = listed(api, tid)[fid]
    assert not file["archived"] and file["current_version"] == 2 and read(api, fid, 1) == b"Items 1-9"
    events = get(api, f"tasks/{tid}")["events"]
    assert [(e["old"], e["new"]) for e in events if e["field"] == "file"][-2:] == [
        ("index.md", None),
        (None, "index.md"),
    ]
    # Someone who is not on the task and may not move it restores nothing.
    refused = api.patch(f"/api/v2/files/{fid}", json={"archived": True}, headers=headers("cara-test"))
    assert refused.status_code in (403, 404), refused.text


def legacy_attachment(api, tid, name, data):
    """An attachment from before versioned files: a blob linked to the task, with no file row."""
    digest = api.app.state.blobs.put(data)
    with api.app.state.store.transaction() as c:
        blob = register(c, Identity("human:ana", "owner"), digest, len(data), name, "text/plain")
        c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, blob["id"]))
    return blob["id"]


def test_an_attachment_from_before_versioned_files_is_replaced_and_archived_without_being_lost(api):
    tid = post(api, "tasks", {"title": "Subpoena", "owner": "ops", "body": "Answer it."})["id"]
    token = as_bot(api, "ops")
    old = legacy_attachment(api, tid, "subpoena.txt", b"As served")
    replaced = post(
        api, f"tasks/{tid}/files", {"name": "subpoena-notes.txt", "text": "Annotated", "replaces": old}, token
    )
    assert (replaced["file_id"], replaced["version"]) == (old, 2)
    assert (read(api, old, 1), read(api, old, 2)) == (b"As served", b"Annotated")

    kept = legacy_attachment(api, tid, "receipt.txt", b"Mailed")
    assert api.patch(f"/api/v2/files/{kept}", json={"archived": True}, headers=headers()).status_code == 200
    assert listed(api, tid)[kept]["archived"] and read(api, kept, 1) == b"Mailed"
    assert api.patch(f"/api/v2/files/{kept}", json={"archived": False}, headers=headers()).status_code == 200
    assert not listed(api, tid)[kept]["archived"]


def test_replaces_refuses_a_page_another_type_and_an_attachment_other_tasks_share(api):
    tid = post(api, "tasks", {"title": "Arbitration", "owner": "ops", "body": "File the claim."})["id"]
    token = as_bot(api, "ops")
    claim = post(api, f"tasks/{tid}/files", {"name": "claim.pdf", "content_base64": "JVBERi0xLjQK"}, token)["file_id"]
    refused = post(
        api, f"tasks/{tid}/files", {"name": "claim.md", "text": "# Claim", "replaces": claim}, token, expected=422
    )
    assert "keeps the file's type" in refused["error"]["detail"]

    page = post(api, "pages", {"task": tid, "as": "Brief", "name": "brief.html", "text": "<p>Brief</p>"}, token)
    page_id = page.get("file_id") or page["page"]["file_id"]
    body = {"name": "brief-2.html", "text": "<p>Brief 2</p>", "replaces": page_id}
    refused = post(api, f"tasks/{tid}/files", body, token, expected=422)
    assert "page publish" in refused["error"]["detail"]

    other = post(api, "tasks", {"title": "Related claim", "owner": "ops", "body": "Same exhibit."})["id"]
    shared = legacy_attachment(api, tid, "exhibit.txt", b"Exhibit A")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_assets VALUES(?,?)", (other, shared))
    body = {"name": "exhibit-2.txt", "text": "Exhibit A, marked", "replaces": shared}
    refused = post(api, f"tasks/{tid}/files", body, token, expected=422)
    assert "more than one task" in refused["error"]["detail"]


def test_a_file_published_for_a_task_and_a_version_by_its_blob_id_archive_and_restore_on_the_task(api):
    tid = post(api, "tasks", {"title": "Mailbox", "owner": "ops", "body": "Scan the mail."})["id"]
    token = as_bot(api, "ops")
    publish = hubtools.BY_NAME["tico_file_publish"]["fn"]
    published = publish(Agent(api, token), {"name": "scan.md", "text": "Letter 1", "task": tid})["file"]["id"]
    assert api.patch(f"/api/v2/files/{published}", json={"archived": True}, headers=headers(token)).status_code == 200
    assert listed(api, tid)[published]["archived"]
    restored = api.patch(f"/api/v2/files/{published}", json={"archived": False}, headers=headers(token))
    assert restored.json() == {"file": {"id": published, "archived": False}}, restored.text
    assert not listed(api, tid)[published]["archived"]

    # A version's blob id archives its own file, never a second copy of it.
    fid = post(api, f"tasks/{tid}/files", {"name": "log.md", "text": "Day 1"})["file_id"]
    with api.app.state.store.read() as c:
        blob = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=1", (fid,)).fetchone()[0]
    archived = api.patch(f"/api/v2/files/{blob}", json={"archived": True}, headers=headers())
    assert archived.json() == {"file": {"id": fid, "archived": True}}, archived.text
    files = listed(api, tid)
    assert files[fid]["archived"] and blob not in files
