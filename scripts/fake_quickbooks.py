"""A stand-in for QuickBooks Online's API for end-to-end runs on a developer's computer: stdlib only, on 127.0.0.1, in
memory. It answers the calls backend/finance/qbo.py makes (query paging, WHERE and counts, CDC, creates and updates by
RequestId and Id, deletes and voids with SyncToken), in the shapes that client parses. Point Tico at it with TICO_QUICKBOOKS_BASE=http://127.0.0.1:<port>.
Nothing here reaches the network.

Control endpoints under /_fake/ are not QuickBooks':
  POST /_fake/seed     {"Account": [...], "Vendor": [...], "Item": [...], ...} adds records of any entity
  POST /_fake/change   the same shape: records the change feed (CDC) reports next time it is read
  POST /_fake/expire   the next call is refused with 401 once (the token has expired)
  POST /_fake/lose     {"path": "bill", "count": 1}: the next <count> creates at that path are written, then the
                       answer is lost (the connection closes with no reply); a retry with the same RequestId finds it
  GET  /_fake/calls    every call made, in order (method, path, query, body; never the token), plus "lost" and
                       "refused" (401s given) counts
  POST /_fake/reset    empties everything

  python3 scripts/fake_quickbooks.py --port 18791
"""
import argparse
import json
import re
import sys
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

ENTITIES = ("Account", "Vendor", "Customer", "JournalEntry", "Item", "Department", "Class", "PurchaseOrder", "Estimate",
            "Invoice", "Bill", "BillPayment", "Payment")
CREATE = {"account": "Account", "vendor": "Vendor", "customer": "Customer", "journalentry": "JournalEntry",
          "purchaseorder": "PurchaseOrder", "estimate": "Estimate", "invoice": "Invoice", "bill": "Bill",
          "billpayment": "BillPayment", "payment": "Payment", "item": "Item", "department": "Department",
          "creditmemo": "CreditMemo"}
LOCK = threading.Lock()
STATE: dict[str, object] = {}


def reset() -> None:
    STATE.clear()
    STATE.update({"books": {e: [] for e in ENTITIES}, "changes": [], "by_request": {}, "calls": [], "expire": False,
                  # Ids are unique per entity, as in QuickBooks: a new record must not take a seeded id (101 to 706).
                  "next_id": 1000, "lose": [], "lost": 0, "refused": 0})


def _rows(entity: str) -> list[dict[str, object]]:
    return STATE["books"].setdefault(entity, [])  # type: ignore[union-attr,return-value]


def _fault(message: str, code: str = "2010") -> tuple[int, dict[str, object]]:
    return 400, {"Fault": {"Error": [{"Message": message, "Detail": message, "code": code}],
                           "type": "ValidationFault"}}


def _stale() -> tuple[int, dict[str, object]]:
    return _fault("Stale Object Error: the record changed after it was read (SyncToken)", "5010")


def _missing() -> tuple[int, dict[str, object]]:
    return _fault("Object Not Found", "610")


def _balanced(lines: object) -> bool:
    if not isinstance(lines, list):
        return False
    total = Decimal(0)
    for line in lines:
        if not isinstance(line, dict):
            return False
        detail = line.get("JournalEntryLineDetail") or {}
        amount = Decimal(str(line.get("Amount", 0)))
        total += amount if isinstance(detail, dict) and detail.get("PostingType") == "Debit" else -amount
    return total == 0


def _conditions(sql: str) -> list[tuple[str, str, str]]:
    """The WHERE conditions of a query as (field, operator, value text), joined by AND: `Name = 'x' AND Active IN (...)`."""
    found = re.search(r"\sWHERE\s+(.*?)(?=\s+STARTPOSITION\s|\s+MAXRESULTS\s|$)", sql)
    if found is None:
        return []
    out: list[tuple[str, str, str]] = []
    for part in re.split(r"\s+AND\s+", found.group(1).strip()):
        clause = re.fullmatch(r"(\w+)\s+(=|IN)\s+(.+)", part)
        if clause is None:
            raise ValueError(part)
        out.append((clause[1], clause[2], clause[3]))
    return out


