"""A stand-in for Xero's accounting API for end-to-end runs on a developer's computer: stdlib only, on 127.0.0.1, in
memory. It answers the calls backend/finance/xero.py makes (lists with page and If-Modified-Since, the whole lists of accounts and bank transfers, the system account lookup,
creates and credit-note allocations with Idempotency-Key replay, and voids), in the shapes that client parses. Point
Tico at it with TICO_XERO_BASE=http://127.0.0.1:<port>/api.xro/2.0. Nothing here reaches the network.

Calls must carry a bearer token and the tenant id (--tenant, default acme-tenant); anything else is refused as Xero
refuses it.

Control endpoints under /_fake/ are not Xero's:
  POST /_fake/seed     {"Accounts": [...], "Contacts": [...], ...} adds rows
  POST /_fake/change   the same shape: a row with an existing id replaces that row; the change time is now
  POST /_fake/expire   the next call is refused with 401 once (the token has expired)
  POST /_fake/throttle {"times": n} the next n calls are refused with 429 and Retry-After: 1
  GET  /_fake/calls    every call made, in order (method, path, query, idempotency key; never the token)
  POST /_fake/reset    empties everything

  python3 scripts/fake_xero.py --port 18792
"""
import argparse
import json
import re
import sys
import threading
import time
from decimal import Decimal
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

ENDPOINTS = ("Accounts", "Contacts", "Items", "TrackingCategories", "Invoices", "CreditNotes", "Payments",
             "ManualJournals", "BankTransactions", "BankTransfers")
ID_KEY = {"Accounts": "AccountID", "Contacts": "ContactID", "Items": "ItemID", "Invoices": "InvoiceID",
          "CreditNotes": "CreditNoteID",
          "Payments": "PaymentID", "ManualJournals": "ManualJournalID", "BankTransactions": "BankTransactionID",
          "BankTransfers": "BankTransferID"}
ACCOUNT_TYPES = {"BANK", "CURRENT", "CURRLIAB", "EQUITY", "EXPENSE", "REVENUE", "FIXED", "SALES", "OVERHEADS"}
PAGE = 100
PREFIX = "/api.xro/2.0/"
MS = re.compile(r"/Date\((-?\d+)")
LOCK = threading.Lock()
STATE: dict[str, Any] = {}
TENANT = ["acme-tenant"]


def reset() -> None:
    STATE.clear()
    STATE.update({"books": {e: [] for e in ENDPOINTS}, "calls": [], "keys": {}, "expire": False, "throttle": 0,
                  "next_id": 100})


def now_ms() -> str:
    return f"/Date({int(time.time() * 1000)}+0000)/"


def ms_of(value: object) -> int | None:
    found = MS.search(str(value or ""))
    return int(found.group(1)) if found else None


def _error(message: str) -> tuple[int, dict[str, object]]:
    return 400, {"ErrorNumber": 10, "Type": "ValidationException", "Message": "A validation exception occurred",
                 "Elements": [{"ValidationErrors": [{"Message": message}]}]}


def _balanced(lines: object) -> bool:
    if not isinstance(lines, list):
        return False
    total = sum((Decimal(str(line.get("LineAmount", 0))) for line in lines if isinstance(line, dict)), Decimal(0))
    return total == 0


def _has_id(value: object, key: str) -> bool:
    return isinstance(value, dict) and bool(value.get(key))


def _find(endpoint: str, ident: str) -> dict[str, object] | None:
    for row in STATE["books"][endpoint]:  # type: ignore[index,union-attr]
        if row.get(ID_KEY[endpoint]) == ident:
            return row
    return None


def _defaults(endpoint: str, row: dict[str, object]) -> None:
    """What Xero fills in: a status, a credit note's allocations, and a payment's type from the invoice it settles."""
    if endpoint in ("Invoices", "CreditNotes", "Payments"):
        row.setdefault("Status", "AUTHORISED")
    if endpoint == "CreditNotes":
        row.setdefault("Allocations", [])
    if endpoint == "Payments" and not row.get("PaymentType") and isinstance(row.get("Invoice"), dict):
        target = _find("Invoices", str(row["Invoice"].get("InvoiceID") or ""))  # type: ignore[index,union-attr]
        if target is not None:
            row["PaymentType"] = "ACCPAYPAYMENT" if target.get("Type") == "ACCPAY" else "ACCRECPAYMENT"


