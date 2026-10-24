"""API JSON compression: the encoding sent is one the client accepts, and the body decodes back."""
import asyncio
import gzip
import json
import types

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from backend import json_response

PAYLOAD = {"rows": [{"id": i, "title": f"task {i}"} for i in range(200)]}


def _send(accept_encoding, *extra):
    request = Request({"type": "http", "method": "GET",
                       "headers": [(b"accept-encoding", accept_encoding.encode()), *extra]})
    return asyncio.run(json_response.compress(request, JSONResponse(PAYLOAD)))


def test_negotiation_respects_what_the_client_accepts(monkeypatch):
    # A stand-in codec, so Python 3.12 (no compression.zstd) checks the zstd choice too.
    monkeypatch.setattr(json_response, "zstd", types.SimpleNamespace(compress=lambda data, level: b"Z" + data[:600]))
    cases = {"gzip, deflate, br, zstd": "zstd", "zstd": "zstd", "gzip": "gzip", "*": "gzip",
             "zstd;q=0, gzip": "gzip", "zstd;q=0.5, gzip": "gzip", "gzip;q=0.5, zstd": "zstd", "identity": None}
    for accept, expected in cases.items():
        response = _send(accept)
        assert response.headers.get("content-encoding") == expected, accept
        assert response.headers["content-length"] == str(len(response.body))
        assert response.headers["vary"] == "Accept-Encoding"
    assert json.loads(gzip.decompress(_send("gzip").body)) == PAYLOAD
    assert "content-encoding" not in _send("zstd, gzip", (b"cf-ray", b"x")).headers
    # Without the codec (Python 3.12) a zstd-capable client gets gzip.
    monkeypatch.setattr(json_response, "zstd", None)
    assert _send("gzip, deflate, br, zstd").headers["content-encoding"] == "gzip"


def test_zstd_body_decodes_back_on_python_3_14():
    zstd = pytest.importorskip("compression.zstd")
    response = _send("gzip, zstd")
    assert response.headers["content-encoding"] == "zstd"
    assert json.loads(zstd.decompress(response.body)) == PAYLOAD


def test_a_polled_read_unchanged_is_a_304_without_its_body():
    def ask(path, *headers):
        request = Request({"type": "http", "method": "GET", "path": path, "query_string": b"",
                           "headers": [(b"accept-encoding", b"gzip"), *headers]})
        return asyncio.run(json_response.compress(request, JSONResponse(PAYLOAD)))
    first = ask("/api/employees")
    tag = first.headers["etag"]
    assert first.status_code == 200 and json.loads(gzip.decompress(first.body)) == PAYLOAD
    again = ask("/api/employees", (b"if-none-match", ("W/" + tag).encode()))   # Cloudflare weakens a compressed tag
    assert again.status_code == 304 and again.body == b"" and again.headers["etag"] == tag
    assert ask("/api/employees", (b"if-none-match", b'"other"')).status_code == 200
    assert "etag" not in ask("/api/v2/tasks").headers          # a path that tags itself, or is not polled, is left alone
