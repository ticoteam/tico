# pyright: strict
"""The web app at /: the files its build wrote to build/web (settings.web_dir), and its page for every app address.

The build's files keep their /next/ prefix (web/vite.config.ts `base`). Files under /next/assets/ are named by their
content, so they are kept for a year; every other file and the page are revalidated on each load. Any other /next
address is a link from when the app lived there (a bookmark, an agent's message, an older desktop app): it is sent on
to the same address without /next, its query kept (the browser keeps the fragment). Any other GET that no route or
mount answered is the page itself: the app routes in the browser, and an old app address (`/#/task/12`) opens its page
there (web/src/router.tsx). The server's own prefixes (SERVER) never fall through to the page, so an unknown API path
is still the plain 404. The folder is read on each request, so a build written while the server runs is served at once; with
no build the page is a 404.

`register` goes before any route that could answer /next; `register_page` goes after every other route and mount.
"""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException

from .config import Settings
from .store import Problem
from .ui_bundle_routes import file_reply
from .ui_build import IMMUTABLE

OLD_PREFIX = "/next"
ASSETS_PREFIX = "/next/assets/"
PAGE_TYPE = "text/html; charset=utf-8"
BUILD = "npm ci && npm run build, in web/"
# First path segments the server answers itself: an address under one of them is never the app's page.
SERVER = frozenset(
    {
        "api",
        "auth",
        "mcp",
        "scim",
        "_tico",
        ".well-known",
        "public",
        "visuals",
        "download",
        "healthz",
        "rtc",
        "tico",
        "next",
        "assets",
        "vendor",
        "styles",
    }
)


def page_file(settings: Settings) -> Path:
    """The build's page, or the 404 for a server with no build, naming the command that makes one."""
    page = Path(settings.web_dir).resolve() / "index.html"
    if not page.is_file():
        raise Problem("not_found", f"Not found: the web app is not built ({BUILD})", 404)
    return page


def moved(request: Request) -> str:
    """Where a /next address now is: the same path and query without /next. From the path as sent, so its encoding is
    kept, and never a path a browser would read as another host (`/next//evil.example`, `/next/\\evil.example`)."""
    raw = request.scope.get("raw_path") or request.url.path.encode()
    path = raw.decode("latin-1")[len(OLD_PREFIX) :]
    query = request.url.query
    return "/" + path.lstrip("/\\") + ("?" + query if query else "")


def register(app: FastAPI, settings: Settings) -> None:
    @app.api_route(OLD_PREFIX, methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route(OLD_PREFIX + "/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_file(request: Request, rest: str = "") -> Response:
        root = Path(settings.web_dir).resolve()
        path = (root / rest).resolve()
        if rest and root in path.parents and path.is_file():
            hashed = request.url.path.startswith(ASSETS_PREFIX)
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return file_reply(request, path, media_type, IMMUTABLE if hashed else None)
        if request.url.path.startswith(ASSETS_PREFIX):
            # An older build's file is gone: never the page under a script's name.
            raise Problem("not_found", "Not found", 404)
        return RedirectResponse(moved(request), status_code=308)


def register_page(app: FastAPI, settings: Settings) -> None:
    @app.api_route("/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_page(request: Request, rest: str = "") -> Response:
        if rest.split("/", 1)[0] in SERVER:
            # The plain 404 an address no route answers gets, as before this route existed (the CLI and MCP read it so).
            raise HTTPException(404)
        return file_reply(request, page_file(settings), PAGE_TYPE)
