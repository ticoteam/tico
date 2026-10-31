"""Payables and receivables reads (spec §4.1, §7.1, §12): bills, invoices, contacts and one document with its lines,
settlements, attachments and approvals. Bills and vendors show to whoever sees payables (a view grant, or the
finance.ap right whose work they are), invoices and customers to whoever sees receivables; a document with a line on
a private account the viewer has no full detail for is left out whole. Re-exported by finance/reads, the only door."""
import sqlite3
from collections.abc import Sequence
from datetime import date
from typing import Any

from .context import Ctx, load
from .core import ap as AP
from .core import dates
from .core import rights as R
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND, denied, invalid
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL
from .tab import decode_cursor, encode_cursor, page_size, private_cut

Row = dict[str, object]
BATCH = 200
SCAN = 2000                 # rows a page may look through before it hands back a cursor
OPEN_MOST = 5000            # open documents an aging summary sums (more is reported as `more`)
SIDES = {"bill": ("payables", R.AP), "invoice": ("receivables", R.AR)}
CONTACT_OF = {"bill": "vendor", "invoice": "customer"}
BILLS_EMAIL_KEY = "finance_bills_email"     # the install's bills address (actions.set_bills_email writes it)
CHART_MOST = 2000
CONTROL_SUBTYPES = ("payable", "receivable")
CHART_FOR = {"bill": ("expense", "asset"), "invoice": ("income",)}


def sees_side(ctx: Ctx, view: VW.View, kind: str) -> bool:
    """Whether the viewer sees bills (kind bill) or invoices (kind invoice): a payables or receivables view, or the
    finance.ap / finance.ar right, since entering and paying them is that right's work."""
    flag, right = SIDES[kind]
    if view.everything or (view.payables if flag == "payables" else view.receivables):
        return True
    return R.has(ctx.org, view.viewer, right, ctx.rights(view.viewer))


def sees_contact_kind(ctx: Ctx, view: VW.View, kind: str) -> bool:
    if kind == "vendor":
        return sees_side(ctx, view, "bill")
    if kind == "customer":
        return sees_side(ctx, view, "invoice")
    return sees_side(ctx, view, "bill") or sees_side(ctx, view, "invoice")


def account_ref(ctx: Ctx, account_id: object) -> Row | None:
    a = ctx.accounts.get(str(account_id or ""))
    return {"id": a.id, "code": a.code, "name": a.name} if a else None


def paid_of(kind: str, r: Any) -> int:
    """Money settled against a document, and for an invoice what was written off."""
    return int(r["amount_paid"]) + (int(r["written_off"]) if kind == "invoice" else 0)


def remaining(kind: str, r: Any) -> int:
    """What a document still owes: nothing for a draft (not yet owed), a paid or a void one."""
    if str(r["status"]) in ("draft", "void", "paid"):
        return 0
    return int(r["total"]) - paid_of(kind, r)


def doc_row(kind: str, r: Any, today: date, attached: int = 0) -> Row:
    """One bill or invoice as a list row."""
    left = remaining(kind, r)
    due = r["due_date"]
    contact = CONTACT_OF[kind]
    out: Row = {"id": str(r["id"]), "kind": kind, "number": str(r["number"]),
                contact: {"id": r[f"{contact}_id"], "name": r["contact_name"]},
                "date": str(r[f"{kind}_date"]), "due_date": due, "status": str(r["status"]),
                "currency": str(r["currency"]), "total": int(r["total"]), "paid": paid_of(kind, r), "remaining": left,
                "aging": AP.aging(due, today) if left else None, "days_late": AP.days_late(due, today) if left else 0,
                "memo": str(r["memo"]), "entry_id": r["entry_id"], "attachments": attached,
                "created_by": str(r["created_by"]), "created_at": str(r["created_at"])}
    if kind == "bill":
        out.update({"origin": str(r["origin"]), "approved_by": r["approved_by"], "approved_at": r["approved_at"],
                    "pay_on": r["pay_on"]})
    else:
        out.update({"sent_at": r["sent_at"], "written_off": int(r["written_off"]), "payment_link": r["payment_link"]})
    return out


def keep_docs(c: sqlite3.Connection, kind: str, rows: Sequence[Any], cut: set[str]
              ) -> tuple[list[Any], dict[str, list[Any]]]:
    """The documents with no line on an account cut from this viewer, and every document's lines."""
    found = SAP.lines(c, kind, [str(r["id"]) for r in rows])
    if not cut:
        return list(rows), found
    return [r for r in rows if not any(str(x["account_id"] or "") in cut for x in found[str(r["id"])])], found


