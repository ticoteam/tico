# pyright: strict
"""The one way the backend calls an outside service's API at a fixed provider host (Stripe, Mercury, Google, Slack...).

Every call has an explicit timeout and never follows a redirect unless the caller says so: a 3xx comes back as the
answer, so a bearer token never goes to the host a Location names. Any HTTP answer, whatever its status, is a Reply. No
answer at all (a name that does not resolve, a refused or dropped connection, a timeout, a malformed response) raises
Failed, the one error this module raises; `Reply.check()` raises it for a refusal too, for a caller that wants only
success.

Retries are bounded and opt-in (`retries`, run by stamina), and only for a request that is safe to send twice: an
IDEMPOTENT method, or one the caller vouches for (`idempotent=True`: a POST that only reads, or that carries an
idempotency key the service honours). A reply `retry_on` names (its statuses, or a test of the reply) is tried again
after the wait its Retry-After asks for, capped at `max_wait`, or 1, 2, 4... seconds (from `first_wait`) when it names
none; `retry_errors` also tries again a call that got no answer. Any other write is sent once, whatever comes back: even
a 429 does not promise the write was not taken. Retries that must outlive the process stay in the database
(webhook_delivery, outbox, cloud_machines).

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
    """An HTTP answer. `complete` is False for a refusal whose body was cut short (its status line arrived, the rest did
    not): a caller that must know whether a write landed treats that as no answer."""

    status: int
    headers: httpx.Headers
    content: bytes
    complete: bool = True

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


def header(headers: httpx.Headers, name: str) -> str | None:
    """The first value of a header: httpx joins repeated ones with commas, where urllib read the first."""
    values = headers.get_list(name)
    return values[0] if values else None


def retry_after(headers: httpx.Headers) -> float | None:
    """The seconds a Retry-After header asks for (a number or an HTTP date), never below 0; None when absent or unreadable."""
    value = (header(headers, "retry-after") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def wait_for(reply: Reply | None, tried: int, max_wait: float = MAX_WAIT, first_wait: float = 1.0) -> float:
    """The wait before try number `tried + 2`: the reply's Retry-After, else first_wait * 2**tried seconds; at most
    `max_wait`."""
    asked = retry_after(reply.headers) if reply is not None else None
    return min(asked if asked is not None else first_wait * 2.0**tried, max_wait)


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
    first_wait: float = 1.0,
    deadline: float | None = None,
    idempotent: bool | None = None,
    max_bytes: int | None = None,
    follow_redirects: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> Reply:
    """One call to `url`, tried up to `retries` more times (see the module docstring); the last Reply, or Failed.

    `deadline` (time.monotonic) bounds the whole call: each try's timeout is what is left of it, and a wait that would end
    past it is not taken (the reply in hand is returned). `idempotent` overrides the method's own rule: True for a POST
    that only reads or carries an idempotency key the service honours. `max_bytes` reads the body only to one byte past
    it, so a caller can refuse an answer too large without holding all of it (0: the status alone, no body). `transport` stands in for the network in
    tests."""
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
        wait = wait_for(reply, len(waits), max_wait, first_wait)
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
                    max_bytes,
                    follow_redirects,
                    transport or TRANSPORT,
                )
                if wanted(reply) and idempotent:
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
    max_bytes: int | None,
    follow_redirects: bool,
    transport: httpx.BaseTransport | None,
) -> Reply:
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise Failed("out of time")
    answered: list[httpx.Response] = []  # the status line, kept when the body or a Location cannot be read
    body = bytearray()
    sent = dict(headers)
    if max_bytes is not None:
        sent.setdefault("Accept-Encoding", "identity")  # the cap counts the bytes that arrive, not what they expand to
    try:
        # A client per call: no pooled connection that went stale between calls can turn a write into an unknown one.
        with httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=transport,
            verify=outbound_tls.context(),
            event_hooks={"response": [answered.append]},
        ) as client:
            request = client.build_request(
                method, url, headers=sent, params=params, content=content, data=data, json=json
            )
            response = client.send(request, stream=True)
            try:
                for chunk in response.iter_bytes() if max_bytes != 0 else ():
                    body += chunk
                    if max_bytes is not None and len(body) > max_bytes:
                        del body[max_bytes + 1 :]
                        break
                return Reply(response.status_code, response.headers, bytes(body))
            finally:
                response.close()
    except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
        if answered and not 200 <= answered[-1].status_code < 300:  # a refusal stays a refusal, marked cut short
            return Reply(answered[-1].status_code, answered[-1].headers, bytes(body), complete=False)
        raise Failed(f"{type(exc).__name__}: no answer") from exc
