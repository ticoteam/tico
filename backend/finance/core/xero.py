"""Xero's records as the mirror takes them (spec §10), and the bodies Tico sends to Xero. Pure: no I/O.

A row a page or a change read holds gives its records. A row the books do not take in yet (a draft, a type or a
currency the books cannot hold) gives a Refused, which waits and is read again. A row that is not needed gives none: a
bank transfer's two legs, which the transfer itself carries once. The page still counts every row (Read.seen), so a
backfill cursor moves past the rest. Amounts are integer minor units; Xero's decimals arrive as exact Decimal."""
import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import ledger as L
from . import mirror as M
from .qbo import as_list, as_map, minor

Record = (M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Refused | M.Ext | M.Item
          | M.Location | M.Dimension)
Built = list[Record]
# A Xero key by a name, "" when Xero holds none: a system account by its role ("DEBTORS"), an account by its code
# ("code:200"), a tracking option by its names ("option:Region/East"), or the location's tracking category ("location").
System = Callable[[str], str]
MS_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")
MARK = re.compile(r"\[tico:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]")
ROUNDING_LIMIT = 10               # minor units: a document's rounding is at most this much either way
ACCOUNT_KIND = {"ASSET": "asset", "LIABILITY": "liability", "EQUITY": "equity", "REVENUE": "income", "EXPENSE": "expense"}
WRITE_TYPE = {"asset": "CURRENT", "liability": "CURRLIAB", "equity": "EQUITY", "income": "REVENUE", "expense": "EXPENSE"}
DOC_KIND = {"ACCREC": "Invoice", "ACCPAY": "Bill", "ACCRECCREDIT": "CreditMemo", "ACCPAYCREDIT": "VendorCredit"}
CREDIT_KINDS = ("CreditMemo", "VendorCredit")
CREDIT_SIDE = {"ACCRECCREDIT": "Invoice", "ACCPAYCREDIT": "Bill"}     # the documents a credit note applies to
SYSTEM_SUBTYPE = {"DEBTORS": "receivable", "CREDITORS": "payable", "GST": M.SALES_TAX}
PAYMENT_KIND = {"ACCRECPAYMENT": "Payment", "ACCPAYPAYMENT": "BillPayment"}
# A refund moves money back out of (or into) the bank against its control account: (control, debit sign, the credit
# memo or supplier's credit it refunds, "" for an overpayment or prepayment, which is not one).
REFUND_LINES = {"ARCREDITPAYMENT": ("DEBTORS", 1, "CreditMemo"), "APCREDITPAYMENT": ("CREDITORS", -1, "VendorCredit"),
                "AROVERPAYMENTPAYMENT": ("DEBTORS", 1, ""), "ARPREPAYMENTPAYMENT": ("DEBTORS", 1, ""),
                "APOVERPAYMENTPAYMENT": ("CREDITORS", -1, ""), "APPREPAYMENTPAYMENT": ("CREDITORS", -1, "")}
BANK_KIND = {"RECEIVE": "BankTransaction", "SPEND": "BankTransaction", "RECEIVE-OVERPAYMENT": "Overpayment",
             "SPEND-OVERPAYMENT": "Overpayment", "RECEIVE-PREPAYMENT": "Prepayment", "SPEND-PREPAYMENT": "Prepayment"}
BANK_ID = {"Overpayment": "OverpaymentID", "Prepayment": "PrepaymentID", "BankTransaction": "BankTransactionID"}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class _Unavailable(Exception):
    """A record names something the books do not hold (a system account Xero does not have, say)."""


def amount_text(units: int) -> str:
    """Integer minor units as Xero's decimal text, signed (a journal line's debit is positive)."""
    return f"{Decimal(units).scaleb(-2):.2f}"


def marked(text: str, marker: str, limit: int) -> str:
    """The text cut to fit `limit` with the Tico marker at its end, so the marker is always whole."""
    if not marker:
        return text[:limit]
    return f"{text[:max(limit - len(marker) - 1, 0)].rstrip()} {marker}".lstrip()


def reference_in(text: str) -> str:
    """The write reference a Tico marker in a record's note carries ("" when the note has none)."""
    found = MARK.search(text)
    return found.group(1) if found else ""


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


