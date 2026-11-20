# pyright: strict
"""The one way the backend calls an outside service's API at a fixed provider host (Stripe, Mercury, Google, Slack...).

Every call has an explicit timeout and never follows a redirect unless the caller says so: a 3xx comes back as the
answer, so a bearer token never goes to the host a Location names. Any HTTP answer, whatever its status, is a Reply. No
answer at all (a name that does not resolve, a refused or dropped connection, a timeout, a malformed response) raises
Failed, the one error this module raises; `Reply.check()` raises it for a refusal too, for a caller that wants only
success.

Retries are bounded and opt-in (`retries`, run by stamina). A reply `retry_on` names (its statuses, or a test of the
reply) is tried again after the wait its Retry-After asks for, capped at `max_wait`, or 1, 2, 4... seconds when it names
none; `retry_errors` also tries again a call that got no answer. A write (any method outside IDEMPOTENT) is tried again
only on 429, which says the request was not taken: a write that may have landed (a 5xx, a dropped connection) is never
sent twice. Retries that must outlive the process stay in the database (webhook_delivery, outbox, cloud_machines).

An address a person typed is not sent from here: backend/egress.py checks it and pins the address it checked.
"""

import email.utils
import json as jsonlib
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import stamina

from . import outbound_tls

TIMEOUT = 15.0
MAX_WAIT = 30.0  # the longest one Retry-After may make a call wait
IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
TRANSPORT: httpx.BaseTransport | None = None  # tests stand in for the network here (httpx.MockTransport)


@dataclass(frozen=True)
class Reply:
    status: int
    headers: httpx.Headers
    content: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> object:
        """The body as JSON; ValueError when it is not."""
        return jsonlib.loads(self.content)

    def check(self) -> "Reply":
        """This reply when it is a success, else Failed carrying it."""
        if not self.ok:
            raise Failed(f"answered {self.status}", self)
        return self


class Failed(Exception):
    """No usable answer. `reply` is the refusal when there was one (from `Reply.check`), else None: the service was not
    reached, or a call that may have reached it got no answer."""

    def __init__(self, reason: str, reply: Reply | None = None) -> None:
        super().__init__(reason)
        self.reason, self.reply = reason, reply

    @property
    def status(self) -> int | None:
        return self.reply.status if self.reply is not None else None


class _Again(Exception):
    """A reply whose status the caller asked to retry; it carries the reply back when the tries run out."""

    def __init__(self, reply: Reply) -> None:
        super().__init__(f"answered {reply.status}")
        self.reply = reply


def retry_after(headers: Mapping[str, str]) -> float | None:
    """The seconds a Retry-After header asks for (a number or an HTTP date), never below 0; None when absent or unreadable."""
    value = (headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def wait_for(reply: Reply | None, tried: int, max_wait: float = MAX_WAIT) -> float:
    """The wait before try number `tried + 2`: the reply's Retry-After, else 2**tried seconds; at most `max_wait`."""
    asked = retry_after(reply.headers) if reply is not None else None
    return min(asked if asked is not None else 2.0**tried, max_wait)


def send(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    content: bytes | None = None,
    data: Mapping[str, str] | None = None,
    json: object = None,
    timeout: float = TIMEOUT,
    retries: int = 0,
    retry_on: Collection[int] | Callable[[Reply], bool] = (429,),
    retry_errors: bool = False,
    max_wait: float = MAX_WAIT,
    deadline: float | None = None,
    idempotent: bool | None = None,
    follow_redirects: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> Reply:
    """One call to `url`, tried up to `retries` more times (see the module docstring); the last Reply, or Failed.

    `deadline` (time.monotonic) bounds the whole call: each try's timeout is what is left of it, and a wait that would end
    past it is not taken (the reply in hand is returned). `idempotent` overrides the method's own rule, for a POST that
    only reads (a search). `transport` stands in for the network in tests."""
    method = method.upper()
    idempotent = method in IDEMPOTENT if idempotent is None else idempotent
    statuses = frozenset[int]() if callable(retry_on) else frozenset(retry_on)

    def wanted(reply: Reply) -> bool:
        return retry_on(reply) if callable(retry_on) else reply.status in statuses

    waits: list[float] = []

    def backoff(exc: BaseException) -> bool | float:
        if isinstance(exc, _Again):
            reply: Reply | None = exc.reply
        elif retry_errors and idempotent and isinstance(exc, Failed):
            reply = None
        else:
            return False
        wait = wait_for(reply, len(waits), max_wait)
        if deadline is not None and time.monotonic() + wait >= deadline:
            return False
        waits.append(wait)
        return wait

    request_headers = dict(headers or {})
    try:
        for attempt in stamina.retry_context(
            on=backoff,
            attempts=max(retries, 0) + 1,
            timeout=None,
        ):
            with attempt:
                reply = _once(
                    method,
                    url,
                    request_headers,
                    params,
                    content,
                    data,
                    json,
                    timeout,
                    deadline,
                    follow_redirects,
                    transport or TRANSPORT,
                )
                if wanted(reply) and (idempotent or reply.status == 429):
                    raise _Again(reply)
                return reply
    except _Again as again:
        return again.reply
    raise Failed("no attempt was made")  # pragma: no cover - stamina always makes the first attempt


def _once(
    method: str,
    url: str,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None,
    content: bytes | None,
    data: Mapping[str, str] | None,
    json: object,
    timeout: float,
    deadline: float | None,
    follow_redirects: bool,
    transport: httpx.BaseTransport | None,
) -> Reply:
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise Failed("out of time")
    try:
        # A client per call: no pooled connection that went stale between calls can turn a write into an unknown one.
        with httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=transport,
            verify=outbound_tls.context(),
        ) as client:
            response = client.request(
                method, url, headers=headers, params=params, content=content, data=data, json=json
            )
            return Reply(response.status_code, response.headers, response.content)
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
        raise Failed(f"{type(exc).__name__}: no answer") from exc
