"""The Company page's one template: pure functions from the view model (see the package docstring) to text.

No database and no I/O beyond this package's own template files. This is the only code that writes reader HTML, so
every value is escaped here and nothing from the view model reaches the page unescaped: update bodies pass through a
tiny Markdown subset, accent and logo are validated and dropped to a safe default when they fail. The page sets its own
Content-Security-Policy with hashes, so it holds wherever it is served; whoever serves it adds frame-ancestors, which a
meta tag can't carry.
"""
import base64
import hashlib
import html
import math
import re
import uuid
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape as xml_escape

TEMPLATE_DIR = Path(__file__).parent / "template"
# Copied into each release as assets/fonts; the CSS asks for them relatively, and a missing font falls back to the stack.
FONT_DIR = TEMPLATE_DIR / "fonts"

# Reader routes this template links to; reader_app.py answers them.
SIGN_IN_PATH = "/sign-in"
SIGN_OUT_PATH = "/sign-out"
REPLY_PATH = "/reply"
HELP_PATH = "/can-help"

DEFAULT_ACCENT = "#1E4FD8"
FEED_ENTRIES = 50
RECENT_UPDATES = 3      # shown before Show older when the view already holds every update
MAX_CHART_POINTS = 12

SECTION_LABELS = {"numbers": "Numbers", "highlights": "Highlights", "lowlights": "Lowlights", "asks": "Asks",
                  "next": "Next"}
SECTION_ORDER = ("numbers", "highlights", "lowlights", "asks", "next")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
PNL_STRONG = {"revenue", "total revenue", "gross profit", "total expenses", "net income", "net profit", "net loss"}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\Z")
# Same-release and relative only: no scheme, no leading slash, no `..`, and only image types that can't carry script.
_LOGO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*\.(?:png|jpe?g|webp|gif)\Z", re.I)
# The owner's preview has no release folder to load from, so its logo arrives inline: a PNG, JPEG or WebP, base64.
_LOGO_DATA = re.compile(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}\Z")
_ANCHOR = re.compile(r"[^A-Za-z0-9_-]+")


@lru_cache(maxsize=None)
def _template(name):
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _text(value):
    """A value as display text. Only strings and numbers are text: anything else in the model shows as nothing."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return _CONTROL.sub("", str(value))


def _e(value):
    return html.escape(_text(value), quote=True)


def _map(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def accent_of(value):
    """A validated #rrggbb. Anything else takes the default, because it goes into a stylesheet."""
    text = value if isinstance(value, str) else ""
    if not _HEX.match(text):
        return DEFAULT_ACCENT
    return "#" + "".join(c * 2 for c in text[1:]) if len(text) == 4 else text


def logo_of(value, inline=False):
    text = value if isinstance(value, str) else ""
    return text if _LOGO.match(text) or (inline and _LOGO_DATA.match(text)) else ""


def anchor_of(value):
    """An id and URL fragment that can't break out of either."""
    return _ANCHOR.sub("-", _text(value)).strip("-")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _on_accent(accent):
    """Ink that reads on the accent: the company picks the colour, so a pale one can't take white text."""
    r, g, b = (int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5))
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return "#14171f" if 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2] > 0.4 else "#ffffff"


# ------------------------------------------------------------------------------------------------- dates and periods
def _date(value):
    text = _text(value)
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _day(value, year=None):
    """Nov 1, 2026; or Nov 1 when `year` is the same year. Text that isn't a date shows as it is."""
    when = _date(value)
    if when is None:
        return _text(value)
    day = f"{MONTHS[when.month - 1]} {when.day}"
    return day if year == when.year else f"{day}, {when.year}"


def _period(value):
    """2026-10 reads as Oct 2026; any other period already reads as the data wrote it."""
    text = _text(value)
    match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", text)
    return f"{MONTHS[int(match[2]) - 1]} {match[1]}" if match else text


def _short_period(value):
    text = _period(value)
    match = re.fullmatch(r"([A-Z][a-z]{2}) \d{4}", text)
    return match[1] if match else text