def _ext(kind: str, j: Mapping[str, Any], id_key: str, *, text: str = "") -> M.Ext:
    """A record's identity: its id, its UTC revision, and the write reference its note carries (`text`)."""
    ident = str(j.get(id_key) or "")
    if not ident:
        raise ValueError(f"no {id_key}")
    return M.Ext(kind, ident, stamp(j.get("UpdatedDateUTC")), reference=reference_in(text))


def _status(j: Mapping[str, Any]) -> str:
    return str(j.get("Status") or "")


def _account(item: Mapping[str, Any]) -> str:
    return f"Account:{item.get('AccountID') or ''}"


def _contact(j: Mapping[str, Any]) -> str:
    contact = as_map(j.get("Contact")) or {}
    return f"Contact:{contact['ContactID']}" if contact.get("ContactID") else ""


def _foreign(j: Mapping[str, Any]) -> str:
    """Why a Xero record is in another currency than the books hold a rate for (empty when it is not): its rate is not
    1, or its bank amount differs from its amount."""
    rate = j.get("CurrencyRate")
    if rate is not None and Decimal(str(rate)) != 1:
        return f"it is in another currency at rate {rate}, which the books hold no rate for"
    if j.get("BankAmount") is not None and j.get("Amount") is not None and j["BankAmount"] != j["Amount"]:
        return "its bank amount differs from its amount, so it is in another currency"
    return ""


def _net(item: Mapping[str, Any], inclusive: bool) -> tuple[int, int]:
    """A line's amount without its tax, and its tax: an inclusive line carries its tax inside its amount."""
    gross, due = minor(item.get("LineAmount") or 0), minor(item.get("TaxAmount") or 0)
    return (gross - due if inclusive else gross), due


def _option(key: object) -> str:
    """A tracking option's Xero pair, "category/option", from its provider key."""
    return str(key or "").removeprefix("TrackingOption:")


def _tags(item: Mapping[str, Any], system: System | None) -> tuple[tuple[str, str], ...]:
    """A line's tracking options as tags: the location's option is a location, any other option is a class. An option
    the books do not hold raises: dropping it would take the line in without the tag it carries, and the books would
    lose the tag the line already has."""
    if system is None:
        return ()
    out: list[tuple[str, str]] = []
    for raw in as_list(item.get("Tracking")):
        option = as_map(raw) or {}
        name = f"{option.get('Name') or ''}/{option.get('Option') or ''}"
        key = system(f"option:{name}")
        if not key:
            raise _Unavailable(f"Xero holds no tracking option {name}, so the line cannot be taken in")
        out.append(("location" if _option(key).partition("/")[0] == system("location") else "class", key))
    return tuple(out)


# A line's item as Xero names it (its ItemCode): the books find the item by that code.
ITEM_CODE_KEY = "code:"


def _lines(rows: object, inclusive: bool, sign: int = 1, party: str = "",
           system: System | None = None, codes: bool = False) -> tuple[list[M.EntryLine], int]:
    """A record's lines on their accounts (signed by `sign`) without their tax, and the tax they carry in all. With
    `codes`, a line that names an item carries it as ITEM_CODE_KEY plus its ItemCode."""
    lines: list[M.EntryLine] = []
    tax = 0
    for raw in as_list(rows):
        item = as_map(raw) or {}
        net, due = _net(item, inclusive)
        tax += due
        code = str(item.get("ItemCode") or "") if codes else ""
        lines.append(M.EntryLine(_account(item), sign * net, party=party, description=str(item.get("Description") or ""),
                                 tags=_tags(item, system), item=f"{ITEM_CODE_KEY}{code}" if code else ""))
    return lines, tax


def _ref(key: object, kind: str) -> str:
    """The Xero id a provider key holds, which must be of `kind` ("Invoice:5" is 5 for an invoice)."""
    prefix, _, value = str(key or "").partition(":")
    if prefix != kind or not value:
        raise ValueError(f"not a Xero {kind.lower()}: {key!r}")
    return value


