# pyright: strict
"""Tico's icons: the files in backend/assets (scripts/build-brand-icons.sh makes the PNGs from the SVGs in
backend/assets/tico), served under /assets/.

The server's own pages (sign-in, Connecting GitHub, the company page) link them, the web manifest names the app icons,
a push shows icon-192.png, and two root paths browsers ask for on their own (/favicon.ico and /apple-touch-icon.png)
hold the same PNG bytes. Nothing is copied or redrawn: the bytes are read from backend/assets.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from .store import Problem

ASSETS = Path(__file__).resolve().parent / "assets"

# The Tico mark, for every page of the server that is not the company page (backend/company_page/render.py).
HEAD = (
    '<link rel="icon" href="/assets/tico/tico-mark.svg" type="image/svg+xml">'
    '<link rel="icon" href="/assets/favicon-32.png" type="image/png" sizes="32x32">'
    '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">'
    '<meta name="theme-color" content="#131413" media="(prefers-color-scheme: dark)">'
    '<meta name="theme-color" content="#fbfbf8" media="(prefers-color-scheme: light)">'
)

# The two root paths, as a file of backend/assets and its media type. Both hosts answer them: the hub with the Tico mark,
# the reader host with the team's logo when one is set (backend/company_page/reader_app.py).
ROOT: dict[str, tuple[str, str]] = {
    "/favicon.ico": ("favicon-32.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
# The brand files a page links under /assets/, as a file of backend/assets and its media type. Only these are public.
FILES: dict[str, tuple[str, str]] = {
    "/assets/tico/tico-mark.svg": ("tico/tico-mark.svg", "image/svg+xml"),
    "/assets/favicon-32.png": ("favicon-32.png", "image/png"),
    "/assets/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
PUBLIC_PATHS: frozenset[str] = frozenset(ROOT) | frozenset(FILES)
CACHE = "public, max-age=86400"
# A team's logo can change, so the reader host keeps the copy it serves for an hour rather than a day.
TEAM_CACHE = "public, max-age=3600"
# A brand file is only ever shown as an image. Served as a document, it gets no script and no framing.
HEADERS: dict[str, str] = {
    "Cache-Control": CACHE,
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}


def public(path: str) -> bool:
    """Whether `path` is one of the icons a signed-out browser may fetch."""
    return path in PUBLIC_PATHS


def read(rel: str) -> bytes | None:
    """A file of backend/assets as bytes, or None when it is not there."""
    try:
        return (ASSETS / rel).read_bytes()
    except OSError:
        return None


def harden(response: Response) -> Response:
    """A brand file's response with its own headers: a file that is there is cached for a day, and nothing is served as
    a script document."""
    for name, value in HEADERS.items():
        if name != "Cache-Control" or response.status_code == 200:
            response.headers[name] = value
    return response


def reply(data: bytes, media_type: str, cache: str = CACHE) -> Response:
    return Response(data, media_type=media_type, headers={**HEADERS, "Cache-Control": cache})


def register(app: FastAPI) -> None:
    """The two root icons, and every file of backend/assets under /assets/ (signed in, but for PUBLIC_PATHS)."""

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"], include_in_schema=False)
    def brand_root_icon(request: Request) -> Response:
        rel, media_type = ROOT[request.url.path]
        data = read(rel)
        if data is None:
            raise Problem("not_found", "Not found", 404)
        return reply(data, media_type)

    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
