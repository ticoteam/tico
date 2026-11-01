"""Mirror mode (spec §10): the rules by which a book copies a provider's books, with no I/O. Which revision of a provider
record the books take, how a bank row finds the entry it is, how a backfill pages through a provider's records, when
the change feed is read, and which provider class a tag sends. The store and the sync call these."""
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import dates

BACKFILL_SIZE = 1000    # records per provider read (QuickBooks returns at most 1000 a query): one page, one transaction
MATCH_DAYS = 3          # a bank row finds an entry this many days either side of its own date
CDC_DAYS = 30           # how far back a change feed reaches (QuickBooks keeps 30 days of changes)
CDC_OVERLAP = 300       # seconds read again before the last pass, so a change made during it is not missed
CDC_EVERY = 900         # seconds between change feed reads (the safety net under the webhooks)
# A write's reference: the id a provider keeps on the record it makes, so a record that comes in can be bound to the
# write that made it (the write's idempotency key, in this namespace). Providers store the same string in their notes.
REFERENCE_NAMESPACE = uuid.UUID("6f0c5a1e-2b7d-4c8e-9a13-5d2f7e4b9c01")
TAX_ACCOUNT = "Tax:sales"     # a provider line that is the sales tax on a document: the books' sales tax account
SALES_TAX = "sales tax"       # the subtype of the account a taxed record's tax lands on

# What the books do with one provider record (decide).
CREATE = "create"       # new to the books
REPLACE = "replace"     # a newer revision: an entry is reversed and posted again; an account or party is updated
DELETE = "delete"       # deleted at the provider: an entry is reversed; an account or party is made inactive
SAME = "same"           # the revision the books hold
OLDER = "older"         # a revision older than the books hold arrived late: nothing changes
GONE = "gone"           # deleted at the provider and never in the books: nothing to do

BOOK_ENTITIES = {  # what a provider's backfill reads, in the order the books take it (one cursor per entity)
    "quickbooks": ("accounts", "parties", "records", "documents", "payments", "entries"),
    "xero": ("accounts", "parties", "items", "tracking", "invoices", "credit_notes", "payments", "manual_journals",
             "bank_transactions", "bank_transfers", "overpayments", "prepayments"),
}


@dataclass(frozen=True)
class Ext:
    """A provider record's identity: its entity kind, its id within that kind, and its revision (a counter the provider
    bumps on each change). A provider that gives no revision gives a `content` digest instead, and the books make the
    revision from it (`content_revision`). `reference` is the write reference the provider holds on the record, when it
    holds one."""
    kind: str
    external_id: str
    revision: str
    deleted: bool = False
    reference: str = ""
    currency: str = ""      # the record's currency code ("" when the provider names none)
    content: str = ""       # a digest of the record's content, for a record whose revision the books make

    @property
    def key(self) -> str:
        """The external id an external ref keeps: kind and id, so equal ids of two kinds never collide."""
        return f"{self.kind}:{self.external_id}"


@dataclass(frozen=True)
class ChartAccount:
    ext: Ext
    code: str
    name: str
    type: str           # asset | liability | equity | income | expense
    subtype: str = ""
    active: bool = True
    payroll: bool = False   # a payroll expense account: its lines are private until the owner says otherwise


@dataclass(frozen=True)
class Party:
    ext: Ext
    name: str
    kind: str           # vendor | customer | both; "" when the provider does not say (the books keep their kind)
    active: bool = True
    emails: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """A provider product or service: what a line sells or buys, and the accounts it posts to (provider keys)."""
    ext: Ext
    name: str
    sku: str = ""
    kind: str = "service"   # service | inventory | non_inventory | bundle | category (the books' item kinds)
    price: int | None = None
    income: str = ""        # the provider's key for the account a sale posts to, "" for none
    expense: str = ""       # the provider's key for the account a purchase posts to, "" for none
    asset: str = ""         # the provider's key for an inventory item's asset account, "" for none
    active: bool = True
    description: str = ""   # the provider's description, "" when it has none (the books keep Tico's text then)
    quantity: str = ""      # the provider's quantity on hand as decimal text, "" for an item it does not track


@dataclass(frozen=True)
class Location:
    """A provider location (a department): what a line is tagged with for the location it was for."""
    ext: Ext
    name: str
    active: bool = True


