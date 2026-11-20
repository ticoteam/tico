# pyright: strict
"""The outside work of a link preview (backend/link_preview.py), each part in its own short-lived process that the
server kills at its deadline, so a slow site (slow headers, a dripped body, a name lookup that hangs) or a picture that
decodes huge holds nothing of the server's past it:

    python -m backend.link_preview_child page <url> <seconds>    the page and its picture's bytes, as JSON
    python -m backend.link_preview_child picture <in> <out>      the picture made a small JPEG; [width, height]

Reading goes through the guard `tico doc fetch` and setup's website research use (clients/doc_fetch.py), with tighter
limits:
- http or https on the standard port (80 or 443), with no user name or password in it;
- the name is resolved here and refused when any of its addresses is not public (loopback, a private network,
  link-local and so the cloud's metadata address, shared, reserved, and IPv6 tunnels that can wrap one); the connection
  goes to the addresses it checked, so a second lookup cannot send it elsewhere;
- at most REDIRECTS redirects, each hop checked as if it were the first;
- PAGE_BYTES of a page (the rest is not read) and no picture over IMAGE_BYTES; only an HTML page and a PNG, JPEG, GIF
  or WebP picture (never SVG: it can carry script);
- no cookie, no sign-in and none of the server's credentials: the fetch is given an empty environment.

A picture is refused by its header before it is decoded when it has more than PIXELS_MOST pixels, and the decoder
works at a fraction of a JPEG's size, so a small file that would decode to a huge one never does.

Imports only the standard library and clients/doc_fetch.py (Pillow for a picture), so the process starts quickly.
"""

import base64
import http.client
import json
import re
import sys
import time
import warnings
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urljoin, urlsplit

from clients import doc_fetch as _doc_fetch_untyped

_F: Any = _doc_fetch_untyped
FetchError = cast("type[Exception]", _F.FetchError)

PAGE_BYTES = 1024 * 1024  # a page is read this far; the tags a preview needs are near its top
IMAGE_BYTES = 1024 * 1024  # a larger picture is not kept
REDIRECTS = 3
PORTS = frozenset((80, 443))
PAGE_TYPES = frozenset(("text/html", "application/xhtml+xml"))
IMAGE_TYPES = frozenset(("image/png", "image/jpeg", "image/gif", "image/webp"))
PIXELS_MOST = 16_000_000
SIDE = 480  # a small picture's longest side
TITLE_MOST, SITE_MOST, DESCRIPTION_MOST = 300, 100, 400
# What may go wrong reading a page that is the page's or the network's doing.
UNREADABLE = (FetchError, OSError, http.client.HTTPException, ValueError, UnicodeError)

# Seams for tests: the name lookup, the connection and the clock (clients/doc_fetch.py's own).
resolver: Callable[[str, int], list[str]] = _F.resolve
opener: Callable[..., Any] = _F.connect
clock: Callable[[], float] = time.monotonic


class Read(TypedDict):
    title: str
    site_name: str
    description: str | None
    image_url: str | None
    image: bytes | None


def fetch(url: str, kinds: frozenset[str], seconds: float) -> tuple[str, bytes]:
    """(where the body came from, the body) for `url` within `seconds`: a page (PAGE_TYPES) cut at PAGE_BYTES, else a
    picture, refused past IMAGE_BYTES. FetchError when the address may not be fetched or does not answer as asked."""
    page = kinds == PAGE_TYPES

    def accepts(_self: Any, ctype: str) -> bool:
        return ctype in kinds

    limits = {
        "timeout_s": seconds,
        "max_redirects": REDIRECTS,
        "max_bytes": PAGE_BYTES if page else IMAGE_BYTES,
        "ports": PORTS,
        "accepts": accepts,
    }
    made: Any = type("LinkPreviewFetcher", (_F.Fetcher,), limits)
    fetcher = made(env={}, resolver=resolver, opener=opener, clock=clock)  # env={}: none of the server's credentials
    final, _ctype, body, truncated = fetcher.get(url, ",".join(sorted(kinds)))
    if truncated and not page:
        raise _F.FetchError("too_large", "The picture is larger than a preview keeps")
    return cast("str", final), cast("bytes", body)


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


