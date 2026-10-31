"""QuickBooks Online shapes (spec §10): the JSON the adapter reads from QuickBooks and writes to it, as mirror records and
request bodies. No I/O (the HTTP client is finance/qbo.py). QuickBooks sends money as decimal text, so amounts go through
Decimal and come out as integer minor units; nothing here is a float. A deleted row (CDC marks it "Deleted") is its
identity only, and the books take it as a deletion."""
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from . import mirror as M

MINOR_VERSION = "65"
# Every QuickBooks entity the change feed reads.
ENTITIES = ("Account", "Vendor", "Customer", "JournalEntry", "Bill", "Invoice", "CreditMemo", "BillPayment", "Payment")
# The QuickBooks entities behind each backfill entity, in the order the books take them in (the mirror's ENTITIES).
PAGES: dict[str, tuple[str, ...]] = {
    "accounts": ("Account",), "parties": ("Vendor", "Customer"), "documents": ("Bill", "Invoice", "CreditMemo"),
    "payments": ("BillPayment", "Payment"), "entries": ("JournalEntry",)}
DOCUMENTS = ("Bill", "Invoice", "CreditMemo")
PAYMENTS = ("BillPayment", "Payment")
# A document line's detail. Its account is its own (a purchase on an account) or its item's: income for a sale, expense
# for a purchase on an item.
LINE_DETAILS = ("AccountBasedExpenseLineDetail", "ItemBasedExpenseLineDetail", "SalesItemLineDetail")
# QuickBooks AccountType -> Tico (type, subtype). Subtypes are only the ones the default chart uses.
TYPES: dict[str, tuple[str, str]] = {
    "Bank": ("asset", "bank"), "Accounts Receivable": ("asset", ""), "Other Current Asset": ("asset", ""),
    "Fixed Asset": ("asset", ""), "Other Asset": ("asset", ""),
    "Accounts Payable": ("liability", ""), "Credit Card": ("liability", ""), "Other Current Liability": ("liability", ""),
    "Long Term Liability": ("liability", ""), "Equity": ("equity", ""), "Income": ("income", ""),
    "Other Income": ("income", "other_income"), "Cost of Goods Sold": ("expense", ""), "Expense": ("expense", ""),
    "Other Expense": ("expense", "other_expense"),
}
_OUT: dict[tuple[str, str], str] = {("asset", "bank"): "Bank", ("income", "other_income"): "Other Income",
                                    ("expense", "other_expense"): "Other Expense"}
_DEFAULT_OUT = {"asset": "Other Current Asset", "liability": "Other Current Liability", "equity": "Equity",
                "income": "Income", "expense": "Expense"}
Made = M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Ext


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
    return M.ChartAccount(ext, code=code, name=_id(row.get("Name")), type=mapped[0], subtype=mapped[1],
                          active=bool(row.get("Active", True)))


def party_record(row: dict[str, Any], kind: str) -> M.Party:
    """A QuickBooks Vendor (kind "Vendor") or Customer (kind "Customer") as a party."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    address = as_map(row.get("PrimaryEmailAddr"))
    emails = (_id(address.get("Address")),) if address is not None and address.get("Address") else ()
    return M.Party(ext, name=_id(row.get("DisplayName")) or _id(row.get("CompanyName")),
                   kind="vendor" if kind == "Vendor" else "customer", active=bool(row.get("Active", True)),
                   emails=emails)


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
                                 description=_id(line.get("Description"))))
    return M.Journal(ext, _id(row.get("TxnDate")), _memo(row), "sync", tuple(lines))


def _doc_line(raw: object, items: Mapping[str, Mapping[str, str]]) -> M.EntryLine | None:
    """One document line as an entry line on its account. None for a line that carries no money (a description, a
    subtotal). Tax and discount lines are not taken in, so a document that has them does not add up to its total and
    waits as unmapped (the mirror says so)."""
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
    return M.EntryLine(account=f"Account:{account}" if account else "", amount=units,
                       description=_id(line.get("Description")))


def document_record(row: dict[str, Any], kind: str, items: Mapping[str, Mapping[str, str]]) -> M.Document:
    """A QuickBooks Bill, Invoice or CreditMemo as a document. A bill's party is its vendor; an invoice's and a credit
    memo's is their customer. `items` maps each item id to its income and expense accounts (see item_accounts)."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    party_kind = "Vendor" if kind == "Bill" else "Customer"
    party_value = _ref(row, "VendorRef" if kind == "Bill" else "CustomerRef")
    lines = tuple(x for x in (_doc_line(raw, items) for raw in as_list(row.get("Line"))) if x is not None)
    links = _links(row, "Invoice") if kind == "CreditMemo" else ()
    return M.Document(ext, party=f"{party_kind}:{party_value}" if party_value else "",
                      number=_id(row.get("DocNumber")), day=_id(row.get("TxnDate")), due=_id(row.get("DueDate")),
                      total=_units(row.get("TotalAmt")) or 0, lines=lines, memo=_memo(row),
                      links=links)


