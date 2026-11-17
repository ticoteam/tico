"""A task's documents (backend/task_documents.py): one file per fixed name per task, with numbered versions that say
who wrote each one, when, and what changed. The task's read lists them newest change first; a task with none lists none,
and the task window then shows no tabs at all."""

import pytest

from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.cards import task
from backend.tests.helpers.files import turn

SPEC = "<h1>Export spec</h1><p>Rows export as CSV.</p><script>alert(1)</script>"


def write(api, tid, name, text, token="ana-test", note=None, expected=200):
    body = {"document": name, "text": text, **({"note": note} if note else {})}
    return post(api, f"tasks/{tid}/files", body, token, expected=expected)


def documents(api, tid, token="ana-test"):
    return get(api, "tasks/" + tid, token)["task"]["documents"]


def test_a_task_without_documents_lists_none(api):
    tid = task(api, "human:ben", "Check the export")
    assert documents(api, tid) == []


def test_a_bot_writes_a_spec_and_a_person_adds_a_version_to_the_same_document(api):
    tid = task(api, "bot:ops", "Write the export spec")
    _, attempt = turn(api)
    first = write(api, tid, "Spec", SPEC, attempt["token"], note="First draft")
    assert (first["document"], first["version"]) == ("Spec", 1)
    second = write(api, tid, "spec", SPEC.replace("CSV", "CSV and JSON"), "ben-test", note="Adds JSON")
    assert (second["document"], second["file_id"], second["version"]) == ("Spec", first["file_id"], 2)

    [spec] = documents(api, tid)
    assert {k: spec[k] for k in ("name", "file_id", "version", "by")} == {
        "name": "Spec",
        "file_id": first["file_id"],
        "version": 2,
        "by": "human:ben",
    }
    assert [(v["n"], v["by"], v["note"]) for v in spec["versions"]] == [
        (2, "human:ben", "Adds JSON"),
        (1, "bot:ops", "First draft"),
    ]
    assert all(v["at"] for v in spec["versions"])

    # Each version is the cleaned page: what a reader opens is the HTML without its scripts.
    page = api.get(f"/api/v2/files/{first['file_id']}?v=1", headers=headers())
    assert page.status_code == 200 and "Export spec" in page.text and "<script" not in page.text
    assert "CSV and JSON" in api.get(f"/api/v2/files/{first['file_id']}?v=2", headers=headers()).text


def test_documents_list_newest_change_first(api):
    tid = task(api, "human:ben", "Ship the export")
    write(api, tid, "Spec", SPEC)
    write(api, tid, "QA report", "<h1>QA</h1><p>All rows match.</p>")
    assert [d["name"] for d in documents(api, tid)] == ["QA report", "Spec"]
    write(api, tid, "Spec", SPEC + "<p>Dates are UTC.</p>")
    assert [(d["name"], d["version"]) for d in documents(api, tid)] == [("Spec", 2), ("QA report", 1)]


def test_fixed_names_keep_their_spelling_and_a_bot_may_name_its_own(api, monkeypatch):
    from backend import task_documents as TD

    tid = task(api, "human:ben", "Write it up")
    for name in ("release REPORT", "Invoice", "email"):
        write(api, tid, name, "<p>Done</p>")
    own = write(api, tid, "  Release   notes ", "<p>Notes</p>")
    assert (own["document"], own["version"]) == ("Release notes", 1)
    again = write(api, tid, "release-notes", "<p>Notes, again</p>")
    assert (again["document"], again["file_id"], again["version"]) == ("Release notes", own["file_id"], 2)
    assert sorted(d["name"] for d in documents(api, tid)) == ["Email", "Invoice", "Release notes", "Release report"]
    for bad in ("", "  ", "--", "x" * 41):
        write(api, tid, bad, "<p>Hi</p>", expected=422)
    monkeypatch.setattr(TD, "MOST", 4)
    assert "at most 4" in write(api, tid, "Research", "<p>Hi</p>", expected=422)["error"]["detail"]
    write(api, tid, "Spec", "<p>Hi</p>", expected=422)
    write(api, tid, "Invoice", "<p>Paid</p>")


