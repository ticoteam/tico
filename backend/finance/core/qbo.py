"""QuickBooks Online shapes (spec §10): the JSON the adapter reads from QuickBooks and writes to it, as mirror records and
request bodies. No I/O (the HTTP client is finance/qbo.py). QuickBooks sends money as decimal text, so amounts go through
Decimal and come out as integer minor units; nothing here is a float. A deleted row (CDC marks it "Deleted") is its
identity only, and the books take it as a deletion."""
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from . import mirror as M

MINOR_VERSION = "65"
# The QuickBooks entities that post to the books' ledger without being a journal entry, each as one balanced entry.
POSTED = ("Purchase", "Deposit", "Transfer", "SalesReceipt", "RefundReceipt", "VendorCredit")
# Every QuickBooks entity the change feed reads.
ENTITIES = ("Account", "Vendor", "Customer", "JournalEntry", "Bill", "Invoice", "CreditMemo", "BillPayment", "Payment",
            *POSTED, "Item", "Department", "Class", "PurchaseOrder", "Estimate")
# The QuickBooks entities behind each backfill entity, in the order the books take them in (the mirror's ENTITIES).
PAGES: dict[str, tuple[str, ...]] = {
    "accounts": ("Account",), "parties": ("Vendor", "Customer"), "records": ("Item", "Department", "Class"),
    "documents": ("Bill", "Invoice", "CreditMemo", "PurchaseOrder", "Estimate"), "payments": ("BillPayment", "Payment"),
    "entries": ("JournalEntry", *POSTED)}
# The entities a query reads with WHERE Active IN (true,false): QuickBooks reads only the active ones otherwise.
ACTIVE_FILTERED = ("Account", "Vendor", "Customer", "Item", "Employee", "Department", "Class")
# QuickBooks item Type -> the books' item kind. An item of another type is not taken in.
ITEM_KINDS = {"Service": "service", "Inventory": "inventory", "NonInventory": "non_inventory", "Group": "bundle",
              "Category": "category"}
DOCUMENTS = ("Bill", "Invoice", "CreditMemo")
# Purchase orders and estimates: documents with no entry. Each one's status as the books keep it, by QuickBooks' own.
ORDERS = ("PurchaseOrder", "Estimate")
ORDER_STATUS = {"PurchaseOrder": {"Open": "approved", "Closed": "closed"},
                "Estimate": {"Pending": "sent", "Accepted": "accepted", "Rejected": "declined", "Closed": "accepted"}}
PAYMENTS = ("BillPayment", "Payment")
# A document line's detail. Its account is its own (a purchase on an account) or its item's: income for a sale, expense
# for a purchase on an item.
LINE_DETAILS = ("AccountBasedExpenseLineDetail", "ItemBasedExpenseLineDetail", "SalesItemLineDetail")
# The lines a bill carries as expenses: each one is a line the bill sent, with its own id.
EXPENSE_LINES = ("AccountBasedExpenseLineDetail", "ItemBasedExpenseLineDetail")
# QuickBooks AccountType -> Tico (type, subtype). Subtypes are only the ones the default chart uses.
TYPES: dict[str, tuple[str, str]] = {
    "Bank": ("asset", "bank"), "Accounts Receivable": ("asset", "receivable"), "Other Current Asset": ("asset", ""),
    "Fixed Asset": ("asset", ""), "Other Asset": ("asset", ""),
    "Accounts Payable": ("liability", "payable"), "Credit Card": ("liability", ""),
    "Other Current Liability": ("liability", ""),
    "Long Term Liability": ("liability", ""), "Equity": ("equity", ""), "Income": ("income", ""),
    "Other Income": ("income", "other_income"), "Cost of Goods Sold": ("expense", ""), "Expense": ("expense", ""),
    "Other Expense": ("expense", "other_expense"),
}
# Payroll expenses, and the liabilities and clearing accounts wages pass through: their lines are private as payroll is
# (spec §7.5), so the bank row that pays wages is private with the expense it came from.
PAYROLL_SUBTYPES = frozenset({"PayrollExpenses", "PayrollClearing", "PayrollTaxPayable"})
_OUT: dict[tuple[str, str], str] = {("asset", "bank"): "Bank", ("income", "other_income"): "Other Income",
                                    ("expense", "other_expense"): "Other Expense",
                                    ("liability", "payable"): "Accounts Payable",
                                    ("asset", "receivable"): "Accounts Receivable"}
_DEFAULT_OUT = {"asset": "Other Current Asset", "liability": "Other Current Liability", "equity": "Equity",
                "income": "Income", "expense": "Expense"}
Made = (M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Item | M.Location | M.Dimension | M.Project
        | M.Order | M.Ext | M.Refused)


