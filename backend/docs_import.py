"""Files to Markdown for `POST /api/v2/docs/import` (docs/docs.md).

Markdown and text pass through, HTML and Word (.docx) are walked with the standard library, and a
PDF's text comes out through pypdf. Nothing here runs code from the file: a Word file is read as
XML from its zip (DOCTYPE and entities refused, sizes capped) and a PDF is only text-extracted.
Every failure is a ValueError whose message a person can act on.
"""

import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

EXTENSIONS = (".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf")
MAX_BYTES = 20_000_000
MAX_XML = 30_000_000
MAX_PDF_PAGES = 500


def decode(data):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def normalize(text):
    text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


# ---------------------------------------------------------------------------- HTML
class _Html(HTMLParser):
    SKIP = {"script", "style", "head", "noscript", "template", "svg", "iframe"}
    BLOCKS = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "figure", "figcaption",
              "table", "tr", "dl", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.lists, self.href, self.pre = [], 0, [], [], 0
        self.cells, self.row_open, self.table_rows, self.title, self.in_title = None, False, None, "", False
        self.link_text = []

    def add(self, text):
        if self.cells is not None and self.row_open:
            self.cells[-1] += text
        else:
            self.out.append(text)

    def blank(self):
        if self.out and not "".join(self.out[-2:]).endswith("\n\n"):
            self.out.append("\n\n" if not "".join(self.out[-1:]).endswith("\n") else "\n")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.blank()
            self.add("#" * int(tag[1]) + " ")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            if len(self.lists) == 1:
                self.blank()
        elif tag == "li":
            depth = max(len(self.lists) - 1, 0)
            mark = "- "
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                mark = f"{self.lists[-1][1]}. "
            if self.out and not self.out[-1].endswith("\n"):
                self.add("\n")
            self.add("  " * depth + mark)
        elif tag == "br":
            self.add("  \n")
        elif tag == "hr":
            self.blank()
            self.add("---\n\n")
        elif tag in ("strong", "b"):
            self.add("**")
        elif tag in ("em", "i"):
            self.add("*")
        elif tag == "pre":
            self.blank()
            self.add("```\n")
            self.pre += 1
        elif tag == "code" and not self.pre:
            self.add("`")
        elif tag == "blockquote":
            self.blank()
            self.add("> ")
        elif tag == "a":
            self.href.append(a.get("href") or "")
            self.add("[")
        elif tag == "img":
            if a.get("alt"):
                self.add(a["alt"])
        elif tag == "table":
            self.blank()
            self.table_rows = []
        elif tag == "tr" and self.table_rows is not None:
            self.cells, self.row_open = [], False
        elif tag in ("td", "th") and self.cells is not None:
            self.cells.append("")
            self.row_open = True
        elif tag in self.BLOCKS:
            self.blank()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.add("\n\n")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self.add("\n")
        elif tag == "li":
            self.add("\n")
        elif tag in ("strong", "b"):
            self.add("**")
        elif tag in ("em", "i"):
            self.add("*")
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.add("\n```\n\n")
        elif tag == "code" and not self.pre:
            self.add("`")
        elif tag == "a":
            href = self.href.pop() if self.href else ""
            self.add("](" + href + ")" if href and not href.lower().startswith("javascript:") else "]")
        elif tag in ("td", "th"):
            self.row_open = False
        elif tag == "tr" and self.table_rows is not None and self.cells is not None:
            self.table_rows.append([" ".join(c.split()).replace("|", "\\|") for c in self.cells])
            self.cells, self.row_open = None, False
        elif tag == "table" and self.table_rows is not None:
            rows = [r for r in self.table_rows if r]
            self.table_rows = None
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.out.append("\n".join(lines) + "\n\n")
        elif tag in self.BLOCKS or tag == "blockquote":
            self.blank()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.skip:
            return
        self.add(data if self.pre else re.sub(r"\s+", " ", data))


def from_html(text):
    parser = _Html()
    parser.feed(text)
    parser.close()
    body = "".join(parser.out)
    lines = [line.rstrip() for line in body.split("\n")]
    return normalize("\n".join(lines)), " ".join(parser.title.split())


# ---------------------------------------------------------------------------- Word
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _xml(archive, name):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML:
        raise ValueError("That Word file is too large to read")
    raw = archive.read(name)
    if b"<!DOCTYPE" in raw[:2000] or b"<!ENTITY" in raw:
        raise ValueError("That Word file uses XML features Tico does not read")
    return ET.fromstring(raw)


def _flag(props, tag):
    node = props.find(W + tag) if props is not None else None
    return node is not None and node.get(W + "val", "true") not in ("0", "false", "none")


def _runs(paragraph, links):
    out = []

    def walk(node, href=""):
        for child in node:
            if child.tag == W + "hyperlink":
                walk(child, links.get(child.get(R + "id"), ""))
            elif child.tag == W + "r":
                text = "".join((t.text or "") if t.tag == W + "t" else "\t" if t.tag == W + "tab"
                               else "\n" if t.tag == W + "br" else "" for t in child)
                if not text:
                    continue
                props = child.find(W + "rPr")
                lead, core, tail = re.match(r"(\s*)(.*?)(\s*)$", text, re.S).groups()
                if core and _flag(props, "b"):
                    core = "**" + core + "**"
                if core and _flag(props, "i"):
                    core = "*" + core + "*"
                piece = lead + core + tail
                out.append((piece, href))
            elif child.tag in (W + "ins", W + "smartTag", W + "sdt", W + "sdtContent"):
                walk(child, href)

    walk(paragraph)
    merged, last = "", None
    for piece, href in out:
        if href != last:
            merged += ("](" + last + ")" if last else "") + ("[" if href else "")
            last = href
        merged += piece
    if last:
        merged += "](" + last + ")"
    return merged.replace("****", "")


