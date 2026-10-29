#!/usr/bin/env python3
"""The forward proxy for scripts/browser-scenarios.sh: the bot's Chrome sends its traffic through it, so the run can see
which hosts Chrome reached. It carries traffic only to the practice site's names (*.practice.test) and refuses every
other host, so no real site is reached from a test run. Stdlib only.

The log holds one line per request: method, host and status. Paths, headers and bodies are never logged.
"""
import http.client
import http.server
import os
import socket
import sys
import threading
import urllib.parse

PORT = int(os.environ.get("PROXY_PORT", "3128"))
SUFFIX = ".practice.test"
HOP_BY_HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade"}


def allowed(host):
    host = (host or "").lower().rstrip(".")
    return host == SUFFIX[1:] or host.endswith(SUFFIX)


def log(method, host, status):
    print(f"{method} {host} {status}", file=sys.stderr, flush=True)


def pump(source, target):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
    except OSError:
        pass
    finally:
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PracticeProxy"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # hosts and statuses only: see log()
        pass

    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        if not allowed(host) or not port.isdigit():
            log("CONNECT", host, 403)
            return self.send_error(403)
        try:
            upstream = socket.create_connection((host, int(port)), timeout=10)
        except OSError:
            log("CONNECT", host, 502)
            return self.send_error(502)
        self.send_response(200, "Connection established")
        self.end_headers()
        log("CONNECT", host, 200)
        self.close_connection = True
        client = self.connection
        relays = [threading.Thread(target=pump, args=(client, upstream)),
                  threading.Thread(target=pump, args=(upstream, client))]
        for relay in relays:
            relay.start()
        for relay in relays:
            relay.join()
        upstream.close()

    def forward(self):
        url = urllib.parse.urlsplit(self.path)
        if url.scheme != "http" or not url.hostname:
            return self.send_error(400)
        host = url.hostname
        if not allowed(host):
            log(self.command, host, 403)
            return self.send_error(403)
        length = min(int(self.headers.get("Content-Length") or 0), 1024 * 1024)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Connection"] = "close"
        target = (url.path or "/") + (("?" + url.query) if url.query else "")
        try:
            upstream = http.client.HTTPConnection(host, url.port or 80, timeout=15)
            upstream.request(self.command, target, body=body, headers=headers)
            reply = upstream.getresponse()
            data = reply.read()
        except OSError:
            log(self.command, host, 502)
            return self.send_error(502)
        log(self.command, host, reply.status)
        self.send_response(reply.status, reply.reason)
        for name, value in reply.getheaders():
            if name.lower() not in HOP_BY_HOP:
                self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        self.close_connection = True
        upstream.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = do_PATCH = forward


def main():
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"proxy: listening on {PORT}", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
