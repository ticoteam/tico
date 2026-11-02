"""The Mercury adapter (spec §8.1, §9): plain HTTP calls to Mercury's API with the company's own token, read at use
time and never logged. It is only ever called outside a transaction, by the sync worker (finance/sync.py) and the
connect probe. Every call returns (status, JSON); it never raises for an HTTP answer. A 429 waits out Retry-After a
few times; 401/403 on a write means the token's IP allowlist lacks this server; a redirect is never followed (the
token stays with the host it was meant for) and a 3xx is a failed answer. The Vault host (full card numbers) is never
called."""
import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, cast

BASE = "https://api.mercury.com/api/v1"
SANDBOX = "https://api-sandbox.mercury.com/api/v1"
BASE_ENV = "TICO_MERCURY_BASE"          # a developer's override: the sandbox, or a local fake (scripts/fake_mercury.py)
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "api-sandbox.mercury.com"})  # the only hosts it may name
log = logging.getLogger(__name__)
_warned = [False]
TIMEOUT = 15
MAX_WAIT = 30.0                       # seconds one 429 may make us wait
TRIES_429 = 3
PAGE = 500
IP_HINT = "Mercury refused the write: add this server's IP address to the token's allowlist in Mercury"


class Opener(Protocol):
    """urlopen's shape: the request, then the timeout by keyword (never positionally: urlopen's 2nd arg is data)."""

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> Any: ...


@dataclass(frozen=True)
class Answer:
    status: int | None                 # None: unreachable or timed out (the call may or may not have landed)
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def refused_write(self) -> bool:
        return self.status in (401, 403)


class Provider(Protocol):
    """What Finance needs from a bank and card provider. Mercury is the one adapter; tests use fakes."""

    def accounts(self) -> Answer: ...
    def credit(self) -> Answer: ...
    def users(self) -> Answer: ...
    def transactions(self, start_after: str | None, limit: int = PAGE) -> Answer: ...
    def transaction(self, txn_id: str) -> Answer: ...
    def account_transactions(self, account_id: str, search: str | None, start: str, end: str,
                             offset: int = 0, request_id: str | None = None) -> Answer: ...
    def events(self, start_after: str | None, limit: int = PAGE, newest: bool = False) -> Answer: ...
    def statements(self, account_id: str, start: str, end: str) -> Answer: ...
    def credit_statements(self, account_id: str) -> Answer: ...
    def cards(self, user_id: str, start_after: str | None = None) -> Answer: ...
    def card(self, card_id: str) -> Answer: ...
    def create_card(self, user_id: str, nickname: str, monthly_cents: int) -> Answer: ...
    def card_limit(self, card_id: str, monthly_cents: int) -> Answer: ...
    def card_state(self, card_id: str, op: str) -> Answer: ...
    def create_recipient(self, body: Mapping[str, object]) -> Answer: ...
    def recipient(self, recipient_id: str) -> Answer: ...
    def request_send_money(self, account_id: str, body: Mapping[str, object]) -> Answer: ...
    def create_transaction(self, account_id: str, body: Mapping[str, object]) -> Answer: ...
    def create_transfer(self, body: Mapping[str, object]) -> Answer: ...
    def send_request(self, request_id: str) -> Answer: ...
    def send_requests(self, account_id: str, start_after: str | None = None, status: str | None = None) -> Answer: ...


def base_url() -> str:
    """The API base the bank worker calls: TICO_MERCURY_BASE when it names a local or sandbox host, else Mercury's own.
    A stray value naming any other host is ignored (one warning per process): the real token never goes elsewhere."""
    raw = os.environ.get(BASE_ENV, "").strip()
    if not raw:
        return BASE
    host = urllib.parse.urlsplit(raw).hostname or ""
    if host in OVERRIDE_HOSTS:
        return raw
    if not _warned[0]:
        _warned[0] = True
        log.warning("%s ignored: %r is not 127.0.0.1, localhost, ::1 or api-sandbox.mercury.com; using Mercury's API",
                    BASE_ENV, host or raw)
    return BASE


