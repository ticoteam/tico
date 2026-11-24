# pyright: strict
"""`python -I office_preview.py <docx|xlsx> <source> <target.html>`: a Word or Excel file's preview, as one small HTML
page the chat draws on the file's card (web/src/conversation/Rich.tsx OfficeCard).

The preview worker (backend/file_metadata.py) runs it through backend/limited.py, so it parses under that process's
memory, CPU and file-size limits: a zip bomb or a parser that never ends stops this process, never the server, and the
file keeps its Download card. Before any part is parsed the archive passes docs_import's caps (part count, each part's
size and the total, from the zip's own directory), and parts are scanned, as streams, for a DOCTYPE or entity
declaration (every entry of a name, when the zip holds it twice).

- Word: mammoth with the import's style map, pictures left out. mammoth finds its parts through relationships a file
  may label as it likes, so every part is scanned, pictures too: a picture holding "<!DOCTYPE" (an SVG, say) leaves the
  file with no preview, and its card keeps the Download chip.
- Excel: the standard library only, and only the parts it parses are scanned. The first visible worksheet's first ROWS
  rows and COLUMNS columns become a table, read as a stream, and only the shared strings those cells name are kept; the
  sheet names go on the page as tabs. Numbers show as stored; a cell whose format shows a date or a time shows it as
  Excel would (its 1900 or 1904 calendar).

The page stops at MAX_HTML, the most the chat reads of it. Run as a script it puts the repository's folder on sys.path
(-I leaves it off) so it imports backend.docs_import just as it does when imported as backend.office_preview.
"""

import html
import io
import math
import posixpath
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
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
# What a number format shows of a date: (the day, the time of day, its seconds). Excel's own formats by id; any other
# format shows the number as stored.
Shows = tuple[bool, bool, bool]
DAY: Shows = (True, False, False)
BUILT_IN: dict[int, Shows] = {
    **dict.fromkeys((14, 15, 16, 17), DAY),
    **dict.fromkeys((18, 20), (False, True, False)),
    **dict.fromkeys((19, 21, 45, 46, 47), (False, True, True)),
    22: (True, True, False),
}
LAST_DAY = 2958465  # 9999-12-31 in the 1900 calendar
MARKS = [m.encode(codec) for m in ("<!DOCTYPE", "<!ENTITY") for codec in ("utf-8", "utf-16-le", "utf-16-be")]
CHUNK = 1024 * 1024
TAG = re.compile(r"<[^>]*>")

# A sheet cell as read: a shared string's place, a number with whether its style shows a date, or text as it stands.
Cell = tuple[str, int] | tuple[str, str, Shows | None] | str


def _docs() -> Any:
    """backend.docs_import, untyped: its archive caps and Word style map."""
    from backend import docs_import

    return docs_import


def _archive(data: bytes) -> zipfile.ZipFile:
    """The zip, once its own directory shows it within docs_import's caps. Nothing in it is read yet."""
    docs = _docs()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        parts = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid Office file") from exc
    sizes = [p.file_size for p in parts]
    if len(parts) > docs.MAX_PARTS or any(s > docs.MAX_XML for s in sizes) or sum(sizes) > docs.MAX_UNPACKED:
        raise ValueError("The file is too large to read")
    return archive


def _plain(archive: zipfile.ZipFile, name: str) -> None:
    """Refuse a part with an XML DOCTYPE or entity declaration (UTF-8 or UTF-16), read as a stream: every entry of that
    name, as a zip may hold a name twice and a reader may take either."""
    keep = max(len(m) for m in MARKS) - 1
    try:
        for entry in (e for e in archive.infolist() if e.filename == name):
            tail = b""
            with archive.open(entry) as stream:
                while chunk := stream.read(CHUNK):
                    window = tail + chunk
                    if any(m in window for m in MARKS):
                        raise ValueError("The file uses XML features Tico does not read")
                    tail = window[-keep:]
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:  # bad CRC, unknown compression, encrypted
        raise ValueError("The file could not be read") from exc


