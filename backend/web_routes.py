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

The diagram frame (web/mermaid-sandbox.html, drawn by web/src/kit/Markdown/diagram.ts) is sandboxed with no origin of
its own, so it sends no cookies: its page and the build files it loads (listed by the build's manifest) are sent
without sign-in, as the build's own static files, the page under a policy that runs only those scripts and reaches no
network, and the scripts with `Access-Control-Allow-Origin: *`, since a module script from an opaque origin is a CORS
request.

The service worker browsers installed for this origin (scope /) asks /sw.js for its updates: it is the build's
sw.js (web/public/sw.js), which shows Web Push.

`register` goes before any route that could answer /next; `register_page` goes after every other route and mount.
"""

import hashlib
import json
import mimetypes
from typing import cast
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException

from .config import Settings
from .store import Problem

OLD_PREFIX = "/next"
ASSETS_PREFIX = "/next/assets/"
PAGE_TYPE = "text/html; charset=utf-8"
IMMUTABLE = "public, max-age=31536000, immutable"
BUILD = "npm ci && npm run build, in web/"
SANDBOX_PAGE = "mermaid-sandbox.html"
# Mermaid's own <style> runs; nothing is fetched, framed, posted or based anywhere.
SANDBOX_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts"
)
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


# Each sent file's ETag, by path, while its size and modified time stay the same.
_ETAGS: dict[Path, tuple[tuple[int, int], str]] = {}


def file_reply(request: Request, path: Path, media_type: str, cache_control: str | None = None) -> Response:
    """The file at `path` with a strong ETag of its content (read again when it changes), and a 304 for a matching
    If-None-Match (a weak form too: Cloudflare sends one)."""
    source = path
    try:
        stat = source.stat()
    except OSError:
        raise Problem("not_found", "Not found", 404) from None
    raw = source.read_bytes()
    entry = _ETAGS.get(source)
    if not entry or entry[0] != (stat.st_mtime_ns, stat.st_size):
        entry = _ETAGS[source] = ((stat.st_mtime_ns, stat.st_size), f'"{hashlib.sha256(raw).hexdigest()[:32]}"')
    etag = entry[1]
    headers = {"ETag": etag}
    if cache_control:
        headers["Cache-Control"] = cache_control
    if etag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    if request.method == "HEAD":
        return Response(None, media_type=media_type, headers={**headers, "Content-Length": str(len(raw))})
    return Response(raw, media_type=media_type, headers=headers)


# The diagram frame's files, by manifest, while its size and modified time stay the same.
_SANDBOX: dict[Path, tuple[tuple[int, int], frozenset[str]]] = {}


def _strings(value: object) -> list[str]:
    return [v for v in cast(list[object], value) if isinstance(v, str)] if isinstance(value, list) else []


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def sandbox_files(root: Path) -> frozenset[str]:
    """The diagram frame's page and every file it loads (its chunks, Mermaid's lazy ones, their styles and assets), as
    paths under the build, read from the build's manifest; none without one or without the page in it."""
    manifest = root / ".vite" / "manifest.json"
    try:
        stat = manifest.stat()
    except OSError:
        return frozenset()
    key = (stat.st_mtime_ns, stat.st_size)
    entry = _SANDBOX.get(manifest)
    if entry and entry[0] == key:
        return entry[1]
    try:
        chunks = _object(json.loads(manifest.read_bytes()))
    except (OSError, ValueError):
        chunks = {}
    files: set[str] = set()
    seen: set[str] = set()
    todo = [SANDBOX_PAGE]
    while todo:
        name = todo.pop()
        chunk = _object(chunks.get(name))
        if name in seen or not chunk:
            continue
        seen.add(name)
        file = chunk.get("file")
        if isinstance(file, str):
            files.add(file)
        files.update(_strings(chunk.get("css")) + _strings(chunk.get("assets")))
        todo += _strings(chunk.get("imports")) + _strings(chunk.get("dynamicImports"))
    found: frozenset[str] = frozenset(files | {SANDBOX_PAGE}) if SANDBOX_PAGE in seen else frozenset()
    _SANDBOX[manifest] = (key, found)
    return found


# The build's name, by manifest, while its size and modified time stay the same.
_BUILDS: dict[Path, tuple[tuple[int, int], str]] = {}


def build_name(settings: Settings) -> str:
    """The build served, named by its page's entry script (`/next/assets/index-<hash>.js`, from the manifest): the live
    stream's `ready` carries it, so a tab whose page loaded another build makes its next page change a full load
    (web/src/lib/build.ts). Empty without a build."""
    manifest = Path(settings.web_dir).resolve() / ".vite" / "manifest.json"
    try:
        stat = manifest.stat()
    except OSError:
        return ""
    key = (stat.st_mtime_ns, stat.st_size)
    entry = _BUILDS.get(manifest)
    if entry and entry[0] == key:
        return entry[1]
    try:
        file = _object(_object(json.loads(manifest.read_bytes())).get("index.html")).get("file")
    except (OSError, ValueError):
        file = None
    name = OLD_PREFIX + "/" + file if isinstance(file, str) and file else ""
    _BUILDS[manifest] = (key, name)
    return name


def sandboxed(settings: Settings, path: str) -> bool:
    """Whether `path` (as requested) is one of the diagram frame's files, which pass without sign-in."""
    prefix = OLD_PREFIX + "/"
    return path.startswith(prefix) and path[len(prefix) :] in sandbox_files(Path(settings.web_dir).resolve())


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
            if rest not in sandbox_files(root):
                return file_reply(request, path, media_type, IMMUTABLE if hashed else None)
            # Sent past the app's own middleware headers (backend/app.py request_guard), so it sets its own.
            reply = file_reply(request, path, media_type, IMMUTABLE if hashed else "no-cache")
            reply.headers["X-Content-Type-Options"] = "nosniff"
            reply.headers["Referrer-Policy"] = "no-referrer"
            if rest == SANDBOX_PAGE:
                reply.headers["Content-Security-Policy"] = SANDBOX_POLICY
            else:
                reply.headers["Access-Control-Allow-Origin"] = "*"
            return reply
        if request.url.path.startswith(ASSETS_PREFIX):
            # An older build's file is gone: never the page under a script's name.
            raise Problem("not_found", "Not found", 404)
        return RedirectResponse(moved(request), status_code=308)

    @app.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
    def service_worker(request: Request) -> Response:
        # Revalidated on every update check (the middleware's private, no-cache), whatever its ?v=.
        worker = Path(settings.web_dir).resolve() / "sw.js"
        if not worker.is_file():
            raise Problem("not_found", "Not found", 404)
        return file_reply(request, worker, "text/javascript; charset=utf-8")


def register_page(app: FastAPI, settings: Settings) -> None:
    @app.api_route("/{rest:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web_page(request: Request, rest: str = "") -> Response:
        if rest.split("/", 1)[0] in SERVER:
            # The plain 404 an address no route answers gets, as before this route existed (the CLI and MCP read it so).
            raise HTTPException(404)
        return file_reply(request, page_file(settings), PAGE_TYPE)
