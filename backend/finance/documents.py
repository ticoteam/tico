"""Contacts, bills, invoices, settlements and attachments (spec §4.2, §5), inside the caller's transaction and with no
network. Finance records on accrual underneath and derives the cash view from settlements:

- A bill is a draft until entered; its expense lines hold budget from the draft on (source `bill`, one hold per line,
  the bill's id its commitment id). Entering it posts Dr expense lines / Cr accounts payable and turns the holds into
  actual in the same post, so the spend counts once.
- An invoice posts Dr accounts receivable / Cr income lines when it is sent.
- Money reaches a document through `fin_settlements` (from a payment, a feed row or a money line; many-to-many, so a
  document may be paid in parts and one row may pay several). A feed row that pays a document clears AP or AR; it never
  posts expense or income again. Each settlement moves the cash view by the document lines' share of what was paid
  (core/ap.recognized), at the day it was paid; a document's own entry is left out of the cash view (posting.post).
- Marked paid from a connected money account, the payment posts to its bank line at once and waits (settlement
  `expected`) for its bank row, which links to that entry when it arrives (`expected_match`), so cash posts once; a
  bank row already in is linked instead. Marked paid with no money line, it posts to a clearing account the feed never
  touches.
- A write-off posts Dr the write-off line / Cr AR with basis `accrual`: the cash view never had that income.
- Stage events: finance.bill.received once per bill, finance.bill.due_soon and finance.invoice.overdue once per
  document per stage (the hourly `watch`)."""
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, cast

from .. import automation_due
from .. import due_date_flow as FLOW
from .. import hubdb as H
from ..records import reads as RR
from . import budgeting, posting, shared
from . import context as FC
from .context import Ctx
from .core import ap as AP
from .core import budgets as B
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import rights as R
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .core.tax import SALES_TAX, taxable_default
from .core.org import SYSTEM
from .handlers import Plan, Result, may_write
from .store import ap as SAP
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import purchasing as SP
from .store import tico

RECEIVED, DUE_SOON, OVERDUE = "finance.bill.received", "finance.bill.due_soon", "finance.invoice.overdue"
RENEWAL_DUE = "finance.vendor.renewal_due"
CONTROL = {"bill": ("2000", "payable", "accounts payable"), "invoice": ("1200", "receivable", "accounts receivable")}
CONTACT_OF = {"bill": "vendor", "invoice": "customer"}
SIGN = {"bill": 1, "invoice": -1}       # a bill's lines are debits, an invoice's credits
OPEN = {"bill": AP.OPEN_BILL, "invoice": AP.OPEN_INVOICE}
LINE_TYPES = {"bill": ("expense", "asset"), "invoice": ("income",)}


def _line_type_ok(kind: str, account: L.Account) -> bool:
    """A line posts to the kind's own types, or to a sales tax account: a bill remits it, an invoice collects it."""
    return account.type in LINE_TYPES[kind] or account.subtype == SALES_TAX
NAME_MIN = 4                            # a counterparty matches a contact's name on at least this many characters
CLEARING = ("1050", "Payments clearing", "asset", "clearing")


def _event(ctx: Ctx, name: str, subject: str, payload: Mapping[str, object], key: str = "") -> None:
    from .banking import event
    event(ctx, name, subject, payload, key)


def control(ctx: Ctx, kind: str) -> L.Account:
    """Accounts payable (bills) or receivable (invoices): the default chart's line, else any active one of its kind."""
    code, subtype, words = CONTROL[kind]
    found = [a for a in ctx.accounts.values() if a.active and a.subtype == subtype]
    best = next((a for a in found if a.code == code), found[0] if found else None)
    if best is None:
        raise invalid(f"the books have no {words} line")
    return best


