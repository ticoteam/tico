"""Tax tracking (spec §11): sales tax jurisdictions, their remittances and quarterly due dates, the 1099-NEC due date, and the
two workpapers with their CSV exports. Each due date is an external rule owned by the Bookkeeper, seeded once when Finance
is set up, and a remittance is a bill to the jurisdiction's tax authority contact. A workpaper is filed as a whole, so only
someone who sees everything can read one, and it is refused whole rather than shown partial: to anyone who does not see
everything, and to a viewer who lacks full detail on a private account that a line behind the workpaper sits on. A
vendor's TIN is never read here, only whether one is on file (the vault holds it, see payments.py)."""
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import Any

from .. import hubdb as _hubdb
from .. import org_rules as _org_rules
from .. import rule_due as _rule_due
from .. import rule_due_routine as _rule_due_routine
from ..auth import Identity
from . import tab
from .banking import new_account
from .context import Ctx, load
from .core import dates
from .core import kinds as K
from .core import tax as T
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .documents import bill_create
from .handlers import Plan
from .store import ap as SAP
from .store import ledger as SL
from .store import tax as ST

# The roster and rule modules are untyped; these aliases make their results Any for strict pyright, as store/tico.py does.
_H: Any = _hubdb
_OR: Any = _org_rules
_D: Any = _rule_due
_RR: Any = _rule_due_routine

Result = dict[str, object]
ACTOR = "bot:bookkeeper"            # the Bookkeeper (backend/bookkeeper.py): it owns the due dates and their routines
ACCOUNT_BASE = "2100"               # a jurisdiction's liability account takes the first free code from here
DEFAULT_DUE_DAY = 20                # a sales tax return falls due on this day of the month after its quarter
TASK_DAYS_SALES = 14                # a routine opens the task this many days before a sales tax due date
TASK_DAYS_1099 = 30                 # and this many before the 1099-NEC due date
FORM_KEY = "1099-nec"               # the seed key of the 1099-NEC due date (one for the company)
PERIOD = re.compile(r"^\d{4}-Q[1-4]$")
LOOKAHEAD = 31                      # a return falls due in the month after its quarter, on a day up to the 28th


def _period(text: str) -> str:
    if not PERIOD.match(text):
        raise invalid("a period is a quarter, like 2026-Q3")
    return text


def _money(ctx: Ctx, amount: int) -> str:
    return f"{T.dollars(amount)} {ctx.book.base_currency}"


def _jurisdiction(ctx: Ctx, code: str) -> Any:
    row = ST.jurisdiction_named(ctx.c, ctx.book.id, code)
    if row is None:
        raise FinanceError(NOT_FOUND, f"there is no sales tax jurisdiction {code}")
    return row


def _first_due(c: sqlite3.Connection, rule_id: str | None) -> tuple[date, str] | None:
    """The first date and repeat of the live due date a seeded rule has, or None when it has none."""
    if not rule_id:
        return None
    rows: list[Any] = _D.live_rows(c, [rule_id])
    if not rows:
        return None
    return date.fromisoformat(str(rows[-1]["first_due_on"])), str(rows[-1]["repeats"])


def _due_after(due: tuple[date, str], end: date) -> str | None:
    """The day a period's return falls due: the rule's occurrence in the month after the period ended. None when the
    rule starts later than that, since the due date before Finance seeded it is not known."""
    first, repeats = due
    limit = end + timedelta(days=LOOKAHEAD)
    found: list[Any] = _D.occurrences(first, repeats, limit)
    return next((d.isoformat() for d in found if end < d <= limit), None)


