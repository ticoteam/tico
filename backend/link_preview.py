# pyright: strict
"""Link previews: the title, site name, description and picture of an outside web page, for the web app's link chips
(`GET /api/v2/link-preview?url=…`).

The server reads the page, never the browser: a picture or a favicon fetched from a thread would tell a stranger who
read what, and when. The page's picture is fetched here too, made small and re-encoded as JPEG in a short-lived,
limited subprocess (backend/file_image.py), and served from `/api/v2/link-preview/images/<id>`.

Fetching an address a message holds is fetching an address anyone could have written, so it goes through the guard
`tico doc fetch` and setup's website research use (clients/doc_fetch.py), with tighter limits:
- http or https on the standard port (80 or 443), with no user name or password in it;
- the name is resolved here and refused when any of its addresses is not public (loopback, a private network,
  link-local and so the cloud's metadata address, shared, reserved, and IPv6 tunnels that can wrap one); the connection
  goes to the addresses it checked, so a second lookup cannot send it elsewhere;
- at most REDIRECTS redirects, each hop checked as if it were the first;
- TIMEOUT seconds for the whole fetch, PAGE_BYTES of a page (the rest is not read) and no picture over IMAGE_BYTES;
  only an HTML page and a PNG, JPEG, GIF or WebP picture;
- no cookie, no sign-in, and none of the server's credentials (the fetch is given an empty environment).

One answer serves everyone: it is kept in `link_previews` for KEEP_HOURS (a page with no preview for MISS_HOURS), keyed
by the address. Only a person signed in to the app asks, and each person starts at most FETCHES_PER_MINUTE fetches a
minute and the server AT_ONCE at once; a kept answer costs nothing and is never counted.
"""

import collections
import hashlib
import http.client
import ipaddress
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import Response

from clients import doc_fetch as _doc_fetch_untyped

from . import file_metadata as _file_metadata_untyped
from . import hubdb as _hubdb_untyped
from . import views as _views_untyped
from .api_answers.link_previews import LinkPreview
from .api_contract import api_v2, error
from .store import Problem

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from fastapi import FastAPI

    from .auth import Identity
    from .store import Store

_F: Any = _doc_fetch_untyped
_file_metadata: Any = _file_metadata_untyped
_hubdb: Any = _hubdb_untyped
_views: Any = _views_untyped
now = cast("Callable[[], str]", _hubdb.now)
shift = cast("Callable[..., str]", _hubdb.shift)
human_only = cast("Callable[[Identity], None]", _views.human_only)
FetchError = cast("type[Exception]", _F.FetchError)
DecodeError = cast("type[Exception]", _file_metadata.DecodeError)

TIMEOUT = 5  # seconds for one fetch: its name lookups, redirects and reading together
PAGE_BYTES = 1024 * 1024  # a page is read this far; the tags a preview needs are near its top
IMAGE_BYTES = 1024 * 1024  # a larger picture is not kept
REDIRECTS = 3
PORTS = frozenset((80, 443))
URL_MOST = 2048
PAGE_TYPES = frozenset(("text/html", "application/xhtml+xml"))
IMAGE_TYPES = frozenset(("image/png", "image/jpeg", "image/gif", "image/webp"))  # never SVG: it can carry script
INTERNAL_NAMES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
KEEP_HOURS = 24
MISS_HOURS = 1
FETCHES_PER_MINUTE = 30
AT_ONCE = 8
TITLE_MOST, SITE_MOST, DESCRIPTION_MOST = 300, 100, 400
IMAGE_PATH = "/api/v2/link-preview/images/"
ID = re.compile(r"^[0-9a-f]{64}$")
STRIPES = 64  # one lock per stripe of addresses, so two people asking for one address make one fetch
# What may go wrong reading a page that is the page's or the network's doing, never this server's.
UNREADABLE = (FetchError, OSError, http.client.HTTPException, ValueError, UnicodeError)

# Seams for tests: the name lookup, the connection and the clock (clients/doc_fetch.py's own).
resolver: Callable[[str, int], list[str]] = _F.resolve
opener: Callable[..., Any] = _F.connect
clock: Callable[[], float] = time.monotonic


