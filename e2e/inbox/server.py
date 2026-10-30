#!/usr/bin/env python3
"""A fake HQ inbound service for scripts/browser-journey.sh, so TICO_INBOUND_URL works offline.

It answers the paths backend/inbound.py calls under /v1/inbound/ with the same shapes: list and create inboxes, rename,
release, pull the delivery feed from a cursor, and ack it. Phone rentals follow HQ's approval contract: a request waits
until TICO approves its quote (POST /_approve, test-only), the owner accepts that exact quote, and the accepted rental
becomes an SMS inbox. Deliveries come from the practice site through POST /_deliver (kind "sms" or "email"), which is
test-only and not part of HQ. Stdlib only; everything is in memory.

Authorization: `Authorization: Bearer $STUB_KEY` on every /v1 call, as the install sends its inbound key.
The log holds one line per request: method, path and status. Bodies are never logged.
"""
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse

PORT = int(os.environ.get("STUB_PORT", "8791"))
KEY = os.environ.get("STUB_KEY", "")
DOMAIN = os.environ.get("STUB_DOMAIN", "inbound.tico.test")
LOCK = threading.Lock()
STATE = {"inboxes": {}, "by_operation": {}, "deliveries": [], "acked": 0, "seq": 0,
         "phones": {}, "phone_by_operation": {}}
PHONE_PRICE = 9_000_000   # micros of a dollar: the quote the stub offers, and the only price it accepts


def inbox_view(item):
    return {key: item[key] for key in ("inbox_id", "kind", "name", "address", "state", "created_by", "period_end",
                                       "cancel_pending")}


