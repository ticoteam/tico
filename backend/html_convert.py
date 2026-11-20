# pyright: strict
"""HTML to plain text and to Markdown, for every server path that reads HTML.

beautifulsoup4 with the lxml parser builds the tree a browser would (a missing </td>, </li> or </p> closes where
HTML says it does), and markdownify writes the Markdown. markdownify is built on beautifulsoup4, so one tree serves
both. Text that is never shown (script, style, head and the like) is dropped, and neither function runs or fetches
anything the page names.
"""

import re

from bs4 import BeautifulSoup
from bs4.element import CData, NavigableString, PageElement, Tag
from markdownify import MarkdownConverter

HIDDEN = ("script", "style", "head", "noscript", "template", "svg", "iframe")
BLOCKS = frozenset(
    "address article aside blockquote dd div dl dt figcaption figure footer h1 h2 h3 h4 h5 h6 header hr li main "
    "nav ol p pre section table tr ul".split()
)
BREAK, LINE = "\x00", "\x01"  # a block edge (runs of them are one line break) and a <br> (each one counts)
CELL, ROW = "\x02", "\x03"  # where markdownify's table cells and rows start, for the table layout below


def _soup(source: str) -> tuple[BeautifulSoup, str]:
    """The tree with hidden elements removed, and the page's <title>."""
    soup = BeautifulSoup(re.sub(r"[\x00-\x03]", "", source or ""), "lxml")
    title = " ".join(soup.title.get_text().split()) if soup.title else ""
    for tag in soup.find_all(HIDDEN):
        tag.decompose()
    return soup, title


def _words(root: PageElement) -> str:
    """The text under `root` with a line per block and a space at every other tag's edges, as the search index and
    the mail reader have always split words. Read without recursion, so a deeply nested page reads like any other."""
    out: list[str] = []
    stack: list[PageElement | str] = [root]
    pre = 0
    while stack:
        node = stack.pop()
        if isinstance(node, NavigableString):
            if type(node) in (NavigableString, CData):  # comments, doctypes and declarations are not text
                out.append(node.replace("\n", LINE) if pre else node)
        elif isinstance(node, str):
            if node == "</pre>":
                pre -= 1
            else:
                out.append(node)
        elif isinstance(node, Tag):
            if node.name == "br":
                out.append(LINE)
                continue
            edge = BREAK if node.name in BLOCKS else " "
            out.append(edge)
            stack.append(edge)
            if node.name == "pre":
                pre += 1
                stack.append("</pre>")
            stack.extend(reversed(node.contents))
    text = re.sub(r"\s+", " ", "".join(out))
    text = re.sub(r"[ \x00]*\x00[ \x00]*", "\n", text).replace(LINE, "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_text(source: str) -> tuple[str, str]:
    """(readable plain text, title) of an HTML page: a line per paragraph, heading, list item or table row."""
    soup, title = _soup(source)
    return _words(soup), title


class _Markdown(MarkdownConverter):
    def convert_img(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return str(el.get("alt") or "")  # the words, not a link to fetch

    def convert_td(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return CELL + " ".join(text.split()).replace("|", "\\|")

    convert_th = convert_td

    def convert_tr(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return ROW + text

    def convert_table(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Every row, its first as the header, padded to the widest row; empty rows are dropped."""
        lead, *rows = text.split(ROW)
        cells = [row.split(CELL)[1:] for row in rows]
        cells = [row for row in cells if row]
        if not cells:
            return "\n\n" + lead.strip() + "\n\n"
        width = max(len(row) for row in cells)
        cells = [row + [""] * (width - len(row)) for row in cells]
        lines = ["| " + " | ".join(cells[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(row) + " |" for row in cells[1:]]
        return "\n\n" + (lead.strip() + "\n\n" if lead.strip() else "") + "\n".join(lines) + "\n\n"


_CONVERTER = _Markdown(
    autolinks=False,
    bullets="-",
    escape_asterisks=False,
    escape_underscores=False,
    heading_style="atx",
    wrap=True,
    wrap_width=None,
)


def to_markdown(source: str) -> tuple[str, str]:
    """(Markdown, title) of an HTML page. Markdown cannot nest tables, so a table inside a table cell becomes that
    cell's words, in order. A page nested deeper than markdownify can walk comes out as its plain text."""
    soup, title = _soup(source)
    gone: set[int] = set()
    for table in soup.find_all("table"):
        if id(table) in gone or not any(parent.name == "table" for parent in table.parents):
            continue
        gone.update(id(inner) for inner in table.find_all("table"))
        table.replace_with(" " + " ".join(_words(table).split()) + " ")
    for tag in soup.find_all(("aside", "dd", "dt", "figure", "footer", "header", "main", "nav")):
        tag.name = "div"  # a block of its own, as markdownify writes a div
    for link in soup.find_all("a"):
        link.attrs.pop("title", None)
        if str(link.get("href") or "").strip().lower().startswith("javascript:"):
            del link["href"]  # its words stay, as plain text
    try:
        return _CONVERTER.convert_soup(soup), title
    except RecursionError:
        return _words(soup), title
