"""`python -I office_preview.py <docx|xlsx> <source> <target.html>`: a Word or Excel file's preview, as one small HTML
page the chat draws on the file's card (web/src/conversation/Rich.tsx OfficeCard).

The preview worker (backend/file_metadata.py) runs it through backend/limited.py, so it parses under that process's
memory, CPU and file-size limits: a zip bomb or a parser that never ends stops this process, never the server, and the
file keeps its Download card. Both kinds pass docs_import's archive check first (sizes and part counts capped, DOCTYPE
and entity declarations refused).

- Word: mammoth with the import's style map, pictures left out.
- Excel: the standard library only. The first visible sheet's first ROWS rows and COLUMNS columns become a table, read
  as a stream, and only the shared strings those cells name are kept; the sheet names go on the page as well.

The page stops at MAX_HTML, the most the chat reads of it. Run as a script it puts its own folder on sys.path (-I
leaves it off) to import docs_import; imported as backend.office_preview, it imports it from the package.
"""

import html
import io
import posixpath
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

MAX_HTML = 256 * 1024
ROWS, COLUMNS = 200, 30
CELL_CHARS = 200
STYLE = (
    "html{background:#fff;color:#1f2328}"
    "body{margin:0;padding:20px 24px;font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}"
    "h1,h2,h3,h4,h5,h6{line-height:1.25;margin:1em 0 .4em}h1{font-size:1.6em}h2{font-size:1.3em}h3{font-size:1.1em}"
    "p{margin:0 0 .6em}table{border-collapse:collapse;margin:0 0 .8em}"
    "td,th{border:1px solid #d0d7de;padding:2px 6px;vertical-align:top}"
    "body.sheet{padding:0}.sheet table{margin:0;font-size:12px;white-space:nowrap}"
    ".sheet th{background:#f6f8fa;color:#656d76;font-weight:400;text-align:center;position:sticky;top:0}"
    ".sheet tbody th{position:sticky;left:0}.sheet td{max-width:24em;overflow:hidden;text-overflow:ellipsis}"
    ".sheet td.n{text-align:right}.tabs{display:flex;gap:2px;padding:6px 8px;font-size:12px;color:#656d76}"
    ".tabs span{padding:2px 8px;border-radius:4px}.tabs .on{background:#f6f8fa;color:#1f2328;font-weight:600}"
)
# Excel's own number formats that show a date or a time; any other format shows the number.
DATE_FORMATS = {14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47}
LAST_SERIAL = 2958466  # 9999-12-31


def _guard():
    if __package__:
        from . import docs_import
    else:  # run as a script: __main__ put this folder on sys.path
        import docs_import
    return docs_import


def page(body, attrs="", room=MAX_HTML):
    """The whole page, under `room` bytes: a body past it is cut before its last tag that would not fit, and the
    browser closes what the cut left open."""
    head = f'<!doctype html><html><head><meta charset="utf-8"><style>{STYLE}</style></head><body{attrs}>'
    tail = "</body></html>"
    left = room - len(head.encode()) - len(tail)
    raw = body.encode()
    if len(raw) > left:
        cut = raw[:left]
        body = cut[: max(0, cut.rfind(b"<"))].decode("utf-8", "ignore")
    return head + body + tail


def word(data):
    import mammoth

    docs = _guard()
    docs._check_docx(data)
    body = mammoth.convert_to_html(
        io.BytesIO(data),
        style_map=docs.WORD_STYLES,
        include_embedded_style_map=False,
        convert_image=lambda image: [],
    ).value
    body = docs.WORD_NOTE_BACK.sub("", body)
    if not re.sub(r"<[^>]*>", "", body).strip():
        raise ValueError("The Word file has no text")
    return page(body)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _rid(element):
    return next((v for k, v in element.attrib.items() if _local(k) == "id"), None)


def _relations(archive, rels):
    """{relationship id: (type, part path)} of one .rels part, paths resolved from the xl/ folder."""
    found = {}
    if rels not in archive.namelist():
        return found
    for rel in ElementTree.fromstring(archive.read(rels)):
        target = rel.get("Target") or ""
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        found[rel.get("Id")] = (rel.get("Type") or "", path)
    return found


def column_name(n):
    name = ""
    n += 1
    while n:
        n, rest = divmod(n - 1, 26)
        name = chr(65 + rest) + name
    return name


CELL = re.compile(r"([A-Z]{1,3})(\d+)$")


def _cell_at(ref):
    """(row, column), both from 1 and 0, of a cell reference such as `C12`, else None."""
    found = CELL.match(ref or "")
    if not found:
        return None
    column = 0
    for letter in found.group(1):
        column = column * 26 + ord(letter) - 64
    return int(found.group(2)), column - 1


def _date_styles(archive, path):
    """The cell styles (their places in cellXfs) whose number format shows a date or a time."""
    if not path or path not in archive.namelist():
        return set()
    root = ElementTree.fromstring(archive.read(path))
    custom = set()
    for element in root.iter():
        if _local(element.tag) == "numFmt":
            code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", element.get("formatCode") or "")
            if re.search(r"[dyhs]", code, re.I):
                custom.add(int(element.get("numFmtId") or -1))
    dated = set()
    for element in root:
        if _local(element.tag) == "cellXfs":
            for i, xf in enumerate(element):
                number = int(xf.get("numFmtId") or 0)
                if number in DATE_FORMATS or number in custom:
                    dated.add(i)
    return dated


