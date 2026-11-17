"""The web UI's routes: its build (build/ui, scripts/build-ui.cjs) and the static folders."""

import hashlib
from .store import Problem
from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
import re
from . import brand_icons
from . import ui_build
from .route_renames import install as install_route_renames


def register(app, auth, settings):
    install_route_renames(app)  # after every route: the new spellings canonical, the old ones deprecated

    # The two icons browsers ask for at the root (backend/brand_icons.py). Before the static mounts below, which would
    # otherwise answer first.
    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"], include_in_schema=False)
    async def brand_root_icon(request: Request):
        rel, media_type = brand_icons.ROOT[request.url.path]
        data = brand_icons.read(settings, rel)
        if data is None:
            raise Problem("not_found", "Not found", 404)
        return brand_icons.reply(data, media_type)

    # Only the frontend directory and its build are served. No project root, runtime DB, or secrets.
    # The page and everything it runs are scripts/build-ui.cjs's output in build/ui (backend/ui_build.py): index.html
    # names the bundle's files under /tico/ui/, each named by its content, so they are kept for a year. The page itself
    # is revalidated on every load. Nothing here builds or checks the build: a server starts only on a current one.
    build_dir = ui_build.out_dir(settings.ui_dir)
    bundle_dir = ui_build.bundle_dir(settings.ui_dir)
    etags = {}

    def file_reply(request, path, media_type, cache_control=None, gz=None):
        """The file at `path` with a strong ETag of its content (read again when it changes), its gzip copy `gz` to a
        browser that accepts it, and a 304 for a matching If-None-Match (a weak form too: Cloudflare sends one)."""
        from .json_response import accepts_gzip

        zipped = gz is not None and gz.is_file() and accepts_gzip(request.headers)
        source = gz if zipped else path
        try:
            stat = source.stat()
        except OSError:
            raise Problem("not_found", "Not found", 404) from None
        raw = source.read_bytes()
        entry = etags.get(source)
        if not entry or entry[0] != (stat.st_mtime_ns, stat.st_size):
            entry = etags[source] = ((stat.st_mtime_ns, stat.st_size), f'"{hashlib.sha256(raw).hexdigest()[:32]}"')
        etag = entry[1]
        headers = {"ETag": etag}
        if cache_control:
            headers["Cache-Control"] = cache_control
        if gz is not None:
            headers["Vary"] = "Accept-Encoding"
        if zipped:
            headers["Content-Encoding"] = "gzip"
        if etag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            return Response(None, media_type=media_type, headers={**headers, "Content-Length": str(len(raw))})
        return Response(raw, media_type=media_type, headers=headers)

    def built(name):
        """build/ui/<name>, or the 503 that names the command when the UI was never built."""
        path = build_dir / name
        if not path.is_file():
            raise Problem("ui_not_built", f"build/ui/{name} is missing: run `{ui_build.COMMAND}`", 503)
        return path

    @app.api_route("/tico/ui/app.bundle.{rest}", methods=["GET", "HEAD"], include_in_schema=False)
    def ui_bundle_file(request: Request, rest: str):
        name = "app.bundle." + rest
        if not ui_build.BUNDLE_NAME.fullmatch(name):
            raise Problem("not_found", "Not found", 404)
        # An older build's file is gone: an open tab from before a release reloads to the current one (ui/app/parts.ts).
        media_type = (
            "application/json"
            if name.endswith(".map")
            else ("text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8")
        )
        return file_reply(request, bundle_dir / name, media_type, ui_build.IMMUTABLE, bundle_dir / f"{name}.gz")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/tico/ui/", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/tico/ui/index.html", methods=["GET", "HEAD"], include_in_schema=False)
    def ui_page(request: Request):
        return file_reply(request, built("index.html"), "text/html; charset=utf-8")

    # A TypeScript file outside the bundle (the overview's modules, which import each other by their .ts names) is
    # served as its compiled JavaScript, and its map beside it under the name the output's last line gives.
    @app.api_route("/tico/ui/{name:path}.ts", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/tico/ui/{name:path}.js.map", methods=["GET", "HEAD"], include_in_schema=False)
    def ui_typescript(request: Request, name: str):
        source = name + ".ts"
        if (
            not re.fullmatch(r"[\w-]+(?:/[\w.-]+)*\.ts", source)
            or ".." in source.split("/")
            or source.endswith(".d.ts")
            or not (settings.ui_dir / source).is_file()
        ):
            raise Problem("not_found", "Not found", 404)
        is_map = request.url.path.endswith(".map")
        path = built(name + (".js.map" if is_map else ".js"))
        return file_reply(request, path, "application/json" if is_map else "text/javascript; charset=utf-8")

    # The service worker, ui/sw.ts compiled to a classic script. Its URL stays /sw.js at the root: ui/app/boot.ts
    # registers /sw.js?v=… with scope /, and an installed worker's update check asks that same URL. The middleware
    # revalidates it on every check (private, no-cache), whatever its ?v=.
    @app.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/sw.js.map", methods=["GET", "HEAD"], include_in_schema=False)
    def ui_service_worker(request: Request):
        is_map = request.url.path.endswith(".map")
        path = built("sw.js.map" if is_map else "sw.js")
        return file_reply(request, path, "application/json" if is_map else "text/javascript; charset=utf-8")

    # three.js is ~790 KB of text and StaticFiles sends it as is; the Overview waits on it. Gzipped once in
    # memory it is ~200 KB.
    three_dir, three_cache = settings.ui_dir / "vendor" / "three", {}

    @app.api_route("/tico/ui/vendor/three/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/vendor/three/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui_three(request: Request, name: str):
        import gzip, hashlib
        from .json_response import accepts_gzip

        path = three_dir / name
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.js", name) or not path.is_file():
            raise Problem("not_found", "Not found", 404)
        stat = path.stat()
        entry = three_cache.get(name)
        if not entry or entry[0] != (stat.st_mtime_ns, stat.st_size):
            raw = path.read_bytes()
            tag = hashlib.sha1(raw).hexdigest()[:20]
            entry = three_cache[name] = ((stat.st_mtime_ns, stat.st_size), raw, gzip.compress(raw, 6, mtime=0), tag)
        _, raw, gz, tag = entry
        zipped = accepts_gzip(request.headers)
        etag = f'"{tag}{"-gz" if zipped else ""}"'
        headers = {"ETag": etag, "Vary": "Accept-Encoding"}
        if zipped:
            headers["Content-Encoding"] = "gzip"
        if etag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(
            (gz if zipped else raw) if request.method == "GET" else None,
            media_type="text/javascript; charset=utf-8",
            headers=headers,
        )

    app.mount("/tico/ui", StaticFiles(directory=settings.ui_dir, html=True), name="ui-prefixed")
    app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    if settings.demo:
        from . import demo

        demo.install(app, auth, settings)
