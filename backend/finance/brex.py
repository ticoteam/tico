"""The Brex adapter (spec §9): plain HTTP calls to Brex's API with the company's own token, read at use time and never
logged. Finance only reads from Brex: the card and cash accounts, their balances and their settled transactions. Only the
sync worker (finance/brex_sync.py) and the connect probe call it, outside any transaction. Every call returns an Answer
(status, JSON); it never raises for an HTTP answer. Lists are cursor pages: `cursor` and `limit` in, `next_cursor` out
(null at the end). A 429 or a 5xx waits and tries again a few times (Retry-After when Brex sends one); a redirect is never
followed (the token stays with the host it was meant for) and a 3xx is a failed answer.

Brex's production API is api.brex.com (its staging host is not a sandbox and takes no customer tokens). A developer's
override names only a local host, as the other providers do."""
import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast

from .mercury import Answer, Opener

BASE = "https://api.brex.com"          # Brex's production API, the host of its OpenAPI server. There is no fallback host: a
                                       # connection error is reported, and the token is never sent to a second host
BASE_ENV = "TICO_BREX_BASE"            # a developer's override: a local fake
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})  # the only hosts it may name
log = logging.getLogger(__name__)
_warned = [False]
TIMEOUT = 15
MAX_WAIT = 30.0                        # seconds one Retry-After may make us wait
TRIES = 3                              # a 429 or 5xx is tried this many times more at most
PAGE = 100                             # rows asked for per page


class Provider(Protocol):
    """What Finance needs from Brex. The adapter is the one implementation; tests use fakes."""

    def card_accounts(self, cursor: str | None = None) -> Answer: ...
    def cash_accounts(self, cursor: str | None = None) -> Answer: ...
    def card_transactions(self, posted_at_start: str, cursor: str | None = None) -> Answer: ...
    def cash_transactions(self, account_id: str, posted_at_start: str, cursor: str | None = None) -> Answer: ...
    def me(self) -> Answer: ...


def base_url() -> str:
    """The API base the sync worker calls: TICO_BREX_BASE when it names a local host, else Brex's own. A stray value naming
    any other host is ignored (one warning per process): the token never goes elsewhere."""
    raw = os.environ.get(BASE_ENV, "").strip()
    if not raw:
        return BASE
    host = urllib.parse.urlsplit(raw).hostname or ""
    if host in OVERRIDE_HOSTS:
        return raw
    if not _warned[0]:
        _warned[0] = True
        log.warning("%s ignored: %r is not 127.0.0.1, localhost or ::1; using Brex's API", BASE_ENV, host or raw)
    return BASE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would resend the Authorization header to wherever Location points."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Brex:
    def __init__(self, token: str, base: str = BASE, opener: Opener | None = None,
                 sleep: Callable[[float], None] = time.sleep, budget: float | None = None) -> None:
        """`budget`, when given, bounds every call made through this client together (the connect probe passes
        PROBE_SECONDS): a call's timeout is what is left of it, and a 429 or 5xx is not waited out past it."""
        self._token, self._base, self._opener, self._sleep = token, base.rstrip("/"), opener, sleep
        self._until = None if budget is None else time.monotonic() + budget

    def _fits(self, wait: float) -> bool:
        return self._until is None or time.monotonic() + wait < self._until

    def _get(self, path: str, params: Sequence[tuple[str, str]]) -> Answer:
        query = urllib.parse.urlencode(list(params))
        url = self._base + path + (f"?{query}" if query else "")
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        for attempt in range(TRIES + 1):
            timeout = float(TIMEOUT)
            if self._until is not None:
                left = self._until - time.monotonic()
                if left <= 0:
                    return Answer(None, {})
                timeout = min(timeout, left)
            request = urllib.request.Request(url, method="GET", headers=headers)
            send = self._opener or cast(Opener, _OPENER.open)
            try:
                with send(request, timeout=timeout) as response:
                    return Answer(int(getattr(response, "status", 200) or 200), _parse(cast(bytes, response.read())))
            except urllib.error.HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < TRIES:
                    retry = exc.headers.get("Retry-After") if exc.headers and exc.code == 429 else None
                    wait = _retry_after(retry, attempt)
                    if self._fits(wait):
                        self._sleep(wait)
                        continue
                try:
                    raw = exc.read()
                except (OSError, http.client.HTTPException):
                    raw = b""
                return Answer(exc.code, _parse(raw))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
                return Answer(None, {})
        return Answer(429, {})

    def card_accounts(self, cursor: str | None = None) -> Answer:
        """The company's card accounts (GET /v2/accounts/card), a page at a time."""
        return self._get("/v2/accounts/card", _page(cursor))

    def cash_accounts(self, cursor: str | None = None) -> Answer:
        """The company's cash accounts with their balances (GET /v2/accounts/cash), a page at a time."""
        return self._get("/v2/accounts/cash", _page(cursor))

    def card_transactions(self, posted_at_start: str, cursor: str | None = None) -> Answer:
        """Settled card transactions posted on or after `posted_at_start` (RFC 3339), newest pages by cursor
        (GET /v2/transactions/card/primary)."""
        return self._get("/v2/transactions/card/primary", _page(cursor, posted_at_start=posted_at_start))

    def cash_transactions(self, account_id: str, posted_at_start: str, cursor: str | None = None) -> Answer:
        """Settled transactions of one cash account posted on or after `posted_at_start`
        (GET /v2/transactions/cash/{id})."""
        return self._get(f"/v2/transactions/cash/{urllib.parse.quote(account_id, safe='')}",
                         _page(cursor, posted_at_start=posted_at_start))

    def me(self) -> Answer:
        """The user the token belongs to, with its role (GET /v2/users/me): an account or card admin's token can see card
        payments, and the connect probe checks it."""
        return self._get("/v2/users/me", [])


def _page(cursor: str | None, posted_at_start: str | None = None) -> list[tuple[str, str]]:
    params = [("limit", str(PAGE))]
    if cursor:
        params.append(("cursor", cursor))
    if posted_at_start:
        params.append(("posted_at_start", posted_at_start))
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
    """The items of a page (Brex's `items`)."""
    value = answer.body.get("items")
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], v) for v in cast(list[Any], value) if isinstance(v, dict)]


def next_cursor(answer: Answer) -> str | None:
    """The cursor of the next page, or None when the list has ended."""
    value = answer.body.get("next_cursor")
    return str(value) if value else None


def error_text(answer: Answer) -> str:
    """What to tell a person about a refused call, without echoing the token or the body."""
    if answer.status is None:
        return "Brex did not answer"
    if answer.status in (401, 403):
        return "Brex refused the token; it needs read access to card and cash accounts and their transactions"
    if 300 <= answer.status < 400:
        return f"Brex answered {answer.status}, a redirect, which is never followed"
    message = answer.body.get("message")
    return f"Brex said {answer.status}" + (f": {str(message)[:200]}" if isinstance(message, str) else "")
