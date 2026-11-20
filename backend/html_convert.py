# pyright: strict
"""HTML to plain text and to Markdown, for every server path that reads HTML.

beautifulsoup4 with the lxml parser builds the tree a browser would (a missing </td>, </li> or </p> closes where
HTML says it does), and markdownify writes the Markdown. markdownify is built on beautifulsoup4, so one tree serves
both. Text that is never shown (script, style, title and the like) is left out, and neither function runs or fetches
anything the page names. Every pass over the tree is linear in its size, whatever the page's shape.
"""

import re
import secrets

from bs4 import BeautifulSoup
from bs4.element import CData, NavigableString, PageElement, Tag
from markdownify import MarkdownConverter

# Not "head": libxml2 leaves an element it does not know (header, nav, main) in an implied head, where a browser shows
# it. What a real head holds is either here (title, style, script) or has no text (meta, link, base).
HIDDEN = frozenset(("script", "style", "title", "noscript", "template", "svg", "iframe"))
BLOCKS = frozenset(
    "address article aside blockquote dd div dl dt figcaption figure footer h1 h2 h3 h4 h5 h6 header hr li main "
    "nav ol p pre section table tr ul".split()
)
AS_DIV = frozenset(("aside", "dd", "dt", "figure", "footer", "header", "main", "nav"))  # markdownify inlines these
BREAK, LINE = "\x00", "\x01"  # a block edge (runs of them are one line break) and a line break (each one counts)
ORDINAL = "data-tico-ordinal"  # an ordered list item's number, counted once per list


def _soup(source: str) -> tuple[BeautifulSoup, str]:
    """The tree and the page's <title>. huge_tree lets libxml2 read a text or comment node over 10 MB as one node;
    without it the parser gives up on the node and shows a comment's contents as text."""
    soup = BeautifulSoup(source.replace(BREAK, "").replace(LINE, ""), "lxml", huge_tree=True)
    return soup, " ".join(soup.title.get_text().split()) if soup.title else ""


def _words(root: PageElement) -> str:
    """The text under `root`: a line per block and per line break in the source, and a space at every other tag's
    edges, as the search index and the mail reader have always split words. Read without recursion, so a deeply
    nested page reads like any other."""
    out: list[str] = []
    stack: list[PageElement | str] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, NavigableString):
            if type(node) in (NavigableString, CData):  # comments, doctypes and declarations are not text
                out.append(node.replace("\n", LINE))
        elif isinstance(node, str):
            out.append(node)
        elif isinstance(node, Tag):
            if node.name == "br":
                out.append(LINE)
            elif node.name in HIDDEN:
                out.append(" ")
            else:
                edge = BREAK if node.name in BLOCKS else " "
                out.append(edge)
                stack.append(edge)
                stack.extend(reversed(node.contents))
    text = re.sub(r"[^\S\n]+", " ", "".join(out).replace("\r", ""))
    text = re.sub(r"[ \x00]*\x00[ \x00]*", "\n", text).replace(LINE, "\n")
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_text(source: str) -> tuple[str, str]:
    """(readable plain text, title) of an HTML page: a line per paragraph, heading, list item or table row."""
    soup, title = _soup(source)
    return _words(soup), title


