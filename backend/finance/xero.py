"""The Xero adapter's HTTP client (spec §10): plain calls to Xero's accounting API with the connection's access token and
tenant, read at use time and never logged. Only the books worker calls it, outside any transaction (finance/books_sync.py).

Lists page with Xero's `page` (100 rows a page, 1-based), newest first (UpdatedDateUTC DESC): an edit during a backfill
moves a row to the front, so a page is re-read rather than skipped. A backfill position is a row count, so a read of the
mirror's page of 1000 rows is ten of Xero's pages. Accounts and bank transfers are one whole list each: Xero pages neither,
and a transfer has no change time, so every pass reads them all. The change feed reads each paged list with
If-Modified-Since every CHANGE_EVERY seconds, since Xero sends no webhook for every change. Writes carry an Idempotency-Key
derived from the outbox key, so a retry after a lost answer is the same request to Xero; a retry looks first for the write
it may have made (find), by the Tico marker in its Narration or Reference, or by its name, through every page Xero holds.
A lookup that cannot finish is unknown and the write is not sent again. A 429 waits out Retry-After within a small budget
per pass; past it the pass stops, and the books wait out Retry-After across passes (`hold`). A 401 gets one new token from
HQ and the call is tried again; a redirect is never followed. Every call returns a Read or a Sent: it never raises for an
answer from Xero."""
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
from datetime import timedelta
from decimal import Decimal
from email.utils import format_datetime
from typing import Any

from . import books_link
from .core import ledger as L
from .core import mirror as M
from .core import xero as X
from .core.qbo import as_list, as_map
from .mirror import Read, Record, Sent

BASE = "https://api.xero.com/api.xro/2.0"
BASE_ENV = "TICO_XERO_BASE"          # a developer's override: a local fake (scripts/fake_xero.py) and nothing else
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
NAMESPACE = uuid.UUID("3b8e2f7a-6c1d-4f5e-9a40-7d2c1b9e6f10")   # Idempotency-Keys are uuid5 of the outbox key here
PAGE = 100                            # Xero returns 100 rows a page; a shorter page is the last
MAX_PAGES = 200                       # a change window longer than this (20,000 rows) is not read; its pass reports it
FIND_PAGES = 50                       # a lookup for a write reads this many pages (5,000 rows); past them it cannot tell
CHANGE_EVERY = 240                    # seconds between change-feed reads: a change is seen within five minutes (§10)
ORDER = "UpdatedDateUTC DESC"         # newest first, so rows edited during a read move to the front (re-read, not skipped)
CHANGE_ORDER = "UpdatedDateUTC ASC"   # oldest first: a list cut at the cap is complete to its last row read
EXTRA = {"parties": {"includeArchived": "true"}, "bank_transfers": {"includeDeleted": "true"}}
UNPAGED = ("accounts", "bank_transfers")   # Xero returns these whole, with no page parameter
ENDPOINTS = {"accounts": "Accounts", "parties": "Contacts", "invoices": "Invoices", "credit_notes": "CreditNotes",
             "payments": "Payments", "manual_journals": "ManualJournals", "bank_transactions": "BankTransactions",
             "bank_transfers": "BankTransfers", "overpayments": "Overpayments", "prepayments": "Prepayments"}
WRAPPER = {"Account": "Accounts", "Contact": "Contacts", "Invoice": "Invoices", "Bill": "Invoices",
           "Payment": "Payments", "BillPayment": "Payments", "CreditMemo": "CreditNotes",
           "ManualJournal": "ManualJournals", "BankTransaction": "BankTransactions"}     # a write's kind -> its list
ID_KEY = {"Account": "AccountID", "Contact": "ContactID", "Invoice": "InvoiceID", "Bill": "InvoiceID",
          "Payment": "PaymentID", "BillPayment": "PaymentID", "CreditMemo": "CreditNoteID",
          "ManualJournal": "ManualJournalID", "BankTransaction": "BankTransactionID"}