def as_map(value: object) -> dict[str, Any] | None:
    """A JSON object as a dict, or None: the one place a value from QuickBooks is narrowed to a row."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def as_list(value: object) -> list[Any]:
    """A JSON array as a list, or an empty one."""
    return cast(list[Any], value) if isinstance(value, list) else []


def minor(value: object) -> int:
    """A QuickBooks decimal (text or number) as integer minor units, rounded to the cent. Raises ValueError on junk."""
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        raise ValueError(f"not an amount: {value!r}") from None


def decimal_text(units: int) -> str:
    """Integer minor units as QuickBooks wants them: two decimals, no sign (the posting type carries it)."""
    return f"{Decimal(abs(units)).scaleb(-2):.2f}"


def _id(value: object) -> str:
    return str(value or "").strip()


def _ref(row: Mapping[str, Any], key: str) -> str:
    """The id a reference field holds (`{"value": "33", "name": ...}`), or ""."""
    ref = as_map(row.get(key))
    return _id(ref.get("value")) if ref is not None else ""


def _units(value: object) -> int | None:
    """A QuickBooks amount as minor units, or None when it is not an amount."""
    try:
        return minor(value if value is not None else 0)
    except ValueError:
        return None


def account_type(tico_type: str, subtype: str) -> str:
    """The QuickBooks AccountType for a Tico account: its subtype when QuickBooks has one for it, else its type's."""
    return _OUT.get((tico_type, subtype)) or _DEFAULT_OUT.get(tico_type, "Expense")


def account_record(row: dict[str, Any]) -> M.ChartAccount | None:
    """A QuickBooks Account as a chart account; None for a type this chart cannot hold."""
    ext = M.Ext("Account", _id(row.get("Id")), _id(row.get("SyncToken")))
    mapped = TYPES.get(_id(row.get("AccountType")))
    if mapped is None:
        return None
    code = _id(row.get("AcctNum")) or f"QB{ext.external_id}"
    subtype = mapped[1]
    if _id(row.get("AccountSubType")) == "SalesTaxPayable":
        subtype = M.SALES_TAX           # where a taxed record's tax lands (the books' sales tax account)
    return M.ChartAccount(ext, code=code, name=_id(row.get("Name")), type=mapped[0], subtype=subtype,
                          active=bool(row.get("Active", True)),
                          payroll=_id(row.get("AccountSubType")) in PAYROLL_SUBTYPES)