def account_record(j: Mapping[str, Any]) -> M.ChartAccount | M.Ext | M.Refused:
    """An account, keyed by its id: Xero's code is optional, so an account without one gets a placeholder code. An
    account with no class waits; a deleted one is made inactive."""
    ext = _ext("Account", j, "AccountID")
    if _status(j) == "DELETED":
        return replace(ext, deleted=True)
    kind = ACCOUNT_KIND.get(str(j.get("Class") or ""))
    if kind is None:
        return M.Refused(ext, f"Xero's account class {j.get('Class')!r} is not one the books take in")
    code = str(j.get("Code") or "") or f"X-{ext.external_id}"
    name = str(j.get("Name") or "") or code
    bank = str(j.get("BankAccountType") or "") == "BANK" or str(j.get("Type") or "") == "BANK"
    subtype = "bank" if bank else SYSTEM_SUBTYPE.get(str(j.get("SystemAccount") or ""), "")
    return M.ChartAccount(ext, code, name, kind, subtype, _status(j) != "ARCHIVED")


def contact_record(j: Mapping[str, Any]) -> M.Party:
    """A contact. Xero decides whether it is a customer or a supplier from its activity; with neither flag set the kind
    is "", so the books keep the kind they hold."""
    ext = _ext("Contact", j, "ContactID")
    customer, vendor = bool(j.get("IsCustomer")), bool(j.get("IsSupplier"))
    kind = "both" if customer and vendor else "customer" if customer else "vendor" if vendor else ""
    email = str(j.get("EmailAddress") or "")
    name = str(j.get("Name") or j.get("ContactNumber") or ext.external_id)
    return M.Party(ext, name, kind, active=str(j.get("ContactStatus") or "ACTIVE") == "ACTIVE",
                   emails=(email,) if email else ())


def _system(system: System, name: str, what: str) -> str:
    key = system(name)
    if not key:
        raise _Unavailable(f"Xero holds no {name} account, so {what} cannot be taken in")
    return key


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


def document_record(j: Mapping[str, Any], system: System, credit: bool = False) -> M.Document | M.Ext | M.Refused | None:
    """An invoice, a bill (both Xero Invoices), or a credit note of either side (Xero CreditNotes). A draft or a submitted
    one waits; a voided one is a deleted document, its entry reversed. Its tax is its own amount on the sales tax account,
    and its rounding (what Total holds beyond its lines and tax) is a line of its own on the rounding account. A line Xero
    puts on the rounding account is that rounding too, so it may be negative."""
    kind = DOC_KIND.get(str(j.get("Type") or ""))
    if kind is None or (kind in CREDIT_KINDS) != credit:
        return None
    ext = _ext(kind, j, "CreditNoteID" if credit else "InvoiceID", text=str(j.get("Reference") or ""))
    if _status(j) in ("DELETED", "VOIDED"):
        return replace(ext, deleted=True)
    if _status(j) in ("", "DRAFT", "SUBMITTED"):
        return None
    try:
        lines, tax = _lines(j.get("LineItems"), str(j.get("LineAmountTypes") or "") == "Inclusive", system=system,
                            codes=True)
        rounding_account = system("ROUNDING")
        lines = [replace(x, rounding=True) if rounding_account and x.account == rounding_account else x for x in lines]
        total = minor(j["Total"]) if j.get("Total") is not None else sum(x.amount for x in lines) + tax
        rounded = total - sum(x.amount for x in lines) - tax
        if rounded:
            if abs(rounded) > ROUNDING_LIMIT:
                raise _Unavailable(f"its rounding of {amount_text(rounded)} is more than the books take in")
            lines.append(M.EntryLine(_system(system, "ROUNDING", "its rounding"), rounded, description="rounding",
                                     rounding=True))
    except _Unavailable as e:
        return M.Refused(ext, str(e))
    return M.Document(ext, party=_contact(j), number=str(j.get("CreditNoteNumber" if credit else "InvoiceNumber") or ""),
                      day=day(j, "Date"), due=day(j, "DueDate") if j.get("DueDate") else "", total=total,
                      lines=tuple(lines), memo=str(j.get("Reference") or ""), tax=tax,
                      currency=str(j.get("CurrencyCode") or ""))