def _number(text, dated, base):
    try:
        x = float(text)
    except ValueError:
        return text
    if dated and 0 <= x < LAST_SERIAL:
        when = base + timedelta(days=x)
        if x < 1:
            return when.strftime("%H:%M")
        return when.strftime("%Y-%m-%d" if x == int(x) else "%Y-%m-%d %H:%M")
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.10g}"


def sheet(data):
    _guard()._check_docx(data)
    archive = zipfile.ZipFile(io.BytesIO(data))
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    names, first, base = [], None, datetime(1899, 12, 30, tzinfo=timezone.utc)
    for element in workbook.iter():
        tag = _local(element.tag)
        if tag == "workbookPr" and element.get("date1904") in ("1", "true"):
            base = datetime(1904, 1, 1, tzinfo=timezone.utc)
        elif tag == "sheet" and element.get("state") not in ("hidden", "veryHidden"):
            names.append(element.get("name") or f"Sheet{len(names) + 1}")
            first = first or _rid(element)
    rels = _relations(archive, "xl/_rels/workbook.xml.rels")
    if first not in rels:
        raise ValueError("The workbook has no sheet")
    by_type = {kind.rsplit("/", 1)[-1]: path for kind, path in rels.values()}
    dated = _date_styles(archive, by_type.get("styles"))

    cells, wanted, row, column, width, height = {}, set(), 0, -1, 0, 0
    with archive.open(rels[first][1]) as stream:
        for event, element in ElementTree.iterparse(stream, events=("start", "end")):
            tag = _local(element.tag)
            if event == "start":
                if tag == "row":
                    row = int(element.get("r") or row + 1)
                    column = -1
                    if row > ROWS:
                        break
                continue
            if tag == "row":
                element.clear()
            elif tag == "c":
                at = _cell_at(element.get("r"))
                column = at[1] if at else column + 1
                kind, style = element.get("t") or "n", int(element.get("s") or 0)
                value = inline = None
                for child in element:
                    if _local(child.tag) == "v":
                        value = child.text or ""
                    elif _local(child.tag) == "is":
                        inline = "".join(t.text or "" for t in child.iter() if _local(t.tag) == "t")
                element.clear()
                if column >= COLUMNS or (value is None and inline is None):
                    continue
                if kind == "s":
                    wanted.add(int(value or 0))
                    shown = ("s", int(value or 0))
                elif kind == "inlineStr":
                    shown = inline or ""
                elif kind == "b":
                    shown = "TRUE" if value == "1" else "FALSE"
                elif kind in ("str", "e", "d"):
                    shown = value or ""
                else:
                    shown = ("n", value or "", style in dated)
                cells[row, column] = shown
                width, height = max(width, column + 1), max(height, row)
    if not cells:
        raise ValueError("The sheet is empty")
    strings = _shared(archive, by_type.get("sharedStrings"), wanted)
    return _sheet_page(cells, width, height, names, strings, base)


def _shared(archive, path, wanted):
    """The shared strings the cells name, read up to the last one they name and no further."""
    found = {}
    if not wanted or not path or path not in archive.namelist():
        return found
    last, index = max(wanted), -1
    with archive.open(path) as stream:
        for _, element in ElementTree.iterparse(stream):
            if _local(element.tag) != "si":
                continue
            index += 1
            if index in wanted:
                parts = []
                for child in element:
                    if _local(child.tag) == "t":
                        parts.append(child.text or "")
                    elif _local(child.tag) == "r":
                        parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
                found[index] = "".join(parts)
            element.clear()
            if index >= last:
                break
    return found


def _sheet_page(cells, width, height, names, strings, base):
    head = "<tr><th></th>" + "".join(f"<th>{column_name(i)}</th>" for i in range(width)) + "</tr>"
    tabs = '<nav class="tabs">' + "".join(
        f'<span class="{"on" if i == 0 else ""}">{html.escape(name)}</span>' for i, name in enumerate(names[:20])
    )
    tabs += f"<span>+{len(names) - 20}</span></nav>" if len(names) > 20 else "</nav>"
    attrs = f' class="sheet" data-sheet="{html.escape(names[0])}" data-sheets="{len(names)}"'
    room = MAX_HTML - len(page("", attrs).encode()) - len(tabs.encode()) - 64
    rows, used = [], 0
    for r in range(1, height + 1):
        parts = [f"<tr><th>{r}</th>"]
        for col in range(width):
            value = cells.get((r, col))
            numeric = False
            if isinstance(value, tuple) and value[0] == "s":
                text = strings.get(value[1], "")
            elif isinstance(value, tuple):
                text, numeric = _number(value[1], value[2], base), True
            else:
                text = value or ""
            if len(text) > CELL_CHARS:
                text = text[: CELL_CHARS - 1] + "…"
            parts.append(f'<td class="n">{html.escape(text)}</td>' if numeric else f"<td>{html.escape(text)}</td>")
        parts.append("</tr>")
        line = "".join(parts)
        used += len(line.encode())
        if used > room:
            break
        rows.append(line)
    body = f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>{tabs}"
    return page(body, attrs)


def main(argv):
    kind, source, target = argv
    data = Path(source).read_bytes()
    out = word(data) if kind == "docx" else sheet(data) if kind == "xlsx" else None
    if out is None:
        raise ValueError("Unknown kind")
    Path(target).write_text(out, encoding="utf-8")


if __name__ == "__main__":
    sys.path.append(str(Path(__file__).resolve().parent))  # -I leaves this folder off sys.path; docs_import is here
    main(sys.argv[1:])