@dataclass(frozen=True)
class Dimension:
    """A provider class: a line's class, which the books keep as a class dimension."""
    ext: Ext
    name: str
    active: bool = True


@dataclass(frozen=True)
class Project:
    """A provider job: a customer that is a job of its parent customer. The books keep it as a project under that
    customer, and a line for it is tagged with the project."""
    ext: Ext
    name: str
    customer: str = ""      # the parent customer's key ("Customer:4"), "" when the provider names none
    active: bool = True


Tags = tuple[tuple[str, str], ...]     # a provider line's tags: (kind, provider key), kind location | class | project


@dataclass(frozen=True)
class EntryLine:
    account: str        # the provider's key for the account ("Account:55")
    amount: int         # minor units, debits positive
    party: str = ""     # the provider's key for the vendor or customer ("Vendor:9"), "" for none
    description: str = ""
    rounding: bool = False  # the provider's rounding on a document: the one line that may be negative
    key: str = ""       # the provider's id for the line: it stays the same when the line's amount changes
    tags: Tags = ()     # the line's location, class and project (provider keys), a transaction's header ones included
    item: str = ""      # the provider's key for the product or service the line sells or buys ("Item:5"), "" for none


@dataclass(frozen=True)
class Journal:
    """One provider entry as the books take it: a balanced set of lines, dated, from one source (sync, bill, invoice
    or payment)."""
    ext: Ext
    day: str
    memo: str
    source: str
    lines: tuple[EntryLine, ...]
    basis: str = "both"     # "accrual" when the provider keeps the entry out of its cash-basis reports
    currency: str = ""      # the provider's currency code; "" when the provider names none (the books' own)
    refund_of: str = ""     # the credit memo or supplier's credit this refunds money for ("" for none)


@dataclass(frozen=True)
class Document:
    """A provider's bill, invoice or credit memo. Its lines are positive amounts on accounts; the books post a bill's lines
    as debits and an invoice's as credits (a credit memo's as debits, against receivables)."""
    ext: Ext
    party: str          # the vendor or customer key ("Vendor:9", "Customer:4"), "" when the provider names none
    number: str
    day: str
    due: str
    total: int          # minor units, positive
    lines: tuple[EntryLine, ...]
    memo: str = ""
    links: tuple[tuple[str, int], ...] = ()     # a credit memo's applied invoices ("Invoice:5", amount)
    tax: int = 0                                # the sales tax in its total, posted on TAX_ACCOUNT
    currency: str = ""  # the provider's currency code; "" when the provider names none (the books' own)
    control: str = ""                           # the provider's payable or receivable account ("Account:33")


@dataclass(frozen=True)
class Settlement:
    """A provider's payment of bills or invoices: a bill payment or a received payment. Its money moves between the bank
    account and the party's control account; `links` says how much settles each document ("Bill:12", amount)."""
    ext: Ext
    day: str
    memo: str
    party: str
    bank: str           # the provider's key for the money account, "" when the provider names none
    total: int          # minor units, positive
    links: tuple[tuple[str, int], ...] = ()
    credits: tuple[tuple[str, int], ...] = ()    # a payment's applied credit memos ("CreditMemo:7", amount)
    currency: str = ""  # the provider's currency code; "" when the provider names none (the books' own)


@dataclass(frozen=True)
class Order:
    """A provider purchase order or estimate: a document the books keep as an order. It posts no entry (it moves no
    money). `status` is the books' own: approved | closed for a purchase order, sent | accepted | declined for an
    estimate. Its lines are the provider's lines, on their accounts (the item's when a line names one). `tax` is the
    order's sales tax in minor units: it lands on the sales tax account as a line of its own, as a document's does."""
    ext: Ext
    party: str          # the vendor or customer key ("Vendor:9", "Customer:4")
    number: str
    day: str
    total: int          # minor units, positive
    lines: tuple[EntryLine, ...]
    memo: str = ""
    status: str = ""
    tax: int = 0


@dataclass(frozen=True)
class Refused:
    """A provider record the books refuse to take in, with the reason a person reads. It waits like any record the
    books could not take in, and is refused again on each read until the provider's record changes."""
    ext: Ext
    why: str


def reference(key: str) -> str:
    """The write reference for an idempotency key: what a provider's write notes carry, and what a record's `reference`
    is compared with."""
    return str(uuid.uuid5(REFERENCE_NAMESPACE, key))