def credit_application(j: Mapping[str, Any]) -> M.Settlement | M.Ext | None:
    """The applications a credit note makes, as one payment of nothing: each invoice or bill it is allocated to is settled
    with the credit note's own entry (Xero keeps no payment for a credit note's allocations, so the note is the record).
    A deleted or voided note clears what it applied before. A note with no allocations left applies nothing, and that
    empty state is a revision, not a deletion, so a later allocation is taken as a change. A draft makes none."""
    kind_type = str(j.get("Type") or "")
    ident = str(j.get("CreditNoteID") or "")
    ext = M.Ext("Payment", f"CreditNote:{ident}", stamp(j.get("UpdatedDateUTC")))
    if _status(j) in ("DELETED", "VOIDED"):
        return replace(ext, deleted=True)
    if _status(j) not in ("AUTHORISED", "PAID"):
        return None
    applied = _allocations(j, CREDIT_SIDE.get(kind_type, ""))
    total = sum(amount for _, amount in applied)
    return M.Settlement(ext, day=day(j, "Date"), memo=str(j.get("Reference") or ""), party="", bank="", total=0,
                        links=applied,
                        credits=((f"{DOC_KIND.get(kind_type, 'CreditMemo')}:{ident}", total),) if applied else ())


def payment_record(j: Mapping[str, Any], system: System) -> M.Settlement | M.Journal | M.Ext | M.Refused | None:
    """A payment as the books take it, by Xero's PaymentType: a bill's or an invoice's money (a settlement), or a refund of
    a credit note, an overpayment or a prepayment (a journal between the bank and its control account). A payment in
    another currency waits; a type the books do not know waits."""
    ptype = str(j.get("PaymentType") or "")
    target = as_map(j.get("Invoice")) or {}
    kind = PAYMENT_KIND.get(ptype, "BillPayment" if target.get("Type") == "ACCPAY" else "Payment")
    ext = _ext(kind, j, "PaymentID", text=str(j.get("Reference") or ""))
    if _status(j) == "DELETED":
        return replace(ext, deleted=True)
    if ptype not in PAYMENT_KIND and ptype not in REFUND_LINES:
        return M.Refused(ext, f"Xero's payment type {ptype or 'none'} is not one the books take in yet")
    if _status(j) != "AUTHORISED":
        return None
    why = _foreign(j)
    if why:
        return M.Refused(ext, why)
    memo = str(j.get("Reference") or "")
    amount = minor(j.get("Amount") or 0)
    if ptype in REFUND_LINES:
        control, debit, credit_kind = REFUND_LINES[ptype]
        note = as_map(j.get("CreditNote")) or {}
        if credit_kind and not note.get("CreditNoteID"):
            return M.Refused(ext, "the refund names no credit note")
        try:
            key = _system(system, control, "a refund")
        except _Unavailable as e:
            return M.Refused(ext, str(e))
        account = as_map(j.get("Account")) or {}
        bank = f"Account:{account.get('AccountID') or ''}"
        lines = (M.EntryLine(key, debit * amount, description=memo), M.EntryLine(bank, -debit * amount, description=memo))
        return M.Journal(ext, day(j, "Date"), memo, "sync", lines,
                         refund_of=f"{credit_kind}:{note['CreditNoteID']}" if credit_kind else "")
    side = "Invoice" if ptype.startswith(("ACCREC", "AR")) else "Bill"
    if not target.get("InvoiceID"):
        return M.Refused(ext, "the payment names no invoice or bill")
    account = as_map(j.get("Account")) or {}
    return M.Settlement(ext, day=day(j, "Date"), memo=memo, party="", bank=f"Account:{account.get('AccountID') or ''}",
                        total=amount, links=((f"{side}:{target['InvoiceID']}", amount),))