def _doc(ctx: Ctx, kind: str, doc_id: str) -> Any:
    row = SAP.doc_with_contact(ctx.c, kind, doc_id)
    if row is None or str(row["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, f"no such {kind}")
    return row


def contact_for(ctx: Ctx, contact_id: str, kind: str) -> Any:
    row = SAP.get(ctx.c, "fin_contacts", contact_id)
    want = CONTACT_OF[kind]
    if row is None or str(row["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such contact")
    if str(row["kind"]) not in (want, "both"):
        raise invalid(f"that contact is not a {want}")
    if not int(row["active"]):
        raise FinanceError(CONFLICT, "that contact is inactive")
    return row


def _left(kind: str, doc: Any) -> int:
    paid = int(doc["amount_paid"]) + (int(doc["written_off"]) if kind == "invoice" else 0)
    return int(doc["total"]) - paid


def scope_tags(tags: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in tags.items() if k in ("team", "teammate")}


def _money(amount: int) -> str:
    return f"{amount / 100:.2f}"


# ------------------------------------------------------------------------------------------------ contacts
CONTACT_FIELDS = ("name", "kind", "emails", "default_account_id", "is_1099", "recipient_id", "active", "seats",
                  "renewal_on")
TAX_FIELDS = ("tax_id_ref", "tax_id_fp", "tax_id_last4")
# Vendor terms and ownership, set on contact_update: a bill's default due date (V1), who owns the vendor, and the
# notice a cancel-by deadline needs (P3).
VENDOR_FIELDS = ("payment_terms_days", "owner_id", "renewal_notice_days")


def _need_contact_rights(ctx: Ctx, who: str, kind: str) -> None:
    """A vendor is payables' (finance.ap), a customer receivables' (finance.ar); one that is both needs both."""
    need = {"vendor": (R.AP,), "customer": (R.AR,), "both": (R.AP, R.AR)}[kind]
    held = ctx.rights(who)
    missing = [r for r in need if not R.has(ctx.org, who, r, held)]
    if missing:
        raise denied(f"a {kind} contact needs {' and '.join(missing)}")


def _contact_fields(ctx: Ctx, p: V.Payload, old: Any = None) -> dict[str, object]:
    out: dict[str, object] = {}
    if "name" in p or old is None:
        name = V.text(p, "name", 120)
        dup = SAP.contact_named(ctx.c, ctx.book.id, name)
        if dup is not None and (old is None or str(dup["id"]) != str(old["id"])):
            raise FinanceError(CONFLICT, "a contact with that name exists")
        out["name"] = name
    if "kind" in p or old is None:
        out["kind"] = V.choice(p, "kind", AP.CONTACT_KINDS)
    if "emails" in p:
        emails = [] if p.get("emails") == [] else V.texts(p, "emails", 20)
        if any("@" not in e or len(e) > 200 for e in emails):
            raise invalid("emails are addresses")
        out["emails_json"] = SK.dump([e.lower() for e in emails])
    if "default_account_id" in p:
        account_id = V.opt_text(p, "default_account_id", 80) or ""
        account = ctx.accounts.get(account_id)
        if account_id and (account is None or not account.active or account.type not in L.PNL_TYPES):
            raise FinanceError(NOT_FOUND, "no such income or expense account")
        out["default_account_id"] = account_id or None
    if "is_1099" in p:
        out["is_1099"] = 1 if V.flag(p, "is_1099") else 0
    if "recipient_id" in p:
        rid = V.opt_text(p, "recipient_id", 80) or ""
        if rid and SK.get(ctx.c, "fin_recipients", rid) is None:
            raise FinanceError(NOT_FOUND, "no such recipient")
        out["payment_ref"] = rid or None
    if "active" in p:
        out["active"] = 1 if V.flag(p, "active") else 0
    if "tax_id_last4" in p:
        last4 = V.opt_text(p, "tax_id_last4", 4) or ""
        out["tax_id_last4"] = last4 or None
        out["tax_id_ref"] = (V.opt_text(p, "tax_id_ref", 80) or None) if last4 else None
    if any(k in p for k in ("seats", "renewal_on", "renewal_notice_days", "payment_terms_days")):
        kind = str(out.get("kind") or (old["kind"] if old is not None else ""))
        if kind == "customer":
            raise invalid("seats, renewal dates and payment terms belong to vendors")
    if "seats" in p:
        out["seats"] = None if p.get("seats") is None else V.integer(p, "seats", 0, 1_000_000)
    if "renewal_on" in p:
        out["renewal_on"] = V.opt_day(p, "renewal_on")
    if "renewal_notice_days" in p:
        out["renewal_notice_days"] = _days_or_none(p, "renewal_notice_days")
    if "payment_terms_days" in p:
        out["payment_terms_days"] = _days_or_none(p, "payment_terms_days")
    if "owner_id" in p:
        owner = V.opt_text(p, "owner_id", 80) or ""
        if owner and not tico.known_actor(ctx.c, owner):
            raise FinanceError(NOT_FOUND, "no such teammate or bot")
        out["owner_id"] = owner or None
    return out


def _days_or_none(p: V.Payload, key: str) -> int | None:
    """Whole days 0 to MAX_TERMS, or None when the payload clears the field."""
    return None if p.get(key) is None else V.integer(p, key, 0, AP.MAX_TERMS)


def _vendor_alias(ctx: Ctx, name: str, kind: str, contact_id: str) -> str:
    """The name key a vendor contact's name adds to fin_vendor_aliases (spec §3: one key per vendor), or "" when it
    adds none (not a vendor, or a name it already went by). Refused when another contact went by that name, or when
    a budget on the name would then count the same spend as a sibling budget."""
    if kind not in ("vendor", "both"):
        return ""
    nk = L.name_key(name)
    if SAP.is_contact(ctx.c, nk):                       # a name that is a contact's id would take that contact's keys
        raise invalid("a vendor's name can't be a contact's id")
    owner = SAP.alias_of(ctx.c, ctx.book.id, nk)
    if owner == contact_id and owner:
        return ""
    if owner:
        raise FinanceError(CONFLICT, f"another vendor went by {name}")
    budgets = ctx.budgets()
    moved = {b.id: replace(b, vendor=contact_id or "contact:new") for b in budgets.values() if b.vendor == nk}
    both = B.clash({**budgets, **moved}, ctx.accounts, ctx.org) if moved else None
    if both is not None and (both[0].id in moved or both[1].id in moved):
        one, other = sorted((both[0].name, both[1].name))
        raise FinanceError(CONFLICT, f"budgets {one} and {other} would both count {name}'s spend; end or change one "
                                     "first")
    return nk


def contact_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A vendor or customer. Its tax id was sealed into the vault before this runs (payments.strip_details): the
    payload carries the vault ref and the last 4 only."""
    V.only(p, (*CONTACT_FIELDS, *TAX_FIELDS, *VENDOR_FIELDS))
    f = _contact_fields(ctx, p)
    _need_contact_rights(ctx, who, str(f["kind"]))
    if "tax_id_last4" in p and not f.get("tax_id_ref") and f.get("tax_id_last4"):
        raise invalid("the tax id was not sealed")

    alias = _vendor_alias(ctx, str(f["name"]), str(f["kind"]), "")

    def apply(aid: str) -> Result:
        cid = SAP.insert(ctx.c, "fin_contacts", {"book_id": ctx.book.id, "created_at": ctx.at, "created_by": who,
                                                 "updated_at": ctx.at, **f})
        if alias:
            SAP.alias_add(ctx.c, ctx.book.id, alias, cid, ctx.at)
            ctx.changed()                       # budgets on the name go by the contact now
        return {"contact_id": cid}
    return Plan(K.default_gate(ctx.org, who, "contact_create"), f"Add {f['kind']} {f['name']}", apply,
                facts={"contact.kind": str(f["kind"]), "counterparty": str(f["name"])})


def contact_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("contact_id", *CONTACT_FIELDS, *TAX_FIELDS, *VENDOR_FIELDS))
    old = SAP.get(ctx.c, "fin_contacts", V.text(p, "contact_id", 80))
    if old is None or str(old["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such contact")
    _need_contact_rights(ctx, who, str(old["kind"]))
    f = _contact_fields(ctx, p, old)
    if "kind" in f:
        _need_contact_rights(ctx, who, str(f["kind"]))
    if not f:
        raise invalid("say what changes")
    # Renamed or made a vendor: the name it now goes by is added; the names it went by stay (history still maps).
    alias = _vendor_alias(ctx, str(f.get("name") or old["name"]), str(f.get("kind") or old["kind"]), str(old["id"]))

    def apply(aid: str) -> Result:
        if "tax_id_last4" in f and old["tax_id_ref"]:
            tico.drop_credential(ctx.c, str(old["tax_id_ref"]))     # the old number goes with its replacement
        SAP.update(ctx.c, "fin_contacts", str(old["id"]), {**f, "updated_at": ctx.at})
        if alias:
            SAP.alias_add(ctx.c, ctx.book.id, alias, str(old["id"]), ctx.at)
            ctx.changed()
        return {"contact_id": str(old["id"])}
    return Plan(K.default_gate(ctx.org, who, "contact_update"), f"Change contact {old['name']}", apply,
                facts={"contact.kind": str(f.get("kind") or old["kind"]), "counterparty": str(old["name"])})


# ------------------------------------------------------------------------------------------------ document lines
def _item_account(ctx: Ctx, kind: str, x: AP.DocLine) -> str | None:
    """The account a line posts to when it names none: its item's expense account on a bill, its income account on
    an invoice. The item must be live (an inactive one is refused), whether or not the line names an account."""
    if not x.item_id:
        return None
    item = shared.live_item(ctx, x.item_id)
    return str(item["expense_account_id" if kind == "bill" else "income_account_id"] or "") or None


def resolve_lines(ctx: Ctx, who: str, kind: str, contact: Any,
                  lines: Sequence[AP.DocLine]) -> tuple[list[AP.DocLine], int]:
    """Each line's account: its item's account when it names an item (an account it names that differs is refused),
    else the one given; for a bill line without one, the first posting rule that matches, else the vendor's default
    line, else Other expenses (counted as asked, for a person to fix before entering it). Every line is checked against
    the asker's view."""
    from .posting_rules import first_for
    out: list[AP.DocLine] = []
    asked = 0
    for raw in lines:
        x = replace(raw, item_id=shared.canonical_item_id(ctx, raw.item_id)) if raw.item_id else raw
        item_account = _item_account(ctx, kind, x)
        if item_account and x.account_id and x.account_id != item_account:
            raise invalid("a line's item posts to its own account: name that account or leave the account out")
        if x.account_id or item_account:
            out.append(replace(x, account_id=item_account or x.account_id))
            continue
        if kind != "bill":
            raise invalid("an invoice line names its income account")
        rule = first_for(ctx, {"kind": "bill", "amount": x.amount, "counterparty": str(contact["name"]),
                               "description": x.description, "contact.id": str(contact["id"]), "direction": "out"})
        if rule is not None and rule.then.outcome == "post":
            out += [replace(x, account_id=account, amount=amount, tags={**x.tags, **tags})
                    for account, amount, tags in AP.split_amounts(x.amount, rule.then)]
            continue
        default = str(contact["default_account_id"] or "")
        if rule is None and default in ctx.accounts and ctx.accounts[default].active:
            out.append(replace(x, account_id=default))
            continue
        from .banking import OTHER, by_code
        other = by_code(ctx, OTHER)
        if other is None:
            raise invalid("say which account each line posts to")
        out.append(replace(x, account_id=other.id))
        asked += 1
    checked: list[AP.DocLine] = []
    ctl = control(ctx, kind).id
    for x in out:
        account = ctx.accounts.get(x.account_id)
        if account is None or not account.active or account.id == ctl or not _line_type_ok(kind, account):
            raise FinanceError(NOT_FOUND, f"no such {'/'.join(LINE_TYPES[kind])} account for a {kind} line")
        tags = shared.check_tags(ctx, posting.with_team(ctx, x.tags))
        if not may_write(ctx, who, account.id, tags):
            raise denied("a line's account or tags are outside your view")
        job = RR.get(ctx.c, "projects", tags["project"]) if kind == "invoice" and tags.get("project") else None
        if job is not None and str(job["customer_id"] or "") not in ("", str(contact["id"])):
            raise invalid("that project belongs to another customer")
        checked.append(replace(x, tags=tags))
    return checked, asked


def _line_rows(lines: Sequence[AP.DocLine], who: str, at: str) -> list[dict[str, object]]:
    """The rows a document's lines are stored as. Only an invoice line says whether it is taxable."""
    return [{"account_id": x.account_id, "amount": x.amount, "description": x.description,
             "tags_json": SK.dump(dict(x.tags)), "created_at": at, "created_by": who, "item_id": x.item_id or None,
             **({"po_line_id": x.po_line_id} if x.po_line_id else {}),
             **({"taxable": int(x.taxable)} if x.taxable is not None else {})} for x in lines]


def doc_lines_of(ctx: Ctx, kind: str, doc_id: str) -> list[AP.DocLine]:
    return [AP.DocLine(str(r["account_id"]), int(r["amount"]), str(r["description"]),
                       cast(dict[str, str], SK.loads(r["tags_json"])), None, str(r["item_id"] or ""))
            for r in SAP.lines(ctx.c, kind, [doc_id]).get(doc_id, [])]


def _hold(ctx: Ctx, bill_id: str, vendor_id: str, lines: Sequence[AP.DocLine], who: str) -> list[str]:
    """Each expense line of a draft bill holds budget until it is entered. A budget at its limit holds nothing: the bill
    is owed whatever the budget says, so it is recorded and the crossing is raised (budgeting.place). A line that bills
    an order line takes over that line's hold rather than being checked again: the order's hold shrinks by the amount
    as the bill's grows, so the commitment is the same before and after."""
    refused: list[str] = []
    for n, x in enumerate(lines):
        account = ctx.accounts[x.account_id]
        if account.type != "expense":
            continue
        moved = None
        if x.po_line_id:
            row = SP.po_line(ctx.c, x.po_line_id)
            moved = ("purchase_order", f"{row['po_id']}:{int(row['line_no'])}") if row is not None else None
        d = budgeting.place(ctx, budget_scope={"account_id": account.id, **scope_tags(x.tags), "vendor": vendor_id},
                            amount=x.amount, currency=ctx.book.base_currency, source="bill",
                            source_id=f"{bill_id}:{n}", expires_at=None, by=who, moved_from=moved)
        if not d.allowed:
            refused.append(d.reason)
    return refused


def _unhold(ctx: Ctx, bill_id: str, count: int) -> None:
    for n in range(count):
        budgeting.release_for(ctx, "bill", f"{bill_id}:{n}")


def _holds(ctx: Ctx, bill_id: str, count: int) -> list[str]:
    found = [SB.hold_for_source(ctx.c, "bill", f"{bill_id}:{n}") for n in range(count)]
    return [str(h["id"]) for h in found if h is not None]


def _attach_files(p: V.Payload) -> list[str]:
    files = V.texts(p, "attachments", 50) if p.get("attachments") not in (None, []) else []
    if any(len(f) > 80 for f in files):
        raise invalid("attachments are Library file ids")
    return files


def doc_facts(kind: str, contact: Any, lines: Sequence[AP.DocLine], ctx: Ctx, **more: object) -> Result:
    codes = sorted({ctx.accounts[x.account_id].code for x in lines if x.account_id in ctx.accounts})
    seen: dict[str, list[str]] = {}
    for x in lines:
        for k, v in x.tags.items():
            values = seen.setdefault(f"tags.{k}", [])
            if v not in values:
                values.append(v)
    # Every line's value of a tag counts: a rule on a tag matches a document with any line carrying that value.
    tags: dict[str, object] = {k: vs[0] if len(vs) == 1 else sorted(vs) for k, vs in seen.items()}
    return {"amount": sum(x.amount for x in lines), "counterparty": str(contact["name"]),
            f"{CONTACT_OF[kind]}.id": str(contact["id"]), "accounts": codes, **tags, **more}


# ------------------------------------------------------------------------------------------------ bills
def bill_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A bill as a draft: its expense lines hold budget now; `bill_approve` enters it."""
    V.only(p, ("vendor_id", "number", "bill_date", "due_date", "terms_days", "memo", "lines", "attachments", "origin"))
    vendor = contact_for(ctx, V.text(p, "vendor_id", 80), "bill")
    number = V.opt_text(p, "number", 80) or ""
    if number and SAP.number_taken(ctx.c, "bill", str(vendor["id"]), number):
        raise FinanceError(CONFLICT, f"{vendor['name']} already has a bill {number}")
    day = V.day(p, "bill_date")
    typed_due = V.opt_day(p, "due_date")
    terms = V.integer(p, "terms_days", 0, AP.MAX_TERMS) if p.get("terms_days") is not None else None
    if terms is None and typed_due is None and vendor["payment_terms_days"] is not None:
        terms = int(vendor["payment_terms_days"])       # the vendor's terms make the default due date (V1)
    due = AP.due_date(day, typed_due, terms)
    from . import purchasing as PU
    linked, orders = PU.link_bill_lines(ctx, who, str(vendor["id"]), AP.doc_lines(p.get("lines")))
    lines, asked = resolve_lines(ctx, who, "bill", vendor, linked)
    files = _attach_files(p)
    origin = V.choice(p, "origin", AP.ORIGINS, "manual")
    total = sum(x.amount for x in lines)
    bid = SL.new_id()

    def apply(aid: str) -> Result:
        SAP.insert(ctx.c, "fin_bills", {
            "id": bid, "book_id": ctx.book.id, "vendor_id": str(vendor["id"]), "number": number, "bill_date": day,
            "due_date": due, "terms_days": terms, "memo": V.opt_text(p, "memo", 500) or "", "status": "draft",
            "currency": ctx.book.base_currency, "total": total, "commitment_id": bid, "origin": origin,
            "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        SAP.replace_lines(ctx.c, "bill", bid, _line_rows(lines, who, ctx.at))
        SAP.attach(ctx.c, ctx.book.id, "bill", bid, files, who, ctx.at)
        SAP.drop_unmatched(ctx.c, ctx.book.id, files)
        # The holds come first: a line's hold moves to this bill before the order's holds follow its billing.
        refused = _hold(ctx, bid, str(vendor["id"]), lines, who)
        PU.sync_orders(ctx, who, orders)
        _event(ctx, RECEIVED, bid, {"bill_id": bid, "origin": origin})
        out: Result = {"bill_id": bid, "status": "draft", "total": total}
        if asked:
            out["uncategorized_lines"] = asked
        if refused:
            out["not_held"] = refused
        return out
    return Plan(K.default_gate(ctx.org, who, "bill_create"),
                f"Record a {_money(total)} {ctx.book.base_currency} bill from {vendor['name']}", apply,
                facts=doc_facts("bill", vendor, lines, ctx, origin=origin))


def _need_status(kind: str, doc: Any, allowed: Sequence[str], what: str) -> None:
    if str(doc["status"]) not in allowed:
        raise FinanceError(CONFLICT, f"that {kind} is {doc['status']}: {what}")


def bill_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Change a bill. Its vendor, date and lines change only while it is a draft (an entered bill is reversed by
    voiding it); its number, due date, memo and attachments change until it is paid or void."""
    V.only(p, ("bill_id", "vendor_id", "number", "bill_date", "due_date", "terms_days", "memo", "lines",
               "attachments"))
    bill = _doc(ctx, "bill", V.text(p, "bill_id", 80))
    draft = str(bill["status"]) == "draft"
    _need_status("bill", bill, ("draft", "open", "scheduled"), "it no longer changes")
    from . import purchasing as PU
    if ("vendor_id" in p or "lines" in p) and PU.bill_orders(ctx, str(bill["id"])):
        raise FinanceError(CONFLICT, "a bill that bills an order keeps its vendor and lines; void it and enter it again")
    if not draft and any(k in p for k in ("vendor_id", "bill_date", "lines")):
        raise FinanceError(CONFLICT, "an entered bill keeps its vendor, date and lines; void it and record it again")
    vendor = contact_for(ctx, V.text(p, "vendor_id", 80), "bill") if "vendor_id" in p else \
        contact_for(ctx, str(bill["vendor_id"]), "bill")
    old_lines = doc_lines_of(ctx, "bill", str(bill["id"]))
    linked, orders = PU.link_bill_lines(ctx, who, str(vendor["id"]), AP.doc_lines(p.get("lines")) if "lines" in p else [])
    lines, asked = resolve_lines(ctx, who, "bill", vendor, linked) if "lines" in p else (old_lines, 0)
    f: dict[str, object] = {}
    if "vendor_id" in p:
        f["vendor_id"] = str(vendor["id"])
    number = V.opt_text(p, "number", 80) or "" if "number" in p else str(bill["number"])
    if "number" in p or "vendor_id" in p:
        if number and SAP.number_taken(ctx.c, "bill", str(vendor["id"]), number, str(bill["id"])):
            raise FinanceError(CONFLICT, f"{vendor['name']} already has a bill {number}")
        f["number"] = number
    day = V.day(p, "bill_date") if "bill_date" in p else str(bill["bill_date"])
    if "bill_date" in p:
        f["bill_date"] = day
    if "terms_days" in p or "due_date" in p or "bill_date" in p:
        terms = V.integer(p, "terms_days", 0, AP.MAX_TERMS) if p.get("terms_days") is not None else \
            (None if "terms_days" in p else bill["terms_days"])
        due = V.opt_day(p, "due_date") if "due_date" in p else (None if "terms_days" in p else bill["due_date"])
        f["terms_days"], f["due_date"] = terms, AP.due_date(day, due, terms)
    if "memo" in p:
        f["memo"] = V.opt_text(p, "memo", 500) or ""
    if "lines" in p:
        f["total"] = sum(x.amount for x in lines)
    files = _attach_files(p)
    if not f and not files:
        raise invalid("say what changes")

    def apply(aid: str) -> Result:
        SAP.update(ctx.c, "fin_bills", str(bill["id"]), {**f, "updated_at": ctx.at})
        out: Result = {"bill_id": str(bill["id"]), "status": str(bill["status"])}
        if "lines" in p or "vendor_id" in p:
            _unhold(ctx, str(bill["id"]), len(old_lines))
            SAP.replace_lines(ctx.c, "bill", str(bill["id"]), _line_rows(lines, who, ctx.at))
            # The bill's holds come before the order's billing, as in bill_create: a line's
            # hold moves from the order here, and the order's holds then follow its billing.
            refused = _hold(ctx, str(bill["id"]), str(vendor["id"]), lines, who)
            PU.sync_orders(ctx, who, orders)
            if refused:
                out["not_held"] = refused
        if files:
            SAP.attach(ctx.c, ctx.book.id, "bill", str(bill["id"]), files, who, ctx.at)
            SAP.drop_unmatched(ctx.c, ctx.book.id, files)
        if asked:
            out["uncategorized_lines"] = asked
        return out
    return Plan(K.default_gate(ctx.org, who, "bill_update"), f"Change bill {bill['number'] or bill['id']}", apply,
                facts=doc_facts("bill", vendor, lines, ctx))


def _post_doc(ctx: Ctx, kind: str, doc: Any, lines: Sequence[AP.DocLine], day: str, who: str,
              holds: Sequence[str] = ()) -> str:
    """A document's own entry: Dr expense / Cr AP for a bill, Dr AR / Cr income for an invoice."""
    from .banking import open_day
    s = SIGN[kind]
    contact = str(doc[f"{CONTACT_OF[kind]}_id"])
    rows: list[dict[str, object]] = [{"account_id": x.account_id, "amount": s * x.amount, "tags": dict(x.tags),
                                      "contact_id": contact, "description": x.description[:200]} for x in lines]
    rows.append({"account_id": control(ctx, kind).id, "amount": -s * sum(x.amount for x in lines),
                 "contact_id": contact})
    label = f"{'Bill' if kind == 'bill' else 'Invoice'} {doc['number'] or doc['id']} · {doc['contact_name'] or ''}"
    done = posting.post_entry(ctx.c, {"date": open_day(ctx, day), "source": kind, "source_id": str(doc["id"]),
                                      "memo": label.strip(" ·")[:200], "lines": rows},
                              by=who, hold_ids=holds, at=ctx.at)
    if not done.get("posted"):
        raise FinanceError(CONFLICT, f"that {kind} was already posted")
    return str(done["entry_id"])


def bill_approve(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Enter a draft bill: Dr its lines / Cr accounts payable, its budget holds turned into actual in the same post."""
    V.only(p, ("bill_id",))
    bill = _doc(ctx, "bill", V.text(p, "bill_id", 80))
    _need_status("bill", bill, ("draft",), "only a draft is entered")
    lines = doc_lines_of(ctx, "bill", str(bill["id"]))
    if not lines:
        raise invalid("a bill has at least one line")
    for x in lines:
        if not may_write(ctx, who, x.account_id, x.tags):
            raise denied("a line's account or tags are outside your view")
    vendor = contact_for(ctx, str(bill["vendor_id"]), "bill")

    def apply(aid: str) -> Result:
        holds = _holds(ctx, str(bill["id"]), len(lines))
        entry = _post_doc(ctx, "bill", bill, lines, str(bill["bill_date"]), who, holds)
        if SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"status": "open", "entry_id": entry, "approved_by": who,
                                                            "approved_at": ctx.at, "updated_at": ctx.at},
                      "status='draft'") != 1:
            raise FinanceError(CONFLICT, "that bill moved on")
        return {"bill_id": str(bill["id"]), "status": "open", "entry_id": entry}
    return Plan(K.default_gate(ctx.org, who, "bill_approve"),
                f"Enter the {_money(int(bill['total']))} bill from {vendor['name']}", apply,
                facts=doc_facts("bill", vendor, lines, ctx))


def bill_schedule(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Pay an entered bill on a day: on `pay_on` the hourly job requests a payment_create for what is left, as the
    teammate who scheduled it (their rights and the payment rules apply then), one live payment per bill."""
    V.only(p, ("bill_id", "pay_on", "money_account_id", "recipient_id", "memo"))
    bill = _doc(ctx, "bill", V.text(p, "bill_id", 80))
    if who != SYSTEM and not _sees_doc(ctx, who, "bill", bill):
        raise FinanceError(NOT_FOUND, "no such bill in your view")
    _need_status("bill", bill, ("open", "scheduled"), "only an entered, unpaid bill is scheduled")
    if SK.live_payment(ctx.c, f"bill:{bill['id']}") is not None:
        raise FinanceError(CONFLICT, "a payment for this bill is already live")
    if not R.has(ctx.org, who, R.PAY, ctx.rights(who)):
        raise denied("scheduling a payment needs finance.pay")
    vendor = contact_for(ctx, str(bill["vendor_id"]), "bill")
    rid = V.opt_text(p, "recipient_id", 80) or str(vendor["payment_ref"] or "")
    rec = SK.get(ctx.c, "fin_recipients", rid) if rid else None
    if rec is None or str(rec["status"]) != "active":
        raise FinanceError(NOT_FOUND, "no active recipient to pay; add the vendor's payment details first")
    money = SK.get(ctx.c, "fin_money_accounts", V.text(p, "money_account_id", 80))
    if money is None or str(money["kind"]) != "bank" or not money["connection_id"] or str(money["status"]) != "active":
        raise FinanceError(NOT_FOUND, "no such bank account")
    if not may_write(ctx, who, control(ctx, "bill").id, {}):
        raise denied("paying a bill posts to accounts payable, which is outside your view")
    pay_on = V.day(p, "pay_on")
    old = SK.loads(bill["schedule_json"])
    n = int(cast(int, old.get("n") or 0)) + 1
    sched = {"n": n, "pay_on": pay_on, "money_account_id": str(money["id"]), "recipient_id": str(rec["id"]),
             "memo": V.opt_text(p, "memo", 140) or "", "by": who}

    def apply(aid: str) -> Result:
        from .actions import withdraw_bill_asks
        SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"status": "scheduled", "pay_on": pay_on,
                                                         "schedule_json": SK.dump(sched), "updated_at": ctx.at})
        # A rescheduled bill pays only on its new day: an earlier ask to pay it (waiting on approval) is withdrawn,
        # after the new schedule is written so its end does not reopen the bill.
        withdraw_bill_asks(ctx, str(bill["id"]), aid, "the bill was rescheduled")
        return {"bill_id": str(bill["id"]), "status": "scheduled", "pay_on": pay_on}
    return Plan(K.default_gate(ctx.org, who, "bill_schedule"),
                f"Pay the {_money(_left('bill', bill))} bill from {vendor['name']} on {pay_on}", apply,
                facts={"amount": _left("bill", bill), "counterparty": str(vendor["name"]),
                       "recipient.id": str(rec["id"])})


def bill_void(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Void a bill: a draft releases its holds; an entered one is reversed. One with money settled against it, or a
    live payment, is refused until those are undone."""
    V.only(p, ("bill_id", "reason"))
    bill = _doc(ctx, "bill", V.text(p, "bill_id", 80))
    _need_status("bill", bill, ("draft", "open", "scheduled"), "it cannot be voided")
    if int(bill["amount_paid"]) or SK.live_payment(ctx.c, f"bill:{bill['id']}") is not None:
        raise FinanceError(CONFLICT, "money is settled or on its way against this bill; undo that first")
    reason = V.opt_text(p, "reason", 300) or ""

    def apply(aid: str) -> Result:
        from .banking import open_day
        from . import purchasing as PU
        bill_id = str(bill["id"])
        posted = False
        if bill["entry_id"]:
            entry = SL.entry_row(ctx.c, str(bill["entry_id"]))
            if entry is not None and str(entry["status"]) == "posted":
                posting.reverse(ctx, str(bill["entry_id"]), day=open_day(ctx, ctx.today),
                                memo=f"Void bill {bill['number'] or bill['id']}: {reason}"[:200], by=who,
                                action_id=aid)
                posted = True
        # The orders the bill billed take back their room first, so the holds the bill still has move back to them.
        PU.give_back(ctx, who, bill_id, posted=posted)
        _unhold(ctx, bill_id, len(doc_lines_of(ctx, "bill", bill_id)))
        SAP.update(ctx.c, "fin_bills", bill_id, {"status": "void", "pay_on": None, "voided_at": ctx.at,
                                                 "updated_at": ctx.at})
        PU.sync_orders(ctx, who, PU.bill_orders(ctx, bill_id))
        return {"bill_id": str(bill["id"]), "status": "void"}
    return Plan(K.default_gate(ctx.org, who, "bill_void"), f"Void bill {bill['number'] or bill['id']}", apply,
                facts={"amount": int(bill["total"]), "counterparty": str(bill["contact_name"] or "")})


# ------------------------------------------------------------------------------------------------ invoices
def _next_number(ctx: Ctx, customer_id: str) -> str:
    n = SAP.doc_count(ctx.c, "invoice", ctx.book.id) + 1
    while SAP.number_taken(ctx.c, "invoice", customer_id, f"INV-{n:04d}"):
        n += 1
    return f"INV-{n:04d}"


def invoice_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A draft invoice (numbered INV-0001... when no number is given); `invoice_send` posts it."""
    V.only(p, ("customer_id", "number", "invoice_date", "due_date", "terms_days", "memo", "lines", "payment_link"))
    customer = contact_for(ctx, V.text(p, "customer_id", 80), "invoice")
    number = V.opt_text(p, "number", 80) or _next_number(ctx, str(customer["id"]))
    if SAP.number_taken(ctx.c, "invoice", str(customer["id"]), number):
        raise FinanceError(CONFLICT, f"invoice {number} exists")
    day = V.day(p, "invoice_date")
    terms = V.integer(p, "terms_days", 0, AP.MAX_TERMS) if p.get("terms_days") is not None else None
    due = AP.due_date(day, V.opt_day(p, "due_date"), terms)
    lines, _ = resolve_lines(ctx, who, "invoice", customer, AP.doc_lines(p.get("lines"), invoice=True))
    carries_tax = any(ctx.accounts[x.account_id].subtype == SALES_TAX for x in lines)
    lines = [replace(x, taxable=taxable_default(x.taxable, ctx.accounts[x.account_id].type == "income", carries_tax))
             for x in lines]
    link = V.opt_text(p, "payment_link", 500) or None
    if link and not link.startswith("https://"):
        raise invalid("a payment link is an https:// address")
    total = sum(x.amount for x in lines)
    iid = SL.new_id()

    def apply(aid: str) -> Result:
        SAP.insert(ctx.c, "fin_invoices", {
            "id": iid, "book_id": ctx.book.id, "customer_id": str(customer["id"]), "number": number,
            "invoice_date": day, "due_date": due, "terms_days": terms, "memo": V.opt_text(p, "memo", 500) or "",
            "status": "draft", "currency": ctx.book.base_currency, "total": total, "payment_link": link,
            "created_at": ctx.at, "created_by": who, "updated_at": ctx.at})
        SAP.replace_lines(ctx.c, "invoice", iid, _line_rows(lines, who, ctx.at))
        return {"invoice_id": iid, "number": number, "status": "draft", "total": total}
    return Plan(K.default_gate(ctx.org, who, "invoice_create"),
                f"Draft invoice {number} for {_money(total)} {ctx.book.base_currency} to {customer['name']}", apply,
                facts=doc_facts("invoice", customer, lines, ctx))


def document(ctx: Ctx, kind: str, doc: Any) -> Result:
    """What a sent invoice says, for whoever delivers it (Finance has no outbound mail of its own)."""
    customer = SAP.get(ctx.c, "fin_contacts", str(doc[f"{CONTACT_OF[kind]}_id"]))
    lines = SAP.lines(ctx.c, kind, [str(doc["id"])]).get(str(doc["id"]), [])
    return {"number": str(doc["number"]), "date": str(doc[f"{kind}_date"]), "due_date": doc["due_date"],
            "currency": str(doc["currency"]), "total": int(doc["total"]), "memo": str(doc["memo"]),
            "payment_link": doc["payment_link"] if kind == "invoice" else None,
            "to": {"id": str(customer["id"]), "name": str(customer["name"]),
                   "emails": SK.loads_list(customer["emails_json"])} if customer is not None else None,
            "lines": [{"description": str(r["description"]), "amount": int(r["amount"])} for r in lines]}


def invoice_send(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Send a draft invoice: Dr AR / Cr its income lines, marked sent. Finance has no outbound mail in the action's
    transaction, so the result carries the document for the bot or person to deliver."""
    V.only(p, ("invoice_id",))
    inv = _doc(ctx, "invoice", V.text(p, "invoice_id", 80))
    _need_status("invoice", inv, ("draft",), "only a draft is sent")
    lines = doc_lines_of(ctx, "invoice", str(inv["id"]))
    if not lines:
        raise invalid("an invoice has at least one line")
    for x in lines:
        if not may_write(ctx, who, x.account_id, x.tags):
            raise denied("a line's account or tags are outside your view")
    customer = contact_for(ctx, str(inv["customer_id"]), "invoice")

    def apply(aid: str) -> Result:
        entry = _post_doc(ctx, "invoice", inv, lines, str(inv["invoice_date"]), who)
        if SAP.update(ctx.c, "fin_invoices", str(inv["id"]), {"status": "sent", "entry_id": entry, "sent_at": ctx.at,
                                                              "updated_at": ctx.at}, "status='draft'") != 1:
            raise FinanceError(CONFLICT, "that invoice moved on")
        return {"invoice_id": str(inv["id"]), "status": "sent", "entry_id": entry, "delivery": "caller",
                "document": document(ctx, "invoice", inv)}
    return Plan(K.default_gate(ctx.org, who, "invoice_send"),
                f"Send invoice {inv['number']} for {_money(int(inv['total']))} to {customer['name']}", apply,
                facts=doc_facts("invoice", customer, lines, ctx))


def invoice_write_off(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Write off what a customer will not pay: Dr the write-off line (default Other expenses) / Cr AR, accrual basis
    only (the cash view never had that income). The invoice is paid once nothing is left."""
    from .banking import OTHER, by_code, open_day
    V.only(p, ("invoice_id", "amount", "account_id", "date", "memo"))
    inv = _doc(ctx, "invoice", V.text(p, "invoice_id", 80))
    _need_status("invoice", inv, OPEN["invoice"], "only a sent invoice is written off")
    left = _left("invoice", inv)
    given = V.opt_amount(p)
    amount = left if given is None else given     # 0 is refused below, never read as the balance left
    if not 0 < amount <= left:
        raise invalid(f"a write-off is more than 0 and at most what is left ({left})")
    account = ctx.accounts.get(V.opt_text(p, "account_id", 80) or "") if "account_id" in p else by_code(ctx, OTHER)
    if account is None or not account.active or account.type != "expense":
        raise FinanceError(NOT_FOUND, "no such expense account")
    if not may_write(ctx, who, account.id, {}):
        raise denied("that account is outside your view")
    day = V.opt_day(p, "date") or ctx.today
    memo = V.opt_text(p, "memo", 200) or f"Write off invoice {inv['number']}"

    def apply(aid: str) -> Result:
        done = posting.post_entry(ctx.c, {
            "date": open_day(ctx, day), "source": "invoice", "source_id": f"{inv['id']}:writeoff:{aid}",
            "memo": memo[:200], "basis": "accrual",
            "lines": [{"account_id": account.id, "amount": amount, "contact_id": str(inv["customer_id"])},
                      {"account_id": control(ctx, "invoice").id, "amount": -amount,
                       "contact_id": str(inv["customer_id"])}]}, by=who, at=ctx.at)
        written = int(inv["written_off"]) + amount
        status = "paid" if int(inv["amount_paid"]) + written >= int(inv["total"]) else str(inv["status"])
        SAP.update(ctx.c, "fin_invoices", str(inv["id"]), {"written_off": written, "status": status,
                                                           "updated_at": ctx.at})
        return {"invoice_id": str(inv["id"]), "status": status, "written_off": written,
                "entry_id": str(done["entry_id"])}
    return Plan(K.default_gate(ctx.org, who, "invoice_write_off"),
                f"Write off {_money(amount)} of invoice {inv['number']}", apply,
                facts={"amount": amount, "counterparty": str(inv["contact_name"] or ""), "accounts": [account.code]})


def invoice_void(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Void an invoice: a draft simply; a sent one with nothing received or written off is reversed."""
    V.only(p, ("invoice_id", "reason"))
    inv = _doc(ctx, "invoice", V.text(p, "invoice_id", 80))
    _need_status("invoice", inv, ("draft", "sent"), "it cannot be voided")
    if int(inv["amount_paid"]) or int(inv["written_off"]):
        raise FinanceError(CONFLICT, "money is settled or written off against this invoice; undo that first")
    reason = V.opt_text(p, "reason", 300) or ""

    def apply(aid: str) -> Result:
        from .banking import open_day
        if inv["entry_id"]:
            entry = SL.entry_row(ctx.c, str(inv["entry_id"]))
            if entry is not None and str(entry["status"]) == "posted":
                posting.reverse(ctx, str(inv["entry_id"]), day=open_day(ctx, ctx.today),
                                memo=f"Void invoice {inv['number']}: {reason}"[:200], by=who, action_id=aid)
        SAP.update(ctx.c, "fin_invoices", str(inv["id"]), {"status": "void", "voided_at": ctx.at,
                                                           "updated_at": ctx.at})
        return {"invoice_id": str(inv["id"]), "status": "void"}
    return Plan(K.default_gate(ctx.org, who, "invoice_void"), f"Void invoice {inv['number']}", apply,
                facts={"amount": int(inv["total"]), "counterparty": str(inv["contact_name"] or "")})


# ------------------------------------------------------------------------------------------------ settlements
def settle(ctx: Ctx, kind: str, doc_id: str, *, amount: int, day: str, from_kind: str, from_id: str, entry_id: str,
           action_id: str = "", by: str = SYSTEM, cash: bool = True) -> str:
    """Link money to a document: its paid amount and status move, and the cash view takes the lines' share of it on
    `day` (or the first open day, when that month is closed, as its entry posts). An invoice is paid once received
    plus written off covers it. `cash=False` moves the document without moving the cash view (a credit note applied to
    it: no money changed hands)."""
    from .banking import open_day
    doc = _doc(ctx, kind, doc_id)
    before = int(doc["amount_paid"])
    after = before + amount
    if cash and from_kind != "credit":          # a credit applied moves no cash, whoever applies it
        _cash(ctx, kind, doc, before, after, open_day(ctx, day))
    sid = SAP.insert(ctx.c, "fin_settlements", {
        "book_id": ctx.book.id, "from_kind": from_kind, "from_id": from_id, "to_kind": kind, "to_id": doc_id,
        "amount": amount, "settled_on": day, "entry_id": entry_id or None, "action_id": action_id or None,
        "created_at": ctx.at, "created_by": by})
    SAP.update(ctx.c, SAP.DOCS[kind][0], doc_id, {"amount_paid": after, "status": _status(kind, doc, after),
                                                  "updated_at": ctx.at})
    return sid


def _status(kind: str, doc: Any, paid: int) -> str:
    covered = paid + (int(doc["written_off"]) if kind == "invoice" else 0)
    if covered >= int(doc["total"]):
        return "paid"
    if kind == "invoice":
        return "partial" if paid else "sent"
    was = str(doc["status"])
    return was if was in AP.OPEN_BILL else "scheduled" if doc["pay_on"] else "open"


def status_of(kind: str, doc: Any, paid: int) -> str:
    """A document's status once `paid` is settled against it (what a mirrored document takes from its settlements)."""
    return _status(kind, doc, paid)


def _cash(ctx: Ctx, kind: str, doc: Any, before: int, after: int, day: str) -> None:
    lines = doc_lines_of(ctx, kind, str(doc["id"]))
    contact = str(doc[f"{CONTACT_OF[kind]}_id"])
    for x, share in zip(lines, AP.shares([x.amount for x in lines], before, after), strict=True):
        account = ctx.accounts.get(x.account_id)
        if share and account is not None and account.type in L.PNL_TYPES:
            SAP.cash_roll(ctx.c, ctx.book.id, day, x.account_id, posting.with_team(ctx, x.tags), contact,
                          SIGN[kind] * share)


def unsettle(ctx: Ctx, s: Any, by: str = SYSTEM, *, cash: bool = True) -> None:
    """Undo one settlement (its money left, or its feed row changed): the document owes it again and the cash view
    gives the share back on the day it took it (or today, when that month is closed). `cash=False` undoes a settlement
    that never moved the cash view (see settle)."""
    from .banking import open_day
    kind = str(s["to_kind"])
    doc = _doc(ctx, kind, str(s["to_id"]))
    before = int(doc["amount_paid"])
    after = before - int(s["amount"])
    if SAP.update(ctx.c, "fin_settlements", str(s["id"]), {"voided_at": ctx.at, "voided_by": by},
                  "voided_at IS NULL") != 1:
        return
    if cash and str(s["from_kind"]) != "credit":
        _cash(ctx, kind, doc, before, after, open_day(ctx, str(s["settled_on"])))
    status = _status(kind, doc, after)
    if kind == "bill" and status == "scheduled" and SK.live_payment(ctx.c, f"bill:{doc['id']}") is None:
        status = "open"
    SAP.update(ctx.c, SAP.DOCS[kind][0], str(doc["id"]), {"amount_paid": after, "status": status,
                                                          "pay_on": doc["pay_on"] if status == "scheduled" else None,
                                                          "updated_at": ctx.at}
               if kind == "bill" else {"amount_paid": after, "status": status, "updated_at": ctx.at})


def feed_gone(ctx: Ctx, feed_id: str, entry_ids: Sequence[str] = ()) -> None:
    """A feed row unposted (the bank changed or cancelled it, or it was re-categorized): what it paid is owed again,
    including what a Finance payment settled through the row's entries (a revised row settles anew, at its new
    amount)."""
    for s in SAP.settlements_from(ctx.c, "feed", feed_id):
        unsettle(ctx, s)
    for eid in entry_ids:
        for s in SAP.settlements_by_entry(ctx.c, eid):
            if str(s["from_kind"]) == "payment":
                unsettle(ctx, s)


def retagged(ctx: Ctx, row: Any, old: Mapping[str, str], new: Mapping[str, str]) -> None:
    """A posted line's tags changed (categorize). An entry the cash view takes as posted moves with the accrual
    rollups; for one it leaves out (a bill's or invoice's, or accrual-only), the line's correction moves to the new
    tags too, and for a document's own entry so do the document line's tags and what its settlements recognized (on
    the day each did, or the first open day when that month is closed), so cash attribution follows the tags."""
    from .banking import open_day
    entry = SL.entry_row(ctx.c, str(row["entry_id"]))
    account = ctx.accounts.get(str(row["account_id"]))
    if entry is None or account is None or account.type not in L.PNL_TYPES or dict(old) == dict(new):
        return
    source, source_id = str(entry["source"]), str(entry["source_id"])
    if str(entry["basis"] or "both") == "both" and source not in L.DOC_SOURCES:
        return
    amount, day, contact = int(row["amount"]), str(row["entry_date"]), str(row["contact_id"] or "")
    SAP.cash_roll(ctx.c, ctx.book.id, day, account.id, old, contact, amount)
    SAP.cash_roll(ctx.c, ctx.book.id, day, account.id, new, contact, -amount)
    if source not in L.DOC_SOURCES or ":" in source_id:
        return
    doc = SAP.get(ctx.c, SAP.DOCS[source][0], source_id)
    if doc is None or str(doc["entry_id"] or "") != str(entry["id"]):
        return
    docs = SAP.lines(ctx.c, source, [source_id])[source_id]
    idx = int(row["line_no"])
    if idx >= len(docs) or str(docs[idx]["account_id"] or "") != account.id:
        return
    SAP.set_line_tags(ctx.c, source, str(docs[idx]["id"]), new)
    amounts = [int(r["amount"]) for r in docs]
    before = 0
    for st in SAP.settlements_to(ctx.c, source, source_id):
        after = before + int(st["amount"])
        share = AP.shares(amounts, before, after)[idx]
        before = after
        if share:
            on = open_day(ctx, str(st["settled_on"]))
            SAP.cash_roll(ctx.c, ctx.book.id, on, account.id, old, contact, -SIGN[source] * share)
            SAP.cash_roll(ctx.c, ctx.book.id, on, account.id, new, contact, SIGN[source] * share)


def entry_reversed(ctx: Ctx, entry_id: str, by: str = SYSTEM) -> None:
    """An entry that recorded money on a document was reversed by hand: the document owes that money again."""
    for s in SAP.settlements_by_entry(ctx.c, entry_id):
        unsettle(ctx, s, by)


def payment_settled(ctx: Ctx, pay: Any, feed_id: str) -> None:
    """A bill's payment reached the bank: it settles the bill (up to what is left; any more stays in AP as a
    prepayment), on the bank row's date, linked to the row's entry."""
    obligation = str(pay["obligation"] or "")
    if not obligation.startswith("bill:") or SAP.settlements_from(ctx.c, "payment", str(pay["id"])):
        return
    bill = SAP.doc_with_contact(ctx.c, "bill", obligation.removeprefix("bill:"))
    row = SK.get(ctx.c, "fin_feed_txns", feed_id)
    if bill is None or row is None or str(bill["status"]) not in AP.OPEN_BILL:
        return
    rec = SK.get(ctx.c, "fin_recipients", str(pay["recipient_id"]))
    if str(pay["account_id"]) != control(ctx, "bill").id or rec is None or not _pays_vendor(ctx, bill, rec):
        return                      # not a payment of this bill after all (checked at create too): it settles nothing
    amount = min(abs(int(row["amount"])), _left("bill", bill))     # what the bank moved (a revised row moves less)
    if amount > 0:
        settle(ctx, "bill", str(bill["id"]), amount=amount, day=str(row["txn_date"]), from_kind="payment",
               from_id=str(pay["id"]), entry_id=str(row["entry_id"] or ""), action_id=str(pay["action_id"] or ""))
    SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"pay_on": None})


def bill_payment(ctx: Ctx, who: str, obligation: str, rec: Any, amount: int, line_id: str) -> K.Gate:
    """Check a payment_create for a bill (obligation `bill:<id>`): the bill is in these books, the actor sees it, it
    is entered and unpaid, the amount is at most what it owes, the payment posts to AP (the bill took the expense
    already) and goes to the vendor's recipient (its payment details, or the one its schedule names). What it returns
    is the gate the bill's expense budgets ask for: their spend was recognized when the bill was entered, so a budget
    already past its amount at hold_payment, ask or decline sends the payment to a finance admin (the bill is owed
    either way, so it is never refused for the budget); within budget or warn, ALLOW."""
    bill = _doc(ctx, "bill", obligation.removeprefix("bill:"))
    if who != SYSTEM and not _sees_doc(ctx, who, "bill", bill):
        raise FinanceError(NOT_FOUND, "no such bill in your view")
    _need_status("bill", bill, AP.OPEN_BILL, "only an entered, unpaid bill is paid")
    left = _left("bill", bill)
    if amount > left:
        raise invalid(f"that bill owes {left}")
    if line_id != control(ctx, "bill").id:
        raise invalid("a bill's payment posts to accounts payable (the bill recorded the expense)")
    if not _pays_vendor(ctx, bill, rec):
        raise denied("that recipient is not this bill's vendor")
    waits = payment_waits(ctx, bill)
    if waits:
        raise denied(waits)
    return _bill_budget_gate(ctx, bill)


def payment_waits(ctx: Ctx, bill: Any) -> str:
    """Why a bill ordered by a purchase order is not paid now: its budget is past its amount at hold_payment, so its
    payment waits until the budget fits again. "" when the payment may go ahead. Every payment path asks this."""
    if not SP.bill_orders(ctx.c, str(bill["id"])):
        return ""
    for bid in _bill_pasts(ctx, bill):
        if ctx.budgets()[bid].at_limit == "hold_payment":
            return f"the {ctx.budgets()[bid].name} budget is past its amount: this bill's payment waits until it fits"
    return ""


def _sees_doc(ctx: Ctx, who: str, kind: str, doc: Any) -> bool:
    from .tab import private_cut
    from .tab_ap import keep_docs, sees_side
    view = ctx.view(who)
    return sees_side(ctx, view, kind) and bool(keep_docs(ctx.c, kind, [doc], set(private_cut(ctx, view)))[0])


def _pays_vendor(ctx: Ctx, bill: Any, rec: Any) -> bool:
    vendor = SAP.get(ctx.c, "fin_contacts", str(bill["vendor_id"]))
    allowed: set[str] = {str(vendor["payment_ref"] or "")} if vendor is not None else set()
    allowed.add(str(SK.loads(bill["schedule_json"]).get("recipient_id") or ""))
    return str(rec["id"]) in allowed - {""} or str(rec["contact_id"] or "") == str(bill["vendor_id"])


def bill_budget(ctx: Ctx, bill_id: str) -> K.Gate:
    """What a bill's expense budgets ask of a payment of it now (payments.recheck): ALLOW when the bill is gone."""
    bill = SAP.get(ctx.c, "fin_bills", bill_id)
    return _bill_budget_gate(ctx, bill) if bill is not None else K.ALLOW


def _bill_pasts(ctx: Ctx, bill: Any) -> list[str]:
    """Every budget a bill's expense lines (and their parents) leave past its amount, at a limit other than warn. A line
    is judged for the teammate its spend is set aside for, as a new spend is: that teammate's own set-aside on the
    line's budgets is not held against it."""
    at = dates.day(str(bill["bill_date"]))
    out: list[str] = []
    for x in doc_lines_of(ctx, "bill", str(bill["id"])):
        account = ctx.accounts.get(x.account_id)
        if account is None or account.type != "expense":
            continue
        tags = scope_tags(x.tags)
        b = budgeting.leaf(ctx, account.id, tags.get("team", ""), tags.get("teammate", ""), str(bill["vendor_id"]))
        chain = budgeting.chain_of(ctx, b.id) if b is not None else []
        target = budgeting.scope_target(tags)
        for bid in chain:
            if bid in out:
                continue
            st = budgeting.state(ctx, bid, at, target, chain)
            if st.remaining is not None and st.remaining < 0 and ctx.budgets()[bid].at_limit != "warn":
                out.append(bid)
    return out


def _bill_worst(ctx: Ctx, bill: Any) -> tuple[int, str]:
    """The strictest at-limit a bill's expense budgets are past (their level, budget id), or (-1, "") when none is."""
    worst: tuple[int, str] = (-1, "")
    for bid in _bill_pasts(ctx, bill):
        level = B.STRICTNESS[ctx.budgets()[bid].at_limit]
        if level > worst[0]:
            worst = (level, bid)
    return worst


def _bill_budget_gate(ctx: Ctx, bill: Any) -> K.Gate:
    _, bid = _bill_worst(ctx, bill)
    return K.Gate("needs_approval", "finance_admin", f"budget:{bid}") if bid else K.ALLOW


def payment_ended(ctx: Ctx, pay: Any) -> None:
    """A bill's payment failed, was returned or cancelled: what it settled is owed again and the bill is open."""
    obligation = str(pay["obligation"] or "")
    if not obligation.startswith("bill:"):
        return
    for s in SAP.settlements_from(ctx.c, "payment", str(pay["id"])):
        unsettle(ctx, s)
    bill = SAP.get(ctx.c, "fin_bills", obligation.removeprefix("bill:"))
    if bill is not None and str(bill["status"]) == "scheduled":
        SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"status": "open", "pay_on": None, "updated_at": ctx.at})


def payment_ask_ended(ctx: Ctx, action_id: str, body: Mapping[str, object], status: str, reason: str) -> None:
    """The payment a scheduled bill asked for ended without running (denied, expired, withdrawn or failed on its
    approval): the bill is open again with no schedule, and says how the ask ended."""
    obligation = str(body.get("obligation") or "").strip()
    bill = SAP.get(ctx.c, "fin_bills", obligation.removeprefix("bill:")) if obligation.startswith("bill:") else None
    if bill is None or str(bill["status"]) != "scheduled":
        return
    sched = SK.loads(bill["schedule_json"])
    if str(sched.get("action_id") or "") == action_id:
        _unschedule(ctx, bill, sched, status, reason, int(cast(int, body.get("amount") or 0)))


def _unschedule(ctx: Ctx, bill: Any, sched: Mapping[str, object], status: str, reason: str, amount: int) -> None:
    """Open the bill with no schedule. What the bill row shows of the ask stays (`ended`); its count stays, so the
    next schedule's payment gets a fresh key."""
    SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"status": "open", "pay_on": None, "updated_at": ctx.at,
                                                     "schedule_json": SK.dump({
                                                         "n": sched.get("n") or 1,
                                                         "ended": {"status": status, "reason": reason[:300],
                                                                   "on": ctx.today, "amount": amount}})})


