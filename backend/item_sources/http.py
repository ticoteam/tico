# pyright: strict
"""Bounded HTTP for outside items, with a separate pool for each source."""

import json
import math
import threading
import time
from collections.abc import Callable
from typing import Any, NamedTuple

TIMEOUT_S = 10.0
MAX_BODY = 512 * 1024
MAX_IN_FLIGHT = 2


class Busy(Exception):
    """Every slot is occupied, including timed-out calls whose threads have yet to end."""


class Reply(NamedTuple):
    status: int
    body: object
    retry_after: float | None = None
    cut: bool = False


def wait_for(worker: threading.Thread, seconds: float) -> None:
    worker.join(seconds)


class Pool:
    def __init__(self, label: str):
        self.label = label
        self._in_flight = 0
        self._lock = threading.Lock()

    def busy(self, limit: int = MAX_IN_FLIGHT) -> bool:
        with self._lock:
            return self._in_flight >= limit

    def call(
        self,
        method: str,
        seconds: float,
        perform: Callable[[float], Reply],
        *,
        limit: int = MAX_IN_FLIGHT,
        wait_for_reply: Callable[[threading.Thread, float], None] = wait_for,
    ) -> Reply:
        """Bound the caller's wait and keep the slot until the request thread ends."""
        wait = min(seconds, TIMEOUT_S)
        ends = time.monotonic() + wait
        answers: list[Reply] = []
        errors: list[BaseException] = []

        def run() -> None:
            try:
                answers.append(perform(ends))
            except BaseException as error:  # noqa: BLE001 - handed to the caller, which raises it
                errors.append(error)
            finally:
                with self._lock:
                    self._in_flight -= 1

        worker = threading.Thread(target=run, name=f"{self.label.lower()}-{method.lower()}", daemon=True)
        with self._lock:
            if self._in_flight >= limit:
                raise Busy()
            self._in_flight += 1
            try:
                worker.start()
            except RuntimeError:
                self._in_flight -= 1
                raise
        wait_for_reply(worker, wait)
        if answers:
            return answers[0]
        if errors:
            raise errors[0]
        raise TimeoutError(f"{self.label} took longer than {wait:g} seconds to answer")


def _retry_after(value: str | None) -> float | None:
    """Retry-After in seconds (a number of seconds or an HTTP date), None when absent or unreadable."""
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (OverflowError, TypeError, ValueError):
        return None


def request(
    label: str,
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    ends: float,
    body: object = None,
    most: int = MAX_BODY,
    *,
    transport: Any = None,
) -> Reply:
    """One call to a public https address, pinned to the IP it resolved to, no redirects, no proxy from the environment,
    at most `most` bytes (MAX_BODY) read, all of it before `ends` (time.monotonic): as egress.post bounds its calls, a timer closes the
    client at the deadline, which ends a read blocked on a socket, and the answer stops being read past it."""
    import httpx

    from .. import egress as _egress

    egress: Any = _egress
    late = TimeoutError(f"{label} took too long to answer")
    parts = egress.check(url)
    address = egress.address_for(parts, budget=max(ends - time.monotonic(), 0.001))
    pinned = httpx.URL(url, params=params or None).copy_with(host=address)
    host = str(parts.hostname) + (f":{parts.port}" if parts.port else "")
    sent = {**headers, "Host": host}
    content = None
    if body is not None:
        content = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False, trust_env=False, transport=transport) as client:
        timer = threading.Timer(max(ends - time.monotonic(), 0.001), client.close)
        timer.daemon = True
        timer.start()
        try:
            with client.stream(
                method, pinned, headers=sent, content=content, extensions={"sni_hostname": parts.hostname}
            ) as response:
                got = b""
                for chunk in response.iter_bytes():
                    got += chunk
                    if len(got) > most or time.monotonic() > ends:
                        break
                if time.monotonic() > ends:
                    raise late
                cut = len(got) > most
                try:
                    answer: object = None if cut else json.loads(got)
                except ValueError:
                    answer = None
                return Reply(response.status_code, answer, _retry_after(response.headers.get("retry-after")), cut)
        except Exception as error:
            if time.monotonic() > ends and error is not late:
                raise late from error
            raise
        finally:
            timer.cancel()
