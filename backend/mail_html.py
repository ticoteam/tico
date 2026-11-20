# pyright: strict
"""The HTML of an email Tico writes: Markdown made into HTML, and a Gmail signature cleaned for it.

Both use only plain markup the send gate's classifier reads whole (email_content.TEXT_MARKUP, with `href` the one
attribute), so an HTML email is reviewed like its text and never held only for being HTML. Pure: nothing here talks
to Gmail or keeps a body.
"""

import html
import re
from html.parser import HTMLParser

SAFE_URL = re.compile(r"(?i)^(https?://|mailto:)[^\s\"'<>]+$")
# One inline token, in order: a code span, a [label](url) link, or a bare web or mailto address.
TOKEN = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)\)"
    r"|(?P<bare>(?:https?://|mailto:)[^\s<>()\[\]]+)"
)
EMPHASIS = (
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "strong"),
    (re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)"), "strong"),
    (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])"), "em"),
    (re.compile(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)"), "em"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), "s"),
)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
NUMBER = re.compile(r"^\s*\d{1,9}[.)]\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
FENCE = re.compile(r"^\s*(```|~~~)")
TRAILING = ".,;:!?'\""

# The signature's markup Tico keeps: plain structure and emphasis, and links. Anything else keeps only its text.
KEPT = frozenset(
    "a b blockquote br code div em h1 h2 h3 h4 h5 h6 hr i li ol p pre s small span strong sub sup "
    "table tbody td th thead tr u ul".split()
)
VOID = frozenset(("br", "hr"))
DROPPED = frozenset(("script", "style", "head", "title"))


def _emphasis(escaped: str) -> str:
    for pattern, tag in EMPHASIS:
        escaped = pattern.sub(lambda m, t=tag: f"<{t}>{m.group(1)}</{t}>", escaped)
    return escaped


def inline(text: str) -> str:
    """One line or paragraph of Markdown as HTML: code spans, links, bold, italic and strikethrough; the rest escaped."""
    out: list[str] = []
    at = 0
    for m in TOKEN.finditer(text):
        out.append(_emphasis(html.escape(text[at : m.start()], quote=False)))
        at = m.end()
        if m.group("code") is not None:
            out.append("<code>" + html.escape(m.group("code"), quote=False) + "</code>")
        elif m.group("label") is not None:
            url = m.group("url")
            if SAFE_URL.match(url):
                label = _emphasis(html.escape(m.group("label"), quote=False))
                out.append(f'<a href="{html.escape(url)}">{label}</a>')
            else:
                out.append(html.escape(m.group(0), quote=False))
        else:
            url = m.group("bare")
            kept = url.rstrip(TRAILING)
            out.append(f'<a href="{html.escape(kept)}">{html.escape(kept, quote=False)}</a>')
            out.append(html.escape(url[len(kept) :], quote=False))
    out.append(_emphasis(html.escape(text[at:], quote=False)))
    return "".join(out)


def _block_start(line: str) -> bool:
    return bool(
        HEADING.match(line)
        or RULE.match(line)
        or BULLET.match(line)
        or NUMBER.match(line)
        or QUOTE.match(line)
        or FENCE.match(line)
    )


def markdown(text: str) -> str:
    """Markdown as HTML: paragraphs (a single line break kept), headings, lists, quotes, code blocks, rules and the
    inline marks `inline` reads. Raw HTML in the text is shown as text, never as markup."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = FENCE.match(line)
        if fence:
            j, code = i + 1, list[str]()
            while j < len(lines) and not lines[j].strip().startswith(fence.group(1)):
                code.append(lines[j])
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(code), quote=False) + "</code></pre>")
            i = j + 1
            continue
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if RULE.match(line):
            out.append("<hr>")
            i += 1
            continue
        if QUOTE.match(line):
            quoted: list[str] = []
            while i < len(lines) and (m := QUOTE.match(lines[i])):
                quoted.append(m.group(1))
                i += 1
            out.append("<blockquote>" + markdown("\n".join(quoted)) + "</blockquote>")
            continue
        for pattern, tag in ((BULLET, "ul"), (NUMBER, "ol")):
            if pattern.match(line):
                items: list[str] = []
                while i < len(lines) and lines[i].strip():
                    m = pattern.match(lines[i])
                    if m:
                        items.append(m.group(1))
                    elif items and not _block_start(lines[i]):
                        items[-1] += "\n" + lines[i].strip()  # a wrapped item continues on the next line
                    else:
                        break
                    i += 1
                out.append(f"<{tag}>" + "".join(f"<li>{_lines(item)}</li>" for item in items) + f"</{tag}>")
                break
        else:
            para: list[str] = []
            while i < len(lines) and lines[i].strip() and not (para and _block_start(lines[i])):
                para.append(lines[i].strip())
                i += 1
            out.append("<p>" + _lines("\n".join(para)) + "</p>")
    return "\n".join(out)


def _lines(text: str) -> str:
    return "<br>\n".join(inline(line) for line in text.split("\n"))


def preformatted(text: str) -> str:
    """Text shown as it is, its line breaks kept: a forwarded message's body in an HTML email."""
    return "<br>\n".join(html.escape(line, quote=False) for line in str(text or "").split("\n"))


class _Clean(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.dropped = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED:
            self.dropped += 1
            return
        if self.dropped:
            return
        if tag == "img":
            alt = next((value for name, value in attrs if name == "alt" and value), "")
            self.out.append(html.escape(alt, quote=False))
        elif tag == "a":
            href = next((value for name, value in attrs if name == "href" and value), "")
            self.out.append(f'<a href="{html.escape(href)}">' if SAFE_URL.match(href.strip()) else "<a>")
        elif tag in KEPT:
            self.out.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in KEPT and tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED:
            self.dropped = max(0, self.dropped - 1)
        elif not self.dropped and tag in KEPT and tag not in VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.dropped:
            self.out.append(html.escape(data, quote=False))


def clean(source: str) -> str:
    """A Gmail signature's HTML in the plain markup above: styles, classes and scripts go, an image becomes its alt
    text, a link keeps only an https or mailto address. What it says stays."""
    parser = _Clean()
    parser.feed(str(source or ""))
    parser.close()
    return "".join(parser.out).strip()
