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


def _post_key(c: Any, message_id: str) -> str:
    base = POST_PREFIX + message_id
    key, suffix = base, 0
    # Before the namespace was reserved, other actions could use these keys.
    while c.execute("SELECT 1 FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone():
        suffix += 1
        key = f"{base}:{suffix}"
    return key


def _insert(c: Any, row: dict[str, Any]) -> None:
    if not c.execute(
        f"INSERT INTO outbound_actions({','.join(row)}) VALUES({','.join('?' * len(row))}) ON CONFLICT DO NOTHING",
        tuple(row.values()),
    ).rowcount:
        conflict(row["id"])


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
    row: dict[str, Any] = {
        "id": mid,
        "channel": CHANNEL,
        "audience": "external",  # only the gateway can classify the live destination
        "operation": "post",
        "destination": channel,
        "occurrence_key": key if key is not None else _post_key(c, mid),
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
    _insert(c, row)
    # Old gateways dedupe by message id, before choosing a destination of their own.
    c.execute(
        "INSERT OR IGNORE INTO slack_posts(message_id,channel,thread_ts,bot,text,state,error,created_at,updated_at,"
        "recipient_actor) VALUES(?,?,?,?,'','canceled','outbound',?,?,?)",
        (mid, channel, thread_ts, bot, at, at, recipient),
    )
    if not error:
        db_path = c.execute("PRAGMA database_list").fetchone()[2]
        OA.after_commit(c, lambda: ring(db_path))
    return mid


def _cancel_pending(c: Any, message_id: str, error: str) -> bool:
    return bool(
        c.execute(
            "UPDATE slack_posts SET state='canceled',text='',error=?,updated_at=? "
            "WHERE message_id=? AND state IN ('ready','retry','rate_limited')",
            (error, H.now(), message_id),
        ).rowcount
    )


def fence_legacy(c: Any, message_id: str) -> None:
    """Take over a pending row while SQLite still holds the permit's write lock."""
    if _cancel_pending(c, message_id, "migrated"):
        c.execute("UPDATE outbound_actions SET metadata=json_remove(metadata,?) WHERE id=?", (LEGACY_MARK, message_id))


def withdraw(c: Any, message_id: str, *, redact: bool = True) -> None:
    """Cancel an unsent copy. Deletion also wipes the local snapshot of what Slack already had."""
    from . import outbound_actions as OA

    _cancel_pending(c, message_id, "The message was withdrawn")
    if redact:
        c.execute("UPDATE slack_posts SET text='' WHERE message_id=? AND text<>''", (message_id,))
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

    post = c.execute("SELECT * FROM slack_posts WHERE message_id=?", (row["id"],)).fetchone()
    if not post or (post["state"] == "canceled" and post["error"] in ("outbound", "migrated")):
        return None
    seen = OA.asked(row).get("slack_legacy", "")
    mark = f"{post['state']}:{post['updated_at']}"
    state = LEGACY_STATES[post["state"]]
    if mark == seen and (row["state"] != "attempting" or state == "queued"):
        return None
    error = post["error"] or ("ratelimited" if post["state"] == "rate_limited" else "")
    if state in OA.ENDED or row["state"] == "done":
        c.execute("UPDATE outbound_actions SET metadata=json_remove(metadata,?) WHERE id=?", (LEGACY_MARK, row["id"]))
    else:
        c.execute(
            "UPDATE outbound_actions SET metadata=json_set(metadata,?,?) WHERE id=?", (LEGACY_MARK, mark, row["id"])
        )
    if row["state"] == "done" or (
        state != "done" and (row["state"] in OA.ENDED or row["provider_attempted_at"] is not None)
    ):
        return None

    attempts = max(row["attempts"], post["attempts"])
    if state == "queued":
        rejected = (
            row["state"] == "unknown"
            and seen.startswith("sending:")
            and post["state"] == "rate_limited"
            and not json.loads(row["metadata"]).get("redacted")
            and bool(OA.content(row)["payload"].get("text"))
        )
        if not rejected and row["state"] not in ("queued", "attempting"):
            return None
        if rejected and (ask := OA.asked(row).get("ask")):
            from . import owner_asks

            owner_asks.withdraw(c, ask, "Slack rejected the old attempt. It will be retried.", row["task_id"])
            c.execute(
                "UPDATE outbound_actions SET metadata=json_remove(metadata,'$._tico.ask') WHERE id=?", (row["id"],)
            )
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
    OA.settle(c, {**row, "attempts": attempts}, state, receipt=receipt, error=error, adopted=True)
    return OA.AttemptResult(state, receipt, error)


def _import_legacy(c: Any, post: Any, now: str) -> None:
    """Import the frozen destination and outcome of a message first queued by an old gateway."""
    mid = post["message_id"]
    state = LEGACY_STATES[post["state"]]
    text = post["text"]
    metadata = {"thread_ts": post["thread_ts"], "recipient_actor": post["recipient_actor"]}
    if not text:
        metadata["redacted"] = True
    if state == "done" and text:
        metadata["_tico"] = {"sent_text": text}
    if state in ("queued", "unknown"):
        metadata["_tico"] = {"slack_legacy": f"{post['state']}:{post['updated_at']}"}
    message = H.message(c, mid)
    conv = H.conversation(c, message["conversation_id"]) if message and message["conversation_id"] else None
    _insert(
        c,
        {
            "id": mid,
            "channel": CHANNEL,
            "audience": "external",
            "operation": "post",
            "destination": post["channel"],
            "occurrence_key": _post_key(c, mid),
            "actor": H.bot_actor(post["bot"]) if post["bot"] else H.TICO_ACTOR,
            "payload": json.dumps({"text": text}) if text and state in ("queued", "unknown") else "",
            "metadata": json.dumps(metadata),
            "state": state,
            "attempts": post["attempts"],
            "send_after": H.shift(now, minutes=2)
            if state == "queued" or post["state"] == "sending"
            else post["created_at"],
            "next_attempt_at": post["next_attempt_at"] or post["created_at"],
            "receipt": json.dumps({"ts": post["slack_ts"]}) if post["slack_ts"] else "{}",
            "last_error": post["error"] or ("ratelimited" if post["state"] == "rate_limited" else ""),
            "created_at": post["created_at"],
            "updated_at": post["updated_at"],
            "task_id": H.message_task_id(message, conv) if message else None,
        },
    )


def adopt_pass(store: Any, after: tuple[str, int, str], now: str) -> tuple[tuple[str, int, str], bool]:
    """Check changes to known posts and discover newer legacy rows in bounded batches."""
    from . import outbound_actions as OA

    changed_after, added_after, anchor = after
    with store.read() as c:
        rows = c.execute(
            f"SELECT a.id,(p.state || ':' || p.updated_at)<>a.seen AS changed FROM "
            f"(SELECT id,json_extract(metadata,'{LEGACY_MARK}') AS seen FROM outbound_actions "
            f"WHERE channel='slack' AND json_extract(metadata,'{LEGACY_MARK}') IS NOT NULL AND id>? "
            "ORDER BY id LIMIT ?) a LEFT JOIN slack_posts p ON p.message_id=a.id ORDER BY a.id",
            (changed_after, LEGACY_BATCH),
        ).fetchall()
        # Both gateways insert with implicit rowids, and live delivery retains the rows for dedupe.
        if (
            added_after
            and not c.execute(
                "SELECT 1 FROM slack_posts WHERE message_id=? AND rowid=?", (anchor, added_after)
            ).fetchone()
        ):
            added_after, anchor = 0, ""  # The database can renumber rowids during a VACUUM.
        added = c.execute(
            "SELECT p.rowid,p.message_id,a.id AS copied FROM slack_posts p "
            "LEFT JOIN outbound_actions a ON a.id=p.message_id WHERE p.rowid>? ORDER BY p.rowid LIMIT ?",
            (added_after, LEGACY_BATCH),
        ).fetchall()
    changed = [row["id"] for row in rows if row["changed"]]
    missing = [row["message_id"] for row in added if row["copied"] is None]
    if changed or missing:
        with store.transaction() as c:
            for mid in missing:
                post = c.execute(
                    "SELECT * FROM slack_posts WHERE message_id=? "
                    "AND NOT EXISTS (SELECT 1 FROM outbound_actions WHERE id=?)",
                    (mid, mid),
                ).fetchone()
                if post:
                    _import_legacy(c, post, now)
            for mid in changed:
                if row := OA.get(c, mid):
                    adopt_legacy(c, row)
    if added:
        added_after, anchor = added[-1]["rowid"], added[-1]["message_id"]
    changed_after = rows[-1]["id"] if len(rows) == LEGACY_BATCH else ""
    return (changed_after, added_after, anchor), len(added) == LEGACY_BATCH


def dispatch(gateway: Any, row: dict[str, Any]) -> "dict[str, str] | SafeRejection":
    """The provider call that follows the gateway's durable permit."""
    from . import outbound_actions as OA

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