# ------------------------------------------------------------------------------------------------- safe Markdown
_LINK = re.compile(r"(?<!!)\[([^\]\n]{1,300})\]\((https://[^\s()<>]{1,2000})\)")
_BOLD = re.compile(r"\*\*(?=\S)([^*\n]{1,2000}?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![*\w])[*_](?=\S)([^*_\n]{1,2000}?)(?<=\S)[*_](?![*\w])")
_BULLET = re.compile(r"\s{0,3}[-*]\s+(.*)\Z")
_ORDERED = re.compile(r"\s{0,3}\d{1,3}[.)]\s+(.*)\Z")


def _emphasis(escaped):
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", escaped))


def inline(value):
    """Bold, italics and https links. Everything is escaped first, so anything else stays literal text."""
    escaped = html.escape(_text(value), quote=True)
    out, at = [], 0
    for match in _LINK.finditer(escaped):
        url = html.unescape(match[2])
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            continue
        out.append(_emphasis(escaped[at:match.start()]))
        out.append(f'<a href="{match[2]}" rel="noopener noreferrer nofollow">{_emphasis(match[1])}</a>')
        at = match.end()
    out.append(_emphasis(escaped[at:]))
    return "".join(out)


def markdown(value):
    """Paragraphs and lists around inline(). One paragraph with nothing else comes back without a <p>."""
    blocks, para, items, kind = [], [], [], None

    def flush():
        nonlocal para, items, kind
        if para:
            blocks.append(("p", " ".join(para)))
        if items:
            blocks.append((kind, list(items)))
        para, items, kind = [], [], None

    for line in _text(value).replace("\r\n", "\n").split("\n"):
        bullet, ordered = _BULLET.match(line), _ORDERED.match(line)
        if bullet or ordered:
            tag = "ul" if bullet else "ol"
            if para or kind != tag:
                flush()
            kind = tag
            items.append((bullet or ordered)[1])
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    if len(blocks) == 1 and blocks[0][0] == "p":
        return inline(blocks[0][1])
    return "".join(f"<p>{inline(b[1])}</p>" if b[0] == "p"
                   else f"<{b[0]}>" + "".join(f"<li>{inline(i)}</li>" for i in b[1]) + f"</{b[0]}>" for b in blocks)


# ------------------------------------------------------------------------------------------------- charts
def _points(stat):
    """The latest points of a series, as (short period, full period, value, display)."""
    out = []
    for point in _list(_map(stat).get("series"))[-MAX_CHART_POINTS:]:
        point = _map(point)
        out.append((_short_period(point.get("period")), _period(point.get("period")), _number(point.get("value")),
                    _text(point.get("display")) or "—"))
    return out


def _plottable(points):
    return sum(1 for p in points if p[2] is not None) >= 2


def _alt(stat, points):
    label = _text(_map(stat).get("label"))
    return _e(f"{label}: " + ", ".join(f"{p[1]} {p[3]}" for p in points))


def _segments(points, width, top, height, center=False):
    """Screen coordinates, broken at a missing value so a gap isn't drawn as a change."""
    values = [p[2] for p in points if p[2] is not None]
    low, high = min(values), max(values)
    span = high / 2 - low / 2       # halves, so two huge values can't overflow to infinity
    step = width / len(points) if center else width / max(len(points) - 1, 1)
    segments, current = [], []
    for i, point in enumerate(points):
        if point[2] is None:
            if current:
                segments.append(current)
            current = []
            continue
        x = (i + 0.5) * step if center else i * step
        y = top + height / 2 if not span else top + height - (point[2] / 2 - low / 2) / span * height
        current.append((round(x, 1), round(y, 1)))
    if current:
        segments.append(current)
    return segments


def _coords(points):
    return " ".join(f"{x:g},{y:g}" for x, y in points)


