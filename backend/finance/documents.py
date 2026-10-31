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
- A write-off posts Dr the write-off line / Cr AR with basis `accrual`: the cash view never had that income.
- Stage events: finance.bill.received once per bill, finance.bill.due_soon and finance.invoice.overdue once per
  document per stage (the hourly `watch`)."""
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from . import budgeting, posting
from .context import Ctx
from .core import ap as AP
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import rights as R
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .core.org import SYSTEM
from .handlers import Plan, Result, may_write
from .store import ap as SAP
from .store import banking as SK
from .store import budgets as SB
from .store import ledger as SL
from .store import tico

RECEIVED, DUE_SOON, OVERDUE = "finance.bill.received", "finance.bill.due_soon", "finance.invoice.overdue"
CONTROL = {"bill": ("2000", "payable", "accounts payable"), "invoice": ("1200", "receivable", "accounts receivable")}
CONTACT_OF = {"bill": "vendor", "invoice": "customer"}
SIGN = {"bill": 1, "invoice": -1}       # a bill's lines are debits, an invoice's credits
OPEN = {"bill": AP.OPEN_BILL, "invoice": AP.OPEN_INVOICE}
LINE_TYPES = {"bill": ("expense", "asset"), "invoice": ("income",)}
NAME_MIN = 4                            # a counterparty matches a contact's name on at least this many characters


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


def _contact(ctx: Ctx, contact_id: str, kind: str) -> Any:
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


def _scope_tags(tags: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in tags.items() if k in ("team", "teammate")}


def _money(amount: int) -> str:
    return f"{amount / 100:.2f}"


# ------------------------------------------------------------------------------------------------ contacts
CONTACT_FIELDS = ("name", "kind", "emails", "default_account_id", "is_1099", "recipient_id", "active")
TAX_FIELDS = ("tax_id_ref", "tax_id_fp", "tax_id_last4")


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
    return out


def contact_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A vendor or customer. Its tax id was sealed into the vault before this runs (payments.strip_details): the
    payload carries the vault ref and the last 4 only."""
    V.only(p, (*CONTACT_FIELDS, *TAX_FIELDS))
    f = _contact_fields(ctx, p)
    _need_contact_rights(ctx, who, str(f["kind"]))
    if "tax_id_last4" in p and not f.get("tax_id_ref") and f.get("tax_id_last4"):
        raise invalid("the tax id was not sealed")

    def apply(aid: str) -> Result:
        cid = SAP.insert(ctx.c, "fin_contacts", {"book_id": ctx.book.id, "created_at": ctx.at, "created_by": who,
                                                 "updated_at": ctx.at, **f})
        return {"contact_id": cid}
    return Plan(K.default_gate(ctx.org, who, "contact_create"), f"Add {f['kind']} {f['name']}", apply,
                facts={"contact.kind": str(f["kind"]), "counterparty": str(f["name"])})


def contact_update(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("contact_id", *CONTACT_FIELDS, *TAX_FIELDS))
    old = SAP.get(ctx.c, "fin_contacts", V.text(p, "contact_id", 80))
    if old is None or str(old["book_id"]) != ctx.book.id:
        raise FinanceError(NOT_FOUND, "no such contact")
    _need_contact_rights(ctx, who, str(old["kind"]))
    f = _contact_fields(ctx, p, old)
    if "kind" in f:
        _need_contact_rights(ctx, who, str(f["kind"]))
    if not f:
        raise invalid("say what changes")

    def apply(aid: str) -> Result:
        if "tax_id_last4" in f and old["tax_id_ref"]:
            tico.drop_credential(ctx.c, str(old["tax_id_ref"]))     # the old number goes with its replacement
        SAP.update(ctx.c, "fin_contacts", str(old["id"]), {**f, "updated_at": ctx.at})
        return {"contact_id": str(old["id"])}
    return Plan(K.default_gate(ctx.org, who, "contact_update"), f"Change contact {old['name']}", apply,
                facts={"contact.kind": str(f.get("kind") or old["kind"]), "counterparty": str(old["name"])})


