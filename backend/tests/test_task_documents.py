"""A task's documents (backend/task_documents.py): one file per fixed name per task, with numbered versions that say
who wrote each one, when, and what changed. The task's read lists them newest change first; a task with none lists none,
and the task window then shows no tabs at all."""

import pytest

from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.cards import task
from backend.tests.helpers.files import turn
from backend.tests.helpers.threads_private_execution import private_tasks_have_an_unrelated_manager  # noqa: F401

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
    created = {d["name"]: d["created"] for d in documents(api, tid)}
    assert created["Spec"] <= created["QA report"]
    write(api, tid, "Spec", SPEC + "<p>Dates are UTC.</p>")
    assert [(d["name"], d["version"]) for d in documents(api, tid)] == [("Spec", 2), ("QA report", 1)]
    # A new version keeps when the document first appeared: the order its tabs keep (ui/app/task-docs.ts docTabs).
    assert {d["name"]: d["created"] for d in documents(api, tid)} == created


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
    for bad in ("", "  ", "--", "x" * 41, "writer", "related"):
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


def test_a_bot_s_private_run_writes_its_own_task_s_document_through_the_task_route(api):
    # A run on a private task may not publish a page (guard_write sees only the path), so `tico task attach --doc`
    # falls back to the task's files route, which is admitted for the run's own private task and keeps its readers.
    from backend.tests.helpers.threads_private_execution import private_run, task as plain_task

    row, attempt = private_run(api)
    page = {"task": row["id"], "as": "QA report", "name": "qa-report.html", "text": "<h1>QA</h1>"}
    refused = post(api, "pages", page, attempt["token"], expected=403)
    assert "company-wide" in str(refused)
    written = write(api, row["id"], "QA report", "<h1>QA</h1>", attempt["token"])
    assert written["document"] == "QA report"
    assert [d["name"] for d in documents(api, row["id"], "ben-test")] == ["QA report"]
    get(api, "tasks/" + row["id"], "cara-test", expected=404)
    get(api, f"files/{written['file_id']}", "cara-test", expected=404)
    other = plain_task(api, requester="human:ben", owner="human:ana", title="Someone else's private task")
    write(api, other["id"], "QA report", "<h1>QA</h1>", attempt["token"], expected=404)  # not its task: unseen
    # Tasks the run can see but may not carry its private work to: the destination guard refuses both.
    public = plain_task(api, requester="human:ben", owner="bot:ops", private=False, title="Company work")
    assert "company-visible" in str(
        write(api, public["id"], "QA report", "<h1>QA</h1>", attempt["token"], expected=403)
    )
    wider = plain_task(api, requester="human:cara", owner="bot:ops", title="Cara's private work")
    write(api, wider["id"], "QA report", "<h1>QA</h1>", attempt["token"], expected=403)
    assert documents(api, public["id"]) == []


def test_writing_a_document_says_so_in_the_task_history(api):
    tid = task(api, "human:ben", "Ship the export")
    write(api, tid, "Spec", SPEC)
    write(api, tid, "Spec", SPEC + "<p>v2</p>")
    events = [e for e in get(api, "tasks/" + tid)["events"] if e["field"] == "document"]
    assert [(e["new"], e["note"]) for e in events][-2:] == [("Spec", ""), ("Spec", "version 2")]