def _sparkline(stat, points):
    segments = _segments(points, 200, 6, 32)
    area = ""
    if len(segments) == 1 and len(segments[0]) == len(points):
        line = segments[0]
        area = f'<polygon class="area" points="{line[0][0]:g},44 {_coords(line)} {line[-1][0]:g},44"></polygon>'
    lines = "".join(f'<polyline points="{_coords(s)}"></polyline>' for s in segments)
    end = segments[-1][-1]
    return (f'<svg class="spark" role="img" aria-label="{_alt(stat, points)}" viewBox="0 0 200 44">{area}{lines}'
            f'<circle cx="{end[0]:g}" cy="{end[1]:g}" r="3"></circle></svg>')


def _bars(stat, points):
    height = 140
    values = [p[2] for p in points if p[2] is not None]
    top, low = max(0, *values), min(0, *values)
    span = (top - low) or 1
    below = round(-low / span * height)
    columns = []
    for i, point in enumerate(points):
        value = point[2] or 0
        up = round(value / span * height) if value > 0 else 0
        down = round(-value / span * height) if value < 0 else 0
        last = " last" if i == len(points) - 1 else ""
        bar = f'<svg class="bar" width="100%" height="{max(up, 2) if value > 0 else up}"><rect width="100%" height="100%"></rect></svg>' if up or value > 0 else ""
        under = ""
        if below:
            hang = (f'<svg class="bar" width="100%" height="{down}"><rect width="100%" height="100%"></rect></svg>' if down else "")
            rest = f'<svg width="100%" height="{below - down}"></svg>' if below > down else ""
            under = f'<div class="below">{hang}{rest}</div>'
        columns.append(f'<div class="col{last}"><div class="val">{_e(point[3])}</div>{bar}{under}'
                       f'<div class="mon">{_e(point[0])}</div></div>')
    return f'<div class="bars" role="img" aria-label="{_alt(stat, points)}">{"".join(columns)}</div>'


def _line(stat, points):
    width, height = 600, 140
    segments = _segments(points, width, 8, height - 16, center=True)
    area = ""
    if len(segments) == 1 and len(segments[0]) == len(points):
        line = segments[0]
        area = f'<polygon class="area" points="{line[0][0]:g},{height} {_coords(line)} {line[-1][0]:g},{height}"></polygon>'
    lines = "".join(f'<polyline vector-effect="non-scaling-stroke" points="{_coords(s)}"></polyline>' for s in segments)
    values = "".join(f"<div><dt>{_e(p[0])}</dt><dd>{_e(p[3])}</dd></div>" for p in points)
    return (f'<svg class="line" role="img" aria-label="{_alt(stat, points)}" viewBox="0 0 {width} {height}" width="100%" '
            f'height="{height}" preserveAspectRatio="none">{area}{lines}</svg><dl class="vals">{values}</dl>')


def _previous(points):
    """The period before the latest, in words, so a stat reads without its chart."""
    if len(points) < 2 or points[-2][3] == "—":
        return ""
    # A day in the period's name makes it a date, which reads "on Oct 29, 2026"; a month alone reads "in Sep 2026".
    joiner = "on" if re.search(r"\b\d{1,2}\b", points[-2][0]) else "in"
    return f"vs {points[-2][3]} {joiner} {points[-2][0]}"


def _freshness(stat, year):
    stat = _map(stat)
    as_of = _day(stat.get("as_of"), year)
    text = f"As of {as_of}" if as_of else ""
    if stat.get("stale") is True:
        return f'<span class="stale">Out of date</span>{" · " + _e(text) if text else ""}'
    return _e(text)


def _note(stat, year):
    definition, fresh = _e(_map(stat).get("definition")), _freshness(stat, year)
    return (f"<p>{definition}</p>" if definition else "") + (f"<p>{fresh}</p>" if fresh else "")


def _hero(stat, year):
    points = _points(stat)
    chart = ""
    if _plottable(points):
        chart = _bars(stat, points) if stat.get("chart") == "bars" else _line(stat, points)
    previous = _previous(points)
    return (f'<div class="hero"><div class="hero-head"><div class="hero-num"><div class="stat-label">{_e(stat.get("label"))} '
            f'<span>· {_e(_period(stat.get("period")))}</span></div><div class="big">{_e(stat.get("display"))}</div>'
            + (f'<div class="sub">{_e(previous)}</div>' if previous else "")
            + f'</div><div class="note">{_note(stat, year)}</div></div>{chart}</div>')