def _statuses(kind: str, status: str | None) -> list[str]:
    allowed = AP.BILL_STATUSES if kind == "bill" else AP.INVOICE_STATUSES
    if not status:
        return []
    out = [s.strip() for s in status.split(",") if s.strip()]
    if "unpaid" in out:
        out = [s for s in out if s != "unpaid"] + list(AP.OPEN_BILL if kind == "bill" else AP.OPEN_INVOICE)
    bad = [s for s in out if s not in allowed]
    if bad:
        raise invalid(f"status is {'|'.join(allowed)}|unpaid (comma-separated)")
    return out


def aging_summary(c: sqlite3.Connection, ctx: Ctx, kind: str, cut: set[str]) -> Row:
    """What open documents owe, in total and by aging bucket."""
    rows = SAP.open_docs(c, kind, ctx.book.id, OPEN_MOST + 1)
    more = len(rows) > OPEN_MOST
    kept, _ = keep_docs(c, kind, rows[:OPEN_MOST], cut)
    today = dates.day(ctx.today)
    buckets: dict[str, int] = dict.fromkeys(AP.AGING, 0)
    total = 0
    for r in kept:
        left = remaining(kind, r)
        buckets[AP.aging(r["due_date"], today)] += left
        total += left
    return {"count": len(kept), "remaining": total, "aging": buckets, "more": more}


def documents(c: sqlite3.Connection, viewer: str, kind: str, *, status: str | None = None,
              contact_id: str | None = None, q: str = "", start: str | None = None, end: str | None = None,
              aging: str | None = None, cursor: str | None = None, limit: int = 50, at: str | None = None) -> Row:
    """Bills or invoices newest first, filtered by status (or `unpaid`), contact, dates, aging bucket and a search
    over number, memo and contact name, with the open total by aging bucket. `next_cursor` pages."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not sees_side(ctx, view, kind):
        raise denied(f"{'payables' if kind == 'bill' else 'receivables'} are not in your view")
    if aging is not None and aging not in AP.AGING:
        raise invalid(f"aging is {'|'.join(AP.AGING)}")
    statuses = _statuses(kind, status)
    cut = set(private_cut(ctx, view))
    most = page_size(limit)
    marks = decode_cursor(cursor)
    before = (marks[0], marks[1]) if len(marks) == 2 else None
    today = dates.day(ctx.today)
    out: list[Row] = []
    scanned = 0
    nxt: str | None = None
    while len(out) < most and scanned < SCAN:
        batch = SAP.docs_page(c, kind, book_id=ctx.book.id, statuses=statuses, contact_id=contact_id or "",
                              q=(q or "").strip()[:100], start=dates.day(start).isoformat() if start else "",
                              end=dates.day(end).isoformat() if end else "", before=before, most=BATCH)
        if not batch:
            break
        scanned += len(batch)
        kept, _ = keep_docs(c, kind, batch, cut)
        keep = {str(r["id"]) for r in kept}
        files = SAP.attachments(c, kind, sorted(keep))
        for r in batch:
            before = (str(r[f"{kind}_date"]), str(r["id"]))
            if str(r["id"]) not in keep:
                continue
            row = doc_row(kind, r, today, len(files.get(str(r["id"]), [])))
            if aging is not None and row["aging"] != aging:
                continue
            out.append(row)
            if len(out) == most:
                break
        if len(batch) < BATCH and len(out) < most:
            before = None
            break
    if before is not None and (len(out) == most or scanned >= SCAN):
        nxt = encode_cursor(list(before))
    return {f"{kind}s": out, "next_cursor": nxt, "open": aging_summary(c, ctx, kind, cut),
            "currency": ctx.book.base_currency, "view_version": view.version}


def _approvals(c: sqlite3.Connection, kind: str, doc_id: str) -> list[Row]:
    return [{"action_id": str(r["id"]), "kind": str(r["kind"]), "status": str(r["status"]), "actor": str(r["actor"]),
             "approval_id": r["approval_id"], "approval_to": r["approval_to"], "rule_id": str(r["rule_id"]) or None,
             "created_at": str(r["created_at"])} for r in SAP.doc_actions(c, kind, doc_id, 20)]


def document(c: sqlite3.Connection, viewer: str, kind: str, doc_id: str, at: str | None = None) -> Row:
    """One bill or invoice with its lines, settlements, attachments and the actions (and approvals) on it."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    r = SAP.doc_with_contact(c, kind, doc_id)
    if r is None or str(r["book_id"]) != ctx.book.id or not sees_side(ctx, view, kind):
        raise FinanceError(NOT_FOUND, f"no such {kind} in your view")
    kept, found = keep_docs(c, kind, [r], set(private_cut(ctx, view)))
    if not kept:
        raise FinanceError(NOT_FOUND, f"no such {kind} in your view")
    files = SAP.attachments(c, kind, [doc_id])[doc_id]
    out = doc_row(kind, r, dates.day(ctx.today), len(files))
    out["lines"] = [{"id": str(x["id"]), "account": account_ref(ctx, x["account_id"]), "amount": int(x["amount"]),
                     "description": str(x["description"]), "tags": SK.loads(x["tags_json"])}
                    for x in found[doc_id]]
    out["settlements"] = [{"id": str(s["id"]), "from_kind": str(s["from_kind"]), "from_id": str(s["from_id"]) or None,
                           "amount": int(s["amount"]), "settled_on": str(s["settled_on"]), "entry_id": s["entry_id"],
                           "voided_at": s["voided_at"], "created_by": str(s["created_by"])}
                          for s in SAP.settlements_to(c, kind, doc_id, live=False)]
    out["attachments"] = [{"id": str(f["id"]), "file_id": str(f["file_id"]), "created_by": str(f["created_by"]),
                           "created_at": str(f["created_at"])} for f in files]
    out["actions"] = _approvals(c, kind, doc_id)
    out["terms_days"] = r["terms_days"]
    if kind == "bill":
        out["schedule"] = SK.loads(r["schedule_json"]) or None
    out["view_version"] = view.version
    return out


