# pyright: strict
"""Bind the outbound Help reply to the task opened by the HQ watcher."""

import hashlib
import os
import re
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any, cast

from . import auth as _auth, batch_work as _batch_work, email_sending as _email_sending, outbound_http
from . import support as _support
from .entities import store as entities
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
STALE_REPLY = "The customer messages changed. Read the ticket and queue a fresh draft."
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


def _support_bot(c: Connection) -> Row | None:
    return H.resolve_bot(c, os.environ.get("TICO_HQ_SUPPORT_BOT", "").strip() or "support-owner")


def _read_staff(settings: Any) -> str:
    if not settings or support.off_reason(settings):
        return ""
    return os.environ.get("TICO_HQ_STAFF_READ_KEY", "").strip() or _staff(settings)


def can_verify(c: Connection, settings: Any, bot: str, key: str) -> bool:
    return bool(_read_staff(settings) and (_support_bot(c) or {}).get("id") == bot and watched(c, bot, key))


def verified_company(store: Any, bot: str, key: str) -> tuple[str, str] | None:
    """Resolve the company from HQ's staff response. Watcher events can supply ticket IDs, not identity proofs."""
    base = str(support.hq_url())
    with store.read() as c:
        if not can_verify(c, store.settings, bot, key):
            return None
    staff = _read_staff(store.settings)
    if not staff:
        return None
    ticket_id = key.removeprefix(WATCH_KEY)
    try:
        response = support._ask_hq("GET", f"/v1/staff/tickets/{ticket_id}", staff=staff, base=base)
        ticket: object = response.json() if response.is_success else None
    except (Problem, ValueError):
        return None
    if not isinstance(ticket, dict):
        return None
    record = cast(Row, ticket)
    install_id: object = record.get("verified_install_id")
    if record.get("ticket_id") != ticket_id or not isinstance(install_id, str) or not install_id.strip():
        return None
    company_id: object = record.get("verified_company_id")
    if not isinstance(company_id, str) or not company_id.strip():
        return None
    company: object = record.get("company")
    details = cast(Row, company) if isinstance(company, dict) else {}
    name: object = details.get("name") if details.get("id") == company_id else None
    return company_id, name.strip() if isinstance(name, str) and name.strip() else company_id


def directory_entity(c: Connection, company: tuple[str, str] | None) -> str:
    if company is None:
        return ""
    company_id, name = company
    system, kind = "tico-hq", "company"
    existing = entities.verified_external_ref(c, H.TICO_ACTOR, system, "", kind, company_id)
    if existing:
        return existing
    # The external-ref writer enforces suppression and conflicts. Roll back any new entity if it refuses the link.
    with batch_work.isolated(c, "help_company_link", "", private=True):
        created = entities.upsert(c, H.TICO_ACTOR, "organization", match={}, name=name)
        entity_id = str(created["id"])
        entities.external_ref(c, H.TICO_ACTOR, entity_id, system, kind, company_id)
        return entity_id
    return ""


def _ticket(c: Connection, actor: str, task_id: str | None, key: str | None = None) -> Row | None:
    if not H.is_bot(actor) or not task_id:
        return None
    bot = H.actor_id(actor)
    support_bot = _support_bot(c)
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


def reply_body(value: object) -> str:
    if not isinstance(value, str) or support.CONTROL.search(value):
        raise Problem("validation", "A Help reply needs a nonblank body of up to 8000 characters", 422)
    body = value.replace("\r\n", "\n").replace("\r", "\n")
    if not body.strip() or len(body) > 8000:
        raise Problem("validation", "A Help reply needs a nonblank body of up to 8000 characters", 422)
    return body.strip()


def reply_key(ticket: str, body: str) -> str:
    return f"closeout:help-reply:{origin_ref()}:{ticket}:{hashlib.sha256(body.encode()).hexdigest()}"


def _observed_message(c: Connection, actor: str, key: str) -> int:
    return int(
        c.execute(
            "SELECT coalesce(max(CAST(substr(ref,5) AS INTEGER)),0) FROM watcher_refs "
            "WHERE bot=? AND key=? AND ref GLOB 'msg:[0-9]*'",
            (H.actor_id(actor), key),
        ).fetchone()[0]
    )


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
    fields = {"body", "last_message_id"} if operation == "answer" or "last_message_id" in payload else {"body"}
    if operation not in ("reply", "answer") or set(payload) != fields or not isinstance(payload["body"], str):
        raise Problem("validation", "Use body and the latest customer message ID for an answer or reply", 422)
    reply_body(payload["body"])
    if "last_message_id" in payload:
        last = payload["last_message_id"]
        if type(last) is not int or not 0 <= last < 2**63:
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


def _stale(store: Any, row: Row) -> "DispatchStopped":
    from . import outbound_actions as OA

    with store.transaction() as c:
        saved = OA.settle(c, row, "blocked", error=STALE_REPLY, token=row["claim_token"])
    return OA.DispatchStopped("blocked" if saved else "lost")


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
    # A legacy body-only action has no recorded follow-up to authorize a send against.
    expected = payload.get("last_message_id", 0)
    try:
        latest = max((int(m["id"]) for m in ticket["messages"] if m["from"] == "person"), default=0)
    except (KeyError, TypeError, ValueError):
        return OA.SafeRejection(60, "HQ did not return the ticket's messages")
    if latest != expected:
        return _stale(store, row)
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
        if OA.asked(row).get("help_engineering") and not _engineering_ready(c, row["task_id"], row["actor"]):
            raise OA.Refused("The Support Agent can no longer confirm the Engineering result")
        if stopped := OA.recheck_dispatch_policy(c, row, "external"):
            return stopped
    try:
        answer = support._ask_hq(
            "POST", path + "/reply", body={"body": payload["body"], "expected_last_message_id": expected}, staff=staff
        )
        if answer.status_code == 429:
            wait = outbound_http.retry_after(answer.headers)
            return OA.SafeRejection(min(wait, 86400) if wait is not None else 60, "HQ answered 429")
        if answer.status_code == 409:
            try:
                refusal: object = answer.json()
            except ValueError:
                refusal = None
            if isinstance(refusal, dict) and cast(Row, refusal).get("error") == "stale_reply":
                return _stale(store, row)
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
    with batch_work.isolated(c, "help_engineering_reply", parent, private=True):
        _, created = OA.enqueue(
            c,
            settings,
            actor=actor,
            channel=CHANNEL,
            operation="reply",
            destination=ticket["key"].removeprefix(WATCH_KEY),
            occurrence_key=CHANNEL,
            payload={"body": FIX_REPLY, "last_message_id": _observed_message(c, actor, ticket["key"])},
            task_id=parent,
            server_metadata={"help_engineering": "yes"},
        )
        if not created:
            return
        H.task_comment(
            c,
            H.TICO_ACTOR,
            parent,
            "Engineering reply ready for review:\n\n" + FIX_REPLY,
            wake=False,
        )