def _tile(stat, year):
    points = _points(stat)
    previous = _previous(points)
    chart = ""
    if _plottable(points):
        chart = _sparkline(stat, points)
        known = [p for p in points if p[2] is not None]
        chart += (f'<div class="ends"><span>{_e(known[0][0])} {_e(known[0][3])}</span>'
                  f'<span>{_e(known[-1][0])} {_e(known[-1][3])}</span></div>')
    fresh = _freshness(stat, year)
    return (f'<div class="tile"><div class="tile-head"><b>{_e(stat.get("label"))}</b>'
            f'<span>{_e(_period(stat.get("period")))}</span></div><div class="num">{_e(stat.get("display"))}</div>'
            + (f'<div class="sub">{_e(previous)}</div>' if previous else "") + chart
            + (f'<div class="def">{_e(stat.get("definition"))}</div>' if _text(stat.get("definition")) else "")
            + (f'<div class="def">{fresh}</div>' if fresh else "") + "</div>")


def _pnl(stats):
    """P&L lines as one table, the last three periods any line reports. Period labels are short ("Oct"), so they can't
    be sorted: the order is the series' own, with the longest series as the reference."""
    series = [[_text(_map(p).get("period")) for p in _list(stat.get("series"))] for stat in stats]
    periods = []
    for labels in sorted(series, key=len, reverse=True):
        for period in labels:
            if period and period not in periods:
                periods.append(period)
    periods = periods[-3:]
    head = "".join(f'<th scope="col">{_e(_short_period(p))}</th>' for p in periods)
    rows = []
    for stat in stats:
        cells = {_text(_map(p).get("period")): _text(_map(p).get("display")) for p in _list(stat.get("series"))}
        strong = ' class="strong"' if _text(stat.get("label")).strip().lower() in PNL_STRONG else ""
        tds = "".join(f"<td>{_e(cells.get(p) or '—')}</td>" for p in periods)
        rows.append(f'<tr{strong}><th scope="row">{_e(stat.get("label"))}</th>{tds}</tr>')
    return ('<div class="pnl"><div class="pnl-head">P&amp;L</div><div class="pnl-scroll"><table><thead><tr>'
            f'<td></td>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></div>')


def _stats(view, year):
    stats = [s for s in _list(view.get("stats")) if isinstance(s, dict)]
    if not stats:
        return ""
    pnl = [s for s in stats if s.get("group") == "pnl"]
    rest = [s for s in stats if s.get("group") != "pnl"]
    heroes = "".join(_hero(s, year) for s in rest if s.get("chart") in ("bars", "line"))
    tiles = "".join(_tile(s, year) for s in rest if s.get("chart") not in ("bars", "line"))
    return ('<section class="stats" aria-label="Stats">' + heroes + (f'<div class="tiles">{tiles}</div>' if tiles else "")
            + (_pnl(pnl) if pnl else "") + "</section>")


# ------------------------------------------------------------------------------------------------- updates
def _initials(name):
    words = [w for w in re.split(r"[\s,]+", _text(name)) if w and w.lower() not in ("of", "the", "and", "for")]
    return "".join(w[0].upper() for w in words[:2])


def _author(author):
    """Who wrote it. A bot is always labelled as one."""
    author = _map(author)
    name = _text(author.get("name"))
    return f"{name} · bot" if author.get("kind") == "bot" else name


def _hidden(view):
    csrf = _text(view.get("csrf"))
    return f'<input type="hidden" name="csrf" value="{_e(csrf)}">' if csrf else ""