def page(body: str, attrs: str = "", room: int = MAX_HTML) -> str:
    """The whole page, under `room` bytes. A body past it is cut at a character, never inside a tag or an entity, and
    the browser closes what the cut left open; a cut that leaves no words to show is refused."""
    head = f'<!doctype html><html><head><meta charset="utf-8"><style>{STYLE}</style></head><body{attrs}>'
    tail = "</body></html>"
    left = room - len(head.encode()) - len(tail)
    raw = body.encode()
    if len(raw) > left:
        body = raw[: max(0, left)].decode("utf-8", "ignore")  # a character cut in half is left out
        if body.rfind("<") > body.rfind(">"):
            body = body[: body.rfind("<")]
        if body.rfind("&") > body.rfind(";"):
            body = body[: body.rfind("&")]
        if not TAG.sub("", body).strip():
            raise ValueError("Nothing to show within the size cap")
    return head + body + tail


def _no_picture(image: object) -> list[object]:
    """mammoth's picture hook: nothing in a picture's place, and the picture is never read."""
    return []


def _resolve(owner: str, target: str) -> str:
    """A relationship's target as a part name: from the folder of the part the .rels file belongs to, or the root."""
    target = target.replace("\\", "/")
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner), target)).lstrip("/")


def _owner(rels: str) -> str:
    """The part a .rels file describes: `word/_rels/document.xml.rels` -> `word/document.xml`, `_rels/.rels` -> ``."""
    folder, name = posixpath.split(rels)
    return posixpath.join(posixpath.dirname(folder), name.removesuffix(".rels"))


def _relations(archive: zipfile.ZipFile, rels: str) -> dict[str, tuple[str, str]]:
    """{relationship id: (type's last word, part name)} of one .rels part, external targets left out. The caller has
    scanned it."""
    found: dict[str, tuple[str, str]] = {}
    if rels not in archive.namelist():
        return found
    for rel in ElementTree.fromstring(archive.read(rels)):
        if rel.get("TargetMode") == "External":
            continue
        kind = (rel.get("Type") or "").rsplit("/", 1)[-1]
        found[rel.get("Id") or ""] = (kind, _resolve(_owner(rels), rel.get("Target") or ""))
    return found


def word(data: bytes) -> str:
    import mammoth  # pyright: ignore[reportMissingTypeStubs]

    docs = _docs()
    archive = _archive(data)
    for name in dict.fromkeys(e.filename for e in archive.infolist() if not e.is_dir()):
        _plain(archive, name)
    convert: Any = mammoth.convert_to_html  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    body: str = convert(
        io.BytesIO(data),
        style_map=docs.WORD_STYLES,
        include_embedded_style_map=False,
        convert_image=_no_picture,
    ).value
    body = str(docs.WORD_NOTE_BACK.sub("", body))
    if not TAG.sub("", body).strip():
        raise ValueError("The Word file has no text")
    return page(body)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _rid(element: ElementTree.Element) -> str | None:
    return next((v for k, v in element.attrib.items() if _local(k) == "id"), None)


def column_name(n: int) -> str:
    name = ""
    n += 1
    while n:
        n, rest = divmod(n - 1, 26)
        name = chr(65 + rest) + name
    return name


CELL = re.compile(r"([A-Z]{1,3})(\d+)$")


def _column_at(ref: str | None) -> int | None:
    """The column, from 0, of a cell reference such as `C12`, else None."""
    found = CELL.match(ref or "")
    if not found:
        return None
    column = 0
    for letter in found.group(1):
        column = column * 26 + ord(letter) - 64
    return column - 1


def _whole(text: str | None) -> int | None:
    try:
        return int(text or "")
    except ValueError:
        return None


def format_shows(code: str) -> Shows | None:
    """What a number format shows of a date, or None for a number. Quoted text, escaped characters, spacing (`_x`),
    fill (`*x`), [colors and conditions] and AM/PM go first; then y or d, or an m that is no minute (not just after h or
    just before s), is a day, and h, s or a minute is a time of day."""
    code = re.sub(r'"[^"]*"|\\.|_.|\*.|\[[^\]]*\]', "", code)
    code = re.sub(r"am/pm|a/p", "", code, flags=re.I).lower()
    months = re.sub(r"(h+[^a-z0-9]*)m+|m+([^a-z0-9]*s)", r"\1\2", code)
    day = bool(re.search(r"[yd]", code)) or "m" in months
    time = bool(re.search(r"[hs]", code)) or months != code
    return (day, time, "s" in code) if day or time else None


