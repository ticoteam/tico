"""Tax tracking (spec §11): sales tax by jurisdiction and quarter, and 1099 vendor totals by calendar year. Pure: the store
reads the rows and the tax module asks the due dates, this module works out the workpapers. Money is integer minor
units, written in exports as dollars and cents."""
import calendar
import csv
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import cast

SALES_TAX = "sales_tax"                     # the GL subtype of a jurisdiction's liability account
REMIT_TAG = "tax"                           # a remittance line's custom tag reads "tax:2026-Q3"
TAXED = ("sent", "partial", "paid")         # invoice statuses whose tax has posted
CARD_KINDS = ("card_program",)              # money accounts whose payments are card charges
CARD_SUBTYPES = ("credit_card",)            # GL accounts card charges post through
NOT_CASH = ("credit",)                      # Gateway credit: a prepaid balance, not money sent to the vendor
THRESHOLDS = ((2026, 200000), (0, 60000))   # (from year, minor units): $2,000 for 2026 payments, $600 before
SALES_COLUMNS = ("period", "jurisdiction", "taxable_sales", "exempt_sales", "tax_collected", "tax_remitted", "due")
FORM_COLUMNS = ("year", "vendor", "tin_on_file", "cash_paid", "card_paid_excluded", "threshold", "reportable")

Jurisdiction = tuple[str, str, str]                      # (GL account id, code, name)
InvoiceLine = tuple[str, str, str, int, str, str, bool]  # (invoice, day, account, amount, type, subtype, taxable)
Remittance = tuple[str, str, int, int, int]              # (account, tags as JSON, amount, bill total, bill paid)
Vendor = tuple[str, str, bool]                           # (contact id, name, a TIN on file)
Settlement = tuple[str, str, int, str, str, bool]        # (vendor, from kind, amount, money kind, GL subtype, card)
Expense = tuple[str, bool, int]                          # (vendor id, paid by card, amount)


@dataclass(frozen=True)
class SalesRow:
    period: str
    code: str
    jurisdiction: str
    taxable_sales: int
    exempt_sales: int
    tax_collected: int
    tax_remitted: int
    due: int
    due_on: str | None


@dataclass(frozen=True)
class FormRow:
    vendor_id: str
    vendor: str
    tin_on_file: bool
    cash_paid: int
    card_paid_excluded: int
    threshold: int
    reportable: bool


def quarters(year: int) -> list[tuple[str, date, date]]:
    """The year's calendar quarters: (key like 2026-Q3, first day, last day)."""
    out: list[tuple[str, date, date]] = []
    for q in range(4):
        first_month = 3 * q + 1
        last_month = first_month + 2
        out.append((f"{year}-Q{q + 1}", date(year, first_month, 1),
                    date(year, last_month, calendar.monthrange(year, last_month)[1])))
    return out


def quarter_key(day: str) -> str:
    """The calendar quarter a day falls in, as 2026-Q3."""
    when = date.fromisoformat(day)
    return f"{when.year}-Q{(when.month - 1) // 3 + 1}"


def threshold(year: int) -> int:
    """What one vendor must be paid in a year before a 1099-NEC is due, in minor units."""
    return next(amount for start, amount in THRESHOLDS if year >= start)


def first_quarterly(today: date, day: int) -> date:
    """The first quarterly due date on or after today: the same day in January, April, July or October."""
    return next(d for d in (date(y, m, day) for y in (today.year, today.year + 1) for m in (1, 4, 7, 10))
                if d >= today)


def first_january(today: date) -> date:
    """The first 1099-NEC due date (January 31) on or after today."""
    due = date(today.year, 1, 31)
    return due if due >= today else date(today.year + 1, 1, 31)


def dollars(minor: int) -> str:
    """Minor units as dollars and cents, without a float."""
    whole, cents = divmod(abs(minor), 100)
    return f"{'-' if minor < 0 else ''}{whole}.{cents:02d}"


def tags_of(text: str) -> dict[str, str]:
    """A line's tags, from the JSON they are stored as."""
    raw: object = json.loads(text or "{}")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in cast(dict[str, object], raw).items()}


def share(part: int, whole: int, amount: int) -> int:
    """The part of `amount` that `part` of `whole` covers, rounded down so a share never comes out above the amount."""
    return amount * part // whole if whole > 0 else 0


def taxable_default(given: bool | None, income: bool, carries_tax: bool) -> bool:
    """Whether an invoice line is taxable: as the payload says, else an income line on an invoice with a sales tax line."""
    return (income and carries_tax) if given is None else given


