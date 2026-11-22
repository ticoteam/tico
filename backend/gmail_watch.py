# pyright: strict
"""The server hears each mailbox's new mail from Gmail itself, on its own drain worker (backend/drains.py `gmail_watch`).

Each pass checks the mailboxes it watches (`watched`: a credential on the server reads them) that were last checked
INTERVAL seconds ago or more, oldest first, WORKERS at a time: Gmail's history since the mailbox's history id, each new
message read once, then the same three calls a computer's sync made (`automations.on_mail`,
`email_sending.record_bounces`, Directory `ingest.from_mail`), then where it got to is saved. Nothing of the message is
stored for ordinary mail: an automation run sees its body while it runs (the run drops it when it ends).
Explicitly labeled intake mail is saved with its run before advancing the Gmail cursor.

Where a check gets to is saved message by message, never past one it has not heard: the pass's budget is checked
before each message, and the next pass goes on from there. A history record is heard whole before its id is saved (one
record can add many messages). A first check, or one after Gmail no longer has the history id (404), catches up
(`catch_up`): it notes Gmail's current history id, lists every message received since the watch began (a first check:
MAIL_LOOKBACK_HOURS back, `automations.mail_watch`) or since the last good check less RECHECK_BACK, and hears them
oldest first, saving the last one heard; only once all are heard does it follow the history from the noted id. A
message heard twice starts one run, since runs dedupe on `mail:<mailbox>:<id>`. A failed check keeps where it got to
and says why on the mailbox (`check_error`); a refused Google sign-in also goes to reconnects, as a computer's report
did. Drafts are not heard: they are not mail anyone received.
"""

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from types import SimpleNamespace
from typing import Any, Protocol, cast

from . import drains, gmail_live, google_workspace
from .store import H, Problem

LOG = logging.getLogger("tico.gmail_watch")

INTERVAL = 90  # seconds between a mailbox's checks
RETRY = 90  # seconds before a failed check is tried again
WORKERS = 4  # mailboxes checked at once
RECHECK_BACK = timedelta(hours=1)
BODY_MAX = 32 * 1024
# Google refused the sign-in or the grant: only a person fixes it (a network failure is retryable, not one of these).
REFUSED = ("gmail_refused", "not_connected", "google_refused")
# The credentials the server reads mail with (backend/google_tokens.py): the Workspace key and a person's own Connect
# Google grant. A mailbox neither covers has no watcher.
SERVER_READS = ("workspace", "personal")

Json = dict[str, Any]
Conn = sqlite3.Connection

_TRIED: dict[str, float] = {}  # address -> when its last failed check was (monotonic): a failing mailbox waits RETRY


class Left(Protocol):
    def left(self) -> bool: ...


# The untyped modules this one leans on, with the shapes it uses. Looked up on each call, so tests can replace them.
def _now() -> str:
    return str(H.now())


def _authority(c: Conn, mailbox: str) -> str:
    return cast("Callable[[Conn, str], str]", google_workspace.authority)(c, mailbox)


def _gmail(name: str) -> Callable[..., Any]:
    return cast("Callable[..., Any]", getattr(gmail_live, name))


def watched(c: Conn, mailbox: str) -> bool:
    """Whether the server watches this mailbox's mail itself: its Workspace key or its person's own grant covers it."""
    return _authority(c, str(mailbox or "").strip().lower()) in SERVER_READS


def due(c: Conn, now: str | None = None) -> list[Json]:
    """The active mailboxes a pass checks, the longest unchecked first (one still catching up at once): a person's
    (once they are matched) or a shared one, that the server watches."""
    stamp = cast("Callable[..., str]", H.shift)(now or _now(), seconds=-INTERVAL)
    rows = c.execute(
        "SELECT id,address,person_id,kind,history_id,checked_at,check_error,catch_up,watching_since_at FROM mailboxes "
        "WHERE status='active' AND (kind='shared' OR person_id IS NOT NULL) "
        "AND (checked_at IS NULL OR checked_at<=? OR catch_up IS NOT NULL) ORDER BY coalesce(checked_at,''), address",
        (stamp,),
    ).fetchall()
    later = time.monotonic() - RETRY
    out: list[Json] = []
    for row in rows:
        box = cast("Json", dict(row))
        if _TRIED.get(box["address"], 0.0) <= later and watched(c, box["address"]):
            out.append(box)
    return out


