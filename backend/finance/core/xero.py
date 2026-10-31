"""Xero's records as the mirror takes them (spec §10), and the bodies Tico sends to it. Pure: no I/O.

A row a page or a change read holds gives one record, or none when the books do not take it in (a draft, a prepayment).
The page still counts every row (Read.seen), so a backfill cursor moves past the rest.
Amounts are integer minor units; Xero's decimals arrive as exact Decimal (the client parses JSON with parse_float)."""
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import ledger as L
from . import mirror as M
from .qbo import as_list, as_map, minor

Record = M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Ext
SystemKey = Callable[[], str]               # a system account's key (tax, rounding), read only when a record needs it
MS_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")
ACCOUNT_KIND = {"ASSET": "asset", "LIABILITY": "liability", "EQUITY": "equity", "REVENUE": "income", "EXPENSE": "expense"}
WRITE_TYPE = {"asset": "CURRENT", "liability": "CURRLIAB", "equity": "EQUITY", "income": "REVENUE", "expense": "EXPENSE"}
SYSTEM_SUBTYPE = {"DEBTORS": "receivable", "CREDITORS": "payable"}     # the control accounts Tico's documents post to
DOC_KIND = {"ACCREC": "Invoice", "ACCPAY": "Bill", "ACCRECCREDIT": "CreditMemo", "ACCPAYCREDIT": "VendorCredit"}
CREDIT_KINDS = ("CreditMemo", "VendorCredit")
CREDIT_SIDE = {"ACCRECCREDIT": "Invoice", "ACCPAYCREDIT": "Bill"}      # the documents a credit note applies to
INVOICE_PAYMENT = {"ACCREC": ("Payment", "Invoice"), "ACCPAY": ("BillPayment", "Bill")}
PAYMENT_TYPE = {"ACCRECPAYMENT": ("Payment", "Invoice"), "ACCPAYPAYMENT": ("BillPayment", "Bill")}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def amount_text(units: int) -> str:
    """Integer minor units as Xero's decimal text, signed (a journal line's debit is positive)."""
    return f"{Decimal(units).scaleb(-2):.2f}"


def marked(text: str, marker: str, limit: int) -> str:
    """The text cut to fit `limit` with the Tico marker at its end, so the marker is always whole."""
    if not marker:
        return text[:limit]
    return f"{text[:max(limit - len(marker) - 1, 0)].rstrip()} {marker}".lstrip()


def _utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    found = MS_DATE.fullmatch(text)
    if found:
        return EPOCH + timedelta(milliseconds=int(found.group(1)))
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def stamp(value: object) -> str:
    """A Xero time as the revision Tico keeps: UTC to the millisecond, so revisions compare as text."""
    when = _utc(value)
    if when is None:
        return ""
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"


def day(j: Mapping[str, Any], key: str) -> str:
    """A Xero date: the plain text beside the Microsoft date when there is one, else the UTC day of that date."""
    text = str(j.get(f"{key}String") or "")
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    when = _utc(j.get(key))
    if when is None:
        raise ValueError(f"no {key} date")
    return when.date().isoformat()


def _ext(kind: str, j: Mapping[str, Any], id_key: str, *, deleted: bool = False) -> M.Ext:
    ident = str(j.get(id_key) or "")
    if not ident:
        raise ValueError(f"no {id_key}")
    return M.Ext(kind, ident, stamp(j.get("UpdatedDateUTC")), deleted)


def _status(j: Mapping[str, Any]) -> str:
    return str(j.get("Status") or "")


def _account(item: Mapping[str, Any]) -> str:
    return f"Account:{item.get('AccountID') or ''}"


def _contact(j: Mapping[str, Any]) -> str:
    contact = as_map(j.get("Contact")) or {}
    return f"Contact:{contact['ContactID']}" if contact.get("ContactID") else ""


def _id_of(key: object, kind: str) -> str:
    """The Xero id a provider key holds ("Account:55" is 55 for an account)."""
    prefix, _, value = str(key or "").partition(":")
    if prefix != kind or not value:
        raise ValueError(f"not a Xero {kind.lower()}: {key!r}")
    return value


