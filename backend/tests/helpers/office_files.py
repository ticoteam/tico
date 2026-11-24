"""Small Word and Excel files built with zipfile, for the office preview tests (backend/office_preview.py)."""

import io
import zipfile
from html import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# The one cell style a test needs: index 1 shows a date (Excel's built-in format 14).
DATE_STYLES = f'<styleSheet xmlns="{S}"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>'


def pack(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return out.getvalue()


def docx(paragraphs, extra=None):
    """paragraphs: [(style id or None, text)]."""
    body = "".join(
        (f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else "<w:p>")
        + f"<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        for style, text in paragraphs
    )
    return pack(
        {
            "_rels/.rels": f'<?xml version="1.0"?><Relationships xmlns="{PKG}">'
            f'<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>',
            "word/document.xml": f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
            **(extra or {}),
        }
    )


def cell_name(row, column):
    name, n = "", column + 1
    while n:
        n, rest = divmod(n - 1, 26)
        name = chr(65 + rest) + name
    return f"{name}{row}"


def xlsx(sheets, styles=DATE_STYLES, extra=None):
    """sheets: [(name, rows)]; a cell is a str (a shared string), a number, or (number, style index)."""
    strings, parts, rels, entries = [], {}, [], []
    for i, (name, rows) in enumerate(sheets, 1):
        xml = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, value in enumerate(row):
                ref = cell_name(r, c)
                if isinstance(value, tuple):
                    cells.append(f'<c r="{ref}" s="{value[1]}"><v>{value[0]}</v></c>')
                elif isinstance(value, str):
                    strings.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            xml.append(f'<row r="{r}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{i}.xml"] = (
            f'<?xml version="1.0"?><worksheet xmlns="{S}"><sheetData>{"".join(xml)}</sheetData></worksheet>'
        )
        rels.append(f'<Relationship Id="rId{i}" Type="{R}/worksheet" Target="worksheets/sheet{i}.xml"/>')
        entries.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
    rels.append(f'<Relationship Id="rIdS" Type="{R}/sharedStrings" Target="sharedStrings.xml"/>')
    rels.append(f'<Relationship Id="rIdY" Type="{R}/styles" Target="styles.xml"/>')
    parts["xl/styles.xml"] = styles
    parts["xl/workbook.xml"] = (
        f'<?xml version="1.0"?><workbook xmlns="{S}" xmlns:r="{R}"><sheets>{"".join(entries)}</sheets></workbook>'
    )
    parts["xl/_rels/workbook.xml.rels"] = (
        f'<?xml version="1.0"?><Relationships xmlns="{PKG}">{"".join(rels)}</Relationships>'
    )
    shared = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    parts["xl/sharedStrings.xml"] = f'<?xml version="1.0"?><sst xmlns="{S}">{shared}</sst>'
    return pack({**parts, **(extra or {})})


def bomb(kind):
    """A file of `kind` with one part that unpacks to 40 MB of nothing: past docs_import's per-part limit, so the
    converter refuses it before any parser reads it."""
    zeros = b"\0" * (40 * 1024**2)
    if kind == "docx":
        return docx([(None, "Hi")], extra={"word/document.xml": zeros})
    return xlsx([("Sheet1", [["Hi"]])], extra={"xl/sharedStrings.xml": zeros})
