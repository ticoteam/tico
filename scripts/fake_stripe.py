"""A local stand-in for Stripe's balance API, for end-to-end checks with no network and no real key.

  python3 scripts/fake_stripe.py --port 18500
  TICO_STRIPE_BASE=http://127.0.0.1:18500/v1   (the server reads only 127.0.0.1, localhost or ::1)

It serves GET /v1/account, GET /v1/balance, GET /v1/balance_transactions (newest first, `starting_after`, `limit`,
`has_more`, the `expand[]` source as an object), and GET /v1/products and GET /v1/prices, paged the same way. Control
endpoints under /_fake/ add what Stripe would have recorded:
  POST /_fake/txn    {"type": "charge", "amount": 10000, "fee": 320, "billing_name": "Acme Buyer"}  (cents)
                     a refund, dispute or returned payout names its sale or payout with "link": "ch_fake000001"
  POST /_fake/payout {"amount": 9680}                                                              (cents)
  POST /_fake/product {"name": "Acme Support", "active": true}   answers with its "id"
  POST /_fake/price   {"product": "prod_fake000001", "amount": 1000, "currency": "usd", "interval": "month"}
                     an interval of "" is a one-time price
  POST /_fake/reset  forget everything
Balance transactions carry `net` = `amount` - `fee`, and the balance is the sum of the nets."""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

LOCK = threading.Lock()
ACCOUNT = "acct_fake000001"
TXNS: list[dict[str, Any]] = []      # oldest first; served newest first
PRODUCTS: list[dict[str, Any]] = []  # oldest first; served newest first, like the balance transactions
PRICES: list[dict[str, Any]] = []
COUNTER = [0]
NOW = [int(time.time()) - 3600]


def _new_id(prefix: str) -> str:
    COUNTER[0] += 1
    return f"{prefix}_fake{COUNTER[0]:06d}"


def _source(kind: str, link: str, billing_name: str) -> dict[str, Any]:
    """The object a balance transaction came from, as the expanded list shows it."""
    if kind in ("charge", "payment"):
        source: dict[str, Any] = {"id": _new_id("ch"), "object": "charge"}
        if billing_name:
            source["billing_details"] = {"name": billing_name}
        return source
    if kind in ("refund", "payment_refund", "refund_failure"):
        return {"id": _new_id("re"), "object": "refund", "charge": link}
    if kind == "adjustment":
        return {"id": _new_id("dp"), "object": "dispute", "charge": link}
    if kind == "payout":
        return {"id": _new_id("po"), "object": "payout"}
    if kind in ("payout_failure", "payout_cancel"):
        return {"id": link, "object": "payout"}
    return {"id": _new_id("src"), "object": "other"}


def _list(rows: list[dict[str, Any]], query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    """One page of a list, newest first, the way Stripe pages it: `starting_after` is the last id of the page before."""
    limit = min(int(query.get("limit", ["10"])[0]), 100)
    after = query.get("starting_after", [""])[0]
    newest = list(reversed(rows))
    start = 0
    if after:
        ids = [str(r["id"]) for r in newest]
        if after not in ids:
            return 400, {"error": {"message": "No such object"}}
        start = ids.index(after) + 1
    page = newest[start:start + limit]
    return 200, {"object": "list", "data": page, "has_more": start + limit < len(newest)}


def _add(kind: str, amount: int, fee: int, billing_name: str, link: str = "") -> dict[str, Any]:
    NOW[0] += 60
    txn = {"id": _new_id("txn"), "object": "balance_transaction", "type": kind, "amount": amount, "fee": fee,
           "net": amount - fee, "currency": "usd", "status": "available", "created": NOW[0],
           "description": f"{kind} {billing_name}".strip(), "source": _source(kind, link, billing_name)}
    TXNS.append(txn)
    return txn


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:       # quiet: the caller's log is the one that matters
        return

    def _send(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization", "").startswith("Bearer ")

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if not self._authorized():
            return self._send(401, {"error": {"message": "No API key provided."}})
        with LOCK:
            if url.path == "/v1/account":
                return self._send(200, {"object": "account", "id": ACCOUNT, "livemode": False})
            if url.path == "/v1/balance":
                net = sum(int(t["net"]) for t in TXNS)
                return self._send(200, {"object": "balance", "available": [{"amount": net, "currency": "usd"}],
                                        "pending": [{"amount": 0, "currency": "usd"}], "livemode": False})
            if url.path == "/v1/balance_transactions":
                query = parse_qs(url.query)
                limit = min(int(query.get("limit", ["10"])[0]), 100)
                after = query.get("starting_after", [""])[0]
                newest = list(reversed(TXNS))
                start = 0
                if after:
                    ids = [t["id"] for t in newest]
                    if after not in ids:
                        return self._send(400, {"error": {"message": "No such balance_transaction"}})
                    start = ids.index(after) + 1
                page = newest[start:start + limit]
                return self._send(200, {"object": "list", "data": page,
                                        "has_more": start + limit < len(newest), "url": "/v1/balance_transactions"})
            if url.path == "/v1/products":
                return self._send(*_list(PRODUCTS, parse_qs(url.query)))
            if url.path == "/v1/prices":
                return self._send(*_list(PRICES, parse_qs(url.query)))
        return self._send(404, {"error": {"message": "Unknown endpoint"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        with LOCK:
            if self.path == "/_fake/txn":
                txn = _add(str(body.get("type", "charge")), int(body["amount"]), int(body.get("fee", 0)),
                           str(body.get("billing_name", "")), str(body.get("link", "")))
                return self._send(200, {"id": txn["id"], "net": txn["net"], "source": txn["source"]["id"]})
            if self.path == "/_fake/payout":
                txn = _add("payout", -int(body["amount"]), 0, "")
                return self._send(200, {"id": txn["id"], "net": txn["net"], "source": txn["source"]["id"]})
            if self.path == "/_fake/product":
                product = {"id": _new_id("prod"), "object": "product", "name": str(body.get("name", "")),
                           "active": bool(body.get("active", True))}
                PRODUCTS.append(product)
                return self._send(200, {"id": product["id"]})
            if self.path == "/_fake/price":
                interval = str(body.get("interval", ""))
                price = {"id": _new_id("price"), "object": "price", "product": str(body["product"]), "active": True,
                         "currency": str(body.get("currency", "usd")), "unit_amount": int(body["amount"]),
                         "type": "recurring" if interval else "one_time",
                         "recurring": {"interval": interval, "interval_count": 1} if interval else None}
                PRICES.append(price)
                return self._send(200, {"id": price["id"]})
            if self.path == "/_fake/reset":
                TXNS.clear()
                PRODUCTS.clear()
                PRICES.clear()
                NOW[0] = int(time.time()) - 3600
                return self._send(200, {"ok": True})
        return self._send(404, {"error": {"message": "Unknown endpoint"}})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=18500)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake Stripe on http://127.0.0.1:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
