"""An agent's new draft of a task file is that file's next version, whatever its name: the task keeps one file, and
every older version still opens. An archived file stays on the task, restorable, and nothing is deleted."""

import pytest

from backend.auth import Identity
from backend.blobs import register
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.automation_cli import Client
from clients import cli_spec, hubcli, hubtools, remotecli
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
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00Z' WHERE id=?", (tid,))
    restored = api.patch(f"/api/v2/files/{fid}", json={"archived": False}, headers=headers(token))
    assert restored.json() == {"file": {"id": fid, "archived": False}}, restored.text
    with api.app.state.store.read() as c:  # the task's stream and ETag move
        assert c.execute("SELECT updated_at FROM tasks WHERE id=?", (tid,)).fetchone()[0] > "2000-01-01T00:00:00Z"
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


def test_replaces_refuses_a_page_another_type_and_an_attachment_other_tasks_share(api, tmp_path, monkeypatch):
    tid = post(api, "tasks", {"title": "Arbitration", "owner": "ops", "body": "File the claim."})["id"]
    token = as_bot(api, "ops")
    claim = post(api, f"tasks/{tid}/files", {"name": "claim.pdf", "content_base64": "JVBERi0xLjQK"}, token)["file_id"]
    # A refused replacement stores no bytes, sent whole or streamed.
    blobs, stored = api.app.state.blobs, []
    monkeypatch.setattr(blobs, "put", lambda *a, **k: stored.append(a))
    monkeypatch.setattr(blobs, "put_staged", lambda *a, **k: stored.append(a))
    refused = post(
        api, f"tasks/{tid}/files", {"name": "claim.md", "text": "# Claim", "replaces": claim}, token, expected=422
    )
    assert "keeps the file's type" in refused["error"]["detail"]
    notes = tmp_path / "claim.md"
    notes.write_text("# Claim")
    with pytest.raises(remotecli.APIError, match="keeps the file's type"):
        Agent(api, token).post_multipart(f"tasks/{tid}/files", {"file": notes}, {"replaces": claim})
    assert stored == []
    monkeypatch.undo()

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
    # Adopted by an archive on its one task, it is refused again once another task links it.
    adopted = legacy_attachment(api, tid, "receipt.txt", b"Mailed")
    assert api.patch(f"/api/v2/files/{adopted}", json={"archived": True}, headers=headers()).status_code == 200
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO task_assets VALUES(?,?)", (other, adopted))
    body = {"name": "receipt-2.txt", "text": "Mailed, signed", "replaces": adopted}
    assert "more than one task" in post(api, f"tasks/{tid}/files", body, token, expected=422)["error"]["detail"]

    # A bot-wide file on the task and a link are their own files, never a task file's next version.
    publish = hubtools.BY_NAME["tico_file_publish"]["fn"]
    agent = Agent(api, token)
    wide = publish(agent, {"name": "rules.md", "text": "v1", "task": tid, "scope": "bot"})["file"]["id"]
    link = hubtools.BY_NAME["tico_file_link"]["fn"](agent, {"url": "https://docs.acme.example/d/1", "task": tid})
    for target in (wide, link["file"]["id"]):
        body = {"name": "rules-2.md", "text": "v2", "replaces": target}
        refused = post(api, f"tasks/{tid}/files", body, token, expected=422)
        assert "Only a file kept on this task" in refused["error"]["detail"]


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

    # Archive goes on its own, never beside another change.
    both = api.patch(f"/api/v2/files/{published}", json={"archived": True, "title": "Scans"}, headers=headers())
    assert both.status_code == 422 and "on its own" in both.text

    # A version's blob id archives its own file, never a second copy of it.
    fid = post(api, f"tasks/{tid}/files", {"name": "log.md", "text": "Day 1"})["file_id"]
    with api.app.state.store.read() as c:
        blob = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=1", (fid,)).fetchone()[0]
    archived = api.patch(f"/api/v2/files/{blob}", json={"archived": True}, headers=headers())
    assert archived.json() == {"file": {"id": fid, "archived": True}}, archived.text
    files = listed(api, tid)
    assert files[fid]["archived"] and blob not in files


def test_a_recording_is_replaced_under_either_name_of_its_type(api):
    tid = post(api, "tasks", {"title": "Hearing", "owner": "ops", "body": "Record it."})["id"]
    old = legacy_attachment(api, tid, "take-1.wav", b"RIFF")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE blobs SET content_type='audio/vnd.wave' WHERE id=?", (old,))
    token = as_bot(api, "ops")
    body = {"name": "take-2.wav", "content_base64": "UklGRg==", "replaces": old}
    assert post(api, f"tasks/{tid}/files", body, token)["version"] == 2


