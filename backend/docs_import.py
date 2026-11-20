"""Files to Markdown for `POST /api/v2/docs/import` (docs/docs.md).

Markdown and text pass through, HTML becomes Markdown through html_convert.py (beautifulsoup4 and
markdownify), Word (.docx) becomes HTML through mammoth and then Markdown the same way, and a PDF's
text comes out through pypdf. Nothing here runs code from the file: a Word file's XML parts are
checked first (DOCTYPE and entities refused, sizes capped) and a PDF is only text-extracted.
Every failure is a ValueError whose message a person can act on.
"""

import io
import re
import zipfile

if __package__:
    from . import html_convert
else:  # docs_import_worker.py runs it as a script, with this folder on sys.path
    import html_convert

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
def from_html(text, links=()):
    markdown, title = html_convert.to_markdown(text, links)
    return normalize("\n".join(line.rstrip() for line in markdown.split("\n"))), title


# ---------------------------------------------------------------------------- Word
# mammoth's own map, plus: the Title style as a heading the import can name the doc from; style ids Word and other
# writers use for headings, headings 7 to 9 at the deepest level Markdown has; list levels 6 to 9; and page and
# column breaks as line breaks, so the words on either side stay apart.
WORD_STYLES = "\n".join(
    [f"p.{style} => h1.title:fresh" for style in ("Title", "title")]
    + ["p[style-name='Title'] => h1.title:fresh"]
    + [f"p.heading{n} => h{min(n, 6)}:fresh" for n in range(1, 10)]
    + [f"p.Heading{n} => h6:fresh" for n in (7, 8, 9)]
    + [f"p[style-name='{name} {n}'] => h6:fresh" for name in ("Heading", "heading") for n in (7, 8, 9)]
    + [
        f"p:{kind}-list({n}) => " + "ul|ol > li > " * (n - 1) + f"{tag} > li:fresh"
        for kind, tag in (("unordered", "ul"), ("ordered", "ol"))
        for n in range(6, 10)
    ]
    + ["br[type='page'] => br", "br[type='column'] => br"]
)
WORD_TITLE = re.compile(r'<h1 class="title">(.*?)</h1>', re.S)
WORD_NOTE_BACK = re.compile(r'<a href="#(?:footnote|endnote)-ref-[^"]*">\u2191</a>')  # a note's way back up
MAX_PARTS = 5_000
MAX_UNPACKED = 4 * MAX_XML


def _check_docx(data):
    """Refuse a file mammoth should not read: not a zip, too many parts or bytes once unpacked, or an XML DOCTYPE or
    entity declaration in any part (UTF-8 or UTF-16) outside its pictures."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        parts = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ValueError("That is not a valid .docx file") from exc
    if (
        len(parts) > MAX_PARTS
        or any(p.file_size > MAX_XML for p in parts)
        or sum(p.file_size for p in parts) > MAX_UNPACKED
    ):
        raise ValueError("That Word file is too large to read")
    marks = [mark.encode(codec) for mark in ("<!DOCTYPE", "<!ENTITY") for codec in ("utf-8", "utf-16-le", "utf-16-be")]
    for part in parts:
        if part.filename.startswith("word/media/") or part.is_dir():
            continue
        try:
            raw = archive.read(part)
        except (
            zipfile.BadZipFile,
            NotImplementedError,
            RuntimeError,
        ) as exc:  # bad CRC, unknown compression, encrypted
            raise ValueError("That Word file could not be read") from exc
        if any(mark in raw for mark in marks):
            raise ValueError("That Word file uses XML features Tico does not read")


def from_docx(data):
    """mammoth reads the Word file as HTML (headings, lists, tables, links, bold and italic, notes; no pictures) with
    Tico's style map only, and the HTML becomes Markdown as an HTML import does, keeping only web and mail links."""
    import mammoth
    from bs4 import BeautifulSoup

    _check_docx(data)
    try:
        html = mammoth.convert_to_html(
            io.BytesIO(data),
            style_map=WORD_STYLES,
            include_embedded_style_map=False,
            convert_image=lambda image: [],
        ).value
    except MemoryError:
        raise
    except Exception as exc:  # mammoth's own: a missing part or reference, bad XML, a bad number, nesting too deep
        raise ValueError("That Word file could not be read") from exc
    html = WORD_NOTE_BACK.sub("", html)
    text = from_html(html, links=("http://", "https://", "mailto:"))[0]
    if not text.strip():
        raise ValueError("That Word file has no text")
    titles = (BeautifulSoup(found, "lxml").get_text().strip() for found in WORD_TITLE.findall(html))
    return text, " ".join(next((title for title in titles if title), "").split())


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