def test_a_document_is_html_text(api):
    tid = task(api, "human:ben", "Write it up")
    post(api, f"tasks/{tid}/files", {"document": "Spec", "content_base64": "aGk="}, expected=422)
    post(api, f"tasks/{tid}/files", {"document": "Spec", "text": "   "}, expected=422)
    uploaded = api.post(
        f"/api/v2/tasks/{tid}/files",
        data={"document": "Spec"},
        files={"file": ("spec.html", b"<p>Hi</p>", "text/html")},
        headers=headers(),
    )
    assert uploaded.status_code == 422, uploaded.text
    assert documents(api, tid) == []


def test_a_document_keeps_the_task_s_read_rules(api):
    tid = task(api, "human:ben", "Private plan", private=True)
    write(api, tid, "Spec", SPEC, "ben-test")
    write(api, tid, "Spec", SPEC, "cara-test", expected=404)
    get(api, "tasks/" + tid, "cara-test", expected=404)
    assert [d["name"] for d in documents(api, tid, "ben-test")] == ["Spec"]


def test_writing_a_document_says_so_in_the_task_history(api):
    tid = task(api, "human:ben", "Ship the export")
    write(api, tid, "Spec", SPEC)
    write(api, tid, "Spec", SPEC + "<p>v2</p>")
    events = [e for e in get(api, "tasks/" + tid)["events"] if e["field"] == "document"]
    assert [(e["new"], e["note"]) for e in events][-2:] == [("Spec", ""), ("Spec", "version 2")]


def test_tico_task_attach_doc_sends_the_html_with_its_pictures(monkeypatch, tmp_path):
    from clients import bot_files as BF, hubcli, hubtools, remotecli

    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **query):
            return {"actor": "bot:writer", "kind": "bot"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"document": "QA report", "version": 1}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

    (tmp_path / "qa.html").write_text('<h1>QA</h1><img src="shot.png">')
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    monkeypatch.setattr(BF, "checkout_root", lambda cwd=None: tmp_path)
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")
    argv = ["task", "attach", "T-1", "qa.html", "--doc", "qa-report", "--note", "All rows match"]
    remotecli.run(hubcli.parser().parse_args(argv))
    method, path, body = sent[-1]
    assert (method, path, body["document"], body["note"]) == ("POST", "tasks/T-1/files", "qa-report", "All rows match")
    assert "<h1>QA</h1>" in body["text"] and "data:image/png;base64," in body["text"]

    hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec", "text": "<p>Spec</p>"})
    assert sent[-1][2] == {"document": "Spec", "text": "<p>Spec</p>"}
    with pytest.raises(ValueError, match="text, or path"):
        hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec"})
    (tmp_path / "notes.md").write_text("# Notes")
    with pytest.raises(ValueError, match="html"):
        hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec", "path": "notes.md"})


def test_an_ordinary_upload_named_like_a_document_starts_its_own_file(api):
    # A document gets versions only by name, cleaned; a plain attachment called spec.html is a file of its own.
    tid = task(api, "human:ben", "Review the export")
    spec = write(api, tid, "Spec", "<p>Document spec</p>")
    plain = post(
        api, f"tasks/{tid}/files", {"name": "spec.html", "text": "<p>Ordinary attachment</p><script>x()</script>"}
    )
    assert plain["file_id"] != spec["file_id"]
    [doc] = documents(api, tid)
    assert (doc["file_id"], doc["version"]) == (spec["file_id"], 1)
    assert "Document spec" in api.get(f"/api/v2/files/{spec['file_id']}?v=1", headers=headers()).text
    # A second plain upload of the same name continues the plain file, not the document.
    again = post(api, f"tasks/{tid}/files", {"name": "spec.html", "text": "<p>Ordinary, again</p>"})
    assert (again["file_id"], again["version"]) == (plain["file_id"], 2)
    assert documents(api, tid)[0]["version"] == 1