def _date_styles(archive: zipfile.ZipFile, path: str | None) -> dict[int, Shows]:
    """{cell style (its place in cellXfs): what its number format shows of a date}, for the styles that show one."""
    if not path or path not in archive.namelist():
        return {}
    _plain(archive, path)
    root = ElementTree.fromstring(archive.read(path))
    formats = dict(BUILT_IN)
    for element in root.iter():
        if _local(element.tag) == "numFmt":
            number = _whole(element.get("numFmtId"))
            shows = format_shows(element.get("formatCode") or "")
            if number is not None:
                formats.pop(number, None)
                if shows:
                    formats[number] = shows
    dated: dict[int, Shows] = {}
    for element in root:
        if _local(element.tag) == "cellXfs":
            for i, xf in enumerate(element):
                shows = formats.get(_whole(xf.get("numFmtId")) or 0)
                if shows:
                    dated[i] = shows
    return dated


def excel_date(serial: float, date1904: bool, shows: Shows = DAY) -> str | None:
    """A date-formatted cell's value as Excel shows it, or None when it is no date. The 1900 calendar counts 1900-01-01
    as 1 and keeps Lotus's 1900-02-29 at 60, so serials past 60 count from 1899-12-30; the 1904 calendar counts from
    1904-01-01 as 0. A time of day is the fraction alone, the same in both."""
    if not math.isfinite(serial) or serial < 0:
        return None
    days = math.floor(serial)
    seconds = round((serial - days) * 86400)
    if seconds >= 86400:
        days, seconds = days + 1, 0
    day, time, with_seconds = shows
    clock = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}" + (f":{seconds % 60:02d}" if with_seconds else "")
    if not day:
        return clock
    if days > LAST_DAY - (1462 if date1904 else 0):
        return None  # past 9999-12-31: the number as stored
    if date1904:
        text = (date(1904, 1, 1) + timedelta(days=days)).isoformat()
    elif days == 60:
        text = "1900-02-29"
    elif days == 0:
        text = "1900-01-00"  # as Excel shows it
    else:
        text = (date(1899, 12, 31 if days < 60 else 30) + timedelta(days=days)).isoformat()
    return f"{text} {clock}" if time else text


def _number(text: str, shows: Shows | None, date1904: bool) -> str:
    """A number cell as stored, or its date or time when its format shows one."""
    if shows:
        try:
            shown = excel_date(float(text), date1904, shows)
        except ValueError:
            shown = None
        if shown:
            return shown
    return text


def sheet(data: bytes) -> str:
    archive = _archive(data)
    package = _relations_scanned(archive, "_rels/.rels")
    book = next((path for kind, path in package.values() if kind == "officeDocument"), "xl/workbook.xml")
    if book not in archive.namelist():
        raise ValueError("The file has no workbook")
    _plain(archive, book)
    workbook = ElementTree.fromstring(archive.read(book))
    rels = _relations_scanned(
        archive, posixpath.join(posixpath.dirname(book), "_rels", posixpath.basename(book) + ".rels")
    )
    names: list[str] = []
    first: str | None = None
    first_name = ""
    date1904 = False
    for element in workbook.iter():
        tag = _local(element.tag)
        if tag == "workbookPr" and element.get("date1904") in ("1", "true"):
            date1904 = True
        elif tag == "sheet" and element.get("state") not in ("hidden", "veryHidden"):
            name = element.get("name") or f"Sheet{len(names) + 1}"
            names.append(name)
            rid = _rid(element)
            if (
                first is None and rid is not None and rid in rels and rels[rid][0] == "worksheet"
            ):  # a chart sheet has no cells to show
                first, first_name = rid, name
    if first is None:
        raise ValueError("The workbook has no sheet")
    by_type = {kind: path for kind, path in rels.values()}
    dated = _date_styles(archive, by_type.get("styles"))
    target = rels[first][1]
    if target not in archive.namelist():
        raise ValueError("The workbook's sheet is missing")
    _plain(archive, target)

    cells: dict[tuple[int, int], Cell] = {}
    wanted: set[int] = set()
    row, column, width, height = 0, -1, 0, 0
    with archive.open(target) as stream:
        for event, element in ElementTree.iterparse(stream, events=("start", "end")):
            tag = _local(element.tag)
            if event == "start":
                if tag == "row":
                    found = _whole(element.get("r"))
                    row = found if found is not None else row + 1
                    column = -1
                    if row > ROWS:
                        break
                continue
            if tag == "row":
                element.clear()
            elif tag == "c":
                at = _column_at(element.get("r"))
                column = at if at is not None else column + 1
                kind, style = element.get("t") or "n", _whole(element.get("s")) or 0
                value: str | None = None
                inline: str | None = None
                for child in element:
                    if _local(child.tag) == "v":
                        value = child.text or ""
                    elif _local(child.tag) == "is":
                        inline = "".join(t.text or "" for t in child.iter() if _local(t.tag) == "t")
                element.clear()
                if row < 1 or column >= COLUMNS or (value is None and inline is None):
                    continue
                shown: Cell
                if kind == "s":
                    index = _whole((value or "").strip())
                    if index is None or index < 0:
                        continue  # an empty or broken shared-string cell is blank
                    wanted.add(index)
                    shown = ("s", index)
                elif kind == "inlineStr":
                    shown = inline or ""
                elif kind == "b":
                    shown = "TRUE" if (value or "").strip() == "1" else "FALSE"
                elif kind in ("str", "e", "d"):
                    shown = value or ""
                else:
                    shown = ("n", (value or "").strip(), dated.get(style))
                cells[row, column] = shown
                width, height = max(width, column + 1), max(height, row)
    if not cells:
        raise ValueError("The sheet is empty")
    strings = _shared(archive, by_type.get("sharedStrings"), wanted)
    return _sheet_page(cells, width, height, names, first_name, strings, date1904)