def bills(c: sqlite3.Connection, viewer: str, *, status: str | None = None, vendor_id: str | None = None,
          q: str = "", start: str | None = None, end: str | None = None, aging: str | None = None,
          cursor: str | None = None, limit: int = 50, at: str | None = None) -> Row:
    """Bills the viewer sees (`documents` with kind bill), with the install's bills address (`bills_email`)."""
    return {**documents(c, viewer, "bill", status=status, contact_id=vendor_id, q=q, start=start, end=end,
                        aging=aging, cursor=cursor, limit=limit, at=at), "bills_email": bills_email(c)}


def invoices(c: sqlite3.Connection, viewer: str, *, status: str | None = None, customer_id: str | None = None,
             q: str = "", start: str | None = None, end: str | None = None, aging: str | None = None,
             cursor: str | None = None, limit: int = 50, at: str | None = None) -> Row:
    """Invoices the viewer sees (`documents` with kind invoice)."""
    return documents(c, viewer, "invoice", status=status, contact_id=customer_id, q=q, start=start, end=end,
                     aging=aging, cursor=cursor, limit=limit, at=at)


def bill(c: sqlite3.Connection, viewer: str, bill_id: str, at: str | None = None) -> Row:
    return document(c, viewer, "bill", bill_id, at)


def invoice(c: sqlite3.Connection, viewer: str, invoice_id: str, at: str | None = None) -> Row:
    return document(c, viewer, "invoice", invoice_id, at)


def contact_row(ctx: Ctx, r: Any, admin: bool, owed: Row | None = None) -> Row:
    """A contact. The tax id never shows; its last 4 only to a finance admin."""
    return {"id": str(r["id"]), "name": str(r["name"]), "kind": str(r["kind"]),
            "emails": SK.loads_list(r["emails_json"]),
            "default_account": account_ref(ctx, r["default_account_id"]), "is_1099": bool(r["is_1099"]),
            "has_tax_id": bool(r["tax_id_ref"]), "tax_id_last4": r["tax_id_last4"] if admin else None,
            "recipient_id": r["payment_ref"], "active": bool(r["active"]), "owed": owed or {},
            "created_at": str(r["created_at"])}