def _section(section, update, view):
    key = section.get("key")
    items = [i for i in _list(section.get("items")) if isinstance(i, dict) and _text(i.get("body"))]
    if key not in SECTION_LABELS or not items:
        return ""
    if key == "numbers":
        pairs = [_text(i["body"]).split(":", 1) for i in items]
        if all(len(p) == 2 and p[0].strip() and p[1].strip() for p in pairs):
            cells = "".join(f"<div><span>{_e(p[0].strip())}</span><b>{_e(p[1].strip())}</b></div>" for p in pairs)
            return f'<div class="figures" aria-label="Numbers">{cells}</div>'
    actions = view.get("actions") is True and key == "asks"
    rows = []
    for item in items:
        form = ""
        if actions and _text(item.get("ask_id")):
            form = (f'<form class="help" method="post" action="{HELP_PATH}">{_hidden(view)}'
                    f'<input type="hidden" name="anchor" value="{_e(anchor_of(update.get("anchor")))}">'
                    f'<input type="hidden" name="ask_id" value="{_e(item.get("ask_id"))}">'
                    '<button class="btn" type="submit">I can help</button></form>')
        rows.append(f"<li>{markdown(item['body'])}{form}</li>")
    return (f'<div class="section"><h4>{SECTION_LABELS[key]}</h4><ul class="prose">{"".join(rows)}</ul></div>')


def _reply(update, view, anchor):
    author = _text(_map(update.get("author")).get("name"))
    return (f'<details class="reply"><summary>Reply</summary><form method="post" action="{REPLY_PATH}">{_hidden(view)}'
            f'<input type="hidden" name="anchor" value="{_e(anchor)}"><label for="reply-{_e(anchor)}">Reply to {_e(author)}</label>'
            f'<textarea id="reply-{_e(anchor)}" name="body" rows="3" required></textarea>'
            '<div><button class="btn solid" type="submit">Send</button></div></form></details>')


def _update(update, view, is_open, year):
    anchor = anchor_of(update.get("anchor"))
    author = _map(update.get("author"))
    name = _author(author)
    avatar = "avatar bot" if author.get("kind") == "bot" else "avatar"
    when = _text(update.get("published_at"))
    date_attr = f' datetime="{_e(when)}"' if _date(when) else ""
    sections = {_map(s).get("key"): _map(s) for s in _list(update.get("sections"))}
    body = "".join(_section(sections[k], update, view) for k in SECTION_ORDER if k in sections)
    note = _text(update.get("correction_note"))
    correction = f'<p class="correction">Corrected: {inline(note)}</p>' if note else ""
    link = f'<a href="#{_e(anchor)}">Link</a>'
    reply = _reply(update, view, anchor) if view.get("actions") is True else ""
    return (f'<article class="update" id="{_e(anchor)}"><details{" open" if is_open else ""}><summary>'
            f'<div class="head"><h3>{_e(update.get("title"))}</h3><span class="who">'
            f'<span class="{avatar}" aria-hidden="true">{_e(_initials(author.get("name")))}</span><span>{_e(name)}</span>'
            f'<span aria-hidden="true">·</span><time{date_attr}>{_e(_day(when, year))}</time></span></div>'
            '<svg class="chev" aria-hidden="true" viewBox="0 0 16 16" width="18" height="18"><path d="M3.5 6l4.5 4.5L12.5 6"></path></svg>'
            f'</summary><div class="body">{correction}{body}<div class="foot">{link}{reply}</div></div></details></article>')


def _updates(view, year):
    updates = [u for u in _list(view.get("updates")) if isinstance(u, dict)]
    if not updates:
        return ('<section class="updates" aria-labelledby="updates-heading"><h2 id="updates-heading">Updates</h2>'
                '<p class="empty">No updates yet.</p></section>')
    before = _text(view.get("next_before"))
    articles = [_update(u, view, i == 0, year) for i, u in enumerate(updates)]
    if before:
        # There is a further page: no script follows the link, the script fetches it in place.
        listed = "".join(articles)
        more = (f'<div class="more"><a class="btn" data-older href="?before={quote(before, safe="")}">Show older</a></div>')
    elif len(articles) > RECENT_UPDATES:
        # Static public files can't answer ?before=, so a whole view holds its older updates here, closed.
        listed = "".join(articles[:RECENT_UPDATES])
        more = ('<details class="more"><summary>Show older</summary>' + "".join(articles[RECENT_UPDATES:]) + "</details>")
    else:
        listed, more = "".join(articles), ""
    return ('<section class="updates" aria-labelledby="updates-heading"><h2 id="updates-heading">Updates</h2>'
            f'<div id="update-list">{listed}</div>{more}</section>')


