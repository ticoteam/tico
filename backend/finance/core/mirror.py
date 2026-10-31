"""Mirror mode (spec §10): the rules by which a book copies a provider's books, with no I/O. Which revision of a provider
record the books take, how a bank row finds the entry it is, how a backfill pages through a provider's records, when
the change feed is read, and which provider class a tag sends. The store and the sync call these."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from . import dates

BACKFILL_SIZE = 100     # records per provider read: one page is one read and one transaction
MATCH_DAYS = 3          # a bank row finds an entry this many days either side of its own date
CDC_DAYS = 30           # how far back a change feed reaches (QuickBooks keeps 30 days of changes)
CDC_OVERLAP = 300       # seconds read again before the last pass, so a change made during it is not missed
CDC_EVERY = 900         # seconds between change feed reads (the safety net under the webhooks)

# What the books do with one provider record (decide).
CREATE = "create"       # new to the books
REPLACE = "replace"     # a newer revision: an entry is reversed and posted again; an account or party is updated
DELETE = "delete"       # deleted at the provider: an entry is reversed; an account or party is made inactive
SAME = "same"           # the revision the books hold
OLDER = "older"         # a revision older than the books hold arrived late: nothing changes
GONE = "gone"           # deleted at the provider and never in the books: nothing to do


@dataclass(frozen=True)
class Ext:
    """A provider record's identity: its entity kind, its id within that kind, and its revision (a counter the provider
    bumps on each change)."""
    kind: str
    external_id: str
    revision: str
    deleted: bool = False

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


@dataclass(frozen=True)
class Party:
    ext: Ext
    name: str
    kind: str           # vendor | customer | both
    active: bool = True
    emails: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntryLine:
    account: str        # the provider's key for the account ("Account:55")
    amount: int         # minor units, debits positive
    party: str = ""     # the provider's key for the vendor or customer ("Vendor:9"), "" for none
    description: str = ""


@dataclass(frozen=True)
class Journal:
    """One provider entry as the books take it: a balanced set of lines, dated, from one source (sync, bill, invoice
    or payment)."""
    ext: Ext
    day: str
    memo: str
    source: str
    lines: tuple[EntryLine, ...]


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


def revision_order(a: str, b: str) -> int:
    """1 when revision `a` is newer than `b`, -1 when older, 0 when the same. Revisions are counters, so digits compare
    as numbers; anything else compares as text."""
    if a.isdigit() and b.isdigit():
        return (int(a) > int(b)) - (int(a) < int(b))
    return (a > b) - (a < b)


def decide(have: tuple[str, str] | None, ext: Ext) -> str:
    """What the books do with a provider record, given its external ref's (revision, status) (None: never mirrored).
    A ref's status is active, deleted or unmapped (a record the books could not take in yet, so it is new again)."""
    if have is None or have[1] == "unmapped":
        return GONE if ext.deleted else CREATE
    revision, status = have
    if status == "deleted":
        return SAME if ext.deleted else CREATE
    if ext.deleted:
        return DELETE
    order = revision_order(ext.revision, revision)
    return REPLACE if order > 0 else SAME if order == 0 else OLDER


def match_row(amount: int, day: str, candidates: Sequence[tuple[str, str, int]]) -> str | None:
    """The entry a bank row is: among (entry id, entry date, line amount) candidates, the one with the row's amount whose
    date is nearest the row's (within MATCH_DAYS; the smaller id on a tie). None when no entry fits."""
    here = dates.day(day)
    best: tuple[int, str] | None = None
    for entry_id, entry_day, line in candidates:
        gap = abs((dates.day(entry_day) - here).days)
        if line == amount and gap <= MATCH_DAYS and (best is None or (gap, entry_id) < best):
            best = (gap, entry_id)
    return best[1] if best else None


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


def cdc_due(cursor: str | None, now: str) -> bool:
    """Whether the change feed is due: never read, or last read CDC_EVERY seconds ago or more."""
    return cursor is None or (parse(now) - parse(cursor)).total_seconds() >= CDC_EVERY


def class_refs(tags: Mapping[str, str], mapping: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """The provider class for each tag a line carries, by tag kind. `mapping` is {tag kind: {tag value: class id}}; a
    tag with no class in it sends none, and an empty mapping (the default) sends nothing."""
    out: dict[str, str] = {}
    for kind, value in tags.items():
        cls = mapping.get(kind, {}).get(value, "")
        if cls:
            out[kind] = cls
    return out
