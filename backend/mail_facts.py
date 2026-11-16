# pyright: strict
"""Facts about a mailbox's mail, asked of Gmail when they are needed, since Tico keeps no copy of the mail.

Whether an address is known to a mailbox, which message a reply answers and that message's headers: each is one small
Gmail call, kept for CACHE_SECONDS so a send decided again within a few minutes (backend/email_sending.py `decision`, on
enqueue and again at dispatch) asks Gmail once. Callers that hold the write lock warm the cache first, under a read
(`warm`). Gmail not answering raises its Problem: a decision that needs a fact waits for Gmail, never guesses. A
mailbox the server holds no credential for, or Gmail refuses, has no facts: nothing is known, nothing is found, and the
decision asks a person.
"""

import collections
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from . import gmail_live
from .store import Problem

T = TypeVar("T")
Json = dict[str, Any]
Conn = sqlite3.Connection

CACHE_SECONDS = 300
CACHE_SIZE = 2048
ADDRESS = re.compile(r"^[A-Za-z0-9._%+'-]{1,200}@[A-Za-z0-9.-]{1,200}$")
MESSAGE_ID = re.compile(r"^<[^<>\s\"]{1,900}>$")

_LOCK = threading.Lock()
# (kind, mailbox, key) -> (value, expiry in monotonic seconds)
_CACHE: collections.OrderedDict[tuple[str, str, str], tuple[Any, float]] = collections.OrderedDict()


def _cached(key: tuple[str, str, str], work: Callable[[], T]) -> T:
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[1] > now:
            return cast("T", hit[0])
    value = work()
    with _LOCK:
        _CACHE[key] = (value, now + CACHE_SECONDS)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return value


def forget() -> None:
    with _LOCK:
        _CACHE.clear()


def _refused(failed: Problem) -> bool:
    return not failed.retryable and failed.status in (401, 403)


def _first(c: Conn, mailbox: str, q: str) -> Json | None:
    try:
        return cast("Callable[[Conn, str, str], Json | None]", gmail_live.first)(c, mailbox, q)
    except Problem as failed:
        if _refused(failed):
            return None
        raise


def _found(c: Conn, mailbox: str, q: str) -> bool:
    return _first(c, mailbox, q) is not None


def sent_to(c: Conn, mailbox: str, address: str) -> bool:
    """Whether this mailbox ever sent mail to `address` (Gmail `in:sent`)."""
    box, addr = mailbox.lower(), str(address or "").strip().lower()
    if not ADDRESS.fullmatch(addr):
        return False
    return _cached(("sent_to", box, addr), lambda: _found(c, box, f"in:sent {{to:{addr} cc:{addr}}}"))


def known(c: Conn, mailbox: str, address: str) -> bool:
    """Whether `address` wrote to this mailbox or the mailbox wrote to it."""
    box, addr = mailbox.lower(), str(address or "").strip().lower()
    if not ADDRESS.fullmatch(addr):
        return False
    return _cached(("heard_from", box, addr), lambda: _found(c, box, f"from:{addr}")) or sent_to(c, box, addr)


def by_message_id(c: Conn, mailbox: str, message_id: str) -> Json | None:
    """The Gmail message whose Message-ID header is `message_id` (with its angle brackets), as {id, threadId}, or None."""
    box = mailbox.lower()
    if not MESSAGE_ID.fullmatch(message_id or ""):
        return None
    return _cached(("rfc822", box, message_id), lambda: _first(c, box, "rfc822msgid:" + message_id[1:-1]))


def message(c: Conn, mailbox: str, msg_id: str) -> Json | None:
    """What a send decision reads of the message it answers: sender, thread and its header facts; None when Gmail no
    longer has it. The body is not kept."""
    box = mailbox.lower()

    def read() -> Json | None:
        try:
            found = cast("Callable[[Conn, str, str], Json]", gmail_live.message)(c, box, msg_id)
        except Problem as missing:
            if missing.status == 404 or _refused(missing):
                return None
            raise
        return {
            "msg_id": found["id"],
            "thread_id": found["thread_id"],
            "from_addr": found["from"],
            "send_facts": dict(found.get("send_facts") or {}),
        }

    return _cached(("message", box, msg_id), read)