def drain(store: Any, budget: Left | None = None) -> int:
    """One pass: the due mailboxes, WORKERS at a time, until the pass's budget is spent. Returns the messages heard."""
    if store.settings.rehearsal or store.settings.demo:
        return 0
    with store.read() as c:
        boxes: list[Json] = due(c)

    def one(box: Json) -> int:
        return check(store, box, budget)

    heard = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for start in range(0, len(boxes), WORKERS):
            if start and not drains.left(cast("Any", budget)):
                break  # the rest are the longest unchecked next pass
            heard += sum(pool.map(one, boxes[start : start + WORKERS]))
    return heard


def _epoch(stamp: str) -> int | None:
    at = cast("Callable[[str], Any]", H.parse_ts)(stamp)
    if at is None:
        return None
    return int((at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at).timestamp())


def _internal(addresses: Iterable[str], domains: list[str]) -> bool:
    everyone = [a for a in addresses if a]
    return bool(everyone) and all(
        any(a.rpartition("@")[2] == d or a.rpartition("@")[2].endswith("." + d) for d in domains) for a in everyone
    )


def _message(item: Json, labels: dict[str, str], domains: list[str], known: bool | None) -> SimpleNamespace:
    sender = str(item["from"] or "").lower()
    return SimpleNamespace(
        msg_id=item["id"],
        thread_id=item["thread_id"],
        epoch=item["epoch"],
        provider_timestamp_ms=item.get("provider_timestamp_ms"),
        date=item["date"],
        from_addr=sender,
        from_header=item["from_header"],
        to=item["to"],
        cc=item["cc"],
        subject=item["subject"],
        snippet=item["snippet"],
        labels=[labels.get(label, label) for label in item["labels"]],
        body=str(item["body"] or "")[:BODY_MAX],
        attachments=item["attachments"],
        list_id=item["list_id"],
        is_internal=_internal((sender, *item["to"], *item["cc"]), domains),
        has_unsubscribe=bool(item["unsubscribe"]),
        known_sender=known,
        send_facts=dict(item.get("send_facts") or {}),
        headers=dict(item.get("headers") or {}),
        from_header_count=item.get("from_header_count", 0),
        dkim_signatures=list(item.get("dkim_signatures") or []),
    )


def _known(c: Conn, address: str, sender: str) -> bool | None:
    """Whether the mailbox has written to this sender: Tico's own sends, else Gmail's sent mail. None when Gmail
    cannot say (the automation reads it as unknown)."""
    if not sender or sender == address:
        return None
    if c.execute(
        "SELECT 1 FROM email_sends WHERE mailbox=? AND state='sent' "
        "AND EXISTS (SELECT 1 FROM json_each(recipients) WHERE value=?) LIMIT 1",
        (address, sender),
    ).fetchone():
        return True
    from . import mail_facts

    try:
        return mail_facts.sent_to(c, address, sender)
    except Problem:
        return None


