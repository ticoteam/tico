"""Payables and receivables reads (spec §4.1, §7.1, §12): bills, invoices, contacts and one document with its lines,
settlements, attachments and approvals. Bills and vendors show to whoever sees payables (the view, §7.5: rights are
for doing, never seeing), invoices and customers to whoever sees receivables; a document with a line on a private
account the viewer has no full detail for is left out whole, from lists, totals and contacts' owed amounts alike.
Nested lists are capped, with a count and `more`. Re-exported by finance/reads, the only door."""
import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any, cast

from ..records import reads as RR
from .context import Ctx, load
from .core import ap as AP
from .core import dates
from .core import ledger as L
from .core import rights as R
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND, denied, invalid
from .store import ap as SAP
from .store import banking as SK
from .store import catalog as SC
from .store import ledger as SL
from .tab import BILLS_EMAIL_KEY, decode_cursor, encode_cursor, page_size, private_cut

Row = dict[str, object]
BATCH = 200
SCAN = 2000                 # rows a page may look through before it hands back a cursor
OPEN_MOST = 5000            # open documents an aging summary sums (more is reported as `more`)
NESTED_MOST = 50            # settlements and attachments one document response carries
SIDES = {"bill": ("payables", R.AP), "invoice": ("receivables", R.AR)}
CONTACT_OF = {"bill": "vendor", "invoice": "customer"}
CHART_MOST = 2000
CONTROL_SUBTYPES = ("payable", "receivable")
CHART_FOR = {"bill": ("expense", "asset"), "invoice": ("income",)}


def sees_side(ctx: Ctx, view: VW.View, kind: str) -> bool:
    """Whether the viewer sees bills (kind bill) or invoices (kind invoice): a payables or receivables view. The
    finance.ap / finance.ar right alone shows nothing."""
    flag = SIDES[kind][0]
    return view.everything or (view.payables if flag == "payables" else view.receivables)


def sees_contact_kind(ctx: Ctx, view: VW.View, kind: str) -> bool:
    if kind == "vendor":
        return sees_side(ctx, view, "bill")
    if kind == "customer":
        return sees_side(ctx, view, "invoice")
    return sees_side(ctx, view, "bill") or sees_side(ctx, view, "invoice")


def vendor_labels(ctx: Ctx, view: VW.View, keys: Iterable[str]) -> dict[str, str]:
    """What each vendor key on a line shows as. A bill's or invoice's lines (and their payments, write-offs and
    settlements) carry the contact's id: its name, to a viewer who sees that kind of contact, else "". A bank or card
    row with no vendor contact is keyed by its folded name: the name of the contact that later took it
    (fin_vendor_aliases), else the text it was last posted under (fin_vendor_labels), else the key itself. A key that is an id with no contact behind it shows "": never an id."""
    wanted = sorted({k for k in keys if k})
    named = {k: SAP.vendor_resolved(ctx.c, ctx.book.id, k) for k in wanted if not _is_id(k)}
    found = SAP.contacts(ctx.c, sorted(set(wanted) | set(named.values())))
    texts = SL.vendor_label_texts(ctx.c, ctx.book.id, sorted({v for v in named.values() if v not in found}))
    out: dict[str, str] = {}
    for k in wanted:
        row = found.get(named.get(k, k))
        if row is not None:
            out[k] = str(row["name"]) if sees_contact_kind(ctx, view, str(row["kind"])) else ""
        else:
            out[k] = "" if _is_id(k) else texts.get(named[k], named[k])
    return out


def _is_id(key: str) -> bool:
    try:
        return len(key) == 36 and str(uuid.UUID(key)) == key.lower()
    except ValueError:
        return False


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


def payment_of(pay: Any) -> Row | None:
    """A bill's latest payment as its row shows it: the state (queued, awaiting_bank_approval, submitted, settled, or
    how it ended), the day it was asked for and, once it ended without settling, why."""
    if pay is None:
        return None
    return {"id": str(pay["id"]), "status": str(pay["status"]), "amount": int(pay["amount"]),
            "on": str(pay["created_at"])[:10], "error": str(pay["last_error"] or "") or None}


