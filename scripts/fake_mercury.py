"""A stand-in for Mercury's API for end-to-end runs on a developer's computer: stdlib only, on 127.0.0.1, in memory.

It answers the endpoints backend/finance/mercury.py calls, in the shapes that client parses, starting from the recorded
answers in backend/tests/fixtures/mercury/responses.json. Point Tico at it with TICO_MERCURY_BASE=http://127.0.0.1:<port>/api/v1
(scripts/finance_e2e.sh does this). Nothing here reaches the network.

Control endpoints under /_fake/ are not Mercury's:
  POST /_fake/txn              {"amount", "counterparty", "date", "kind": "bank"|"credit card", "account"?}
                               adds a sent transaction and a transaction.created event (the Events API shows it)
  POST /_fake/approve/<id>     the approver approves a send-money request: it becomes a sent transaction
  POST /_fake/reject/<id>      the approver rejects a send-money request
  POST /_fake/card-spend       {"card", "amount", "merchant", "date"?, "pending"?}: a purchase on a card (its Mercury id,
                               last four or nickname) on the IO credit account, with its transaction.created event; a
                               frozen or cancelled card is declined (a failed transaction), as Mercury does
  GET  /_fake/calls            every Mercury call Tico made, in order (method, path, query, body; never the token)

  python3 scripts/fake_mercury.py --port 18790
"""
import argparse
import copy
import json
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURES = json.loads((Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures" / "mercury"
                       / "responses.json").read_text())
PREFIX = "/api/v1"
BANK, CARD = "acct-chk", "credit-io"          # the checking account and the IO credit account in the fixtures
SEED_TXNS = ("txn_card_posted", "txn_card_bill_bank", "txn_card_bill_io", "txn_bank_fee", "txn_deposit")
# The last four Mercury gives each new card, in turn (fictional; never a real card's).
LAST_FOUR = ("4021", "7713", "3390", "5168", "8842", "2207", "6634", "1958", "9071", "3546")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class State:
    """Everything Mercury would hold for Acme, guarded by one lock (the server answers each request on its own thread)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.seq = 0
        self.txns: list[dict] = [copy.deepcopy(FIXTURES[k]) for k in SEED_TXNS]
        self.events: list[dict] = []
        self.requests: list[dict] = []
        self.cards: list[dict] = []
        self.recipients: dict[str, dict] = {}
        self.calls: list[dict] = []

    def new(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{self.seq}"

    def add_txn(self, txn: dict) -> None:
        self.txns.append(txn)
        self.events.append({"id": self.new("evt"), "resourceType": "transaction", "resourceId": txn["id"],
                            "operationType": "create", "resourceVersion": 1, "occurredAt": now(),
                            "changedPaths": sorted(txn), "mergePatch": txn, "previousValues": None})


STATE = State()


def page(items: list[dict], key: str, start_after: str | None, limit: int) -> tuple[list[dict], dict]:
    """Mercury's cursor page: the items after `start_after`, and page.nextPage naming the last one when more follow."""
    ids = [str(i[key]) for i in items]
    start = ids.index(start_after) + 1 if start_after in ids else 0
    chunk = items[start:start + max(1, limit)]
    more = start + max(1, limit) < len(items)
    return chunk, ({"nextPage": str(chunk[-1][key])} if more and chunk else {})


def one(q: dict, name: str, default: str | None = None) -> str | None:
    return (q.get(name) or [default])[0]


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-mercury/1"

    def log_message(self, fmt, *args):             # one line per call on stderr
        sys.stderr.write("fake-mercury " + (fmt % args) + "\n")

    def reply(self, status: int, body: object) -> None:
        raw = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size else b""
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        body = self.body() if method == "POST" else {}
        if url.path.startswith("/_fake/"):
            return self.control(method, url.path[len("/_fake/"):], body)
        if not url.path.startswith(PREFIX + "/"):
            return self.reply(404, {"message": "not a Mercury path"})
        path = url.path[len(PREFIX):]
        with STATE.lock:
            STATE.calls.append({"method": method, "path": path, "query": {k: v[0] for k, v in q.items()},
                                "body": body or None})
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                return self.reply(401, {"message": "Unauthorized"})
            status, answer = mercury(method, path, q, body)
        self.reply(status, answer)

    def control(self, method: str, name: str, body: dict) -> None:
        with STATE.lock:
            if method == "GET" and name == "calls":
                return self.reply(200, {"calls": STATE.calls})
            if method == "POST" and name == "txn":
                status, answer = add_txn(body)
                return self.reply(status, answer)
            if method == "POST" and name == "card-spend":
                status, answer = card_spend(body)
                return self.reply(status, answer)
            verb, _, rid = name.partition("/")
            if method == "POST" and verb in ("approve", "reject") and rid:
                status, answer = decide(verb, rid)
                return self.reply(status, answer)
        self.reply(404, {"message": "no such fake control endpoint"})