def bare_host(url: str) -> str:
    host = (urlsplit(url).hostname or "").rstrip(".")
    return host[4:] if host.startswith("www.") else host


def parse(url: str, body: bytes) -> Read | None:
    """What a page says about itself: Open Graph first, then Twitter's tags, then its <title>. None without a title."""
    meta_charset = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", body[:4096], re.I)
    named = meta_charset.group(1).decode("ascii", "replace") if meta_charset else None
    head = _Head()
    head.feed(cast("str", _F.decode(body, "text/html", named)))  # html.parser reads any markup without raising
    head.close()
    meta = head.meta

    def first(*keys: str) -> str | None:
        return next((meta[k] for k in keys if meta.get(k, "").strip()), None)

    title = _text(first("og:title", "twitter:title") or "".join(head.title), TITLE_MOST)
    if not title:
        return None
    image = first("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    image_url = urljoin(url, image.strip()) if image else None
    return {
        "title": title,
        "site_name": _text(first("og:site_name", "application-name"), SITE_MOST) or bare_host(url),
        "description": _text(first("og:description", "twitter:description", "description"), DESCRIPTION_MOST),
        "image_url": image_url if image_url and urlsplit(image_url).scheme in ("http", "https") else None,
        "image": None,
    }


def read(url: str, seconds: float) -> Read | None:
    """The page at `url` and its picture's bytes, both within `seconds`; None when it has no preview or cannot be read.
    Never raises for what the page or the network does."""
    ends = clock() + seconds
    try:
        final, body = fetch(url, PAGE_TYPES, seconds)
        found = parse(final, body)
    except UNREADABLE:
        return None
    left = ends - clock()
    if found and found["image_url"] and left > 0:
        try:
            found["image"] = fetch(found["image_url"], IMAGE_TYPES, left)[1]
        except UNREADABLE:
            pass  # a page whose picture cannot be read is still its title
    return found


def small(source: Path, target: Path) -> tuple[int, int]:
    """`source` made a JPEG of at most SIDE pixels a side at `target`; its (width, height). ValueError when it has more
    than PIXELS_MOST pixels, which is read from its header before anything is decoded."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = PIXELS_MOST
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    try:
        opened = Image.open(source, formats=["PNG", "JPEG", "GIF", "WEBP"])  # reads the header, decodes nothing
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The picture has too many pixels") from exc
    with opened:
        if opened.width * opened.height > PIXELS_MOST:
            raise ValueError("The picture has too many pixels")
        opened.draft("RGB", (SIDE, SIDE))  # a JPEG decodes at the smallest scale still over SIDE
        opened.thumbnail((SIDE, SIDE))
        made = ImageOps.exif_transpose(opened.convert("RGB"))
        made.save(target, "JPEG", quality=82)
        return made.size


def _limit(memory: int) -> None:  # pragma: no cover - runs in the child process, on Linux
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    if sys.platform != "darwin":  # macOS refuses an address-space limit
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def main(argv: list[str]) -> int:  # pragma: no cover - runs in the child process; tests call read and small
    mode, *rest = argv
    if mode == "page":
        _limit(512 * 1024 * 1024)
        found = read(rest[0], float(rest[1]))
        image = found and found["image"]
        shown = {**found, "image": base64.b64encode(image).decode() if image else None} if found else None
        sys.stdout.write(json.dumps(shown))
        return 0
    if mode == "picture":
        _limit(1024 * 1024 * 1024)
        try:
            sys.stdout.write(json.dumps(small(Path(rest[0]), Path(rest[1]))))
        except Exception:  # noqa: BLE001 - any picture that does not decode is no picture; the exit code says so
            return 1
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover - the child process
    raise SystemExit(main(sys.argv[1:]))
