"""The local website audit refuses an unsafe destination before opening a socket to it."""

import asyncio
import email.message
import json
import runpy
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

AUDIT = Path(__file__).resolve().parents[2] / "templates/catalog/_shared/.claude/skills/audit-site/audit.py"
audit = SimpleNamespace(**runpy.run_path(str(AUDIT)))


def test_redirect_is_checked_before_another_request(monkeypatch):
    requested = []

    def redirect(request, **_options):
        requested.append(request.full_url)
        headers = email.message.Message()
        headers["Location"] = "http://127.0.0.1/"
        raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)

    monkeypatch.setattr(audit._OPENER, "open", redirect)
    fence = audit.Fence("acme.example")
    with pytest.raises(audit.Refused, match="public DNS name"):
        audit.check_site("https://acme.example", fence, audit.time.monotonic() + 1)
    assert requested == ["https://acme.example"]


@pytest.mark.parametrize(
    ("request_line", "reason"),
    [
        (b"CONNECT acme.example.other.example:443 HTTP/1.1", b"outside the owned domain"),
        (b"CONNECT router.acme.example:443 HTTP/1.1", b"which is not a public address"),
        (b"GET http://router.acme.example/ HTTP/1.1", b"which is not a public address"),
    ],
)
def test_proxy_blocks_off_domain_and_mixed_public_private_dns(monkeypatch, request_line, reason):
    class Response(bytearray):
        write = bytearray.extend

        async def drain(self):
            pass

    def resolve(host, *_args, **_kwargs):
        assert host == "router.acme.example"
        return [(0, 0, 0, "", ("93.184.215.14", 443)), (0, 0, 0, "", ("169.254.169.254", 443))]

    async def connect(*_args, **_kwargs):
        pytest.fail("the proxy attempted to connect to a refused host")

    async def relay():
        reader = asyncio.StreamReader()
        reader.feed_data(request_line + b"\r\n\r\n")
        response = Response()
        await audit.Fence("acme.example")._relay(reader, response)
        return response

    monkeypatch.setattr(audit.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(audit.asyncio, "open_connection", connect)
    response = asyncio.run(relay())
    assert response.startswith(b"HTTP/1.1 403")
    assert reason in response


def test_partial_report_counts_missing_scores_and_keeps_genuine_console_errors(tmp_path):
    messages = [{"source": "network", "description": "Failed to load resource: net::ERR_TUNNEL_CONNECTION_FAILED"}]
    report = {
        "requestedUrl": "https://acme.example/about",
        "categories": {
            "best-practices": {
                "score": 0.9,
                "auditRefs": [{"id": "errors-in-console", "weight": 1}, {"id": "doctype", "weight": 1}],
            },
        },
        "audits": {
            "errors-in-console": {"score": 0, "title": "Check browser errors", "details": {"items": messages}},
            "doctype": {"score": 0, "title": "Add a doctype"},
        },
    }

    def write_route(folder, raw):
        path = tmp_path / "out" / folder
        path.mkdir(parents=True)
        (path / "lighthouse.json").write_text(raw)

    write_route("only-blocked-assets", json.dumps(report))
    messages.append({"source": "javascript", "description": "Uncaught TypeError"})
    write_route("real-error", json.dumps(report))
    write_route("truncated", "{")
    write_route("no-data", "null")
    (tmp_path / "started-routes").write_bytes(b"\n" * 5)

    pages, unscored = audit.read_pages(tmp_path, max_routes=5, blocked_connections=1)

    assert unscored == 3
    assert audit.top_fixes(pages) == [
        ("Add a doctype", "best-practices", 2),
        ("Check browser errors", "best-practices", 1),
    ]
