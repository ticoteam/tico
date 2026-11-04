"""Compress the standalone UI scripts and styles; leave API streams and files to their routes."""
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware

from .json_response import accepts_gzip
from .ui_bundle import is_part


class UiCompression:
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=3)

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "GET"
                or not scope["path"].startswith("/tico/ui/") or is_part(scope["path"])):
            return await self.app(scope, receive, send)

        async def encoded_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.add_vary_header("Accept-Encoding")
                # StaticFiles' ETag describes the source; a weak tag is valid for either wire encoding.
                if headers.get("content-encoding") == "gzip" and headers.get("etag", "").startswith('"'):
                    headers["ETag"] = "W/" + headers["etag"]
            await send(message)

        if accepts_gzip(Headers(scope=scope)):
            # The middleware's substring check does not understand q=0 or the wildcard encoding.
            encoded = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k.lower() != b"accept-encoding"]
                       + [(b"accept-encoding", b"gzip")]}
            await self.compressed(encoded, receive, encoded_send)
        else:
            await self.app(scope, receive, encoded_send)
