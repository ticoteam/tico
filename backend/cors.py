"""Opt-in cross-origin access for a company's own browser frontend (TICO_CORS_ORIGINS).

The list holds exact origins ("https://app.acme.com", "http://localhost:5173"): no wildcard, no
path, no "null". An origin on it may call the API from the browser with credentials, and may be
the `next` of a sign-in (backend/oidc.py), which hands it a short-lived bearer session. Unset,
nothing changes: the API answers no CORS header at all. docs/custom-frontend.md is the guide.
"""

import re
from urllib.parse import urlparse

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse

LOOPBACK = ("localhost", "127.0.0.1", "[::1]")
ORIGIN = re.compile(r"(https?)://([a-z0-9]([a-z0-9.-]*[a-z0-9])?|\[::1\])(:[0-9]{1,5})?")
# Idempotency-Key is required on every write; Last-Event-ID lets a client resume a stream.
HEADERS = ["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"]
METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
EXPOSED = ["Server-Timing"]


def parse(value):
    """A tuple of validated origins from a comma-separated string (or an existing sequence)."""
    parts = value.split(",") if isinstance(value, str) else list(value or ())
    origins = []
    for part in parts:
        origin = str(part).strip().lower()
        if not origin:
            continue
        match = ORIGIN.fullmatch(origin)
        if not match or "*" in origin:
            raise RuntimeError("TICO_CORS_ORIGINS holds exact origins such as https://app.acme.com "
                               "(scheme, host, optional port; no path, no wildcard): got " + part.strip())
        if match.group(1) == "http" and (urlparse(origin).hostname or "") not in ("localhost", "127.0.0.1", "::1"):
            raise RuntimeError("TICO_CORS_ORIGINS allows http only for localhost, 127.0.0.1 and [::1]; "
                               "use https for " + origin)
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


class Cors:
    """ASGI middleware for the allowlist. Starlette's CORSMiddleware also answers credential and
    expose headers to origins it refuses; this adds nothing at all for them, and never `*`."""

    def __init__(self, app, origins):
        self.app, self.origins = app, frozenset(origins)
        self.headers = {h.lower() for h in HEADERS}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith("/public/roadmaps/"):
            return await self.app(scope, receive, send)
        request = Headers(scope=scope)
        origin = request.get("origin")
        allowed = origin is not None and origin in self.origins
        if allowed and scope["method"] == "OPTIONS" and "access-control-request-method" in request:
            asked = {h.strip().lower() for h in request.get("access-control-request-headers", "").split(",") if h.strip()}
            if request["access-control-request-method"] not in METHODS or not asked <= self.headers:
                return await PlainTextResponse("Disallowed CORS request", status_code=400)(scope, receive, send)
            reply = PlainTextResponse("OK", headers={
                "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": ", ".join(METHODS), "Access-Control-Allow-Headers": ", ".join(HEADERS),
                "Access-Control-Max-Age": "600", "Vary": "Origin"})
            return await reply(scope, receive, send)

        async def add(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.append("Vary", "Origin")
                if allowed:
                    headers["Access-Control-Allow-Origin"] = origin
                    headers["Access-Control-Allow-Credentials"] = "true"
                    headers["Access-Control-Expose-Headers"] = ", ".join(EXPOSED)
            await send(message)
        await self.app(scope, receive, add)


def install(app, settings):
    """Add the middleware, for the allowlist only. Added last, so it wraps the sign-in check: a
    preflight carries no credentials and must be answered before it."""
    if settings.cors_origins:
        app.add_middleware(Cors, origins=settings.cors_origins)
