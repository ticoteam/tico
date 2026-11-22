# pyright: strict
"""Bind the outbound Help reply to the task opened by the HQ watcher."""

import hashlib
import os
import re
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any, cast

from . import auth as _auth, batch_work as _batch_work, email_sending as _email_sending, outbound_http
from . import support as _support
from .store import H as _H, Problem

if TYPE_CHECKING:
    from .outbound_actions import DispatchStopped, SafeRejection

H: Any = _H
support: Any = _support
auth: Any = _auth
batch_work: Any = _batch_work
email_sending: Any = _email_sending
Row = dict[str, Any]

CHANNEL = "help-ticket"
TICKET = re.compile(r"TK-[A-Z2-7]{8}")
WATCH_KEY = "hq:"
ORIGIN_PREFIX = "hq-origin:"
FIX_KEY = "closeout:help:"
FIX_REPLY = (
    "Engineering has finished work on the issue you reported. Thanks for reporting it. "
    "If you still need help with this, reply here."
)


def origin_ref() -> str:
    return ORIGIN_PREFIX + hashlib.sha256(support.hq_url().encode()).hexdigest()


def watched(c: Connection, bot: str, key: str) -> bool:
    """This watcher first opened the ticket on the server's configured HQ."""
    return bool(
        TICKET.fullmatch(key.removeprefix(WATCH_KEY))
        and key.startswith(WATCH_KEY)
        and c.execute("SELECT 1 FROM watcher_refs WHERE bot=? AND key=? AND ref=?", (bot, key, origin_ref())).fetchone()
    )


def pipeline_for(c: Connection, bot: str, key: str) -> str | None:
    from . import routing

    if key and watched(c, bot, key):
        pipeline = routing.support_pipeline(c)
        if not routing.first_stage_refusal(c, pipeline):
            return pipeline
    return None


def _staff(settings: Any) -> str:
    key = os.environ.get("TICO_HQ_STAFF_KEY", "").strip()
    return key if key and settings is not None and not support.off_reason(settings) else ""


def _ticket(c: Connection, actor: str, task_id: str | None, key: str | None = None) -> Row | None:
    if not H.is_bot(actor) or not task_id:
        return None
    bot = H.actor_id(actor)
    support_bot = H.resolve_bot(c, os.environ.get("TICO_HQ_SUPPORT_BOT", "").strip() or "support-owner")
    if not support_bot or support_bot["id"] != bot:
        return None
    predicate, params = ("w.key=?", (bot, task_id, key)) if key is not None else ("w.key LIKE 'hq:%'", (bot, task_id))
    row = c.execute(
        "SELECT w.key, json_extract(t.detail,'$.kind') AS kind "
        "FROM watcher_items w LEFT JOIN watcher_triage t ON t.bot=w.bot AND t.key=w.key "
        f"WHERE w.bot=? AND w.task_id=? AND {predicate}",
        params,
    ).fetchone()
    # Ownership is also support_triage's release rule for a locally held ticket.
    if row and watched(c, bot, row["key"]):
        return dict(row)
    return None


def policy_floor(c: Connection, actor: str) -> str:
    return str(email_sending.effective(c, bot=H.actor_id(actor))["mode"])


def prepare(
    c: Connection,
    settings: Any,
    actor: str,
    destination: str,
    payload: Row,
    *,
    operation: str,
    connection_id: str,
    task_id: str | None = None,
) -> tuple[str, str]:
    if connection_id or not TICKET.fullmatch(destination):
        raise Problem("validation", "A Help reply names one HQ ticket and uses the configured HQ", 422)
    fields = {"body", "last_message_id"} if operation == "answer" else {"body"}
    if operation not in ("reply", "answer") or set(payload) != fields or not isinstance(payload["body"], str):
        raise Problem("validation", "Use reply with body, or answer with body and last_message_id", 422)
    if not payload["body"].strip() or support.CONTROL.search(payload["body"]) or len(payload["body"]) > 8000:
        raise Problem("validation", "A Help reply needs a nonblank body of up to 8000 characters", 422)
    if operation == "answer":
        last = payload["last_message_id"]
        if type(last) is not int or last < 0:
            raise Problem("validation", "last_message_id is the latest customer message ID, or zero", 422)
    if not _staff(settings):
        raise Problem("validation", "Help replies need a staff key on this server", 422)
    ticket = _ticket(c, actor, task_id, WATCH_KEY + destination)
    task = H.task(c, task_id) if ticket else None
    if (
        not ticket
        or not task
        or task["status"] != "open"
        or task["owner_id"] != actor
        or not auth.actor_reads_task(c, actor, task)
    ):
        raise Problem("forbidden", "Only the assigned Support Agent can reply on its open HQ ticket task", 403)
    if operation == "answer" and ticket["kind"] != "question":
        raise Problem("validation", "Only a ticket triaged as a question can settle on reply", 422)
    return "external", destination


