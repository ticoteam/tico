"""Tico's icons for the server's own pages.

ui/index.html links the app's icons under /assets/ (the files scripts/build-brand-icons.sh makes in ui/assets). The
server's other pages link those same files, and two root paths browsers ask for on their own (/favicon.ico and
/apple-touch-icon.png) hold the same PNG bytes. Nothing is copied or redrawn: the bytes are read from ui/assets.
"""

from pathlib import Path

from starlette.responses import Response

# The Tico mark, for every page of the server that is not the company page (backend/company_page/render.py).
HEAD = (
    '<link rel="icon" href="/assets/tico/tico-mark.svg" type="image/svg+xml">'
    '<link rel="icon" href="/assets/favicon-32.png" type="image/png" sizes="32x32">'
    '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">'
    '<meta name="theme-color" content="#131413" media="(prefers-color-scheme: dark)">'
    '<meta name="theme-color" content="#fbfbf8" media="(prefers-color-scheme: light)">'
)

# The two root paths, as a file of ui/assets and its media type. Both hosts answer them: the hub with the Tico mark,
# the reader host with the team's logo when one is set (backend/company_page/reader_app.py).
ROOT = {
    "/favicon.ico": ("favicon-32.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
# The brand files a page links under /assets/, as a file of ui/assets and its media type. Only these are public.
FILES = {
    "/assets/tico/tico-mark.svg": ("tico/tico-mark.svg", "image/svg+xml"),
    "/assets/favicon-32.png": ("favicon-32.png", "image/png"),
    "/assets/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
PUBLIC_PATHS = frozenset(ROOT) | frozenset(FILES)
CACHE = "public, max-age=86400"
# A brand file is only ever shown as an image. Served as a document, it gets no script and no framing.
HEADERS = {
    "Cache-Control": CACHE,
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
}


def public(path):
    """Whether `path` is one of the icons a signed-out browser may fetch."""
    return path in PUBLIC_PATHS


def read(settings, rel):
    """A file of ui/assets as bytes, or None when it is not there."""
    try:
        return (Path(settings.ui_dir) / "assets" / rel).read_bytes()
    except OSError:
        return None


def harden(response):
    """A brand file's response with its own headers: a file that is there is cached for a day, and nothing is served as
    a script document."""
    for name, value in HEADERS.items():
        if name != "Cache-Control" or response.status_code == 200:
            response.headers[name] = value
    return response


def reply(data, media_type):
    return Response(data, media_type=media_type, headers=HEADERS)
