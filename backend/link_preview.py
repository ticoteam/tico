# pyright: strict
"""Link previews: the title, site name, description and picture of an outside web page, for the web app's link chips
(`GET /api/v2/link-preview?url=…`).

The server reads the page, never the browser: a picture or a favicon fetched from a thread would tell a stranger who
read what, and when. The picture is made small and re-encoded as JPEG and served from
`/api/v2/link-preview/images/<id>`.

The reading and the picture each run in a short-lived process (backend/link_preview_child.py, which holds the fetch
rules) that is killed at DEADLINE: one preview holds a request worker for at most DEADLINE seconds in all, whatever the
site does (slow headers, a dripped body, a name lookup that hangs). At most AT_ONCE previews are read at once and at
most DECODES pictures decoded at once; a request past either, or for an address already being read, is refused at once
with 429 `busy` rather than waiting. A rehearsal copy reads nothing: every address has no preview there.

Asked only by a person in the app, directly: not a bot, the Assistant acting for a person, an API token or a bot's
read-only view, and never from another site (a request whose Sec-Fetch-Site is not same-origin or none, or one that
carries cookies without saying, is refused), so a page elsewhere cannot make the server fetch an address as the person.

One answer serves everyone: it is kept in `link_previews` for KEEP_HOURS (a page with no preview for MISS_HOURS), keyed
by the address made canonical (no #fragment, its query sorted, tracking parameters dropped). Each person starts at most
FETCHES_PER_MINUTE fetches a minute; a kept answer costs nothing and is never counted. The kept pictures stay under
IMAGES_BYTES in all and the rows under ROWS_MOST, the oldest going first, and expired rows are deleted at the first
request after SWEEP_SECONDS.
"""

import base64
import collections
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import Response

from clients import doc_fetch as _doc_fetch_untyped

from . import hubdb as _hubdb_untyped
from . import link_preview_child as child
from . import views as _views_untyped
from .api_answers.link_previews import LinkPreview
from .api_contract import api_v2, error
from .store import Problem

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime

    from fastapi import FastAPI

    from .auth import Identity
    from .store import Store

_F: Any = _doc_fetch_untyped
_hubdb: Any = _hubdb_untyped
_views: Any = _views_untyped
now = cast("Callable[[], str]", _hubdb.now)
shift = cast("Callable[..., str]", _hubdb.shift)
parse_ts = cast("Callable[[str], datetime | None]", _hubdb.parse_ts)
human_only = cast("Callable[[Identity], None]", _views.human_only)
FetchError = cast("type[Exception]", _F.FetchError)

DEADLINE = 5.0  # seconds one preview may take in all: the page, its picture and making the picture small
MARGIN = 0.3  # of the deadline, left to the reading process to answer before it is killed
AT_ONCE = 8
DECODES = 2
URL_MOST = 2048
INTERNAL_NAMES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
TRACKING = re.compile(
    r"^(?:utm_\w+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|yclid|igshid|mc_cid|mc_eid|_hsenc|_hsmi|mkt_tok)$", re.I
)
KEEP_HOURS = 24
MISS_HOURS = 1
FETCHES_PER_MINUTE = 30
IMAGES_BYTES = 64 * 1024 * 1024
ROWS_MOST = 20_000
SWEEP_SECONDS = 600
IMAGE_PATH = "/api/v2/link-preview/images/"
ID = re.compile(r"^[0-9a-f]{64}$")
ROOT = Path(__file__).resolve().parents[1]
# The reading process, as a command; a seam for tests.
CHILD = [sys.executable, "-m", "backend.link_preview_child"]


@dataclass
class Preview:
    title: str
    site_name: str
    description: str | None = None
    image: bytes | None = None
    image_width: int | None = None
    image_height: int | None = None


def normal(url: str) -> str:
    """`url` as it is kept and fetched: its scheme and host in lower case, no #fragment, its query sorted without
    tracking parameters (utm_*, fbclid, gclid…), so one page is one key. ValueError when it is not a URL."""
    parts = urlsplit(url.strip())
    _port = parts.port  # raises on a bad port
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def refusal(url: str) -> str | None:
    """Why `url` may not be fetched, by its spelling alone, or None. Needs no network: a name that resolves to an
    internal address is refused by the fetch itself."""
    try:
        _parts, host, _port = _F.Fetcher(env={}).check_url(url)
    except FetchError as exc:
        return str(exc)
    host = cast("str", host)
    if urlsplit(url).port not in (None, *child.PORTS):
        return "Only the standard ports have previews"
    if host == "localhost" or host.endswith(INTERNAL_NAMES):
        return "That address is this computer or an internal one"
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    return None if _F.public_address(str(literal)) else "That address is this computer or an internal one"


def _run(args: list[str], ends: float) -> bytes | None:
    """What the reading process wrote, when it ended well before `ends`; None when it failed or was killed there."""
    left = ends - time.monotonic()
    if left <= 0:
        return None
    with subprocess.Popen(
        [*CHILD, *args],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    ) as process:
        try:
            out, _ = process.communicate(timeout=left)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None
    return out if process.returncode == 0 else None