class Reader:
    """One check's reads under no write lock: each message once, with what hearing it needs."""

    def __init__(self, c: Conn, address: str, budget: Left | None) -> None:
        from . import mailbox_access

        self.c, self.address, self.budget = c, address, budget
        self.domains = cast("list[str]", cast("Callable[[Conn], Any]", mailbox_access.company_domains)(c))
        self.labels: dict[str, str] | None = None
        self.known: dict[str, bool | None] = {}
        self.read = 0

    def go_on(self) -> bool:
        """Whether to read one more message: always the first of a check, then while the budget lasts."""
        self.read += 1
        return drains.left(cast("Any", self.budget)) or self.read == 1

    def item(self, mid: str) -> Json | None:
        """The message as Gmail has it now, or None when it was deleted meanwhile."""
        try:
            return cast("Json", _gmail("message")(self.c, self.address, mid, include_provider_time=True))
        except Problem as gone:
            if gone.status == 404:
                return None
            raise

    def hear(self, item: Json) -> SimpleNamespace | None:
        """What `on_mail` and the rest hear of it; None for a draft."""
        if "DRAFT" in item["labels"]:
            return None
        if self.labels is None and any(str(label).startswith("Label_") for label in item["labels"]):
            rows = cast("list[Json]", _gmail("labels")(self.c, self.address))
            self.labels = {str(row["id"]): str(row["name"]) for row in rows}
        sender = str(item["from"] or "").lower()
        if sender not in self.known:
            self.known[sender] = _known(self.c, self.address, sender)
        return _message(item, self.labels or {}, self.domains, self.known[sender])


def _catch_up(c: Conn, box: Json, now: str) -> Json:
    """Where catching up starts: Gmail's history id now, and the time to list mail after."""
    from .automations import MAIL_LOOKBACK_HOURS

    address, watch = str(box["address"]), str(box["watching_since_at"])
    after = _epoch(watch)
    checked = _epoch(str(box["checked_at"]))
    if checked is not None:
        after = max(after or 0, checked - int(RECHECK_BACK.total_seconds()))
    if after is None:
        after = (_epoch(now) or 0) - MAIL_LOOKBACK_HOURS * 3600
    return {"history_id": str(_gmail("profile")(c, address)), "after": after, "id": ""}


def _gather(c: Conn, box: Json, now: str, budget: Left | None) -> tuple[list[SimpleNamespace], Json]:
    """Under a read: the messages to hear and where the mailbox gets to once they are heard (its history_id, catch_up
    and watching_since_at)."""
    from . import automations

    address = str(box["address"])
    if not box["watching_since_at"]:
        box = {**box, "watching_since_at": cast("Callable[..., str]", automations.mail_watch)(c, address, now)}
    reader = Reader(c, address, budget)
    state: Json = {
        "history_id": box["history_id"],
        "catch_up": json.loads(box["catch_up"]) if box["catch_up"] else None,
        "watching_since_at": box["watching_since_at"],
    }
    heard: list[SimpleNamespace] = []
    if state["history_id"] and not state["catch_up"]:
        try:
            records, newest = cast(
                "tuple[list[tuple[str, list[str]]], str | None]",
                _gmail("history")(c, address, state["history_id"]),
            )
        except Problem as expired:
            if expired.status != 404:
                raise
            state["catch_up"] = _catch_up(c, box, now)
        else:
            for record, ids in records:
                found: list[SimpleNamespace] = []
                for mid in ids:
                    if not reader.go_on() and heard:
                        return heard, state  # this record waits, whole, for the next pass
                    item = reader.item(mid)
                    message = reader.hear(item) if item else None
                    if message:
                        found.append(message)
                heard += found
                state["history_id"] = record
            if newest and int(newest) > int(state["history_id"]):
                state["history_id"] = newest
            return heard, state
    if not state["catch_up"]:
        state["catch_up"] = _catch_up(c, box, now)
    mark = cast("Json", state["catch_up"])
    ids = cast("list[str]", _gmail("all_ids")(c, address, f"after:{max(0, int(mark['after']) - 1)}"))
    for mid in reversed(ids):  # Gmail lists newest first; mail is heard oldest first
        if mid == mark["id"]:
            continue  # the last one heard
        if not reader.go_on():
            return heard, state
        item = reader.item(mid)
        if not item or (int(item["epoch"]), str(item["id"])) <= (int(mark["after"]), str(mark["id"])):
            continue
        message = reader.hear(item)
        if message:
            heard.append(message)
        state["catch_up"] = mark = {**mark, "after": int(item["epoch"]), "id": str(item["id"])}
    state["history_id"], state["catch_up"] = mark["history_id"], None  # all heard: follow the history from here
    return heard, state


