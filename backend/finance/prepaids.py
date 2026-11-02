"""Prepaid expenses (spec §4.2, §4.4): a bill line paid up front, or over months, enters as an asset (Prepaid
expenses, 1150) and comes out as expense month by month. A month that has come due posts Dr the line's expense
account (its tags kept) / Cr prepaid, on accrual only (a noncash journal: the cash view still counts the bill as it
is paid). A month posts once; its row in fin_prepaid_postings is the record of it."""
from collections.abc import Mapping
from typing import Any, cast

from . import posting
from .context import Ctx
from .core import ap as AP
from .core import kinds as K
from .core import ledger as L
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, invalid
from .handlers import Plan, Result
from .store import banking as SK
from .store import ledger as SL
from .store import prepaids as SPP

KIND = "prepaid_amortize"
ACCOUNT = ("1150", "Prepaid expenses", "asset", "prepaid_expense")
ENTERED = ("open", "scheduled", "paid")


def find(ctx: Ctx) -> L.Account | None:
    found = [a for a in ctx.accounts.values() if a.active and a.subtype == ACCOUNT[3]]
    return min(found, key=lambda a: a.code) if found else None


def account(ctx: Ctx, who: str) -> L.Account:
    """The prepaid expenses account, made the first time a prepaid bill line is entered."""
    found = find(ctx)
    if found is not None:
        return found
    from .banking import new_account
    code, name, type_, subtype = ACCOUNT
    return new_account(ctx, code, name, type_, subtype, who)


def in_lines(payload: Mapping[str, object]) -> bool:
    """Whether a request's bill lines carry a prepaid schedule (refused where the books are a provider's)."""
    lines = payload.get("lines")
    return isinstance(lines, list) and any(isinstance(x, Mapping) and "prepaid" in x for x in lines)


def schedule(row: Any) -> list[tuple[str, int]]:
    """Each month of a prepaid line's schedule, with the expense it takes."""
    start, months = str(row["prepaid_start"]), int(row["prepaid_months"])
    return [(AP.prepaid_month(start, n), amount)
            for n, amount in enumerate(AP.prepaid_amounts(int(row["amount"]), months))]


def due_months(c: Any, ctx: Ctx) -> list[tuple[str, str]]:
    """The (bill line id, month) pairs that have come and are not expensed yet, in schedule order."""
    today = ctx.today[:7]
    out: list[tuple[str, str]] = []
    for row in SPP.lines(c, ctx.book.id):
        line_id = str(row["line_id"])
        done = SPP.posted_months(c, line_id)
        out += [(line_id, month) for month, _ in schedule(row) if month <= today and month not in done]
    return out


def amortize_plan(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """One month of a prepaid bill line: Dr its expense account / Cr prepaid expenses. Only a month that has come and is
    not expensed yet. The entry is dated the month's first day, or today when that month is closed, and its memo says
    which month it is for."""
    from .banking import open_day
    if ctx.book.mode == "mirror":
        raise FinanceError(NOT_AVAILABLE, "prepaid expenses are not available while the books are a provider's")
    V.only(p, ("bill_line_id", "month"))
    line_id = V.text(p, "bill_line_id", 80)
    month = V.month(p, "month")
    row = SPP.line(ctx.c, ctx.book.id, line_id)
    if row is None:
        raise FinanceError(NOT_FOUND, "no such prepaid bill line")
    if str(row["status"]) not in ENTERED:
        raise FinanceError(CONFLICT, "that bill is not entered")
    amount = dict(schedule(row)).get(month)
    if amount is None:
        raise invalid("that month is not in the line's prepaid schedule")
    if month > ctx.today[:7]:
        raise FinanceError(CONFLICT, f"{month} has not come yet")
    if month in SPP.posted_months(ctx.c, line_id):
        raise FinanceError(CONFLICT, f"{month} is already expensed")
    expense = ctx.accounts.get(str(row["account_id"] or ""))
    prepaid = find(ctx)
    if expense is None or not expense.active or prepaid is None:
        raise FinanceError(NOT_FOUND, "the line's expense account or the prepaid expenses account is not active")
    day = open_day(ctx, f"{month}-01")
    memo = f"Prepaid {row['vendor_name'] or 'bill'} {month}" + ("" if day == f"{month}-01" else f" (dated {month}-01)")
    contact = str(row["vendor_id"] or "")
    tags = cast(dict[str, str], SK.loads(row["tags_json"]))
    base = ctx.book.base_currency
    e = L.Entry(book_id=ctx.book.id, entry_date=day, memo=memo[:200], source="bill", source_id=f"{line_id}:{month}",
                basis="accrual",
                lines=(L.Line(account_id=expense.id, amount=amount, currency=base, original_amount=amount,
                              contact_id=contact, description=str(row["description"] or "")[:200], tags=tags),
                       L.Line(account_id=prepaid.id, amount=-amount, currency=base, original_amount=-amount,
                              contact_id=contact)))

    def apply(aid: str) -> Result:
        entry_id, _ = posting.post(ctx, e, by=who, action_id=aid)
        SPP.insert(ctx.c, {"bill_line_id": line_id, "month": month, "amount": amount, "entry_id": entry_id,
                           "action_id": aid, "created_at": ctx.at, "created_by": who})
        return {"bill_line_id": line_id, "month": month, "entry_id": entry_id, "amount": amount}
    return Plan(K.default_gate(ctx.org, who, KIND), f"Expense {month} of a prepaid bill line", apply,
                facts={"amount": amount, "counterparty": str(row["vendor_name"] or ""), "accounts": [expense.code]})


def unpost(ctx: Ctx, bill_id: str, who: str, action_id: str, memo: str) -> None:
    """Void a bill: each month it expensed is reversed on an open day, so that expense leaves and the prepaid asset is
    back where the bill's own entry left it."""
    from .banking import open_day
    for entry_id in SPP.posted_entries(ctx.c, bill_id):
        row = SL.entry_row(ctx.c, entry_id)
        if row is not None and str(row["status"]) == "posted":
            posting.reverse(ctx, entry_id, day=open_day(ctx, ctx.today), memo=memo, by=who, action_id=action_id)


PLANNERS = {KIND: amortize_plan}