def dollars(cents: int) -> Decimal:
    """Cents as the dollar amount Mercury takes (a number in 0.01 steps)."""
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would resend the Authorization header to wherever Location points, past the
    host guard. The 3xx comes back as the answer (an HTTPError), a failure no one retries to the Location."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Mercury:
    def __init__(self, token: str, base: str = BASE, opener: Opener | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._token, self._base, self._opener, self._sleep = token, base.rstrip("/"), opener, sleep

    def _call(self, method: str, path: str, params: Mapping[str, object] | None = None,
              body: Mapping[str, object] | None = None) -> Answer:
        query = urllib.parse.urlencode([(k, str(v)) for k, v in (params or {}).items() if v not in (None, "")],
                                       doseq=True)
        url = self._base + path + (f"?{query}" if query else "")
        try:
            data = json.dumps(body, default=_json_default).encode() if body is not None else None
        except ValueError:                       # an amount too big to send exactly (see _json_default)
            return Answer(None, {})
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(TRIES_429 + 1):
            request = urllib.request.Request(url, data=data, method=method, headers=headers)
            send = self._opener or cast(Opener, _OPENER.open)
            try:
                with send(request, timeout=TIMEOUT) as response:
                    return Answer(int(getattr(response, "status", 200) or 200), _parse(cast(bytes, response.read())))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < TRIES_429:
                    self._sleep(_retry_after(exc.headers.get("Retry-After") if exc.headers else None, attempt))
                    continue
                try:
                    raw = exc.read()
                except (OSError, http.client.HTTPException):
                    raw = b""
                return Answer(exc.code, _parse(raw))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
                return Answer(None, {})
        return Answer(429, {})

    # reads
    def accounts(self) -> Answer:
        return self._call("GET", "/accounts", {"limit": 1000})

    def credit(self) -> Answer:
        return self._call("GET", "/credit")

    def users(self) -> Answer:
        return self._call("GET", "/users")

    def transactions(self, start_after: str | None, limit: int = PAGE) -> Answer:
        return self._call("GET", "/transactions", {"limit": limit, "order": "asc", "start_after": start_after})

    def transaction(self, txn_id: str) -> Answer:
        return self._call("GET", f"/transaction/{urllib.parse.quote(txn_id)}")

    def account_transactions(self, account_id: str, search: str | None, start: str, end: str,
                             offset: int = 0, request_id: str | None = None) -> Answer:
        """One account's transactions created from `start` through `end` (YYYY-MM-DD), a page of up to PAGE from
        `offset`. Mercury pages this list by offset and its answer carries `total` (every match): a read goes on until
        offset reaches it (sync.py). `search` finds a transaction by its note, description, recipient memo or
        counterparty name; `request_id` finds the transaction a send-money request became. The list has no start_after,
        cursor or nextPage (docs.mercury.com listaccounttransactions)."""
        return self._call("GET", f"/account/{urllib.parse.quote(account_id)}/transactions",
                          {"search": search, "start": start, "end": end, "limit": PAGE, "offset": offset,
                           "requestId": request_id})

    def events(self, start_after: str | None, limit: int = PAGE, newest: bool = False) -> Answer:
        return self._call("GET", "/events", {"limit": limit, "order": "desc" if newest else "asc",
                                             "start_after": start_after})

    def statements(self, account_id: str, start: str, end: str) -> Answer:
        return self._call("GET", f"/account/{urllib.parse.quote(account_id)}/statements", {"start": start, "end": end})

    def credit_statements(self, account_id: str) -> Answer:
        return self._call("GET", f"/credit/{urllib.parse.quote(account_id)}/statements", {"limit": 25})

    def cards(self, user_id: str, start_after: str | None = None) -> Answer:
        return self._call("GET", "/cards", {"userId": user_id, "limit": 500, "start_after": start_after})

    def card(self, card_id: str) -> Answer:
        return self._call("GET", f"/cards/{urllib.parse.quote(card_id)}")

    def recipient(self, recipient_id: str) -> Answer:
        return self._call("GET", f"/recipient/{urllib.parse.quote(recipient_id)}")

    def send_request(self, request_id: str) -> Answer:
        return self._call("GET", f"/request-send-money/{urllib.parse.quote(request_id)}")

    def send_requests(self, account_id: str, start_after: str | None = None, status: str | None = None) -> Answer:
        """One page of the account's send-money requests, filtered to one documented status when `status` is given
        (pendingApproval, approved, rejected or cancelled; the list does not show a sent status)."""
        return self._call("GET", "/request-send-money", {"accountId": account_id, "limit": PAGE, "status": status,
                                                         "start_after": start_after})

    # writes
    def create_card(self, user_id: str, nickname: str, monthly_cents: int) -> Answer:
        return self._call("POST", "/cards", body={"userId": user_id, "type": "virtual", "kind": "credit",
                                                  "nickname": nickname[:50],
                                                  "spendLimit": {"amountCents": monthly_cents, "interval": "monthly"}})

    def card_limit(self, card_id: str, monthly_cents: int) -> Answer:
        return self._call("POST", f"/cards/{urllib.parse.quote(card_id)}",
                          body={"spendLimit": {"amountCents": monthly_cents, "interval": "monthly"}})

    def card_state(self, card_id: str, op: str) -> Answer:
        assert op in ("freeze", "unfreeze", "cancel")
        return self._call("POST", f"/cards/{urllib.parse.quote(card_id)}/{op}", body={})

    def create_recipient(self, body: Mapping[str, object]) -> Answer:
        return self._call("POST", "/recipients", body=body)

    def request_send_money(self, account_id: str, body: Mapping[str, object]) -> Answer:
        return self._call("POST", f"/account/{urllib.parse.quote(account_id)}/request-send-money", body=body)

    def create_transaction(self, account_id: str, body: Mapping[str, object]) -> Answer:
        """Direct send (an IP-allowlisted token; bypasses Mercury's approval queue): the same body as request-send-money."""
        return self._call("POST", f"/account/{urllib.parse.quote(account_id)}/transactions", body=body)

    def create_transfer(self, body: Mapping[str, object]) -> Answer:
        """An internal transfer between two of the company's accounts (documented: sourceAccountId, destinationAccountId, amount, idempotencyKey)."""
        return self._call("POST", "/transfer", body=body)


def _json_default(value: object) -> object:
    """Decimal amounts go out as JSON numbers (Mercury takes 0.01 steps); anything else as its text.
    Floats hold cents exactly below 2**53 cents, so amounts of 10**13 dollars or more are refused."""
    if isinstance(value, Decimal):
        if abs(value) >= 10**13:
            raise ValueError("amount too large to send exactly")
        return float(value)
    return str(value)


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw or b"{}", parse_float=Decimal)
    except ValueError:
        return {}
    if isinstance(value, list):
        return {"items": cast(list[Any], value)}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _retry_after(value: object, attempt: int) -> float:
    try:
        wait = float(str(value))
    except (TypeError, ValueError):
        wait = 2.0 ** attempt
    return max(0.0, min(wait, MAX_WAIT))


