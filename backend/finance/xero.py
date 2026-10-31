"""The Xero adapter's HTTP client (spec §10): plain calls to Xero's accounting API with the connection's access token and
tenant, read at use time and never logged. Only the books worker calls it, outside any transaction (finance/books_sync.py).

Lists page with Xero's `page` (100 rows a page, 1-based), newest first (UpdatedDateUTC DESC): an edit during a backfill
moves a row to the front, so a page is re-read rather than skipped. A backfill position is a row count, so the page of a
start is (start - 1) // 100 + 1. Accounts are one list with no paging. The change feed reads each entity with
If-Modified-Since and pages it the same way. Writes carry an Idempotency-Key derived from the outbox key, so a retry after
a lost answer is the same request to Xero; a retry looks first for the write it may have made (find), by a Tico marker in
the write's Narration or Reference. A 429 waits out Retry-After within a small budget per pass, and past it the pass stops
and the next one resumes; a 401 gets one new token from HQ and the call is tried again; a redirect is never followed.
Every call returns a Read or a Sent: it never raises for an answer from Xero."""
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from typing import Any

from . import books_link
from .core import mirror as M
from .core import xero as X
from .core.qbo import as_list, as_map
from .mirror import Read, Record, Sent

BASE = "https://api.xero.com/api.xro/2.0"
BASE_ENV = "TICO_XERO_BASE"          # a developer's override: a local fake (scripts/fake_xero.py) and nothing else
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
NAMESPACE = uuid.UUID("3b8e2f7a-6c1d-4f5e-9a40-7d2c1b9e6f10")   # Idempotency-Keys are uuid5 of the outbox key here
PAGE = M.BACKFILL_SIZE                # Xero returns 100 rows a page; a shorter page is the last
MAX_PAGES = 200                       # a change window longer than this (20,000 rows) is not read; its pass reports it
FIND_PAGES = 50                       # a lookup for a write reads this many pages; past them it cannot tell
FIND_DAYS = 30                        # a lookup covers the rows changed this long ago (a retry comes within hours)
ORDER = "UpdatedDateUTC DESC"         # newest first, so rows edited during a read move to the front (re-read, not skipped)
EXTRA = {"parties": {"includeArchived": "true"}}     # Xero leaves archived contacts out of a list unless asked
ENDPOINTS = {"accounts": "Accounts", "parties": "Contacts", "invoices": "Invoices", "credit_notes": "CreditNotes",
             "payments": "Payments", "manual_journals": "ManualJournals", "bank_transactions": "BankTransactions"}
WRAPPER = {"Account": "Accounts", "Contact": "Contacts", "ManualJournal": "ManualJournals",
           "BankTransaction": "BankTransactions"}          # a write's kind -> its list
ID_KEY = {"Account": "AccountID", "Contact": "ContactID", "ManualJournal": "ManualJournalID",
          "BankTransaction": "BankTransactionID"}
TIMEOUT = 20
TRIES_429 = 3
PASS_WAIT = 5.0                       # seconds one pass may sleep on rate limits in all; past it the pass stops
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


def _marker(key: str) -> str:
    """The Tico reference a write carries in its Narration or Reference: the outbox key's uuid, so a retry finds the write."""
    return f"[tico:{uuid.uuid5(NAMESPACE, key)}]"


def _query(entity: str, page: int) -> dict[str, str]:
    """The query a list of an entity is read with: a page, newest first (accounts are one list, unpaged)."""
    if entity == "accounts":
        return {}
    return {"page": str(page), "order": ORDER, **EXTRA.get(entity, {})}


