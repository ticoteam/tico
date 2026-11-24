"""A chat's Word and Excel files get an HTML preview the preview worker makes through backend/office_preview.py inside
backend/limited.py: the card reads it at /api/v2/files/<id>/preview, which checks its reader as the file does. A file
that unpacks past the limits gets none and keeps its Download card, and the preview goes with its file. Fictional
people only: ana and ben share a thread, cara is in none of it."""

from backend import file_library, file_metadata, office_preview
from backend.tests.helpers.api import headers
from backend.tests.helpers.office_files import DOCX, XLSX, bomb, docx, xlsx
from backend.tests.helpers.thread_api import new_thread


def worker(api):
    """The preview worker, driven by the test: the server's own loop is stopped first, so it never takes a row."""
    running = api.app.state.file_metadata
    running.stop.set()
    running.wake.set()
    return file_metadata.Metadata(api.app.state.store, api.app.state.blobs)


def upload(api, thread_id, files):
    sent = api.post(
        f"/api/v2/uploads/threads/{thread_id}/messages",
        data={"text": "The numbers"},
        files=[("files", f) for f in files],
        headers=headers("ana-test"),
    )
    assert sent.status_code == 200, sent.text
    return sent.json()["message"]


def test_word_and_excel_files_get_previews_only_their_readers_open(api):
    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    report = docx([("Heading1", "Quarterly plan"), (None, "Rent & food <b>")])
    budget = xlsx([("Budget", [["Item", "Cost", "Due"], ["Rent", 1200, (45627, 1)]]), ("Notes", [["x"]])])
    message = upload(api, thread["id"], [("plan.docx", report, DOCX), ("budget.xlsx", budget, XLSX)])
    word, sheet = message["refs"]["attachments"]
    assert api.get(word["url"] + "/preview", headers=headers("ben-test")).status_code == 404  # not made yet
    assert drive.batch() is True
    shown = api.get(word["url"] + "/preview", headers=headers("ben-test"))
    assert shown.status_code == 200 and shown.headers["content-type"].startswith("text/html")
    assert "sandbox" in shown.headers["content-security-policy"]
    assert shown.headers["content-disposition"].startswith("attachment")  # never drawn on the app's own address
    assert "<h1>Quarterly plan</h1>" in shown.text and "Rent &amp; food &lt;b&gt;" in shown.text
    table = api.get(sheet["url"] + "/preview", headers=headers("ben-test")).text
    assert 'data-sheet="Budget" data-sheets="2"' in table
    assert "<td>Rent</td>" in table and ">1200<" in table and ">2024-12-01<" in table
    assert api.get(sheet["url"] + "/preview", headers=headers("cara-test")).status_code in (403, 404)
    with api.app.state.store.read() as c:
        states = {r[0] for r in c.execute("SELECT media_state FROM blob_media")}
    assert states == {"ready"}


def test_a_zip_bomb_ends_in_its_own_process_and_keeps_the_download_card(api):
    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("bomb.docx", bomb("docx"), DOCX), ("bomb.xlsx", bomb("xlsx"), XLSX)])
    assert drive.batch() is True
    for f in message["refs"]["attachments"]:
        assert api.get(f["url"] + "/preview", headers=headers("ben-test")).status_code == 404
        assert api.get(f["url"], headers=headers("ben-test")).status_code == 200  # the file itself still opens
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT media_state,preview_html_blob_id FROM blob_media").fetchall()
    assert [tuple(r) for r in rows] == [("none", None), ("none", None)]
    assert drive.batch() is False  # given up, never read again


def test_a_removed_message_takes_its_document_preview_with_it(api):
    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("plan.docx", docx([(None, "Hello")]), DOCX)])
    drive.batch()
    with api.app.state.store.transaction() as c:
        (preview,) = c.execute("SELECT preview_html_blob_id FROM blob_media").fetchone()
        assert preview
        assert file_library.release_message_files(c, "human:ana", message["id"]) == 1
        assert c.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM blob_media").fetchone()[0] == 0


def test_a_sheet_preview_is_its_first_rows_and_columns_under_the_size_cap():
    wide = [[f"r{r}c{c}" for c in range(40)] for r in range(1, 251)]
    out = office_preview.sheet(xlsx([("Big", wide), ("Hidden", [["x"]])]))
    assert len(out.encode()) <= office_preview.MAX_HTML
    assert "<td>r1c0</td>" in out and "<td>r1c29</td>" in out and "r1c30" not in out
    assert "r201c0" not in out
    long = [["x" * 1000] * 30 for _ in range(200)]
    capped = office_preview.sheet(xlsx([("Long", long)]))
    assert len(capped.encode()) <= office_preview.MAX_HTML and capped.endswith("</nav></body></html>")