def revision_order(a: str, b: str) -> int:
    """1 when revision `a` is newer than `b`, -1 when older, 0 when the same. Revisions are counters, so digits compare
    as numbers; anything else compares as text."""
    if a.isdigit() and b.isdigit():
        return (int(a) > int(b)) - (int(a) < int(b))
    return (a > b) - (a < b)


def content_revision(stored: str, at: str, digest: str) -> str:
    """The revision a record takes when the provider gives it none: the time the books first see its content, so a
    change
    sorts after the revision it replaces. The stored revision stays while the content is the same, so an unchanged
    record is not a new revision on every read."""
    return stored if stored.endswith(f"-{digest}") else f"{at}-{digest}"


def decide(have: tuple[str, str] | None, ext: Ext) -> str:
    """What the books do with a provider record, given its external ref's (revision, status) (None: never mirrored).
    A ref's status is active, deleted or unmapped (a record the books could not take in yet, so it is new again)."""
    if have is None or have[1] == "unmapped":
        return GONE if ext.deleted else CREATE
    revision, status = have
    if status == "deleted":
        # A deleted record stays deleted: the provider cannot bring it back, so a revision that arrives later (a parked
        # one, or the echo of a void the books made) is the same record.
        return SAME
    if ext.deleted:
        return DELETE
    order = revision_order(ext.revision, revision)
    return REPLACE if order > 0 else SAME if order == 0 else OLDER


def match_row(amount: int, day: str, candidates: Sequence[tuple[str, str, str, int]]) -> tuple[str, str] | None:
    """The line a bank row is: among (line id, entry id, entry date, line amount) candidates, the one with the row's
    amount whose entry date is nearest the row's (within MATCH_DAYS; the smaller entry id, then line id, on a tie).
    Returns (entry id, line id), or None when no line fits."""
    here = dates.day(day)
    best: tuple[int, str, str] | None = None
    for line_id, entry_id, entry_day, line in candidates:
        gap = abs((dates.day(entry_day) - here).days)
        if line == amount and gap <= MATCH_DAYS and (best is None or (gap, entry_id, line_id) < best):
            best = (gap, entry_id, line_id)
    return (best[1], best[2]) if best else None


def backfill_next(state: Mapping[str, object], entity: str) -> int | None:
    """The first position of the next page of an entity to read (QuickBooks counts from 1); None once it is all read."""
    row = state.get(entity)
    if not isinstance(row, dict):
        return 1
    row = cast(dict[str, object], row)
    if row.get("done"):
        return None
    start = row.get("next")
    return start if isinstance(start, int) and start > 0 else 1


def backfill_after(state: Mapping[str, object], entity: str, start: int, count: int,
                   size: int = BACKFILL_SIZE) -> dict[str, object]:
    """The backfill state once the page from `start` (`count` records) is in the books. A short page is the last."""
    return {**state, entity: {"next": start + count, "done": count < size}}


def backfill_done(state: Mapping[str, object], entities: Sequence[str]) -> bool:
    """Whether every entity has been read through to its last page."""
    return all(backfill_next(state, entity) is None for entity in entities)


