# pyright: strict
"""Facts about a mailbox's mail, asked of Gmail when they are needed, since Tico keeps no copy of the mail.

Whether an address is known to a mailbox, which message a reply answers and that message's headers: each is one small
Gmail call, kept for CACHE_SECONDS so a send decided again within a few minutes (backend/email_sending.py `decision`, on
enqueue and again at dispatch) asks Gmail once. Callers that hold the write lock warm the cache first, under a read
(`warm`). Only Gmail's answers are kept. Gmail not answering, or refusing (it refuses for a rate limit too, and a
token that cannot be had refuses), raises a retryable Problem and keeps nothing: a decision that needs a fact waits for
Gmail, never guesses.

A mailbox the server does not watch (backend/gmail_watch.py `watched`) is still synced by its computer, so its facts
come from that computer's copy (`mail_messages`) until the computer side goes.
"""

import collections
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from . import gmail_live, gmail_watch
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
    """Gmail's answer to `work`, kept CACHE_SECONDS; a failure raises and is not kept."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[1] > now:
            return cast("T", hit[0])
    try:
        value = work()
    except Problem as failed:
        raise Problem(
            "gmail_unavailable",
            "Gmail could not answer about this mailbox now; the email waits and is tried again",
            503,
            retryable=True,
        ) from failed
    with _LOCK:
        _CACHE[key] = (value, now + CACHE_SECONDS)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return value


def forget() -> None:
    with _LOCK:
        _CACHE.clear()


def _found(c: Conn, mailbox: str, q: str) -> bool:
    return cast("Callable[[Conn, str, str], Json | None]", gmail_live.first)(c, mailbox, q) is not None


def _copy(c: Conn, sql: str, args: tuple[Any, ...]) -> Any:
    """A row of the computer's copy of a mailbox the server does not watch."""
    return c.execute(sql, args).fetchone()


def sent_to(c: Conn, mailbox: str, address: str) -> bool:
    """Whether this mailbox ever sent mail to `address` (Gmail `in:sent`)."""
    box, addr = mailbox.lower(), str(address or "").strip().lower()
    if not ADDRESS.fullmatch(addr):
        return False
    if not gmail_watch.watched(c, box):
        return bool(
            _copy(
                c,
                "SELECT 1 FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL AND lower(from_addr)=? "
                "AND EXISTS (SELECT 1 FROM json_each(labels) WHERE value='SENT') "
                "AND (EXISTS (SELECT 1 FROM json_each(to_addresses) WHERE lower(value)=?) "
                "OR EXISTS (SELECT 1 FROM json_each(cc) WHERE lower(value)=?)) LIMIT 1",
                (box, box, addr, addr),
            )
        )
    return _cached(("sent_to", box, addr), lambda: _found(c, box, f"in:sent {{to:{addr} cc:{addr}}}"))


def known(c: Conn, mailbox: str, address: str) -> bool:
    """Whether `address` wrote to this mailbox or the mailbox wrote to it."""
    box, addr = mailbox.lower(), str(address or "").strip().lower()
    if not ADDRESS.fullmatch(addr):
        return False
    if not gmail_watch.watched(c, box):
        wrote = _copy(
            c,
            "SELECT 1 FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL AND lower(from_addr)=? LIMIT 1",
            (box, addr),
        )
        return bool(wrote) or sent_to(c, box, addr)
    return _cached(("heard_from", box, addr), lambda: _found(c, box, f"from:{addr}")) or sent_to(c, box, addr)


def by_message_id(c: Conn, mailbox: str, message_id: str) -> Json | None:
    """The Gmail message whose Message-ID header is `message_id` (with its angle brackets), as {id, threadId}, or None."""
    box = mailbox.lower()
    if not MESSAGE_ID.fullmatch(message_id or ""):
        return None
    if not gmail_watch.watched(c, box):
        row = _copy(
            c,
            "SELECT msg_id,thread_id FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL "
            "AND json_extract(send_facts,'$.message-id')=? LIMIT 1",
            (box, message_id),
        )
        return {"id": row["msg_id"], "threadId": row["thread_id"]} if row else None
    first = cast("Callable[[Conn, str, str], Json | None]", gmail_live.first)
    return _cached(("rfc822", box, message_id), lambda: first(c, box, "rfc822msgid:" + message_id[1:-1]))


def message(c: Conn, mailbox: str, msg_id: str) -> Json | None:
    """What a send decision reads of the message it answers: sender, thread and its header facts; None when Gmail no
    longer has it. The body is not kept."""
    box = mailbox.lower()
    if not gmail_watch.watched(c, box):
        row = _copy(
            c,
            "SELECT msg_id,thread_id,from_addr,send_facts FROM mail_messages WHERE mailbox=? AND msg_id=? "
            "AND deleted_at IS NULL",
            (box, msg_id),
        )
        if not row:
            return None
        facts = cast("Json", json.loads(row["send_facts"] or "{}"))
        return {
            "msg_id": row["msg_id"],
            "thread_id": row["thread_id"],
            "from_addr": row["from_addr"],
            "send_facts": facts,
        }

    def read() -> Json | None:
        try:
            found = cast("Callable[[Conn, str, str], Json]", gmail_live.message)(c, box, msg_id)
        except Problem as missing:
            if missing.status == 404:
                return None  # Gmail's answer: it has no such message
            raise
        return {
            "msg_id": found["id"],
            "thread_id": found["thread_id"],
            "from_addr": found["from"],
            "send_facts": dict(found.get("send_facts") or {}),
        }

    return _cached(("message", box, msg_id), read)