def _fetcher(kinds: frozenset[str], most: int) -> Any:
    """doc_fetch's Fetcher with this module's limits: the same checks on every hop, less time, fewer bytes and redirects,
    and only `kinds` of body."""

    def accepts(_self: Any, ctype: str) -> bool:
        return ctype in kinds

    limits = {"timeout_s": TIMEOUT, "max_redirects": REDIRECTS, "max_bytes": most, "ports": PORTS, "accepts": accepts}
    return type("LinkPreviewFetcher", (_F.Fetcher,), limits)


PAGE_FETCHER = _fetcher(PAGE_TYPES, PAGE_BYTES)
PICTURE_FETCHER = _fetcher(IMAGE_TYPES, IMAGE_BYTES)


@dataclass
class Preview:
    title: str
    site_name: str
    description: str | None = None
    image_url: str | None = None
    image: bytes | None = None
    image_width: int | None = None
    image_height: int | None = None


@dataclass
class Fetched:
    url: str  # where the body came from, after redirects
    content_type: str
    body: bytes


def normal(url: str) -> str:
    """`url` as it is kept and fetched: trimmed, without its #fragment."""
    parts = urlsplit(url.strip())
    return urlunsplit(parts._replace(fragment=""))


def refusal(url: str) -> str | None:
    """Why `url` may not be fetched, by its spelling alone, or None. Needs no network: a name that resolves to an
    internal address is refused by the fetch itself."""
    try:
        _parts, host, _port = PAGE_FETCHER(env={}).check_url(url)
    except FetchError as exc:
        return str(exc)
    except ValueError:
        return "That is not a web address"
    host = cast("str", host)
    if host == "localhost" or host.endswith(INTERNAL_NAMES):
        return "That address is this computer or an internal one"
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    return None if _F.public_address(str(literal)) else "That address is this computer or an internal one"


def fetch(url: str, kinds: frozenset[str], cut: bool) -> Fetched:
    """The body at `url` (a page when `kinds` is PAGE_TYPES, else a picture), within this module's limits. A page past
    PAGE_BYTES is cut there when `cut`; a picture past IMAGE_BYTES is refused. FetchError when the address may not be
    fetched or does not answer as asked."""
    made = PAGE_FETCHER if kinds == PAGE_TYPES else PICTURE_FETCHER
    fetcher = made(env={}, resolver=resolver, opener=opener, clock=clock)  # env={}: none of the server's credentials
    final, ctype, body, truncated = fetcher.get(url, ",".join(sorted(kinds)))
    if truncated and not cut:
        raise _F.FetchError("too_large", "The picture is larger than the preview keeps")
    return Fetched(cast("str", final), cast("str", ctype), cast("bytes", body))


class _Head(HTMLParser):
    """A page's <meta> tags by name or property (the first of each) and its first <title>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: list[str] = []
        self.titling = False
        self.titled = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            given = {k.lower(): v for k, v in attrs if v is not None}
            key = (given.get("property") or given.get("name") or "").strip().lower()
            if key and "content" in given:
                self.meta.setdefault(key, given["content"])
        elif tag == "title" and not self.titled:
            self.titling = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.titling:
            self.titling, self.titled = False, True

    def handle_data(self, data: str) -> None:
        if self.titling:
            self.title.append(data)


def _text(value: str | None, most: int) -> str | None:
    words = " ".join((value or "").split())
    return (words[: most - 1] + "…" if len(words) > most else words) or None


def _decoded(page: Fetched) -> str:
    """The page's text in the charset its <meta> names, else UTF-8 (what nearly every page is)."""
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", page.body[:4096], re.I)
    named = meta.group(1).decode("ascii", "replace") if meta else None
    return cast("str", _F.decode(page.body, page.content_type, named))


def bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").rstrip(".")
    return host[4:] if host.startswith("www.") else host