def account_record(j: Mapping[str, Any]) -> M.ChartAccount | M.Ext | None:
    """An account, keyed by its id: Xero's code is optional, so an account without one gets a placeholder code. An
    account with no class is not taken in; a deleted one is made inactive."""
    ext = _ext("Account", j, "AccountID")
    if _status(j) == "DELETED":
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    kind = ACCOUNT_KIND.get(str(j.get("Class") or ""))
    if kind is None:
        return None
    code = str(j.get("Code") or "") or f"X-{ext.external_id}"
    name = str(j.get("Name") or "") or code
    return M.ChartAccount(ext, code, name, kind, SYSTEM_SUBTYPE.get(str(j.get("SystemAccount") or ""), ""),
                          _status(j) != "ARCHIVED")


def contact_record(j: Mapping[str, Any]) -> M.Party:
    """A contact. Xero decides whether it is a customer or a supplier from its activity; with neither flag set the kind
    is "", so the books keep the kind they hold."""
    ext = _ext("Contact", j, "ContactID")
    customer, vendor = bool(j.get("IsCustomer")), bool(j.get("IsSupplier"))
    kind = "both" if customer and vendor else "customer" if customer else "vendor" if vendor else ""
    email = str(j.get("EmailAddress") or "")
    name = str(j.get("Name") or j.get("ContactNumber") or ext.external_id)
    return M.Party(ext, name, kind, active=str(j.get("ContactStatus") or "ACTIVE") != "ARCHIVED",
                   emails=(email,) if email else ())


def _lines(j: Mapping[str, Any], tax: SystemKey) -> tuple[M.EntryLine, ...]:
    """A document's lines net of tax (an Inclusive line carries its tax inside its amount), then one line for the tax
    they carry on the tax account, so the lines add up to the document's total."""
    inclusive = str(j.get("LineAmountTypes") or "") == "Inclusive"
    lines: list[M.EntryLine] = []
    due_total = 0
    for raw in as_list(j.get("LineItems")):
        item = as_map(raw) or {}
        gross, due = minor(item.get("LineAmount") or 0), minor(item.get("TaxAmount") or 0)
        due_total += due
        lines.append(M.EntryLine(_account(item), gross - due if inclusive else gross,
                                 description=str(item.get("Description") or "")))
    if due_total:
        lines.append(M.EntryLine(tax(), due_total, description="tax"))
    return tuple(lines)


def _allocations(j: Mapping[str, Any], side: str) -> tuple[tuple[str, int], ...]:
    """The documents a credit note is applied to (`side` names their kind), with each amount. A deleted allocation counts
    for nothing."""
    out: list[tuple[str, int]] = []
    for raw in as_list(j.get("Allocations")):
        allocation = as_map(raw) or {}
        target = as_map(allocation.get("Invoice")) or {}
        if allocation.get("IsDeleted") is True or not target.get("InvoiceID"):
            continue
        out.append((f"{side}:{target['InvoiceID']}", minor(allocation.get("Amount") or 0)))
    return tuple(out)


def document_record(j: Mapping[str, Any], *, tax: SystemKey, rounding: SystemKey,
                    credit: bool = False) -> M.Document | M.Ext | None:
    """An invoice, a bill (both Xero Invoices), or a credit note of either side (Xero CreditNotes). A draft or a submitted
    one waits; a voided one is a deleted document, its entry reversed. The total is Xero's, and its rounding is a line of
    its own on the rounding account (no account, no rounding: the document waits)."""
    kind = DOC_KIND.get(str(j.get("Type") or ""))
    if kind is None or (kind in CREDIT_KINDS) != credit:
        return None
    ext = _ext(kind, j, "CreditNoteID" if credit else "InvoiceID")
    if _status(j) in ("DELETED", "VOIDED"):
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) in ("", "DRAFT", "SUBMITTED"):
        return None
    lines = list(_lines(j, tax))
    rounded = minor(j.get("RoundingAmount") or 0)
    if rounded:
        lines.append(M.EntryLine(rounding(), rounded, description="rounding"))
    total = minor(j["Total"]) if j.get("Total") is not None else sum(x.amount for x in lines)
    side = CREDIT_SIDE.get(str(j.get("Type") or ""), "")
    return M.Document(ext, party=_contact(j), number=str(j.get("CreditNoteNumber" if credit else "InvoiceNumber") or ""),
                      day=day(j, "Date"), due=day(j, "DueDate") if j.get("DueDate") else "", total=total,
                      lines=tuple(lines), memo=str(j.get("Reference") or ""), currency=str(j.get("CurrencyCode") or ""),
                      links=_allocations(j, side) if credit else ())