# ------------------------------------------------------------------------------------------------- the page
def badge_label(view):
    company, audience = _map(view.get("company")), _map(view.get("audience"))
    badge = company.get("badge")
    if badge == "open":
        return "Open company"
    if badge == "public":
        return "Public"
    kind = audience.get("kind")
    if kind == "board":
        return "Private to the board"
    if kind == "investors":
        return "Private to investors"
    name = _text(audience.get("name"))
    return f"Private to {name}" if name else "Private"


def _is_public(view):
    """Only output that says it is public may be indexed or linked as a feed. Anything unknown counts as private."""
    return _map(view.get("audience")).get("kind") == "public" and _map(view.get("company")).get("badge") in ("open", "public")


def _hash(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def _document(view, body, title, description, canonical=""):
    company = _map(view.get("company"))
    accent = accent_of(company.get("accent"))
    theme = f":root{{--accent:{accent};--on-accent:{_on_accent(accent)}}}"
    css, script = _template("page.css"), _template("page.js")
    policy = ("default-src 'none'; "
              f"style-src {_hash(theme)} {_hash(css)}; script-src {_hash(script)}; img-src 'self'; font-src 'self'; "
              "connect-src 'self'; form-action 'self'; base-uri 'none'")
    public = _is_public(view)
    robots = "" if public else '<meta name="robots" content="noindex,nofollow,noarchive">'
    extra = (f'<link rel="alternate" type="application/atom+xml" href="feed.xml" title="{_e(title)}">' if public else "")
    if canonical and public:
        extra += f'<link rel="canonical" href="{_e(canonical)}">'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{_e(policy)}">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="strict-origin-when-cross-origin">'
            f'{robots}<title>{_e(title)}</title><meta name="description" content="{_e(description)}">{extra}'
            f'<style>{theme}</style><style>{css}</style></head><body>{body}<script>{script}</script></body></html>')


def _brand(company, inline_logo=False):
    name = _text(company.get("name"))
    logo = logo_of(company.get("logo_url"), inline_logo)
    mark = (f'<img class="logo" src="{_e(logo)}" alt="" width="36" height="36">' if logo
            else f'<div class="logo" aria-hidden="true">{_e(name[:1].upper())}</div>')
    return f'<div class="brand">{mark}<div class="name">{_e(name)}</div></div>'


def _footer():
    return '<footer><i aria-hidden="true"></i><span>Runs on Tico</span></footer>'


def _url(company):
    """The page's own address, when the view knows it and it's a plain http(s) origin."""
    text = _text(company.get("url"))
    parts = urlsplit(text)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return ""
    return text.rstrip("/") + "/"


def page(view, inline_logo=False):
    """The whole page. Stats sit above updates; the newest update is open, and it reads with no script at all. The owner's
    preview passes `inline_logo`: its logo arrives as a data URI, which a release never carries."""
    view = _map(view)
    company = _map(view.get("company"))
    updated = _date(company.get("updated_at"))
    year = updated.year if updated else None
    name, mission = _text(company.get("name")), _text(company.get("mission"))
    public = _is_public(view)
    viewer = _text(_map(view.get("viewer")).get("email"))
    tools = ""
    if viewer:
        tools = (f'<span>{_e(viewer)}</span><form method="post" action="{SIGN_OUT_PATH}">{_hidden(view)}'
                 '<button class="linkish" type="submit">Sign out</button></form>')
    if public:
        tools += '<a class="linkish" href="feed.xml">RSS</a>'
    mark = ('<i aria-hidden="true"></i>' if public else
            '<svg aria-hidden="true" viewBox="0 0 16 16" width="13" height="13"><rect x="3" y="7" width="10" height="7" rx="1.5"></rect><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"></path></svg>')
    stamp = _day(company.get("updated_at"))
    header = (f'<header><div class="top">{_brand(company, inline_logo)}<div class="tools">{tools}</div></div>'
              f'<h1>{_e(mission or "Company updates")}</h1><div class="meta"><span class="badge">{mark}{_e(badge_label(view))}</span>'
              + (f'<span>Updated {_e(stamp)}</span>' if stamp else "") + "</div></header>")
    body = f'<div class="wrap">{header}{_stats(view, year)}{_updates(view, year)}{_footer()}</div>'
    title = f"{name}: {mission}" if name and mission else (name or mission)
    return _document(view, body, title, mission or name, _url(company))