def _check(endpoint: str, row: dict[str, object]) -> str:
    if endpoint == "Accounts" and (not row.get("Name") or row.get("Type") not in ACCOUNT_TYPES):
        return "Account Name and a valid Type are required"
    if endpoint == "Contacts" and not row.get("Name"):
        return "Contact Name is required"
    if endpoint == "ManualJournals" and not _balanced(row.get("JournalLines")):
        return "Journal lines do not balance"
    if endpoint == "BankTransactions" and (not isinstance(row.get("BankAccount"), dict) or not row.get("LineItems")):
        return "A bank account and at least one line item are required"
    if endpoint == "BankTransactions" and not _has_id(row.get("Contact"), "ContactID"):
        return "A contact is required"
    if endpoint == "Invoices" and (row.get("Type") not in ("ACCREC", "ACCPAY") or not _has_id(row.get("Contact"), "ContactID")):
        return "An invoice needs a type and a contact"
    if endpoint == "CreditNotes" and not _has_id(row.get("Contact"), "ContactID"):
        return "A credit note needs a contact"
    if endpoint == "Payments" and (not _has_id(row.get("Invoice"), "InvoiceID") or not row.get("PaymentType")):
        return "A payment needs an invoice that Xero holds"
    return ""


def _store(endpoint: str, row: dict[str, object]) -> dict[str, object]:
    rows = STATE["books"][endpoint]  # type: ignore[index]
    ident = str(row.get(ID_KEY[endpoint]) or "")
    row["UpdatedDateUTC"] = now_ms()
    for i, old in enumerate(rows):
        if ident and old.get(ID_KEY[endpoint]) == ident:
            rows[i] = row
            return row
    if not ident:
        STATE["next_id"] = int(STATE["next_id"]) + 1  # type: ignore[call-overload]
        row[ID_KEY[endpoint]] = f"fake-{endpoint.lower()}-{STATE['next_id']}"
    rows.append(row)
    return row


def create(endpoint: str, body: dict[str, Any], key: str) -> tuple[int, dict[str, object]]:
    if key and key in STATE["keys"]:  # type: ignore[operator]
        return STATE["keys"][key]  # type: ignore[index,return-value]
    if endpoint not in ENDPOINTS:
        return 404, {"Message": "no such endpoint"}
    made: list[dict[str, object]] = []
    # PUT /Accounts takes the account itself, not a list of them.
    raws = [body] if endpoint == "Accounts" else body.get(endpoint) or []
    for raw in raws:
        row = dict(raw)
        _defaults(endpoint, row)
        problem = _check(endpoint, row)
        if problem:
            return _error(problem)
        made.append(_store(endpoint, row))
    answer: tuple[int, dict[str, object]] = (200, {endpoint: made})
    if key:
        STATE["keys"][key] = answer  # type: ignore[index]
    return answer


def void(endpoint: str, ident: str) -> tuple[int, dict[str, object]]:
    row = _find(endpoint, ident)
    if row is None:
        return _error(f"{endpoint} {ident} was not found")
    row["Status"] = "VOIDED"
    row["UpdatedDateUTC"] = now_ms()
    return 200, {endpoint: [row]}


def credit_note(ident: str) -> tuple[int, dict[str, object]]:
    row = _find("CreditNotes", ident)
    if row is None:
        return _error(f"CreditNote {ident} was not found")
    return 200, {"CreditNotes": [dict(row)]}


def allocate(note_id: str, body: dict[str, Any], key: str) -> tuple[int, dict[str, object]]:
    """Applies a credit note to invoices: each allocation is kept on the note with its own id."""
    if key and key in STATE["keys"]:  # type: ignore[operator]
        return STATE["keys"][key]  # type: ignore[index,return-value]
    note = _find("CreditNotes", note_id)
    if note is None:
        return _error(f"CreditNote {note_id} was not found")
    made: list[dict[str, object]] = []
    for raw in body.get("Allocations") or []:
        STATE["next_id"] = int(STATE["next_id"]) + 1  # type: ignore[call-overload]
        made.append({**raw, "AllocationID": f"fake-allocation-{STATE['next_id']}", "IsDeleted": False})
    if not made:
        return _error("An allocation is required")
    note.setdefault("Allocations", []).extend(made)  # type: ignore[union-attr]
    note["UpdatedDateUTC"] = now_ms()
    answer: tuple[int, dict[str, object]] = (200, {"Allocations": made})
    if key:
        STATE["keys"][key] = answer  # type: ignore[index]
    return answer