def applied_record(j: Mapping[str, Any], kind: str) -> M.Settlement | M.Ext | M.Refused | None:
    """An overpayment's or prepayment's allocations as the applications the books take: the credit it has applied to each
    invoice or bill it names. A deleted allocation applies nothing, and removing every allocation is a revision with
    nothing applied; only the overpayment's own status deletes it."""
    ident = str(j.get(BANK_ID[kind]) or "")
    ext = M.Ext("Payment", f"{kind}:{ident}", stamp(j.get("UpdatedDateUTC")))
    if _status(j) in ("DELETED", "VOIDED"):
        return replace(ext, deleted=True)
    why = _foreign(j)
    if why:
        return M.Refused(ext, why)
    links: list[tuple[str, int]] = []
    for raw in as_list(j.get("Allocations")):
        row = as_map(raw) or {}
        if row.get("IsDeleted") is True:
            continue
        invoice = as_map(row.get("Invoice")) or {}
        if not invoice.get("InvoiceID"):
            return M.Refused(ext, "an allocation names no invoice or bill")
        named = str(invoice.get("Type") or j.get("Type") or "")     # a nested invoice may omit its Type
        side = "Invoice" if named.startswith(("ACCREC", "RECEIVE")) else "Bill"
        links.append((f"{side}:{invoice['InvoiceID']}", minor(row.get("Amount") or 0)))
    return M.Settlement(ext, day=day(j, "Date"), memo=str(j.get("Reference") or ""), party="", bank="", total=0,
                        links=tuple(links),
                        credits=((f"{kind}:{ident}", sum(amount for _, amount in links)),) if links else ())


def manual_journal_record(j: Mapping[str, Any], system: System) -> M.Journal | M.Ext | M.Refused | None:
    """A manual journal. Tax is taken out of a line's amount only when the journal is Inclusive of it; an Exclusive or
    NoTax journal keeps its amounts and its tax is posted on the sales tax account on top. A journal Xero keeps out of its
    cash-basis reports is accrual only."""
    ext = _ext("ManualJournal", j, "ManualJournalID", text=str(j.get("Narration") or ""))
    if _status(j) in ("VOIDED", "DELETED", "ARCHIVED"):
        return replace(ext, deleted=True)
    if _status(j) != "POSTED":
        return None
    try:
        lines, tax = _lines(j.get("JournalLines"), str(j.get("LineAmountTypes") or "") == "Inclusive", system=system)
    except _Unavailable as e:
        return M.Refused(ext, str(e))
    if tax:
        lines.append(M.EntryLine(M.TAX_ACCOUNT, tax, description="tax"))
    basis = "accrual" if j.get("ShowOnCashBasisReports") is False else "both"
    return M.Journal(ext, day(j, "Date"), str(j.get("Narration") or ""), "sync", tuple(lines), basis)


def bank_record(j: Mapping[str, Any], system: System) -> M.Journal | M.Ext | M.Refused | None:
    """A bank transaction as an entry on its bank account: each line item on its account (net of its tax when the line is
    inclusive), its tax on the sales tax account, and the bank account the rest. An overpayment or a prepayment has no
    line items: its money is against the debtors or creditors control account, and the payment that applies it names it.
    A transfer's legs are none: a bank transfer carries both, once."""
    kind_type = str(j.get("Type") or "")
    if kind_type.endswith("-TRANSFER"):
        return None
    kind = BANK_KIND.get(kind_type)
    if kind is None:
        return M.Refused(_ext("BankTransaction", j, "BankTransactionID", text=str(j.get("Reference") or "")),
                         f"Xero's bank transaction type {kind_type or 'none'} is not one the books take in yet")
    ext = _ext(kind, j, BANK_ID[kind], text=str(j.get("Reference") or ""))
    if _status(j) in ("DELETED", "VOIDED"):
        return replace(ext, deleted=True)
    if _status(j) != "AUTHORISED":
        return None
    why = _foreign(j)
    if why:
        return M.Refused(ext, why)
    sign = -1 if kind_type.startswith("RECEIVE") else 1
    party = _contact(j)
    try:
        contra, tax = _lines(j.get("LineItems"), str(j.get("LineAmountTypes") or "") == "Inclusive", sign, party,
                             system=system)
    except _Unavailable as e:
        return M.Refused(ext, str(e))
    if tax:
        contra.append(M.EntryLine(M.TAX_ACCOUNT, sign * tax, party=party, description="tax"))
    if not contra and kind != "BankTransaction":
        control = "DEBTORS" if sign < 0 else "CREDITORS"
        try:
            contra.append(M.EntryLine(_system(system, control, "an overpayment or a prepayment"),
                                      sign * minor(j.get("Total") or 0), party=party))
        except _Unavailable as e:
            return M.Refused(ext, str(e))
    if not contra:
        return M.Refused(ext, "the bank transaction names no account to post against")
    bank = as_map(j.get("BankAccount")) or {}
    lines = (M.EntryLine(f"Account:{bank.get('AccountID') or ''}", -sum(x.amount for x in contra)), *contra)
    return M.Journal(ext, day(j, "Date"), str(j.get("Reference") or ""), "bank", lines,
                     currency=str(j.get("CurrencyCode") or ""))