def sales_tax_rows(year: int, jurisdictions: Sequence[Jurisdiction], lines: Iterable[InvoiceLine],
                   remittances: Iterable[Remittance], due_on: Mapping[tuple[str, str], str | None]) -> list[SalesRow]:
    """One row per jurisdiction and quarter of the year. Tax collected is the tax lines of invoices dated in the quarter.
    The income lines of those invoices split into taxable sales and exempt sales by their taxable flag. Tax remitted is
    the paid share of each remittance line tagged with the quarter. Due is what was collected less what was remitted."""
    taxable: dict[str, int] = {}
    exempt: dict[str, int] = {}
    taxed: dict[tuple[str, str], set[str]] = {}
    collected: dict[tuple[str, str], int] = {}
    for invoice, day, account, amount, kind, subtype, flagged in lines:
        if subtype == SALES_TAX:
            key = (account, quarter_key(day))
            taxed.setdefault(key, set()).add(invoice)
            collected[key] = collected.get(key, 0) + amount
        elif kind == "income":
            bucket = taxable if flagged else exempt
            bucket[invoice] = bucket.get(invoice, 0) + amount
    remitted: dict[tuple[str, str], int] = {}
    for account, tags_json, amount, bill_total, bill_paid in remittances:
        tag = tags_of(tags_json).get("custom", "")
        if tag.startswith(REMIT_TAG + ":"):
            key = (account, tag[len(REMIT_TAG) + 1:])
            remitted[key] = remitted.get(key, 0) + share(bill_paid, bill_total, amount)
    rows: list[SalesRow] = []
    for account, code, name in sorted(jurisdictions, key=lambda j: j[1]):
        for period, _, _ in quarters(year):
            key = (account, period)
            got, sent = collected.get(key, 0), remitted.get(key, 0)
            invoices = taxed.get(key, set())
            rows.append(SalesRow(period=period, code=code, jurisdiction=name,
                                 taxable_sales=sum(taxable.get(i, 0) for i in invoices),
                                 exempt_sales=sum(exempt.get(i, 0) for i in invoices),
                                 tax_collected=got, tax_remitted=sent, due=got - sent, due_on=due_on.get(key)))
    return rows


def paid_by_vendor(settled: Iterable[Settlement], expenses: Iterable[Expense]) -> tuple[dict[str, int], dict[str, int]]:
    """Cash paid to each vendor, and what was paid by card (kept out of it), as (cash, card). A bill settled from a card
    row, a card money account or a card GL account is a card payment; so is an expense posted from a card row. A
    Gateway credit is neither."""
    cash: dict[str, int] = {}
    card: dict[str, int] = {}
    for vendor, kind, amount, money_kind, subtype, by_card in settled:
        if kind in NOT_CASH:
            continue
        target = card if by_card or money_kind in CARD_KINDS or subtype in CARD_SUBTYPES else cash
        target[vendor] = target.get(vendor, 0) + amount
    for vendor, by_card, amount in expenses:
        target = card if by_card else cash
        target[vendor] = target.get(vendor, 0) + amount
    return cash, card


def form_rows(year: int, vendors: Sequence[Vendor], cash: Mapping[str, int], card: Mapping[str, int]) -> list[FormRow]:
    """One row per flagged vendor, by name: cash paid in the year, the card payments left out, and whether the cash
    reaches the threshold."""
    limit = threshold(year)
    rows: list[FormRow] = []
    for vendor_id, name, tin in sorted(vendors, key=lambda v: v[1].lower()):
        paid = cash.get(vendor_id, 0)
        rows.append(FormRow(vendor_id=vendor_id, vendor=name, tin_on_file=tin, cash_paid=paid,
                            card_paid_excluded=card.get(vendor_id, 0), threshold=limit, reportable=paid >= limit))
    return rows


def csv_text(columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> str:
    """A CSV with a header row, in the columns given, one line per row."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return out.getvalue()


def sales_export(rows: Iterable[SalesRow]) -> list[dict[str, object]]:
    """The sales tax workpaper's export lines (SALES_COLUMNS), amounts in dollars and cents."""
    return [{"period": r.period, "jurisdiction": r.jurisdiction, "taxable_sales": dollars(r.taxable_sales),
             "exempt_sales": dollars(r.exempt_sales), "tax_collected": dollars(r.tax_collected),
             "tax_remitted": dollars(r.tax_remitted), "due": dollars(r.due)} for r in rows]


def form_export(rows: Iterable[FormRow], year: int) -> list[dict[str, object]]:
    """The 1099 workpaper's export lines (FORM_COLUMNS), amounts in dollars and cents."""
    return [{"year": year, "vendor": r.vendor, "tin_on_file": "yes" if r.tin_on_file else "no",
             "cash_paid": dollars(r.cash_paid), "card_paid_excluded": dollars(r.card_paid_excluded),
             "threshold": dollars(r.threshold), "reportable": "yes" if r.reportable else "no"} for r in rows]