def ask_ended(r: Any) -> Row | None:
    """How a bill's scheduled payment ended before any payment existed (its approval denied, expired or withdrawn,
    or the ask refused), in payment_of's shape with no id."""
    end = SK.loads(r["schedule_json"]).get("ended")
    if not isinstance(end, dict):
        return None
    e = cast(dict[str, Any], end)
    return {"id": None, "status": str(e.get("status") or ""), "amount": int(e.get("amount") or 0),
            "on": str(e.get("on") or ""), "error": str(e.get("reason") or "") or None}


def doc_row(kind: str, r: Any, today: date, attached: int = 0, pay: Any = None) -> Row:
    """One bill or invoice as a list row; a bill carries its latest payment (`pay`, from SK.last_payments), or how
    its schedule's ask ended when that came after (a new schedule clears it)."""
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
                    "pay_on": r["pay_on"], "payment": ask_ended(r) or payment_of(pay)})
    else:
        out.update({"sent_at": r["sent_at"], "written_off": int(r["written_off"]), "payment_link": r["payment_link"]})
    return out


def doc_lines(ctx: Ctx, lines: Sequence[Any]) -> Row:
    """A list row's line summary: the line its lines go to (`split` when several) and the team they share."""
    accounts = list(dict.fromkeys(str(x["account_id"] or "") for x in lines))
    teams = {str(SK.loads(x["tags_json"]).get("team") or "") for x in lines}
    return {"account": account_ref(ctx, accounts[0]) if len(accounts) == 1 else None, "split": len(accounts) > 1,
            "team": next(iter(teams)) or None if len(teams) == 1 else None}


def paid_of_doc(c: sqlite3.Connection, kind: str, doc_id: str) -> Row:
    """When a paid document was last paid and who recorded it."""
    last = (SAP.settlements_to(c, kind, doc_id) or [None])[-1]
    return {"paid_on": str(last["settled_on"]) if last else None, "paid_by": str(last["created_by"]) if last else None}


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
              aging: str | None = None, late: bool = False, cursor: str | None = None, limit: int = 50,
              order: str = "date", location_id: str | None = None, project_id: str | None = None,
              at: str | None = None) -> Row:
    """Bills or invoices newest first (by date, or with `order` paid_on by when they were last paid), filtered by status (or `unpaid`), contact, dates, aging bucket (or `late`: past
    due) and a search over number, memo and contact name, with the open total by aging bucket. Each row has the line
    its lines go to and their team; a paid one when it was paid and by whom. `next_cursor` pages."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not sees_side(ctx, view, kind):
        raise denied(f"{'payables' if kind == 'bill' else 'receivables'} are not in your view")
    if aging is not None and aging not in AP.AGING:
        raise invalid(f"aging is {'|'.join(AP.AGING)}")
    statuses = _statuses(kind, status)
    if order not in ("date", "paid_on"):
        raise invalid("order is date|paid_on")
    cut = set(private_cut(ctx, view))
    most = page_size(limit)
    marks = decode_cursor(cursor)
    before = (marks[0], marks[1]) if len(marks) == 2 else None
    today = dates.day(ctx.today)
    out: list[Row] = []
    scanned = 0
    nxt: str | None = None
    tagged = tag_filter(c, location_id, project_id)
    while len(out) < most and scanned < SCAN:
        batch = SAP.docs_page(c, kind, book_id=ctx.book.id, statuses=statuses, contact_id=contact_id or "",
                              q=(q or "").strip()[:100], start=dates.day(start).isoformat() if start else "",
                              end=dates.day(end).isoformat() if end else "", before=before, most=BATCH,
                              by_paid=order == "paid_on", tagged=tagged)
        if not batch:
            break
        scanned += len(batch)
        kept, found = keep_docs(c, kind, batch, cut)
        keep = {str(r["id"]) for r in kept}
        files = SAP.attachment_counts(c, kind, sorted(keep))
        pays = SK.last_payments(c, [f"bill:{i}" for i in sorted(keep)]) if kind == "bill" else {}
        for r in batch:
            before = (str(r["sort_key"]), str(r["id"]))
            if str(r["id"]) not in keep:
                continue
            row = doc_row(kind, r, today, files.get(str(r["id"]), 0), pays.get(f"bill:{r['id']}"))
            if aging is not None and row["aging"] != aging or late and row["aging"] in (None, "current"):
                continue
            row.update(doc_lines(ctx, found[str(r["id"])]))
            if row["status"] == "paid":
                row.update(paid_of_doc(c, kind, str(r["id"])))
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


def tag_filter(c: sqlite3.Connection, location_id: str | None, project_id: str | None
               ) -> list[tuple[str, tuple[str, ...]]]:
    """What a list narrowed to a location or project matches on a line's tags: the record's id and the ids merged into
    it, since a line keeps the id it was tagged with."""
    asked = (("location", "locations", location_id), ("project", "projects", project_id))
    return [(tag, RR.ids_of(c, table, value)) for tag, table, value in asked if value]


def list_names(c: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """The names a list row shows: its locations and projects. A panel's full set is `record_names`."""
    return {"location": RR.names(c, "locations"), "project": RR.names(c, "projects")}