def fetch_in_child(url: str, ends: float) -> child.Read | None:
    """The page at `url` and its picture's bytes, read in its own process killed at `ends`."""
    out = _run(["page", url, f"{max(ends - time.monotonic() - MARGIN, 0.1):.2f}"], ends)
    try:
        got: Any = json.loads(out) if out else None
    except ValueError:
        return None
    if not isinstance(got, dict) or not isinstance(cast("dict[str, Any]", got).get("title"), str):
        return None
    found = cast("dict[str, Any]", got)
    image = found.get("image")
    return {
        "title": found["title"],
        "site_name": str(found.get("site_name") or ""),
        "description": found.get("description") if isinstance(found.get("description"), str) else None,
        "image_url": None,
        "image": base64.b64decode(image) if isinstance(image, str) else None,
    }


class Limits:
    """Each person's fetches in any 60 seconds, the previews being read and the pictures being decoded on this server,
    and when expired rows were last swept. In memory, like backend/fair_share.py: a restart starts every window again."""

    def __init__(self, per_minute: int = FETCHES_PER_MINUTE, at_once: int = AT_ONCE, decodes: int = DECODES) -> None:
        self.per_minute = per_minute
        self.lock = threading.Lock()
        self.started: dict[str, collections.deque[float]] = {}
        self.running = threading.BoundedSemaphore(at_once)
        self.decoding = threading.BoundedSemaphore(decodes)
        self.reading: set[str] = set()
        self.swept = -float(SWEEP_SECONDS)

    def spend(self, actor: str) -> None:
        at = time.monotonic()
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

    def claim(self, key: str) -> None:
        """Start reading `key`, or 429 `busy` at once: it is being read already, or AT_ONCE previews are."""
        with self.lock:
            if key in self.reading or not self.running.acquire(blocking=False):
                raise Problem("busy", "This link preview is being read; try again shortly", 429, retryable=True)
            self.reading.add(key)

    def done(self, key: str) -> None:
        with self.lock:
            self.reading.discard(key)
            self.running.release()

    def sweep_due(self) -> bool:
        at = time.monotonic()
        with self.lock:
            if at - self.swept < SWEEP_SECONDS:
                return False
            self.swept = at
            return True


def small_picture(data: bytes, ends: float, limits: Limits) -> tuple[bytes, int, int] | None:
    """`data` made a small JPEG in its own process, killed at `ends`; None when it does not decode, has too many pixels,
    or DECODES pictures are being decoded and none finishes in time. Re-encoding keeps only pixels: nothing else of the
    outside file reaches the browser."""
    if not limits.decoding.acquire(timeout=max(ends - time.monotonic(), 0)):
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="tico-link-") as folder:
            source, target = Path(folder) / "picture", Path(folder) / "small.jpg"
            source.write_bytes(data)
            out = _run(["picture", str(source), str(target)], ends)
            if not out or not target.is_file():
                return None
            width, height = cast("list[int]", json.loads(out))
            return target.read_bytes(), int(width), int(height)
    except ValueError:
        return None
    finally:
        limits.decoding.release()


def read_page(url: str, limits: Limits) -> Preview | None:
    """The preview of the page at `url`, its picture included when it has one, all within DEADLINE; None when it has
    none or cannot be read."""
    ends = time.monotonic() + DEADLINE
    found = fetch_in_child(url, ends)
    if not found:
        return None
    preview = Preview(found["title"], found["site_name"], found["description"])
    picture = small_picture(found["image"], ends, limits) if found["image"] else None
    if picture:
        preview.image, preview.image_width, preview.image_height = picture
    return preview


COLUMNS = "id, url, found, title, site_name, description, image IS NOT NULL AS pictured, image_width, image_height"


def kept(c: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return c.execute(f"SELECT {COLUMNS} FROM link_previews WHERE id = ? AND expires_at > ?", (key, now())).fetchone()


def sweep(c: sqlite3.Connection) -> None:
    c.execute("DELETE FROM link_previews WHERE expires_at <= ?", (now(),))


def keep(c: sqlite3.Connection, key: str, url: str, preview: Preview | None) -> None:
    """Keep the answer for `url`, then the oldest go until the pictures fit IMAGES_BYTES and the rows ROWS_MOST."""
    at = now()
    sweep(c)
    p = preview or Preview("", "")
    c.execute(
        "INSERT OR REPLACE INTO link_previews(id, url, found, title, site_name, description, image, image_bytes,"
        " image_width, image_height, created_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            url,
            1 if preview else 0,
            p.title or None,
            p.site_name or None,
            p.description,
            p.image,
            len(p.image or b""),
            p.image_width,
            p.image_height,
            at,
            shift(at, hours=KEEP_HOURS if preview else MISS_HOURS),
        ),
    )
    over = int(c.execute("SELECT COALESCE(SUM(image_bytes), 0) FROM link_previews").fetchone()[0]) - IMAGES_BYTES
    if over > 0:
        oldest = c.execute(
            "SELECT id, image_bytes FROM link_previews WHERE image_bytes > 0 AND id <> ? ORDER BY created_at, id",
            (key,),
        )
        gone: list[str] = []
        for row in oldest:
            if over <= 0:
                break
            gone.append(row["id"])
            over -= int(row["image_bytes"])
        c.executemany("DELETE FROM link_previews WHERE id = ?", [(g,) for g in gone])
    c.execute(
        "DELETE FROM link_previews WHERE id IN"
        " (SELECT id FROM link_previews ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?)",
        (ROWS_MOST,),
    )