def test_tico_task_attach_doc_is_the_page_publish_with_task_and_as(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit

    from clients import bot_files as BF, hubcli, hubtools, remotecli
    from clients.tico import APIError

    sent = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **query):
            return {"actor": "bot:writer", "kind": "bot"}

        def call(self, method, path, body=None, key=None, query=None, delegate=False):
            sent.append((method, path, body))
            return {"file_id": "file-1", "version": 1}

        def post(self, path, body=None, key=None):
            return self.call("POST", path, body)

        def request(self, method, path, body=None, key=None, raw=None):
            sent.append((method, path, raw))
            return {"file_id": "file-1", "version": 1}

    (tmp_path / "qa.html").write_text('<h1>QA</h1><img src="shot.png">')
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    monkeypatch.setattr(BF, "checkout_root", lambda cwd=None: tmp_path)
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")
    argv = ["task", "attach", "T-1", "qa.html", "--doc", "qa-report"]
    remotecli.run(hubcli.parser().parse_args(argv))
    method, path, data = sent[-1]
    url = urlsplit(path)
    query = parse_qs(url.query)
    assert (method, url.path) == ("POST", "/api/v2/pages")
    assert query["task"] == ["T-1"] and query["as"] == ["qa-report"] and query["name"] == ["qa.html"]
    assert b"<h1>QA</h1>" in data and b"data:image/png;base64," in data

    noted = ["task", "attach", "T-1", "qa.html", "--doc", "QA report", "--note", "x"]
    with pytest.raises(APIError, match="takes no --note"):
        remotecli.run(hubcli.parser().parse_args(noted))

    hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec", "text": "<p>Spec</p>"})
    assert sent[-1] == ("POST", "pages", {"task": "T-1", "as": "Spec", "name": "spec.html", "text": "<p>Spec</p>"})
    with pytest.raises(ValueError, match="text, or path"):
        hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec"})
    (tmp_path / "notes.md").write_text("# Notes")
    with pytest.raises(ValueError, match="html"):
        hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec", "path": "notes.md"})
    with pytest.raises(ValueError):  # outside the checkout: refused before anything is read
        hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec", "path": "../outside.html"})


def test_tico_task_attach_doc_falls_back_to_the_task_route_when_a_private_run_may_not_publish(monkeypatch, tmp_path):
    from clients import bot_files as BF, hubcli, hubtools, remotecli
    from clients.tico import APIError

    sent = []

    def refuse_pages(path):
        if "pages" in path:
            raise APIError("privacy", "Private task work cannot publish into company-wide resources", 403)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path, **query):
            return {"actor": "bot:writer", "kind": "bot"}

        def post(self, path, body=None, key=None):
            sent.append(("POST", path, body, key))
            refuse_pages(path)
            return {"file_id": "file-1", "version": 2, "document": "QA report"}

        def request(self, method, path, body=None, key=None, raw=None):
            sent.append((method, path, raw, key))
            refuse_pages(path)
            return {}

    (tmp_path / "qa.html").write_text('<h1>QA</h1><img src="shot.png">')
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
    monkeypatch.setattr(BF, "checkout_root", lambda cwd=None: tmp_path)
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://hub.example.test")
    monkeypatch.setenv("HUB_OPERATION_ID", "op-1")
    remotecli.run(hubcli.parser().parse_args(["task", "attach", "T-1", "qa.html", "--doc", "QA report"]))
    method, path, body, key = sent[-1]
    assert (method, path, key) == ("POST", "tasks/T-1/files", "op-1:private")
    assert (
        body["document"] == "QA report" and "<h1>QA</h1>" in body["text"] and "data:image/png;base64," in body["text"]
    )

    result = hubtools.task_attach(Client(), {"id": "T-1", "document": "Spec", "text": "<p>Spec</p>"})
    assert sent[-1][:3] == ("POST", "tasks/T-1/files", {"document": "Spec", "text": "<p>Spec</p>"})
    assert (result["file_id"], result["version"]) == ("file-1", 2)

    class Other(Client):
        def post(self, path, body=None, key=None):
            raise APIError("forbidden", "No", 403)

    with pytest.raises(APIError, match="No"):  # only the privacy refusal falls back
        hubtools.task_attach(Other(), {"id": "T-1", "document": "Spec", "text": "<p>Spec</p>"})

    (tmp_path / "notes.md").write_text("# Notes")
    with pytest.raises(APIError, match="html"):  # the fallback sends only an .html file, as the page route would
        remotecli.run(hubcli.parser().parse_args(["task", "attach", "T-1", "notes.md", "--doc", "QA report"]))
    assert sent[-1][1] != "tasks/T-1/files"

    class Twice(Client):
        def post(self, path, body=None, key=None):
            refuse_pages(path)
            raise APIError("ambiguous", "Several pages", 409, extra={"pages": ["file-a", "file-b"]})

    with pytest.raises(APIError, match="file-a, file-b. A run on a private task cannot pick one"):
        hubtools.task_attach(Twice(), {"id": "T-1", "document": "Spec", "text": "<p>Spec</p>"})


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


