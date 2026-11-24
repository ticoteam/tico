"""A chat's Word and Excel files get an HTML preview the preview worker makes through backend/office_preview.py inside
backend/limited.py: the card reads it at /api/v2/files/<id>/preview, which checks its reader as the file does. A file
that unpacks past the limits gets none and keeps its Download card, and the preview goes with its file. Fictional
people only: ana and ben share a thread, cara is in none of it."""

import re

import pytest

from backend import file_library, file_metadata, office_preview
from backend.tests.helpers.api import headers
from backend.tests.helpers.office_files import DOCX, PKG, R, S, W, XLSX, bomb, docx, xlsx
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
    waiting = api.get(word["url"] + "/preview", headers=headers("ben-test"))
    assert waiting.status_code == 202 and not waiting.content  # being made: the card asks again
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


def test_a_word_file_sent_with_no_type_is_known_by_its_name(api):
    drive = worker(api)
    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("plan.docx", docx([(None, "Hello")]), "application/octet-stream")])
    (f,) = message["refs"]["attachments"]
    assert drive.batch() is True
    assert "Hello" in api.get(f["url"] + "/preview", headers=headers("ben-test")).text


def test_a_preview_made_for_a_document_deleted_meanwhile_leaves_no_bytes_behind(api, monkeypatch):
    """The Word file goes between the conversion and the save: the preview page already stored is queued and removed."""
    drive = worker(api)
    blobs = api.app.state.blobs
    thread = new_thread(api, members=("human:ben",))
    message = upload(api, thread["id"], [("plan.docx", docx([(None, "Hello")]), DOCX)])
    save, made = drive.save, []

    def deleted_first(blob, result, prepared):
        with api.app.state.store.transaction() as c:
            assert file_library.release_message_files(c, "human:ana", message["id"]) == 1
        made.extend(upload["digest"] for field, upload in prepared if field == "preview_html_blob_id")
        save(blob, result, prepared)

    monkeypatch.setattr(drive, "save", deleted_first)
    drive.batch()
    assert made and blobs.get(made[0])  # stored before the save found its source gone
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM blob_purges WHERE digest=?", (made[0],)).fetchone()
        assert c.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0
    monkeypatch.setattr(blobs, "_fresh", {})  # past the wait for fresh writes
    file_library.drain(api.app.state.store, blobs)
    with pytest.raises(Exception):
        blobs.get(made[0])


def sheet_xml(rows):
    return f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>{rows}</sheetData></worksheet>'


def test_sheet_cells_show_as_stored_and_dates_by_excels_own_calendar():
    rows = (
        '<row r="0"><c r="A0"><v>9</v></c></row>'  # no row 0 in a sheet: left out
        '<row r="1"><c r="A1"><v>1234567890.01</v></c><c r="B1" t="s"><v/></c><c r="C1"><v>NaN</v></c>'
        '<c r="D1" s="1"><v>inf</v></c><c r="E1" s="1"><v>61</v></c><c r="F1" t="s"><v>0</v></c></row>'
    )
    out = office_preview.sheet(xlsx([("One", [["first"]])], extra={"xl/worksheets/sheet1.xml": sheet_xml(rows)}))
    assert (
        '<tbody><tr><th>1</th><td class="n">1234567890.01</td><td></td><td class="n">NaN</td><td class="n">inf</td>'
        '<td class="n">1900-03-01</td><td>first</td></tr></tbody>'
    ) in out
    when = office_preview.excel_date
    assert [when(1, False), when(59, False), when(60, False), when(61, False), when(45627, False)] == [
        "1900-01-01",
        "1900-02-28",
        "1900-02-29",  # the day Excel keeps from Lotus
        "1900-03-01",
        "2024-12-01",
    ]
    assert when(0.5, False) == "12:00" and when(45627.25, False) == "2024-12-01 06:00" and when(0, True) == "1904-01-01"
    assert when(float("nan"), False) is None and when(-1, False) is None


def test_the_first_worksheet_shows_past_a_chart_and_only_parsed_parts_are_checked():
    rels = (
        f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rId1" Type="{R}/chartsheet" Target="chartsheets\\sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{R}/worksheet" Target="worksheets\\sheet2.xml"/></Relationships>'
    )
    book = (
        f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Chart" sheetId="1" r:id="rId1"/>'
        f'<sheet name="Data" sheetId="2" r:id="rId2"/></sheets></workbook>'
    )
    parts = {
        "xl/workbook.xml": book,
        "xl/_rels/workbook.xml.rels": rels,
        "xl/worksheets/sheet2.xml": sheet_xml('<row r="1"><c r="A1"><v>7</v></c></row>'),
        "xl/chartsheets/sheet1.xml": "<!DOCTYPE chart>",  # never parsed, so never refused
        "xl/media/logo.svg": "<!DOCTYPE svg>",
    }
    out = office_preview.sheet(xlsx([("x", [["y"]])], extra=parts))
    assert 'data-sheet="Data" data-sheets="2"' in out and '<td class="n">7</td>' in out
    with pytest.raises(ValueError, match="XML features"):
        office_preview.sheet(xlsx([("x", [["y"]])], extra={**parts, "xl/worksheets/sheet2.xml": "<!DOCTYPE x>"}))


def test_a_word_part_is_checked_wherever_it_lives_and_pictures_are_not_read():
    picture = (
        f'<Relationships xmlns="{PKG}"><Relationship Id="rId9" Type="{R}/image" Target="media/a.svg"/></Relationships>'
    )
    shown = docx([(None, "Hi")], extra={"word/media/a.svg": "<!DOCTYPE svg>", "word/_rels/document.xml.rels": picture})
    assert "Hi" in office_preview.word(shown)
    moved = (
        f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{R}/officeDocument" '
        f'Target="word/media/doc.xml"/></Relationships>'
    )
    hidden = f'<!DOCTYPE x><w:document xmlns:w="{W}"><w:body/></w:document>'
    with pytest.raises(ValueError, match="XML features"):
        office_preview.word(docx([(None, "Hi")], extra={"_rels/.rels": moved, "word/media/doc.xml": hidden}))


def test_one_long_paragraph_is_cut_inside_its_words_not_to_nothing():
    out = office_preview.word(docx([(None, "é & " * 100_000)]))
    assert len(out.encode()) <= office_preview.MAX_HTML and out.endswith("</body></html>")
    body = out.split("<body>", 1)[1]
    assert body.startswith("<p>é &amp; é") and not re.search(r"&(?!amp;)", body)  # no entity cut in half