class _Markdown(MarkdownConverter):
    def __init__(self) -> None:
        super().__init__(
            autolinks=False,
            bullets="-",
            escape_asterisks=False,
            escape_underscores=False,
            heading_style="atx",
            wrap=True,
            wrap_width=None,
        )
        # Where a table's cells and rows start and end. Random per conversion, so no text in the page can fake one.
        self.cell, self.end_cell, self.row, self.end_row, self.span = (
            f"\ue000{secrets.token_hex(8)}{n}\ue001" for n in "cCrRs"
        )

    def _hidden(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return ""

    convert_iframe = convert_noscript = convert_svg = convert_template = convert_title = _hidden

    def convert_img(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return str(el.get("alt") or "")  # the words, not a link to fetch

    def convert_video(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return text  # its fallback words, not a poster image to fetch

    def convert_li(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """markdownify's list item, with the number counted once per list instead of once per item."""
        text = text.strip()
        if not text:
            return "\n"
        bullet = (str(el.get(ORDINAL)) if el.get(ORDINAL) else "-") + " "
        text = re.sub(r"^(.+)$", lambda line: " " * len(bullet) + line[1], text, flags=re.M)
        return bullet + text[len(bullet) :] + "\n"

    def convert_td(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        rows = str(el.get("rowspan") or "")
        span = min(int(rows), 1000) if rows.isascii() and rows.isdigit() and int(rows) > 1 else 1
        return self.cell + str(span) + self.span + " ".join(text.split()).replace("|", "\\|") + self.end_cell

    convert_th = convert_td

    def convert_tr(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return self.row + text + self.end_row

    def convert_table(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """Every row, its first as the header, padded to the widest row; empty rows are dropped. A cell spanning rows
        leaves an empty cell in its column below, so later cells stay under their headings. Words in the table but in
        no cell go above it."""
        stray: list[str] = []
        spans: list[list[tuple[int, str]]] = []
        for part in text.split(self.row):
            row, _, after = part.rpartition(self.end_row)
            found: list[tuple[int, str]] = []
            for piece in row.split(self.cell):
                cell, _, outside = piece.rpartition(self.end_cell)
                if _:
                    span, _, words = cell.partition(self.span)
                    found.append((int(span), words))
                stray.append(outside)
            stray.append(after)
            spans.append(found)
        lead = " ".join(" ".join(stray).split())
        cells: list[list[str]] = []
        below: dict[int, int] = {}  # column -> rows a cell above still covers
        for row in (row for row in spans if row):
            laid: list[str] = []
            for span, words in row:
                while below.get(len(laid)):
                    below[len(laid)] -= 1
                    laid.append("")
                if span > 1:
                    below[len(laid)] = span - 1
                laid.append(words)
            for column in [c for c in below if c >= len(laid) and below[c]]:
                below[column] -= 1
            cells.append(laid)
        if not cells:
            return "\n\n" + lead + "\n\n"
        width = max(len(row) for row in cells)
        cells = [row + [""] * (width - len(row)) for row in cells]
        lines = ["| " + " | ".join(cells[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(row) + " |" for row in cells[1:]]
        return "\n\n" + (lead + "\n\n" if lead else "") + "\n".join(lines) + "\n\n"


def _prepare(soup: BeautifulSoup, links: tuple[str, ...]) -> None:
    """Shape the tree for markdownify in one pass."""
    gone: set[int] = set()  # tags inside a nested table already turned into words
    for tag in soup.find_all(True):
        name = tag.name
        if id(tag) in gone:
            continue
        if name == "table" and any(parent.name == "table" for parent in tag.parents):
            # Markdown cannot nest tables: the inner table becomes its words, in order, in the outer cell.
            gone.update(map(id, tag.find_all(True)))
            tag.string = " " + " ".join(_words(tag).split()) + " "
            tag.name, tag.attrs = "span", {}
        elif name in AS_DIV:
            tag.name = "div"  # a block of its own, as markdownify writes a div
        elif name == "a":
            tag.attrs.pop("title", None)
            href = str(tag.get("href") or "").strip().lower()
            if not href or href.startswith("javascript:") or links and not href.startswith(links):
                tag.name, tag.attrs = "span", {}  # its words stay, as plain text with their spaces
        elif name == "ol":
            start = str(tag.get("start") or "")
            number = int(start) if re.fullmatch(r"[0-9]{1,9}", start) else 1
            for item in tag.find_all("li", recursive=False):
                item[ORDINAL] = f"{number}."
                number += 1
        elif re.sub(r"[\[\]:-]", "_", name) in ("soup", "fn_cache"):
            tag.name = "span"  # markdownify would call its own convert_soup or convert_fn_cache for these


def to_markdown(source: str, links: tuple[str, ...] = ()) -> tuple[str, str]:
    """(Markdown, title) of an HTML page. `links`: keep only links whose address starts with one of these (every link
    but javascript: when empty). A page nested deeper than markdownify can walk comes out as its plain text."""
    soup, title = _soup(source)
    _prepare(soup, links)
    try:
        return _Markdown().convert_soup(soup), title
    except RecursionError:
        return _words(soup), title