def listed(answer: Answer, *keys: str) -> list[dict[str, Any]]:
    """The list under the first key present (Mercury names it per endpoint)."""
    for key in (*keys, "items"):
        value = answer.body.get(key)
        if isinstance(value, list):
            return [cast(dict[str, Any], v) for v in cast(list[Any], value) if isinstance(v, dict)]
    return []


def next_page(answer: Answer) -> str | None:
    page = answer.body.get("page")
    if isinstance(page, dict):
        nxt = cast(dict[str, Any], page).get("nextPage")
        return str(nxt) if nxt else None
    return None


def error_text(answer: Answer, write: bool = False) -> str:
    """What to tell a person about a refused call, without echoing the token or the body."""
    if answer.status is None:
        return "Mercury did not answer"
    if answer.refused_write and write:
        return IP_HINT
    if answer.status in (401, 403):
        return "Mercury refused the token"
    if 300 <= answer.status < 400:
        return f"Mercury answered {answer.status}, a redirect, which is never followed"
    message = answer.body.get("message") or answer.body.get("error")
    return f"Mercury said {answer.status}" + (f": {str(message)[:200]}" if isinstance(message, str) else "")


def ids(rows: Sequence[Mapping[str, object]]) -> list[str]:
    return [str(r.get("id") or "") for r in rows]
