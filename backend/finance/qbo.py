"""The QuickBooks Online adapter's HTTP client (spec §10): plain calls to Intuit's API with the connection's access token,
read at use time and never logged. Only the books worker calls it, outside any transaction (finance/books_sync.py).

Reads page with QuickBooks' STARTPOSITION and MAXRESULTS (1-based, like the backfill cursor); the change feed is CDC.
Writes carry a RequestId derived from the outbox key, so a retry after a lost answer is the same request to QuickBooks.
A 429 waits out Retry-After a few times; a 401 gets one new token from HQ and the call is tried again; a redirect is
never followed (the token stays with the host it was meant for). Every call returns a Read or a Sent: it never raises
for an answer from QuickBooks."""
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import books_link
from .core import mirror as M
from .core import qbo as Q
from .mirror import Read, Record, Sent

PRODUCTION = "https://quickbooks.api.intuit.com"
SANDBOX = "https://sandbox-quickbooks.api.intuit.com"
BASE_ENV = "TICO_QUICKBOOKS_BASE"     # a developer's override: the sandbox, or a local fake (scripts/fake_quickbooks.py)
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "sandbox-quickbooks.api.intuit.com"})
NAMESPACE = M.REFERENCE_NAMESPACE      # RequestIds are uuid5 of the outbox key in this namespace (core/mirror.reference)
TIMEOUT = 20
TRIES_429 = 3
MAX_WAIT = 30.0
MAX_TEXT = 300
PAGE = 1000                            # QuickBooks' most rows in one query or one change-feed entity
MAX_PAGES = 100                        # a list of items larger than this is refused rather than read forever
# The transactions a retry looks for first, by entity: a cut-off call may already have landed in QuickBooks.
TRANSACTIONS = {"bill.create": "Bill", "invoice.create": "Invoice", "journal.create": "JournalEntry",
                "purchaseorder.create": "PurchaseOrder", "estimate.create": "Estimate",
                "payment.bill": "BillPayment", "payment.invoice": "Payment", "writeoff.create": "CreditMemo",
                "credit.apply": "Payment"}
# A shared record found by its name: the entity it is read from, and the field its name is in.
NAMED = {"item.create": ("Item", "Name"), "location.create": ("Department", "Name"),
         "project.create": ("Customer", "DisplayName")}
# The provider kind a write's record is kept under when it differs from the QuickBooks entity (a job is a Project).
SENT_AS = {"project.create": "Project", "project.update": "Project"}


def _marker(key: str) -> str:
    """The Tico reference a write carries in its private note: the RequestId of its outbox key, so a retry finds it."""
    return f"[tico:{M.reference(key)}]"


def _where(entity: str) -> str:
    """The filter that reads inactive records too, for the entities that have an active flag."""
    return " WHERE Active IN (true,false)" if entity in Q.ACTIVE_FILTERED else ""