def test_document_writes_make_a_page_and_links_without_task_document_rows(api):
    tid = task(api, "human:ben", "Write a linked spec")
    first = write(api, tid, "Spec", SPEC)
    second = write(api, tid, "Spec", SPEC + "<p>Reviewed</p>", "ben-test")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM pages WHERE file_id=?", (first["file_id"],)).fetchone()
        assert not c.execute("SELECT 1 FROM task_documents WHERE task_id=?", (tid,)).fetchone()
        links = {
            (r[0], r[1]) for r in c.execute("SELECT target,role FROM page_links WHERE page_id=?", (first["file_id"],))
        }
    assert {("task:" + tid, "Spec"), ("human:ana", "writer"), ("human:ben", "writer")} <= links
    assert second["file_id"] == first["file_id"]
    [doc] = documents(api, tid)
    assert doc["page_id"] == first["file_id"] and doc["changed_since_ok"] is False
    assert any(link["target"] == "task:" + tid and link["role"] == "Spec" for link in doc["links"])


REPORT = "# How UI works today\n\nThe web app is one page.\n"


def test_a_report_published_and_attached_by_name_is_one_file_and_a_markdown_tab(api):
    # A bot writes reports/2026-11-17-how-ui-works-today.md (the runner publishes it after the turn) and attaches the same
    # bytes by name: the task holds one file, the one attached by name, and it is a document read as Markdown.
    from backend.tests.helpers.files import publish

    tid = task(api, "bot:ops", "UI: how it works today")
    _, attempt = turn(api)
    dated = publish(
        api,
        attempt,
        "2026-11-17-how-ui-works-today.md",
        REPORT,
        task=tid,
        path="reports/2026-11-17-how-ui-works-today.md",
    )
    assert dated.status_code == 200, dated.text
    named = post(api, f"tasks/{tid}/files", {"name": "How UI works today.md", "text": REPORT}, attempt["token"])
    post(api, f"tasks/{tid}/files", {"name": "rows.csv", "text": "a,b\n1,2\n"}, attempt["token"])

    files = get(api, f"tasks/{tid}/files")["files"]
    assert sorted(f["name"] for f in files) == ["How UI works today.md", "rows.csv"]
    [doc] = documents(api, tid)
    assert {k: doc[k] for k in ("name", "file_id", "kind", "version")} == {
        "name": "How UI works today",
        "file_id": named["file_id"],
        "kind": "markdown",
        "version": 1,
    }
    revised = post(api, f"tasks/{tid}/files", {"name": "How UI works today.md", "text": REPORT + "\nUpdated."})
    assert (revised["file_id"], revised["version"]) == (named["file_id"], 2)
    [doc] = documents(api, tid)
    assert doc["kind"] == "markdown" and "page_id" not in doc
    assert [v["n"] for v in doc["versions"]] == [2, 1]

    # Changed, they are two contents again; the dated one's tab drops the date, and an HTML document of the same name wins.
    publish(
        api,
        attempt,
        "2026-11-17-how-ui-works-today.md",
        REPORT + "\nMore.\n",
        task=tid,
        path="reports/2026-11-17-how-ui-works-today.md",
    )
    assert sorted(f["name"] for f in get(api, f"tasks/{tid}/files")["files"]) == [
        "2026-11-17-how-ui-works-today.md",
        "How UI works today.md",
        "rows.csv",
    ]
    assert [(d["name"], d["kind"]) for d in documents(api, tid)] == [("How UI works today", "markdown")]
    write(api, tid, "Plan", "<p>Plan</p>")
    assert [(d["name"], d["kind"]) for d in documents(api, tid)] == [
        ("Plan", "html"),
        ("How UI works today", "markdown"),
    ]