def parse(page: Fetched) -> Preview | None:
    """What a page says about itself: Open Graph first, then Twitter's tags, then its <title>. None without a title."""
    head = _Head()
    head.feed(_decoded(page))  # html.parser reads any markup, however broken, without raising
    head.close()
    meta = head.meta

    def first(*keys: str) -> str | None:
        return next((meta[k] for k in keys if meta.get(k, "").strip()), None)

    title = _text(first("og:title", "twitter:title") or "".join(head.title), TITLE_MOST)
    if not title:
        return None
    site = _text(first("og:site_name", "application-name"), SITE_MOST) or bare_host(page.url)
    image = first("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    image_url = urljoin(page.url, image.strip()) if image else None
    if image_url and urlsplit(image_url).scheme not in ("http", "https"):
        image_url = None
    description = _text(first("og:description", "twitter:description", "description"), DESCRIPTION_MOST)
    return Preview(title, site, description, image_url)


def small_picture(data: bytes) -> tuple[bytes, int | None, int | None] | None:
    """`data` decoded in a short-lived, limited subprocess and made a JPEG of at most 480 pixels a side, or None when it
    is not a picture that decodes (or Pillow is missing). Re-encoding keeps only pixels: nothing else of the outside
    file reaches the browser."""
    with tempfile.TemporaryDirectory(prefix="tico-link-") as folder:
        source, target = Path(folder) / "picture", Path(folder) / "small.jpg"
        source.write_bytes(data)
        try:
            _width, _height, made = _file_metadata.image(source, target)
        except (DecodeError, OSError, ValueError):
            return None
        if made is None or not target.is_file():
            return None
        small = target.read_bytes()
        with target.open("rb") as stream:
            width, height = cast("tuple[int | None, int | None]", _file_metadata.dimensions(stream))
        return small, width, height


def read_page(url: str) -> Preview | None:
    """The preview of the page at `url`, its picture included when it has one that may be fetched; None when it has
    none or cannot be read. Never raises for what the page or the network does."""
    try:
        preview = parse(fetch(url, PAGE_TYPES, cut=True))
    except UNREADABLE:
        return None
    if preview is None or not preview.image_url:
        return preview
    try:
        picture = fetch(preview.image_url, IMAGE_TYPES, cut=False)
    except UNREADABLE:
        picture = None
    small = small_picture(picture.body) if picture else None
    if small:
        preview.image, preview.image_width, preview.image_height = small
    return preview


class Limits:
    """Each person's fetches in any 60 seconds, and the fetches running at once on this server. In memory, like
    backend/fair_share.py: a restart starts every window again."""

    def __init__(self, per_minute: int = FETCHES_PER_MINUTE, at_once: int = AT_ONCE) -> None:
        self.per_minute = per_minute
        self.lock = threading.Lock()
        self.started: dict[str, collections.deque[float]] = {}
        self.running = threading.BoundedSemaphore(at_once)
        self.stripes = [threading.Lock() for _ in range(STRIPES)]

    def spend(self, actor: str) -> None:
        at = clock()
        with self.lock:
            for key in [k for k, times in self.started.items() if times and at - times[-1] >= 60]:
                del self.started[key]
            times = self.started.setdefault(actor, collections.deque())
            while times and at - times[0] >= 60:
                times.popleft()
            if len(times) >= self.per_minute:
                wait = max(1, int(60 - (at - times[0])) + 1)
                raise Problem(
                    "rate_limited",
                    f"At most {self.per_minute} new link previews a minute; try again in {wait} seconds",
                    429,
                    retryable=True,
                    extra={"retry_after": wait},
                )
            times.append(at)

    def stripe(self, key: str) -> threading.Lock:
        return self.stripes[int(key[:8], 16) % STRIPES]


COLUMNS = "id, url, found, title, site_name, description, image IS NOT NULL AS pictured, image_width, image_height"


def kept(c: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return c.execute(f"SELECT {COLUMNS} FROM link_previews WHERE id = ? AND expires_at > ?", (key, now())).fetchone()


def keep(c: sqlite3.Connection, key: str, url: str, preview: Preview | None) -> None:
    at = now()
    c.execute("DELETE FROM link_previews WHERE expires_at < ?", (shift(at, hours=-KEEP_HOURS),))
    p = preview or Preview("", "")
    c.execute(
        "INSERT OR REPLACE INTO link_previews(id, url, found, title, site_name, description, image, image_width,"
        " image_height, created_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            url,
            1 if preview else 0,
            p.title or None,
            p.site_name or None,
            p.description,
            p.image,
            p.image_width,
            p.image_height,
            at,
            shift(at, hours=KEEP_HOURS if preview else MISS_HOURS),
        ),
    )


def shown(row: sqlite3.Row) -> LinkPreview:
    if not row["found"]:
        raise Problem("not_found", "This address has no preview", 404)
    return {
        "url": row["url"],
        "title": row["title"] or "",
        "site_name": row["site_name"] or bare_host(row["url"]),
        "description": row["description"],
        "image": IMAGE_PATH + row["id"] if row["pictured"] else None,
        "image_width": row["image_width"] if row["pictured"] else None,
        "image_height": row["image_height"] if row["pictured"] else None,
    }


def install(app: "FastAPI", store: "Store", limits: Limits | None = None) -> None:
    limit = limits or Limits()

    def read() -> "AbstractContextManager[sqlite3.Connection]":
        return cast("AbstractContextManager[sqlite3.Connection]", store.read())

    def write() -> "AbstractContextManager[sqlite3.Connection]":
        return cast("AbstractContextManager[sqlite3.Connection]", store.transaction())

    @app.get(
        "/api/v2/link-preview",
        **api_v2(
            "Link previews",
            "getLinkPreview",
            "The title, site, description and picture of an outside web page, read by the server and kept a day",
            LinkPreview,
            parameters={"url": "The page's http or https address"},
            responses={
                "404": error("not_found: the page has no preview (it did not answer, is not HTML or has no title)"),
                "422": error("blocked: the address may not be fetched (not http or https, a private address, a port)"),
                "429": error("rate_limited: too many new previews this minute; busy: too many fetches running"),
            },
        ),
    )
    def link_preview(request: Request, url: str) -> LinkPreview:
        who: Identity = request.state.identity
        human_only(who)
        url = normal(url)
        if len(url) > URL_MOST:
            raise Problem("blocked", f"The address is longer than {URL_MOST} characters", 422)
        refused = refusal(url)
        if refused:
            raise Problem("blocked", refused, 422)
        key = hashlib.sha256(url.encode()).hexdigest()
        with read() as c:
            row = kept(c, key)
        if row is not None:
            return shown(row)
        limit.spend(who.actor)
        with limit.stripe(key):
            with read() as c:
                row = kept(c, key)  # someone else's fetch of the same address finished while this one waited
            if row is None:
                if not limit.running.acquire(blocking=False):
                    raise Problem("busy", "Too many link previews are being read; try again shortly", 429, True)
                try:
                    preview = read_page(url)
                finally:
                    limit.running.release()
                with write() as c:
                    keep(c, key, url, preview)
                    row = kept(c, key)
        if row is None:  # pragma: no cover - the row was just written in this transaction
            raise Problem("not_found", "This address has no preview", 404)
        return shown(row)

    @app.get(
        IMAGE_PATH + "{preview_id}",
        response_class=Response,
        **api_v2(
            "Link previews",
            "getLinkPreviewImage",
            "A link preview's picture: the page's own, made small and re-encoded as JPEG",
            {"description": "The picture", "content": {"image/jpeg": {}}},
            parameters={"preview_id": "The end of the preview's `image` address"},
            responses={"404": error("not_found: no such picture")},
        ),
    )
    def link_preview_image(request: Request, preview_id: str) -> Response:
        human_only(request.state.identity)
        if not ID.match(preview_id):
            raise Problem("not_found", "No such picture", 404)
        with read() as c:
            row = c.execute("SELECT image FROM link_previews WHERE id = ?", (preview_id,)).fetchone()
        if row is None or row["image"] is None:
            raise Problem("not_found", "No such picture", 404)
        return Response(
            bytes(row["image"]),
            media_type="image/jpeg",
            headers={
                "Cache-Control": f"private, max-age={KEEP_HOURS * 3600}",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