# ------------------------------------------------------------------------------------------------ document lines
def _resolve(ctx: Ctx, who: str, kind: str, contact: Any, lines: Sequence[AP.DocLine]) -> tuple[list[AP.DocLine], int]:
    """Each line's account: the one given; for a bill line without one, the first posting rule that matches, else the
    vendor's default line, else Other expenses (counted as asked, for a person to fix before entering it). Every line
    is checked against the asker's view."""
    from .posting_rules import first_for
    out: list[AP.DocLine] = []
    asked = 0
    for x in lines:
        if x.account_id:
            out.append(x)
            continue
        if kind != "bill":
            raise invalid("an invoice line names its income account")
        rule = first_for(ctx, {"kind": "bill", "amount": x.amount, "counterparty": str(contact["name"]),
                               "description": x.description, "contact.id": str(contact["id"]), "direction": "out"})
        if rule is not None and rule.then.outcome == "post":
            out += [AP.DocLine(account, amount, x.description, {**x.tags, **tags})
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
        if account is None or not account.active or account.id == ctl or account.type not in LINE_TYPES[kind]:
            raise FinanceError(NOT_FOUND, f"no such {'/'.join(LINE_TYPES[kind])} account for a {kind} line")
        tags = posting.with_team(ctx, x.tags)
        if not may_write(ctx, who, account.id, tags):
            raise denied("a line's account or tags are outside your view")
        checked.append(replace(x, tags=tags))
    return checked, asked


def _line_rows(lines: Sequence[AP.DocLine], who: str, at: str) -> list[dict[str, object]]:
    return [{"account_id": x.account_id, "amount": x.amount, "description": x.description,
             "tags_json": SK.dump(dict(x.tags)), "created_at": at, "created_by": who} for x in lines]


def doc_lines_of(ctx: Ctx, kind: str, doc_id: str) -> list[AP.DocLine]:
    return [AP.DocLine(str(r["account_id"]), int(r["amount"]), str(r["description"]),
                       cast(dict[str, str], SK.loads(r["tags_json"])))
            for r in SAP.lines(ctx.c, kind, [doc_id]).get(doc_id, [])]


def _hold(ctx: Ctx, bill_id: str, vendor_id: str, lines: Sequence[AP.DocLine], who: str) -> list[str]:
    """Each expense line of a draft bill holds budget until it is entered. A budget at its limit holds nothing: the bill
    is owed whatever the budget says, so it is recorded and the crossing is raised (budgeting.place)."""
    refused: list[str] = []
    for n, x in enumerate(lines):
        account = ctx.accounts[x.account_id]
        if account.type != "expense":
            continue
        d = budgeting.place(ctx, budget_scope={"account_id": account.id, **_scope_tags(x.tags), "vendor": vendor_id},
                            amount=x.amount, currency=ctx.book.base_currency, source="bill",
                            source_id=f"{bill_id}:{n}", expires_at=None, by=who)
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


def _facts(kind: str, contact: Any, lines: Sequence[AP.DocLine], ctx: Ctx, **more: object) -> Result:
    codes = sorted({ctx.accounts[x.account_id].code for x in lines if x.account_id in ctx.accounts})
    tags: dict[str, object] = {}
    for x in lines:
        for k, v in x.tags.items():
            tags.setdefault(f"tags.{k}", v)
    return {"amount": sum(x.amount for x in lines), "counterparty": str(contact["name"]),
            f"{CONTACT_OF[kind]}.id": str(contact["id"]), "accounts": codes, **tags, **more}


# ------------------------------------------------------------------------------------------------ bills
def bill_create(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A bill as a draft: its expense lines hold budget now; `bill_approve` enters it."""
    V.only(p, ("vendor_id", "number", "bill_date", "due_date", "terms_days", "memo", "lines", "attachments", "origin"))
    vendor = _contact(ctx, V.text(p, "vendor_id", 80), "bill")
    number = V.opt_text(p, "number", 80) or ""
    if number and SAP.number_taken(ctx.c, "bill", str(vendor["id"]), number):
        raise FinanceError(CONFLICT, f"{vendor['name']} already has a bill {number}")
    day = V.day(p, "bill_date")
    terms = V.integer(p, "terms_days", 0, AP.MAX_TERMS) if p.get("terms_days") is not None else None
    due = AP.due_date(day, V.opt_day(p, "due_date"), terms)
    lines, asked = _resolve(ctx, who, "bill", vendor, AP.doc_lines(p.get("lines")))
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
        refused = _hold(ctx, bid, str(vendor["id"]), lines, who)
        _event(ctx, RECEIVED, bid, {"bill_id": bid, "origin": origin})
        out: Result = {"bill_id": bid, "status": "draft", "total": total}
        if asked:
            out["uncategorized_lines"] = asked
        if refused:
            out["not_held"] = refused
        return out
    return Plan(K.default_gate(ctx.org, who, "bill_create"),
                f"Record a {_money(total)} {ctx.book.base_currency} bill from {vendor['name']}", apply,
                facts=_facts("bill", vendor, lines, ctx, origin=origin))


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
    if not draft and any(k in p for k in ("vendor_id", "bill_date", "lines")):
        raise FinanceError(CONFLICT, "an entered bill keeps its vendor, date and lines; void it and record it again")
    vendor = _contact(ctx, V.text(p, "vendor_id", 80), "bill") if "vendor_id" in p else \
        _contact(ctx, str(bill["vendor_id"]), "bill")
    old_lines = doc_lines_of(ctx, "bill", str(bill["id"]))
    lines, asked = _resolve(ctx, who, "bill", vendor, AP.doc_lines(p.get("lines"))) if "lines" in p else (old_lines, 0)
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
            refused = _hold(ctx, str(bill["id"]), str(vendor["id"]), lines, who)
            if refused:
                out["not_held"] = refused
        if files:
            SAP.attach(ctx.c, ctx.book.id, "bill", str(bill["id"]), files, who, ctx.at)
            SAP.drop_unmatched(ctx.c, ctx.book.id, files)
        if asked:
            out["uncategorized_lines"] = asked
        return out
    return Plan(K.default_gate(ctx.org, who, "bill_update"), f"Change bill {bill['number'] or bill['id']}", apply,
                facts=_facts("bill", vendor, lines, ctx))


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
    vendor = _contact(ctx, str(bill["vendor_id"]), "bill")

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
                facts=_facts("bill", vendor, lines, ctx))


def bill_schedule(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Pay an entered bill on a day: on `pay_on` the hourly job requests a payment_create for what is left, as the
    teammate who scheduled it (their rights and the payment rules apply then), one live payment per bill."""
    V.only(p, ("bill_id", "pay_on", "money_account_id", "recipient_id", "memo"))
    bill = _doc(ctx, "bill", V.text(p, "bill_id", 80))
    _need_status("bill", bill, ("open", "scheduled"), "only an entered, unpaid bill is scheduled")
    if SK.live_payment(ctx.c, f"bill:{bill['id']}") is not None:
        raise FinanceError(CONFLICT, "a payment for this bill is already live")
    if not R.has(ctx.org, who, R.PAY, ctx.rights(who)):
        raise denied("scheduling a payment needs finance.pay")
    vendor = _contact(ctx, str(bill["vendor_id"]), "bill")
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
        SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"status": "scheduled", "pay_on": pay_on,
                                                         "schedule_json": SK.dump(sched), "updated_at": ctx.at})
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
        _unhold(ctx, str(bill["id"]), len(doc_lines_of(ctx, "bill", str(bill["id"]))))
        if bill["entry_id"]:
            entry = SL.entry_row(ctx.c, str(bill["entry_id"]))
            if entry is not None and str(entry["status"]) == "posted":
                posting.reverse(ctx, str(bill["entry_id"]), day=open_day(ctx, ctx.today),
                                memo=f"Void bill {bill['number'] or bill['id']}: {reason}"[:200], by=who,
                                action_id=aid)
        SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"status": "void", "pay_on": None, "voided_at": ctx.at,
                                                         "updated_at": ctx.at})
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
    customer = _contact(ctx, V.text(p, "customer_id", 80), "invoice")
    number = V.opt_text(p, "number", 80) or _next_number(ctx, str(customer["id"]))
    if SAP.number_taken(ctx.c, "invoice", str(customer["id"]), number):
        raise FinanceError(CONFLICT, f"invoice {number} exists")
    day = V.day(p, "invoice_date")
    terms = V.integer(p, "terms_days", 0, AP.MAX_TERMS) if p.get("terms_days") is not None else None
    due = AP.due_date(day, V.opt_day(p, "due_date"), terms)
    lines, _ = _resolve(ctx, who, "invoice", customer, AP.doc_lines(p.get("lines")))
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
                facts=_facts("invoice", customer, lines, ctx))


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
    customer = _contact(ctx, str(inv["customer_id"]), "invoice")

    def apply(aid: str) -> Result:
        entry = _post_doc(ctx, "invoice", inv, lines, str(inv["invoice_date"]), who)
        if SAP.update(ctx.c, "fin_invoices", str(inv["id"]), {"status": "sent", "entry_id": entry, "sent_at": ctx.at,
                                                              "updated_at": ctx.at}, "status='draft'") != 1:
            raise FinanceError(CONFLICT, "that invoice moved on")
        return {"invoice_id": str(inv["id"]), "status": "sent", "entry_id": entry, "delivery": "caller",
                "document": document(ctx, "invoice", inv)}
    return Plan(K.default_gate(ctx.org, who, "invoice_send"),
                f"Send invoice {inv['number']} for {_money(int(inv['total']))} to {customer['name']}", apply,
                facts=_facts("invoice", customer, lines, ctx))


