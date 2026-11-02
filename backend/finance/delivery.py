"""Invoice emails: the send approval a reminder or an invoice goes out under, and how each send went.

A reminder or an invoice reaches its customer only under a Tico `send` approval (docs/mail.md). Finance issues it when the
action commits, for these recipients, this exact text and this invoice document, approved at once by the person whose
yes the action waited on (or the person who made it). The Bookkeeper sends the text with that approval
(`mail send --approval-issue`) and reports the outcome through `invoice_delivery`. Finance sends no mail itself, and a
bot cannot issue or decide one. A failed send raises one Needs-you for the finance admin; a send with no report for a
day raises one more. A retry sends the same text under the same approval, and a stage is never made again.
"""
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from clients.manifest import tools_of

from .context import Ctx
from .core.errors import CONFLICT, DENIED, FinanceError
from .store import ap as SAP
from .store import tico

BOOKKEEPER = "bookkeeper"     # the Bookkeeper's slug; its stored bot config names the mailbox
SEND = "send"                 # the Tico approval kind a mail send is checked against
GMAIL = "gmail"               # the connector the Bookkeeper sends from


def digest(text: str) -> str:
    """The sha256 an approval's body_sha256 holds for this exact text (the mail connector hashes the body it sends)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_digest(document: Mapping[str, Any]) -> str:
    """The sha256 of the invoice document the email is about, as Finance showed it when the approval was made."""
    return hashlib.sha256(json.dumps(document, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def recipients(emails: Any) -> list[str]:
    """An approval's `to`: the customer's addresses, trimmed, lower-case, sorted, each once."""
    return sorted({str(e).strip().lower() for e in emails or [] if str(e).strip()})


def subject_of(stage: int | None, number: str) -> str:
    return f"Invoice {number} is unpaid" if stage is not None else f"Invoice {number}"


def mailbox(c: Any) -> str:
    """The address the Bookkeeper sends from: its gmail tool in the stored bot config, with `send` in its `can`. Empty
    when it has none; then a reminder is refused and an invoice is sent without an email."""
    row = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (BOOKKEEPER,)).fetchone()
    if row is None:
        return ""
    try:
        config = json.loads(row[0] or "{}")
    except ValueError:
        return ""
    if not isinstance(config, Mapping):
        return ""
    for entry in tools_of(config) or []:
        if not isinstance(entry, Mapping) or str(entry.get("service") or "") != GMAIL:
            continue
        if SEND not in (entry.get("can") or []):
            continue
        box = str(entry.get("identity") or "").strip().lower()
        if "@" in box:
            return box
    return ""


def issue(c: Any, decider: str, requester: str, *, to: Any, subject: str, text: str, box: str,
          document: Mapping[str, Any], ref: str) -> str:
    """The send approval for one invoice email, approved by `decider` at once. Only Finance's commit path calls this,
    for the person whose decision the action waits on; a bot is refused. Returns the approval id."""
    if not tico.is_person(decider):
        raise FinanceError(DENIED, "an invoice email is approved by a person, not by a bot")
    payload = {"to": recipients(to), "cc": [], "subject": subject, "body_sha256": digest(text), "mailbox": box,
               "document_sha256": document_digest(document), "ref": ref}
    approval_id = tico.approval_request(c, requester, SEND, payload, decider)
    tico.approval_decide(c, decider, approval_id, "approved")
    return approval_id


def pending(c: Any, invoice_id: str | None = None) -> list[dict[str, Any]]:
    """Invoice emails still waiting on a delivery report, oldest first: the invoice's own email (stage None) and each
    reminder's. With `invoice_id`, only that invoice's."""
    where = " AND i.id=?" if invoice_id else ""
    args = (invoice_id, invoice_id) if invoice_id else ()
    rows = c.execute(
        "SELECT i.id, i.number, NULL, i.send_approval_id, i.send_to, i.send_text, i.delivery_reason, i.sent_at AS since "
        "FROM fin_invoices i WHERE i.send_approval_id IS NOT NULL AND i.delivered_at IS NULL" + where + " "
        "UNION ALL "
        "SELECT i.id, i.number, r.stage, r.send_approval_id, r.sent_to, r.text, r.delivery_reason, r.created_at "
        "FROM fin_invoice_reminders r JOIN fin_invoices i ON i.id = r.invoice_id "
        "WHERE r.send_approval_id IS NOT NULL AND r.delivered_at IS NULL"
        + (" AND i.id=?" if invoice_id else "") + " ORDER BY since", args).fetchall()
    return [{"invoice_id": str(r[0]), "number": str(r[1]), "stage": r[2], "approval_id": str(r[3]),
             "to": recipients(json.loads(r[4] or "[]")), "subject": subject_of(r[2], str(r[1])),
             "text": str(r[5] or ""), "reason": r[6], "since": str(r[7] or "")} for r in rows]


def _which(stage: int | None) -> str:
    return f" ({stage}-day reminder)" if stage is not None else ""


def report(c: Any, at: str, invoice_id: str, stage: int | None, delivered: bool, reason: str, person: str) -> None:
    """Record how a send went: delivered, or failed with its reason (one Needs-you for `person`, the finance admin).
    A failed email stays waiting, so the retry sends the same text under the same approval."""
    if stage is None:
        table, where, args = "fin_invoices", "id=?", (invoice_id,)
    else:
        table, where, args = "fin_invoice_reminders", "invoice_id=? AND stage=?", (invoice_id, stage)
    row = c.execute(f"SELECT id, send_approval_id, delivered_at FROM {table} WHERE {where}", args).fetchone()
    if row is None or not row[1]:
        raise FinanceError(CONFLICT, "no invoice email waits on a send approval for that invoice and stage")
    if row[2]:
        raise FinanceError(CONFLICT, "that invoice email is already delivered")
    if delivered:
        SAP.update(c, table, str(row[0]), {"delivered_at": at, "delivery_reason": None})
        return
    SAP.update(c, table, str(row[0]), {"delivery_reason": reason[:300]})
    number = str(c.execute("SELECT number FROM fin_invoices WHERE id=?", (invoice_id,)).fetchone()[0])
    tico.needs_you(c, person, f"Invoice email not sent: {number}{_which(stage)}",
                   f"{reason[:300]}\n\nThe Bookkeeper retries under the same approval; the text does not change.")


def queue_items(c: Any) -> list[dict[str, str]]:
    """The Bookkeeper's queue items for invoice emails waiting to send. The text itself is read through Finance's
    invoice search (its `emails` field), so the label names the search rather than carrying the text."""
    items = []
    for row in pending(c):
        label = (f"Invoice {row['number']}{_which(row['stage'])} email waits to send. Read it: "
                 f"hub finance search --kind invoice --q {row['number']}")
        items.append({"kind": "invoice_email", "id": f"{row['invoice_id']}:{row['stage'] or 'invoice'}",
                      "label": label[:160], "stamp": str(row["reason"] or "pending")})
    return items


def watch(ctx: Ctx) -> int:
    """The hourly look: an approved invoice email with no delivery report a day after it was made raises one Needs-you
    for the finance admin. How many it raised."""
    from .payments import admin_person
    cut = tico.shift(ctx.at, days=-1)
    n = 0
    for row in pending(ctx.c):
        if row["reason"] or row["since"] > cut:
            continue
        title = f"Invoice email has no delivery report: {row['number']}{_which(row['stage'])}"
        body = ("The Bookkeeper has not reported whether this email was sent. Check the mailbox before anything is "
                "resent; a retry uses the same approval and the same text.")
        if tico.needs_you(ctx.c, admin_person(ctx), title, body):
            n += 1
    return n


__all__ = ["BOOKKEEPER", "SEND", "digest", "document_digest", "issue", "mailbox", "pending", "queue_items", "recipients",
           "report", "subject_of", "watch"]