def mercury(method: str, path: str, q: dict, body: dict) -> tuple[int, dict]:
    """One Mercury endpoint, for the shapes backend/finance/mercury.py reads."""
    parts = path.strip("/").split("/")
    limit = int(one(q, "limit", "500") or 500)
    after = one(q, "start_after")
    if method == "GET" and path == "/accounts":
        return 200, FIXTURES["accounts"]
    if method == "GET" and path == "/credit":
        return 200, FIXTURES["credit"]
    if method == "GET" and path == "/users":
        return 200, FIXTURES["users"]
    if method == "GET" and path == "/transactions":
        chunk, nxt = page(STATE.txns, "id", after, limit)
        return 200, {"transactions": chunk, "page": nxt}
    if method == "GET" and parts[0] == "transaction" and len(parts) == 2:
        return found(next((t for t in STATE.txns if t["id"] == parts[1]), None))
    if method == "GET" and path == "/events":
        events = list(reversed(STATE.events)) if one(q, "order") == "desc" else STATE.events
        if after and one(q, "order") != "desc":
            ids = [e["id"] for e in events]
            events = events[ids.index(after) + 1:] if after in ids else events
        chunk = events[:limit]
        return 200, {"events": chunk, "page": {"nextPage": chunk[-1]["id"]} if len(events) > limit and chunk else {}}
    if method == "GET" and len(parts) == 3 and parts[0] == "account" and parts[2] == "transactions":
        mine = [t for t in STATE.txns if str(t.get("accountId")) == parts[1] and in_search(t, one(q, "search"))
                and in_days(t, one(q, "start"), one(q, "end"))]
        chunk, nxt = page(mine, "id", after, limit)
        return 200, {"transactions": chunk, "page": nxt}
    if method == "POST" and path == "/transfer":
        return transfer(body)
    if method == "GET" and parts[0] == "account" and parts[-1] == "statements":
        return 200, {"statements": [FIXTURES["bank_statement"]]}
    if method == "GET" and parts[0] == "credit" and parts[-1] == "statements":
        return 200, {"statements": [FIXTURES["credit_statement_new"]]}
    if method == "GET" and path == "/cards":
        user = one(q, "userId")
        mine = [c for c in STATE.cards if not user or c["userId"] == user]
        chunk, nxt = page(mine, "id", after, limit)
        return 200, {"cards": chunk, "page": nxt}
    if parts[0] == "cards" and len(parts) >= 2:
        card = next((c for c in STATE.cards if c["id"] == parts[1]), None)
        if method == "GET" and len(parts) == 2:
            return found(card)
        if card is None:
            return 404, {"message": "no such card"}
        if method == "POST" and len(parts) == 2:
            card["spendLimit"] = body.get("spendLimit", card["spendLimit"])
            card["nickname"] = body.get("nickname", card["nickname"])
            return 200, card
        if method == "POST" and len(parts) == 3 and parts[2] in ("freeze", "unfreeze", "cancel"):
            want = {"freeze": "active", "unfreeze": "frozen", "cancel": None}[parts[2]]
            if card["status"] == "cancelled" or (want and card["status"] != want):
                return 400, {"message": f"card is {card['status']}"}
            card["status"] = {"freeze": "frozen", "unfreeze": "active", "cancel": "cancelled"}[parts[2]]
            card["updatedAt"] = now()
            return 200, card
    if method == "POST" and path == "/cards":
        return 200, new_card(body)
    if method == "POST" and path == "/recipients":
        rid = STATE.new("rcp")
        STATE.recipients[rid] = {**body, "id": rid}
        return 200, {"id": rid}
    if method == "GET" and parts[0] == "recipient" and len(parts) == 2:
        return found(STATE.recipients.get(parts[1]))
    if method == "POST" and parts[0] == "account" and parts[-1] == "request-send-money":
        rid = STATE.new("req")
        req = {**copy.deepcopy(FIXTURES["send_request_created"]), "requestId": rid, "accountId": parts[1],
               "amount": body.get("amount"), "recipientId": body.get("recipientId"),
               "memo": body.get("externalMemo") or body.get("note")}
        STATE.requests.append(req)
        return 200, req
    if method == "GET" and parts[0] == "request-send-money" and len(parts) == 2:
        return found(next((r for r in STATE.requests if r["requestId"] == parts[1]), None))
    if method == "GET" and path == "/request-send-money":
        mine = [r for r in STATE.requests if r["accountId"] == one(q, "accountId")]
        chunk, nxt = page(mine, "requestId", after, limit)
        return 200, {"requests": chunk, "page": nxt}
    return 404, {"message": f"fake Mercury does not answer {method} {path}"}