# ------------------------------------------------------------------------------------------------ paying
def _same_name(counterparty: str, name: str) -> bool:
    a = "".join(ch for ch in counterparty.lower() if ch.isalnum())
    b = "".join(ch for ch in name.lower() if ch.isalnum())
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= NAME_MIN and (a in b or b in a)))


def named(counterparty: str, docs: Sequence[Any]) -> list[Any]:
    """The documents whose contact the counterparty names (no counterparty names none)."""
    return [d for d in docs if _same_name(counterparty, str(d["contact_name"] or ""))]


OWING_MOST = 50             # open documents owing a row's exact amount looked at; with more, the row is unclear


def match_feed(ctx: Ctx, row: Any, t: F.FeedTxn) -> tuple[str, Any] | str | None:
    """The one open document a new settled feed row pays: a debit and an open bill (a credit and a sent invoice) that
    still owes exactly the row's amount, from a contact whose name the counterparty carries. None when there is none
    or more than one (it posts as usual); ASK when more documents owe that amount than were looked at (the one it pays
    may be among those left out: the row stays new, nothing posts, and a person links it)."""
    kind = "bill" if t.amount < 0 else "invoice"
    owing = SAP.open_owing(ctx.c, kind, ctx.book.id, abs(t.amount), OWING_MOST + 1)
    if len(owing) > OWING_MOST:
        return ASK
    found = [d for d in owing if _same_name(t.counterparty, str(d["contact_name"] or ""))]
    return (kind, found[0]) if len(found) == 1 else None