def contacts(c: sqlite3.Connection, viewer: str, *, kind: str | None = None, q: str = "", active: bool | None = True,
             cursor: str | None = None, limit: int = 50, at: str | None = None) -> Row:
    """Vendors (to whoever sees payables) and customers (receivables) by name, with what their open bills and
    invoices owe. `kind` is vendor|customer|both; a search matches name and email."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    if kind is not None and kind not in AP.CONTACT_KINDS:
        raise invalid(f"kind is {'|'.join(AP.CONTACT_KINDS)}")
    kinds = [k for k in AP.CONTACT_KINDS if sees_contact_kind(ctx, view, k) and
             (kind is None or k == kind or (kind != "both" and k == "both"))]
    if not kinds:
        raise denied("contacts are not in your view")
    vendors, customers = sees_side(ctx, view, "bill"), sees_side(ctx, view, "invoice")
    admin = R.is_admin(ctx.org, viewer, ctx.rights(viewer))
    most = page_size(limit)
    marks = decode_cursor(cursor)
    after = (marks[0], marks[1]) if len(marks) == 2 else None
    rows = SAP.contacts_page(c, book_id=ctx.book.id, kinds=kinds, q=(q or "").strip()[:100], active=active,
                             after=after, most=most + 1)
    page = rows[:most]
    ids = [str(r["id"]) for r in page]
    bills_owed = SAP.owed(c, "bill", ids) if vendors else {}
    invoices_owed = SAP.owed(c, "invoice", ids) if customers else {}
    out = [contact_row(ctx, r, admin, {**({"bills": bills_owed.get(str(r["id"]), 0)} if vendors else {}),
                                       **({"invoices": invoices_owed.get(str(r["id"]), 0)} if customers else {})})
           for r in page]
    nxt = encode_cursor([str(page[-1]["name"]), str(page[-1]["id"])]) if len(rows) > most else None
    return {"contacts": out, "next_cursor": nxt, "view_version": view.version}


def bills_email(c: sqlite3.Connection) -> str | None:
    """The address vendors send bills to (Bookkeeper reads that mailbox), or None while there is none."""
    value = SL.setting(c, BILLS_EMAIL_KEY)
    return value if isinstance(value, str) and value else None


def chart(c: sqlite3.Connection, viewer: str, *, for_: str | None = None, at: str | None = None) -> Row:
    """The active lines the viewer may post to, for the bill, invoice and journal forms: every line in their view,
    for teammates who write to the books (finance.book, ap or ar). `for_` bill keeps expense and asset lines, invoice
    income lines (never accounts payable or receivable, which only documents move); journal keeps every line.
    `company_wide` says whether a line takes untagged amounts from this viewer (else they tag their team)."""
    from .handlers import may_write
    ctx = load(c, at)
    view = ctx.view(viewer)
    held = ctx.rights(viewer)
    if not any(R.has(ctx.org, viewer, r, held) for r in (R.BOOK, R.AP, R.AR)):
        raise denied("the chart is for teammates who write to the books")
    if for_ is not None and for_ not in (*CHART_FOR, "journal"):
        raise invalid("for is bill|invoice|journal")
    types = CHART_FOR.get(for_ or "journal")
    out: list[Row] = []
    for a in sorted(ctx.accounts.values(), key=lambda x: (x.code, x.id)):
        if not a.active or not (view.everything or view.sees_account(a)):
            continue
        if types is not None and (a.type not in types or a.subtype in CONTROL_SUBTYPES):
            continue
        out.append({"id": a.id, "number": a.code, "name": a.name, "type": a.type, "subtype": a.subtype,
                    "parent_id": a.parent_id or None, "company_wide": may_write(ctx, viewer, a.id, {})})
    return {"accounts": out[:CHART_MOST], "more": len(out) > CHART_MOST, "view_version": view.version}


def pay_from(c: sqlite3.Connection, viewer: str, at: str | None = None) -> Row:
    """The connected bank accounts the viewer may pay a bill from (finance.pay): id, name, last 4, and the balance
    only when the viewer sees that account's line on the balance sheet."""
    from .tab import sees_money_account
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not R.has(ctx.org, viewer, R.PAY, ctx.rights(viewer)):
        raise denied("paying is for teammates with finance.pay")
    out: list[Row] = []
    for m in SK.money_accounts(c, ctx.book.id):
        if str(m["kind"]) != "bank" or not m["connection_id"] or str(m["status"] or "active") != "active":
            continue
        seen = sees_money_account(ctx, view, m)
        out.append({"id": str(m["id"]), "name": str(m["name"]), "provider": str(m["provider"] or ""),
                    "last4": m["last4"], "balance": m["balance"] if seen else None,
                    "available_balance": m["available_balance"] if seen else None,
                    "balance_as_of": m["balance_as_of"] if seen else None})
    return {"accounts": out, "currency": ctx.book.base_currency, "view_version": view.version}