def settlement_record(row: dict[str, Any], kind: str) -> M.Settlement:
    """A QuickBooks BillPayment (paid to a vendor) or Payment (received from a customer): its money account, party,
    total, and what each line applies to a bill (BillPayment) or an invoice (Payment)."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")))
    if kind == "Payment":
        bank, party_kind, party_value = _ref(row, "DepositToAccountRef"), "Customer", _ref(row, "CustomerRef")
        target = "Invoice"
    else:
        check: dict[str, Any] = as_map(row.get("CheckPayment")) or {}
        card: dict[str, Any] = as_map(row.get("CreditCardPayment")) or {}
        bank = _ref(check, "BankAccountRef") or _ref(card, "CCAccountRef")
        party_kind, party_value = "Vendor", _ref(row, "VendorRef")
        target = "Bill"
    return M.Settlement(ext, day=_id(row.get("TxnDate")), memo=_memo(row),
                        party=f"{party_kind}:{party_value}" if party_value else "",
                        bank=f"Account:{bank}" if bank else "", total=_units(row.get("TotalAmt")) or 0,
                        links=_links(row, target))


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


def item_accounts(rows: Iterable[object]) -> dict[str, dict[str, str]]:
    """Each item's income and expense account (QuickBooks keeps them on the item): what its lines post to."""
    out: dict[str, dict[str, str]] = {}
    for raw in rows:
        row = as_map(raw)
        if row is None or not _id(row.get("Id")):
            continue
        out[_id(row.get("Id"))] = {"income": _ref(row, "IncomeAccountRef"), "expense": _ref(row, "ExpenseAccountRef")}
    return out


def _one(entity: str, row: dict[str, Any], items: Mapping[str, Mapping[str, str]]) -> Made | None:
    if entity == "Account":
        return account_record(row)
    if entity in ("Vendor", "Customer"):
        return party_record(row, entity)
    if entity == "JournalEntry":
        return journal_record(row)
    if entity in DOCUMENTS:
        return document_record(row, entity, items)
    if entity in PAYMENTS:
        return settlement_record(row, entity)
    return None


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
            out.append(made)
    return out


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
        if party is not None:
            detail["Entity"] = {"Type": party[0], "EntityRef": {"type": party[0], "value": party[1]}}
        classes = as_map(x.get("classes"))
        if classes:
            detail["ClassRef"] = {"value": str(next(iter(classes.values())))}
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


def _party_id(key: object, kind: str) -> str:
    found = _party_ref(str(key))
    if found is None or found[0] != kind:
        raise ValueError(f"not a QuickBooks {kind.lower()}: {key!r}")
    return found[1]


MARKER = re.compile(r"\s*\[tico:[0-9a-f-]{36}\]$")


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