def party_record(row: dict[str, Any], kind: str) -> M.Party:
    """A QuickBooks Vendor (kind "Vendor") or Customer (kind "Customer") as a party."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    address = as_map(row.get("PrimaryEmailAddr"))
    emails = (_id(address.get("Address")),) if address is not None and address.get("Address") else ()
    return M.Party(ext, name=_id(row.get("DisplayName")) or _id(row.get("CompanyName")),
                   kind="vendor" if kind == "Vendor" else "customer", active=bool(row.get("Active", True)),
                   emails=emails)


def _active(row: Mapping[str, Any]) -> bool:
    return bool(row.get("Active", True))


def _account_key(row: Mapping[str, Any], key: str) -> str:
    """An account reference on a record (an item's income, expense or asset account) as the provider's key, or ""."""
    value = _ref(row, key)
    return f"Account:{value}" if value else ""


def _quantity(value: object) -> str:
    """QuickBooks' QtyOnHand as decimal text without trailing zeros; "" when it sends none (a plain item has none)."""
    if value in (None, ""):
        return ""
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except InvalidOperation:
        return ""


def item_record(row: dict[str, Any]) -> M.Item | None:
    """A QuickBooks Item as a product or service; None for an item type the books do not hold. Its price is its unit
    price (none when QuickBooks keeps none). Its quantity on hand is read here only: nothing is written back to it."""
    ext = M.Ext("Item", _id(row.get("Id")), _id(row.get("SyncToken")))
    kind = ITEM_KINDS.get(_id(row.get("Type")))
    if kind is None:
        return None
    price = _units(row.get("UnitPrice")) if row.get("UnitPrice") not in (None, "") else None
    return M.Item(ext, name=_id(row.get("Name")), sku=_id(row.get("Sku")), kind=kind, price=price,
                  income=_account_key(row, "IncomeAccountRef"), expense=_account_key(row, "ExpenseAccountRef"),
                  asset=_account_key(row, "AssetAccountRef"), active=_active(row),
                  description=_id(row.get("Description")), quantity=_quantity(row.get("QtyOnHand")))


def location_record(row: dict[str, Any]) -> M.Location:
    """A QuickBooks Department as a location."""
    return M.Location(M.Ext("Department", _id(row.get("Id")), _id(row.get("SyncToken"))), name=_id(row.get("Name")),
                   active=_active(row))


def dimension_record(row: dict[str, Any]) -> M.Dimension:
    """A QuickBooks Class as a class dimension."""
    return M.Dimension(M.Ext("Class", _id(row.get("Id")), _id(row.get("SyncToken"))), name=_id(row.get("Name")),
                       active=_active(row))


def project_record(row: dict[str, Any]) -> M.Project:
    """A QuickBooks job (a customer with Job set) as a project under its parent customer."""
    parent = _ref(row, "ParentRef")
    return M.Project(M.Ext("Project", _id(row.get("Id")), _id(row.get("SyncToken"))),
                     name=_id(row.get("DisplayName")) or _id(row.get("CompanyName")),
                     customer=f"Customer:{parent}" if parent else "", active=_active(row))


def _is_job(row: Mapping[str, Any]) -> bool:
    """A customer is a project when it is a job (a sub-customer with Job set) or a project made in the QuickBooks UI
    (IsProject, read when the provider sends it)."""
    return str(row.get("Job", False)).lower() == "true" or str(row.get("IsProject", False)).lower() == "true"


def journal_record(row: dict[str, Any]) -> M.Journal:
    """A QuickBooks JournalEntry as one balanced entry. Debits are positive; lines without an account are skipped."""
    ext = M.Ext("JournalEntry", _id(row.get("Id")), _id(row.get("SyncToken")))
    lines: list[M.EntryLine] = []
    for raw in as_list(row.get("Line")):
        line = as_map(raw)
        if line is None or line.get("DetailType") != "JournalEntryLineDetail":
            continue
        detail = as_map(line.get("JournalEntryLineDetail"))
        ref = as_map(detail.get("AccountRef")) if detail is not None else None
        if detail is None or ref is None or not _id(ref.get("value")):
            continue
        units = _units(line.get("Amount")) or 0            # junk makes the entry unbalanced: it waits as unmapped
        if _id(detail.get("PostingType")) == "Credit":
            units = -units
        party = ""
        entity = as_map(detail.get("Entity"))
        entity_ref = as_map(entity.get("EntityRef")) if entity is not None else None
        if entity is not None and entity_ref is not None:
            kind = _id(entity.get("Type"))
            if kind in ("Vendor", "Customer"):
                party = f"{kind}:{_id(entity_ref.get('value'))}"
        lines.append(M.EntryLine(account=f"Account:{_id(ref.get('value'))}", amount=units, party=party,
                                 description=_id(line.get("Description")), key=_id(line.get("Id")),
                                 tags=_dims(detail)))
    return M.Journal(ext, _id(row.get("TxnDate")), _memo(row), "sync", tuple(lines))


def _dims(detail: Mapping[str, Any], location: str = "") -> M.Tags:
    """A line's location, class and project as provider keys: its own DepartmentRef (a journal line's), else the
    transaction's (`location`), its ClassRef, and the job it is billed to (its CustomerRef)."""
    out: list[tuple[str, str]] = []
    where = _ref(detail, "DepartmentRef") or location
    if where:
        out.append(("location", f"Department:{where}"))
    cls = _ref(detail, "ClassRef")
    if cls:
        out.append(("class", f"Class:{cls}"))
    customer = _ref(detail, "CustomerRef")
    if customer:
        out.append(("project", f"Customer:{customer}"))
    return tuple(out)


def _doc_line(raw: object, items: Mapping[str, Mapping[str, str]], location: str = "") -> M.EntryLine | None:
    """One document line as an entry line on its account. None for a line that carries no money (a description, a
    subtotal). Tax and discount lines are not taken in, so a document that has them does not add up to its total and
    waits as unmapped (the mirror says so). `location` is the transaction's location, which each line inherits."""
    line = as_map(raw)
    if line is None:
        return None
    detail_type = _id(line.get("DetailType"))
    if detail_type not in LINE_DETAILS:
        return None
    detail: dict[str, Any] = as_map(line.get(detail_type)) or {}
    units = _units(line.get("Amount"))
    if units is None:
        return M.EntryLine(account="", amount=0, description=_id(line.get("Description")))
    if detail_type == "AccountBasedExpenseLineDetail":
        account = _ref(detail, "AccountRef")
    else:
        side = "expense" if detail_type == "ItemBasedExpenseLineDetail" else "income"
        found = items.get(_ref(detail, "ItemRef"))
        account = found.get(side, "") if found is not None else ""
    item = _ref(detail, "ItemRef")
    return M.EntryLine(account=f"Account:{account}" if account else "", amount=units,
                       description=_id(line.get("Description")), key=_id(line.get("Id")),
                       tags=_dims(detail, location), item=f"Item:{item}" if item else "")


def document_record(row: dict[str, Any], kind: str, items: Mapping[str, Mapping[str, str]]) -> M.Document:
    """A QuickBooks Bill, Invoice or CreditMemo as a document. A bill's party is its vendor; an invoice's and a credit
    memo's is their customer. `items` maps each item id to its income and expense accounts (see item_accounts). A credit
    memo's line links name the invoice it was made from, not an application: a credit applied to an invoice comes in as
    a payment (see credit_apply_body)."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    party_kind = "Vendor" if kind == "Bill" else "Customer"
    party_value = _ref(row, "VendorRef" if kind == "Bill" else "CustomerRef")
    location = _ref(row, "DepartmentRef")
    lines = tuple(x for x in (_doc_line(raw, items, location) for raw in as_list(row.get("Line"))) if x is not None)
    control = _ref(row, "APAccountRef" if kind == "Bill" else "ARAccountRef")
    return M.Document(ext, party=f"{party_kind}:{party_value}" if party_value else "",
                      number=_id(row.get("DocNumber")), day=_id(row.get("TxnDate")), due=_id(row.get("DueDate")),
                      total=_units(row.get("TotalAmt")) or 0, lines=lines, memo=_memo(row), tax=_tax(row),
                      control=f"Account:{control}" if control else "")


def order_record(row: dict[str, Any], kind: str, items: Mapping[str, Mapping[str, str]]) -> M.Order:
    """A QuickBooks PurchaseOrder (to a vendor) or Estimate (to a customer) as an order. Its lines are a bill's or an
    invoice's: a purchase order's line posts to its item's expense account, an estimate's to its income account."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    purchase = kind == "PurchaseOrder"
    party_value = _ref(row, "VendorRef" if purchase else "CustomerRef")
    party = f"{'Vendor' if purchase else 'Customer'}:{party_value}" if party_value else ""
    location = _ref(row, "DepartmentRef")
    lines = tuple(x for x in (_doc_line(raw, items, location) for raw in as_list(row.get("Line"))) if x is not None)
    status = ORDER_STATUS[kind].get(_id(row.get("POStatus" if purchase else "TxnStatus")), "")
    return M.Order(ext, party=party, number=_id(row.get("DocNumber")), day=_id(row.get("TxnDate")),
                   total=_units(row.get("TotalAmt")) or 0, lines=lines, memo=_memo(row), status=status,
                   tax=_tax(row))


def settlement_record(row: dict[str, Any], kind: str) -> M.Settlement:
    """A QuickBooks BillPayment (paid to a vendor) or Payment (received from a customer): its money account, party,
    total, and what each line applies to a bill (BillPayment) or an invoice (Payment)."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    credits: tuple[tuple[str, int], ...] = ()
    if kind == "Payment":
        bank, party_kind, party_value = _ref(row, "DepositToAccountRef"), "Customer", _ref(row, "CustomerRef")
        target = "Invoice"
        credits = _links(row, "CreditMemo")
    else:
        check: dict[str, Any] = as_map(row.get("CheckPayment")) or {}
        card: dict[str, Any] = as_map(row.get("CreditCardPayment")) or {}
        bank = _ref(check, "BankAccountRef") or _ref(card, "CCAccountRef")
        party_kind, party_value = "Vendor", _ref(row, "VendorRef")
        target = "Bill"
    return M.Settlement(ext, day=_id(row.get("TxnDate")), memo=_memo(row),
                        party=f"{party_kind}:{party_value}" if party_value else "",
                        bank=f"Account:{bank}" if bank else "", total=_units(row.get("TotalAmt")) or 0,
                        links=_links(row, target), credits=credits)


def _links(row: Mapping[str, Any], target: str) -> tuple[tuple[str, int], ...]:
    """What a payment or a credit memo applies to: each line's linked transaction of the target type ("Bill" or
    "Invoice") with that line's amount, as ("Bill:12", amount)."""
    out: list[tuple[str, int]] = []
    for raw in as_list(row.get("Line")):
        line = as_map(raw)
        if line is None:
            continue
        units = _units(line.get("Amount"))
        for txn_raw in as_list(line.get("LinkedTxn")):
            txn = as_map(txn_raw) or {}
            if _id(txn.get("TxnType")) == target and _id(txn.get("TxnId")) and units is not None:
                out.append((f"{target}:{_id(txn.get('TxnId'))}", units))
    return tuple(out)