def transfer_record(j: Mapping[str, Any]) -> M.Journal | M.Ext | M.Refused | None:
    """A bank transfer: money leaves its From account and lands on its To account. Xero's transfer has no revision of its
    own, so its revision is its creation, and only a deletion changes it."""
    ident = str(j.get("BankTransferID") or "")
    if not ident:
        raise ValueError("no BankTransferID")
    ext = M.Ext("BankTransfer", ident, stamp(j.get("CreatedDateUTC")))
    if _status(j) == "DELETED":
        return replace(ext, deleted=True)
    if _status(j) != "AUTHORISED":
        return None
    why = _foreign(j)
    if why:
        return M.Refused(ext, why)
    source = as_map(j.get("FromBankAccount")) or {}
    target = as_map(j.get("ToBankAccount")) or {}
    amount = minor(j.get("Amount") or 0)
    lines = (M.EntryLine(f"Account:{target.get('AccountID') or ''}", amount, description="transfer"),
             M.EntryLine(f"Account:{source.get('AccountID') or ''}", -amount, description="transfer"))
    return M.Journal(ext, day(j, "Date"), str(j.get("Reference") or ""), "bank", lines)


def _one(made: Record | None) -> Built:
    return [] if made is None else [made]


def item_record(j: Mapping[str, Any], system: System) -> M.Item | M.Ext:
    """An item, keyed by its id, with the accounts it sells and buys on (found by their codes). Xero's item has no kind but
    its inventory flag, so an item tracked as inventory is inventory and any other is a service."""
    sales = as_map(j.get("SalesDetails")) or {}
    purchase = as_map(j.get("PurchaseDetails")) or {}
    price = sales.get("UnitPrice")
    return M.Item(_ext("Item", j, "ItemID"), str(j.get("Name") or j.get("Code") or ""), sku=str(j.get("Code") or ""),
                  kind="inventory" if j.get("IsTrackedAsInventory") else "service",
                  price=None if price is None else minor(price), income=_code(sales.get("AccountCode"), system),
                  expense=_code(purchase.get("AccountCode"), system),
                  asset=_code(j.get("InventoryAssetAccountCode"), system))


def _code(code: object, system: System) -> str:
    return system(f"code:{code}") if code else ""


def tracking_records(j: Mapping[str, Any], system: System) -> list[Record]:
    """A tracking category's options: the location category's options are locations, the other categories' are classes.
    Each option is keyed by its category and its id, so a location and a class never share a key. An archived option, or
    any option of an archived category, is kept as inactive: a bill or journal line on it still resolves on import, and it
    is not offered for a new write. Its revision follows its name and status, so a rename or an archive is a change the
    books take."""
    category = str(j.get("TrackingCategoryID") or "")
    if not category:
        return []
    category_live = _status(j) == "ACTIVE"
    as_location = system("location") == category
    out: list[Record] = []
    for raw in as_list(j.get("Options")):
        option = as_map(raw) or {}
        option_id = str(option.get("TrackingOptionID") or "")
        if not option_id:
            continue
        name = str(option.get("Name") or "")
        live = category_live and str(option.get("Status") or "ACTIVE") == "ACTIVE"
        # Xero gives an option no revision, so the books make one from this digest when they take the option in.
        digest = hashlib.sha256(f"{name}|{'ACTIVE' if live else 'ARCHIVED'}".encode()).hexdigest()[:16]
        when = stamp(option.get("UpdatedDateUTC"))
        ext = M.Ext("TrackingOption", f"{category}/{option_id}", when, content="" if when else digest)
        out.append(M.Location(ext, name, active=live) if as_location else M.Dimension(ext, name, active=live))
    return out