def test_who_may_not_read_a_file_or_may_not_manage_its_file_neither_archives_nor_restores_it(api):
    tid = post(api, "tasks", {"title": "Warrant", "owner": "ops", "body": "Declaration."}, "cara-test")["id"]
    token = as_bot(api, "ops")
    publish = hubtools.BY_NAME["tico_file_publish"]["fn"]
    # A page for the task that only Ana may read: Cara asked for the task, but cannot see it.
    secret = publish(Agent(api, token), {"name": "sealed.html", "text": "<p>Sealed</p>", "task": tid})["file"]["id"]
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pages(file_id,audience,created_at,updated_at) VALUES(?,?,?,?)",
            (secret, '{"people": ["ana"]}', "2026-11-20T00:00:00Z", "2026-11-20T00:00:00Z"),
        )
    for archived in (True, False):
        r = api.patch(f"/api/v2/files/{secret}", json={"archived": archived}, headers=headers("cara-test"))
        assert r.status_code == 404, r.text
    assert "sealed.html" not in str(get(api, f"tasks/{tid}")["events"])
    # A page Cara sees but may not write: she is on the task, yet only its managers and writers archive it.
    brief = publish(Agent(api, token), {"name": "brief.html", "text": "<p>Brief</p>", "task": tid})["file"]["id"]
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO pages(file_id,created_at,updated_at) VALUES(?,?,?)",
            (brief, "2026-11-20T00:00:00Z", "2026-11-20T00:00:00Z"),
        )
    r = api.patch(f"/api/v2/files/{brief}", json={"archived": True}, headers=headers("cara-test"))
    assert r.status_code == 403, r.text
    # Archived as a page, it is restored as one, by whoever may manage the page; the task's open views hear of it.
    with api.app.state.store.read() as c:
        seen = c.execute("SELECT max(seq) FROM changes").fetchone()[0] or 0
    assert api.patch(f"/api/v2/pages/{secret}", json={"archived": True}, headers=headers()).status_code == 200
    with api.app.state.store.read() as c:
        heard = c.execute(
            "SELECT subject_id FROM changes WHERE seq>? AND topic='tasks' AND kind='files'", (seen,)
        ).fetchall()
    assert (tid,) in [tuple(r) for r in heard]
    page = listed(api, tid)[secret]
    assert page["archived"] and page["page_archived"] and page["can_restore"]

    # A bot-wide file a same-name upload on the task continued keeps its own rule: its managers archive and restore.
    shared = publish(Agent(api, token), {"name": "policy.md", "text": "v1", "task": tid, "scope": "bot"})["file"]["id"]
    assert post(api, f"tasks/{tid}/files", {"name": "policy.md", "text": "v2"})["file_id"] == shared
    with api.app.state.store.read() as c:
        blob = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=2", (shared,)).fetchone()[0]
    r = api.patch(f"/api/v2/files/{blob}", json={"archived": True}, headers=headers("cara-test"))
    assert r.status_code == 403, r.text
    assert api.patch(f"/api/v2/files/{blob}", json={"archived": True}, headers=headers()).status_code == 200
    files = get(api, f"tasks/{tid}/files?all=1", token="cara-test")["files"]
    assert [(f["id"], f["archived"], f["can_restore"]) for f in files if f["name"] == "policy.md"] == [
        (shared, True, False)
    ]
    assert blob not in {f["id"] for f in files}
    assert listed(api, tid)[shared]["can_restore"]
    assert api.patch(f"/api/v2/files/{shared}", json={"archived": False}, headers=headers()).status_code == 200
    assert not listed(api, tid)[shared]["archived"]


def test_a_person_archives_and_restores_a_task_file_from_the_cli_and_the_tool(api):
    tid = post(api, "tasks", {"title": "Lease", "owner": "ops", "body": "Sign it."})["id"]
    fid = post(api, f"tasks/{tid}/files", {"name": "lease.md", "text": "Terms"})["file_id"]
    person = Client(api, "ana-test")
    for argv, archived in ((["file", "archive", fid], True), (["file", "archive", fid, "--restore"], False)):
        args = hubcli.parser().parse_args(argv)
        assert cli_spec.of(args).call(person, args) == {"file": {"id": fid, "archived": archived}}
        assert listed(api, tid)[fid]["archived"] is archived
    archive = hubtools.BY_NAME["tico_file_archive"]["fn"]
    assert archive(person, {"id": fid})["file"]["archived"] is True
    assert archive(person, {"id": fid, "restore": True})["file"]["archived"] is False