def _value(text: str) -> Any:
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        return [_value(x) for x in text[1:-1].split(",")]
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("\\'", "'")
    return {"true": True, "false": False}.get(text, text)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _matches(row: dict[str, object], field: str, op: str, wanted: Any) -> bool:
    have = _text(row.get(field))
    if op == "IN":
        return have in {_text(x) for x in wanted}
    return have == _text(wanted)


def query(sql: str) -> tuple[int, dict[str, object]]:
    parts = sql.split()
    entity = parts[3]
    try:
        wanted = [(field, op, _value(text)) for field, op, text in _conditions(sql)]
    except ValueError as e:
        return _fault(f"not a query the fake answers: {e}")
    rows = [row for row in _rows(entity) if all(_matches(row, f, op, value) for f, op, value in wanted)]
    if parts[1] == "COUNT(*)":
        return 200, {"QueryResponse": {"totalCount": len(rows)}}
    start = int(parts[parts.index("STARTPOSITION") + 1]) if "STARTPOSITION" in parts else 1
    size = int(parts[parts.index("MAXRESULTS") + 1]) if "MAXRESULTS" in parts else 100      # QuickBooks' default page
    page = rows[start - 1:start - 1 + size]
    return 200, {"QueryResponse": {entity: page, "startPosition": start, "maxResults": len(page)}}


def cdc(entities: str) -> tuple[int, dict[str, object]]:
    wanted = [e for e in entities.split(",") if e]
    block: dict[str, object] = {e: [] for e in wanted}
    for entity, row in STATE["changes"]:  # type: ignore[index,misc]
        if entity in wanted:
            block[entity].append(row)  # type: ignore[union-attr]
    # Rows of the entities this call did not ask for wait for their own call (the client reads one entity at a time).
    STATE["changes"] = [(e, r) for e, r in STATE["changes"] if e not in wanted]  # type: ignore[index,misc]
    return 200, {"CDCResponse": [{"QueryResponse": [block]}]}


def _lose(path: str) -> bool:
    """True when the next create at this path is one whose answer is lost (the write itself is kept)."""
    for rule in STATE["lose"]:  # type: ignore[attr-defined]
        if rule["path"] == path and rule["count"] > 0:
            rule["count"] -= 1
            STATE["lost"] = int(STATE["lost"]) + 1  # type: ignore[call-overload]
            return True
    return False


def _complete(made: dict[str, object], entity: str) -> None:
    """What QuickBooks adds to a record it keeps: an Id for each line that has none, and a total from the lines when
    the write leaves it out."""
    lines = made.get("Line")
    if not isinstance(lines, list):
        return
    for n, line in enumerate(lines, 1):
        if isinstance(line, dict) and "Id" not in line:
            line["Id"] = f"{made['Id']}-{n}"
    if entity in ("Bill", "Invoice", "Payment", "BillPayment") and "TotalAmt" not in made:
        made["TotalAmt"] = float(sum(Decimal(str(x.get("Amount", 0))) for x in lines if isinstance(x, dict)))


def create(path: str, body: dict[str, object], request_id: str, operation: str) -> tuple[int, dict[str, object]]:
    if request_id and request_id in STATE["by_request"]:  # type: ignore[operator]
        return STATE["by_request"][request_id]  # type: ignore[index,return-value]
    entity = CREATE.get(path)
    if entity is None:
        return 404, {"Fault": {"Error": [{"Message": "no such entity"}], "type": "ValidationFault"}}
    if operation in ("delete", "void"):
        answer = _remove(entity, str(body.get("Id", "")), str(body.get("SyncToken", "")), operation)
    elif body.get("Id"):
        answer = _update(entity, body)
    else:
        if entity == "JournalEntry" and not _balanced(body.get("Line")):
            return _fault("Journal entry does not balance")
        made = {**body, "Id": str(STATE["next_id"]), "SyncToken": "0"}
        STATE["next_id"] = int(made["Id"]) + 1  # type: ignore[call-overload]
        _complete(made, entity)
        _rows(entity).append(made)
        answer = (200, {entity: made})
    if request_id and answer[0] == 200:
        STATE["by_request"][request_id] = answer  # type: ignore[index]
    return answer


