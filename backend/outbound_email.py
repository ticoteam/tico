# pyright: strict
"""Email's projection and writes on the shared outbound queue."""

import json
import sqlite3
from typing import Any

from . import hubdb as _H
from . import outbox as _O

H: Any = _H
O: Any = _O
CHANNEL = "email"
PREFIX = "email:send:"
STATES = {"pending": "queued", "dispatching": "attempting", "sent": "done"}
TABLE = O.Table("outbound_actions", due="queued", flight="attempting", token="claim_token")
METADATA = (
    "bot",
    "via",
    "automation_action_id",
    "calendar_action_id",
    "content_sha256",
    "idempotency_key",
    "operation_key",
    "recipients",
    "attachments",
    "subject",
    "body_sha256",
    "draft_id",
    "thread_id",
    "reply_to_id",
    "approval_task_id",
    "classification",
    "runner_id",
    "checked_at",
    "finished_at",
    "cutover_unconfirmed",
)
COLUMNS = {
    "approval_id": "approval_id",
    "decision": "policy_decision",
    "detail": "last_error",
}


def get(c: sqlite3.Connection, send_id: str) -> Any:
    return c.execute("SELECT * FROM outbound_email WHERE id=?", (send_id,)).fetchone()


def reached(c: sqlite3.Connection, mailbox: str, address: str, *, unconfirmed: bool = False) -> bool:
    """Whether this inbox sent to the address, optionally including an unconfirmed attempt for bounce matching."""
    states = "('done','unknown')" if unconfirmed else "('done')"
    return bool(
        c.execute(
            f"SELECT 1 FROM outbound_email WHERE mailbox=? AND queue_state IN {states} "
            "AND EXISTS (SELECT 1 FROM json_each(recipients) WHERE value=?) LIMIT 1",
            (mailbox, address),
        ).fetchone()
    )


def insert(c: sqlite3.Connection, row: dict[str, Any], *, audience: str) -> None:
    metadata = dict.fromkeys(METADATA)
    metadata["checked_at"] = ""
    metadata.update({k: row[k] for k in METADATA if k in row})
    for key in ("recipients", "attachments", "classification"):
        metadata[key] = json.loads(row[key])
    key = base = PREFIX + row["id"]
    # Earlier generic actions could have used this key before the email namespace was reserved.
    while c.execute("SELECT 1 FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone():
        key = base + ":" + H.new_id()
    c.execute(
        "INSERT INTO outbound_actions(id,channel,audience,operation,destination,occurrence_key,actor,payload,metadata,"
        "policy_decision,state,send_after,next_attempt_at,approval_id,task_id,last_error,created_at,updated_at) "
        "VALUES(?,'email',?,'send',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row["id"],
            audience,
            row["mailbox"],
            key,
            row["actor"],
            json.dumps({"raw": row["raw"]}) if row["raw"] else "",
            json.dumps(metadata),
            row["decision"],
            STATES.get(row["state"], row["state"]),
            row["send_after"],
            row["send_after"],
            row["approval_id"],
            row.get("task_id"),
            row["detail"],
            row["created_at"],
            row["created_at"],
        ),
    )


def update(c: sqlite3.Connection, send_id: str, **changes: Any) -> None:
    """Write the email fields in the caller's transaction without reading or rebuilding frozen content."""
    fields = {"updated_at": H.now()}
    metadata: list[Any] = []
    for key, value in changes.items():
        if key in METADATA:
            metadata.extend(("$." + key, value))
        else:
            fields[COLUMNS[key]] = value
    assignments = [f"{key}=?" for key in fields]
    if metadata:
        assignments.append("metadata=json_set(metadata," + ",".join("?" for _ in metadata) + ")")
    c.execute(
        f"UPDATE outbound_actions SET {','.join(assignments)} WHERE channel='email' AND id=?",
        (*fields.values(), *metadata, send_id),
    )


def queue(c: sqlite3.Connection, send_id: str, send_after: str, decision: str) -> None:
    c.execute(
        "UPDATE outbound_actions SET state='queued',send_after=?,next_attempt_at=?,policy_decision=?,updated_at=? "
        "WHERE channel='email' AND id=? AND state IN ('queued','waiting_approval')",
        (send_after, send_after, decision, H.now(), send_id),
    )


def wait_for_approval(c: sqlite3.Connection, send_id: str, decision: str) -> None:
    c.execute(
        "UPDATE outbound_actions SET state='waiting_approval',policy_decision=?,updated_at=? "
        "WHERE channel='email' AND id=? AND state IN ('queued','waiting_approval')",
        (decision, H.now(), send_id),
    )


def claim(c: sqlite3.Connection, row: Any, now: str, runner: str, decision: str, audience: str) -> str:
    token: str = O.new_token()
    if not O.claim(c, TABLE, row["id"], now, token):
        return ""
    # Task cards use this timestamp to keep showing the send when a matter held it past the undo window.
    c.execute(
        "UPDATE outbound_actions SET metadata=json_set(metadata,'$.runner_id',?),policy_decision=?,audience=?,"
        "send_after=?,updated_at=? WHERE channel='email' AND id=?",
        (runner, decision, audience, now, now, row["id"]),
    )
    return token


def release(c: sqlite3.Connection, row: Any) -> None:
    if O.release(c, TABLE, row["id"], row["lease_id"]):
        update(c, row["id"], runner_id=None)


def permit(c: sqlite3.Connection, row: Any, decision: str) -> bool:
    now = H.now()
    return bool(
        c.execute(
            "UPDATE outbound_actions SET provider_attempted_at=?,attempts=attempts+1,policy_decision=?,updated_at=? "
            "WHERE channel='email' AND id=? AND state='attempting' AND claim_token=? AND provider_attempted_at IS NULL",
            (now, decision, now, row["id"], row["lease_id"]),
        ).rowcount
    )


def finish(
    c: sqlite3.Connection,
    row: Any,
    state: str,
    *,
    detail: str = "",
    decision: str | None = None,
    message: str | None = None,
    thread_id: str = "",
    extend_window: bool = False,
) -> bool:
    """End the send in its current claim (if any), wiping its frozen MIME and keeping its audit and receipt."""
    now = H.now()
    receipt = {"message_id": message} if message is not None else {}
    if thread_id:
        receipt["thread_id"] = thread_id
    return bool(
        c.execute(
            "UPDATE outbound_actions SET state=?,payload='',metadata=json_set(metadata,'$.finished_at',?),"
            "last_error=?,updated_at=?,policy_decision=coalesce(?,policy_decision),"
            "receipt=CASE WHEN ? IS NULL THEN receipt ELSE ? END,send_after=?,claim_token=NULL "
            "WHERE channel='email' AND id=? AND state=? AND coalesce(claim_token,'')=?",
            (
                STATES.get(state, state),
                now,
                detail,
                now,
                decision,
                message,
                json.dumps(receipt),
                max(row["send_after"], now) if extend_window else row["send_after"],
                row["id"],
                STATES.get(row["state"], row["state"]),
                row["lease_id"],
            ),
        ).rowcount
    )
