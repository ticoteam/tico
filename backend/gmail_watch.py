# pyright: strict
"""The server hears each mailbox's new mail from Gmail itself, on its own drain worker (backend/drains.py `gmail_watch`).

Each pass checks the active mailboxes last checked INTERVAL seconds ago or more, oldest first, WORKERS at a time:
Gmail's history since the mailbox's history id, each new message read once, then the same three calls a computer's sync
made (`automations.on_mail`, `email_sending.record_bounces`, Directory `ingest.from_mail`), then the new history id
saved. Nothing of the message is stored: an automation run sees its body while it runs (the run drops it when it ends).

A first check saves Gmail's current history id, starts the watch MAIL_LOOKBACK_HOURS back (`automations.mail_watch`) and
lists the mail received since then. A history id Gmail no longer has (404) lists the mail since the last good check
less RECHECK_BACK; a message heard twice starts one run, since runs dedupe on `mail:<mailbox>:<id>`. A failed check
leaves the history id where it was and says why on the mailbox (`check_error`); a refused Google sign-in also goes to
reconnects, as a computer's report did. Drafts are not heard: they are not mail anyone received.
"""

import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from . import drains, gmail_live, google_workspace
from .store import H, Problem

LOG = logging.getLogger("tico.gmail_watch")

INTERVAL = 90  # seconds between a mailbox's checks
RETRY = 90  # seconds before a failed check is tried again
WORKERS = 4  # mailboxes checked at once
LIST_MAX = 500  # messages a first check, or one after an expired history id, reads
RECHECK_BACK = timedelta(hours=1)
BODY_MAX = 32 * 1024
REFUSED = ("gmail_refused", "not_connected")  # Google refused the sign-in: only a person fixes it

Json = dict[str, Any]
Conn = sqlite3.Connection

_TRIED: dict[str, float] = {}  # address -> when its last failed check was (monotonic): a failing mailbox waits RETRY


# The untyped modules this one leans on, with the shapes it uses. Looked up on each call, so tests can replace them.
def _now() -> str:
    return str(H.now())


def _authority(c: Conn, mailbox: str) -> str:
    return cast("Callable[[Conn, str], str]", google_workspace.authority)(c, mailbox)


def _gmail(name: str) -> Callable[..., Any]:
    return cast("Callable[..., Any]", getattr(gmail_live, name))


def due(c: Conn, now: str | None = None) -> list[Json]:
    """The active mailboxes a pass checks, the longest unchecked first: a person's (once they are matched) or a shared
    one, whose mail a credential on the server covers."""
    stamp = cast("Callable[..., str]", H.shift)(now or _now(), seconds=-INTERVAL)
    rows = c.execute(
        "SELECT id,address,person_id,kind,history_id,checked_at,check_error,watching_since_at FROM mailboxes "
        "WHERE status='active' AND (kind='shared' OR person_id IS NOT NULL) "
        "AND (checked_at IS NULL OR checked_at<=?) ORDER BY coalesce(checked_at,''), address",
        (stamp,),
    ).fetchall()
    later = time.monotonic() - RETRY
    out: list[Json] = []
    for row in rows:
        box = cast("Json", dict(row))
        if _TRIED.get(box["address"], 0.0) <= later and _authority(c, box["address"]) in ("workspace", "personal"):
            out.append(box)
    return out


def drain(store: Any, budget: drains.Budget | None = None) -> int:
    """One pass: the due mailboxes, WORKERS at a time, until the pass's budget is spent. Returns the messages heard."""
    if store.settings.rehearsal or store.settings.demo:
        return 0
    with store.read() as c:
        boxes: list[Json] = due(c)

    def one(box: Json) -> int:
        return check(store, box)

    heard = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for start in range(0, len(boxes), WORKERS):
            if not drains.left(budget):
                break  # the rest are the longest unchecked next pass
            heard += sum(pool.map(one, boxes[start : start + WORKERS]))
    return heard


def _epoch(stamp: str) -> int:
    at = cast("Callable[[str], Any]", H.parse_ts)(stamp)
    if at is None:
        raise ValueError("not a timestamp: " + str(stamp))
    return int((at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at).timestamp())