def from_docx(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        document = _xml(archive, "word/document.xml")
        if document is None:
            raise ValueError("no document")
        rels, numbering = {}, {}
        rel_xml = _xml(archive, "word/_rels/document.xml.rels")
        if rel_xml is not None:
            rels = {r.get("Id"): r.get("Target", "") for r in rel_xml.iter(REL + "Relationship")
                    if r.get("TargetMode") == "External" and r.get("Target", "").lower().startswith(("http://", "https://", "mailto:"))}
        num_xml = _xml(archive, "word/numbering.xml")
        if num_xml is not None:
            abstract = {a.get(W + "abstractNumId"): (a.find(W + "lvl/" + W + "numFmt").get(W + "val")
                                                     if a.find(W + "lvl/" + W + "numFmt") is not None else "bullet")
                        for a in num_xml.iter(W + "abstractNum")}
            numbering = {n.get(W + "numId"): abstract.get(n.find(W + "abstractNumId").get(W + "val"), "bullet")
                         for n in num_xml.iter(W + "num") if n.find(W + "abstractNumId") is not None}
    except zipfile.BadZipFile as exc:
        raise ValueError("That is not a valid .docx file") from exc
    except ET.ParseError as exc:
        raise ValueError("That Word file could not be read") from exc
    body = document.find(W + "body")
    if body is None:
        raise ValueError("That Word file has no text")
    counters, blocks, title = {}, [], ""

    def paragraph(node):
        nonlocal title
        props = node.find(W + "pPr")
        style = props.find(W + "pStyle").get(W + "val", "") if props is not None and props.find(W + "pStyle") is not None else ""
        text = _runs(node, rels).strip()
        if not text:
            return ""
        heading = re.fullmatch(r"(?:Heading|heading)\s?(\d)", style)
        if style.lower() == "title":
            title = title or re.sub(r"[*\[\]]|\([^)]*\)$", "", text).strip()
            return "# " + text
        if heading:
            return "#" * min(int(heading.group(1)), 6) + " " + text
        num = props.find(W + "numPr") if props is not None else None
        if num is not None or style.lower().startswith("listparagraph"):
            level = int(num.find(W + "ilvl").get(W + "val", "0")) if num is not None and num.find(W + "ilvl") is not None else 0
            ident = num.find(W + "numId").get(W + "val", "") if num is not None and num.find(W + "numId") is not None else ""
            ordered = numbering.get(ident) not in (None, "bullet", "none")
            counters[(ident, level)] = counters.get((ident, level), 0) + 1
            return "  " * level + (f"{counters[(ident, level)]}. " if ordered else "- ") + text
        counters.clear()
        return text

    def table(node):
        rows = []
        for tr in node.iter(W + "tr"):
            rows.append([" ".join(" ".join(_runs(p, rels) for p in tc.iter(W + "p")).split()).replace("|", "\\|")
                         for tc in tr.findall(W + "tc")])
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                         + ["| " + " | ".join(r) + " |" for r in rows[1:]])

    previous_list = False
    for node in body:
        if node.tag == W + "p":
            line = paragraph(node)
            is_list = bool(re.match(r"\s*(?:- |\d+\. )", line))
            if line:
                blocks.append(("\n" if previous_list and is_list else "\n\n") + line)
            previous_list = is_list
        elif node.tag == W + "tbl":
            blocks.append("\n\n" + table(node))
            previous_list = False
    text = "".join(blocks).strip()
    if not text:
        raise ValueError("That Word file has no text")
    return normalize(text), title


# ---------------------------------------------------------------------------- PDF
def from_pdf(data):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("That PDF is password protected")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"That PDF has more than {MAX_PDF_PAGES} pages; split it first")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        title = str((reader.metadata or {}).get("/Title") or "").strip()
    except (ValueError, MemoryError):
        raise
    except Exception as exc:
        raise ValueError("That PDF could not be read") from exc
    text = "\n\n".join(p for p in pages if p)
    if not text:
        raise ValueError("That PDF has no text to import (a scan needs OCR first)")
    return normalize(text), title


# ---------------------------------------------------------------------------- entry point
def to_markdown(filename, data):
    """(markdown, title guess) for an uploaded file. ValueError says what is wrong with it."""
    name = str(filename or "").lower()
    if not name.endswith(EXTENSIONS):
        raise ValueError("Import a .md, .markdown, .txt, .html, .htm, .docx or .pdf file")
    if len(data) > MAX_BYTES:
        raise ValueError("That file is larger than 20 MB")
    if name.endswith(".pdf"):
        return from_pdf(data)
    if name.endswith(".docx"):
        return from_docx(data)
    text = decode(data)
    if name.endswith((".html", ".htm")):
        return from_html(text)
    return normalize(text), ""