def record_names(c: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Each record's name by id, archived ones too: a line keeps naming what it was tagged with."""
    return {"location": RR.names(c, "locations"), "project": RR.names(c, "projects"), "class": SC.class_names(c),
            "item": RR.names(c, "items")}


def tag_names(names: dict[str, dict[str, str]], tags: dict[str, str]) -> dict[str, str]:
    """A line's location, project and class by name, for a panel that shows them."""
    return {k: names[k].get(tags[k], tags[k]) for k in ("location", "project", "class") if tags.get(k)}


def doc_approvals(c: sqlite3.Connection, kind: str, doc_id: str) -> list[Row]:
    return [{"action_id": str(r["id"]), "kind": str(r["kind"]), "status": str(r["status"]), "actor": str(r["actor"]),
             "approval_id": r["approval_id"], "approval_to": r["approval_to"], "rule_id": str(r["rule_id"]) or None,
             "created_at": str(r["created_at"])} for r in SAP.doc_actions(c, kind, doc_id, 20)]


def document(c: sqlite3.Connection, viewer: str, kind: str, doc_id: str, at: str | None = None) -> Row:
    """One bill or invoice with its lines, settlements, attachments and the actions (and approvals) on it. An invoice
    line says whether it is taxable."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    r = SAP.doc_with_contact(c, kind, doc_id)
    if r is None or str(r["book_id"]) != ctx.book.id or not sees_side(ctx, view, kind):
        raise FinanceError(NOT_FOUND, f"no such {kind} in your view")
    kept, found = keep_docs(c, kind, [r], set(private_cut(ctx, view)))
    if not kept:
        raise FinanceError(NOT_FOUND, f"no such {kind} in your view")
    attached = SAP.attachment_counts(c, kind, [doc_id]).get(doc_id, 0)
    settled = SAP.settlement_count(c, kind, doc_id)
    pay = SK.last_payments(c, [f"bill:{doc_id}"]).get(f"bill:{doc_id}") if kind == "bill" else None
    out = doc_row(kind, r, dates.day(ctx.today), attached, pay)
    names = record_names(c)
    out["lines"] = [{"id": str(x["id"]), "account": account_ref(ctx, x["account_id"]), "amount": int(x["amount"]),
                     "description": str(x["description"]), "tags": SK.loads(x["tags_json"]),
                     "tag_names": tag_names(names, cast(dict[str, str], SK.loads(x["tags_json"]))),
                     **({"taxable": bool(x["taxable"])} if kind == "invoice" else {})}
                    for x in found[doc_id]]
    out["settlements"] = [{"id": str(s["id"]), "from_kind": str(s["from_kind"]), "from_id": str(s["from_id"]) or None,
                           "amount": int(s["amount"]), "settled_on": str(s["settled_on"]), "entry_id": s["entry_id"],
                           "voided_at": s["voided_at"], "created_by": str(s["created_by"])}
                          for s in SAP.settlements_to(c, kind, doc_id, live=False, most=NESTED_MOST)]
    out["settlement_count"], out["settlements_more"] = settled, settled > NESTED_MOST
    out["attachments"] = [{"id": str(f["id"]), "file_id": str(f["file_id"]), "created_by": str(f["created_by"]),
                           "created_at": str(f["created_at"])}
                          for f in SAP.attachments_first(c, kind, doc_id, NESTED_MOST)]
    out["attachment_count"], out["attachments_more"] = attached, attached > NESTED_MOST
    out["actions"] = doc_approvals(c, kind, doc_id)
    out["terms_days"] = r["terms_days"]
    if kind == "bill":
        out["schedule"] = SK.loads(r["schedule_json"]) or None
    out["view_version"] = view.version
    return out


def bills(c: sqlite3.Connection, viewer: str, *, status: str | None = None, vendor_id: str | None = None,
          q: str = "", start: str | None = None, end: str | None = None, aging: str | None = None,
          late: bool = False, cursor: str | None = None, limit: int = 50, order: str = "date",
          at: str | None = None) -> Row:
    """Bills the viewer sees (`documents` with kind bill), with the install's bills address (`bills_email`)."""
    return {**documents(c, viewer, "bill", status=status, contact_id=vendor_id, q=q, start=start, end=end,
                        aging=aging, late=late, cursor=cursor, limit=limit, order=order, at=at),
            "bills_email": bills_email(c)}


def invoices(c: sqlite3.Connection, viewer: str, *, status: str | None = None, customer_id: str | None = None,
             q: str = "", start: str | None = None, end: str | None = None, aging: str | None = None,
             late: bool = False, cursor: str | None = None, limit: int = 50, location_id: str | None = None,
             project_id: str | None = None, at: str | None = None) -> Row:
    """Invoices the viewer sees (`documents` with kind invoice)."""
    return documents(c, viewer, "invoice", status=status, contact_id=customer_id, q=q, start=start, end=end,
                     aging=aging, late=late, cursor=cursor, limit=limit, location_id=location_id,
                     project_id=project_id, at=at)


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
            "payment_terms_days": r["payment_terms_days"], "owner_id": r["owner_id"], "renewal_on": r["renewal_on"],
            "renewal_notice_days": r["renewal_notice_days"], "created_at": str(r["created_at"])}


