"""UI bundle and static routes."""

import hashlib
from fastapi.responses import FileResponse
from .store import Problem
from fastapi import Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
import re
from . import brand_icons
from . import ui_build
from . import ui_bundle
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

    # Only the frontend directory is served. No project root, runtime DB, or secrets.
    # The page loads its scripts and styles from /tico/ui/ (ui/index.html), so the same directory is
    # mounted there as well as at the root.
    # A few scripts and stylesheets instead of ~190 files (backend/ui_bundle.py): index.html is served with
    # its bundle regions replaced by the versioned bundle tags. TICO_UI_BUNDLE=off serves the files as listed.
    if ui_bundle.enabled():
        ui_bundles = ui_bundle.shared(settings.ui_dir)

        def ui_reply(request, body, media_type, etag, cache_control=None, gz=None, gz_etag=None):
            headers = {"ETag": etag}
            if cache_control:
                headers["Cache-Control"] = cache_control
            if gz is not None:
                headers["Vary"] = "Accept-Encoding"
                from .json_response import accepts_gzip

                if accepts_gzip(request.headers):
                    body = gz() if callable(gz) else gz
                    etag, headers["ETag"], headers["Content-Encoding"] = gz_etag, gz_etag, "gzip"
            # A weak form of the tag (Cloudflare sends one) matches too: a 304 needs only the same content.
            if etag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
                return Response(status_code=304, headers=headers)
            return Response(body if request.method == "GET" else None, media_type=media_type, headers=headers)

        def current_bundle():
            try:
                return ui_bundles.get()
            except ui_build.StaleBuild as exc:
                raise Problem("ui_not_built", str(exc), 503) from None

        @app.api_route("/tico/ui/app.bundle.{part}", methods=["GET", "HEAD"], include_in_schema=False)
        async def ui_bundle_asset(request: Request, part: str):
            bundle = current_bundle()
            path = request.url.path
            if bundle is not None and path.endswith(".map"):
                # A part's source map (ui_bundle.index_map), only as the part names it; asked for by the browser's
                # tools, never by the page, so it is revalidated rather than kept.
                found = bundle.source_map(path, request.query_params.get("v") or "")
                if found is None:
                    raise Problem("not_found", "Not found", 404)
                body, gz, etag = found
                return ui_reply(request, body, "application/json", etag, None, gz, etag[:-1] + '-gzip"')
            if bundle is None or path not in bundle.parts:
                raise Problem("not_found", "Not found", 404)
            kind = "js" if path.endswith(".js") else "css"
            media_type = "text/javascript; charset=utf-8" if kind == "js" else "text/css; charset=utf-8"
            v = request.query_params.get("v")
            stale = v is not None and v != bundle.version[kind]
            cache_control = "no-store" if stale else ui_bundle.IMMUTABLE
            # A page from before the split names only the first URL: it needs the whole app, without caching it there.
            if stale and path == ui_bundle.part_path(kind, 0):
                etag = bundle.whole_etag[kind]
                return ui_reply(
                    request,
                    getattr(bundle, kind),
                    media_type,
                    etag,
                    cache_control,
                    lambda: bundle.gzip_whole(kind),
                    etag[:-1] + '-gzip"',
                )
            return ui_reply(
                request,
                bundle.parts[path],
                media_type,
                bundle.etag[path],
                cache_control,
                bundle.gz[path],
                bundle.gz_etag[path],
            )

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/tico/ui/", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/tico/ui/index.html", methods=["GET", "HEAD"], include_in_schema=False)
        async def ui_page(request: Request):
            bundle = current_bundle()
            if bundle is None:  # cannot be bundled: the files, as they are
                return FileResponse(settings.ui_dir / "index.html")
            return ui_reply(request, bundle.page, "text/html; charset=utf-8", bundle.page_etag)

    # A file index.html lists as TypeScript is served as its compiled JavaScript, and only while that is current
    # (backend/ui_build.py): the bundle uses it the same way, so this is the separate files' path (TICO_UI_BUNDLE=off).
    def ui_typescript_source(name):
        """ui/<name>.ts as a path relative to ui/, when it is a TypeScript file there; 404 otherwise."""
        name += ".ts"
        if (
            not re.fullmatch(r"[\w-]+(?:/[\w.-]+)*\.ts", name)
            or ".." in name.split("/")
            or name.endswith(".d.ts")
            or not (settings.ui_dir / name).is_file()
        ):
            raise Problem("not_found", "Not found", 404)
        return name

    # A compiled script is sent with a weak ETag of its content, the same whether or not it is gzipped, and a 304 when
    # the browser has it. StaticFiles did this for the .js files before the flip; FileResponse does not.
    compiled_tags = {}

    def compiled_reply(request, path, media_type="text/javascript; charset=utf-8"):
        stat = path.stat()
        entry = compiled_tags.get(path)
        if not entry or entry[0] != (stat.st_mtime_ns, stat.st_size):
            raw = path.read_bytes()
            entry = compiled_tags[path] = (
                (stat.st_mtime_ns, stat.st_size),
                raw,
                'W/"' + hashlib.sha256(raw).hexdigest()[:32] + '"',
            )
        _, raw, etag = entry
        headers = {"ETag": etag, "Vary": "Accept-Encoding"}
        offered = [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]
        if etag.removeprefix("W/") in offered:
            return Response(status_code=304, headers=headers)
        if request.method == "HEAD":
            return Response(None, media_type=media_type, headers={**headers, "Content-Length": str(len(raw))})
        return Response(raw, media_type=media_type, headers=headers)

    @app.api_route("/tico/ui/{name:path}.ts", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui_typescript(request: Request, name: str):
        name = ui_typescript_source(name)
        try:
            path = ui_build.compiled(settings.ui_dir, name)
        except ui_build.StaleBuild as exc:
            raise Problem("ui_not_built", str(exc), 503) from None
        return compiled_reply(request, path)

    # Its source map, which the compiled file's last line names relative to it (app/a.ts -> app/a.js.map), so a stack
    # trace and the developer tools show the .ts. Only for a file compiled from .ts, and only while current.
    @app.api_route("/tico/ui/{name:path}.js.map", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui_typescript_map(request: Request, name: str):
        name = ui_typescript_source(name)
        try:
            path = ui_build.source_map(settings.ui_dir, name)
        except ui_build.StaleBuild as exc:
            raise Problem("ui_not_built", str(exc), 503) from None
        return FileResponse(path, media_type="application/json")

    # The service worker, ui/sw.ts compiled to a classic script. Its URL stays /sw.js at the root: ui/app/boot.ts
    # registers /sw.js?v=… with scope /, and an installed worker's update check asks that same URL. Both bundle modes
    # serve it, and only while its compiled output is current: a stale build is the 503 above, never the .ts source or
    # the static mounts below. The middleware revalidates it on every check (private, no-cache), whatever its ?v=.
    @app.api_route("/sw.js", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui_service_worker(request: Request):
        try:
            path = ui_build.compiled(settings.ui_dir, "sw.ts")
        except ui_build.StaleBuild as exc:
            raise Problem("ui_not_built", str(exc), 503) from None
        return compiled_reply(request, path)

    # Its source map, which the compiled file's last line names relative to it (sw.js.map). Answered like sw.js: an
    # ETag of its content, and a 304 for a matching If-None-Match.
    @app.api_route("/sw.js.map", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui_service_worker_map(request: Request):
        try:
            path = ui_build.source_map(settings.ui_dir, "sw.ts")
        except ui_build.StaleBuild as exc:
            raise Problem("ui_not_built", str(exc), 503) from None
        return compiled_reply(request, path, "application/json")

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
