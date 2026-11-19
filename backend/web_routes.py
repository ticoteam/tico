"""The new web app at /next: the files its build wrote to build/web (settings.web_dir), beside today's UI.

Registered before backend/ui_bundle_routes.py, whose mount at / would otherwise answer every /next path. Files under
/next/assets/ are named by their content, so they are kept for a year; every other file and the page are revalidated
on each load. Any other /next path is the page itself: the app routes in the browser. The folder is read on each
request, so a build written while the server runs is served at once, and with no build /next is a plain 404.
"""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response

from .config import Settings
from .store import Problem
from .ui_bundle_routes import file_reply
from .ui_build import IMMUTABLE

ASSETS_PREFIX = "/next/assets/"
PAGE_TYPE = "text/html; charset=utf-8"


def register(app: FastAPI, settings: Settings) -> None:
    @app.api_route("/next", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/next/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_app(request: Request, rest: str = "") -> Response:
        root = Path(settings.web_dir).resolve()
        page = root / "index.html"
        if not page.is_file():
            raise Problem("not_found", "Not found", 404)
        path = (root / rest).resolve()
        if rest and root in path.parents and path.is_file():
            hashed = request.url.path.startswith(ASSETS_PREFIX)
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return file_reply(request, path, media_type, IMMUTABLE if hashed else None)
        if request.url.path.startswith(ASSETS_PREFIX):
            # An older build's file is gone: never the page under a script's name.
            raise Problem("not_found", "Not found", 404)
        return file_reply(request, page, PAGE_TYPE)