def _window(day: str) -> tuple[str, str]:
    from .banking import MATCH_DAYS
    d = dates.day(day)
    return (d - timedelta(days=MATCH_DAYS)).isoformat(), (d + timedelta(days=MATCH_DAYS)).isoformat()


def _named(counterparty: str, name: str) -> bool:
    """A bank row with no counterparty matches on amount, account and day alone."""
    return not counterparty.strip() or _same_name(counterparty, name)


ASK = "ask"                 # expected_match: the row may pay a marked payment, but which is unclear; a person picks


def expected_match(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, *, posted: F.FeedTxn | None = None,
                   fees: Sequence[dict[str, object]] = ()) -> str:
    """Link a new settled row to a payment marked paid from its money account that waits for it (same direction and
    amount, marked within MATCH_DAYS of the row): the row takes that entry, nothing new posts, and the settlement now
    comes from the row (so a bank change or cancellation makes the document owe it again). None of that amount: a set
    of two or more payments to one contact that sums to it (several bills in one transfer), whose entries become one.
    The contact's name only breaks a tie (a bank often names the rail, not the vendor). `posted` is the balance the row
    posts when it differs from the amount matched (a Stripe sale matches its gross and posts its net), and `fees` are
    the lines beyond the payments; with either, the row's entry is its own. "linked"; ASK when candidates exist but no
    single one (the row stays new, never posting a second money line, and a person picks); "" when nothing waits, and
    the row posts as usual."""
    kind = "bill" if t.amount < 0 else "invoice"
    start, end = _window(t.txn_date)
    exact, cut = _waiting(ctx, kind, money, abs(t.amount), start, end)
    if cut:
        return ASK              # more waiting than were looked at: which one is unclear
    single = [[s] for s in exact]
    group = _pick(single, t.counterparty) if single else None
    if not single:
        waiting, cut = _waiting(ctx, kind, money, 0, start, end)
        sets, partial = _combined(waiting, abs(t.amount))
        if cut or partial:
            return ASK          # the search could not look at every candidate: a person picks (feed_match)
        if not sets:
            return ""
        group = _pick(sets, t.counterparty)
    if group is None:
        return ASK
    _link(ctx, row, money, posted or t, group, fees)
    return "linked"