def _owed(c: sqlite3.Connection, kind: str, contact_ids: Sequence[str], cut: set[str]) -> dict[str, int]:
    """What each contact's open documents owe, from only the documents this viewer sees whole."""
    contact = f"{CONTACT_OF[kind]}_id"
    kept, _ = keep_docs(c, kind, SAP.open_for(c, kind, contact_ids, OPEN_MOST), cut)
    out: dict[str, int] = {}
    for r in kept:
        out[str(r[contact])] = out.get(str(r[contact]), 0) + int(r["total"]) - paid_of(kind, r)
    return out


def _contact_rows(c: sqlite3.Connection, ctx: Ctx, view: VW.View, admin: bool, page: Sequence[Any]) -> list[Row]:
    """Contacts with what their open documents owe, on the sides this viewer sees."""
    vendors, customers = sees_side(ctx, view, "bill"), sees_side(ctx, view, "invoice")
    ids = [str(r["id"]) for r in page]
    cut = set(private_cut(ctx, view))
    bills_owed = _owed(c, "bill", ids, cut) if vendors else {}
    invoices_owed = _owed(c, "invoice", ids, cut) if customers else {}
    return [contact_row(ctx, r, admin, {**({"bills": bills_owed.get(str(r["id"]), 0)} if vendors else {}),
                                        **({"invoices": invoices_owed.get(str(r["id"]), 0)} if customers else {})})
            for r in page]


