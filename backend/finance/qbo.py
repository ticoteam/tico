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
NAMESPACE = uuid.UUID("6f0c5a1e-2b7d-4c8e-9a13-5d2f7e4b9c01")   # RequestIds are uuid5 of the outbox key in this namespace
TIMEOUT = 20
TRIES_429 = 3
MAX_WAIT = 30.0
MAX_TEXT = 300
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

    entities: tuple[str, ...] = ("accounts", "parties", "entries")

    def __init__(self, realm: str, tokens: Callable[[bool], str], *, base: str | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.realm, self.tokens = realm, tokens
        self.base = (base or base_url()).rstrip("/")
        self.transport = transport or _urllib
        self.sleep = sleep

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
        reply = self._call("GET", "query", query={"query": f"SELECT COUNT(*) FROM {entity}"})
        if reply.status != 200:
            return _fault(reply.body, reply.status)
        found: dict[str, Any] = Q.as_map(reply.body.get("QueryResponse")) or {}
        total = found.get("totalCount")
        return int(total) if isinstance(total, int) else 0

    def _query(self, entity: str, start: int, size: int) -> Read:
        sql = f"SELECT * FROM {entity} STARTPOSITION {start} MAXRESULTS {size}"
        reply = self._call("GET", "query", query={"query": sql})
        if reply.status != 200:
            return Read(False, error=_fault(reply.body, reply.status))
        found: dict[str, Any] = Q.as_map(reply.body.get("QueryResponse")) or {}
        return Read(True, tuple(Q.records(entity, Q.as_list(found.get(entity)))))

    def page(self, entity: str, start: int, size: int) -> Read:
        if entity == "accounts":
            return self._query("Account", start, size)
        if entity == "entries":
            return self._query("JournalEntry", start, size)
        vendors = self._count("Vendor")
        if isinstance(vendors, str):
            return Read(False, error=vendors)
        records: list[Record] = []
        if start <= vendors:
            got = self._query("Vendor", start, min(size, vendors - start + 1))
            if not got.ok:
                return got
            records += list(got.records)
        if len(records) < size:
            got = self._query("Customer", max(start - vendors, 1), size - len(records))
            if not got.ok:
                return got
            records += list(got.records)
        return Read(True, tuple(records))

    def changed(self, since: str) -> Read:
        reply = self._call("GET", "cdc", query={"entities": ",".join(Q.ENTITIES), "changedSince": _iso(since)})
        if reply.status != 200:
            return Read(False, error=_fault(reply.body, reply.status))
        return Read(True, tuple(Q.change_records(reply.body)))

    def send(self, op: str, body: Mapping[str, Any], key: str) -> Sent:
        """One write, idempotent by its RequestId. Only a refusal from QuickBooks itself is final."""
        request_id = str(uuid.uuid5(NAMESPACE, key))
        try:
            kind, path, payload, query = _write_of(op, body)
        except (KeyError, ValueError, TypeError) as e:
            return Sent(False, error=f"not a QuickBooks write: {e}"[:MAX_TEXT], final=True)
        if kind is None:
            return Sent(False, error=f"QuickBooks has no write for {op}", final=True)
        reply = self._call("POST", path, query=query, body=payload, request_id=request_id)
        if reply.status == 0:
            return Sent(False, error="QuickBooks did not answer")
        made: dict[str, Any] = Q.as_map(reply.body.get(kind)) or {}
        if 200 <= reply.status < 300 and made:
            return Sent(True, kind=kind, external_id=str(made.get("Id") or ""),
                        revision=str(made.get("SyncToken") or ""))
        final = reply.status == 400 and "Fault" in reply.body      # a validation refusal: a retry says the same thing
        return Sent(False, error=_fault(reply.body, reply.status), final=final)


def _write_of(op: str, body: Mapping[str, Any]) -> tuple[str | None, str, dict[str, Any], dict[str, str]]:
    """The record kind, path, body and query one outbox operation sends."""
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
    return None, "", {}, {}