def parse(value: str) -> datetime:
    """An ISO time (UTC, Z or an offset) as an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stamp(value: str, n: int) -> str:
    """`value` plus n microseconds, in the store's format: writes queued in one pass keep their order."""
    return (parse(value) + timedelta(microseconds=n)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def cdc_since(cursor: str | None, now: str) -> str:
    """The time a change feed is read from: the last read less an overlap, never more than CDC_DAYS back (ISO, UTC)."""
    at = parse(now)
    floor = at - timedelta(days=CDC_DAYS)
    base = parse(cursor) - timedelta(seconds=CDC_OVERLAP) if cursor else floor
    return max(base, floor).strftime("%Y-%m-%dT%H:%M:%SZ")


def cdc_due(cursor: str | None, now: str, every: int = CDC_EVERY) -> bool:
    """Whether the change feed is due: never read, or last read `every` seconds ago or more."""
    return cursor is None or (parse(now) - parse(cursor)).total_seconds() >= every


def class_refs(tags: Mapping[str, str], mapping: Mapping[str, Mapping[str, str]], own: str = "") -> dict[str, str]:
    """The provider class a line is sent with. A line's own class (`own`, its class tag's provider class) is the class
    it is sent with; otherwise each tag's class from `mapping`, which is {tag kind: {tag value: class id}}. A tag with no
    class in it sends none, and an empty mapping (the default) sends nothing."""
    if own:
        return {"class": own}
    out: dict[str, str] = {}
    for kind, value in tags.items():
        cls = mapping.get(kind, {}).get(value, "")
        if cls:
            out[kind] = cls
    return out


# ------------------------------------------------------------------------------------------------ parked records
Record = ChartAccount | Party | Journal | Document | Settlement | Item | Location | Dimension | Project | Ext | Refused


def encode(r: Record) -> dict[str, Any]:
    """A record as JSON data: the records the books could not take in yet are kept this way until they can be."""
    return {"record": type(r).__name__, **asdict(r)}


def _ext(d: Mapping[str, Any]) -> Ext:
    return Ext(str(d["kind"]), str(d["external_id"]), str(d["revision"]), bool(d.get("deleted", False)),
               str(d.get("reference", "")), str(d.get("currency", "")), str(d.get("content", "")))


def _lines(raw: Any) -> tuple[EntryLine, ...]:
    return tuple(EntryLine(str(x["account"]), int(x["amount"]), str(x.get("party", "")),
                           str(x.get("description", "")), bool(x.get("rounding", False)),
                           key=str(x.get("key", "")),
                           tags=tuple((str(k), str(v)) for k, v in x.get("tags", [])),
                           item=str(x.get("item", ""))) for x in raw)


def _pairs(raw: Any) -> tuple[tuple[str, int], ...]:
    return tuple((str(a), int(b)) for a, b in raw)


def decode(d: Mapping[str, Any]) -> Record:
    """The record `encode` wrote."""
    kind = str(d["record"])
    if kind == "Ext":
        return _ext(d)
    ext = _ext(d["ext"])
    if kind == "ChartAccount":
        return ChartAccount(ext, str(d["code"]), str(d["name"]), str(d["type"]), str(d.get("subtype", "")),
                            bool(d.get("active", True)), bool(d.get("payroll", False)))
    if kind == "Party":
        return Party(ext, str(d["name"]), str(d["kind"]), bool(d.get("active", True)),
                     tuple(str(x) for x in d.get("emails", [])))
    if kind == "Journal":
        return Journal(ext, str(d["day"]), str(d["memo"]), str(d["source"]), _lines(d["lines"]),
                       str(d.get("basis", "both")), str(d.get("currency", "")),
                       refund_of=str(d.get("refund_of", "")))
    if kind == "Document":
        return Document(ext, str(d["party"]), str(d["number"]), str(d["day"]), str(d["due"]), int(d["total"]),
                        _lines(d["lines"]), str(d.get("memo", "")), _pairs(d.get("links", [])),
                        int(d.get("tax", 0)), str(d.get("currency", "")),
                        control=str(d.get("control", "")))
    if kind == "Settlement":
        return Settlement(ext, str(d["day"]), str(d["memo"]), str(d["party"]), str(d["bank"]), int(d["total"]),
                          _pairs(d.get("links", [])), _pairs(d.get("credits", [])), str(d.get("currency", "")))
    if kind == "Item":
        price = d.get("price")
        return Item(ext, str(d["name"]), str(d.get("sku", "")), str(d.get("kind", "service")),
                    None if price is None else int(price), str(d.get("income", "")), str(d.get("expense", "")),
                    str(d.get("asset", "")), bool(d.get("active", True)), str(d.get("description", "")),
                    str(d.get("quantity", "")))
    if kind == "Order":
        return Order(ext, str(d["party"]), str(d["number"]), str(d["day"]), int(d["total"]), _lines(d["lines"]),
                     str(d.get("memo", "")), str(d.get("status", "")))
    if kind == "Location":
        return Location(ext, str(d["name"]), bool(d.get("active", True)))
    if kind == "Dimension":
        return Dimension(ext, str(d["name"]), bool(d.get("active", True)))
    if kind == "Project":
        return Project(ext, str(d["name"]), str(d.get("customer", "")), bool(d.get("active", True)))
    if kind == "Refused":
        return Refused(ext, str(d["why"]))
    raise ValueError(f"not a mirror record: {kind}")