def dispatch(store: Any, row: Row) -> "Row | SafeRejection | DispatchStopped":
    from . import outbound_actions as OA

    payload: Row = OA.content(row)["payload"]
    ticket_id = row["destination"]
    path = f"/v1/staff/tickets/{ticket_id}"
    staff = _staff(store.settings)
    if not staff:
        raise OA.Refused("Help replies are disabled")
    try:
        looked = support._ask_hq("GET", path, staff=staff)
        if looked.status_code in (401, 403, 404):
            raise OA.Refused(f"HQ refused the ticket lookup ({looked.status_code})")
        ticket: object = looked.json() if looked.is_success else None
    except (Problem, ValueError):
        ticket = None
    if not isinstance(ticket, dict):
        return OA.SafeRejection(60, "HQ did not confirm the ticket before sending")
    ticket = cast(Row, ticket)
    if ticket.get("ticket_id") != ticket_id:
        return OA.SafeRejection(60, "HQ did not confirm the ticket before sending")
    if (
        ticket.get("status") not in ("open", "answered")
        or ticket.get("held")
        or ticket.get("verdict") in ("spam", "injection_risk")
    ):
        raise OA.Refused("HQ is holding the ticket or it is closed")
    if row["operation"] == "answer":
        try:
            latest = max((int(m["id"]) for m in ticket["messages"] if m["from"] == "person"), default=0)
        except (KeyError, TypeError, ValueError):
            return OA.SafeRejection(60, "HQ did not return the ticket's messages")
        if latest != payload["last_message_id"]:
            raise OA.Refused("There is a newer customer message. Read the ticket before answering.")
    with store.transaction() as c:
        try:
            if stopped := OA.bot_stopped(c, row["actor"]):
                raise OA.Refused(stopped)
            prepare(
                c,
                store.settings,
                row["actor"],
                ticket_id,
                payload,
                operation=row["operation"],
                connection_id=row["connection_id"],
                task_id=row["task_id"],
            )
        except Problem as exc:
            problem: Any = exc
            raise OA.Refused(str(problem.detail)) from None
        if row["occurrence_key"].startswith(FIX_KEY) and not _engineering_ready(c, row["task_id"], row["actor"]):
            raise OA.Refused("The Support Agent can no longer confirm the Engineering result")
        if stopped := OA.recheck_dispatch_policy(c, row, "external"):
            return stopped
    try:
        answer = support._ask_hq("POST", path + "/reply", body={"body": payload["body"]}, staff=staff)
        if answer.status_code == 429:
            wait = outbound_http.retry_after(answer.headers)
            return OA.SafeRejection(min(wait, 86400) if wait is not None else 60, "HQ answered 429")
        if answer.status_code in (400, 401, 403, 404, 409, 422):
            raise OA.Refused(f"HQ refused the reply ({answer.status_code})")
        record: object = answer.json() if answer.is_success else None
    except (Problem, ValueError):
        record = None
    receipt = cast(Row, record) if isinstance(record, dict) else {}
    if receipt.get("ticket_id") != ticket_id or type(receipt.get("reply_id")) is not int or receipt["reply_id"] <= 0:
        raise RuntimeError("HQ returned no confirmed reply receipt")
    return {"provider_id": receipt["reply_id"], "email_pending": bool(receipt.get("email_pending"))}