def payment_record(j: Mapping[str, Any]) -> M.Settlement | M.Ext | None:
    """A payment of an invoice or a bill, told apart by Xero's PaymentType (the invoice's type when it names none). A
    prepayment, an overpayment or a payment of a credit note is not an invoice's money, so it waits."""
    target = as_map(j.get("Invoice")) or {}
    ptype = str(j.get("PaymentType") or "")
    kind, side = (PAYMENT_TYPE.get(ptype, ("Payment", "")) if ptype
                  else INVOICE_PAYMENT.get(str(target.get("Type") or ""), ("Payment", "")))
    ext = _ext(kind, j, "PaymentID")
    if _status(j) == "DELETED":
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) != "AUTHORISED" or not side or not target.get("InvoiceID"):
        return None
    account = as_map(j.get("Account")) or {}
    amount = minor(j.get("Amount") or 0)
    return M.Settlement(ext, day=day(j, "Date"), memo=str(j.get("Reference") or ""), party="",
                        bank=f"Account:{account.get('AccountID') or ''}", total=amount,
                        links=((f"{side}:{target['InvoiceID']}", amount),))


def manual_journal_record(j: Mapping[str, Any], tax: SystemKey) -> M.Journal | M.Ext | None:
    """A manual journal. A line's tax moves from its account to the tax account, so the journal still adds up to zero
    (a line's LineAmount is its gross amount). A journal Xero keeps out of its cash-basis reports is accrual only."""
    ext = _ext("ManualJournal", j, "ManualJournalID")
    if _status(j) in ("VOIDED", "DELETED"):
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) != "POSTED":
        return None
    lines: list[M.EntryLine] = []
    due_total = 0
    for raw in as_list(j.get("JournalLines")):
        line = as_map(raw) or {}
        gross, due = minor(line.get("LineAmount") or 0), minor(line.get("TaxAmount") or 0)
        due_total += due
        lines.append(M.EntryLine(_account(line), gross - due, description=str(line.get("Description") or "")))
    if due_total:
        lines.append(M.EntryLine(tax(), due_total, description="tax"))
    basis = "accrual" if j.get("ShowOnCashBasisReports") is False else "both"
    return M.Journal(ext, day(j, "Date"), str(j.get("Narration") or ""), "sync", tuple(lines), basis)


def bank_record(j: Mapping[str, Any], tax: SystemKey) -> M.Journal | M.Ext | None:
    """A bank transaction as an entry on its bank account: each line item on its account (net of its tax when the line is
    inclusive), its tax on the tax account, and the bank account the rest. Its currency is carried; the books refuse
    another than their own."""
    ext = _ext("BankTransaction", j, "BankTransactionID")
    if _status(j) in ("DELETED", "VOIDED"):
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) != "AUTHORISED":
        return None
    sign = -1 if str(j.get("Type") or "").startswith("RECEIVE") else 1
    inclusive = str(j.get("LineAmountTypes") or "") == "Inclusive"
    party = _contact(j)
    contra: list[M.EntryLine] = []
    due_total = 0
    for raw in as_list(j.get("LineItems")):
        item = as_map(raw) or {}
        gross, due = minor(item.get("LineAmount") or 0), minor(item.get("TaxAmount") or 0)
        due_total += due
        net = gross - due if inclusive else gross
        contra.append(M.EntryLine(_account(item), sign * net, party=party,
                                  description=str(item.get("Description") or "")))
    if due_total:
        contra.append(M.EntryLine(tax(), sign * due_total, party=party, description="tax"))
    bank = as_map(j.get("BankAccount")) or {}
    lines = (M.EntryLine(f"Account:{bank.get('AccountID') or ''}", -sum(x.amount for x in contra)), *contra)
    return M.Journal(ext, day(j, "Date"), str(j.get("Reference") or ""), "bank", lines,
                     currency=str(j.get("CurrencyCode") or ""))


def _build(entity: str, j: Mapping[str, Any], tax: SystemKey, rounding: SystemKey) -> Record | None:
    if entity == "accounts":
        return account_record(j)
    if entity == "parties":
        return contact_record(j)
    if entity == "invoices":
        return document_record(j, tax=tax, rounding=rounding)
    if entity == "credit_notes":
        return document_record(j, tax=tax, rounding=rounding, credit=True)
    if entity == "payments":
        return payment_record(j)
    if entity == "manual_journals":
        return manual_journal_record(j, tax)
    if entity == "bank_transactions":
        return bank_record(j, tax)
    raise ValueError(f"not a Xero entity: {entity}")


