"""Word import (backend/docs_import.py from_docx): mammoth reads the file as HTML, which becomes Markdown as an HTML
import does. The file is checked before mammoth reads it."""

import io
import zipfile

import pytest

from backend import docs_import

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
LINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def para(text, style="", bold=False, num=None):
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + (
        f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>' if num else ""
    )
    run = ("<w:rPr><w:b/></w:rPr>" if bold else "") + f"<w:t xml:space='preserve'>{text}</w:t>"
    return f"<w:p><w:pPr>{props}</w:pPr><w:r>{run}</w:r></w:p>"


def docx(body, rels="", numbering="", parts=None):
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W} {R}><w:body>{body}</w:body></w:document>'
    relations = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", relations)
        if numbering:
            archive.writestr("word/numbering.xml", f'<?xml version="1.0"?><w:numbering {W}>{numbering}</w:numbering>')
        for name, data in (parts or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_word_file_keeps_its_title_headings_lists_tables_and_web_links():
    numbering = (
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    )
    rels = (
        f'<Relationship Id="rWeb" Type="{LINK}" Target="https://acme.example/plan" TargetMode="External"/>'
        f'<Relationship Id="rScript" Type="{LINK}" Target="javascript:alert(1)" TargetMode="External"/>'
        f'<Relationship Id="rFile" Type="{LINK}" Target="file:///etc/passwd" TargetMode="External"/>'
    )

    def link(rid, text):
        return f'<w:hyperlink r:id="{rid}"><w:r><w:t>{text}</w:t></w:r></w:hyperlink>'

    body = (
        para("Launch plan", "Title")
        + para("Goals", "Heading1")
        + para("Ship it", bold=True)
        + para("First", num=(1, 0))
        + para("Second", num=(1, 0))
        + para("Point", num=(2, 0))
        + f'<w:p>{link("rWeb", "the plan")}<w:r><w:t xml:space="preserve"> and </w:t></w:r>{link("rScript", "this")}'
        f'<w:r><w:t xml:space="preserve"> and </w:t></w:r>{link("rFile", "that")}'
        '<w:hyperlink w:anchor="_Toc1"><w:r><w:t xml:space="preserve"> and contents</w:t></w:r></w:hyperlink></w:p>'
        "<w:tbl><w:tr><w:tc>" + para("a | b") + "</w:tc><w:tc>" + para("c") + "</w:tc></w:tr>"
        "<w:tr><w:tc>" + para("d") + "</w:tc></w:tr></w:tbl>" + para("Deep", "Heading8")
    )
    text, title = docs_import.from_docx(docx(body, rels, numbering))
    assert title == "Launch plan"
    assert text == (
        "# Launch plan\n\n# Goals\n\n**Ship it**\n\n1. First\n2. Second\n\n- Point\n\n"
        "[the plan](https://acme.example/plan) and this and that and contents\n\n"
        "| a \\| b | c |\n| --- | --- |\n| d |  |\n\n###### Deep\n"
    )


def test_a_picture_brings_no_image_or_its_bytes():
    drawing = (
        '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<wp:docPr id="1" name="Picture 1" descr="A red square"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData>'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:blipFill>'
        '<a:blip r:embed="rImg"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    )
    rels = f'<Relationship Id="rImg" Type="{IMAGE}" Target="media/red.png"/>'
    text, _ = docs_import.from_docx(docx(para("Before") + drawing, rels, parts={"word/media/red.png": b"\x89PNG"}))
    assert text == "Before\n"


@pytest.mark.parametrize(
    "data,message",
    [
        (b"not a zip", "That is not a valid .docx file"),
        (docx(""), "That Word file has no text"),
        (docx(para("<broken")), "That Word file could not be read"),
        (
            docx("<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val='x'/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>"),
            "could not be read",
        ),
        (
            docx(para("x"), parts={"word/styles.xml": '<!DOCTYPE x [<!ENTITY a "b">]><x/>'}),
            "That Word file uses XML features Tico does not read",
        ),
    ],
)
def test_a_word_file_tico_cannot_read_is_refused_in_words(data, message):
    with pytest.raises(ValueError, match=message):
        docs_import.from_docx(data)


def test_a_word_part_over_the_size_cap_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(docs_import, "MAX_XML", 100)
    with pytest.raises(ValueError, match="That Word file is too large to read"):
        docs_import.from_docx(docx(para("x" * 200)))


def test_merged_cells_page_breaks_and_a_formatted_title_keep_their_place():
    def cell(text, merge=""):
        return f"<w:tc><w:tcPr>{merge}</w:tcPr>{para(text)}</w:tc>"

    table = (
        "<w:tbl><w:tr>" + cell("Name") + cell("Old") + cell("New") + "</w:tr>"
        "<w:tr>" + cell("A", '<w:vMerge w:val="restart"/>') + cell("1") + cell("2") + "</w:tr>"
        "<w:tr>" + cell("", "<w:vMerge/>") + cell("") + cell("3") + "</w:tr></w:tbl>"
    )
    title = (
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Work</w:t></w:r>'
        "<w:r><w:rPr><w:b/></w:rPr><w:t>flow</w:t></w:r></w:p>"
    )
    paged = '<w:p><w:r><w:t>Page one</w:t></w:r><w:r><w:br w:type="page"/></w:r><w:r><w:t>Page two</w:t></w:r></w:p>'
    # The file's own style map, which mammoth reads unless told not to, would drop every paragraph.
    text, guess = docs_import.from_docx(docx(title + table + paged, parts={"mammoth/style-map": "p => !"}))
    assert guess == "Workflow"
    assert text == (
        "# Work**flow**\n\n| Name | Old | New |\n| --- | --- | --- |\n| A | 1 | 2 |\n|  |  | 3 |\n\nPage one\nPage two\n"
    )
