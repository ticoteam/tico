#!/usr/bin/env python3
"""A company hub's update feed on loopback, for the desktop updater test in app/src/main.rs.

    update_hub.py BUNDLE_DIR VERSION COOKIE

The pieces a real install uses, not stand-ins: scripts/app_release.py publishes BUNDLE_DIR to a bucket, and the
server's own download routes (backend/downloads.py) serve its latest.json and redirect each artifact to a URL the
server's S3 client presigns. Only the two network services are local: a bucket (path-style S3 on loopback) and, in
front of the hub, an Access gate that sends a request without the hub session cookie to a sign-in page, as
Cloudflare Access does. Prints one JSON line, {"hub": ..., "log": ...}, then serves until stdin closes. The log is
one JSON line per request either service saw: which service, method, path and whether it carried a Cookie header.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
BUCKET = "acme-files"
LOCK = threading.Lock()


def record(log, **entry):
    with LOCK, log.open("a") as stream:
        stream.write(json.dumps(entry) + "\n")


def bucket_server(log):
    """Path-style S3 for one bucket, in memory. It checks no signatures: deciding which signed URLs to follow is
    the desktop app's job, and that is what the test exercises."""
    objects = {}

    class Bucket(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def key(self):
            path = urlsplit(self.path).path
            prefix = f"/{BUCKET}/"
            return path[len(prefix):] if path.startswith(prefix) else None

        def note(self):
            record(log, service="bucket", method=self.command, path=self.path,
                   cookie="Cookie" in self.headers, authorization="Authorization" in self.headers)

        def reply(self, status, body=b"", content_type="application/xml"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_PUT(self):
            self.note()
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if "aws-chunked" in (self.headers.get("Content-Encoding") or ""):
                body = unchunk(body)
            objects[self.key()] = body
            self.send_response(200)
            self.send_header("ETag", '"0"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self.note()
            body = objects.get(self.key())
            if body is None:
                self.reply(404, b"<Error><Code>NoSuchKey</Code><Message>none</Message></Error>")
            else:
                self.reply(200, body, "application/octet-stream")

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Bucket)


def unchunk(body):
    """An aws-chunked upload's payload, without its chunk sizes and trailing checksum."""
    out, rest = b"", body
    while rest:
        size, _, rest = rest.partition(b"\r\n")
        size = int(size.split(b";")[0], 16)
        if size == 0:
            break
        out, rest = out + rest[:size], rest[size + 2:]
    return out


def access_gate(app, cookie, hub, log):
    """Cloudflare Access in front of the hub: without the session cookie, a redirect to its sign-in page."""
    async def gate(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        headers = {name.decode().lower(): value.decode() for name, value in scope["headers"]}
        sent = headers.get("cookie", "")
        signed_in = cookie in [part.strip() for part in sent.split(";")]
        record(log, service="hub", method=scope["method"], path=scope["path"], cookie="cookie" in headers,
               signed_in=signed_in)
        if signed_in:
            return await app(scope, receive, send)
        await send({"type": "http.response.start", "status": 302,
                    "headers": [(b"location", f"{hub}/cdn-cgi/access/login".encode()), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})
    return gate


def main(bundles, version, cookie):
    scratch = Path(tempfile.mkdtemp(prefix="tico-update-hub-"))
    log = scratch / "requests.jsonl"
    log.touch()
    storage = bucket_server(log)
    threading.Thread(target=storage.serve_forever, daemon=True).start()
    storage_url = f"http://127.0.0.1:{storage.server_address[1]}"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    hub = f"http://127.0.0.1:{listener.getsockname()[1]}"

    # Throwaway credentials for the loopback bucket; nothing here may reach a real account.
    for name in list(os.environ):
        if name.startswith(("AWS_", "TICO_BLOB_", "LITESTREAM_", "TICO_BACKUP_")):
            del os.environ[name]
    os.environ.update(AWS_ACCESS_KEY_ID="AKIDACMEEXAMPLE", AWS_SECRET_ACCESS_KEY="acme-example-secret",
                      AWS_DEFAULT_REGION="us-west-2", AWS_ENDPOINT_URL_S3=storage_url,
                      AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
                      AWS_EC2_METADATA_DISABLED="true")
    sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
    import app_release
    if app_release.main(["--version", version, "--bucket", BUCKET, "--base", hub, "--quiet", str(bundles)]):
        raise SystemExit("publishing the bundles failed")

    import uvicorn
    from fastapi import FastAPI
    from backend.blobs import Blobs
    from backend.config import Settings
    from backend.downloads import install_downloads
    settings = Settings(db_path=scratch / "hub.db", public_url=hub, blob_bucket=BUCKET, blob_endpoint=storage_url,
                        blob_region="us-west-2")
    server = FastAPI()
    server.state.blobs = Blobs(settings)
    install_downloads(server, SimpleNamespace(settings=settings))
    web = uvicorn.Server(uvicorn.Config(access_gate(server, cookie, hub, log), log_level="warning"))
    threading.Thread(target=web.run, kwargs={"sockets": [listener]}, daemon=True).start()
    while not web.started:
        threading.Event().wait(0.05)
    print(json.dumps({"hub": hub, "log": str(log)}), flush=True)
    sys.stdin.read()
    web.should_exit = True
    storage.shutdown()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