def on_sent(c: Connection, row: Row, receipt: Row) -> None:
    from . import outbound_actions as OA, watchers

    task_id = row["task_id"]
    if not (receipt.get("provider_id") or receipt.get("confirmed_by")) or not task_id or not H.task(c, task_id):
        return
    payload = OA.content(row)["payload"]
    if not isinstance(payload.get("body"), str):
        return
    defang = cast("Any", watchers.defang)  # pyright: ignore[reportUnknownMemberType] watcher sanitizes untyped inputs
    recorded = False
    with batch_work.isolated(c, "help_reply_note", row["id"], private=True):
        text = f"Reply posted on {row['destination']}:\n\n" + defang(payload["body"], c)
        H.task_comment(c, H.TICO_ACTOR, task_id, text, wake=False)
        recorded = True
    if not recorded or row["operation"] != "answer":
        return
    task = H.task(c, task_id)
    if task["status"] != "open" or task["owner_id"] != row["actor"]:
        return
    newer = c.execute(
        "SELECT 1 FROM watcher_refs WHERE bot=? AND key=? AND ref GLOB 'msg:[0-9]*' "
        "AND CAST(substr(ref,5) AS INTEGER)>?",
        (H.actor_id(row["actor"]), WATCH_KEY + row["destination"], payload["last_message_id"]),
    ).fetchone()
    if not newer:
        with batch_work.isolated(c, "help_answer_done", row["id"], private=True):
            H.task_done(c, H.TICO_ACTOR, task_id, "Answered from the docs on the Help ticket.")


def _engineering_ready(c: Connection, parent: str, actor: str, child: str | None = None) -> bool:
    rows = c.execute(
        "SELECT e.* FROM tasks e JOIN pipelines p ON p.id=e.pipeline_id "
        "JOIN links l ON l.to_task_id=e.id AND l.kind='sub_task' "
        "WHERE l.from_task_id=? AND e.requester_id=? AND p.preset='engineering' AND (? IS NULL OR e.id=?) "
        "AND EXISTS (SELECT 1 FROM task_links WHERE task_id=e.id AND kind='issue' "
        "AND lower(url) LIKE 'https://github.com/ticoteam/tico/issues/%') "
        "AND (e.status='done' OR EXISTS (SELECT 1 FROM task_links WHERE task_id=e.id AND kind='pr' "
        "AND lower(repo)='ticoteam/tico' AND state IN ('merged','shipped'))) ORDER BY e.id LIMIT 25",
        (parent, actor, child, child),
    )
    return any(auth.actor_reads_task(c, actor, dict(row)) for row in rows)


def engineering_finished(c: Any, task_id: str) -> None:
    """The issue link and Support-filed subtask identify a bug handed to Engineering."""
    settings = getattr(c, "settings", None)
    if not _staff(settings):
        return
    from . import outbound_actions as OA

    row = c.execute(
        "SELECT e.requester_id, l.from_task_id AS parent FROM tasks e "
        "JOIN links l ON l.to_task_id=e.id AND l.kind='sub_task' WHERE e.id=?",
        (task_id,),
    ).fetchone()
    if not row:
        return
    actor, parent = row["requester_id"], row["parent"]
    ticket = _ticket(c, actor, parent)
    if not ticket:
        return
    task = H.task(c, parent)
    if (
        not task
        or task["status"] != "open"
        or task["owner_id"] != actor
        or not auth.actor_reads_task(c, actor, task)
        or not _engineering_ready(c, parent, actor, task_id)
    ):
        return
    key = f"{FIX_KEY}{origin_ref()}:{ticket['key']}"
    if c.execute("SELECT 1 FROM outbound_actions WHERE occurrence_key=?", (key,)).fetchone():
        return
    with batch_work.isolated(c, "help_engineering_reply", parent, private=True):
        OA.enqueue(
            c,
            settings,
            actor=actor,
            channel=CHANNEL,
            operation="reply",
            destination=ticket["key"].removeprefix(WATCH_KEY),
            occurrence_key=key,
            payload={"body": FIX_REPLY},
            task_id=parent,
            system_occurrence=True,
        )
        H.task_comment(
            c,
            H.TICO_ACTOR,
            parent,
            "Engineering reply ready for review:\n\n" + FIX_REPLY,
            wake=False,
        )