def test_a_markdown_document_keeps_the_task_s_read_rules(api):
    tid = task(api, "human:ben", "Private notes", private=True)
    post(api, f"tasks/{tid}/files", {"name": "notes.md", "text": "# Notes"}, "ben-test")
    assert [(d["name"], d["kind"]) for d in documents(api, tid, "ben-test")] == [("Notes", "markdown")]
    get(api, "tasks/" + tid, "cara-test", expected=404)


def test_markdown_tab_names():
    from backend.task_documents import markdown_name

    assert markdown_name("2026-11-17-how-admin-tico-works-today.md") == "How admin tico works today"
    assert markdown_name("How admin-tico works today.md") == "How admin-tico works today"
    assert markdown_name("notes.MARKDOWN") == "Notes"


def test_linked_document_files_and_context_fill_their_limits_with_readable_pages(api, monkeypatch):
    from backend import files as file_store
    from backend import task_context, task_documents
    from backend.auth import Identity

    tid = task(api, "human:ben", "List linked planning documents")
    source = task(api, "human:ben", "Write planning documents")
    original = write(api, source, "Spec", SPEC)["file_id"]
    store = api.app.state.store
    visible = [f"file-{i:024x}" for i in range(205)]
    hidden = [f"file-{i:024x}" for i in range(205, 260)]
    with store.transaction() as c:
        old, recent = "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"
        for ids, at, audience in ((visible, old, None), (hidden, recent, '{"people":["ana"]}')):
            c.executemany(
                "INSERT INTO bot_files(id,bot_id,task_id,scope,identity,title,kind,mime,locator,current_version,"
                "first_activity_at,last_activity_at) SELECT ?,bot_id,task_id,scope,?,title,kind,mime,locator,1,?,? "
                "FROM bot_files WHERE id=?",
                [(fid, fid, at, at, original) for fid in ids],
            )
            c.executemany(
                "INSERT INTO pages(file_id,created_at,updated_at,audience) VALUES(?,?,?,?)",
                [(fid, at, at, audience) for fid in ids],
            )
            c.executemany(
                "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
                "SELECT ?,1,blob_id,digest,size,name,mime,actor,? FROM bot_file_versions WHERE file_id=?",
                [(fid, at, original) for fid in ids],
            )
            c.executemany(
                "INSERT INTO page_links VALUES(?,?,?,'Spec','human:ana',?)",
                [(fid, fid, f"task:{tid}", at) for fid in ids],
            )
        newest = visible[0]
        c.execute("UPDATE bot_files SET current_version=51,last_activity_at=? WHERE id=?", (recent, newest))
        c.executemany(
            "INSERT INTO bot_file_versions(file_id,version,blob_id,digest,size,name,mime,actor,created_at) "
            "SELECT ?,?,blob_id,digest,size,name,mime,actor,? FROM bot_file_versions WHERE file_id=?",
            [(newest, v, recent, original) for v in range(2, 52)],
        )
        c.execute("INSERT INTO task_file_reviews(file_id,version,note) VALUES(?,51,'Latest note')", (newest,))

    files = api.app.state.files
    reader = Identity("human:cara", "human", "cara@acme.example")
    with store.read_transaction() as c:
        trace = []
        c.set_trace_callback(trace.append)
        docs = task_documents.listing(c, files, reader, tid)
        result = files.task_listing(c, reader, tid, all_pages=True)["files"]
        batches = []
        prime_visible = file_store.prime_visible

        def primed(c, rows):
            batches.append(len(rows))
            return prime_visible(c, rows)

        monkeypatch.setattr(file_store, "prime_visible", primed)
        context = task_context.listing(c, files.auth, reader, tid)["pages"]
        c.set_trace_callback(None)
    assert len(docs) == 50 and len(result) == 200 and len(context) == 10
    assert batches == [50, 50]
    for records, key in ((docs, "file_id"), (result, "id"), (context, "id")):
        assert records[0][key] == newest
        assert {record[key] for record in records} <= set(visible)
    for records in (docs, result):
        assert [v["n"] for v in records[0]["versions"]] == list(range(51, 1, -1))
        assert records[0]["versions"][0]["note"] == "Latest note"
    review_reads = [q for q in trace if "task_file_reviews" in q]
    assert len(review_reads) <= 3, review_reads