def _applied(links: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """Each applied amount as a line linked to the bill or invoice it pays."""
    out: list[dict[str, Any]] = []
    for key, amount in links:
        kind, _, value = str(key).partition(":")
        out.append({"Amount": decimal_text(int(amount)), "LinkedTxn": [{"TxnId": value, "TxnType": kind}]})
    return out


def bill_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A bill's request body from an outbox payload (keys already translated to QuickBooks ids). Each line is an
    expense on its account; QuickBooks posts the bill when it is saved."""
    lines = [{"DetailType": "AccountBasedExpenseLineDetail", "Amount": decimal_text(int(x["amount"])),
              "Description": str(x.get("description") or ""),
              "AccountBasedExpenseLineDetail": {"AccountRef": {"value": _account_id(x["account"])}}}
             for x in body["lines"]]
    out: dict[str, Any] = {**_header(body), "VendorRef": {"value": _party_id(body["party"], "Vendor")}, "Line": lines}
    if body.get("number"):
        out["DocNumber"] = str(body["number"])
    if body.get("due"):
        out["DueDate"] = str(body["due"])
    return out


def invoice_body(body: Mapping[str, Any], income: Mapping[str, str]) -> dict[str, Any]:
    """An invoice's request body. QuickBooks bills lines by item, so each line uses the item that sells to its account
    (`income` maps an account id to an item id). A line with no such item is refused, so nothing is half-written."""
    lines: list[dict[str, Any]] = []
    for x in body["lines"]:
        account = _account_id(x["account"])
        item = income.get(account)
        if item is None:
            raise ValueError(f"no QuickBooks item sells to account {account}; add one in QuickBooks")
        units = int(x["amount"])
        lines.append({"DetailType": "SalesItemLineDetail", "Amount": decimal_text(units),
                      "Description": str(x.get("description") or ""),
                      "SalesItemLineDetail": {"ItemRef": {"value": item}, "Qty": 1,
                                              "UnitPrice": decimal_text(units)}})
    out: dict[str, Any] = {**_header(body), "CustomerRef": {"value": _party_id(body["party"], "Customer")},
                           "Line": lines}
    if body.get("number"):
        out["DocNumber"] = str(body["number"])
    if body.get("due"):
        out["DueDate"] = str(body["due"])
    return out


def bill_payment_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A bill payment: paid from one bank account to one vendor, applied to the bill it settles."""
    return {**_header(body), "VendorRef": {"value": _party_id(body["party"], "Vendor")}, "PayType": "Check",
            "CheckPayment": {"BankAccountRef": {"value": _account_id(body["bank"])}},
            "TotalAmt": decimal_text(int(body["total"])), "Line": _applied(body["links"])}


def payment_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A received payment: one customer's money into one bank account, applied to the invoice it settles."""
    return {**_header(body), "CustomerRef": {"value": _party_id(body["party"], "Customer")},
            "DepositToAccountRef": {"value": _account_id(body["bank"])},
            "TotalAmt": decimal_text(int(body["total"])), "Line": _applied(body["links"])}


WRITE_OFF_ITEM = "Bad debt"     # the item a write-off is billed by: found by this name, or created once


def item_body(name: str, account: str) -> dict[str, Any]:
    """A service item that sells to and buys from one account (by the account's QuickBooks id)."""
    ref = {"value": account}
    return {"Name": name, "Type": "Service", "IncomeAccountRef": ref, "ExpenseAccountRef": ref}


def writeoff_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """A write-off as a credit memo: one line billed by the write-off item, applied to the invoice it closes. A write-off
    closes one invoice, so anything else is refused."""
    links = body["links"]
    if len(links) != 1 or str(links[0][0]).partition(":")[0] != "Invoice":
        raise ValueError("a write-off closes one invoice")
    total = decimal_text(int(body["total"]))
    line = {"DetailType": "SalesItemLineDetail", "Amount": total, "Description": str(body.get("memo") or "")[:4000],
            "SalesItemLineDetail": {"ItemRef": {"value": str(body["item"])}, "Qty": 1, "UnitPrice": total},
            "LinkedTxn": [txn for applied in _applied(links) for txn in applied["LinkedTxn"]]}
    return {**_header(body), "CustomerRef": {"value": _party_id(body["party"], "Customer")}, "Line": [line]}
