"""Xero's records as the mirror takes them (spec §10), and the bodies Tico sends to it. Pure: no I/O.

A row a page or a change read holds gives one record, or none when the books do not take it in (a draft, a supplier
credit note, a prepayment). The page still counts every row (Read.seen), so a backfill cursor moves past the rest.
Amounts are integer minor units; Xero's decimals arrive as exact Decimal (the client parses JSON with parse_float)."""
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import mirror as M
from .qbo import as_list, as_map, minor

Record = M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Ext
TaxKey = Callable[[], str]                  # the tax control account's key, read only when a document carries tax
MS_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")
ACCOUNT_KIND = {"ASSET": "asset", "LIABILITY": "liability", "EQUITY": "equity", "REVENUE": "income", "EXPENSE": "expense"}
WRITE_TYPE = {"asset": "CURRENT", "liability": "CURRLIAB", "equity": "EQUITY", "income": "REVENUE", "expense": "EXPENSE"}
SYSTEM_SUBTYPE = {"DEBTORS": "receivable", "CREDITORS": "payable"}     # the control accounts Tico's documents post to
DOC_KIND = {"ACCREC": "Invoice", "ACCPAY": "Bill", "ACCRECCREDIT": "CreditMemo"}   # ACCPAYCREDIT is not taken in
PAYMENT_KIND = {"ACCREC": ("Payment", "Invoice"), "ACCPAY": ("BillPayment", "Bill")}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def amount_text(units: int) -> str:
    """Integer minor units as Xero's decimal text, signed (a journal line's debit is positive)."""
    return f"{Decimal(units).scaleb(-2):.2f}"


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


def account_record(j: Mapping[str, Any]) -> M.ChartAccount | M.Ext | None:
    """An account. One with no code or no class is not taken in (Tico needs both); a deleted one is made inactive."""
    ext = _ext("Account", j, "AccountID")
    if _status(j) == "DELETED":
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    kind = ACCOUNT_KIND.get(str(j.get("Class") or ""))
    code = str(j.get("Code") or "")
    if kind is None or not code:
        return None
    return M.ChartAccount(ext, code, str(j.get("Name") or code), kind,
                          SYSTEM_SUBTYPE.get(str(j.get("SystemAccount") or ""), ""), _status(j) != "ARCHIVED")


def contact_record(j: Mapping[str, Any]) -> M.Party:
    ext = _ext("Contact", j, "ContactID")
    customer, vendor = bool(j.get("IsCustomer")), bool(j.get("IsSupplier"))
    kind = "both" if customer and vendor else "vendor" if vendor else "customer"
    email = str(j.get("EmailAddress") or "")
    name = str(j.get("Name") or j.get("ContactNumber") or ext.external_id)
    return M.Party(ext, name, kind, active=str(j.get("ContactStatus") or "ACTIVE") != "ARCHIVED",
                   emails=(email,) if email else ())


def _lines(j: Mapping[str, Any], tax: TaxKey) -> tuple[M.EntryLine, ...]:
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


def _allocations(j: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    """The invoices a credit note is applied to, with each amount. A deleted allocation counts for nothing."""
    out: list[tuple[str, int]] = []
    for raw in as_list(j.get("Allocations")):
        allocation = as_map(raw) or {}
        target = as_map(allocation.get("Invoice")) or {}
        if allocation.get("IsDeleted") is True or not target.get("InvoiceID"):
            continue
        out.append((f"Invoice:{target['InvoiceID']}", minor(allocation.get("Amount") or 0)))
    return tuple(out)


def document_record(j: Mapping[str, Any], *, tax: TaxKey, credit: bool = False) -> M.Document | M.Ext | None:
    """An invoice, a bill (both Xero Invoices) or a receivable credit note (Xero CreditNotes). A draft or a submitted one
    waits; a voided one is a deleted document, its entry reversed."""
    kind = DOC_KIND.get(str(j.get("Type") or ""))
    if kind is None or (kind == "CreditMemo") != credit:
        return None
    ext = _ext(kind, j, "CreditNoteID" if credit else "InvoiceID")
    if _status(j) in ("DELETED", "VOIDED"):
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) in ("", "DRAFT", "SUBMITTED"):
        return None
    lines = _lines(j, tax)
    return M.Document(ext, party=_contact(j), number=str(j.get("CreditNoteNumber" if credit else "InvoiceNumber") or ""),
                      day=day(j, "Date"), due=day(j, "DueDate") if j.get("DueDate") else "",
                      total=sum(x.amount for x in lines), lines=lines,
                      memo=str(j.get("Reference") or ""), currency=str(j.get("CurrencyCode") or ""),
                      links=_allocations(j) if credit else ())