def may_wait(ctx: Ctx, row: Any, money: Any) -> bool:
    """Whether a settled row not yet in the books may be the bank side of payments already marked paid from its money
    account: expected_match leaves such a row to a person (feed_match), so it may neither pay another document nor be
    posted as a bank line of its own (categorize). True when a payment waits for its gross (alone, or in a set that sums
    to it), or the search could not look at every candidate.
    A booked row, or a transfer between accounts, is never held back."""
    from .banking import provider_fee
    gross = abs(int(row["amount"])) + provider_fee(row, money)
    if row["entry_id"] or not gross or str(row["kind"] or "") in F.TRANSFER_KINDS:
        return False                # a transfer is never matched to payments at ingest either
    kind = "bill" if int(row["amount"]) < 0 else "invoice"
    start, end = _window(str(row["txn_date"]))
    exact, cut = _waiting(ctx, kind, money, gross, start, end)
    if exact or cut:
        return True
    waiting, cut = _waiting(ctx, kind, money, 0, start, end)
    sets, partial = _combined(waiting, gross)
    return bool(sets) or cut or partial


def _link(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, group: Sequence[Any],
          fees: Sequence[dict[str, object]] = ()) -> str:
    """The row is the bank side of these waiting payments: one takes its entry as it is; several, or one with fees
    beyond the payment, are replaced by one entry for the row (the bank moves once). Each settlement now comes from the
    row. The row's entry id."""
    from .banking import mark_posted
    fid = str(row["id"])
    if len(group) == 1 and not fees:
        mark_posted(ctx, [fid], str(group[0]["entry_id"]))
        SAP.update(ctx.c, "fin_settlements", str(group[0]["id"]), {"from_kind": "feed", "from_id": fid})
        return str(group[0]["entry_id"])
    return _post_combined(ctx, row, money, t, group, fees)


