"""QuickBooks Online shapes (spec §10): the JSON the adapter reads from QuickBooks and writes to it, as mirror records and
request bodies. No I/O (the HTTP client is finance/qbo.py). QuickBooks sends money as decimal text, so amounts go through
Decimal and come out as integer minor units; nothing here is a float."""
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from . import mirror as M

MINOR_VERSION = "65"
ENTITIES = ("Account", "Vendor", "Customer", "JournalEntry")     # what the change feed and the backfill read
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


def account_type(tico_type: str, subtype: str) -> str:
    """The QuickBooks AccountType for a Tico account: its subtype when QuickBooks has one for it, else its type's."""
    return _OUT.get((tico_type, subtype)) or _DEFAULT_OUT.get(tico_type, "Expense")


def account_record(row: dict[str, Any], *, deleted: bool = False) -> M.ChartAccount | None:
    """A QuickBooks Account as a chart account; None for a type this chart cannot hold."""
    ext = M.Ext("Account", _id(row.get("Id")), _id(row.get("SyncToken")), deleted)
    if deleted:
        return M.ChartAccount(ext, code="", name="", type="", active=False)
    mapped = TYPES.get(_id(row.get("AccountType")))
    if mapped is None:
        return None
    code = _id(row.get("AcctNum")) or f"QB{ext.external_id}"
    return M.ChartAccount(ext, code=code, name=_id(row.get("Name")), type=mapped[0], subtype=mapped[1],
                          active=bool(row.get("Active", True)))


def party_record(row: dict[str, Any], kind: str, *, deleted: bool = False) -> M.Party:
    """A QuickBooks Vendor (kind "Vendor") or Customer (kind "Customer") as a party."""
    ext = M.Ext(kind, _id(row.get("Id")), _id(row.get("SyncToken")), deleted)
    if deleted:
        return M.Party(ext, name="", kind="vendor" if kind == "Vendor" else "customer", active=False)
    address = as_map(row.get("PrimaryEmailAddr"))
    emails = (_id(address.get("Address")),) if address is not None and address.get("Address") else ()
    return M.Party(ext, name=_id(row.get("DisplayName")) or _id(row.get("CompanyName")),
                   kind="vendor" if kind == "Vendor" else "customer", active=bool(row.get("Active", True)),
                   emails=emails)


def journal_record(row: dict[str, Any], *, deleted: bool = False) -> M.Journal:
    """A QuickBooks JournalEntry as one balanced entry. Debits are positive; lines without an account are skipped."""
    ext = M.Ext("JournalEntry", _id(row.get("Id")), _id(row.get("SyncToken")), deleted)
    lines: list[M.EntryLine] = []
    for raw in as_list(row.get("Line")):
        line = as_map(raw)
        if line is None or line.get("DetailType") != "JournalEntryLineDetail":
            continue
        detail = as_map(line.get("JournalEntryLineDetail"))
        ref = as_map(detail.get("AccountRef")) if detail is not None else None
        if detail is None or ref is None or not _id(ref.get("value")):
            continue
        units = minor(line.get("Amount") or 0)
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
    return M.Journal(ext, _id(row.get("TxnDate")), _id(row.get("PrivateNote")), "sync", tuple(lines))


def records(entity: str, rows: Iterable[object]) -> list[M.ChartAccount | M.Party | M.Journal]:
    """The mirror records for one QuickBooks entity's rows (a query page or a change block); rows of another shape
    are skipped, and a deleted row (CDC marks it "Deleted") keeps only its identity."""
    out: list[M.ChartAccount | M.Party | M.Journal] = []
    for raw in rows:
        row = as_map(raw)
        if row is None:
            continue
        gone = _id(row.get("status")) == "Deleted"
        if entity == "Account":
            made = account_record(row, deleted=gone)
            if made is not None:
                out.append(made)
        elif entity in ("Vendor", "Customer"):
            out.append(party_record(row, entity, deleted=gone))
        elif entity == "JournalEntry":
            out.append(journal_record(row, deleted=gone))
    return out


def change_records(answer: Mapping[str, Any]) -> list[M.ChartAccount | M.Party | M.Journal]:
    """The records in a CDC answer: {"CDCResponse": [{"QueryResponse": [{"Account": [...], ...}]}]}."""
    out: list[M.ChartAccount | M.Party | M.Journal] = []
    for block in as_list(answer.get("CDCResponse")):
        found = as_map(block)
        if found is None:
            continue
        for query in as_list(found.get("QueryResponse")):
            page = as_map(query)
            if page is None:
                continue
            for entity in ENTITIES:
                out += records(entity, as_list(page.get(entity)))
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
    return {"TxnDate": str(body["date"]), "PrivateNote": str(body.get("memo") or "")[:4000], "Line": lines}


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
