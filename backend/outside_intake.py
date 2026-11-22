# pyright: strict
"""Authenticated outside arrivals and the first human triage step."""

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import timezone
from email.utils import getaddresses
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import quote

from . import auth, mailbox_access, threads
from .entities.ingest import automated
from .entities import store as entities
from .mail_lib.gmail import dkim_tags
from .store import H

GMAIL_LABEL = "Tico Intake"
DEADLINE_HOURS = 24
hub: Any = H
mailboxes: Any = mailbox_access
thread_store: Any = threads
standing = cast("Any", auth.standing)  # pyright: ignore[reportUnknownMemberType]
_AUTH_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\\.|[();]|[^\s();"]+')


def _verified_from(message: SimpleNamespace, sender: str) -> bool:
    """Use the first Authentication-Results field, which Gmail's receiving MX puts ahead of upstream results."""
    headers = cast("dict[str, str]", message.headers)
    raw = headers.get("authentication-results", "")
    if (
        not raw
        or message.from_header_count != 1
        or [address.lower() for _, address in getaddresses([str(message.from_header)])] != [sender]
    ):
        return False
    domain = sender.rpartition("@")[2]
    clauses: list[list[str]] = [[]]
    depth = 0
    for token in _AUTH_TOKEN.findall(raw):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0:
            if token == ";":
                clauses.append([])
            else:
                clauses[-1].append(token)
    if depth or [token.lower() for token in clauses[0]] != ["mx.google.com"]:
        return False
    for clause in clauses[1:]:
        if not clause:
            continue
        result, *properties = clause
        lowered = [prop.lower() for prop in properties]
        if result.lower() == "dmarc=pass" and f"header.from={domain}" in lowered:
            return True
        if result.lower() != "dkim=pass":
            continue
        if f"header.d={domain}" in lowered:
            return True
        # Gmail commonly reports header.b in place of header.d. Resolve it to exactly one DKIM signature.
        prefix = next((p.partition("=")[2] for p in properties if p.lower().startswith("header.b=")), "")
        if not prefix:
            continue
        signers: list[str] = []
        for signature in cast("list[str]", message.dkim_signatures):
            tags = dkim_tags(signature)
            if tags.get("b", "").startswith(prefix):
                signers.append(tags.get("d", "").lower())
        if signers == [domain]:
            return True
    return False


def _owner(c: sqlite3.Connection, box: dict[str, Any]) -> str:
    person = str(box.get("person_id") or "")
    if not person or box["kind"] != "personal" or standing(c, person):
        return ""
    if mailboxes.level(c, f"human:{person}", box["address"]) == "none":
        return ""
    return person


def from_gmail(c: sqlite3.Connection, box: dict[str, Any], message: SimpleNamespace, watch: str) -> bool:
    """Take over routing for inbound outside mail labeled for intake in an enrolled inbox."""
    labels = set(cast("list[str]", message.labels))
    if GMAIL_LABEL not in labels or labels.intersection(("SENT", "SPAM", "TRASH")) or message.is_internal:
        return False
    since = hub.parse_ts(watch)
    if since and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if since and 0 < int(message.epoch) < since.timestamp():
        return False
    sender = str(message.from_addr)
    owner = _owner(c, box)
    if (
        not owner
        or not mailboxes.feeds_directory(c, box)
        or "@" not in sender
        or not message.thread_id
        or not message.msg_id
    ):
        return False
    headers = cast("dict[str, str]", message.headers)
    if (
        sender == box["address"]
        or "list-id" in headers
        or ("auto-submitted" in headers and headers["auto-submitted"].strip().lower() != "no")
        or headers.get("return-path", "").strip() == "<>"
        or automated(sender, {**vars(message), "has_unsubscribe": "list-unsubscribe" in headers})
        or message.send_facts.get("delivery-status")
    ):
        return True
    if not entities.is_suppressed(c, "email", sender):
        save_message(
            c,
            channel="gmail",
            connection_id=str(box["id"]),
            owner_id=owner,
            source_thread_id=str(message.thread_id),
            source_message_id=str(message.msg_id),
            sender=sender,
            title=str(message.subject or ""),
            body=str(message.body or ""),
            verified_id=sender if _verified_from(message, sender) else "",
        )
    return True


def save_message(
    c: sqlite3.Connection,
    *,
    channel: str,
    connection_id: str,
    owner_id: str,
    source_thread_id: str,
    source_message_id: str,
    sender: str,
    title: str,
    body: str,
    verified_id: str = "",
) -> str | None:
    """Save the message and its intake run in the adapter's write transaction. A replay returns the run."""
    from . import automations

    if not c.in_transaction:
        raise ValueError("Conversation intake requires the adapter's write transaction")
    queue: Any = automations
    ref = "intake:" + hashlib.sha256(source_message_id.encode()).hexdigest()
    prior = queue.replay_intake(c, connection_id, ref)
    if prior:
        return str(prior)

    payload: dict[str, Any] = {"source_message_id": source_message_id, "sender_verified": bool(verified_id)}
    verified = entities.verified_sender(c, f"human:{owner_id}", channel, connection_id, verified_id)
    if verified is None:
        if verified_id:
            payload["suggested"] = entities.suggested_sender(c, f"human:{owner_id}", sender)
        return str(queue.queue_intake(c, connection_id, owner_id, ref, payload))

    key = json.dumps([channel, connection_id, source_thread_id])
    thread_id = str(uuid.uuid5(uuid.NAMESPACE_OID, key))
    root = thread_store.thread_row(c, thread_id)
    if root and (root["deleted_at"] or not root["entity_id"]):
        return None  # a replay or later arrival cannot reopen a deleted or erased conversation
    if root is not None and root["entity_id"] != verified:
        return str(queue.queue_intake(c, connection_id, owner_id, ref, payload))
    actor = f"ext:{channel}:{verified_id}"
    if root is None:
        root = thread_store._insert_thread(
            c,
            actor,
            title=title or "Outside conversation",
            audience="entity",
            owner_id=f"human:{owner_id}",
            thread_id=thread_id,
            entity_id=verified,
            connection_id=connection_id,
        )
    else:
        thread_store._with_participants(c, root, [actor])
    message = hub._write_message(
        c, actor, None, body, root, "say", {}, None, None, outside=(channel, connection_id, source_message_id)
    )
    payload["message_id"] = message["id"]
    return str(queue.queue_intake(c, connection_id, owner_id, ref, payload))


def triage(
    c: sqlite3.Connection,
    connection_id: str,
    ref: str,
    payload: dict[str, Any],
    started_at: str,
    *,
    recovery: bool = False,
) -> str:
    """Hand the arrival to its inbox owner. Gmail keeps the original when the sender has no matched thread."""
    box = mailboxes.row_by_id(c, connection_id)
    owner = _owner(c, box) if box else ""
    if not owner:
        return "skipped"
    due = hub.shift(started_at, hours=DEADLINE_HOURS)
    subject = "Triage outside conversation" if payload.get("sender_verified") else "Triage unverified sender"
    mailbox = quote(str(box["address"]), safe="")
    source_id = payload.get("source_message_id")
    if not source_id and payload.get("message_id"):
        message = hub.message(c, payload["message_id"])
        source_id = message["source_message_id"] if message else None
    delivery = quote(str(source_id or ""), safe="")
    text = f"Review this arrival in [Gmail](https://mail.google.com/mail/u/{mailbox}/#all/{delivery})."
    if recovery:
        text += " Automatic processing could not finish."
    elif suggested := payload.get("suggested"):
        if entities.visible_entity(c, f"human:{owner}", str(suggested)):
            text += f" Possible contact {suggested}."
    hub.task_create(
        c,
        hub.TICO_ACTOR,
        subject,
        text,
        f"human:{owner}",
        due=due,
        private=True,
        lint=False,
        deduplicate=False,
        occurrence=f"{connection_id}:{ref}",
        occurrence_step="triage",
    )
    return "ok"


def process(c: sqlite3.Connection, connection_id: str, ref: str, payload: dict[str, Any], started_at: str) -> str:
    if (
        not payload.get("message_id")
        or hub.parse_ts(hub.shift(started_at, hours=DEADLINE_HOURS)) <= hub.parse_ts(hub.now())
        or payload.get("triage")
    ):
        return triage(c, connection_id, ref, payload, started_at)
    return "ok"