def _revision(row: dict[str, object]) -> str:
    return str(row.get("SyncToken", "0"))


def _next_revision(row: dict[str, object]) -> str:
    return str(int(_revision(row)) + 1)


def _update(entity: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    """A write that names a record's Id updates it (QuickBooks does not make a second one). Its SyncToken must be the
    record's, or the write is stale; a sparse write sets only the fields it names, and the record moves to its next
    SyncToken."""
    for row in _rows(entity):
        if str(row.get("Id")) != str(body["Id"]):
            continue
        if _revision(row) != str(body.get("SyncToken", "")):
            return _stale()
        row.update({k: v for k, v in body.items() if k not in ("Id", "SyncToken", "sparse")})
        row["SyncToken"] = _next_revision(row)
        return 200, {entity: dict(row)}
    return _missing()


def _remove(entity: str, entry_id: str, token: str, operation: str) -> tuple[int, dict[str, object]]:
    """A delete removes the record it names (and answers it as deleted). A void keeps the record with a zero total, as
    QuickBooks does. Either needs the record's current SyncToken."""
    rows = _rows(entity)
    for row in rows:
        if str(row.get("Id")) != entry_id:
            continue
        if _revision(row) != token:
            return _stale()
        if operation == "void":
            row["TotalAmt"] = 0
            row["SyncToken"] = _next_revision(row)
            return 200, {entity: dict(row)}
        rows.remove(row)
        return 200, {entity: {"Id": entry_id, "status": "Deleted"}}
    return _missing()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, body: object) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length") or 0)
        found = json.loads(self.rfile.read(size) or b"{}") if size else {}
        return found if isinstance(found, dict) else {}

    def _route(self, method: str) -> None:
        url = urlsplit(self.path)
        query_string = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = self._body()
        with LOCK:
            if url.path.startswith("/_fake/"):
                return self._control(method, url.path, body)
            STATE["calls"].append({"method": method, "path": url.path, "query": query_string, "body": body})  # type: ignore[attr-defined]
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                return self._send(401, {"Fault": {"Error": [{"Message": "no token"}]}})
            if STATE["expire"]:
                STATE["expire"] = False
                STATE["refused"] = int(STATE["refused"]) + 1  # type: ignore[call-overload]
                return self._send(401, {"Fault": {"Error": [{"Message": "token expired"}]}})
            parts = url.path.strip("/").split("/")
            if len(parts) < 4 or parts[0] != "v3" or parts[1] != "company":
                return self._send(404, {})
            if method == "GET" and parts[3] == "query":
                return self._send(*query(query_string.get("query", "")))
            if method == "GET" and parts[3] == "cdc":
                return self._send(*cdc(query_string.get("entities", "")))
            if method == "POST":
                answer = create(parts[3], body, query_string.get("requestid", ""), query_string.get("operation", ""))
                if _lose(parts[3]):
                    # The write is kept above; its answer never leaves (the caller sees the connection close).
                    self.close_connection = True
                    return None
                return self._send(*answer)
            self._send(404, {})

    def _control(self, method: str, path: str, body: dict[str, object]) -> None:
        if path == "/_fake/calls":
            return self._send(200, {"calls": STATE["calls"], "lost": STATE["lost"], "refused": STATE["refused"]})
        if path == "/_fake/reset":
            reset()
            return self._send(200, {"ok": True})
        if path == "/_fake/expire":
            STATE["expire"] = True
            return self._send(200, {"ok": True})
        if path == "/_fake/lose":
            STATE["lose"].append({"path": str(body.get("path", "")), "count": int(body.get("count", 1))})  # type: ignore[attr-defined]
            return self._send(200, {"ok": True})
        if path == "/_fake/seed" or path == "/_fake/change":
            for entity, rows in body.items():
                if entity and isinstance(rows, list):
                    for row in rows:
                        if path == "/_fake/seed":
                            _rows(entity).append(dict(row))
                        else:
                            STATE["changes"].append((entity, dict(row)))  # type: ignore[attr-defined]
            return self._send(200, {"ok": True})
        self._send(404, {})

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=18791)
    args = parser.parse_args()
    reset()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake QuickBooks on http://127.0.0.1:{args.port}", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