def records(entity: str, rows: Iterable[object], tax: SystemKey, rounding: SystemKey) -> list[Record]:
    """The records a page or a change read of one entity holds, in order: a row that is not taken in gives none."""
    out: list[Record] = []
    for raw in rows:
        j = as_map(raw)
        if j is None:
            raise ValueError(f"a {entity} row is not an object")
        made = _build(entity, j, tax, rounding)
        if made is not None:
            out.append(made)
    return out


# ------------------------------------------------------------------------------------------------ bodies out
def _tracking(classes: Mapping[str, Any]) -> list[dict[str, str]]:
    """A line's tracking: each class is "category/option" (the owner maps tags to them; off by default)."""
    out: list[dict[str, str]] = []
    for value in classes.values():
        category, _, option = str(value).partition("/")
        if category and option:
            out.append({"TrackingCategoryID": category, "TrackingOptionID": option})
    return out


def account_body(body: Mapping[str, Any]) -> dict[str, Any]:
    return {"Code": str(body.get("code") or ""), "Name": str(body.get("name") or ""),
            "Type": WRITE_TYPE[str(body["type"])], "Status": "ACTIVE" if body.get("active", True) else "ARCHIVED"}


def party_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A contact as Xero takes it. Xero sets whether it is a customer or a supplier from its activity, so no flag is sent."""
    emails = as_list(body.get("emails"))
    out: dict[str, Any] = {"Name": str(body.get("name") or ""),
                           "ContactStatus": "ACTIVE" if body.get("active", True) else "ARCHIVED"}
    if emails:
        out["EmailAddress"] = str(emails[0])
    return out


def journal_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A posted manual journal from an outbox payload (accounts already translated to Xero's ids)."""
    lines: list[dict[str, Any]] = []
    for raw in as_list(body.get("lines")):
        line = as_map(raw) or {}
        row: dict[str, Any] = {"LineAmount": amount_text(int(line["amount"])),
                               "AccountID": _id_of(line.get("account"), "Account"),
                               "Description": str(line.get("description") or "")[:4000]}
        tracking = _tracking(as_map(line.get("classes")) or {})
        if tracking:
            row["Tracking"] = tracking
        lines.append(row)
    return {"Narration": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 500),
            "Date": str(body["date"]), "Status": "POSTED", "LineAmountTypes": "NoTax", "JournalLines": lines}


def bank_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A categorized bank row as a bank transaction: its money account is the bank account, and its other line is the one
    line item. Xero's type gives the direction (SPEND takes money out, RECEIVE brings it in)."""
    money: Mapping[str, Any] | None = None
    items: list[Mapping[str, Any]] = []
    for raw in as_list(body.get("lines")):
        line = as_map(raw) or {}
        if line.get("role") == L.MONEY_ROLE and money is None:
            money = line
        else:
            items.append(line)
    if money is None or len(items) != 1:
        raise ValueError("a bank transaction takes one money line and one category")
    amount = int(money["amount"])
    item = items[0]
    if amount == 0 or int(item["amount"]) != -amount:
        raise ValueError("a bank transaction's two lines do not mirror each other")
    row: dict[str, Any] = {"AccountID": _id_of(item.get("account"), "Account"), "LineAmount": amount_text(abs(amount)),
                           "Description": str(item.get("description") or "")[:4000]}
    tracking = _tracking(as_map(item.get("classes")) or {})
    if tracking:
        row["Tracking"] = tracking
    out: dict[str, Any] = {"Type": "SPEND" if amount < 0 else "RECEIVE",
                           "BankAccount": {"AccountID": _id_of(money.get("account"), "Account")},
                           "Date": str(body["date"]), "Status": "AUTHORISED", "LineAmountTypes": "NoTax",
                           "Reference": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 1000),
                           "LineItems": [row]}
    party = str(item.get("party") or "")
    if party:
        out["Contact"] = {"ContactID": _id_of(party, "Contact")}
    return out


def void_body(ident: str) -> dict[str, Any]:
    return {"ManualJournals": [{"ManualJournalID": ident, "Status": "VOIDED"}]}