def shown(row: sqlite3.Row) -> LinkPreview:
    if not row["found"]:
        raise Problem("not_found", "This address has no preview", 404)
    return {
        "url": row["url"],
        "title": row["title"] or "",
        "site_name": row["site_name"] or child.bare_host(row["url"]),
        "description": row["description"],
        "image": IMAGE_PATH + row["id"] if row["pictured"] else None,
        "image_width": row["image_width"] if row["pictured"] else None,
        "image_height": row["image_height"] if row["pictured"] else None,
    }


def direct_person(who: "Identity") -> None:
    """A person in the app themselves: not a bot, the Assistant or a runner turn acting for them, an API token, or a
    bot's read-only view."""
    human_only(who)
    if who.via or who.attempt_id or who.via_token or who.view:
        raise Problem("identity", "Link previews are read only for a person in the app", 403)


def same_site(request: Request) -> None:
    """Refuses a request another site's page made: the browser says where it came from (Sec-Fetch-Site), and one that
    does not say and carries cookies (an older browser) is refused too. A client that sends its own credential, not a
    cookie, cannot be made to ask by another site."""
    site = request.headers.get("sec-fetch-site")
    if site in ("same-origin", "none") or (site is None and "cookie" not in request.headers):
        return
    raise Problem("cross_site", "A link preview is asked for only by the app itself", 403)


def install(app: "FastAPI", store: "Store", limits: Limits | None = None) -> None:
    limit = limits or Limits()

    def read() -> "AbstractContextManager[sqlite3.Connection]":
        return cast("AbstractContextManager[sqlite3.Connection]", store.read())

    def write() -> "AbstractContextManager[sqlite3.Connection]":
        return cast("AbstractContextManager[sqlite3.Connection]", store.transaction())

    def swept() -> None:
        if limit.sweep_due():
            with write() as c:
                sweep(c)

    @app.get(
        "/api/v2/link-preview",
        **api_v2(
            "Link previews",
            "getLinkPreview",
            "The title, site, description and picture of an outside web page, read by the server and kept a day",
            LinkPreview,
            parameters={"url": "The page's http or https address"},
            responses={
                "403": error("identity: not a person in the app; cross_site: asked from another site"),
                "404": error("not_found: the page has no preview (it did not answer, is not HTML or has no title)"),
                "422": error(
                    "invalid_url: not a URL; blocked: the address may not be fetched (not http or https, a private "
                    "address, a port)"
                ),
                "429": error("rate_limited: too many new previews this minute; busy: it or too many are being read"),
            },
        ),
    )
    def link_preview(request: Request, url: str) -> LinkPreview:
        who: Identity = request.state.identity
        direct_person(who)
        same_site(request)
        if len(url) > URL_MOST:
            raise Problem("invalid_url", f"The address is longer than {URL_MOST} characters", 422)
        try:
            url = normal(url)
        except ValueError as exc:
            raise Problem("invalid_url", "That is not a web address", 422) from exc
        refused = refusal(url)
        if refused:
            raise Problem("blocked", refused, 422)
        swept()
        key = hashlib.sha256(url.encode()).hexdigest()
        with read() as c:
            row = kept(c, key)
        if row is not None:
            return shown(row)
        if cast("Any", store).settings.rehearsal:  # a rehearsal copy sends nothing, a page's fetch included
            raise Problem("not_found", "This address has no preview", 404)
        limit.spend(who.actor)
        limit.claim(key)
        try:
            preview = read_page(url, limit)
        finally:
            limit.done(key)
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
            "A link preview's picture: the page's own, made small and re-encoded as JPEG, until its preview expires",
            {"description": "The picture", "content": {"image/jpeg": {}}},
            parameters={"preview_id": "The end of the preview's `image` address"},
            responses={"404": error("not_found: no such picture, or its preview expired")},
        ),
    )
    def link_preview_image(request: Request, preview_id: str) -> Response:
        direct_person(request.state.identity)
        if not ID.match(preview_id):
            raise Problem("not_found", "No such picture", 404)
        swept()
        with read() as c:
            row = c.execute(
                "SELECT image, expires_at FROM link_previews WHERE id = ? AND expires_at > ?", (preview_id, now())
            ).fetchone()
        if row is None or row["image"] is None:
            raise Problem("not_found", "No such picture", 404)
        ends, at = parse_ts(row["expires_at"]), parse_ts(now())
        fresh = max(0, int((ends - at).total_seconds())) if ends and at else 0
        return Response(
            bytes(row["image"]),
            media_type="image/jpeg",
            headers={
                "Cache-Control": f"private, max-age={fresh}",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
