"""Compress API JSON without buffering live streams or changing downloaded file bytes."""
import asyncio
import gzip

from fastapi.responses import JSONResponse

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


async def compress(request, response):
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
