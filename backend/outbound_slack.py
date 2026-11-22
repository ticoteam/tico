# pyright: strict
"""Message-backed Slack actions, sent only by the Socket Mode gateway."""

import json
import logging
import re
import socket
from contextlib import ExitStack, contextmanager
from pathlib import Path
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, NoReturn

from . import hubdb as _H
from . import outbox as _O
from .store import Problem
from .slack_errors import RATE_LIMIT_DEFAULT, SlackError, SlackUnreachable

if TYPE_CHECKING:
    from .outbound_actions import AttemptResult, SafeRejection

H: Any = _H
O: Any = _O
LOG = logging.getLogger(__name__)

CHANNEL = "slack"
PREFIX = "slack:"
POST_PREFIX = PREFIX + "post:"
TS_RE = r"^[0-9]{6,12}\.[0-9]{1,6}$"
DEFAULTS = {"internal": "on_their_own", "external": "never"}
POLICY_CHOICES = {"internal": ("on_their_own", "never"), "external": ("never",)}
# Treat unrecognized Slack errors as ambiguous. These codes describe a rejected post.
REFUSALS = frozenset(
    {
        "access_denied",
        "account_inactive",
        "cannot_reply_to_message",
        "channel_not_found",
        "invalid_arguments",
        "invalid_auth",
        "invalid_blocks",
        "invalid_blocks_format",
        "is_archived",
        "missing_scope",
        "msg_blocks_too_long",
        "msg_too_long",
        "no_permission",
        "no_text",
        "not_allowed_token_type",
        "not_authed",
        "not_in_channel",
        "restricted_action",
        "restricted_action_non_threadable_channel",
        "restricted_action_read_only_channel",
        "restricted_action_thread_locked",
        "restricted_action_thread_only_channel",
        "token_expired",
        "token_revoked",
    }
)
# Keep the existing Slack status response while the shared queue uses its own state names.
STATES = {"queued": "ready", "attempting": "sending", "done": "sent", "unknown": "uncertain"}
LEGACY_MARK = "$._tico.slack_legacy"
LEGACY_STATES = {
    "ready": "queued",
    "retry": "queued",
    "rate_limited": "queued",
    "sending": "unknown",
    "uncertain": "unknown",
    "sent": "done",
    "canceled": "canceled",
    "failed": "failed",
}
LEGACY_BATCH = 20
POSTS = """
    SELECT a.id AS message_id, a.destination AS channel,
           CASE WHEN a.actor LIKE 'bot:%' THEN substr(a.actor,5) ELSE '' END AS bot,
           coalesce(json_extract(a.metadata,'$.thread_ts'),'') AS thread_ts,
           json_extract(a.metadata,'$.recipient_actor') AS recipient_actor,
           CASE WHEN json_extract(a.metadata,'$.redacted') THEN ''
                WHEN a.state='done' THEN coalesce(json_extract(a.metadata,'$._tico.sent_text'),'')
                WHEN a.payload<>'' THEN coalesce(json_extract(a.payload,'$.text'),'')
                ELSE '' END AS text,
           a.state, a.attempts, a.next_attempt_at, a.created_at, a.updated_at,
           json_extract(a.receipt,'$.ts') AS slack_ts, nullif(a.last_error,'') AS error
    FROM outbound_actions a WHERE a.channel='slack'
"""


def status(row: Any) -> str:
    state = str(row["state"])
    if state == "queued" and row["error"] == "ratelimited":
        return "rate_limited"
    return STATES.get(state, state)


def _socket_path(db_path: str | Path) -> str:
    path = Path(db_path).resolve()
    return str(path.with_name(path.name + ".slack.sock"))


def ring(db_path: str | Path | None) -> None:
    """Best effort across the API and gateway processes. The gateway's normal tick catches a missed ring."""
    if not db_path or not hasattr(socket, "AF_UNIX"):
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as bell:
            bell.setblocking(False)
            bell.sendto(b"1", _socket_path(db_path))
    except OSError:
        pass