def new_card(body: dict) -> dict:
    """POST /cards: a virtual card, usable at once, in Mercury's card shape (the create answer's cardId and
    lastFourDigits beside the card fields). A credit card with no accountId is on the org's IO credit account."""
    cid = STATE.new("card")
    four = LAST_FOUR[len(STATE.cards) % len(LAST_FOUR)]
    user = next((u for u in FIXTURES["users"]["users"] if u["id"] == body.get("userId")), None)
    holder = f"{user['firstName']} {user['lastName']}" if user else "Acme"
    at = now()
    card = {**copy.deepcopy(FIXTURES["card_created"]), "id": cid, "cardId": cid, "userId": body.get("userId"),
            "accountId": body.get("accountId") or CARD, "nameOnCard": holder, "nickname": body.get("nickname"),
            "lastFour": four, "lastFourDigits": four, "expiration": {"month": 11, "year": int(at[:4]) + 3},
            "type": "virtual", "kind": body.get("kind", "credit"), "status": "active",
            "spendLimit": body.get("spendLimit"), "spendLimitType": "cardLimit", "isAgentCard": False,
            "physicalCardStatus": None, "createdAt": at, "updatedAt": at}
    STATE.cards.append(card)
    return card


def card_spend(body: dict) -> tuple[int, dict]:
    """A purchase on a card: a creditCardTransaction carrying its cardId, pending until it posts. Mercury declines a
    card that is not active, which shows as a failed transaction."""
    key = str(body.get("card") or "")
    card = next((c for c in STATE.cards if key in (c["id"], c["lastFour"], c.get("nickname"))), None)
    if card is None:
        return 404, {"message": "no such card (its id, last four or nickname)"}
    try:
        amount = -abs(float(body["amount"]))
        day = datetime.fromisoformat(str(body.get("date") or now()[:10])).strftime("%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        return 400, {"message": "amount (a number) is required; date is YYYY-MM-DD"}
    name = str(body.get("merchant") or "ACME MERCHANT")
    pending = bool(body.get("pending"))
    declined = card["status"] != "active"
    txn = {"id": STATE.new("txn-card"), "accountId": card["accountId"], "amount": amount,
           "createdAt": f"{day}T15:00:00Z", "postedAt": None if pending or declined else f"{day}T18:00:00Z",
           "status": "failed" if declined else "pending" if pending else "sent",
           "failedAt": f"{day}T15:00:01Z" if declined else None,
           "reasonForFailure": f"Card {card['status']}" if declined else None,
           "kind": "creditCardTransaction", "counterpartyName": name.upper(), "counterpartyNickname": name,
           "bankDescription": name.upper(), "cardId": card["id"], "requestId": None, "note": None,
           "merchant": {"amount": amount, "currency": "USD"}}
    STATE.add_txn(txn)
    return 200, {"transaction": txn}


def in_search(txn: dict, search: str | None) -> bool:
    """Mercury's search of an account's transactions: the text sits in the note, description or counterparty."""
    text = (search or "").lower()
    return not text or any(text in str(txn.get(f) or "").lower() for f in ("note", "bankDescription", "counterpartyName"))


def in_days(txn: dict, start: str | None, end: str | None) -> bool:
    """Whether the transaction was created on a day from `start` through `end` (YYYY-MM-DD; either may be left out)."""
    day = str(txn.get("createdAt") or "")[:10]
    return (not start or day >= start) and (not end or day <= end)


def transfer(body: dict) -> tuple[int, dict]:
    """POST /transfer: Mercury moves the money at once and answers with both legs, each a transaction of its own (the
    debit out of the source, the credit into the destination), each carrying the note it was sent with."""
    if any(body.get(k) in (None, "") for k in ("sourceAccountId", "destinationAccountId", "amount", "idempotencyKey")):
        return 400, {"message": "sourceAccountId, destinationAccountId, amount and idempotencyKey are required"}
    try:
        amount = abs(float(body["amount"]))
    except (TypeError, ValueError):
        return 400, {"message": "amount must be a number"}
    at = now()
    legs = []
    for account, value in ((str(body["sourceAccountId"]), -amount), (str(body["destinationAccountId"]), amount)):
        leg = {"id": STATE.new("txn-xfer"), "accountId": account, "amount": value, "createdAt": at, "postedAt": None,
               "status": "pending", "kind": "internalTransfer", "counterpartyName": "Internal transfer",
               "bankDescription": "Internal transfer", "note": body.get("note"), "externalMemo": None,
               "requestId": None, "cardId": None}
        STATE.add_txn(leg)
        legs.append(leg)
    debit, credit = legs
    return 200, {"debitTransaction": debit, "creditTransaction": credit, "status": "pending"}


def found(item: dict | None) -> tuple[int, dict]:
    return (200, item) if item is not None else (404, {"message": "not found"})


def add_txn(body: dict) -> tuple[int, dict]:
    try:
        amount = float(body["amount"])
        day = datetime.fromisoformat(str(body["date"])).strftime("%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        return 400, {"message": "amount (a number) and date (YYYY-MM-DD) are required"}
    kind = str(body.get("kind") or "bank")
    if kind not in ("bank", "credit card"):
        return 400, {"message": "kind is bank or credit card"}
    card = kind == "credit card"
    name = str(body.get("counterparty") or "FAKE COUNTERPARTY")
    txn = {"id": STATE.new("txn-fake"), "accountId": str(body.get("account") or (CARD if card else BANK)),
           "amount": amount, "createdAt": f"{day}T12:00:00Z", "postedAt": f"{day}T12:00:00Z", "status": "sent",
           "kind": "creditCardTransaction" if card else ("outgoingPayment" if amount < 0 else "externalTransfer"),
           "counterpartyName": name, "counterpartyNickname": None, "bankDescription": name, "cardId": None,
           "requestId": None, "note": None, "merchant": {"amount": amount, "currency": "USD"}}
    STATE.add_txn(txn)
    return 200, {"transaction": txn}


def decide(verb: str, rid: str) -> tuple[int, dict]:
    req = next((r for r in STATE.requests if r["requestId"] == rid), None)
    if req is None:
        return 404, {"message": "no such request"}
    if req["status"] != "pendingApproval":
        return 409, {"message": f"request is {req['status']}"}
    if verb == "reject":
        req["status"] = "rejected"
        req["reviews"] = [{"userId": "user-ben", "decision": "rejected"}]
        return 200, req
    req["status"] = "approved"
    req["reviews"] = [{"userId": "user-ben", "decision": "approved"}]
    txn = {"id": STATE.new("txn-req"), "accountId": req["accountId"], "amount": -abs(float(req["amount"])),
           "createdAt": now(), "postedAt": now(), "status": "sent", "kind": "outgoingPayment",
           "counterpartyName": str(req.get("recipientId") or ""), "bankDescription": "ACH", "cardId": None,
           "requestId": rid, "externalMemo": req.get("memo"), "note": None}
    STATE.add_txn(txn)
    return 200, {"request": req, "transaction": txn}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=18790)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"fake Mercury on http://127.0.0.1:{args.port}{PREFIX}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
