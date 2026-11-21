"""clients/tool_call.py, the bot side of a proxied tool call: what it sends."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from clients import tool_call


def token_for(tool):
    claims = base64.urlsafe_b64encode(json.dumps({"t": tool}).encode()).decode().rstrip("=")
    return tool_call.TOKEN_PREFIX + claims + ".sig"


def test_call_names_itself_so_cloudflare_lets_it_through():
    # Cloudflare in front of Tico answers Python's default User-Agent with 403 error 1010 before the proxy runs.
    seen = {}

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.update(path=self.path, agent=self.headers.get("User-Agent"), auth=self.headers.get("Authorization"))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        token = token_for("sentry")
        environ = {
            "TICO_TOOL_SENTRY_URL": f"http://127.0.0.1:{server.server_port}/api/v2/tool-proxy/sentry",
            "SENTRY_READ_TOKEN": token,
        }
        out = tool_call.call("sentry", "GET", "/api/0/projects/", query=["query=is:unresolved"], environ=environ)
    finally:
        server.shutdown()
    assert out == {"status": 200, "content_type": "application/json", "body": {"ok": True}}
    assert seen["path"] == "/api/v2/tool-proxy/sentry/api/0/projects/?query=is%3Aunresolved"
    assert seen["auth"] == "Bearer " + token
    assert seen["agent"] == tool_call.USER_AGENT
    assert not seen["agent"].startswith("Python-urllib")


def test_a_write_carries_one_idempotency_key_and_a_read_none():
    """The proxy checks a write against the org rules only with an Idempotency-Key (backend/rule_links.py)."""
    seen = []

    class Proxy(BaseHTTPRequestHandler):
        def answer(self):
            seen.append(self.headers.get("Idempotency-Key"))
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = answer

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        environ = {
            "TICO_TOOL_CLOSE_URL": f"http://127.0.0.1:{server.server_port}/api/v2/tool-proxy/close",
            "CLOSE_KEY": token_for("close"),
        }
        tool_call.call("close", "GET", "/leads", environ=environ)
        tool_call.call("close", "POST", "/leads", {"name": "Acme"}, environ=environ)
        tool_call.call("close", "POST", "/leads", {"name": "Acme"}, environ=environ, idempotency_key="op-7")
    finally:
        server.shutdown()
    assert seen[0] is None and seen[1].startswith("tico-tool-call-") and seen[2] == "op-7"