@contextmanager
def listener(db_path: str | Path) -> Generator[socket.socket | None]:
    """A gateway already owns the workspace connection. A second listener falls back to its normal tick."""
    if not hasattr(socket, "AF_UNIX"):
        yield None
        return
    import fcntl

    path = _socket_path(db_path)
    with ExitStack() as stack:
        try:
            lock = stack.enter_context(open(path + ".lock", "a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            Path(path).unlink(missing_ok=True)
            bell = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM))
            bell.bind(path)
        except OSError:
            yield None
            return
        try:
            yield bell
        finally:
            Path(path).unlink(missing_ok=True)


def conflict(message_id: str) -> NoReturn:
    LOG.error("Slack outbound action conflicts for message %s", message_id)
    raise Problem("conflict", "This Slack outbound action conflicts with an existing action", 409)


def queue(
    c: Any,
    message: dict[str, Any],
    bot: str,
    channel: str,
    thread_ts: str,
    text: str,
    *,
    recipient: str | None = None,
    key: str | None = None,
    error: str = "",
    now: str | None = None,
) -> str:
    """Queue the exact message and its Slack destination in the producer's own write."""
    from . import outbound_actions as OA

    conv = H.conversation(c, message["conversation_id"]) if message.get("conversation_id") else None
    mid, at = str(message["id"]), now or H.now()
    if key is None:
        base = POST_PREFIX + mid
        key, suffix = base, 0
        # Non-Slack actions can still have keys from before this namespace was reserved.
        while c.execute(
            "SELECT 1 FROM outbound_actions WHERE occurrence_key=? AND channel<>'slack'", (key,)
        ).fetchone():
            suffix += 1
            key = f"{base}:{suffix}"
    row: dict[str, Any] = {
        "id": mid,
        "channel": CHANNEL,
        "audience": "external",  # only the gateway can classify the live destination
        "operation": "post",
        "destination": channel,
        "occurrence_key": key,
        "actor": H.bot_actor(bot) if bot else H.TICO_ACTOR,
        "payload": json.dumps({"text": text}) if text and not error else "",
        "metadata": json.dumps({"thread_ts": thread_ts, "recipient_actor": recipient}),
        "state": "canceled" if error else "queued",
        "send_after": at,
        "next_attempt_at": at,
        "created_at": at,
        "updated_at": at,
        "task_id": H.message_task_id(message, conv),
        "last_error": error,
    }
    made = c.execute(
        f"INSERT INTO outbound_actions({','.join(row)}) VALUES({','.join('?' * len(row))}) ON CONFLICT DO NOTHING",
        tuple(row.values()),
    ).rowcount
    if not made:
        conflict(mid)
    if not error:
        db_path = c.execute("PRAGMA database_list").fetchone()[2]
        OA.after_commit(c, lambda: ring(db_path))
    return mid


def withdraw(c: Any, message_id: str, *, redact: bool = True) -> None:
    """Cancel an unsent copy. Deletion also wipes the local snapshot of what Slack already had."""
    from . import outbound_actions as OA

    row = OA.get(c, message_id)
    if not row or row["channel"] != CHANNEL:
        return
    if row["state"] in OA.OPEN or (row["state"] == "attempting" and not row["provider_attempted_at"]):
        OA.settle(c, row, "canceled", error="The message was withdrawn")
    if redact:
        c.execute(
            "UPDATE outbound_actions SET payload='', "
            "metadata=json_set(json_remove(metadata,'$._tico.sent_text'),'$.redacted',1), updated_at=? WHERE id=?",
            (H.now(), message_id),
        )
    elif row["state"] == "attempting" and row["provider_attempted_at"]:
        # Keep the permitted text for a receipt, but a definite rejection must not retry the old edit.
        c.execute(
            "UPDATE outbound_actions SET payload=json_remove(payload,'$.text'), updated_at=? "
            "WHERE id=? AND payload<>''",
            (H.now(), message_id),
        )