def _write_of(op: str, body: Mapping[str, Any]) -> tuple[str | None, str, str, Any]:
    """The record kind, method, path and body one outbox operation sends. An account is sent as the account itself."""
    if op == "account.create":
        return "Account", "PUT", "Accounts", X.account_body(body)
    if op == "party.create":
        return "Contact", "PUT", "Contacts", {"Contacts": [X.party_body(body)]}
    if op == "journal.create":
        if body.get("feed"):
            return "BankTransaction", "PUT", "BankTransactions", {"BankTransactions": [X.bank_body(body)]}
        return "ManualJournal", "PUT", "ManualJournals", {"ManualJournals": [X.journal_body(body)]}
    if op == "journal.delete":
        kind, _, ident = str(body["ext"]).partition(":")
        if kind != "ManualJournal" or not ident:
            raise ValueError(f"Xero voids manual journals only, not {body['ext']}")
        return "ManualJournal", "POST", f"ManualJournals/{urllib.parse.quote(ident, safe='')}", X.void_body(ident)
    return None, "", "", {}


class Xero:
    """The books of one Xero organisation (its tenant). `tokens(force)` returns a usable access token, refreshed through
    HQ when `force` is set (after a 401); it raises books_link.BrokerFailed when HQ cannot. One instance is one pass: a
    rate limit that outlasts the pass's budget stops every later call in it."""

    entities: tuple[str, ...] = M.BOOK_ENTITIES["xero"]

    def __init__(self, tenant: str, tokens: Callable[[bool], str], *, base: str | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.tenant, self.tokens = tenant, tokens
        self.base = (base or base_url()).rstrip("/")
        self.transport = transport or _urllib
        self.sleep = sleep
        self._gst = ""
        self._rounding: str | None = None
        self._waited = 0.0
        self._held = False

    def _call(self, method: str, path: str, *, query: Mapping[str, str] | None = None,
              body: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None) -> Reply:
        if self._held:
            return Reply(429, {"Message": "Xero is rate limiting; this pass stops and the next one resumes"})
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
            return reply

    def _read(self, entity: str, page: int, since: str = "") -> Read:
        name = ENDPOINTS[entity]
        query = _query(entity, page)
        reply = self._call("GET", name, query=query, headers={"If-Modified-Since": since} if since else None)
        if reply.status != 200:
            return Read(False, error=_fault(reply) if reply.status else reply.note)
        rows = as_list(reply.body.get(name))
        try:
            return Read(True, tuple(X.records(entity, rows, self.gst, self.rounding)), seen=len(rows))
        except ValueError as e:
            return Read(False, error=f"Xero's {name} could not be read: {e}"[:MAX_TEXT])

    def _accounts(self) -> list[dict[str, Any]]:
        reply = self._call("GET", "Accounts")
        if reply.status != 200:
            raise ValueError(_fault(reply) if reply.status else reply.note)
        return [as_map(raw) or {} for raw in as_list(reply.body.get("Accounts"))]

    def gst(self) -> str:
        """The tax control account's key: Xero's GST system account, read once per connection."""
        if not self._gst:
            found = next((x for x in self._accounts() if x.get("SystemAccount") == "GST" and x.get("AccountID")), None)
            if found is None:
                raise ValueError("no GST control account, so tax cannot be posted")
            self._gst = f"Account:{found['AccountID']}"
        return self._gst

    def rounding(self) -> str:
        """The rounding account's key: the account Xero's books name Rounding, read once per connection. Empty when there
        is none, so a document that rounds waits as unmapped rather than failing the page."""
        if self._rounding is None:
            found = next((x for x in self._accounts()
                          if str(x.get("Name") or "").casefold() == "rounding" and x.get("AccountID")), None)
            self._rounding = f"Account:{found['AccountID']}" if found else ""
        return self._rounding

    def page(self, entity: str, start: int, size: int) -> Read:
        if entity == "accounts":
            return self._read(entity, 1) if start <= 1 else Read(True, (), seen=0)
        return self._read(entity, (start - 1) // PAGE + 1)

    def changed(self, since: str) -> Read:
        """Every row of every entity changed since the time, read page by page (a short page ends an entity)."""
        stamp = format_datetime(M.parse(since), usegmt=True)     # If-Modified-Since is an HTTP date
        out: list[Record] = []
        for entity in self.entities:
            for page in range(1, MAX_PAGES + 1):
                got = self._read(entity, page, stamp)
                if not got.ok:
                    return got
                out += got.records
                if entity == "accounts" or got.seen < PAGE:
                    break
            else:
                return Read(False, error=f"Xero's {ENDPOINTS[entity]} changed too much to read in one pass")
        return Read(True, tuple(out))

    def _lookup(self, entity: str, kind: str, id_key: str, hit: Callable[[Mapping[str, Any]], bool],
                since: str = "") -> Sent | None:
        """The first row of an entity that `hit` takes, read as the lists are read; None when no row is. A lookup that
        cannot finish is a final unknown, so the write it looks for is never sent again on a guess."""
        name = ENDPOINTS[entity]
        for page in range(1, FIND_PAGES + 1):
            query = _query(entity, page)
            reply = self._call("GET", name, query=query, headers={"If-Modified-Since": since} if since else None)
            if reply.status in (0, 429) or reply.status >= 500:
                return Sent(False, error="Xero did not answer the lookup")
            if reply.status != 200:
                return Sent(False, error=f"UNKNOWN: {_fault(reply)}"[:MAX_TEXT], final=True)
            rows = [as_map(raw) or {} for raw in as_list(reply.body.get(name))]
            for row in rows:
                if row.get(id_key) and row.get("Status") != "DELETED" and hit(row):
                    return Sent(True, kind=kind, external_id=str(row[id_key]),
                                revision=X.stamp(row.get("UpdatedDateUTC")))
            if entity == "accounts" or len(rows) < PAGE:
                return None
        return Sent(False, error="UNKNOWN: too many rows to tell whether Xero holds the write", final=True)

    def find(self, op: str, body: Mapping[str, Any], key: str) -> Sent | None:
        """Whether Xero already holds the write sent under `key`, for a retry after a call that may have landed. A journal
        or a bank transaction is found by its Tico marker among the rows changed in the last FIND_DAYS; an account by its
        name, a contact by its name. None: it is not there. A lookup that cannot tell comes back final."""
        mark = _marker(key)
        recent = format_datetime(datetime.now(UTC) - timedelta(days=FIND_DAYS), usegmt=True)
        if op == "journal.create" and body.get("feed"):
            return self._lookup("bank_transactions", "BankTransaction", "BankTransactionID",
                                lambda row: mark in str(row.get("Reference") or ""), recent)
        if op == "journal.create":
            return self._lookup("manual_journals", "ManualJournal", "ManualJournalID",
                                lambda row: mark in str(row.get("Narration") or ""), recent)
        if op in ("account.create", "party.create"):
            name = str(body.get("name") or "").casefold()
            if not name:
                return Sent(False, error="UNKNOWN: no name to find the write by", final=True)
            if op == "account.create":
                return self._lookup("accounts", "Account", "AccountID",
                                    lambda row: str(row.get("Name") or "").casefold() == name)
            return self._lookup("parties", "Contact", "ContactID",
                                lambda row: str(row.get("Name") or "").casefold() == name)
        return None

    def send(self, op: str, body: Mapping[str, Any], key: str) -> Sent:
        """One write, idempotent by its Idempotency-Key. Only a refusal from Xero itself is final."""
        try:
            kind, method, path, payload = _write_of(op, {**body, "marker": _marker(key)})
        except (KeyError, ValueError, TypeError) as e:
            return Sent(False, error=f"not a Xero write: {e}"[:MAX_TEXT], final=True)
        if kind is None:
            return Sent(False, error=f"Xero has no write for {op}", final=True)
        reply = self._call(method, path, body=payload, headers={"Idempotency-Key": str(uuid.uuid5(NAMESPACE, key))})
        if reply.status == 0:
            return Sent(False, error=reply.note)
        made = as_map((as_list(reply.body.get(WRAPPER[kind])) or [None])[0]) or {}
        if 200 <= reply.status < 300 and made.get(ID_KEY[kind]):
            return Sent(True, kind=kind, external_id=str(made[ID_KEY[kind]]),
                        revision=X.stamp(made.get("UpdatedDateUTC")))
        return Sent(False, error=_fault(reply), final=reply.status == 400)     # a validation refusal says the same again