def _sales_rows(ctx: Ctx, year: int, due: bool) -> list[T.SalesRow]:
    """The sales tax rows of a year from the posted invoices and the paid shares of the remittance bills."""
    c = ctx.c
    jurisdictions = ST.jurisdictions(c, ctx.book.id)
    accounts = [(str(j["account_id"]), str(j["code"]), str(j["name"])) for j in jurisdictions]
    account_ids = [account for account, _, _ in accounts]
    lines = [(str(r["invoice_id"]), str(r["invoice_date"]), str(r["account_id"]), int(r["amount"]), str(r["type"]),
              str(r["subtype"]), bool(r["taxable"]))
             for r in ST.invoice_lines(c, ctx.book.id, f"{year}-01-01", f"{year}-12-31")]
    remits = [(str(r["account_id"]), str(r["tags"]), int(r["amount"]), int(r["bill_total"]), int(r["bill_paid"]))
              for r in ST.remittance_lines(c, ctx.book.id, account_ids)]
    due_on: dict[tuple[str, str], str | None] = {}
    for j in jurisdictions:
        dues = _first_due(c, ST.seeded(c, f"sales-tax:{j['code']}")) if due else None
        for period, _, end in T.quarters(year):
            due_on[(str(j["account_id"]), period)] = _due_after(dues, end) if dues else None
    return T.sales_tax_rows(year, accounts, lines, remits, due_on)


def _year(ctx: Ctx, year: int | None) -> int:
    return year if year is not None else dates.day(ctx.today).year