TIMEOUT = 20
TRIES_429 = 3
PASS_WAIT = 5.0                       # seconds one pass may sleep on rate limits in all; past it the pass stops
HOLD_DEFAULT = 60.0                   # seconds the books wait after a rate limit that gave no Retry-After
MAX_TEXT = 300
log = logging.getLogger(__name__)
_warned = [False]


def _no_body() -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class Reply:
    status: int                       # 0: unreachable, timed out, or no token (the write may or may not have landed)
    body: dict[str, Any] = field(default_factory=_no_body)
    retry_after: float = 0.0
    note: str = ""                    # why there is no answer, for a status 0


Transport = Callable[[str, str, Mapping[str, str], bytes | None], Reply]


class _Stop(Exception):
    """A write that another write depends on could not be made: its answer is the answer of the write that depends on it."""

    def __init__(self, sent: Sent) -> None:
        super().__init__(sent.error)
        self.sent = sent


def base_url() -> str:
    """The API base: TICO_XERO_BASE when it names a local host, else Xero's own. Any other host is ignored (one warning
    per process): the connection's token never goes anywhere else."""
    raw = os.environ.get(BASE_ENV, "").strip().rstrip("/")
    if not raw:
        return BASE
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme in ("http", "https") and parts.hostname in OVERRIDE_HOSTS:
        return raw
    if not _warned[0]:
        _warned[0] = True
        log.warning("%s names a host that is not a local fake; using Xero's own address", BASE_ENV)
    return BASE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _reply(status: int, raw: bytes, retry: str | None) -> Reply:
    """The answer as JSON with decimals kept exact: a float would lose a cent in the books."""
    try:
        body: object = json.loads(raw or b"{}", parse_float=Decimal)
    except ValueError:
        body = {}
    try:
        wait = float(retry or 0)
    except ValueError:
        wait = 0.0
    return Reply(status, as_map(body) or {}, wait)