def signed_out(view):
    """What a private page shows to anyone not signed in: the company and which audience, and no content."""
    view = _map(view)
    company = _map(view.get("company"))
    name, mission = _text(company.get("name")), _text(company.get("mission"))
    who = _e(name) if name else "this company"
    body = (f'<div class="wrap gate">{_brand(company)}<main><h1>{_e(badge_label(view))}</h1>'
            f'<p class="lead">Sign in to read {who}\'s updates. {who} chooses who can see them.</p>'
            f'<a class="signin" href="{SIGN_IN_PATH}">Sign in with Tico</a>'
            f'<p class="small">No access? Ask your contact at {who} to add your email.</p></main>{_footer()}</div>')
    # Whatever the view says, this page is the door to private content and is never indexed.
    closed = {**view, "audience": {**_map(view.get("audience")), "kind": "private"}}
    return _document(closed, body, f"{name}, sign in" if name else "Sign in", mission or name)


# ------------------------------------------------------------------------------------------------- the feed
def _x(value):
    return xml_escape(_text(value))


def _rfc3339(value):
    """An Atom date. Stored times are UTC; one that can't be read gets the epoch rather than an invalid feed."""
    text = _text(value)
    match = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}:\d{2}))?", text)
    if not match or _date(text) is None:
        return "1970-01-01T00:00:00Z"
    return f"{match[1]}T{match[2] or '00:00:00'}Z"


def _entry_html(update):
    sections = {_map(s).get("key"): _map(s) for s in _list(update.get("sections"))}
    out = []
    note = _text(update.get("correction_note"))
    if note:
        out.append(f"<p>Corrected: {inline(note)}</p>")
    for key in SECTION_ORDER:
        items = [i for i in _list(sections.get(key, {}).get("items")) if isinstance(i, dict) and _text(i.get("body"))]
        if items:
            out.append(f"<h4>{SECTION_LABELS[key]}</h4><ul>" + "".join(f"<li>{markdown(i['body'])}</li>" for i in items) + "</ul>")
    return "".join(out)


def feed(view):
    """Atom, newest first, each entry linking to its anchor on the page."""
    view = _map(view)
    company = _map(view.get("company"))
    name = _text(company.get("name"))
    base = _url(company)
    home = base or "./"
    feed_id = uuid.uuid5(uuid.NAMESPACE_URL, base or f"company-page:{name}")
    entries = []
    updates = [u for u in _list(view.get("updates")) if isinstance(u, dict)][:FEED_ENTRIES]
    for update in updates:
        anchor = anchor_of(update.get("anchor"))
        when = _rfc3339(update.get("published_at"))
        entries.append(f"<entry><id>urn:uuid:{uuid.uuid5(feed_id, anchor)}</id><title>{_x(update.get('title'))}</title>"
                       f'<link rel="alternate" type="text/html" href="{xml_escape(home + "#" + anchor, {chr(34): "&quot;"})}"/>'
                       f"<published>{when}</published><updated>{when}</updated>"
                       f"<author><name>{_x(_author(update.get('author')))}</name></author>"
                       f'<content type="html">{xml_escape(_entry_html(update))}</content></entry>')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>urn:uuid:{feed_id}</id><title>{_x(name)}</title><updated>{_rfc3339(company.get('updated_at'))}</updated>"
            f'<link rel="alternate" type="text/html" href="{xml_escape(home, {chr(34): "&quot;"})}"/>'
            f'<generator>Tico</generator>{"".join(entries)}</feed>\n')