def listing(endpoint: str, query: dict[str, str], since: int | None) -> tuple[int, dict[str, object]]:
    rows = [dict(r) for r in STATE["books"][endpoint]]  # type: ignore[index,union-attr]
    if endpoint == "BankTransfers":           # no paging and no change time: the whole list, deleted ones when asked
        if query.get("includeDeleted") != "true":
            rows = [r for r in rows if r.get("Status") != "DELETED"]
        return 200, {endpoint: rows}
    if endpoint == "Contacts" and query.get("includeArchived") != "true":
        rows = [r for r in rows if r.get("ContactStatus") != "ARCHIVED"]
    if since is not None:
        rows = [r for r in rows if (ms_of(r.get("UpdatedDateUTC")) or 0) > since]
    order = query.get("order", "")
    if order:
        rows.sort(key=lambda r: ms_of(r.get("UpdatedDateUTC")) or 0, reverse=order.endswith("DESC"))
    if endpoint == "Accounts":
        return 200, {"Accounts": rows}
    page = max(int(query.get("page") or 1), 1)
    return 200, {endpoint: rows[(page - 1) * PAGE:page * PAGE]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, body: object, extra: dict[str, str] | None = None) -> None:
        raw = json.dumps(body, default=float).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length") or 0)
        found = json.loads(self.rfile.read(size) or b"{}", parse_float=Decimal) if size else {}
        return found if isinstance(found, dict) else {}

    def _route(self, method: str) -> None:
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = self._body()
        with LOCK:
            if url.path.startswith("/_fake/"):
                return self._control(url.path, body)
            key = self.headers.get("Idempotency-Key", "")
            since_header = self.headers.get("If-Modified-Since", "")
            STATE["calls"].append({"method": method, "path": url.path, "query": query,  # type: ignore[attr-defined]
                                   "idempotency_key": key, "if_modified_since": since_header})
            if not self.headers.get("Authorization", "").startswith("Bearer ") or len(self.headers["Authorization"]) < 8:
                return self._send(401, {"Message": "Unauthorized"})
            if self.headers.get("xero-tenant-id") != TENANT[0]:
                return self._send(403, {"Message": "Forbidden: unknown tenant"})
            if STATE["expire"]:
                STATE["expire"] = False
                return self._send(401, {"Message": "token expired"})
            if STATE["throttle"]:
                STATE["throttle"] -= 1
                return self._send(429, {"Message": "rate limit exceeded"}, {"Retry-After": "1"})
            if not url.path.startswith(PREFIX):
                return self._send(404, {})
            parts = url.path[len(PREFIX):].strip("/").split("/")
            if method == "GET" and len(parts) == 1:
                since = None
                if since_header:
                    since = int(parsedate_to_datetime(since_header).timestamp() * 1000)
                return self._send(*listing(parts[0], query, since))
            if method == "PUT" and len(parts) == 1:
                return self._send(*create(parts[0], body, key))
            if method == "POST" and parts[0] in ("ManualJournals", "Invoices") and len(parts) == 2:
                return self._send(*void(parts[0], parts[1]))
            if method == "GET" and parts[0] == "CreditNotes" and len(parts) == 2:
                return self._send(*credit_note(parts[1]))
            if method == "PUT" and parts[0] == "CreditNotes" and len(parts) == 3 and parts[2] == "Allocations":
                return self._send(*allocate(parts[1], body, key))
            self._send(404, {})

    def _control(self, path: str, body: dict[str, Any]) -> None:
        if path == "/_fake/calls":
            return self._send(200, {"calls": STATE["calls"]})
        if path == "/_fake/reset":
            reset()
            return self._send(200, {"ok": True})
        if path == "/_fake/expire":
            STATE["expire"] = True
            return self._send(200, {"ok": True})
        if path == "/_fake/throttle":
            STATE["throttle"] = int(body.get("times") or 0)
            return self._send(200, {"ok": True})
        if path in ("/_fake/seed", "/_fake/change"):
            for endpoint, rows in body.items():
                if endpoint in ENDPOINTS and isinstance(rows, list):
                    for row in rows:
                        if path == "/_fake/seed":
                            STATE["books"][endpoint].append(dict(row))  # type: ignore[index,union-attr]
                        else:
                            _store(endpoint, dict(row))
            return self._send(200, {"ok": True})
        self._send(404, {})

    def do_GET(self) -> None:
        self._route("GET")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_POST(self) -> None:
        self._route("POST")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=18792)
    parser.add_argument("--tenant", default="acme-tenant")
    args = parser.parse_args()
    TENANT[0] = args.tenant
    reset()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake Xero on http://127.0.0.1:{args.port}{PREFIX}", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
