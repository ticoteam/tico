"""Cross-origin access for the routes the tico.team pages call from a visitor's browser, and for nothing else.

    allow(app, {"/v1/support": ("POST",)})

A listed path answers the browser's preflight (`OPTIONS` with `Access-Control-Request-Method`) and carries
`Access-Control-Allow-Origin` on its answers, only when the request's Origin is exactly `ORIGIN`. Any other origin gets
the same answers with no allow header, so its browser refuses to read them. Paths not listed get nothing. Credentials
are never allowed: the pages send no cookies, and HQ sets none.
"""
from fastapi import Request
from fastapi.responses import Response

ORIGIN = "https://tico.team"
HEADERS = "Content-Type"
MAX_AGE = "600"


def allow(app, paths, origin=ORIGIN):
    """Add the middleware. `paths` maps an exact path to the methods a browser at `origin` may call on it."""
    allowed = {path: tuple(m.upper() for m in methods) for path, methods in paths.items()}

    @app.middleware("http")
    async def cors(request: Request, call_next):
        methods = allowed.get(request.url.path)
        if methods is None:
            return await call_next(request)
        mine = request.headers.get("origin") == origin
        if request.method == "OPTIONS" and "access-control-request-method" in request.headers:
            response = Response(status_code=204, headers={"Vary": "Origin", "Cache-Control": "no-store"})
            if mine and request.headers["access-control-request-method"].upper() in methods:
                response.headers.update({
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": ", ".join(methods + ("OPTIONS",)),
                    "Access-Control-Allow-Headers": HEADERS,
                    "Access-Control-Max-Age": MAX_AGE})
            return response
        response = await call_next(request)
        response.headers["Vary"] = "Origin"
        if mine:
            response.headers["Access-Control-Allow-Origin"] = origin
        return response