def item_accounts(rows: Iterable[object]) -> dict[str, dict[str, Any]]:
    """Each item's income and expense account (QuickBooks keeps them on the item): what its lines post to, and whether
    the item is active (an inactive one is read for history, never sent on a new sale)."""
    out: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = as_map(raw)
        if row is None or not _id(row.get("Id")):
            continue
        out[_id(row.get("Id"))] = {"income": _ref(row, "IncomeAccountRef"), "expense": _ref(row, "ExpenseAccountRef"),
                                   "active": str(row.get("Active", True)).lower() != "false"}
    return out


def income_items(items: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """The item a sale on each income account is sent on: an active item over an inactive one that shares its account
    (the first found wins among those)."""
    out: dict[str, str] = {}
    for item_id, found in sorted(items.items(), key=lambda kv: (not kv[1].get("active", True), kv[0])):
        account = str(found.get("income") or "")
        if account and account not in out:
            out[account] = item_id
    return out


def _tax(row: Mapping[str, Any]) -> int:
    """A transaction's sales tax in minor units: QuickBooks keeps it in TxnTaxDetail, outside its lines."""
    detail = as_map(row.get("TxnTaxDetail")) or {}
    return _units(detail.get("TotalTax")) or 0


def _reference(row: Mapping[str, Any]) -> str:
    """The write reference in a record's private note, when one of Tico's writes made it."""
    found = REFERENCE.search(_id(row.get("PrivateNote")))
    return found.group(1) if found is not None else ""


def _one(entity: str, row: dict[str, Any], items: Mapping[str, Mapping[str, str]]) -> Made | None:
    if entity == "Account":
        return account_record(row)
    if entity == "Customer" and _is_job(row):
        return project_record(row)
    if entity in ("Vendor", "Customer"):
        return party_record(row, entity)
    if entity == "Item":
        return item_record(row)
    if entity == "Department":
        return location_record(row)
    if entity == "Class":
        return dimension_record(row)
    if entity not in ENTITIES:
        return None
    if entity == "JournalEntry":
        made: Made = journal_record(row)
    elif entity in DOCUMENTS:
        made = document_record(row, entity, items)
    elif entity in ORDERS:
        made = order_record(row, entity, items)
    elif entity in PAYMENTS:
        made = settlement_record(row, entity)
    else:
        made = posted_record(row, entity, items)
    code = _ref(row, "CurrencyRef")
    return replace(made, ext=replace(made.ext, currency=code)) if code else made


def _with_reference(made: Made, ref: str) -> Made:
    """A record with its write reference set (on its identity, for an Ext)."""
    if isinstance(made, M.Ext):
        return replace(made, reference=ref)
    return replace(made, ext=replace(made.ext, reference=ref))


def records(entity: str, rows: Iterable[object], items: Mapping[str, Mapping[str, str]] | None = None) -> list[Made]:
    """The mirror records for one QuickBooks entity's rows (a query page or a change block). Rows that are not objects
    are skipped; a deleted row keeps only its identity, which the books take as the deletion."""
    out: list[Made] = []
    for raw in rows:
        row = as_map(raw)
        if row is None:
            continue
        if _id(row.get("status")) == "Deleted":
            if _id(row.get("Id")):
                out.append(M.Ext(entity, _id(row.get("Id")), _id(row.get("SyncToken")), True))
            continue
        made = _one(entity, row, items or {})
        if made is not None:
            ref = _reference(row)
            out.append(_with_reference(made, ref) if ref else made)
    return out


CHANGE_CAP = 1000       # the most objects one change feed answer holds


def change_through(answer: Mapping[str, Any], entity: str) -> str:
    """Where one entity's change feed answer is complete to when it is cut at its cap (""  when it is whole): one second
    before the newest change of that entity in it, so a change made in that second is read again by the next read.
    Each entity is read in its own answer, so no other entity's changes move this boundary."""
    stamps: list[datetime] = []
    count = 0
    for block in as_list(answer.get("CDCResponse")):
        for query in as_list((as_map(block) or {}).get("QueryResponse")):
            for raw in as_list((as_map(query) or {}).get(entity)):
                count += 1
                meta = as_map((as_map(raw) or {}).get("MetaData")) or {}
                if meta.get("LastUpdatedTime"):
                    stamps.append(M.parse(str(meta["LastUpdatedTime"])))
    if count < CHANGE_CAP or not stamps:
        return ""
    return (max(stamps) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def change_records(answer: Mapping[str, Any], items: Mapping[str, Mapping[str, str]] | None = None) -> list[Made]:
    """The records in a CDC answer: {"CDCResponse": [{"QueryResponse": [{"Account": [...], ...}]}]}."""
    out: list[Made] = []
    for block in as_list(answer.get("CDCResponse")):
        found = as_map(block)
        if found is None:
            continue
        for query in as_list(found.get("QueryResponse")):
            page = as_map(query)
            if page is None:
                continue
            for entity in ENTITIES:
                out += records(entity, as_list(page.get(entity)), items)
    return out


def _scaled(lines: Sequence[M.EntryLine], sign: int) -> list[M.EntryLine]:
    return [replace(x, amount=sign * x.amount) for x in lines]


def _deposit_lines(row: Mapping[str, Any]) -> list[M.EntryLine]:
    """A deposit's lines, each an account it credits. A line that names no account (a received payment waiting in
    undeposited funds) reads as an empty account, so the entry is refused rather than posted short."""
    out: list[M.EntryLine] = []
    for raw in as_list(row.get("Line")):
        line = as_map(raw)
        if line is None or line.get("DetailType") != "DepositLineDetail":
            continue
        detail: dict[str, Any] = as_map(line.get("DepositLineDetail")) or {}
        units = _units(line.get("Amount"))
        account = _ref(detail, "AccountRef")
        out.append(M.EntryLine(f"Account:{account}" if account else "", units or 0,
                               description=_id(line.get("Description")), key=_id(line.get("Id"))))
    return out


def posted_record(row: dict[str, Any], kind: str, items: Mapping[str, Mapping[str, str]]) -> M.Journal | M.Refused:
    """A purchase, deposit, transfer, sales receipt, refund receipt or vendor credit as one balanced entry. The money
    side is the bank account (or payables, for a vendor credit) for the total, and each line is an account it posts to.
    A document's tax is one more line on the sales tax account, with the sign of its lines. A purchase marked Credit is a
    refund, so its entry is reversed. Lines that do not add up to the total are refused, so nothing is posted short."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    total = _units(row.get("TotalAmt")) or 0
    location = _ref(row, "DepartmentRef")
    doc_lines = [x for x in (_doc_line(raw, items, location) for raw in as_list(row.get("Line"))) if x is not None]
    doc_sign = -1 if kind in ("SalesReceipt", "VendorCredit") else 1
    if kind == "Transfer":
        amount = _units(row.get("Amount")) or 0
        lines = [M.EntryLine(f"Account:{_ref(row, 'ToAccountRef')}", amount),
                 M.EntryLine(f"Account:{_ref(row, 'FromAccountRef')}", -amount)]
    elif kind == "Deposit":
        lines = [M.EntryLine(f"Account:{_ref(row, 'DepositToAccountRef')}", total),
                 *_scaled(_deposit_lines(row), -1)]
    elif kind == "Purchase":
        lines = [M.EntryLine(f"Account:{_ref(row, 'AccountRef')}", -total), *doc_lines]
    elif kind == "SalesReceipt":
        lines = [M.EntryLine(f"Account:{_ref(row, 'DepositToAccountRef')}", total), *_scaled(doc_lines, -1)]
    elif kind == "RefundReceipt":
        lines = [M.EntryLine(f"Account:{_ref(row, 'DepositToAccountRef')}", -total), *doc_lines]
    else:
        party = f"Vendor:{_ref(row, 'VendorRef')}" if _ref(row, "VendorRef") else ""
        lines = [M.EntryLine(f"Account:{_ref(row, 'APAccountRef')}", total, party), *_scaled(doc_lines, -1)]
    tax = _tax(row)
    if kind in ("Purchase", "SalesReceipt", "RefundReceipt", "VendorCredit") and tax:
        lines.append(M.EntryLine(M.TAX_ACCOUNT, doc_sign * tax))
    if kind == "Purchase" and row.get("Credit") is True:
        lines = _scaled(lines, -1)
    if any(x.account in ("", "Account:") for x in lines):
        return M.Refused(ext, "a line names no account the books hold")
    if sum(x.amount for x in lines) != 0:
        return M.Refused(ext, "the lines do not add up to the total")
    return M.Journal(ext, _id(row.get("TxnDate")), _memo(row), "sync", tuple(lines))


def _party_ref(key: str) -> tuple[str, str] | None:
    kind, _, value = key.partition(":")
    return (kind, value) if kind in ("Vendor", "Customer") and value else None


def journal_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A journal entry's request body from an outbox payload (keys already translated to QuickBooks ids)."""
    lines: list[dict[str, Any]] = []
    for x in body["lines"]:
        account, _, value = str(x["account"]).partition(":")
        if account != "Account" or not value:
            raise ValueError(f"not a QuickBooks account: {x['account']!r}")
        units = int(x["amount"])
        detail: dict[str, Any] = {"PostingType": "Debit" if units >= 0 else "Credit", "AccountRef": {"value": value}}
        party = _party_ref(str(x.get("party") or ""))
        project = str(x.get("project") or "")
        if party is not None:
            detail["Entity"] = {"Type": party[0], "EntityRef": {"type": party[0], "value": party[1]}}
        elif project:
            # A line with no party that is for a job is entered against the job's customer.
            detail["Entity"] = {"Type": "Customer", "EntityRef": {"type": "Customer",
                                                                  "value": _keyed(project, "Customer")}}
        classes = as_map(x.get("classes"))
        if classes:
            detail["ClassRef"] = {"value": str(next(iter(classes.values())))}
        if x.get("location"):
            detail["DepartmentRef"] = {"value": _keyed(x["location"], "Department")}
        lines.append({"DetailType": "JournalEntryLineDetail", "Amount": decimal_text(units),
                      "Description": str(x.get("description") or ""), "JournalEntryLineDetail": detail})
    return {"TxnDate": str(body["date"]), "PrivateNote": _note(body), "Line": lines}


def account_body(body: Mapping[str, Any]) -> dict[str, Any]:
    return {"Name": str(body["name"])[:100], "AcctNum": str(body["code"])[:7],
            "AccountType": account_type(str(body["type"]), str(body.get("subtype") or "")),
            "Active": bool(body.get("active", True))}


def party_body(body: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"DisplayName": str(body["name"])[:500]}
    emails = as_list(body.get("emails"))
    if emails:
        out["PrimaryEmailAddr"] = {"Address": str(emails[0])}
    return out


def delete_body(ext_key: str, revision: str) -> dict[str, Any]:
    """The body that deletes a journal entry: its id and the SyncToken it was last read with."""
    _, _, value = ext_key.partition(":")
    return {"Id": value, "SyncToken": revision}


def _account_id(key: object) -> str:
    kind, _, value = str(key).partition(":")
    if kind != "Account" or not value:
        raise ValueError(f"not a QuickBooks account: {key!r}")
    return value


def _keyed(key: object, kind: str) -> str:
    """The QuickBooks id in a provider key of one kind ("Department:3"); raises for a key of another kind."""
    found, _, value = str(key).partition(":")
    if found != kind or not value:
        raise ValueError(f"not a QuickBooks {kind.lower()}: {key!r}")
    return value


def _single(values: Iterable[str], what: str) -> str:
    """The one value a transaction's lines share ("" when none names one). Raises when they name more than one, since a
    QuickBooks transaction takes one location or one job."""
    found = {v for v in values if v}
    if len(found) > 1:
        raise ValueError(f"the lines name more than one {what}; QuickBooks takes one per transaction")
    return found.pop() if found else ""


def _line_extras(x: Mapping[str, Any]) -> dict[str, Any]:
    """A line's class and the job it is billed to, as QuickBooks line references."""
    out: dict[str, Any] = {}
    if x.get("class"):
        out["ClassRef"] = {"value": str(x["class"])}
    if x.get("project"):
        out["CustomerRef"] = {"value": _keyed(x["project"], "Customer")}
    return out


def _party_id(key: object, kind: str) -> str:
    found = _party_ref(str(key))
    if found is None or found[0] != kind:
        raise ValueError(f"not a QuickBooks {kind.lower()}: {key!r}")
    return found[1]


MARKER = re.compile(r"\s*\[tico:[0-9a-f-]{36}\]$")
REFERENCE = re.compile(r"\[tico:([0-9a-f-]{36})\]")      # the write reference a record's note carries
FOREIGN = "a record in a foreign currency: the books take home-currency records only"


def _memo(row: Mapping[str, Any]) -> str:
    """A provider record's memo, without the Tico reference a write of ours carries in its private note."""
    return MARKER.sub("", _id(row.get("PrivateNote")))


def _note(body: Mapping[str, Any]) -> str:
    """A write's private note: its memo, then the Tico reference that finds the write again after a retry."""
    marker, memo = str(body.get("marker") or ""), str(body.get("memo") or "")
    if not marker:
        return memo[:4000]
    return f"{memo[:4000 - len(marker) - 1]} {marker}".lstrip()


def _header(body: Mapping[str, Any]) -> dict[str, Any]:
    return {"TxnDate": str(body["day"]), "PrivateNote": _note(body)}


def line_ids(made: Mapping[str, Any]) -> tuple[str, ...]:
    """The provider's id for each expense line of a bill it holds, in the order the lines are stored. Lines that carry
    no
    money (a description) have none and are left out, so the ids line up with the lines Tico sent."""
    out: list[str] = []
    for raw in as_list(made.get("Line")):
        line = as_map(raw)
        if line is not None and _id(line.get("DetailType")) in EXPENSE_LINES:
            out.append(_id(line.get("Id")))
    return tuple(out)


def _applied(links: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """Each applied amount as a line linked to the bill or invoice it pays."""
    out: list[dict[str, Any]] = []
    for key, amount in links:
        kind, _, value = str(key).partition(":")
        out.append({"Amount": decimal_text(int(amount)), "LinkedTxn": [{"TxnId": value, "TxnType": kind}]})
    return out


def bill_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A bill's request body from an outbox payload (keys already translated to QuickBooks ids). Each line is an
    expense on its account, or on its item when it names one; QuickBooks posts the bill when it is saved. The bill's
    location is its lines' one location, and each line keeps its class and job."""
    lines: list[dict[str, Any]] = []
    for x in body["lines"]:
        description = str(x.get("description") or "")
        if x.get("item"):
            detail: dict[str, Any] = {"ItemRef": {"value": _keyed(x["item"], "Item")}, "Qty": 1,
                                      "UnitPrice": decimal_text(int(x["amount"])), **_line_extras(x)}
            lines.append({"DetailType": "ItemBasedExpenseLineDetail", "Amount": decimal_text(int(x["amount"])),
                          "Description": description, "ItemBasedExpenseLineDetail": detail})
        else:
            detail = {"AccountRef": {"value": _account_id(x["account"])}, **_line_extras(x)}
            lines.append({"DetailType": "AccountBasedExpenseLineDetail", "Amount": decimal_text(int(x["amount"])),
                          "Description": description, "AccountBasedExpenseLineDetail": detail})
    out: dict[str, Any] = {**_header(body), "VendorRef": {"value": _party_id(body["party"], "Vendor")}, "Line": lines}
    location = _single((str(x.get("location") or "") for x in body["lines"]), "location")
    if location:
        out["DepartmentRef"] = {"value": _keyed(location, "Department")}
    if body.get("number"):
        out["DocNumber"] = str(body["number"])
    if body.get("due"):
        out["DueDate"] = str(body["due"])
    return out


def invoice_body(body: Mapping[str, Any], income: Mapping[str, str]) -> dict[str, Any]:
    """An invoice's request body. QuickBooks bills lines by item: a line that names one uses it, else the item that
    sells to its account (`income` maps an account id to an item id). A line with no such item is refused, so nothing is
    half-written. A line for a job bills the job, so the invoice is to the job's customer."""
    lines: list[dict[str, Any]] = []
    for x in body["lines"]:
        units = int(x["amount"])
        if x.get("item"):
            item = _keyed(x["item"], "Item")
        else:
            account = _account_id(x["account"])
            found = income.get(account)
            if found is None:
                raise ValueError(f"no QuickBooks item sells to account {account}; add one in QuickBooks")
            item = found
        detail: dict[str, Any] = {"ItemRef": {"value": item}, "Qty": 1, "UnitPrice": decimal_text(units)}
        if x.get("class"):
            detail["ClassRef"] = {"value": str(x["class"])}
        lines.append({"DetailType": "SalesItemLineDetail", "Amount": decimal_text(units),
                      "Description": str(x.get("description") or ""), "SalesItemLineDetail": detail})
    jobs = _single((str(x.get("project") or "") for x in body["lines"]), "job")
    customer = _keyed(jobs, "Customer") if jobs else _party_id(body["party"], "Customer")
    out: dict[str, Any] = {**_header(body), "CustomerRef": {"value": customer}, "Line": lines}
    location = _single((str(x.get("location") or "") for x in body["lines"]), "location")
    if location:
        out["DepartmentRef"] = {"value": _keyed(location, "Department")}
    if body.get("number"):
        out["DocNumber"] = str(body["number"])
    if body.get("due"):
        out["DueDate"] = str(body["due"])
    if body.get("tax"):
        # The invoice's sales tax as TxnTaxDetail.TotalTax, the field the import reads it from.
        out["TxnTaxDetail"] = {"TotalTax": decimal_text(int(body["tax"]))}
    if body.get("linked_estimate"):
        out["LinkedTxn"] = [{"TxnId": str(body["linked_estimate"]), "TxnType": "Estimate"}]
    return out


def purchase_order_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A purchase order's request body: a bill's lines on its vendor, open until QuickBooks closes it."""
    return {**bill_body(body), "POStatus": "Open"}


def purchase_order_close_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A purchase order's close: a sparse update that sets its status to Closed, on the revision the books hold (a stale
    revision is refused by QuickBooks, so the close is tried again after the change feed brings the newer one)."""
    return {"Id": _keyed(body["ext"], "PurchaseOrder"), "SyncToken": str(body["revision"]), "POStatus": "Closed",
            "sparse": True}


def estimate_body(body: Mapping[str, Any], income: Mapping[str, str]) -> dict[str, Any]:
    """An estimate's request body: an invoice's lines, since QuickBooks prices an estimate by item too."""
    return invoice_body(body, income)


def bill_payment_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A bill payment: paid from one bank account (or a credit card, a liability) to one vendor, applied to the bill it
    settles."""
    account = {"value": _account_id(body["bank"])}
    if body.get("card"):
        method: dict[str, Any] = {"PayType": "CreditCard", "CreditCardPayment": {"CCAccountRef": account}}
    else:
        method = {"PayType": "Check", "CheckPayment": {"BankAccountRef": account}}
    return {**_header(body), "VendorRef": {"value": _party_id(body["party"], "Vendor")}, **method,
            "TotalAmt": decimal_text(int(body["total"])), "Line": _applied(body["links"])}


def payment_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A received payment: one customer's money into one bank account, applied to the invoice it settles."""
    return {**_header(body), "CustomerRef": {"value": _party_id(body["party"], "Customer")},
            "DepositToAccountRef": {"value": _account_id(body["bank"])},
            "TotalAmt": decimal_text(int(body["total"])), "Line": _applied(body["links"])}


WRITE_OFF_ITEM = "Bad debt"     # the item a write-off is billed by: found by this name, or created once


def item_body(name: str, account: str) -> dict[str, Any]:
    """A non-inventory item that sells to one account (by the account's QuickBooks id). The write-off item is this kind,
    with the bad debt expense account as its income account, as Intuit's write-off article sets it up."""
    return {"Name": name, "Type": "NonInventory", "IncomeAccountRef": {"value": account}}


# Tico's raw material and assembly labels have no books type: the books take them as a plain non-inventory item, and the
# label stays in Tico.
ITEM_TYPES = {"service": "Service", "inventory": "Inventory", "non_inventory": "NonInventory", "bundle": "Group",
              "category": "Category", "raw_material": "NonInventory", "assembly": "NonInventory"}


def _head(body: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """The id and revision a sparse update names: QuickBooks checks the SyncToken, so a stale write is refused."""
    return {"Id": _keyed(body["ext"], kind), "SyncToken": str(body["revision"]), "sparse": True}


def item_write(body: Mapping[str, Any], *, update: bool = False) -> dict[str, Any]:
    """An item as QuickBooks takes it. Its type is set on create only (QuickBooks does not change it). An inventory item
    starts at zero on the day it is made, as QuickBooks requires."""
    out: dict[str, Any] = {"Name": str(body["name"])[:100], "Active": bool(body.get("active", True))}
    if body.get("sku"):
        out["Sku"] = str(body["sku"])[:100]
    if body.get("price") is not None:
        out["UnitPrice"] = decimal_text(int(body["price"]))
    for key, field in (("income", "IncomeAccountRef"), ("expense", "ExpenseAccountRef"), ("asset", "AssetAccountRef")):
        if body.get(key):
            out[field] = {"value": _keyed(body[key], "Account")}
    if update:
        return {**out, **_head(body, "Item")}
    out["Type"] = ITEM_TYPES[str(body["kind"])]
    if str(body["kind"]) == "inventory":
        out.update({"TrackQtyOnHand": True, "QtyOnHand": 0, "InvStartDate": str(body["day"])})
    return out


def location_write(body: Mapping[str, Any], *, update: bool = False) -> dict[str, Any]:
    """A location as a QuickBooks department: its name and whether it is active."""
    out: dict[str, Any] = {"Name": str(body["name"])[:100], "Active": bool(body.get("active", True))}
    return {**out, **_head(body, "Department")} if update else out


def job_write(body: Mapping[str, Any], *, update: bool = False) -> dict[str, Any]:
    """A project as a QuickBooks job: a customer with Job set, under its parent customer (the one QuickBooks requires on
    create). An update is sparse: it names the job's id and revision, and its parent only when the parent changed."""
    out: dict[str, Any] = {"DisplayName": str(body["name"])[:500], "Job": True,
                           "Active": bool(body.get("active", True))}
    if body.get("customer") or not update:
        out["ParentRef"] = {"value": _keyed(body.get("customer"), "Customer")}
    if update:
        return {**out, "Id": _keyed(body["ext"], "Project"), "SyncToken": str(body["revision"]), "sparse": True}
    return out


def writeoff_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A write-off as a credit memo: one line billed by the write-off item. QuickBooks does not apply a credit through its
    lines, so the memo is applied to its invoice by credit_apply_body once it is saved."""
    total = decimal_text(int(body["total"]))
    line = {"DetailType": "SalesItemLineDetail", "Amount": total, "Description": str(body.get("memo") or "")[:4000],
            "SalesItemLineDetail": {"ItemRef": {"value": str(body["item"])}, "Qty": 1, "UnitPrice": total}}
    return {**_header(body), "CustomerRef": {"value": _party_id(body["party"], "Customer")}, "Line": [line]}


def _linked(key: object, kind: str) -> str:
    name, _, value = str(key).partition(":")
    if name != kind or not value:
        raise ValueError(f"not a QuickBooks {kind.lower()}: {key!r}")
    return value


def credit_apply_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A credit memo applied to an invoice: a payment of nothing that links the invoice and the credit memo. This is how
    QuickBooks applies a credit (no money moves)."""
    amount = decimal_text(int(body["amount"]))
    lines = [{"Amount": amount, "LinkedTxn": [{"TxnId": _linked(body["invoice"], "Invoice"), "TxnType": "Invoice"}]},
             {"Amount": amount, "LinkedTxn": [{"TxnId": _linked(body["credit"], "CreditMemo"),
                                               "TxnType": "CreditMemo"}]}]
    return {**_header(body), "CustomerRef": {"value": _party_id(body["party"], "Customer")},
            "TotalAmt": decimal_text(0), "Line": lines}