def _read(c: Conn, box: Json, now: str) -> tuple[list[str], str, str]:
    """Gmail's side of one check, under no write lock: (new message ids, the history id reached, the watch start)."""
    from . import automations

    address, history_id = str(box["address"]), box["history_id"]
    watch = str(box["watching_since_at"] or cast("Callable[..., str]", automations.mail_watch)(c, address, now))
    if history_id:
        try:
            ids, reached = cast("tuple[list[str], str]", _gmail("history")(c, address, history_id))
            return ids, reached, watch
        except Problem as expired:
            if expired.status != 404:
                raise
        since = max(_epoch(box["checked_at"] or now) - int(RECHECK_BACK.total_seconds()), _epoch(watch))
    else:
        since = _epoch(watch)
    reached = str(_gmail("profile")(c, address))
    ids = cast("list[str]", _gmail("message_ids")(c, address, f"after:{since}", LIST_MAX))
    return list(reversed(ids)), reached, watch  # Gmail lists newest first; mail is heard oldest first


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


def _gather(c: Conn, box: Json, now: str) -> tuple[list[SimpleNamespace], str, str]:
    """Under a read: the new messages to hear, the history id they reach and the watch start."""
    from . import mailbox_access

    address = str(box["address"])
    ids, reached, watch = _read(c, box, now)
    items: list[Json] = []
    for mid in ids:
        try:
            item = cast("Json", _gmail("message")(c, address, mid))
        except Problem as gone:
            if gone.status == 404:
                continue  # deleted between the history and the read
            raise
        if "DRAFT" not in item["labels"]:
            items.append(item)
    labels: dict[str, str] = {}
    if any(str(label).startswith("Label_") for item in items for label in item["labels"]):
        labels = {str(row["id"]): str(row["name"]) for row in cast("list[Json]", _gmail("labels")(c, address))}
    domains = cast("list[str]", cast("Callable[[Conn], Any]", mailbox_access.company_domains)(c))
    known = {sender: _known(c, address, sender) for sender in {str(item["from"] or "").lower() for item in items}}
    messages = [_message(item, labels, domains, known[str(item["from"] or "").lower()]) for item in items]
    return messages, reached, watch


def check(store: Any, box: Json) -> int:
    """One mailbox's check: read Gmail, then hear each new message in one write. Returns the messages heard."""
    from . import automations, reconnects
    from . import email_sending
    from .entities import ingest

    address, now = str(box["address"]), _now()
    try:
        with store.read() as c:
            messages, reached, watch = _gather(c, box, now)
    except Problem as failed:
        _failed(store, box, failed)
        return 0
    _TRIED.pop(address, None)
    on_mail = cast("Callable[..., Any]", automations.on_mail)
    bounces = cast("Callable[..., Any]", email_sending.record_bounces)
    with store.transaction() as c:
        current = c.execute("SELECT status,history_id FROM mailboxes WHERE id=?", (box["id"],)).fetchone()
        if not current or current["status"] != "active" or current["history_id"] != box["history_id"]:
            return 0  # stopped, or checked by another pass meanwhile: that check's mail is its own
        for message in messages:
            facts = cast("Json", message.send_facts)
            bounces(c, address, message.from_addr, message.body + "\n" + str(facts.get("delivery-status") or "")[:4096])
            on_mail(c, address, message, watch)
            headers = {
                "from_addr": message.from_addr,
                "from_header": message.from_header,
                "list_id": message.list_id,
                "has_unsubscribe": message.has_unsubscribe,
                "send_facts": facts,
            }
            ingest.safely(c, "mail", ingest.from_mail, c, str(box["id"]), headers)
        c.execute(
            "UPDATE mailboxes SET history_id=?,checked_at=?,check_error=NULL,"
            "watching_since_at=coalesce(watching_since_at,?) WHERE id=?",
            (reached, now, watch, box["id"]),
        )
        if box["check_error"] or not box["history_id"]:
            # It works again, or works for the first time.
            cast("Callable[..., Any]", reconnects.google_good)(c, "mail", [address])
    return len(messages)


def _failed(store: Any, box: Json, failed: Problem) -> None:
    """Why the check failed, on the mailbox; a refused sign-in also to reconnects. The history id stays. A mailbox
    whose person's own grant the server does not hold yet is not a refusal."""
    from . import reconnects

    _TRIED[str(box["address"])] = time.monotonic()
    code, detail = str(cast("Any", failed).code), str(cast("Any", failed).detail)
    extra = cast("Json", cast("Any", failed).extra)
    LOG.info("gmail watch of a mailbox failed: %s", code)
    refused = code in REFUSED and extra.get("kind") != "personal"
    with store.transaction() as c:
        c.execute("UPDATE mailboxes SET check_error=? WHERE id=?", (detail[:300], box["id"]))
        if refused:
            report = cast("Callable[..., Any]", reconnects.google_report)
            report(c, "mail", [box["address"]], [box["address"]], ok=[])
