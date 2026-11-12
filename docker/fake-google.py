"""A stand-in for Google's token, Gmail and Calendar endpoints, for docker/connectors-smoke.sh only.

It answers just what one mailbox sync and one calendar lookup ask, and appends each request path to
the file named by argv[2] so the smoke test can see the connectors job really reached it. The sync reads
messages in a Gmail batch call (POST /batch/gmail/v1): each part is answered as its own path would be.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MESSAGE = {
    "id": "m1",
    "threadId": "t1",
    "labelIds": ["INBOX", "UNREAD"],
    "snippet": "hello",
    "internalDate": "1790000000000",
    "historyId": "5",
    "payload": {
        "mimeType": "text/plain",
        "body": {"size": 5, "data": "aGVsbG8"},
        "headers": [
            {"name": "From", "value": "Pat <pat@customer.example>"},
            {"name": "To", "value": "owner@example.com"},
            {"name": "Subject", "value": "Hello"},
            {"name": "Date", "value": "Mon, 28 Sep 2026 10:00:00 +0000"},
        ],
    },
}


def answer(path):
    if path.startswith("/token"):
        return {"access_token": "fixture", "expires_in": 3600, "token_type": "Bearer"}
    if "/messages/m1" in path:
        return MESSAGE
    if path.endswith("/messages") or "/messages?" in path:
        return {"messages": [{"id": "m1", "threadId": "t1"}]}
    if path.endswith("/profile"):
        return {"emailAddress": "owner@example.com", "historyId": "5"}
    if "/labels" in path:
        return {"labels": []}
    if "/calendarList" in path:
        return {"items": [{"id": "primary", "primary": True}]}
    return {}


def batch(content_type, body):
    """(content type, body) answering each part of a Gmail batch call."""
    boundary = re.search(r'boundary="?([^";]+)"?', content_type).group(1)
    parts = []
    for chunk in body.split("--" + boundary)[1:]:
        if chunk.startswith("--"):
            break
        cid = re.search(r"(?im)^content-id:\s*<([^>]+)>", chunk).group(1)
        path = re.search(r"(?m)^[A-Z]+ (\S+) HTTP/", chunk).group(1)
        parts.append(
            f"--part\r\nContent-Type: application/http\r\nContent-ID: <response-{cid}>\r\n\r\n"
            f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{json.dumps(answer(path))}\r\n"
        )
    return "multipart/mixed; boundary=part", "".join(parts) + "--part--\r\n"


class Handler(BaseHTTPRequestHandler):
    def respond(self):
        length = int(self.headers.get("Content-Length") or 0)
        sent = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        with open(sys.argv[2], "a") as log:
            log.write(self.command + " " + self.path + "\n")
        kind, text = "application/json", json.dumps(answer(self.path))
        if self.path.startswith("/batch/"):
            kind, text = batch(self.headers.get("Content-Type", ""), sent)
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = respond

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
