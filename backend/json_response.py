"""Compress API JSON without buffering live streams or changing downloaded file bytes."""
import asyncio
import gzip
import hashlib

from fastapi.responses import JSONResponse, Response

try:  # Python 3.14+. zstd level 1 is 8-14x faster than gzip level 3 on API JSON and as small; 3.12 keeps gzip.
    from compression import zstd
except ImportError:
    zstd = None


def accepted_encodings(headers):
    accepted = {}
    for item in headers.get("accept-encoding", "").lower().split(","):
        name, *parameters = item.strip().split(";")
        quality = 1.0
        try:
            for parameter in parameters:
                key, _, value = parameter.strip().partition("=")
                if key == "q":
                    quality = float(value)
        except ValueError:
            quality = 0.0
        accepted[name] = quality
    return accepted


def accepts_gzip(headers):
    accepted = accepted_encodings(headers)
    return 0 < accepted.get("gzip", accepted.get("*", 0)) <= 1


def _zstd(data):
    return zstd.compress(data, 1)


def _gzip(data):
    return gzip.compress(data, compresslevel=3, mtime=0)


# The reads every open page repeats on its refresh (ui/app/refresh.js, health.js, notices.js) and that carry no tag of
# their own. Their answer is tagged by its bytes: one asked again with that tag and unchanged is a 304 with no body,
# and the page reads its kept copy (ui/app/api.js). Only a few paths: the page keeps a few dozen tagged answers.
TAGGED = frozenset({"/api/employees", "/api/people", "/api/humans", "/api/status", "/api/issues", "/api/v2/status",
                    "/api/v2/task-types", "/api/v2/health", "/api/v2/config"})


def unchanged(request, response):
    if (request.method != "GET" or not isinstance(response, JSONResponse) or response.status_code != 200
            or "etag" in response.headers or request.scope.get("path") not in TAGGED):
        return response
    tag = '"j' + hashlib.blake2b(response.body, digest_size=12).hexdigest() + '"'
    if tag in [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers={"ETag": tag})
    response.headers["ETag"] = tag
    return response


async def compress(request, response):
    response = unchanged(request, response)
    # Behind Cloudflare (cf-ray) the edge compresses for the browser; doing it here too only spends this server's CPU.
    if (not isinstance(response, JSONResponse) or request.method == "HEAD" or "cf-ray" in request.headers
            or response.status_code != 200 or len(response.body) < 1024
            or "content-encoding" in response.headers
            or "content-disposition" in response.headers or "x-content-sha256" in response.headers):
        return response
    response.headers.add_vary_header("Accept-Encoding")
    accepted = accepted_encodings(request.headers)
    gzip_q, zstd_q = accepted.get("gzip", accepted.get("*", 0)), accepted.get("zstd", 0)
    # zstd only when named (not via *), and not when the client ranks gzip above it.
    if zstd and 0 < zstd_q <= 1 and not (gzip_q <= 1 and zstd_q < gzip_q):
        encoding, encode = "zstd", _zstd
    elif 0 < gzip_q <= 1:
        encoding, encode = "gzip", _gzip
    else:
        return response
    # Large task pages must not hold up the event loop's other requests and live events.
    if len(response.body) >= 128 * 1024:
        body = await asyncio.to_thread(encode, response.body)
    else:
        body = encode(response.body)
    if len(body) < len(response.body):
        response.body = body
        response.headers["Content-Encoding"] = encoding
        response.headers["Content-Length"] = str(len(body))
    return response
