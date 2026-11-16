"""A stub vendor for the credential delivery suite (runner_support/tests/test_credential_delivery.py): one local HTTP
server that checks a credential on every route, the way a real service does, and records each request.

Routes (GET or POST), each 200 with the credential it was shown echoed back in the body (so a proxied call proves the
proxy scrubs it), else 401:
- `/bearer`: `Authorization: Bearer <key>`
- `/header`: `X-Api-Key: <key>`
- `/query`: `?api_key=<key>`
- `/basic`: `Authorization: Basic base64(<username>:<key>)`
- `/file`: a POST whose body is the expected file's bytes (a client certificate, say)
- `/mcp`: a minimal streamable-HTTP MCP server (initialize, notifications, tools/list, tools/call) behind
  `Authorization: Bearer <key>`
- `/login`: a sign-in form POST (username, password, code), the code an RFC 6238 TOTP of the account's 2FA key

It binds 127.0.0.1 on a free port. `proxy_transport` is what the Tools proxy sends through in these tests: the proxy
builds the call for the tool's public https address as it always does, and only the last hop is turned to this server.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx

HOST = "vendor.acme.example"  # the tool's public name; the proxy tests resolve it to PUBLIC
PUBLIC = ipaddress.ip_address("93.184.215.14")
MCP_TOOL = "acme_lookup"


def totp(key, at=None, step=30, digits=6):
    """RFC 6238 over HMAC-SHA1, written here apart from backend/totp.py so the site checks Tico's code independently."""
    raw = base64.b32decode(key.upper() + "=" * (-len(key) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(raw, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


class Vendor:
    """The stub service. `seen` holds one dict per request: route, method, status, user agent, the Host header, and
    `shown` (what was presented as the credential, kept in this process only so a test can say what was wrong)."""

    def __init__(self, key, username="", file_body=b"", login=None):
        self.key, self.username, self.file_body = key, username, file_body
        self.login = login or {}  # {"username", "password", "totp"}
        self.seen = []
        self.lock = threading.Lock()
        vendor = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                vendor.handle(self, "GET")

            def do_POST(self):
                vendor.handle(self, "POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def calls(self, route, agent=None):
        """The requests to `route`, from clients whose user agent starts with `agent` when given."""
        with self.lock:
            return [s for s in self.seen if s["route"] == route and (agent is None or s["agent"].startswith(agent))]

    # ---- requests

    def handle(self, request, method):
        parts = urlsplit(request.path)
        route = parts.path.rstrip("/") or "/"
        size = int(request.headers.get("content-length") or 0)
        body = request.rfile.read(size) if size else b""
        status, shown, answer, extra, more = 404, "", {"error": "not found"}, {}, {}
        try:
            if route in ("/bearer", "/header", "/query", "/basic"):
                shown = self._presented(route, request.headers, parse_qs(parts.query))
                status = 200 if shown == self._expected(route) else 401
                answer = {"ok": True, "echo": shown} if status == 200 else {"error": "bad credential"}
            elif route == "/file":
                status = 200 if body and body == self.file_body else 401
                answer = {"ok": status == 200}
            elif route == "/mcp":
                status, answer, extra, more = self._mcp(request.headers, method, body)
                shown = more.pop("shown", "")
            elif route == "/login":
                status, answer = self._login(body)
        except Exception as exc:  # a broken request is a 400 here, never a dead server thread
            status, answer = 400, {"error": type(exc).__name__}
        with self.lock:
            self.seen.append(
                {
                    "route": route,
                    "method": method,
                    "status": status,
                    "agent": request.headers.get("user-agent", ""),
                    "host": request.headers.get("host", ""),
                    "shown": shown,
                    **more,
                }
            )
        data = b"" if answer is None else json.dumps(answer).encode()
        request.send_response(status)
        if data:
            request.send_header("Content-Type", "application/json")
        for name, value in extra.items():
            request.send_header(name, value)
        request.send_header("Content-Length", str(len(data)))
        request.end_headers()
        request.wfile.write(data)

    def _expected(self, route):
        return f"{self.username}:{self.key}" if route == "/basic" else self.key

    @staticmethod
    def _presented(route, headers, query):
        auth = headers.get("authorization", "")
        if route == "/bearer":
            return auth[7:] if auth.startswith("Bearer ") else ""
        if route == "/header":
            return headers.get("x-api-key", "")
        if route == "/query":
            return (query.get("api_key") or [""])[0]
        if auth.startswith("Basic "):
            return base64.b64decode(auth[6:]).decode("utf-8", "replace")
        return ""

    def _mcp(self, headers, method, body):
        auth = headers.get("authorization", "")
        shown = auth[7:] if auth.startswith("Bearer ") else ""
        if method != "POST":
            return 405, {"error": "POST only"}, {}, {"shown": shown}
        message = json.loads(body or b"{}")
        rpc = message.get("method", "")
        if shown != self.key:
            return 401, {"error": "bad credential"}, {}, {"shown": shown, "rpc": rpc}
        if "id" not in message:  # a notification
            return 202, None, {}, {"shown": shown, "rpc": rpc}
        if rpc == "initialize":
            version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "acme-vendor", "version": "1"},
            }
            extra = {"Mcp-Session-Id": "acme-session-1"}
        elif rpc == "tools/list":
            result = {
                "tools": [
                    {
                        "name": MCP_TOOL,
                        "description": "Look up an Acme order",
                        "inputSchema": {"type": "object", "properties": {"order": {"type": "string"}}},
                        "annotations": {"readOnlyHint": True},  # a lookup: the proxy lets a Read caller use it
                    }
                ]
            }
            extra = {}
        elif rpc == "tools/call":
            result = {"content": [{"type": "text", "text": "Order 42 shipped"}]}
            extra = {}
        else:
            return (
                200,
                {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "no such method"}},
                {},
                {"shown": shown, "rpc": rpc},
            )
        return 200, {"jsonrpc": "2.0", "id": message["id"], "result": result}, extra, {"shown": shown, "rpc": rpc}

    def _login(self, body):
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        want = self.login
        now = time.time()
        codes = {totp(want["totp"], now + drift) for drift in (-30, 0, 30)} if want.get("totp") else {""}
        ok = (
            form.get("username") == want.get("username")
            and form.get("password") == want.get("password")
            and form.get("code", "") in codes
        )
        return (200, {"ok": True, "signed_in": want.get("username")}) if ok else (401, {"error": "sign-in refused"})

    # ---- the proxy's last hop

    def proxy_transport(self):
        """An httpx transport for `app.state.tool_proxy_transport`: the request the proxy built for
        https://vendor.acme.example/... (pinned to PUBLIC, Host and SNI the name) goes to this server over plain http,
        path, query, headers and body unchanged."""
        return _ToVendor(self.port)


class _ToVendor(httpx.AsyncBaseTransport):
    """A connection of its own per call, read whole: the proxy closes its client (and so this transport) after every
    call, and a later call must still get through."""

    def __init__(self, port):
        self.port = port

    async def handle_async_request(self, request):
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        async with httpx.AsyncHTTPTransport() as inner:
            response = await inner.handle_async_request(request)
            body = await response.aread()
        return httpx.Response(response.status_code, headers=response.headers, content=body)