def payment_record(j: Mapping[str, Any]) -> M.Settlement | M.Ext | None:
    """A payment of an invoice or a bill. A prepayment, an overpayment or a payment of a credit note is not an invoice's
    money, so it waits."""
    target = as_map(j.get("Invoice")) or {}
    kind, side = PAYMENT_KIND.get(str(target.get("Type") or ""), ("Payment", ""))
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


def manual_journal_record(j: Mapping[str, Any]) -> M.Journal | M.Ext | None:
    ext = _ext("ManualJournal", j, "ManualJournalID")
    if _status(j) in ("VOIDED", "DELETED"):
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) != "POSTED":
        return None
    lines = tuple(M.EntryLine(_account(x), minor(x.get("LineAmount") or 0), description=str(x.get("Description") or ""))
                  for x in (as_map(raw) or {} for raw in as_list(j.get("JournalLines"))))
    return M.Journal(ext, day(j, "Date"), str(j.get("Narration") or ""), "sync", lines)


def bank_record(j: Mapping[str, Any]) -> M.Journal | M.Ext | None:
    """A bank transaction as an entry on its bank account: each line item on its account, the bank account the rest."""
    ext = _ext("BankTransaction", j, "BankTransactionID")
    if _status(j) in ("DELETED", "VOIDED"):
        return M.Ext(ext.kind, ext.external_id, ext.revision, True)
    if _status(j) != "AUTHORISED":
        return None
    receive = str(j.get("Type") or "").startswith("RECEIVE")
    party = _contact(j)
    contra: list[M.EntryLine] = []
    for raw in as_list(j.get("LineItems")):
        item = as_map(raw) or {}
        gross = minor(item.get("LineAmount") or 0) + minor(item.get("TaxAmount") or 0)
        contra.append(M.EntryLine(_account(item), -gross if receive else gross, party=party,
                                  description=str(item.get("Description") or "")))
    bank = as_map(j.get("BankAccount")) or {}
    lines = (M.EntryLine(f"Account:{bank.get('AccountID') or ''}", -sum(x.amount for x in contra)), *contra)
    return M.Journal(ext, day(j, "Date"), str(j.get("Reference") or ""), "bank", lines)


def _build(entity: str, j: Mapping[str, Any], tax: TaxKey) -> Record | None:
    if entity == "accounts":
        return account_record(j)
    if entity == "parties":
        return contact_record(j)
    if entity == "invoices":
        return document_record(j, tax=tax)
    if entity == "credit_notes":
        return document_record(j, tax=tax, credit=True)
    if entity == "payments":
        return payment_record(j)
    if entity == "manual_journals":
        return manual_journal_record(j)
    if entity == "bank_transactions":
        return bank_record(j)
    raise ValueError(f"not a Xero entity: {entity}")


def records(entity: str, rows: Iterable[object], tax: TaxKey) -> list[Record]:
    """The records a page or a change read of one entity holds, in order: a row that is not taken in gives none."""
    out: list[Record] = []
    for raw in rows:
        j = as_map(raw)
        if j is None:
            raise ValueError(f"a {entity} row is not an object")
        made = _build(entity, j, tax)
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
    kind = str(body.get("kind") or "")
    emails = as_list(body.get("emails"))
    out: dict[str, Any] = {"Name": str(body.get("name") or ""), "IsCustomer": kind in ("customer", "both"),
                           "IsSupplier": kind in ("vendor", "both"),
                           "ContactStatus": "ACTIVE" if body.get("active", True) else "ARCHIVED"}
    if emails:
        out["EmailAddress"] = str(emails[0])
    return out


def journal_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A posted manual journal from an outbox payload (accounts already translated to Xero's ids)."""
    lines: list[dict[str, Any]] = []
    for raw in as_list(body.get("lines")):
        line = as_map(raw) or {}
        account, _, value = str(line.get("account") or "").partition(":")
        if account != "Account" or not value:
            raise ValueError("not a Xero account")
        row: dict[str, Any] = {"LineAmount": amount_text(int(line["amount"])), "AccountID": value,
                               "Description": str(line.get("description") or "")[:4000]}
        tracking = _tracking(as_map(line.get("classes")) or {})
        if tracking:
            row["Tracking"] = tracking
        lines.append(row)
    return {"Narration": str(body.get("memo") or "")[:500], "Date": str(body["date"]), "Status": "POSTED",
            "LineAmountTypes": "NoTax", "JournalLines": lines}


def void_body(ident: str) -> dict[str, Any]:
    return {"ManualJournals": [{"ManualJournalID": ident, "Status": "VOIDED"}]}