def _quote(value: object) -> str:
    """A string as a QuickBooks query takes it, with its quotes escaped."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")
log = logging.getLogger(__name__)
_warned = [False]


def _no_body() -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class Reply:
    status: int                       # 0: unreachable or timed out (the write may or may not have landed)
    body: dict[str, Any] = field(default_factory=_no_body)
    retry_after: float = 0.0


Transport = Callable[[str, str, Mapping[str, str], bytes | None], Reply]


def base_url() -> str:
    """The API base: TICO_QUICKBOOKS_BASE when it names a local host or the sandbox, else Intuit's own. A value naming any
    other host is ignored (one warning per process): the connection's token never goes anywhere else."""
    raw = os.environ.get(BASE_ENV, "").strip().rstrip("/")
    if not raw:
        return PRODUCTION
    host = urllib.parse.urlsplit(raw).hostname or ""
    if urllib.parse.urlsplit(raw).scheme in ("http", "https") and host in OVERRIDE_HOSTS:
        return raw
    if not _warned[0]:
        _warned[0] = True
        log.warning("%s names a host that is not a local fake or the sandbox; using QuickBooks' own address", BASE_ENV)
    return PRODUCTION


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _urllib(method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> Reply:
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return _reply(response.status, response.read(), response.headers.get("Retry-After"))
    except urllib.error.HTTPError as e:
        return _reply(e.code, e.read(), e.headers.get("Retry-After") if e.headers else None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return Reply(0)


def _reply(status: int, raw: bytes, retry: str | None) -> Reply:
    body: object
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    try:
        wait = float(retry or 0)
    except ValueError:
        wait = 0.0
    answer: dict[str, Any] = Q.as_map(body) or {}
    return Reply(status, answer, wait)


def _fault(body: Mapping[str, Any], status: int) -> str:
    """QuickBooks' own message for a refusal (Fault.Error[].Message), short; the status when it gives none."""
    fault: dict[str, Any] = Q.as_map(body.get("Fault")) or {}
    errors = Q.as_list(fault.get("Error"))
    first: dict[str, Any] = (Q.as_map(errors[0]) or {}) if errors else {}
    text = " ".join(str(first.get(k) or "") for k in ("Message", "Detail")).strip()
    return text[:MAX_TEXT] or f"QuickBooks answered {status}"


def _iso(value: str) -> str:
    """A change-feed time as QuickBooks reads it: whole seconds, UTC."""
    return M.parse(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class QuickBooks:
    """The books of one QuickBooks company (its realm). `tokens(force)` returns a usable access token, refreshed through
    HQ when `force` is set (after a 401); it raises books_link.BrokerFailed when HQ cannot."""

    entities: tuple[str, ...] = tuple(Q.PAGES)
    feeds: tuple[str, ...] = Q.ENTITIES

    def __init__(self, realm: str, tokens: Callable[[bool], str], *, base: str | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.realm, self.tokens = realm, tokens
        self.base = (base or base_url()).rstrip("/")
        self.transport = transport or _urllib
        self.sleep = sleep
        self._items: dict[str, dict[str, str]] | None = None      # one read of the items per pass, when a document needs it

    def _call(self, method: str, path: str, *, query: Mapping[str, str] | None = None,
              body: Mapping[str, Any] | None = None, request_id: str = "") -> Reply:
        params = {**(query or {}), "minorversion": Q.MINOR_VERSION}
        if request_id:
            params["requestid"] = request_id
        url = f"{self.base}/v3/company/{urllib.parse.quote(self.realm, safe='')}/{path}?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        try:
            token = self.tokens(False)
        except books_link.BrokerFailed:
            return Reply(0)
        refreshed = False
        reply = Reply(0)
        tries = 0
        while True:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                       "Content-Type": "application/json"}
            reply = self.transport(method, url, headers, data)
            if reply.status == 401 and not refreshed:
                refreshed = True
                try:
                    token = self.tokens(True)
                except books_link.BrokerFailed:
                    return Reply(0)
                continue
            if reply.status == 429 and tries < TRIES_429:
                tries += 1
                self.sleep(min(max(reply.retry_after, 1.0), MAX_WAIT))
                continue
            return reply

    def _count(self, entity: str) -> int | str:
        reply = self._call("GET", "query", query={"query": f"SELECT COUNT(*) FROM {entity}{_where(entity)}"})
        if reply.status != 200:
            return _fault(reply.body, reply.status)
        found: dict[str, Any] = Q.as_map(reply.body.get("QueryResponse")) or {}
        total = found.get("totalCount")
        return int(total) if isinstance(total, int) else 0

    def _item_accounts(self) -> dict[str, dict[str, str]] | str:
        """Each item's income and expense account, read once per pass (every page of items, active or not); a line that
        names an item posts to its account."""
        if self._items is None:
            rows: list[object] = []
            for page in range(MAX_PAGES):
                start = page * PAGE + 1
                reply = self._call("GET", "query", query={"query": (
                    f"SELECT * FROM Item{_where('Item')} STARTPOSITION {start} MAXRESULTS {PAGE}")})
                if reply.status != 200:
                    return _fault(reply.body, reply.status)
                found: dict[str, Any] = Q.as_map(reply.body.get("QueryResponse")) or {}
                got = Q.as_list(found.get("Item"))
                rows += got
                if len(got) < PAGE:
                    self._items = Q.item_accounts(rows)
                    return self._items
            return f"more than {MAX_PAGES * PAGE} items: the item list is too long to read"
        return self._items

    def _query(self, entity: str, start: int, size: int) -> Read:
        sql = f"SELECT * FROM {entity}{_where(entity)} STARTPOSITION {start} MAXRESULTS {size}"
        reply = self._call("GET", "query", query={"query": sql})
        if reply.status != 200:
            return Read(False, error=_fault(reply.body, reply.status))
        found: dict[str, Any] = Q.as_map(reply.body.get("QueryResponse")) or {}
        rows = Q.as_list(found.get(entity))
        items: dict[str, dict[str, str]] = {}
        if entity in Q.DOCUMENTS or entity in Q.POSTED or entity in Q.ORDERS:
            known = self._item_accounts()
            if isinstance(known, str):
                return Read(False, error=known)
            items = known
        return Read(True, tuple(Q.records(entity, rows, items)), seen=len(rows))

    def _across(self, names: tuple[str, ...], start: int, size: int) -> Read:
        """A page of several QuickBooks entities read as one list in order (the first's rows, then the next's), counted
        from `start`: the books' backfill cursor counts all of them together."""
        if len(names) == 1:
            return self._query(names[0], start, size)
        counts: list[int] = []
        for name in names:
            count = self._count(name)
            if isinstance(count, str):
                return Read(False, error=count)
            counts.append(count)
        out: list[Record] = []
        seen, offset, left = 0, start - 1, size
        for name, count in zip(names, counts, strict=True):
            if left <= 0:
                break
            if offset >= count:
                offset -= count
                continue
            take = min(left, count - offset)
            got = self._query(name, offset + 1, take)
            if not got.ok:
                return got
            out += got.records
            seen += got.seen
            left -= take
            offset = 0
        return Read(True, tuple(out), seen=seen)

    def page(self, entity: str, start: int, size: int) -> Read:
        names = Q.PAGES.get(entity)
        if names is None:
            return Read(False, error=f"no such entity: {entity}")
        return self._across(names, start, size)

    def changed(self, since: Mapping[str, str]) -> Read:
        """The change feed, one answer per entity: each has its own cap, so each entity's cursor moves on its own."""
        answers: list[tuple[str, Any]] = []
        for entity in Q.ENTITIES:
            if entity not in since:
                continue
            reply = self._call("GET", "cdc", query={"entities": entity, "changedSince": _iso(since[entity])})
            if reply.status != 200:
                return Read(False, error=_fault(reply.body, reply.status))
            answers.append((entity, reply.body))
        known = self._item_accounts()
        if isinstance(known, str):
            return Read(False, error=known)
        out: list[Any] = []
        through: list[tuple[str, str]] = []
        for entity, body in answers:
            out += Q.change_records(body, known)
            cut = Q.change_through(body, entity)
            if cut:
                through.append((entity, cut))
        return Read(True, tuple(out), through=tuple(through))

    def _write_off_item(self, account_key: str, key: str) -> str | Sent:
        """The item a write-off is billed by: found by name, else created once (its RequestId comes from the write-off's
        key, so a retry does not make a second). One that sells to another account is refused, since the write-off
        would post there."""
        account = account_key.partition(":")[2]
        found = self._call("GET", "query", query={"query": f"SELECT * FROM Item WHERE Name = '{Q.WRITE_OFF_ITEM}'"})
        if found.status == 0:
            return Sent(False, error="QuickBooks did not answer")
        if found.status != 200:
            return Sent(False, error=_fault(found.body, found.status))
        known = Q.item_accounts(Q.as_list((Q.as_map(found.body.get("QueryResponse")) or {}).get("Item")))
        for sells in known.values():
            if sells["income"] != account:
                return Sent(False, final=True, error=(
                    f"the {Q.WRITE_OFF_ITEM} item already sells to another account; point the write-off at that "
                    "account or change the item in QuickBooks"))
        if known:
            return next(iter(known))
        created = self._call("POST", "item", body=Q.item_body(Q.WRITE_OFF_ITEM, account),
                             request_id=str(uuid.uuid5(NAMESPACE, f"{key}:item")))
        if created.status == 0:
            return Sent(False, error="QuickBooks did not answer")
        made: dict[str, Any] = Q.as_map(created.body.get("Item")) or {}
        if 200 <= created.status < 300 and made:
            return str(made.get("Id") or "")
        final = created.status == 400 and "Fault" in created.body
        return Sent(False, error=_fault(created.body, created.status), final=final)

    def find(self, op: str, body: Mapping[str, Any], key: str) -> Sent | None:
        """Whether QuickBooks already holds the write sent under `key`, for a retry after a cut-off call. A transaction is
        found by the Tico reference in its private note, on its day; an account or party by its name. None: it is not
        there. A lookup that cannot tell comes back final, so the write is marked unknown and is not sent again."""
        if op in TRANSACTIONS:
            entity, day = TRANSACTIONS[op], str(body.get("day") or body.get("date") or "")
            if not day:
                return Sent(False, error="UNKNOWN: no date to find the write by", final=True, unknown=True)
            where, mark = f"TxnDate = '{day}'", _marker(key)
        elif op == "account.create":
            entity, where, mark = "Account", f"Name = '{_quote(body['name'])}'", ""
        elif op == "party.create":
            entity = "Vendor" if str(body.get("kind")) == "vendor" else "Customer"
            where, mark = f"DisplayName = '{_quote(body['name'])}'", ""
        elif op in NAMED:
            entity, field = NAMED[op]
            where, mark = f"{field} = '{_quote(body['name'])}'" + (" AND Job = true" if op == "project.create" else ""), ""
        else:
            return None
        reply = self._call("GET", "query", query={"query": f"SELECT * FROM {entity} WHERE {where} MAXRESULTS 1000"})
        if reply.status in (0, 429) or reply.status >= 500:
            return Sent(False, error="QuickBooks did not answer the lookup")
        if reply.status != 200:
            return Sent(False, error=f"UNKNOWN: {_fault(reply.body, reply.status)}"[:MAX_TEXT], final=True,
                        unknown=True)
        rows = Q.as_list((Q.as_map(reply.body.get("QueryResponse")) or {}).get(entity))
        for raw in rows:
            row = Q.as_map(raw) or {}
            if not mark or mark in str(row.get("PrivateNote") or ""):
                return Sent(True, kind=SENT_AS.get(op, entity), external_id=str(row.get("Id") or ""),
                            revision=str(row.get("SyncToken") or ""),
                            lines=Q.line_ids(row) if op == "bill.create" else ())
        if len(rows) >= 1000:
            return Sent(False, error="UNKNOWN: too many writes that day to tell", final=True, unknown=True)
        return None

    def send(self, op: str, body: Mapping[str, Any], key: str) -> Sent:
        """One write, idempotent by its RequestId. Only a refusal from QuickBooks itself is final."""
        return self._send(op, body, key, key)

    def _send(self, op: str, body: Mapping[str, Any], key: str, request_key: str) -> Sent:
        """A write whose marker comes from `key` (what the books find it by) and whose RequestId comes from
        `request_key`, so a retry under a new request keeps the same marker."""
        request_id = str(uuid.uuid5(NAMESPACE, request_key))
        items: dict[str, dict[str, str]] = {}
        if op in ("invoice.create", "estimate.create"):
            found = self._item_accounts()
            if isinstance(found, str):
                return Sent(False, error=found)
            items = found
        body = {**body, "marker": _marker(key)}
        if op == "writeoff.create":
            item = self._write_off_item(str(body["account"]), key)
            if isinstance(item, Sent):
                return item
            body = {**body, "item": item}
        try:
            kind, path, payload, query = _write_of(op, body, items)
        except (KeyError, ValueError, TypeError) as e:
            return Sent(False, error=f"not a QuickBooks write: {e}"[:MAX_TEXT], final=True)
        if kind is None:
            return Sent(False, error=f"QuickBooks has no write for {op}", final=True)
        reply = self._call("POST", path, query=query, body=payload, request_id=request_id)
        if reply.status == 0:
            return Sent(False, error="QuickBooks did not answer")
        made: dict[str, Any] = Q.as_map(reply.body.get(kind)) or {}
        if 200 <= reply.status < 300 and made:
            return Sent(True, kind=SENT_AS.get(op, kind), external_id=str(made.get("Id") or ""),
                        revision=str(made.get("SyncToken") or ""),
                        lines=Q.line_ids(made) if op == "bill.create" else ())
        final = reply.status == 400 and "Fault" in reply.body      # a validation refusal: a retry says the same thing
        if final and op == "invoice.create" and body.get("linked_estimate"):
            # The estimate link is best effort: a refusal sends the invoice again without it, under its own request.
            return self._send(op, {k: v for k, v in body.items() if k != "linked_estimate"}, key, f"{key}:unlinked")
        return Sent(False, error=_fault(reply.body, reply.status), final=final)


def _write_of(op: str, body: Mapping[str, Any],
              items: Mapping[str, Mapping[str, str]]) -> tuple[str | None, str, dict[str, Any], dict[str, str]]:
    """The record kind, path, body and query one outbox operation sends. `items` maps each item to its accounts."""
    if op == "account.create":
        return "Account", "account", Q.account_body(body), {}
    if op == "party.create":
        vendor = str(body.get("kind")) == "vendor"
        return ("Vendor", "vendor", Q.party_body(body), {}) if vendor else ("Customer", "customer", Q.party_body(body), {})
    if op == "journal.create":
        return "JournalEntry", "journalentry", Q.journal_body(body), {}
    if op == "journal.delete":
        return "JournalEntry", "journalentry", Q.delete_body(str(body["ext"]), str(body["revision"])), {
            "operation": "delete"}
    if op == "bill.create":
        return "Bill", "bill", Q.bill_body(body), {}
    if op == "invoice.create":
        income = Q.income_items(items)
        return "Invoice", "invoice", Q.invoice_body(body, income), {}
    if op == "purchaseorder.create":
        return "PurchaseOrder", "purchaseorder", Q.purchase_order_body(body), {}
    if op == "estimate.create":
        return "Estimate", "estimate", Q.estimate_body(body, Q.income_items(items)), {}
    if op == "bill.void":
        return "Bill", "bill", Q.delete_body(str(body["ext"]), str(body["revision"])), {"operation": "delete"}
    if op == "invoice.void":
        return "Invoice", "invoice", Q.delete_body(str(body["ext"]), str(body["revision"])), {"operation": "void"}
    if op in ("item.create", "item.update"):
        return "Item", "item", Q.item_write(body, update=op.endswith("update")), {}
    if op in ("location.create", "location.update"):
        return "Department", "department", Q.location_write(body, update=op.endswith("update")), {}
    if op in ("project.create", "project.update"):
        return "Customer", "customer", Q.job_write(body, update=op.endswith("update")), {}
    if op == "writeoff.create":
        return "CreditMemo", "creditmemo", Q.writeoff_body(body), {}
    if op == "credit.apply":
        return "Payment", "payment", Q.credit_apply_body(body), {}
    if op == "payment.bill":
        return "BillPayment", "billpayment", Q.bill_payment_body(body), {}
    if op == "payment.invoice":
        return "Payment", "payment", Q.payment_body(body), {}
    return None, "", {}, {}