def phone_view(item):
    return {key: item[key] for key in ("request_id", "state", "name", "quote_price_micros", "inbox_id", "address")}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeInbound"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        print(f"{self.command} {urllib.parse.urlsplit(self.path).path} {code}", file=sys.stderr, flush=True)

    def body(self):
        length = min(int(self.headers.get("Content-Length") or 0), 1024 * 1024)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def authorized(self):
        return bool(KEY) and self.headers.get("Authorization") == "Bearer " + KEY

    def route(self):
        url = urllib.parse.urlsplit(self.path)
        return url.path, urllib.parse.parse_qs(url.query)

    def do_GET(self):
        path, query = self.route()
        if path == "/_deliveries":   # test-only: the texts the practice site sent, for the leak check's needles
            with LOCK:
                return self.send(200, {"texts": [d["text"] for d in STATE["deliveries"]]})
        if path == "/_state":
            with LOCK:
                return self.send(200, {"inboxes": len(STATE["inboxes"]), "deliveries": len(STATE["deliveries"]),
                                       "acked": STATE["acked"], "phones": len(STATE["phones"])})
        if not path.startswith("/v1/inbound/"):
            return self.send(404, {"error": "not_found"})
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        if path == "/v1/inbound/inboxes":
            after = (query.get("after") or [""])[0]
            with LOCK:
                rows = [inbox_view(STATE["inboxes"][k]) for k in sorted(STATE["inboxes"]) if k > after]
            return self.send(200, {"inboxes": rows[:100], "next": rows[99]["inbox_id"] if len(rows) > 100 else None})
        if path == "/v1/inbound/phone-quote":
            return self.send(200, {"price_micros": PHONE_PRICE, "currency": "USD", "period_days": 30,
                                   "purchase_available": False})
        if path == "/v1/inbound/phone-requests":
            with LOCK:
                rows = [phone_view(STATE["phones"][k]) for k in sorted(STATE["phones"])]
            return self.send(200, {"phone_requests": rows[:100], "next": None})
        return self.send(404, {"error": "not_found"})

    def do_POST(self):
        path, _ = self.route()
        body = self.body()
        if path == "/_deliver":
            return self.deliver(body)
        if path == "/_approve":
            return self.approve(body)
        if not path.startswith("/v1/inbound/"):
            return self.send(404, {"error": "not_found"})
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        rest = path[len("/v1/inbound/"):]
        if rest == "inboxes/email":
            return self.create(body)
        if rest == "phone-requests":
            return self.request_phone(body)
        parts = rest.split("/")
        if len(parts) == 3 and parts[0] == "phone-requests" and parts[2] in ("accept", "cancel"):
            return self.phone_action(parts[1], parts[2], body)
        if rest == "pull":
            return self.pull(body)
        if rest == "ack":
            with LOCK:
                STATE["acked"] = max(STATE["acked"], int(body.get("through_hq_seq") or 0))
            return self.send(200, {})
        parts = rest.split("/")
        if len(parts) == 3 and parts[0] == "inboxes" and parts[2] in ("rename", "release"):
            return self.change(parts[1], parts[2], body)
        return self.send(404, {"error": "not_found"})

    def create(self, body):
        operation = str(body.get("operation_id") or "")
        with LOCK:
            if operation and operation in STATE["by_operation"]:
                return self.send(200, inbox_view(STATE["inboxes"][STATE["by_operation"][operation]]))
            ident = "inb_" + secrets.token_hex(8)
            item = {"inbox_id": ident, "kind": "email", "name": str(body.get("name") or "Agent inbox")[:80],
                    "address": f"journey-{secrets.token_hex(4)}@{DOMAIN}", "state": "active",
                    "created_by": str(body.get("created_by") or ""), "period_end": None, "cancel_pending": 0}
            STATE["inboxes"][ident] = item
            if operation:
                STATE["by_operation"][operation] = ident
        return self.send(200, inbox_view(item))

    def request_phone(self, body):
        operation = str(body.get("operation_id") or "")
        with LOCK:
            if operation and operation in STATE["phone_by_operation"]:
                known = STATE["phones"][STATE["phone_by_operation"][operation]]
                return self.send(200, {"phone_request": phone_view(known)})
            ident = "pr_" + secrets.token_hex(8)
            item = {"request_id": ident, "state": "pending", "name": str(body.get("name") or "Agent phone")[:80],
                    "quote_price_micros": PHONE_PRICE, "inbox_id": "", "address": ""}
            STATE["phones"][ident] = item
            if operation:
                STATE["phone_by_operation"][operation] = ident
            return self.send(200, {"phone_request": phone_view(item)})

    def phone_action(self, ident, action, body):
        with LOCK:
            item = STATE["phones"].get(ident)
            if not item:
                return self.send(404, {"error": "not_found"})
            if action == "cancel":
                item["state"] = "cancelled"
                return self.send(200, {"phone_request": phone_view(item)})
            if item["state"] == "accepted":
                return self.send(200, {"phone_request": phone_view(item)})
            if item["state"] != "approved":
                return self.send(409, {"error": "phone_approval_required"})
            if int(body.get("price_micros") or 0) != item["quote_price_micros"]:
                return self.send(409, {"error": "quote_expired_or_changed"})
            # The rental's number is exclusive to this company: an SMS inbox that takes the texts sent to it.
            inbox_id = "inb_" + secrets.token_hex(8)
            address = "+1555" + f"{secrets.randbelow(10 ** 7):07d}"
            STATE["inboxes"][inbox_id] = {"inbox_id": inbox_id, "kind": "sms", "name": item["name"], "address": address,
                                          "state": "active", "created_by": str(body.get("created_by") or ""),
                                          "period_end": None, "cancel_pending": 0}
            item.update(state="accepted", inbox_id=inbox_id, address=address)
            return self.send(200, {"phone_request": phone_view(item)})

    def approve(self, body):
        """Test-only: TICO approves the quote of a phone request, which the owner may then accept."""
        with LOCK:
            item = STATE["phones"].get(str(body.get("request_id") or ""))
            if not item or item["state"] != "pending":
                return self.send(404, {"error": "not_found"})
            item["state"] = "approved"
            return self.send(200, {"phone_request": phone_view(item)})

    def change(self, ident, action, body):
        with LOCK:
            item = STATE["inboxes"].get(ident)
            if not item:
                return self.send(404, {"error": "not_found"})
            if action == "rename":
                item["name"] = str(body.get("name") or item["name"])[:80]
            else:
                item["state"] = "released"
            return self.send(200, inbox_view(item))

    def pull(self, body):
        cursor = int(body.get("cursor") or 0)
        with LOCK:
            pending = [d for d in STATE["deliveries"] if d["hq_seq"] > cursor][:50]
            through = pending[-1]["hq_seq"] if pending else cursor
        return self.send(200, {"cursor": through, "deliveries": pending, "gaps": []})

    def deliver(self, body):
        """The practice site's mail: addressed to an inbox's address, kept until the company's pull takes it."""
        to = str(body.get("to") or "").strip().lower()
        kind = "sms" if body.get("kind") == "sms" else "email"
        with LOCK:
            item = next((i for i in STATE["inboxes"].values()
                         if i["address"] == to and i["state"] == "active" and i["kind"] == kind), None)
            if not item:
                return self.send(404, {"error": "unknown_address"})
            STATE["seq"] += 1
            delivery = {"hq_seq": STATE["seq"], "delivery_id": "dlv_" + secrets.token_hex(8), "inbox_id": item["inbox_id"],
                        "kind": kind, "received_at": time.time(), "from": str(body.get("from") or "")[:300],
                        "subject": str(body.get("subject") or "")[:300], "text": str(body.get("text") or "")[:200000],
                        "spam": False, "truncated": False}
            STATE["deliveries"].append(delivery)
        return self.send(200, {"delivery_id": delivery["delivery_id"]})


def main():
    if not KEY:
        print("inbox: STUB_KEY must be set (the install's TICO_INBOUND_KEY)", file=sys.stderr)
        return 2
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"inbox: listening on {PORT}", file=sys.stderr, flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