def _build(entity: str, j: Mapping[str, Any], system: System) -> Built:
    if entity == "accounts":
        return [account_record(j)]
    if entity == "parties":
        return [contact_record(j)]
    if entity == "items":
        return [item_record(j, system)]
    if entity == "tracking":
        return tracking_records(j, system)
    if entity == "invoices":
        return _one(document_record(j, system))
    if entity == "credit_notes":
        made = [document_record(j, system, credit=True), credit_application(j)]
        return [x for x in made if x is not None]
    if entity == "payments":
        return _one(payment_record(j, system))
    if entity == "manual_journals":
        return _one(manual_journal_record(j, system))
    if entity == "bank_transactions":
        return _one(bank_record(j, system))
    if entity in ("overpayments", "prepayments"):
        return _one(applied_record(j, "Overpayment" if entity == "overpayments" else "Prepayment"))
    if entity == "bank_transfers":
        return _one(transfer_record(j))
    raise ValueError(f"not a Xero entity: {entity}")


def records(entity: str, rows: Iterable[object], system: System) -> list[Record]:
    """The records a page or a change read of one entity holds, in order: a row that is not taken in gives none."""
    out: list[Record] = []
    for raw in rows:
        j = as_map(raw)
        if j is None:
            raise ValueError(f"a {entity} row is not an object")
        out += _build(entity, j, system)
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
    """An account as Xero takes it: a bank account is Xero's bank type, every other one its class's type."""
    out: dict[str, Any] = {"Code": str(body.get("code") or ""), "Name": str(body.get("name") or ""),
                           "Status": "ACTIVE" if body.get("active", True) else "ARCHIVED"}
    if body.get("type") == "asset" and body.get("subtype") == "bank":
        out.update({"Type": "BANK", "BankAccountType": "BANK"})
    else:
        out["Type"] = WRITE_TYPE[str(body["type"])]
    return out


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
                               "AccountID": _ref(line.get("account"), "Account"),
                               "Description": str(line.get("description") or "")[:4000]}
        tracking = _tracking({**(as_map(line.get("classes")) or {}), "location": _option(line.get("location"))})
        if tracking:
            row["Tracking"] = tracking
        lines.append(row)
    return {"Narration": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 500),
            "Date": str(body["date"]), "Status": "POSTED", "LineAmountTypes": "NoTax", "JournalLines": lines}


def bank_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A categorized bank row as a bank transaction: the money line's account is the bank account, and the other line is
    its one line item. Xero's type gives the direction (SPEND takes money out, RECEIVE brings it in). Xero requires a
    contact on a bank transaction: the line's counterparty when it has one, else the contact the write names (the
    books' Unspecified contact)."""
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
    row: dict[str, Any] = {"AccountID": _ref(item.get("account"), "Account"), "LineAmount": amount_text(abs(amount)),
                           "Description": str(item.get("description") or "")[:4000]}
    tracking = _tracking({**(as_map(item.get("classes")) or {}), "location": _option(item.get("location"))})
    if tracking:
        row["Tracking"] = tracking
    party = str(item.get("party") or "")
    contact = _ref(party, "Contact") if party else str(body.get("contact") or "")
    if not contact:
        raise ValueError("a bank transaction names no contact")
    return {"Type": "SPEND" if amount < 0 else "RECEIVE",
            "BankAccount": {"AccountID": _ref(money.get("account"), "Account")}, "Contact": {"ContactID": contact},
            "Date": str(body["date"]), "Status": "AUTHORISED", "LineAmountTypes": "NoTax",
            "Reference": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 1000),
            "LineItems": [row]}


def void_body(ident: str) -> dict[str, Any]:
    """A manual journal voided: Xero keeps the record and takes it out of the books."""
    return {"ManualJournals": [{"ManualJournalID": ident, "Status": "VOIDED"}]}