def _pick(sets: Sequence[list[Any]], counterparty: str) -> list[Any] | None:
    """The one candidate, else the one whose contact the counterparty names, else None."""
    if len(sets) == 1:
        return sets[0]
    named = [g for g in sets if counterparty.strip() and _same_name(counterparty, str(g[0]["contact_name"] or ""))]
    return named[0] if len(named) == 1 else None


def _waiting(ctx: Ctx, kind: str, money: Any, amount: int, start: str, end: str, ids: Sequence[str] = ()
             ) -> tuple[list[Any], bool]:
    """Payments marked paid from this money account still waiting for their row (of `amount`, 0: any; only `ids` when
    given), whose entry is posted and no row's yet; True when there were more than WAITING_MOST to look at."""
    found: list[Any] = []
    raw = SAP.expected(ctx.c, kind, str(money["id"]), amount, start, end, most=WAITING_MOST + 1, ids=ids)
    for s in raw[:WAITING_MOST]:
        entry = SL.entry_row(ctx.c, str(s["entry_id"] or "-"))
        if entry is not None and str(entry["status"]) == "posted" and not SK.rows_of_entry(ctx.c, str(entry["id"])):
            found.append(s)
    return found, len(raw) > WAITING_MOST


WAITING_MOST = 200          # waiting payments looked at for one row; with more, the row is unclear (a person picks)
COMBINED_MOST = 12          # waiting payments to one contact searched for a combined row, up to (2^12 subsets)
COMBINED_SETS = 4           # sets found before the search stops (more than one is a tie for the name to break)
MATCH_MOST = 100            # waiting payments one feed_match links


def _combined(waiting: Sequence[Any], amount: int) -> tuple[list[list[Any]], bool]:
    """Sets of two or more waiting payments to a single contact that sum to the row's amount, and whether the search
    left some out (a contact with more than COMBINED_MOST candidates of at most the amount, or COMBINED_SETS sets found
    before it finished): then what it found may not be the only answer, so the row is unclear."""
    by_contact: dict[str, list[Any]] = {}
    for s in waiting:
        if 0 < int(s["amount"]) <= amount:
            by_contact.setdefault(str(s["contact_id"] or ""), []).append(s)
    if any(len(g) > COMBINED_MOST for g in by_contact.values()):
        return [], True
    found: list[list[Any]] = []
    for group in by_contact.values():
        for mask in range(1, 1 << len(group)):
            if mask & (mask - 1) and sum(int(s["amount"]) for i, s in enumerate(group) if mask >> i & 1) == amount:
                found.append([s for i, s in enumerate(group) if mask >> i & 1])
                if len(found) >= COMBINED_SETS:
                    return found, True      # stopped early: other sets were never looked at
    return found, False


def _post_combined(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn, group: Sequence[Any],
                   fees: Sequence[dict[str, object]] = ()) -> str:
    """One row paying the documents marked paid from its account (several, or one with the fees it carries): their
    entries are reversed and the row posts one entry for them all, with `fees`, and the bank moves once. Each settlement
    now comes from the row."""
    from .banking import mark_posted, open_day, post_row
    gl = ctx.accounts.get(str(money["account_id"] or ""))
    if gl is None:
        raise invalid("that row's money account has no account in the books")
    kind = str(group[0]["to_kind"])
    ctl = control(ctx, kind)
    fid = str(row["id"])
    entries = [e for e in (SL.entry_row(ctx.c, str(s["entry_id"])) for s in group) if e is not None]
    # The reversals and the row's one entry share a day (the latest open one of them all), so no period between them
    # shows the bank moving twice; `banking._unchanged` keeps that day when the row comes again.
    day = max([open_day(ctx, t.txn_date), *(open_day(ctx, str(e["entry_date"])) for e in entries)])
    for entry in entries:
        posting.reverse(ctx, str(entry["id"]), day=day, memo="its bank row came", by=SYSTEM)
    lines: list[dict[str, object]] = [{"account_id": ctl.id, "amount": SIGN[kind] * int(s["amount"]), "tags": {},
                                       "contact_id": str(s["contact_id"] or "")} for s in group]
    entry_id = post_row(ctx, fid, t, [*lines, *fees], gl.id, [], day=day)
    mark_posted(ctx, [fid], entry_id)
    for s in group:
        SAP.update(ctx.c, "fin_settlements", str(s["id"]), {"from_kind": "feed", "from_id": fid, "entry_id": entry_id})
    return entry_id


EXPECTED_MOST = 20          # waiting payments listed for one row


def expected_for(ctx: Ctx, who: str, row: Any) -> tuple[list[Any], bool]:
    """The payments marked paid that a settled, unposted row may be the bank side of, for a person to pick
    (feed_match): on its money account, in its direction, of at most its amount, marked within MATCH_DAYS of it, on
    documents the viewer sees. Capped; True when there were more."""
    if row["entry_id"] or int(row["pending"]) or str(row["status"]) == "ignored" or not int(row["amount"]):
        return [], False
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    if money is None:
        return [], False
    found, cut = _candidates(ctx, who, row, money)
    return found[:EXPECTED_MOST], cut or len(found) > EXPECTED_MOST


def _candidates(ctx: Ctx, who: str, row: Any, money: Any, ids: Sequence[str] = ()) -> tuple[list[Any], bool]:
    """`expected_for` uncapped (only `ids` when given), and whether more waited than were looked at. A provider's fee
    comes out of what the row carries, so the payments may add up to its gross."""
    from .banking import provider_fee
    kind = "bill" if int(row["amount"]) < 0 else "invoice"
    start, end = _window(str(row["txn_date"]))
    gross = abs(int(row["amount"])) + provider_fee(row, money)
    waiting, cut = _waiting(ctx, kind, money, 0, start, end, ids)
    return [s for s in waiting if int(s["amount"]) <= gross
            and _sees_doc(ctx, who, kind, _doc(ctx, kind, str(s["to_id"])))], cut


