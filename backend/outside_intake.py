# pyright: strict
"""Authenticated outside arrivals and the first human triage step."""

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from . import auth, mailbox_access, threads
from .entities import store as entities
from .store import H

GMAIL_LABEL = "Tico Intake"
DEADLINE_HOURS = 24
hub: Any = H
mailboxes: Any = mailbox_access
thread_store: Any = threads
standing = cast("Any", auth.standing)  # pyright: ignore[reportUnknownMemberType]


def _owner(c: sqlite3.Connection, box: dict[str, Any]) -> str:
    person = str(box.get("person_id") or "")
    if not person or box["kind"] != "personal" or standing(c, person):
        return ""
    if not mailboxes.feeds_directory(c, box) or mailboxes.level(c, f"human:{person}", box["address"]) == "none":
        return ""
    return person


def from_gmail(c: sqlite3.Connection, box: dict[str, Any], message: SimpleNamespace, watch: str) -> bool:
    """Accept only inbound, outside mail explicitly labeled for intake in a watched inbox."""
    labels = set(cast("list[str]", message.labels))
    if GMAIL_LABEL not in labels or labels.intersection(("SENT", "SPAM", "TRASH")) or message.is_internal:
        return False
    if 0 < int(message.epoch) < datetime.fromisoformat(watch).timestamp():
        return False
    owner = _owner(c, box)
    sender = str(message.from_addr)
    if not owner or "@" not in sender or not message.thread_id or not message.msg_id:
        return False
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
    prior = c.execute(
        "SELECT id FROM automation_runs WHERE automation_id=? AND trigger_ref=? AND trigger_ref LIKE 'intake:%'",
        (queue.INTAKE_AUTOMATION + connection_id, ref),
    ).fetchone()
    if prior:
        return str(prior["id"])

    key = json.dumps([channel, connection_id, source_thread_id])
    thread_id = str(uuid.uuid5(uuid.NAMESPACE_OID, key))
    root = thread_store.thread_row(c, thread_id)
    if root and (root["deleted_at"] or not root["entity_id"]):
        return None  # a replay or later arrival cannot reopen a deleted or erased conversation
    actor = f"ext:{channel}:{verified_id or sender}"
    verified = entities.verified_sender(c, f"human:{owner_id}", channel, connection_id, verified_id)
    if root is None:
        entity_id = verified
        if entity_id is None:
            system = str(hub.TICO_ACTOR)
            entity = entities.upsert(c, system, "person", match={}, name=f"Unidentified sender {thread_id[:12]}")
            entity_id = str(entity["id"])
            entities.add_source(c, system, entity_id, "mailbox", connection_id, visibility="restricted")
        root = thread_store._insert_thread(
            c,
            actor,
            title=title or "Outside conversation",
            audience="entity",
            owner_id=f"human:{owner_id}",
            thread_id=thread_id,
            entity_id=entity_id,
            connection_id=connection_id,
        )
    else:
        thread_store._with_participants(c, root, [actor])
    message = hub._write_message(
        c, actor, None, body, root, "say", {}, None, None, outside=(channel, connection_id, source_message_id)
    )
    return str(
        queue.queue_intake(
            c,
            connection_id,
            owner_id,
            ref,
            {"message_id": message["id"], "triage": verified is None or verified != root["entity_id"]},
        )
    )


def process(c: sqlite3.Connection, payload: dict[str, Any], started_at: str) -> str:
    """Settle the claim's human handoff in the same transaction as its receipt."""
    message = hub.message(c, payload["message_id"])
    root = thread_store.thread_row(c, message["conversation_id"]) if message else None
    if not root or root["deleted_at"] or not root["entity_id"]:
        return "skipped"
    box = mailboxes.row_by_id(c, root["connection_id"])
    owner = _owner(c, box) if box else ""
    if not owner or not entities.visible_entity(c, f"human:{owner}", root["entity_id"]):
        return "skipped"
    due = hub.shift(started_at, hours=DEADLINE_HOURS)
    if payload["triage"] or hub.parse_ts(due) <= hub.parse_ts(hub.now()):
        occurrence = f"intake:{root['id']}"
        if not c.execute(
            "SELECT 1 FROM tasks WHERE occurrence=? AND occurrence_step='triage'", (occurrence,)
        ).fetchone():
            actor = f"human:{owner}"
            sender = str(message["sender_id"]).split(":", 2)[-1]
            suggested = entities.suggested_sender(c, actor, sender)
            text = f"Review the [outside thread](/threads/{root['id']}) and identify the sender."
            if suggested:
                text += f" Possible contact {suggested}."
            hub.task_create(
                c,
                hub.TICO_ACTOR,
                "Triage outside conversation",
                text,
                actor,
                due=due,
                private=True,
                lint=False,
                deduplicate=False,
                entity_id=root["entity_id"],
                occurrence=occurrence,
                occurrence_step="triage",
            )
    return "ok"