def adopt_legacy(c: Any, row: dict[str, Any]) -> "AttemptResult | None":
    """Read the old gateway's result in the same transaction as the new gateway's permit."""
    from . import outbound_actions as OA

    seen = json.loads(row["metadata"]).get("_tico", {}).get("slack_legacy")
    if seen is None:
        return None
    post = c.execute("SELECT * FROM slack_posts WHERE message_id=?", (row["id"],)).fetchone()
    if not post:
        return None
    mark = f"{post['state']}:{post['updated_at']}"
    if mark == seen:
        return None

    state = LEGACY_STATES[post["state"]]
    error = post["error"] or ("ratelimited" if post["state"] == "rate_limited" else "")
    c.execute("UPDATE outbound_actions SET metadata=json_set(metadata,?,?) WHERE id=?", (LEGACY_MARK, mark, row["id"]))
    if row["state"] == "done" or (state != "done" and (row["state"] in OA.ENDED or row["provider_attempted_at"])):
        return None

    attempts = max(row["attempts"], post["attempts"])
    if state == "queued":
        if row["state"] not in ("queued", "attempting"):
            return None  # A legacy retry cannot reopen a canceled, blocked or ambiguous send.
        O.finish(
            c,
            OA.TABLE,
            row["id"],
            "queued",
            attempts=attempts,
            error=error,
            due_at=max(row["next_attempt_at"], post["next_attempt_at"] or post["created_at"]),
            extra={"updated_at": H.now()},
        )
        return OA.AttemptResult("requeued", error=error)
    if row["state"] == state:
        c.execute(
            "UPDATE outbound_actions SET attempts=?,last_error=?,updated_at=? WHERE id=?",
            (attempts, error, H.now(), row["id"]),
        )
        return None
    receipt = {"ts": post["slack_ts"]} if state == "done" and post["slack_ts"] else {}
    if state == "done":
        row = {**row, "payload": json.dumps({"sent_text": post["text"]})}
    OA.settle(c, {**row, "attempts": attempts}, state, receipt=receipt, error=error)
    return OA.AttemptResult(state, receipt, error)


def adopt_pass(store: Any, after: str) -> str:
    """Check at most one batch of migrated ids each tick, without locking a quiet queue for writes."""
    from . import outbound_actions as OA

    with store.read() as c:
        rows = c.execute(
            f"SELECT a.id,(p.state || ':' || p.updated_at)<>a.seen AS changed FROM "
            f"(SELECT id,json_extract(metadata,'{LEGACY_MARK}') AS seen FROM outbound_actions "
            f"WHERE channel='slack' AND json_extract(metadata,'{LEGACY_MARK}') IS NOT NULL AND id>? "
            "ORDER BY id LIMIT ?) a LEFT JOIN slack_posts p ON p.message_id=a.id ORDER BY a.id",
            (after, LEGACY_BATCH),
        ).fetchall()
    if changed := [row["id"] for row in rows if row["changed"]]:
        with store.transaction() as c:
            for mid in changed:
                if row := OA.get(c, mid):
                    adopt_legacy(c, row)
    return rows[-1]["id"] if len(rows) == LEGACY_BATCH else ""


def dispatch(gateway: Any, row: dict[str, Any]) -> "dict[str, str] | SafeRejection":
    """The provider call that follows the gateway's durable permit."""
    from . import outbound_actions as OA

    if target := row.get("_closeout_jira"):
        from . import outbound_jira

        if wait := outbound_jira.closeout_read(gateway.store, target):
            return wait
    try:
        answer = gateway.slack.post_message(row["destination"], row["_slack_thread"] or None, **row["_slack_body"])
    except SlackError as exc:
        if exc.code in ("ratelimited", "rate_limited"):
            return OA.SafeRejection(exc.retry_after or RATE_LIMIT_DEFAULT, "ratelimited")
        if exc.code in REFUSALS:
            raise OA.Refused(exc.code) from None
        raise SlackUnreachable("Slack may have posted the message") from None
    ts = str(answer.get("ts") or "")
    if not re.fullmatch(TS_RE, ts):
        raise SlackUnreachable("Slack returned no valid message timestamp")
    return {"ts": ts}