def feed_match(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Link a settled bank row to the payments marked paid it is the bank side of (the ones the automatic match could
    not tell apart): exactly as that match does, so the bank moves once and nothing new is expensed. The payments'
    amounts add up to the row's gross (its amount and any provider fee); the documents are ones the asker sees and may record payments on."""
    from .banking import provider_fee, provider_fee_line, seen
    V.only(p, ("feed_id", "settlement_ids"))
    row = SK.get(ctx.c, "fin_feed_txns", V.text(p, "feed_id", 80))
    if row is None or not seen(ctx, who, row):
        raise FinanceError(NOT_FOUND, "no such feed row in your view")
    if int(row["pending"]) or str(row["status"]) == "ignored" or row["entry_id"]:
        raise FinanceError(CONFLICT, "only a settled row not yet in the books is linked; re-categorize a posted one")
    kind = "bill" if int(row["amount"]) < 0 else "invoice"
    need = R.AP if kind == "bill" else R.AR
    if not R.has(ctx.org, who, need, ctx.rights(who)):
        raise denied(f"linking a row to {kind} payments needs {need}")
    ids = list(dict.fromkeys(V.texts(p, "settlement_ids", MATCH_MOST)))
    if not ids:
        raise invalid("name the waiting payments (settlement_ids) this row pays")
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    # Checked by id, so a row with more candidates than the automatic search looks at can still be linked.
    waiting = {str(s["id"]): s for s in _candidates(ctx, who, row, money, ids)[0]} if money is not None else {}
    group = [waiting[i] for i in ids if i in waiting]
    if len(group) != len(ids):
        raise FinanceError(NOT_FOUND, "no such payment waiting for this row in your view")
    if len({str(s["contact_id"] or "") for s in group}) > 1:
        raise invalid("one row pays one contact's payments")
    gross = abs(int(row["amount"])) + provider_fee(row, money)
    total = sum(int(s["amount"]) for s in group)
    if total != gross:
        raise invalid(f"those payments add up to {total}, the row to {gross}")
    docs = [str(s["to_id"]) for s in group]

    def apply(aid: str) -> Result:
        fresh = SK.get(ctx.c, "fin_feed_txns", str(row["id"]))
        fee = provider_fee_line(ctx, fresh, money)
        entry = _link(ctx, fresh, money, _row_txn(fresh), group, [] if fee is None else [fee])
        return {"feed_id": str(row["id"]), "entry_id": entry, "settlement_ids": ids, f"{kind}_ids": docs}
    names = ", ".join(f"{kind} {SAP.get(ctx.c, SAP.DOCS[kind][0], d)['number'] or d}" for d in docs)
    return Plan(K.default_gate(ctx.org, who, "feed_match"),
                f"Link the {_money(abs(int(row['amount'])))} bank row to the payment(s) marked on {names}"[:300], apply,
                facts={"amount": abs(int(row["amount"])), "counterparty": str(row["counterparty"])})


def _arrived(ctx: Ctx, kind: str, doc: Any, money: Any, amount: int, day: str) -> Any:
    """The bank row of this payment when it is in already (marked paid after the bank showed it): the one settled row
    on the money account of exactly this amount, within MATCH_DAYS of `day`, named like the contact, not a Finance
    payment, a transfer or already applied to a document. An unbooked row that other marked payments may be the bank
    side of is left to a person (feed_match); this payment then waits like any other from the account."""
    start, end = _window(day)
    signed = -amount if kind == "bill" else amount
    found = [r for r in SK.transfer_candidates(ctx.c, [str(money["id"])], signed, start, end)
             if not r["request_id"] and not r["card_id"] and str(r["kind"] or "") not in F.TRANSFER_KINDS
             and _named(str(r["counterparty"]), str(doc["contact_name"] or ""))
             and not SAP.settled_from(ctx.c, "feed", str(r["id"]))
             and not (r["entry_id"] and len(SK.rows_of_entry(ctx.c, str(r["entry_id"]))) > 1)
             and not may_wait(ctx, r, money)]
    return found[0] if len(found) == 1 else None


def clearing(ctx: Ctx, who: str) -> L.Account:
    """Where a payment made outside every connected account goes: an asset line no feed row ever posts to."""
    found = [a for a in ctx.accounts.values() if a.active and a.subtype == CLEARING[3]]
    if found:
        return min(found, key=lambda a: a.code)
    from .banking import new_account
    code, name, type_, subtype = CLEARING
    return new_account(ctx, code, name, type_, subtype, who)


def feed_paid(ctx: Ctx, row: Any, kind: str, doc: Any, entry_id: str) -> None:
    """A feed row the books posted straight to AP or AR (match_feed) settles its document. A Stripe row never gets
    here: its sale settles on its gross through stripe_post, so one that did is refused, not settled at its net."""
    money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
    if money is not None and str(money["kind"]) == "stripe":
        raise invalid("a Stripe sale settles through its own posting, on its gross")
    settle(ctx, kind, str(doc["id"]), amount=abs(int(row["amount"])), day=str(row["txn_date"]), from_kind="feed",
           from_id=str(row["id"]), entry_id=entry_id)


def _spent(ctx: Ctx, row: Any, ctl: str, sign: int) -> list[L.Line]:
    """What a posted row's entry (and its reclassifications) still has on lines other than the money line and the
    control account, netted per line and tags, in the direction a document would take it from."""
    ids = _row_entries(ctx, row)
    net: dict[tuple[str, str, str], tuple[L.Line, int]] = {}
    for eid in ids:
        entry = SL.entry_row(ctx.c, eid)
        if entry is None or str(entry["status"]) != "posted":
            continue
        for _, x in SL.entry_lines(ctx.c, eid):
            if x.role == L.MONEY_ROLE or x.account_id == ctl:
                continue
            key = (x.account_id, L.tagset(x.tags), x.contact_id)
            prior = net.get(key)
            net[key] = (x, (prior[1] if prior else 0) + x.amount)
    return [replace(x, amount=total) for x, total in net.values() if sign * total > 0]


def _row_entries(ctx: Ctx, row: Any) -> list[str]:
    """A posted row's entry and its live reclassifications."""
    return [str(row["entry_id"])] + [str(e["id"]) for e in SL.posted_by_source_prefix(
        ctx.c, ctx.book.id, "bank", f"{row['id']}:{F.RECLASS}") if str(e["status"]) == "posted" and not e["reverses_id"]]


def _row_txn(row: Any) -> F.FeedTxn:
    """A stored settled feed row as the feed transaction it was."""
    return F.FeedTxn(provider_id=str(row["provider_id"]), account_id="", amount=int(row["amount"]),
                     txn_date=str(row["txn_date"]), status=F.POSTED, kind=str(row["kind"]),
                     counterparty=str(row["counterparty"]), description=str(row["description"]),
                     card_id=str(row["card_id"] or ""))


def _auto_default(ctx: Ctx, row: Any) -> set[str]:
    """The automatic default line a posted row sits on, if it does: Other expenses, when ingest posted it there and
    asked for a category (needs_category) and no categorize action posted its entry since. Empty otherwise."""
    from .banking import CATEGORY_EVENT, OTHER, by_code
    other = by_code(ctx, OTHER)
    if other is None or not SAP.raised(ctx.c, CATEGORY_EVENT, str(row["id"])) or \
            SAP.categorized_into(ctx.c, str(row["entry_id"])):
        return set()
    return {other.id}


def _unapplied(ctx: Ctx, row: Any, ctl: str, sign: int, contact: str) -> int:
    """What a posted row put in AP or AR for this contact that no document took yet (a receipt applied whole to AR
    for its first invoice keeps the rest there for the next one)."""
    put = 0
    for eid in _row_entries(ctx, row):
        entry = SL.entry_row(ctx.c, eid)
        if entry is None or str(entry["status"]) != "posted":
            continue
        put += sum(x.amount for _, x in SL.entry_lines(ctx.c, eid) if x.account_id == ctl and x.contact_id == contact)
    taken = 0
    for st in SAP.settlements_from(ctx.c, "feed", str(row["id"])):
        to = SAP.get(ctx.c, SAP.DOCS[str(st["to_kind"])][0], str(st["to_id"]))
        if to is not None and str(to[f"{CONTACT_OF[str(st['to_kind'])]}_id"]) == contact:
            taken += int(st["amount"])
    return max(0, sign * put - taken)


def _from_feed(ctx: Ctx, kind: str, doc: Any, row: Any, amount: int) -> str:
    """Post what a feed row paying a document needs. A row not yet in the books posts its gross to AP or AR, with any
    provider fee as its own line (any amount beyond this document stays there, for the next one it pays). A posted row uses what it already put in AP or AR for
    the contact first, then moves the rest from the lines it hit to AP or AR (and whatever it left on the automatic
    default, unapplied there), in an entry beside its own (the bank line is never touched, so a reconciled row works too)."""
    from .banking import feed_line, fresh_source, mark_posted, open_day, post_row, provider_fee, provider_fee_line
    ctl = control(ctx, kind)
    contact = str(doc[f"{CONTACT_OF[kind]}_id"])
    fid = str(row["id"])
    if not row["entry_id"]:
        money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
        gl = ctx.accounts.get(str(money["account_id"] or "")) if money is not None else None
        if gl is None:
            raise invalid("that row's money account has no account in the books")
        t = _row_txn(row)
        # A card charge left unposted (it may pay one of many bills) keeps its authorization: it settles here.
        holds = [str(row["hold_id"])] if row["hold_id"] and t.amount < 0 else []
        # A provider's fee comes out of the gross before the row arrives: the document settles the gross, and the fee
        # is its own expense line, the same as an automatic match posts it.
        lines = [feed_line(ctl.id, -(t.amount + provider_fee(row, money)), {}, contact)]
        fee = provider_fee_line(ctx, row, money)
        if fee is not None:
            lines.append(fee)
        entry = post_row(ctx, fid, t, lines, gl.id, holds)
        mark_posted(ctx, [fid], entry)
        if holds:
            from .banking import card_used
            card_used(ctx, row, -t.amount, entry)
        return entry
    s = SIGN[kind]
    have = min(amount, _unapplied(ctx, row, ctl.id, s, contact))
    left = amount - have
    if not left:
        return str(row["entry_id"])     # the row already put this much in AP or AR for this contact
    # This document takes `left` from the lines the row hit (what sits on the automatic default first). Whatever else
    # sits on that default (Other expenses, posted at ingest and asked about) moves too and stays in AP or AR for the
    # contact, unapplied (§5); a line a person or a posting rule chose keeps its categorization.
    auto = _auto_default(ctx, row)
    spent = sorted(_spent(ctx, row, ctl.id, s), key=lambda y: (y.account_id not in auto, -abs(y.amount)))
    lines: list[dict[str, object]] = []
    moved = 0
    for x in spent:
        take = abs(x.amount) if x.account_id in auto else min(max(left - moved, 0), abs(x.amount))
        if take <= 0:
            continue
        lines.append({"account_id": x.account_id, "amount": -s * take, "tags": dict(x.tags),
                      "contact_id": x.contact_id})
        moved += take
    if left > moved:
        raise invalid("that row has less left to apply than the amount")
    lines.append({"account_id": ctl.id, "amount": s * moved, "contact_id": contact})
    entry = SL.entry_row(ctx.c, str(row["entry_id"]))
    day = str(entry["entry_date"]) if entry is not None else ctx.today
    done = posting.post_entry(ctx.c, {"date": open_day(ctx, day), "source": "bank",
                                      "source_id": fresh_source(ctx, "bank", f"{fid}:{F.RECLASS}"),
                                      "memo": f"Pays {kind} {doc['number'] or doc['id']}"[:200], "lines": lines},
                              at=ctx.at)
    return str(done["entry_id"])


def _pay(kind: str) -> Any:
    words = {"bill": "bill_mark_paid", "invoice": "invoice_record_payment"}[kind]

    def planner(ctx: Ctx, who: str, p: V.Payload) -> Plan:
        """Record money paid on a bill (received on an invoice), in part or in full: from a feed row (linked, never
        posting expense or income again), from a money account (a connected one's bank row links when it comes, so
        cash posts once), from another money line, or with none (a clearing account the feed never touches). More
        than is left is refused."""
        from .banking import seen
        V.only(p, (f"{kind}_id", "amount", "date", "feed_id", "money_account_id", "account_id"))
        doc = _doc(ctx, kind, V.text(p, f"{kind}_id", 80))
        _need_status(kind, doc, OPEN[kind], "only an open one is paid")
        waits = payment_waits(ctx, doc) if kind == "bill" else ""
        if waits:
            raise denied(waits)
        if kind == "bill" and SK.live_payment(ctx.c, f"bill:{doc['id']}") is not None:
            raise FinanceError(CONFLICT, "a payment for this bill is live; it settles the bill when it clears")
        left = _left(kind, doc)
        given = V.opt_amount(p)
        amount = left if given is None else given     # 0 is refused below, never read as the balance left
        if not 0 < amount <= left:
            raise invalid(f"the amount is more than 0 and at most what is left ({left})")
        if sum(k in p for k in ("feed_id", "money_account_id", "account_id")) > 1:
            raise invalid("say one of feed_id (the bank or card row), money_account_id or account_id, or none")
        row: Any = None
        account: L.Account | None = None
        money: Any = None
        if "money_account_id" in p:
            money = SK.get(ctx.c, "fin_money_accounts", V.text(p, "money_account_id", 80))
            if money is None or str(money["book_id"]) != ctx.book.id:
                raise FinanceError(NOT_FOUND, "no such money account")
            account = ctx.accounts.get(str(money["account_id"] or ""))
            if account is None:
                raise invalid("that money account has no account in the books")
        elif "account_id" in p:
            account = ctx.accounts.get(V.text(p, "account_id", 80))
            if account is None or not account.active or account.type not in ("asset", "liability") or \
                    account.id == control(ctx, kind).id:
                raise FinanceError(NOT_FOUND, "no such money account line")
            # A connected money account's own line is that account: its bank row will come.
            money = next((m for m in SK.money_accounts(ctx.c, ctx.book.id)
                          if str(m["account_id"] or "") == account.id and m["connection_id"]), None)
        if money is not None and str(money["kind"]) == "card_program":
            raise invalid("a Tico card's charge pays a document through its feed row (feed_id)")
        if account is not None and not may_write(ctx, who, account.id, {}):
            raise denied("that line is outside your view")
        day = V.opt_day(p, "date") or ctx.today
        waits = str(money["id"]) if money is not None and money["connection_id"] else ""
        if waits:
            row = _arrived(ctx, kind, doc, money, amount, day)
        if "feed_id" in p:
            row = SK.get(ctx.c, "fin_feed_txns", V.text(p, "feed_id", 80))
            if row is None or not seen(ctx, who, row):
                raise FinanceError(NOT_FOUND, "no such feed row in your view")
            if int(row["pending"]) or str(row["status"]) == "ignored":
                raise FinanceError(CONFLICT, "that row is pending or ignored")
            if (int(row["amount"]) < 0) != (kind == "bill"):
                raise invalid("a bill is paid by money out, an invoice by money in")
            if row["request_id"] and SK.payment_by_request(ctx.c, str(row["request_id"])) is not None:
                raise FinanceError(CONFLICT, "that row is a Finance payment; it settles its own bill")
            if row["entry_id"] and len(SK.rows_of_entry(ctx.c, str(row["entry_id"]))) > 1:
                raise FinanceError(CONFLICT, "that row is a transfer between your own accounts")
            from .banking import provider_fee
            row_money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
            gross = abs(int(row["amount"])) + (provider_fee(row, row_money) if row_money is not None else 0)
            free = gross - SAP.settled_from(ctx.c, "feed", str(row["id"]))
            if amount > free:
                raise invalid(f"that row has {free} left to apply")
            if row_money is not None and may_wait(ctx, row, row_money):
                raise FinanceError(CONFLICT, "that row may be a payment already marked paid that waits for its bank "
                                             "row; link it with feed_match")
            day = V.opt_day(p, "date") or str(row["txn_date"])
        elif row is not None:
            day = V.opt_day(p, "date") or str(row["txn_date"])

        def apply(aid: str) -> Result:
            from .banking import open_day
            if row is not None:
                entry = _from_feed(ctx, kind, doc, SK.get(ctx.c, "fin_feed_txns", str(row["id"])), amount)
                src, src_id = "feed", str(row["id"])
            else:
                line = account or clearing(ctx, who)
                s = SIGN[kind]
                contact = str(doc[f"{CONTACT_OF[kind]}_id"])
                done = posting.post_entry(ctx.c, {
                    "date": open_day(ctx, day), "source": kind, "source_id": f"{doc['id']}:paid:{aid}",
                    "memo": f"Pays {kind} {doc['number'] or doc['id']}"[:200],
                    "lines": [{"account_id": control(ctx, kind).id, "amount": s * amount, "contact_id": contact},
                              {"account_id": line.id, "amount": -s * amount}]}, by=who, at=ctx.at)
                entry = str(done["entry_id"])
                src, src_id = ("expected", waits) if waits else ("account", line.id)
            sid = settle(ctx, kind, str(doc["id"]), amount=amount, day=day, from_kind=src, from_id=src_id,
                         entry_id=entry, action_id=aid, by=who)
            fresh = _doc(ctx, kind, str(doc["id"]))
            return {f"{kind}_id": str(doc["id"]), "settlement_id": sid, "entry_id": entry, "from": src,
                    "feed_id": str(row["id"]) if row is not None else None,
                    "status": str(fresh["status"]), "remaining": _left(kind, fresh)}
        return Plan(K.default_gate(ctx.org, who, words),
                    f"Record {_money(amount)} {'paid on' if kind == 'bill' else 'received on'} {kind} "
                    f"{doc['number'] or doc['id']}", apply,
                    facts={"amount": amount, "counterparty": str(doc["contact_name"] or "")})
    return planner


bill_mark_paid = _pay("bill")
invoice_record_payment = _pay("invoice")


# ------------------------------------------------------------------------------------------------ attachments
ATTACH_SUBJECTS = (*AP.SUBJECTS, "unmatched")
ATTACH_RIGHT = {"entry": R.BOOK, "feed": R.BOOK, "unmatched": R.BOOK, "bill": R.AP, "invoice": R.AR}


def attach(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """File receipts and documents (Library file ids) on an entry, a feed row, a bill or an invoice the asker sees, or
    as unmatched receipts waiting for one. Filing a file to a subject takes it out of the unmatched receipts."""
    from .banking import seen
    from .tab_ap import sees_side
    V.only(p, ("subject_kind", "subject_id", "file_ids"))
    kind = V.choice(p, "subject_kind", ATTACH_SUBJECTS)
    if not R.has(ctx.org, who, ATTACH_RIGHT[kind], ctx.rights(who)):
        raise denied(f"attaching to a {kind} needs {ATTACH_RIGHT[kind]}")
    files = V.texts(p, "file_ids", 50)
    if any(len(f) > 80 for f in files):
        raise invalid("file_ids are Library file ids")
    subject = V.opt_text(p, "subject_id", 80) or ""
    view = ctx.view(who)
    if kind == "entry":
        entry = SL.entry_row(ctx.c, subject)
        lines = [x for _, x in SL.entry_lines(ctx.c, subject)] if entry is not None else []
        if entry is None or not all(x.account_id in ctx.accounts and view.sees(ctx.accounts[x.account_id], x.tags)
                                    for x in lines):
            raise FinanceError(NOT_FOUND, "no such entry in your view")
    elif kind == "feed":
        row = SK.get(ctx.c, "fin_feed_txns", subject)
        if row is None or not seen(ctx, who, row):
            raise FinanceError(NOT_FOUND, "no such feed row in your view")
    elif kind in ("bill", "invoice"):
        _doc(ctx, kind, subject)
        if not sees_side(ctx, view, kind):
            raise FinanceError(NOT_FOUND, f"no such {kind}")
    else:
        subject = ""

    def apply(aid: str) -> Result:
        made = SAP.attach(ctx.c, ctx.book.id, kind, subject, files, who, ctx.at)
        if kind != "unmatched":
            SAP.drop_unmatched(ctx.c, ctx.book.id, files)
        return {"subject_kind": kind, "subject_id": subject, "attachment_ids": made}
    return Plan(K.default_gate(ctx.org, who, "attach"), f"Attach {len(files)} file(s) to a {kind}", apply,
                facts={"subject_kind": kind})


# ------------------------------------------------------------------------------------------------ hourly
def pay_due(ctx: Ctx) -> int:
    """Request the payment of each scheduled bill whose day has come, as the teammate who scheduled it, through the
    one write path (their rights and the payment rules apply). A refusal reopens the bill and tells them."""
    from . import actions
    from .core.org import Principal
    from .payments import admin_person
    n = 0
    for bill in SAP.scheduled_due(ctx.c, ctx.book.id, ctx.today):
        sched = SK.loads(bill["schedule_json"])
        obligation = f"bill:{bill['id']}"
        if SK.live_payment(ctx.c, obligation) is not None:
            SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"pay_on": None})
            continue
        who = str(sched.get("by") or "")
        left = _left("bill", bill)
        if left <= 0:               # a bank row paid it before its day (match_feed takes scheduled bills too)
            SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"pay_on": None})
            continue
        res = actions.act(ctx.c, Principal(actor=SYSTEM, for_actor=who), "payment_create", {
            "recipient_id": str(sched.get("recipient_id") or ""), "amount": left,
            "money_account_id": str(sched.get("money_account_id") or ""), "obligation": obligation,
            "account_id": control(ctx, "bill").id,
            "memo": str(sched.get("memo") or f"Bill {bill['number']}".strip())[:140]},
            f"bill-pay-{bill['id']}-{sched.get('n') or 1}", at=ctx.at)
        done = res.status in ("committed", "needs_approval")
        # Asked for: the day stays on the bill (it shows when it pays) and the action id keeps it from being asked
        # again; refused, the bill is open again with no schedule.
        if done:
            SAP.update(ctx.c, "fin_bills", str(bill["id"]), {
                "updated_at": ctx.at, "schedule_json": SK.dump({**sched, "action_id": res.action_id,
                                                                "result": res.status})})
        else:
            _unschedule(ctx, bill, sched, res.status, res.reason, left)
            person = who if who in ctx.org.humans else admin_person(ctx)
            tico.needs_you(ctx.c, person, f"Bill {bill['number'] or bill['id']} was not paid",
                           f"The payment scheduled for bill {bill['id']} was refused: {res.reason[:300]}. Fix it and "
                           "schedule it again.")
        n += 1
    return n