def _relations_scanned(archive: zipfile.ZipFile, rels: str) -> dict[str, tuple[str, str]]:
    if rels in archive.namelist():
        _plain(archive, rels)
    return _relations(archive, rels)


def _shared(archive: zipfile.ZipFile, path: str | None, wanted: set[int]) -> dict[int, str]:
    """The shared strings the cells name, read up to the last one they name and no further."""
    found: dict[int, str] = {}
    if not wanted or not path or path not in archive.namelist():
        return found
    _plain(archive, path)
    last, index = max(wanted), -1
    with archive.open(path) as stream:
        for _, element in ElementTree.iterparse(stream):
            if _local(element.tag) != "si":
                continue
            index += 1
            if index in wanted:
                parts: list[str] = []
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


def _sheet_page(
    cells: dict[tuple[int, int], Cell],
    width: int,
    height: int,
    names: list[str],
    shown: str,
    strings: dict[int, str],
    date1904: bool,
) -> str:
    head = "<tr><th></th>" + "".join(f"<th>{column_name(i)}</th>" for i in range(width)) + "</tr>"
    tabs = '<nav class="tabs">' + "".join(
        f'<span class="{"on" if name == shown else ""}">{html.escape(name)}</span>' for name in names[:20]
    )
    tabs += f"<span>+{len(names) - 20}</span></nav>" if len(names) > 20 else "</nav>"
    attrs = f' class="sheet" data-sheet="{html.escape(shown)}" data-sheets="{len(names)}"'
    start, end = f"<table><thead>{head}</thead><tbody>", f"</tbody></table>{tabs}"
    # Everything but the rows: the page around the body, the table's own tags, its column letters and the tabs bar.
    room = MAX_HTML - len(page("", attrs).encode()) - len(start.encode()) - len(end.encode())
    rows: list[str] = []
    used = 0
    for r in range(1, height + 1):
        parts = [f"<tr><th>{r}</th>"]
        for col in range(width):
            value = cells.get((r, col))
            numeric = False
            if isinstance(value, tuple) and len(value) == 2:
                text = strings.get(value[1], "")
            elif isinstance(value, tuple):
                text, numeric = _number(value[1], value[2], date1904), True
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
    return page(start + "".join(rows) + end, attrs)


def main(argv: list[str]) -> None:
    kind, source, target = argv
    data = Path(source).read_bytes()
    out = word(data) if kind == "docx" else sheet(data) if kind == "xlsx" else None
    if out is None:
        raise ValueError("Unknown kind")
    Path(target).write_text(out, encoding="utf-8")


if __name__ == "__main__":
    sys.path.append(str(Path(__file__).resolve().parents[1]))  # -I leaves the repository off sys.path; backend is in it
    main(sys.argv[1:])
