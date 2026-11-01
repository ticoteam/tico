"""The Stripe adapter (spec §9): plain HTTP calls to Stripe's API with the company's restricted key, read at use time and
never logged. Only the sync worker (finance/stripe_sync.py) and the connect probe call it, outside any transaction.
Every call returns (status, JSON); it never raises for an HTTP answer. A 429 waits out Retry-After a few times, and a
redirect is never followed (the key stays with the host it was meant for)."""
import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast, runtime_checkable

from .mercury import Answer, Opener

BASE = "https://api.stripe.com/v1"
BASE_ENV = "TICO_STRIPE_BASE"          # a developer's override: a local fake (scripts/fake_stripe.py)
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})  # the only hosts it may name
log = logging.getLogger(__name__)
_warned = [False]
TIMEOUT = 15
MAX_WAIT = 30.0
TRIES_429 = 3
PAGE = 100                             # Stripe's list limit per page
EXPAND = ("data.source",)              # each balance transaction's charge, refund or payout, for who paid


class Provider(Protocol):
    """What Finance needs from Stripe. The adapter is the one implementation; tests use fakes."""

    def account(self) -> Answer: ...
    def balance(self) -> Answer: ...
    def transactions(self, starting_after: str | None, limit: int = PAGE) -> Answer: ...


@runtime_checkable
class Catalog(Protocol):
    """The product catalog a Stripe provider can list: products become items, prices their refs. Kept apart from Provider
    so a provider without it (a fake of the balance alone) still syncs its balance."""

    def products(self, starting_after: str | None, limit: int = PAGE) -> Answer: ...
    def prices(self, starting_after: str | None, limit: int = PAGE) -> Answer: ...


def base_url() -> str:
    """The API base the sync worker calls: TICO_STRIPE_BASE when it names a local host, else Stripe's own. A stray value
    naming any other host is ignored (one warning per process): the key never goes elsewhere."""
    raw = os.environ.get(BASE_ENV, "").strip()
    if not raw:
        return BASE
    host = urllib.parse.urlsplit(raw).hostname or ""
    if host in OVERRIDE_HOSTS:
        return raw
    if not _warned[0]:
        _warned[0] = True
        log.warning("%s ignored: %r is not 127.0.0.1, localhost or ::1; using Stripe's API", BASE_ENV, host or raw)
    return BASE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would resend the Authorization header to wherever Location points."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Stripe:
    def __init__(self, key: str, base: str = BASE, opener: Opener | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._key, self._base, self._opener, self._sleep = key, base.rstrip("/"), opener, sleep

    def _get(self, path: str, params: Sequence[tuple[str, str]]) -> Answer:
        query = urllib.parse.urlencode(list(params))
        url = self._base + path + (f"?{query}" if query else "")
        headers = {"Authorization": "Bearer " + self._key, "Accept": "application/json"}
        for attempt in range(TRIES_429 + 1):
            request = urllib.request.Request(url, method="GET", headers=headers)
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

    def account(self) -> Answer:
        """The account the key belongs to: its id names the money account it posts to."""
        return self._get("/account", [])

    def balance(self) -> Answer:
        return self._get("/balance", [])

    def transactions(self, starting_after: str | None, limit: int = PAGE) -> Answer:
        """Balance transactions newest first; `starting_after` is the last id of the page before (older ones next)."""
        params = [("limit", str(limit)), *(("expand[]", e) for e in EXPAND)]
        if starting_after:
            params.append(("starting_after", starting_after))
        return self._get("/balance_transactions", params)

    def products(self, starting_after: str | None, limit: int = PAGE) -> Answer:
        """Products, active or not, paged the way the balance transactions are."""
        return self._get("/products", _page(starting_after, limit))

    def prices(self, starting_after: str | None, limit: int = PAGE) -> Answer:
        """Prices (a product has several: currencies, monthly and yearly intervals), paged the same way."""
        return self._get("/prices", _page(starting_after, limit))


def _page(starting_after: str | None, limit: int) -> list[tuple[str, str]]:
    params = [("limit", str(limit))]
    if starting_after:
        params.append(("starting_after", starting_after))
    return params


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _retry_after(value: object, attempt: int) -> float:
    try:
        wait = float(str(value))
    except (TypeError, ValueError):
        wait = 2.0 ** attempt
    return max(0.0, min(wait, MAX_WAIT))


def listed(answer: Answer) -> list[dict[str, Any]]:
    """The rows of a list answer (Stripe's `data`)."""
    value = answer.body.get("data")
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], v) for v in cast(list[Any], value) if isinstance(v, dict)]


def has_more(answer: Answer) -> bool:
    return answer.body.get("has_more") is True


def error_text(answer: Answer) -> str:
    """What to tell a person about a refused call, without echoing the key or the body."""
    if answer.status is None:
        return "Stripe did not answer"
    if answer.status in (401, 403):
        return "Stripe refused the key; check its permissions for account, balance and balance transactions"
    if 300 <= answer.status < 400:
        return f"Stripe answered {answer.status}, a redirect, which is never followed"
    error = answer.body.get("error")
    message = cast(dict[str, Any], error).get("message") if isinstance(error, dict) else None
    return f"Stripe said {answer.status}" + (f": {str(message)[:200]}" if isinstance(message, str) else "")