RENEWAL_TASK_MIN_LEAD = 30      # days before the cancel-by day, so a vendor with short or no notice still gets time


def _renewal_task(ctx: Ctx, vendor: Any, today: date) -> None:
    """The task that puts a vendor's renewal in front of someone (plan P3). It opens the vendor's notice days, at least
    RENEWAL_TASK_MIN_LEAD, before its cancel-by day and falls due on that day, once per renewal date. It goes to the vendor's owner, else to the install
    owner; a vendor whose owner can't take work sends the install owner a Needs-you item instead."""
    renewal_on = str(vendor["renewal_on"])
    notice_by = AP.renewal_notice_by(renewal_on, int(vendor["renewal_notice_days"] or 0))
    if notice_by is None:
        return
    cancel_by = dates.day(notice_by)
    opens = cancel_by - timedelta(days=max(int(vendor["renewal_notice_days"] or 0), RENEWAL_TASK_MIN_LEAD))
    if not opens <= today <= cancel_by:
        return
    key = f"vendor-renewal-task:{vendor['id']}:{renewal_on}"
    if ctx.c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (key,)).fetchone() is not None:
        return
    install = FC.owner(ctx.c)
    name = str(vendor["name"])
    target = str(vendor["owner_id"] or "") or install
    if not target:
        return
    actor, why = FLOW.assignee(ctx.c, target)
    if why:
        _renewal_notice(ctx, key, install, f"Vendor renewal needs a teammate: {name}",
                        f"{name} renews on {renewal_on} and its notice runs out on {cancel_by.isoformat()}. Its owner "
                        f"{why}, so no one has its renewal task. Give the vendor an active owner (contact_update "
                        "owner_id) and the next pass opens the task.")
        return
    title = f"Vendor renewal: {name}"[:200]
    body = (f"{name} renews on {renewal_on}, and its notice runs out on {cancel_by.isoformat()}. Decide before then "
            "whether to renew, renegotiate or cancel.")
    try:
        task = H.task_create(ctx.c, H.KEEPER, title, body, actor, due=automation_due.due_end(cancel_by, FLOW.zone()),
                             deduplicate=False, trigger="schedule", trigger_source="vendor_renewal")
    except H.Refused as exc:
        _renewal_notice(ctx, key, install, f"Vendor renewal could not open: {name}", f"{title} was refused: {exc}")
        return
    ctx.c.execute("INSERT INTO registry_metadata(key, value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                  "value_json=excluded.value_json", (key, json.dumps(str(task["id"]))))


def _renewal_notice(ctx: Ctx, key: str, install: str, title: str, body: str) -> None:
    """Tell the install owner once per vendor and renewal date: this runs every pass, and a closed item stays closed."""
    told = f"{key}:told"
    if not install or ctx.c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (told,)).fetchone() is not None:
        return
    tico.needs_you(ctx.c, install, title[:200], body)
    ctx.c.execute("INSERT INTO registry_metadata(key, value_json) VALUES(?,'true')", (told,))


def watch(ctx: Ctx) -> int:
    """Raise each open bill's due-soon stage and each sent invoice's overdue stage, once per document per stage."""
    today = dates.day(ctx.today)
    n = 0
    for bill in SAP.open_docs(ctx.c, "bill", ctx.book.id):
        stage = AP.bill_stage(bill["due_date"], today)
        if stage:
            _event(ctx, DUE_SOON, str(bill["id"]), {"bill_id": str(bill["id"]), "stage": stage,
                                                    "due_date": bill["due_date"]}, stage)
            n += 1
    for inv in SAP.open_docs(ctx.c, "invoice", ctx.book.id):
        stage = AP.invoice_stage(inv["due_date"], today)
        if stage:
            _event(ctx, OVERDUE, str(inv["id"]), {"invoice_id": str(inv["id"]), "stage": stage,
                                                  "due_date": inv["due_date"]}, stage)
            n += 1
    for vendor in SAP.renewals_due(ctx.c, ctx.book.id):
        # Counted to the cancel-by day, so the task lands before the vendor's notice runs out (P3).
        _renewal_task(ctx, vendor, today)
        notice_by = AP.renewal_notice_by(vendor["renewal_on"], vendor["renewal_notice_days"])
        stage = AP.renewal_stage(notice_by, today)
        if stage:
            _event(ctx, RENEWAL_DUE, str(vendor["id"]), {"contact_id": str(vendor["id"]), "stage": stage,
                                                         "renewal_on": vendor["renewal_on"], "notice_by": notice_by},
                   f"{vendor['renewal_on']}:{stage}")
            n += 1
    return n


# The rules the mirror's document planners share with the native ones (mirror.py takes the same checks, then queues the
# write to the provider instead of posting here).
doc_of, contact_of, left_of, need_status = _doc, contact_for, _left, _need_status
sees_doc = _sees_doc
next_number, money_of = _next_number, _money


PLANNERS = {"contact_create": contact_create, "contact_update": contact_update, "bill_create": bill_create,
            "bill_update": bill_update, "bill_approve": bill_approve, "bill_schedule": bill_schedule,
            "bill_mark_paid": bill_mark_paid, "bill_void": bill_void, "invoice_create": invoice_create,
            "invoice_send": invoice_send, "invoice_record_payment": invoice_record_payment,
            "invoice_write_off": invoice_write_off, "invoice_void": invoice_void, "attach": attach,
            "feed_match": feed_match}

__all__ = ["PLANNERS", "settle", "unsettle", "feed_gone", "payment_settled", "payment_ended", "match_feed",
           "feed_paid", "pay_due", "watch", "control", "document", "expected_match", "ASK", "bill_payment",
           "retagged", "expected_for", "bill_budget"]