def _urllib(method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> Reply:
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return _reply(response.status, response.read(), response.headers.get("Retry-After"))
    except urllib.error.HTTPError as e:
        return _reply(e.code, e.read(), e.headers.get("Retry-After") if e.headers else None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return Reply(0, note="Xero did not answer")


def _fault(reply: Reply) -> str:
    """Xero's own words for a refusal (Message, then the first validation message), short; the status when it gives none."""
    text = str(reply.body.get("Message") or "")
    elements = as_list(reply.body.get("Elements"))
    first = as_map(elements[0]) if elements else None
    errors = as_list((first or {}).get("ValidationErrors"))
    detail = as_map(errors[0]) if errors else None
    if detail and detail.get("Message"):
        text = f"{text} {detail['Message']}".strip()
    return text[:MAX_TEXT] or f"Xero answered {reply.status}"


def _lookup_failure(reply: Reply) -> Sent:
    """A lookup that got no list back: a retry asks again, and a refusal says the write's fate is unknown."""
    if reply.status in (0, 429) or reply.status >= 500:
        return Sent(False, error="Xero did not answer the lookup")
    return Sent(False, error=f"UNKNOWN: {_fault(reply)}"[:MAX_TEXT], final=True)


def _marker(key: str) -> str:
    """The Tico reference a write carries in its Narration or Reference: the outbox key's reference, so a retry finds the
    write and the answer binds to the key."""
    return f"[tico:{M.reference(key)}]"


def _idem(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, key))


def _quote(ident: str) -> str:
    return urllib.parse.quote(ident, safe="")


def _id_of(ext: object, kind: str) -> str:
    """The provider's id in a books key such as `Invoice:<id>`, when the key is of that kind; empty otherwise."""
    head, _, ident = str(ext or "").partition(":")
    return ident if head == kind else ""


def _live(entity: str, row: Mapping[str, Any]) -> bool:
    """Whether a row is one a lookup may bind a write to: not deleted, and not an archived contact."""
    if row.get("Status") == "DELETED":
        return False
    return not (entity == "parties" and row.get("ContactStatus") == "ARCHIVED")


def _has_counterparty(body: Mapping[str, Any]) -> bool:
    """Whether a bank row's category names its counterparty, which Xero requires on a bank transaction."""
    for raw in as_list(body.get("lines")):
        line = as_map(raw) or {}
        if line.get("role") != L.MONEY_ROLE and line.get("party"):
            return True
    return False


def _query(entity: str, page: int, order: str = ORDER) -> dict[str, str]:
    """The query a list of an entity is read with: a page, in `order`. A whole list takes only its own extras."""
    if entity in UNPAGED:
        return dict(EXTRA.get(entity, {}))
    return {"page": str(page), "order": order, **EXTRA.get(entity, {})}


def _write_of(op: str, body: Mapping[str, Any]) -> tuple[str | None, str, str, Any]:
    """The record kind, method, path and body one outbox operation sends. An account is sent as the account itself; a
    document, payment or credit note as the list it is written into."""
    if op == "account.create":
        return "Account", "PUT", "Accounts", X.account_body(body)
    if op == "party.create":
        return "Contact", "PUT", "Contacts", {"Contacts": [X.party_body(body)]}
    if op == "journal.create":
        if body.get("feed"):
            return "BankTransaction", "PUT", "BankTransactions", {"BankTransactions": [X.bank_body(body)]}
        return "ManualJournal", "PUT", "ManualJournals", {"ManualJournals": [X.journal_body(body)]}
    if op == "journal.delete":
        ident = _id_of(body["ext"], "ManualJournal")
        if not ident:
            raise ValueError(f"Xero voids manual journals only, not {body['ext']}")
        return "ManualJournal", "POST", f"ManualJournals/{_quote(ident)}", X.void_body(ident)
    if op in ("bill.create", "invoice.create"):
        kind = "Bill" if op == "bill.create" else "Invoice"
        return kind, "PUT", "Invoices", X.document_body(body, kind)
    if op in ("bill.void", "invoice.void"):
        kind = "Bill" if op == "bill.void" else "Invoice"
        ident = _id_of(body["ext"], kind)
        if not ident:
            raise ValueError(f"no {kind} to void in {body['ext']}")
        return kind, "POST", f"Invoices/{_quote(ident)}", X.void_document_body(ident)
    if op in ("payment.invoice", "payment.bill"):
        kind = "Payment" if op == "payment.invoice" else "BillPayment"
        return kind, "PUT", "Payments", X.payment_body(body)
    if op == "writeoff.create":
        return "CreditMemo", "PUT", "CreditNotes", X.credit_note_body(body)
    return None, "", "", {}


class Xero:
    """The books of one Xero organisation (its tenant). `tokens(force)` returns a usable access token, refreshed through
    HQ when `force` is set (after a 401); it raises books_link.BrokerFailed when HQ cannot. One instance is one pass: a
    rate limit that outlasts the pass's budget stops every later call in it, and `hold` says how long the books wait."""

    entities: tuple[str, ...] = M.BOOK_ENTITIES["xero"]
    # One cursor per entity with a change time (bills come from the invoices list); bank transfers have none.
    feeds: tuple[str, ...] = ("accounts", "parties", "invoices", "credit_notes", "payments", "overpayments",
                              "prepayments", "bank_transactions", "manual_journals")

    def __init__(self, tenant: str, tokens: Callable[[bool], str], *, base: str | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.tenant, self.tokens = tenant, tokens
        self.base = (base or base_url()).rstrip("/")
        self.transport = transport or _urllib
        self.sleep = sleep
        self.hold = 0.0                   # seconds the books wait before the next pass: the Retry-After that stopped this one
        self._roles: dict[str, str] | None = None
        self._contact = ""
        self._waited = 0.0
        self._held = False

    def _call(self, method: str, path: str, *, query: Mapping[str, str] | None = None,
              body: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None) -> Reply:
        if self._held:
            return Reply(429, {"Message": "Xero is rate limiting; this pass stops and the next one resumes"}, self.hold)
        url = f"{self.base}/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = json.dumps(body).encode() if body is not None else None
        try:
            token = self.tokens(False)
        except books_link.BrokerFailed:
            return Reply(0, note="HQ could not give the Xero access token")
        refreshed = False
        tries = 0
        while True:
            head = {"Authorization": f"Bearer {token}", "xero-tenant-id": self.tenant, "Accept": "application/json",
                    **(headers or {})}
            if data is not None:
                head["Content-Type"] = "application/json"
            reply = self.transport(method, url, head, data)
            if reply.status == 401 and not refreshed:
                refreshed = True
                try:
                    token = self.tokens(True)
                except books_link.BrokerFailed:
                    return Reply(0, note="HQ could not refresh the Xero access token")
                continue
            if reply.status == 429:
                wait = max(reply.retry_after, 1.0)
                if tries < TRIES_429 and self._waited + wait <= PASS_WAIT:
                    tries += 1
                    self._waited += wait
                    self.sleep(wait)
                    continue
                self._held = True
                self.hold = max(self.hold, reply.retry_after or HOLD_DEFAULT)
            return reply

    def _rows(self, entity: str, page: int, since: str = "", order: str = ORDER) -> tuple[list[dict[str, Any]], Reply]:
        """One list's rows as Xero gave them, and the answer; no rows when the answer is not a list."""
        name = ENDPOINTS[entity]
        reply = self._call("GET", name, query=_query(entity, page, order),
                           headers={"If-Modified-Since": since} if since else None)
        if reply.status != 200:
            return [], reply
        return [as_map(raw) or {} for raw in as_list(reply.body.get(name))], reply

    def _taken(self, entity: str, rows: list[dict[str, Any]]) -> Read:
        """The records of rows the books take in; the rows are all counted, drafts included."""
        try:
            return Read(True, tuple(X.records(entity, rows, self.system)), seen=len(rows))
        except ValueError as e:
            return Read(False, error=f"Xero's {ENDPOINTS[entity]} could not be read: {e}"[:MAX_TEXT])

    def _read(self, entity: str, page: int, since: str = "") -> Read:
        rows, reply = self._rows(entity, page, since)
        if reply.status != 200:
            return Read(False, error=_fault(reply) if reply.status else reply.note)
        return self._taken(entity, rows)

    def system(self, name: str) -> str:
        """The key of the account Xero holds under a system role (ROUNDING, DEBTORS, CREDITORS, GST), read once per pass.
        Empty when Xero holds none under that role, so a record that needs it waits as unmapped."""
        if self._roles is None:
            rows, reply = self._rows("accounts", 1)
            if reply.status != 200:
                raise ValueError(_fault(reply) if reply.status else reply.note)
            roles: dict[str, str] = {}
            for row in rows:
                role, ident = str(row.get("SystemAccount") or ""), str(row.get("AccountID") or "")
                if role and ident:
                    roles.setdefault(role, f"Account:{ident}")
            self._roles = roles
        return self._roles.get(name, "")

    def page(self, entity: str, start: int, size: int) -> Read:
        """`size` rows from position `start` (1-based), read as the pages of 100 Xero holds that cover them. A whole list
        (accounts, bank transfers) is read at its first position and has nothing after it."""
        if entity in UNPAGED:
            return self._read(entity, 1) if start <= 1 else Read(True, (), seen=0)
        skip = (start - 1) % PAGE
        number = (start - 1) // PAGE + 1
        rows: list[dict[str, Any]] = []
        while len(rows) < skip + size:
            got, reply = self._rows(entity, number)
            if reply.status != 200:
                return Read(False, error=_fault(reply) if reply.status else reply.note)
            rows += got
            if len(got) < PAGE:
                break
            number += 1
        return self._taken(entity, rows[skip:skip + size])

    def changed(self, since: Mapping[str, str]) -> Read:
        """The change feed, one cursor per entity (see feeds), each read from its own stamp, oldest change first. An
        entity cut at the page cap is complete only to one second before the newest change it read, so the rest of its
        changes are read next time and no other entity waits on it. Bank transfers have no change time: read whole."""
        out: list[Record] = []
        through: list[tuple[str, str]] = []
        for entity in self.feeds:
            if entity not in since:
                continue
            stamp = format_datetime(M.parse(since[entity]), usegmt=True)     # If-Modified-Since is an HTTP date
            if entity in UNPAGED:
                got = self._read(entity, 1, stamp)
                if not got.ok:
                    return got
                out += got.records
                continue
            newest = ""
            for number in range(1, MAX_PAGES + 1):
                rows, reply = self._rows(entity, number, stamp, CHANGE_ORDER)
                if reply.status != 200:
                    return Read(False, error=_fault(reply) if reply.status else reply.note)
                got = self._taken(entity, rows)
                if not got.ok:
                    return got
                out += got.records
                newest = max([newest, *(X.stamp(row.get("UpdatedDateUTC")) for row in rows)])
                if len(rows) < PAGE:
                    break
            else:
                # Without a change time on any row there is nothing to move to, so the cursor stays where it was.
                complete = (M.parse(newest) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if newest \
                    else since[entity]
                through.append((entity, complete))
        if "bank_transfers" in self.entities:
            got = self._read("bank_transfers", 1)
            if not got.ok:
                return got
            out += got.records
        return Read(True, tuple(out), through=tuple(through))

    def _lookup(self, entity: str, kind: str, hit: Callable[[Mapping[str, Any]], bool]) -> Sent | None:
        """The first live row of an entity that `hit` takes, newest first, through every page Xero holds; None when no row
        is. A lookup that cannot finish is a final unknown: a write it cannot rule out is never sent again on a guess."""
        id_key = ID_KEY[kind]
        for number in range(1, FIND_PAGES + 1):
            rows, reply = self._rows(entity, number)
            if reply.status != 200:
                return _lookup_failure(reply)
            for row in rows:
                if row.get(id_key) and _live(entity, row) and hit(row):
                    return Sent(True, kind=kind, external_id=str(row[id_key]),
                                revision=X.stamp(row.get("UpdatedDateUTC")))
            if entity in UNPAGED or len(rows) < PAGE:
                return None
        return Sent(False, error="UNKNOWN: too many rows to tell whether Xero holds the write", final=True)

    def _allocated(self, body: Mapping[str, Any]) -> Sent | None:
        """The allocation of a credit note to an invoice for the amount, when Xero holds it: the write may have landed."""
        note_id, invoice_id = _id_of(body.get("credit"), "CreditMemo"), _id_of(body.get("invoice"), "Invoice")
        if not note_id or not invoice_id:
            return None
        reply = self._call("GET", f"CreditNotes/{_quote(note_id)}")
        if reply.status == 400:
            return None                   # Xero holds no such credit note, so no allocation of it can have landed
        if reply.status != 200:
            return _lookup_failure(reply)
        notes = [as_map(raw) or {} for raw in as_list(reply.body.get("CreditNotes"))]
        note = notes[0] if notes else {}
        amount = Decimal(X.amount_text(int(body["amount"])))
        for raw in as_list(note.get("Allocations")):
            item = as_map(raw) or {}
            target = as_map(item.get("Invoice")) or {}
            if str(target.get("InvoiceID") or "") == invoice_id and not item.get("IsDeleted") \
                    and Decimal(str(item.get("Amount") or 0)) == amount:
                return Sent(True, kind="Payment", external_id=f"CreditNote:{note_id}",
                            revision=X.stamp(note.get("UpdatedDateUTC")))
        return None

    def _unspecified(self) -> str:
        """The id of Xero's Unspecified contact, which a bank transaction with no counterparty is written against. It is
        found by name, or created once under its own key when Xero holds none."""
        if not self._contact:
            found = self._lookup("parties", "Contact", lambda r: str(r.get("Name") or "").casefold() == "unspecified")
            if found is None:
                found = self.send("party.create", {"name": "Unspecified", "active": True}, "contact:unspecified")
            if not found.ok:
                raise _Stop(found)
            self._contact = found.external_id
        return self._contact

    def find(self, op: str, body: Mapping[str, Any], key: str) -> Sent | None:
        """Whether Xero already holds the write sent under `key`, for a retry after a call that may have landed: the row
        carrying the key's Tico marker, or the name, through every page Xero holds. None: it is not there. A body that is
        not a write is none too (the write itself is then refused, final)."""
        try:
            return self._find(op, body, key)
        except (KeyError, TypeError, ValueError):
            return None

    def _find(self, op: str, body: Mapping[str, Any], key: str) -> Sent | None:
        mark = _marker(key)
        if op == "journal.create" and body.get("feed"):
            return self._lookup("bank_transactions", "BankTransaction",
                                lambda r: mark in str(r.get("Reference") or ""))
        if op == "journal.create":
            return self._lookup("manual_journals", "ManualJournal", lambda r: mark in str(r.get("Narration") or ""))
        if op in ("bill.create", "invoice.create"):
            kind = "Bill" if op == "bill.create" else "Invoice"
            side = "ACCPAY" if kind == "Bill" else "ACCREC"
            return self._lookup("invoices", kind,
                                lambda r: r.get("Type") == side and mark in str(r.get("Reference") or ""))
        if op in ("payment.invoice", "payment.bill"):
            kind = "BillPayment" if op == "payment.bill" else "Payment"
            return self._lookup("payments", kind, lambda r: mark in str(r.get("Reference") or ""))
        if op == "writeoff.create":
            return self._lookup("credit_notes", "CreditMemo", lambda r: mark in str(r.get("Reference") or ""))
        if op == "credit.apply":
            return self._allocated(body)
        if op in ("account.create", "party.create"):
            name = str(body.get("name") or "").casefold()
            if not name:
                return Sent(False, error="UNKNOWN: no name to find the write by", final=True)
            if op == "account.create":
                return self._lookup("accounts", "Account", lambda r: str(r.get("Name") or "").casefold() == name)
            return self._lookup("parties", "Contact", lambda r: str(r.get("Name") or "").casefold() == name)
        return None

    def _apply(self, body: Mapping[str, Any], key: str) -> Sent:
        """A credit note applied to an invoice: one allocation on the note, answered by the note as Xero holds it then."""
        note_id = _id_of(body.get("credit"), "CreditMemo")
        if not note_id:
            return Sent(False, error="not a Xero write: no credit note to apply", final=True)
        payload = X.allocation_body(body)
        reply = self._call("PUT", f"CreditNotes/{_quote(note_id)}/Allocations", body=payload,
                           headers={"Idempotency-Key": _idem(key)})
        if not 200 <= reply.status < 300:
            return Sent(False, error=_fault(reply) if reply.status else reply.note, final=reply.status == 400)
        got = self._call("GET", f"CreditNotes/{_quote(note_id)}")
        notes = as_list(got.body.get("CreditNotes")) if got.status == 200 else []
        note = as_map(notes[0]) if notes else None
        if note is None:                  # the allocation landed; the answer is asked for again, and found by find
            return Sent(False, error="Xero did not answer the lookup")
        return Sent(True, kind="Payment", external_id=f"CreditNote:{note_id}",
                    revision=X.stamp(note.get("UpdatedDateUTC")))

    def send(self, op: str, body: Mapping[str, Any], key: str) -> Sent:
        """One write, idempotent by its Idempotency-Key. Only a refusal from Xero itself is final."""
        try:
            if op == "credit.apply":
                return self._apply(body, key)
            payload: dict[str, Any] = {**body, "marker": _marker(key)}
            if op == "journal.create" and body.get("feed") and not _has_counterparty(body):
                payload["contact"] = self._unspecified()
            kind, method, path, wire = _write_of(op, payload)
        except _Stop as stop:
            return stop.sent
        except (KeyError, ValueError, TypeError) as e:
            return Sent(False, error=f"not a Xero write: {e}"[:MAX_TEXT], final=True)
        if kind is None:
            return Sent(False, error=f"Xero has no write for {op}", final=True)
        reply = self._call(method, path, body=wire, headers={"Idempotency-Key": _idem(key)})
        if reply.status == 0:
            return Sent(False, error=reply.note)
        made = as_map((as_list(reply.body.get(WRAPPER[kind])) or [None])[0]) or {}
        if 200 <= reply.status < 300 and made.get(ID_KEY[kind]):
            return Sent(True, kind=kind, external_id=str(made[ID_KEY[kind]]),
                        revision=X.stamp(made.get("UpdatedDateUTC")))
        return Sent(False, error=_fault(reply), final=reply.status == 400)     # a validation refusal says the same again
