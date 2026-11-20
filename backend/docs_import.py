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
# mammoth's own map, plus the Title style as a heading the import can name the doc from, and headings 7 to 9 at the
# deepest level Markdown has.
WORD_STYLES = "\n".join(
    ["p.Title => h1.title:fresh", "p[style-name='Title'] => h1.title:fresh"]
    + [f"p[style-name='{name} {n}'] => h6:fresh" for name in ("Heading", "heading") for n in (7, 8, 9)]
    + [f"p.Heading{n} => h6:fresh" for n in (7, 8, 9)]
)
WORD_TITLE = re.compile(r'<h1 class="title">(.*?)</h1>', re.S)


def from_docx(data):
    """mammoth reads the Word file as HTML (headings, lists, tables, links, bold and italic; no images), and the
    HTML becomes Markdown as an HTML import does, keeping only web and mail links."""
    import mammoth

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        for info in archive.infolist():
            if info.filename.lower().endswith((".xml", ".rels")):
                if info.file_size > MAX_XML:
                    raise ValueError("That Word file is too large to read")
                raw = archive.read(info)
                if b"<!DOCTYPE" in raw[:2000] or b"<!ENTITY" in raw:
                    raise ValueError("That Word file uses XML features Tico does not read")
        html = mammoth.convert_to_html(io.BytesIO(data), style_map=WORD_STYLES, convert_image=lambda image: []).value
    except zipfile.BadZipFile as exc:
        raise ValueError("That is not a valid .docx file") from exc
    except (ValueError, MemoryError):
        raise
    except Exception as exc:  # mammoth's own: a missing part (OSError), bad XML (ExpatError), a part nested too deep
        raise ValueError("That Word file could not be read") from exc
    text = from_html(html, links=("http://", "https://", "mailto:"))[0]
    if not text.strip():
        raise ValueError("That Word file has no text")
    title = WORD_TITLE.search(html)
    return text, html_convert.to_text(title.group(1))[0] if title else ""


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