def check(store: Any, box: Json, budget: Left | None = None) -> int:
    """One mailbox's check: read Gmail, then hear each new message in one write. Returns the messages heard."""
    from . import automations, email_sending, outside_intake, reconnects
    from .entities import ingest

    address, now = str(box["address"]), _now()
    watch = box["watching_since_at"]
    if watch is not None and _epoch(str(watch)) is None:
        # Keep the new start even if Gmail is unavailable during the repair. Gmail's times are whole seconds, so the new
        # start is this second's beginning: a message received later in this same second still counts.
        start = now[:19] + ".000000Z"
        with store.transaction() as c:
            repaired = c.execute(
                "UPDATE mailboxes SET watching_since_at=?,catch_up=NULL "
                "WHERE id=? AND status='active' AND watching_since_at=? AND history_id IS ? AND catch_up IS ?",
                (start, box["id"], watch, box["history_id"], box["catch_up"]),
            ).rowcount
        if not repaired:
            return 0
        box = {**box, "watching_since_at": start, "catch_up": None}
    try:
        with store.read() as c:
            messages, state = _gather(c, box, now, budget)
    except Problem as failed:
        _failed(store, box, failed)
        return 0
    _TRIED.pop(address, None)
    on_mail = cast("Callable[..., Any]", automations.on_mail)
    bounces = cast("Callable[..., Any]", email_sending.record_bounces)
    with store.transaction(notify=True) as c:
        current = c.execute("SELECT * FROM mailboxes WHERE id=?", (box["id"],)).fetchone()
        if (
            not current
            or current["status"] != "active"
            or current["watching_since_at"] != box["watching_since_at"]
            or (current["history_id"], current["catch_up"]) != (box["history_id"], box["catch_up"])
        ):
            return 0  # stopped, or checked by another pass meanwhile: that check's mail is its own
        inbox = dict(current)
        for message in messages:
            facts = cast("Json", message.send_facts)
            taken = outside_intake.from_gmail(c, inbox, message, str(state["watching_since_at"]))
            bounces(c, address, message.from_addr, message.body + "\n" + str(facts.get("delivery-status") or "")[:4096])
            if taken:
                continue
            on_mail(c, address, message, state["watching_since_at"])
            headers = {
                "from_addr": message.from_addr,
                "from_header": message.from_header,
                "list_id": message.list_id,
                "has_unsubscribe": message.has_unsubscribe,
                "send_facts": facts,
            }
            ingest.safely(c, "mail", ingest.from_mail, c, str(box["id"]), headers)
        c.execute(
            "UPDATE mailboxes SET history_id=?,catch_up=?,checked_at=?,check_error=NULL,"
            "watching_since_at=coalesce(watching_since_at,?) WHERE id=?",
            (
                state["history_id"],
                json.dumps(state["catch_up"]) if state["catch_up"] else None,
                now,
                state["watching_since_at"],
                box["id"],
            ),
        )
        if box["check_error"] or not box["checked_at"]:
            # It works again, or works for the first time.
            cast("Callable[..., Any]", reconnects.google_good)(c, "mail", [address])
    return len(messages)


def _failed(store: Any, box: Json, failed: Problem) -> None:
    """Why the check failed, on the mailbox; a refused sign-in also to reconnects. Where it got to stays."""
    from . import reconnects

    _TRIED[str(box["address"])] = time.monotonic()
    code, detail = str(cast("Any", failed).code), str(cast("Any", failed).detail)
    LOG.info("gmail watch of a mailbox failed: %s", code)
    refused = code in REFUSED and not cast("Any", failed).retryable
    with store.transaction() as c:
        c.execute("UPDATE mailboxes SET check_error=? WHERE id=?", (detail[:300], box["id"]))
        if refused:
            report = cast("Callable[..., Any]", reconnects.google_report)
            report(c, "mail", [box["address"]], [box["address"]], ok=[])