# ------------------------------------------------------------------------------------------------ planners
def tax_jurisdiction_add(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """A sales tax jurisdiction: a liability account for the tax it collects, the authority contact its remittances go to,
    and the day its quarterly return falls due. Its due date is seeded for the Bookkeeper as the apply runs."""
    V.only(p, ("code", "name", "authority_id", "due_day"))
    code = V.text(p, "code", 20)
    name = V.text(p, "name", 120)
    authority = V.opt_text(p, "authority_id", 80) or ""
    if authority and ST.contact_kind(ctx.c, ctx.book.id, authority) not in ("vendor", "both"):
        raise invalid("a tax authority is a vendor contact")
    due_day = V.integer(p, "due_day", 1, 28, DEFAULT_DUE_DAY)
    if ST.jurisdiction_named(ctx.c, ctx.book.id, code) is not None:
        raise FinanceError(CONFLICT, f"{code} is already a sales tax jurisdiction")

    def apply(aid: str) -> Result:
        account = new_account(ctx, ACCOUNT_BASE, f"Sales tax payable - {name}", "liability", T.SALES_TAX, who)
        jid = SL.new_id()
        ST.insert_jurisdiction(ctx.c, {"id": jid, "book_id": ctx.book.id, "code": code, "name": name,
                                       "account_id": account.id, "authority_id": authority, "due_day": due_day,
                                       "created_at": ctx.at, "created_by": who})
        seed_dues(ctx.c, ctx.at)
        return {"jurisdiction_id": jid, "account_id": account.id, "due_day": due_day}
    return Plan(K.default_gate(ctx.org, who, "tax_jurisdiction_add"),
                f"Add sales tax jurisdiction {name} ({code}), due on day {due_day} after each quarter", apply)


def tax_remit(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Remit a quarter's sales tax: a bill to the jurisdiction's tax authority with one line on its sales tax account,
    tagged with the quarter. It is paid like any bill, and the workpaper counts what is paid on it as remitted."""
    V.only(p, ("jurisdiction", "period", "amount", "bill_date", "due_date", "number"))
    j = _jurisdiction(ctx, V.text(p, "jurisdiction", 20))
    period = _period(V.text(p, "period", 7))
    if not j["authority_id"]:
        raise invalid(f"{j['code']} has no tax authority: name one on the jurisdiction first")
    if "amount" in p:
        amount = V.amount(p)
    else:
        year = int(period[:4])
        _require_full(ctx, who, _sales_feeds(ctx, year, period))    # the amount owed is read off the workpaper
        amount = next((r.due for r in _sales_rows(ctx, year, False)
                       if r.code == j["code"] and r.period == period), 0)
    if amount <= 0:
        raise invalid(f"nothing is owed for {j['code']} {period}")
    payload: dict[str, object] = {
        "vendor_id": j["authority_id"], "number": V.opt_text(p, "number", 80) or "",
        "bill_date": V.opt_day(p, "bill_date") or dates.day(ctx.today).isoformat(),
        "lines": [{"account_id": j["account_id"], "amount": amount, "description": f"Sales tax {j['code']} {period}",
                   "tags": {"custom": f"{T.REMIT_TAG}:{period}"}}]}
    if V.opt_day(p, "due_date"):
        payload["due_date"] = V.opt_day(p, "due_date")
    plan = bill_create(ctx, who, payload)
    return replace(plan, gate=K.default_gate(ctx.org, who, "tax_remit"),
                   summary=f"Remit {_money(ctx, amount)} sales tax ({j['code']}, {period}) to the tax authority")


PLANNERS = {"tax_jurisdiction_add": tax_jurisdiction_add, "tax_remit": tax_remit}


# ------------------------------------------------------------------------------------------------ due dates
def _seed(c: sqlite3.Connection, at: str, key: str, text: str, source: str, kind: str, repeats: str, first: date,
          days: int) -> None:
    """One outside obligation: its external rule, a due date owned by the Bookkeeper, and the routine that opens its task."""
    rule_id: str = _OR.seed_external(c, Identity(actor=ACTOR, role="bot"), text, source, kind)
    due_id: str = _D.add(c, ACTOR, rule_id, first.isoformat(), repeats, _D.default_zone(), ACTOR)
    _RR.sync(c, ACTOR, _D.one(c, due_id), text, days)
    ST.add_seed(c, key, rule_id, at)


def seed_dues(c: sqlite3.Connection, at: str) -> list[str]:
    """Seed the outside obligations Finance tracks, each once: the 1099-NEC (due January 31) and each sales tax
    jurisdiction's quarterly return. Nothing is seeded before Finance is set up or before the Bookkeeper exists to own
    them; the Bookkeeper's own set-up calls this again. Returns the keys it seeded now."""
    book = SL.book(c)
    if book is None or _H.bot(c, ACTOR) is None:
        return []
    today = dates.day(at)
    made: list[str] = []
    if ST.seeded(c, FORM_KEY) is None:
        _seed(c, at, FORM_KEY, "File a 1099-NEC for each vendor paid for services, by January 31", "IRS", "government",
              "yearly", T.first_january(today), TASK_DAYS_1099)
        made.append(FORM_KEY)
    for j in ST.jurisdictions(c, book.id):
        key = f"sales-tax:{j['code']}"
        if ST.seeded(c, key) is None:
            _seed(c, at, key, f"File and pay {j['name']} sales tax for each quarter", f"{j['name']} tax authority",
                  "government", "quarterly", T.first_quarterly(today, int(j["due_day"])), TASK_DAYS_SALES)
            made.append(key)
    return made


# ------------------------------------------------------------------------------------------------ reads
def _remit_for(tags_json: str, year: int, period: str | None) -> bool:
    """Whether a remittance line is tagged for the year, or for one period of it when one is given."""
    tag = T.tags_of(tags_json).get("custom", "")
    if not tag.startswith(T.REMIT_TAG + ":"):
        return False
    key = tag[len(T.REMIT_TAG) + 1:]
    return key == period if period else key.startswith(f"{year}-")


def _sales_feeds(ctx: Ctx, year: int, period: str | None = None) -> set[str]:
    """The accounts of the lines behind a year's sales tax rows, or behind one quarter's remittance: the lines of the
    invoices that carry sales tax (an invoice without one feeds no row), dated in the year or the quarter, and every line
    of the remittance bills tagged for it."""
    c = ctx.c
    account_ids = [str(j["account_id"]) for j in ST.jurisdictions(c, ctx.book.id)]
    bills = sorted({str(r["bill_id"]) for r in ST.remittance_lines(c, ctx.book.id, account_ids)
                    if _remit_for(str(r["tags"]), year, period)})
    invoices = ST.invoice_lines(c, ctx.book.id, f"{year}-01-01", f"{year}-12-31")
    taxed = {str(r["invoice_id"]) for r in invoices if str(r["subtype"]) == T.SALES_TAX
             and (period is None or T.quarter_key(str(r["invoice_date"])) == period)}
    lines = [*(r for r in invoices if str(r["invoice_id"]) in taxed), *ST.bill_lines(c, bills)]
    return {str(r["account_id"]) for r in lines}


def _require_full(ctx: Ctx, viewer: str, feeding: Iterable[str]) -> None:
    """Refuses a workpaper to anyone who does not see everything. Even for them, it is refused whole when a line behind it
    sits on a private account the viewer lacks full detail for, since a total without that line would be partial."""
    view = ctx.view(viewer)
    if not view.everything:
        raise denied("tax workpapers need full view: only someone who sees everything can read them")
    if set(feeding) & set(tab.private_cut(ctx, view)):
        raise denied("a line behind this tax workpaper is on a private account you do not see in full")


def _sales(c: sqlite3.Connection, viewer: str, year: int | None, at: str | None) -> tuple[Ctx, int, list[T.SalesRow]]:
    ctx = load(c, at)
    y = _year(ctx, year)
    _require_full(ctx, viewer, _sales_feeds(ctx, y))
    return ctx, y, _sales_rows(ctx, y, True)


def sales_tax_report(c: sqlite3.Connection, viewer: str, year: int | None = None, at: str | None = None) -> Result:
    """The sales tax workpaper for a year: each jurisdiction's four quarters, with taxable and exempt sales, tax collected,
    tax remitted, what is due, and the day the return falls due."""
    ctx, y, rows = _sales(c, viewer, year, at)
    return {"year": y, "currency": ctx.book.base_currency, "rows": [asdict(r) for r in rows]}


def sales_tax_csv(c: sqlite3.Connection, viewer: str, year: int | None = None, at: str | None = None) -> str:
    """The sales tax workpaper as CSV (T.SALES_COLUMNS), amounts in dollars and cents."""
    _, _, rows = _sales(c, viewer, year, at)
    return T.csv_text(T.SALES_COLUMNS, T.sales_export(rows))


def _form(c: sqlite3.Connection, viewer: str, year: int | None, at: str | None) -> tuple[Ctx, int, list[T.FormRow]]:
    ctx = load(c, at)
    y = _year(ctx, year)
    start, end = f"{y}-01-01", f"{y}-12-31"
    vendors = ST.vendors_1099(c, ctx.book.id)
    owner: dict[str, str] = {}
    for r in vendors:
        for key in SAP.vendor_keys(c, ctx.book.id, str(r["id"])):
            owner.setdefault(key, str(r["id"]))
    flagged = {str(r["id"]) for r in vendors}
    expenses = [r for r in ST.expense_lines(c, ctx.book.id, start, end) if str(r["vendor_key"]) in owner]
    settled = [r for r in ST.settlements(c, ctx.book.id, start, end) if str(r["vendor_id"]) in flagged]
    feeding = {str(r["account_id"]) for r in expenses}
    feeding |= {str(r["account_id"]) for r in ST.bill_lines(c, sorted({str(r["bill_id"]) for r in settled}))}
    _require_full(ctx, viewer, feeding)
    cash, card = T.paid_by_vendor(
        [(str(r["vendor_id"]), str(r["from_kind"]), int(r["amount"]), str(r["money_kind"]), str(r["subtype"]),
          bool(r["by_card"])) for r in settled],
        [(owner[str(r["vendor_key"])], bool(r["by_card"]), int(r["amount"])) for r in expenses])
    rows = [(str(r["id"]), str(r["name"]), bool(r["tin_ref"])) for r in vendors]
    return ctx, y, T.form_rows(y, rows, cash, card)


def form_1099_report(c: sqlite3.Connection, viewer: str, year: int | None = None, at: str | None = None) -> Result:
    """The 1099 workpaper for a year: each flagged vendor's cash paid (card payments kept out), the threshold, whether a
    1099-NEC is due, and the day the forms fall due."""
    ctx, y, rows = _form(c, viewer, year, at)
    due = _first_due(c, ST.seeded(c, FORM_KEY))
    due_on = None
    if due:
        due_on = next((d.isoformat() for d in _D.occurrences(due[0], due[1], date(y + 1, 12, 31))
                       if d.year == y + 1), None)
    return {"year": y, "currency": ctx.book.base_currency, "threshold": T.threshold(y), "due_on": due_on,
            "rows": [asdict(r) for r in rows]}


def form_1099_csv(c: sqlite3.Connection, viewer: str, year: int | None = None, at: str | None = None) -> str:
    """The 1099 workpaper as CSV (T.FORM_COLUMNS), amounts in dollars and cents."""
    _, y, rows = _form(c, viewer, year, at)
    return T.csv_text(T.FORM_COLUMNS, T.form_export(rows, y))