def invoice_write_off(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Write off what a customer will not pay: Dr the write-off line (default Other expenses) / Cr AR, accrual basis
    only (the cash view never had that income). The invoice is paid once nothing is left."""
    from .banking import OTHER, by_code, open_day
    V.only(p, ("invoice_id", "amount", "account_id", "date", "memo"))
    inv = _doc(ctx, "invoice", V.text(p, "invoice_id", 80))
    _need_status("invoice", inv, OPEN["invoice"], "only a sent invoice is written off")
    left = _left("invoice", inv)
    amount = V.opt_amount(p) or left
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
           action_id: str = "", by: str = SYSTEM) -> str:
    """Link money to a document: its paid amount and status move, and the cash view takes the lines' share of it on
    `day`. An invoice is paid once received plus written off covers it."""
    doc = _doc(ctx, kind, doc_id)
    before = int(doc["amount_paid"])
    after = before + amount
    _cash(ctx, kind, doc, before, after, day)
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


def _cash(ctx: Ctx, kind: str, doc: Any, before: int, after: int, day: str) -> None:
    lines = doc_lines_of(ctx, kind, str(doc["id"]))
    contact = str(doc[f"{CONTACT_OF[kind]}_id"])
    for x, share in zip(lines, AP.shares([x.amount for x in lines], before, after), strict=True):
        account = ctx.accounts.get(x.account_id)
        if share and account is not None and account.type in L.PNL_TYPES:
            SAP.cash_roll(ctx.c, ctx.book.id, day, x.account_id, posting.with_team(ctx, x.tags), contact,
                          SIGN[kind] * share)


def unsettle(ctx: Ctx, s: Any, by: str = SYSTEM) -> None:
    """Undo one settlement (its money left, or its feed row changed): the document owes it again and the cash view
    gives the share back on the day it took it (or today, when that month is closed)."""
    from .banking import open_day
    kind = str(s["to_kind"])
    doc = _doc(ctx, kind, str(s["to_id"]))
    before = int(doc["amount_paid"])
    after = before - int(s["amount"])
    if SAP.update(ctx.c, "fin_settlements", str(s["id"]), {"voided_at": ctx.at, "voided_by": by},
                  "voided_at IS NULL") != 1:
        return
    _cash(ctx, kind, doc, before, after, open_day(ctx, str(s["settled_on"])))
    status = _status(kind, doc, after)
    if kind == "bill" and status == "scheduled" and SK.live_payment(ctx.c, f"bill:{doc['id']}") is None:
        status = "open"
    SAP.update(ctx.c, SAP.DOCS[kind][0], str(doc["id"]), {"amount_paid": after, "status": status,
                                                          "pay_on": doc["pay_on"] if status == "scheduled" else None,
                                                          "updated_at": ctx.at}
               if kind == "bill" else {"amount_paid": after, "status": status, "updated_at": ctx.at})


def feed_gone(ctx: Ctx, feed_id: str) -> None:
    """A feed row unposted (the bank changed or cancelled it, or it was re-categorized): what it paid is owed again."""
    for s in SAP.settlements_from(ctx.c, "feed", feed_id):
        unsettle(ctx, s)


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
    amount = min(int(pay["amount"]), _left("bill", bill))
    if amount > 0:
        settle(ctx, "bill", str(bill["id"]), amount=amount, day=str(row["txn_date"]), from_kind="payment",
               from_id=str(pay["id"]), entry_id=str(row["entry_id"] or ""), action_id=str(pay["action_id"] or ""))
    SAP.update(ctx.c, "fin_bills", str(bill["id"]), {"pay_on": None})


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


# ------------------------------------------------------------------------------------------------ paying
def _same_name(counterparty: str, name: str) -> bool:
    a = "".join(ch for ch in counterparty.lower() if ch.isalnum())
    b = "".join(ch for ch in name.lower() if ch.isalnum())
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= NAME_MIN and (a in b or b in a)))


def match_feed(ctx: Ctx, row: Any, t: F.FeedTxn) -> tuple[str, Any] | None:
    """The one open document a new settled feed row pays: a debit and an open bill (a credit and a sent invoice) that
    still owes exactly the row's amount, from a contact whose name the counterparty carries. None when there is none
    or more than one (a person picks)."""
    kind = "bill" if t.amount < 0 else "invoice"
    found = [d for d in SAP.open_owing(ctx.c, kind, ctx.book.id, abs(t.amount))
             if _same_name(t.counterparty, str(d["contact_name"] or ""))]
    return (kind, found[0]) if len(found) == 1 else None


def feed_paid(ctx: Ctx, row: Any, kind: str, doc: Any, entry_id: str) -> None:
    """A feed row the books posted straight to AP or AR (match_feed) settles its document."""
    settle(ctx, kind, str(doc["id"]), amount=abs(int(row["amount"])), day=str(row["txn_date"]), from_kind="feed",
           from_id=str(row["id"]), entry_id=entry_id)


def _spent(ctx: Ctx, row: Any, ctl: str, sign: int) -> list[L.Line]:
    """What a posted row's entry (and its reclassifications) still has on lines other than the money line and the
    control account, netted per line and tags, in the direction a document would take it from."""
    ids = [str(row["entry_id"])] + [str(e["id"]) for e in SL.posted_by_source_prefix(
        ctx.c, ctx.book.id, "bank", f"{row['id']}:{F.RECLASS}") if str(e["status"]) == "posted" and not e["reverses_id"]]
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


def _from_feed(ctx: Ctx, kind: str, doc: Any, row: Any, amount: int) -> str:
    """Post what a feed row paying a document needs. A row not yet in the books posts whole to AP or AR (any amount
    beyond this document stays there, for the next one it pays). A posted row moves `amount` from the lines it hit to
    AP or AR, in an entry beside its own (the bank line is never touched, so a reconciled row works too)."""
    from .banking import feed_line, fresh_source, mark_posted, open_day, post_row
    ctl = control(ctx, kind)
    contact = str(doc[f"{CONTACT_OF[kind]}_id"])
    fid = str(row["id"])
    if not row["entry_id"]:
        money = SK.get(ctx.c, "fin_money_accounts", str(row["money_account_id"]))
        gl = ctx.accounts.get(str(money["account_id"] or "")) if money is not None else None
        if gl is None:
            raise invalid("that row's money account has no account in the books")
        t = F.FeedTxn(provider_id=str(row["provider_id"]), account_id="", amount=int(row["amount"]),
                      txn_date=str(row["txn_date"]), status=F.POSTED, kind=str(row["kind"]),
                      counterparty=str(row["counterparty"]), description=str(row["description"]),
                      card_id=str(row["card_id"] or ""))
        entry = post_row(ctx, fid, t, [feed_line(ctl.id, -t.amount, {}, contact)], gl.id, [])
        mark_posted(ctx, [fid], entry)
        return entry
    s = SIGN[kind]
    left = amount
    lines: list[dict[str, object]] = []
    for x in sorted(_spent(ctx, row, ctl.id, s), key=lambda y: -abs(y.amount)):
        take = min(left, abs(x.amount))
        if take <= 0:
            break
        lines.append({"account_id": x.account_id, "amount": -s * take, "tags": dict(x.tags),
                      "contact_id": x.contact_id})
        left -= take
    if left:
        raise invalid("that row has less left to apply than the amount")
    lines.append({"account_id": ctl.id, "amount": s * amount, "contact_id": contact})
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
        """Record money paid on a bill (received on an invoice), in part or in full, from a feed row (linked, never
        posting expense or income again) or from a money line with no feed in Finance. More than is left is refused."""
        from .banking import seen
        V.only(p, (f"{kind}_id", "amount", "date", "feed_id", "account_id"))
        doc = _doc(ctx, kind, V.text(p, f"{kind}_id", 80))
        _need_status(kind, doc, OPEN[kind], "only an open one is paid")
        if kind == "bill" and SK.live_payment(ctx.c, f"bill:{doc['id']}") is not None:
            raise FinanceError(CONFLICT, "a payment for this bill is live; it settles the bill when it clears")
        left = _left(kind, doc)
        amount = V.opt_amount(p) or left
        if not 0 < amount <= left:
            raise invalid(f"the amount is more than 0 and at most what is left ({left})")
        if ("feed_id" in p) == ("account_id" in p):
            raise invalid("say feed_id (the bank or card row) or account_id (the money line)")
        row: Any = None
        account: L.Account | None = None
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
            free = abs(int(row["amount"])) - SAP.settled_from(ctx.c, "feed", str(row["id"]))
            if amount > free:
                raise invalid(f"that row has {free} left to apply")
            day = V.opt_day(p, "date") or str(row["txn_date"])
        else:
            account = ctx.accounts.get(V.text(p, "account_id", 80))
            if account is None or not account.active or account.type not in ("asset", "liability") or \
                    account.id == control(ctx, kind).id:
                raise FinanceError(NOT_FOUND, "no such money account line")
            if not may_write(ctx, who, account.id, {}):
                raise denied("that line is outside your view")
            day = V.opt_day(p, "date") or ctx.today

        def apply(aid: str) -> Result:
            from .banking import open_day
            if row is not None:
                entry = _from_feed(ctx, kind, doc, SK.get(ctx.c, "fin_feed_txns", str(row["id"])), amount)
                src, src_id = "feed", str(row["id"])
            else:
                assert account is not None
                s = SIGN[kind]
                contact = str(doc[f"{CONTACT_OF[kind]}_id"])
                done = posting.post_entry(ctx.c, {
                    "date": open_day(ctx, day), "source": kind, "source_id": f"{doc['id']}:paid:{aid}",
                    "memo": f"Pays {kind} {doc['number'] or doc['id']}"[:200],
                    "lines": [{"account_id": control(ctx, kind).id, "amount": s * amount, "contact_id": contact},
                              {"account_id": account.id, "amount": -s * amount}]}, by=who, at=ctx.at)
                entry, src, src_id = str(done["entry_id"]), "account", account.id
            sid = settle(ctx, kind, str(doc["id"]), amount=amount, day=day, from_kind=src, from_id=src_id,
                         entry_id=entry, action_id=aid, by=who)
            fresh = _doc(ctx, kind, str(doc["id"]))
            return {f"{kind}_id": str(doc["id"]), "settlement_id": sid, "entry_id": entry,
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
        res = actions.act(ctx.c, Principal(actor=SYSTEM, for_actor=who), "payment_create", {
            "recipient_id": str(sched.get("recipient_id") or ""), "amount": left,
            "money_account_id": str(sched.get("money_account_id") or ""), "obligation": obligation,
            "account_id": control(ctx, "bill").id,
            "memo": str(sched.get("memo") or f"Bill {bill['number']}".strip())[:140]},
            f"bill-pay-{bill['id']}-{sched.get('n') or 1}", at=ctx.at)
        done = res.status in ("committed", "needs_approval")
        SAP.update(ctx.c, "fin_bills", str(bill["id"]), {
            "pay_on": None, "status": "scheduled" if done else "open", "updated_at": ctx.at,
            "schedule_json": SK.dump({**sched, "action_id": res.action_id, "result": res.status})})
        if not done:
            person = who if who in ctx.org.humans else admin_person(ctx)
            tico.needs_you(ctx.c, person, f"Bill {bill['number'] or bill['id']} was not paid",
                           f"The payment scheduled for bill {bill['id']} was refused: {res.reason[:300]}. Fix it and "
                           "schedule it again.")
        n += 1
    return n


def watch(ctx: Ctx) -> int:
    """Raise each open bill's due-soon stage and each sent invoice's overdue stage, once per document per stage."""
    from .core import dates
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
    return n


PLANNERS = {"contact_create": contact_create, "contact_update": contact_update, "bill_create": bill_create,
            "bill_update": bill_update, "bill_approve": bill_approve, "bill_schedule": bill_schedule,
            "bill_mark_paid": bill_mark_paid, "bill_void": bill_void, "invoice_create": invoice_create,
            "invoice_send": invoice_send, "invoice_record_payment": invoice_record_payment,
            "invoice_write_off": invoice_write_off, "invoice_void": invoice_void, "attach": attach}

__all__ = ["PLANNERS", "settle", "unsettle", "feed_gone", "payment_settled", "payment_ended", "match_feed",
           "feed_paid", "pay_due", "watch", "control", "document"]
