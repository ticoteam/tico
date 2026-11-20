"""The Brex adapter (spec §9): plain HTTP calls to Brex's API with the company's own token, read at use time and never
logged. Finance only reads from Brex: the card and cash accounts, their balances and their settled transactions. Only the
sync worker (finance/brex_sync.py) and the connect probe call it, outside any transaction. Every call returns an Answer
(status, JSON); it never raises for an HTTP answer. Lists are cursor pages: `cursor` and `limit` in, `next_cursor` out
(null at the end). A 429 or a 5xx waits and tries again a few times (Retry-After when Brex sends one); a redirect is never
followed (backend/outbound_http.py) (the token stays with the host it was meant for) and a 3xx is a failed answer.

Brex's production API is api.brex.com (its staging host is not a sandbox and takes no customer tokens). A developer's
override names only a local host, as the other providers do."""

import json
import logging
import os
import time
import urllib.parse
from collections.abc import Sequence
from typing import Any, Protocol, cast

import httpx

from .. import outbound_http
from .mercury import Answer

BASE = "https://api.brex.com"  # Brex's production API, the host of its OpenAPI server. There is no fallback host: a
# connection error is reported, and the token is never sent to a second host
BASE_ENV = "TICO_BREX_BASE"  # a developer's override: a local fake
OVERRIDE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})  # the only hosts it may name
log = logging.getLogger(__name__)
_warned = [False]
TIMEOUT = 15
TRIES = 3  # a 429 or 5xx is tried this many times more at most
RETRY_ON = frozenset({429, *range(500, 600)})
PAGE = 100  # rows asked for per page


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


class Brex:
    def __init__(
        self,
        token: str,
        base: str = BASE,
        transport: httpx.BaseTransport | None = None,
        budget: float | None = None,
    ) -> None:
        """`budget`, when given, bounds every call made through this client together (the connect probe passes
        PROBE_SECONDS): a call's timeout is what is left of it, and a 429 or 5xx is not waited out past it."""
        self._token, self._base, self._transport = token, base.rstrip("/"), transport
        self._until = None if budget is None else time.monotonic() + budget

    def _get(self, path: str, params: Sequence[tuple[str, str]]) -> Answer:
        query = urllib.parse.urlencode(list(params))
        url = self._base + path + (f"?{query}" if query else "")
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        try:
            reply = outbound_http.send(
                "GET",
                url,
                headers=headers,
                timeout=TIMEOUT,
                retries=TRIES,
                retry_on=RETRY_ON,
                deadline=self._until,
                transport=self._transport,
            )
        except outbound_http.Failed:
            return Answer(None, {})
        return Answer(reply.status, _parse(reply.content))

    def card_accounts(self, cursor: str | None = None) -> Answer:
        """The company's card accounts (GET /v2/accounts/card). Brex answers them all at once as a bare array (_parse)."""
        return self._get("/v2/accounts/card", _page(cursor))

    def cash_accounts(self, cursor: str | None = None) -> Answer:
        """The company's cash accounts with their balances (GET /v2/accounts/cash), a page at a time."""
        return self._get("/v2/accounts/cash", _page(cursor))

    def card_transactions(self, posted_at_start: str, cursor: str | None = None) -> Answer:
        """Settled card transactions posted on or after `posted_at_start`, a date-time with no zone
        (`YYYY-MM-DDTHH:MM:SS`: Brex answers 400 to a `Z` or an offset), newest pages by cursor
        (GET /v2/transactions/card/primary)."""
        return self._get("/v2/transactions/card/primary", _page(cursor, posted_at_start=posted_at_start))

    def cash_transactions(self, account_id: str, posted_at_start: str, cursor: str | None = None) -> Answer:
        """Settled transactions of one cash account posted on or after `posted_at_start`
        (GET /v2/transactions/cash/{id})."""
        return self._get(
            f"/v2/transactions/cash/{urllib.parse.quote(account_id, safe='')}",
            _page(cursor, posted_at_start=posted_at_start),
        )

    def me(self) -> Answer:
        """The user the token belongs to (GET /v2/users/me). The connect probe reads it as the token check; the response has
        no role, so nothing else is read from it."""
        return self._get("/v2/users/me", [])


def _page(cursor: str | None, posted_at_start: str | None = None) -> list[tuple[str, str]]:
    params = [("limit", str(PAGE))]
    if cursor:
        params.append(("cursor", cursor))
    if posted_at_start:
        params.append(("posted_at_start", posted_at_start))
    return params


def _parse(raw: bytes) -> dict[str, Any]:
    """A Brex body as an object. GET /v2/accounts/card answers a bare JSON array of accounts, not a page: it reads as a page
    of those items with no next cursor."""
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    if isinstance(value, list):
        return {"items": value}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


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