def document_body(body: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """A bill or an invoice as Xero takes it: an authorised document on its contact, each line on its account at its amount
    (a quantity of one, so the line amount is the amount), tagged with its location and class when it names them. The
    lines
    are sent without tax, as the books hold them."""
    lines: list[dict[str, Any]] = []
    for raw in as_list(body.get("lines")):
        line = as_map(raw) or {}
        row: dict[str, Any] = {"AccountID": _ref(line.get("account"), "Account"),
                               "Description": str(line.get("description") or "")[:4000], "Quantity": 1,
                               "UnitAmount": amount_text(int(line["amount"]))}
        if line.get("item_code"):      # the item the line sells, by the code Xero holds it under (see item_body)
            row["ItemCode"] = str(line["item_code"])[:30]
        tracking = _tracking({"location": _option(line.get("location")), "class": _option(line.get("class"))})
        if tracking:
            row["Tracking"] = tracking
        lines.append(row)
    out: dict[str, Any] = {"Type": "ACCREC" if kind == "Invoice" else "ACCPAY",
                           "Contact": {"ContactID": _ref(body.get("party"), "Contact")},
                           "Date": str(body["day"]), "DueDate": str(body.get("due") or body["day"]),
                           "Reference": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 255),
                           "Status": "AUTHORISED", "LineAmountTypes": "NoTax", "LineItems": lines}
    if body.get("number"):
        out["InvoiceNumber"] = str(body["number"])[:255]
    return {"Invoices": [out]}


def item_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """An item as Xero takes it, keyed by its code (the SKU, else the name). Its sale and purchase accounts are sent by
    the codes the adapter found for them; a side with no account or price is left off."""
    sales: dict[str, Any] = {}
    if body.get("income_code"):
        sales["AccountCode"] = str(body["income_code"])
    if body.get("price") is not None:
        sales["UnitPrice"] = amount_text(int(body["price"]))
    purchase = {"AccountCode": str(body["expense_code"])} if body.get("expense_code") else {}
    out: dict[str, Any] = {"Code": (str(body.get("sku") or "") or str(body["name"]))[:30],
                           "Name": str(body["name"])[:50], "IsSold": bool(sales), "IsPurchased": bool(purchase)}
    if sales:
        out["SalesDetails"] = sales
    if purchase:
        out["PurchaseDetails"] = purchase
    if body.get("kind") == "inventory":
        out["IsTrackedAsInventory"] = True
        if body.get("asset_code"):
            out["InventoryAssetAccountCode"] = str(body["asset_code"])
    return out


def void_document_body(ident: str) -> dict[str, Any]:
    """A bill or an invoice voided: Xero keeps the record and takes it out of the books."""
    return {"Invoices": [{"InvoiceID": ident, "Status": "VOIDED"}]}


def payment_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A payment of one bill or invoice from its bank account, on the day and for the amount it settles."""
    links = as_list(body.get("links"))
    if len(links) != 1:
        raise ValueError("a payment settles one bill or invoice")
    doc, amount = as_list(links[0])[0], int(as_list(links[0])[1])
    return {"Payments": [{"Invoice": {"InvoiceID": _ref(doc, _kind_of(doc))},
                          "Account": {"AccountID": _ref(body.get("bank"), "Account")},
                          "Date": str(body["day"]), "Amount": amount_text(amount),
                          "Reference": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 255)}]}


def _kind_of(key: object) -> str:
    return str(key or "").partition(":")[0]


def credit_note_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A write-off as a credit note to the write-off account: a receivable credit on its customer, one line for the amount."""
    total = int(body["total"])
    return {"CreditNotes": [{"Type": "ACCRECCREDIT", "Contact": {"ContactID": _ref(body.get("party"), "Contact")},
                             "Date": str(body["day"]),
                             "Reference": marked(str(body.get("memo") or ""), str(body.get("marker") or ""), 255),
                             "Status": "AUTHORISED", "LineAmountTypes": "NoTax",
                             "LineItems": [{"AccountID": _ref(body.get("account"), "Account"),
                                            "Description": str(body.get("memo") or "")[:4000], "Quantity": 1,
                                            "UnitAmount": amount_text(total)}]}]}


def allocation_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A credit note applied to an invoice for an amount on a day."""
    return {"Allocations": [{"Invoice": {"InvoiceID": _ref(body["invoice"], _kind_of(body["invoice"]))},
                             "Amount": amount_text(int(body["amount"])), "Date": str(body["day"])}]}