def contact(c: sqlite3.Connection, viewer: str, contact_id: str, at: str | None = None) -> Row:
    """One vendor or customer, active or not, to whoever sees its side; to anyone else it does not exist."""
    ctx = load(c, at)
    view = ctx.view(viewer)
    r = SAP.get(c, "fin_contacts", contact_id)
    if r is None or str(r["book_id"]) != ctx.book.id or not sees_contact_kind(ctx, view, str(r["kind"])):
        raise FinanceError(NOT_FOUND, "no such contact in your view")
    admin = R.is_admin(ctx.org, viewer, ctx.rights(viewer))
    return {"contact": _contact_rows(c, ctx, view, admin, [r])[0], "view_version": view.version}


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
    admin = R.is_admin(ctx.org, viewer, ctx.rights(viewer))
    most = page_size(limit)
    marks = decode_cursor(cursor)
    after = (marks[0], marks[1]) if len(marks) == 2 else None
    rows = SAP.contacts_page(c, book_id=ctx.book.id, kinds=kinds, q=(q or "").strip()[:100], active=active,
                             after=after, most=most + 1)
    page = rows[:most]
    out = _contact_rows(c, ctx, view, admin, page)
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
    `company_wide` says whether a line takes untagged amounts from this viewer; when it does not, `attributable` lists
    the team and teammate tags they may use on it (the same rule a write checks)."""
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
        wide = may_write(ctx, viewer, a.id, {})
        row: Row = {"id": a.id, "number": a.code, "name": a.name, "type": a.type, "subtype": a.subtype,
                    "parent_id": a.parent_id or None, "company_wide": wide}
        if not wide:
            row["attributable"] = _attributable(ctx, viewer, a)
        out.append(row)
    return {"accounts": out[:CHART_MOST], "more": len(out) > CHART_MOST,
            "classes": [{"id": i, "name": n} for i, n in SC.classes_active(c)], "view_version": view.version}


ATTRIBUTABLE_MOST = 200


def _attributable(ctx: Ctx, viewer: str, account: L.Account) -> list[Row]:
    """The tags the viewer may put an amount on this line under (handlers.may_write decides each): the viewer's own
    teammate tag first (when allowed), then teams, then other teammates, as {"team": id} or {"teammate": id}."""
    from .handlers import may_write
    view = ctx.view(viewer)
    mine = may_write(ctx, viewer, account.id, {"teammate": viewer})
    out: list[Row] = [{"teammate": viewer}] if mine else []     # their own tag first, never cut by the cap
    for t in sorted(view.teams(account)):
        if may_write(ctx, viewer, account.id, {"team": t}):
            out.append({"team": t})
    for m in sorted(ctx.org.humans | ctx.org.bots):
        if len(out) >= ATTRIBUTABLE_MOST:
            break
        if m != viewer and may_write(ctx, viewer, account.id, {"teammate": m}):
            out.append({"teammate": m})
    return out[:ATTRIBUTABLE_MOST]


def feed_expected(c: sqlite3.Connection, viewer: str, feed_id: str, at: str | None = None) -> Row:
    """The payments marked paid a settled bank row not yet in the books may be the bank side of (same money account
    and direction, at most its amount, marked within 5 days), for feed_match: settlement id, document, contact,
    amount and day. Capped at 20 (`more`)."""
    from .banking import seen
    from .documents import expected_for
    ctx = load(c, at)
    view = ctx.view(viewer)
    row = SK.get(c, "fin_feed_txns", feed_id)
    if row is None or not seen(ctx, viewer, row):
        raise FinanceError(NOT_FOUND, "no such feed row in your view")
    kind = "bill" if int(row["amount"]) < 0 else "invoice"
    found, more = expected_for(ctx, viewer, row)
    docs = {str(d["id"]): d for d in (SAP.doc_with_contact(c, kind, str(s["to_id"])) for s in found) if d is not None}
    return {"feed_id": feed_id, "kind": kind, "amount": abs(int(row["amount"])), "date": str(row["txn_date"]),
            "expected": [{"settlement_id": str(s["id"]), "kind": kind, "document_id": str(s["to_id"]),
                          "number": str(docs[str(s["to_id"])]["number"] or "") if str(s["to_id"]) in docs else "",
                          "contact_id": str(s["contact_id"] or ""), "contact_name": str(s["contact_name"] or ""),
                          "amount": int(s["amount"]), "marked_on": str(s["settled_on"])} for s in found],
            "more": more, "view_version": view.version}


def pay_from(c: sqlite3.Connection, viewer: str, at: str | None = None) -> Row:
    """The connected bank accounts the viewer may pay a bill from (finance.pay) and sees (its view's sources): id,
    name, provider, last 4 and balance. An account outside the view is left out whole."""
    from .tab import sees_money_account
    ctx = load(c, at)
    view = ctx.view(viewer)
    if not R.has(ctx.org, viewer, R.PAY, ctx.rights(viewer)):
        raise denied("paying is for teammates with finance.pay")
    out: list[Row] = []
    for m in SK.money_accounts(c, ctx.book.id):
        if str(m["kind"]) != "bank" or not m["connection_id"] or str(m["status"] or "active") != "active" \
                or not sees_money_account(ctx, view, m):
            continue
        out.append({"id": str(m["id"]), "name": str(m["name"]), "provider": str(m["provider"] or ""),
                    "last4": m["last4"], "balance": m["balance"], "available_balance": m["available_balance"],
                    "balance_as_of": m["balance_as_of"]})
    return {"accounts": out, "currency": ctx.book.base_currency, "view_version": view.version}
