"""Mirror mode (spec §10): a book copies a provider's books, and Tico's writes go to the provider before they land here.

Provider records come in by backfill (one page per step, a cursor per entity) and by the change feed (read every
CDC_EVERY seconds once the backfill is done). A record is found by its external ref. A newer revision reverses the entry
it replaces and posts the new one; an edit to a closed month posts in the open period and is flagged as a restatement.
A bank row takes the provider's entry for it when one fits, and only an unmatched row is written out. A provider call
never runs inside a transaction: each step reads, calls, then records what came back in a transaction of its own."""
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

from . import banking, documents, handlers, posting
from .context import Ctx, load
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import mirror as M
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, invalid
from .core.org import SYSTEM
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL
from .store import mirror as SX
from .store import tico

OUTBOX_MOST = 50                            # writes out claimed in one pass
RETRY = (60, 300, 1800, 3600)               # seconds to wait after each failed try of a write (the last repeats)
CLAIM_MINUTES = 10                          # a write claimed this long ago with no answer was cut off mid-call
MATCH_MOST = 500                            # bank rows looked at in one pass to find their provider entry
# In the order the books take them in: documents need the parties and accounts, payments need the documents.
ENTITIES = ("accounts", "parties", "documents", "payments", "entries")
TAG_SETTING = "mirror.tags"                 # {"enabled": bool, "map": {tag kind: {tag value: provider class id}}}
# Actions a mirrored book refuses: the provider holds these records, so Tico cannot write them here.
REFUSED = ("bill_create", "bill_update", "bill_approve", "bill_schedule", "bill_mark_paid", "bill_void",
           "invoice_create", "invoice_send", "invoice_record_payment", "invoice_write_off", "invoice_void",
           "payment_create", "payment_cancel", "recipient_add", "contact_create", "contact_update",
           "account_create", "account_update", "feed_match")

Record = M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Ext
DOC_TABLES = {"Bill": "fin_bills", "Invoice": "fin_invoices"}   # a document kind with a row of its own
TABLE_OF = {"Account": "fin_accounts", "JournalEntry": "fin_entries", "CreditMemo": "fin_entries",
            "BillPayment": "fin_entries", "Payment": "fin_entries", "Bill": "fin_bills", "Invoice": "fin_invoices"}
GROUP_OF = {"Account": "accounts", "Vendor": "parties", "Customer": "parties", "Bill": "documents",
            "Invoice": "documents", "CreditMemo": "documents", "BillPayment": "payments", "Payment": "payments",
            "JournalEntry": "entries"}


@dataclass(frozen=True)
class Read:
    ok: bool
    records: tuple[Record, ...] = ()
    error: str = ""
    seen: int = -1          # provider rows read (records can be fewer: a row no record fits); -1 when not counted


@dataclass(frozen=True)
class Sent:
    ok: bool
    kind: str = ""
    external_id: str = ""
    revision: str = ""
    error: str = ""
    final: bool = False                     # the provider refused the write itself: it is not tried again


class Books(Protocol):
    """A provider's books as the mirror reads and writes them; each provider has an adapter (qbo.py for QuickBooks)."""
    entities: tuple[str, ...]

    def page(self, entity: str, start: int, size: int) -> Read: ...
    def changed(self, since: str) -> Read: ...
    def send(self, op: str, body: Mapping[str, Any], key: str) -> Sent: ...


class _Missing(Exception):
    """A write waits: an account or contact it names is not in the provider yet."""


# ------------------------------------------------------------------------------------------------ records in
def _table(kind: str) -> str:
    return TABLE_OF.get(kind, "fin_contacts")


def _group(r: Record) -> str:
    if isinstance(r, M.ChartAccount):
        return "accounts"
    if isinstance(r, M.Journal):
        return "entries"
    if isinstance(r, M.Party):
        return "parties"
    if isinstance(r, M.Document):
        return "documents"
    if isinstance(r, M.Settlement):
        return "payments"
    return GROUP_OF.get(r.kind, "parties")


def apply(c: sqlite3.Connection, cid: str, records: Sequence[Record], at: str) -> None:
    """Take in provider records: accounts first (parties and entries name them), then parties, then entries. Each
    record is its own savepoint; a record the books cannot take is marked unmapped and the rest go in."""
    for group in ENTITIES:
        for r in records:
            if _group(r) == group:
                _unit(c, cid, r, at)
    _link_open_rows(c, cid, at)


def _unit(c: sqlite3.Connection, cid: str, r: Record, at: str) -> None:
    c.execute("SAVEPOINT mirror_unit")
    try:
        _do(c, load(c, at), cid, r, at)
    except (FinanceError, sqlite3.IntegrityError) as e:
        c.execute("ROLLBACK TO mirror_unit")
        _unmapped(c, cid, r, e.message if isinstance(e, FinanceError) else str(e), at)
    c.execute("RELEASE mirror_unit")


def _do(c: sqlite3.Connection, ctx: Ctx, cid: str, r: Record, at: str) -> None:
    if isinstance(r, M.Ext):
        _gone(c, ctx, cid, r, at)
    elif isinstance(r, M.ChartAccount):
        _account(c, ctx, cid, r, at)
    elif isinstance(r, M.Party):
        _party(c, ctx, cid, r, at)
    elif isinstance(r, M.Document):
        _document(c, ctx, cid, r, at)
    elif isinstance(r, M.Settlement):
        _settlement(c, ctx, cid, r, at)
    else:
        _journal(c, ctx, cid, r, at)


def _have(row: Any) -> tuple[str, str] | None:
    return None if row is None else (str(row["revision"]), str(row["status"]))


def _bind(c: sqlite3.Connection, cid: str, table: str, local_id: str, ext: M.Ext, at: str,
          status: str = "active") -> None:
    row = SX.ref(c, cid, table, ext.key)
    if row is None:
        SX.ref_insert(c, cid=cid, local_table=table, local_id=local_id, kind=ext.kind, external_id=ext.key,
                      revision=ext.revision, status=status, at=at, by=SYSTEM)
    else:
        SX.ref_set(c, str(row["id"]), local_id=local_id, revision=ext.revision, status=status, at=at)


def _unmapped(c: sqlite3.Connection, cid: str, r: Record, why: str, at: str) -> None:
    """A record the books could not take in: its ref says so (and it is taken in again on the next read), unless the
    books already hold it, which an error on a change never undoes. Always audited."""
    ext = r if isinstance(r, M.Ext) else r.ext
    table = _table(ext.kind)
    row = SX.ref(c, cid, table, ext.key)
    if row is None:
        SX.ref_insert(c, cid=cid, local_table=table, local_id="", kind=ext.kind, external_id=ext.key,
                      revision=ext.revision, status="unmapped", at=at, by=SYSTEM)
    elif str(row["status"]) == "unmapped":
        SX.ref_set(c, str(row["id"]), local_id="", revision=ext.revision, status="unmapped", at=at)
    tico.audit(c, SYSTEM, "finance.mirror.unmapped", ext.key, {"why": why[:300], "connection_id": cid})


def _local_account(ctx: Ctx, row: Any, code: str) -> L.Account | None:
    local = str(row["local_id"]) if row is not None else ""
    if local and local in ctx.accounts:
        return ctx.accounts[local]
    return next((x for x in ctx.accounts.values() if x.code == code), None)


def _account(c: sqlite3.Connection, ctx: Ctx, cid: str, a: M.ChartAccount, at: str) -> None:
    row = SX.ref(c, cid, "fin_accounts", a.ext.key)
    if M.decide(_have(row), a.ext) in (M.SAME, M.OLDER, M.GONE):
        return
    current = _local_account(ctx, row, a.code)
    if current is None:
        new = L.Account(id=SL.new_id(), book_id=ctx.book.id, code=a.code, name=a.name, type=a.type,
                        subtype=a.subtype, active=a.active)
        L.check_account(new, ctx.accounts)
        SL.insert_account(c, new, SYSTEM, at)
    else:
        # The type stays as it was given: an account that may hold lines does not change kind.
        new = replace(current, code=a.code, name=a.name, subtype=a.subtype, active=a.active)
        L.check_account(new, ctx.accounts)
        SL.update_account(c, new, at)
    _bind(c, cid, "fin_accounts", new.id, a.ext, at)


def _party(c: sqlite3.Connection, ctx: Ctx, cid: str, p: M.Party, at: str) -> None:
    row = SX.ref(c, cid, "fin_contacts", p.ext.key)
    if M.decide(_have(row), p.ext) in (M.SAME, M.OLDER, M.GONE):
        return
    local = str(row["local_id"]) if row is not None else ""
    current = SX.contact(c, local) if local else None
    if current is None:
        current = SX.contact_by_name(c, ctx.book.id, p.name)     # a party the books already keep by name
    if current is None:
        local = SL.new_id()
        SX.contact_insert(c, book_id=ctx.book.id, contact_id=local, name=p.name, kind=p.kind, emails=p.emails,
                          active=p.active, at=at, by=SYSTEM)
    else:
        local = str(current["id"])
        SX.contact_update(c, local, name=p.name, kind=p.kind, emails=p.emails, active=p.active, at=at)
    _bind(c, cid, "fin_contacts", local, p.ext, at)


def _gone(c: sqlite3.Connection, ctx: Ctx, cid: str, ext: M.Ext, at: str) -> None:
    """A record deleted at the provider: an entry is reversed; an account or party is made inactive."""
    row = SX.ref(c, cid, _table(ext.kind), ext.key)
    if M.decide(_have(row), ext) != M.DELETE:
        return
    table, local = str(row["local_table"]), str(row["local_id"])
    if table == "fin_entries":
        _retire(c, ctx, cid, local, ext.key, at)
    elif table == "fin_accounts":
        account = ctx.accounts.get(local)
        if account is not None:
            SL.update_account(c, replace(account, active=False), at)
    elif table in ("fin_bills", "fin_invoices"):
        _void(c, ctx, table, local, at)
    else:
        SX.contact_deactivate(c, local, at)
    _bind(c, cid, table, local, ext, at, status="deleted")


def _mapped(c: sqlite3.Connection, cid: str, table: str, key: str) -> str:
    row = SX.ref(c, cid, table, key)
    if row is None or str(row["status"]) == "unmapped" or not str(row["local_id"]):
        raise FinanceError(NOT_FOUND, f"{key} is not in the books")
    return str(row["local_id"])


def _entry(c: sqlite3.Connection, cid: str, ctx: Ctx, j: M.Journal, day: str) -> L.Entry:
    lines = tuple(L.Line(account_id=_mapped(c, cid, "fin_accounts", x.account), amount=x.amount,
                         currency=ctx.book.base_currency, original_amount=x.amount, fx_rate=Decimal(1),
                         contact_id=_mapped(c, cid, "fin_contacts", x.party) if x.party else "",
                         description=x.description) for x in j.lines)
    return L.Entry(book_id=ctx.book.id, entry_date=day, memo=j.memo,
                   source=j.source if j.source in L.SOURCES else "sync",
                   source_id=f"{j.ext.key}:{j.ext.revision}", lines=lines)


def _restate(c: sqlite3.Connection, ctx: Ctx, day: str, change: str, entry_id: str | None,
             reversal_id: str | None, cid: str, key: str, at: str) -> None:
    """A change that landed in a closed month: the month stays closed, the change posted in the open period, and the
    month is named here so a person can see what moved."""
    period = SL.period(c, ctx.book.id, day)
    SX.restatement_insert(c, book_id=ctx.book.id, period_id=str(period["id"]), day=day, change=change,
                          entry_id=entry_id, reversal_id=reversal_id, cid=cid, external_id=key, at=at, by=SYSTEM)


def _unsettle_entry(c: sqlite3.Connection, ctx: Ctx, entry_id: str) -> None:
    """Undo the settlements a payment's entry made, before the entry is reversed: each document owes its money again."""
    for s in SX.settlements_of(c, entry_id):
        documents.unsettle(ctx, s)


def _book(c: sqlite3.Connection, ctx: Ctx, cid: str, ext: M.Ext, verdict: str, old: str, day: str,
          make: Callable[[str], L.Entry], at: str) -> str:
    """Post the entry a provider record needs, reversing the entry it replaces (`old`, "" for none). The new entry posts
    first, so a failure reverses nothing. A replaced entry's settlements are undone before it is reversed, and a change
    that lands in a closed month is flagged as a restatement. Returns the new entry's id."""
    closed = SL.closed_on(c, ctx.book.id, day)
    new_id, _ = posting.post(ctx, make(banking.open_day(ctx, day)), by=SYSTEM)
    replaced = SL.entry_row(c, old) if verdict == M.REPLACE and old else None
    replaced_closed = False
    if replaced is not None and str(replaced["status"]) == "posted" and not replaced["reverses_id"]:
        old_day = str(replaced["entry_date"])
        _unsettle_entry(c, ctx, str(replaced["id"]))
        rid = posting.reverse(ctx, str(replaced["id"]), day=banking.open_day(ctx, old_day),
                              memo="revised at the provider", by=SYSTEM)
        SX.link_feed(c, str(replaced["id"]), new_id)
        replaced_closed = SL.closed_on(c, ctx.book.id, old_day)
        if replaced_closed:
            _restate(c, ctx, old_day, "replaced", new_id, rid, cid, ext.key, at)
    if closed and not replaced_closed:
        _restate(c, ctx, day, "added", new_id, None, cid, ext.key, at)
    return new_id


def _journal(c: sqlite3.Connection, ctx: Ctx, cid: str, j: M.Journal, at: str) -> None:
    row = SX.ref(c, cid, "fin_entries", j.ext.key)
    verdict = M.decide(_have(row), j.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    old = str(row["local_id"]) if verdict == M.REPLACE and row is not None and row["local_id"] else ""
    new_id = _book(c, ctx, cid, j.ext, verdict, old, j.day, lambda when: _entry(c, cid, ctx, j, when), at)
    _bind(c, cid, "fin_entries", new_id, j.ext, at)


def _retire(c: sqlite3.Connection, ctx: Ctx, cid: str, local: str, key: str, at: str) -> None:
    """Reverse an entry the provider deleted, and let the bank rows that pointed at it wait again."""
    entry = SL.entry_row(c, local)
    if entry is None or str(entry["status"]) != "posted" or entry["reverses_id"]:
        return
    day = str(entry["entry_date"])
    closed = SL.closed_on(c, ctx.book.id, day)
    _unsettle_entry(c, ctx, local)
    rid = posting.reverse(ctx, local, day=banking.open_day(ctx, day), memo="deleted at the provider", by=SYSTEM)
    SX.unlink_feed(c, local, at)
    if closed:
        _restate(c, ctx, day, "deleted", local, rid, cid, key, at)


# ------------------------------------------------------------------------------------------------ documents and payments
def _void(c: sqlite3.Connection, ctx: Ctx, table: str, local: str, at: str) -> None:
    """A bill or invoice deleted at the provider: its entry is reversed and the document is void. The payments that
    settled it stay as they are, since the provider keeps them too."""
    doc = SAP.get(c, table, local)
    if doc is None:
        return
    entry = str(doc["entry_id"] or "")
    if entry:
        _retire_entry_only(c, ctx, entry)
    SAP.update(c, table, local, {"status": "void", "updated_at": at})


def _retire_entry_only(c: sqlite3.Connection, ctx: Ctx, entry_id: str) -> None:
    """Reverse a document's own entry (a posted one; a reversal or a reversed entry is left alone)."""
    entry = SL.entry_row(c, entry_id)
    if entry is None or str(entry["status"]) != "posted" or entry["reverses_id"]:
        return
    posting.reverse(ctx, entry_id, day=banking.open_day(ctx, str(entry["entry_date"])),
                    memo="deleted at the provider", by=SYSTEM)


def _doc_lines(c: sqlite3.Connection, cid: str, d: M.Document) -> list[tuple[str, int, str]]:
    """A document's lines as (local account id, amount, description), amounts positive. Raises unless every line has an
    account the books hold and the lines add up to the document's total."""
    if not d.party:
        raise FinanceError(NOT_AVAILABLE, "the document names no party")
    rows: list[tuple[str, int, str]] = []
    for x in d.lines:
        if not x.account:
            raise FinanceError(NOT_AVAILABLE, "a line names no account the books hold (its item has none)")
        if x.amount < 0:
            raise FinanceError(NOT_AVAILABLE, "a line has a negative amount, which this books cannot take in")
        rows.append((_mapped(c, cid, "fin_accounts", x.account), x.amount, x.description))
    if not rows:
        raise FinanceError(NOT_AVAILABLE, "the document has no lines")
    if sum(amount for _, amount, _ in rows) != d.total:
        raise FinanceError(NOT_AVAILABLE, "the lines do not add up to the total (tax or discounts are not taken in yet)")
    return rows


def _doc_entry(ctx: Ctx, *, control_kind: str, sign: int, source: str, source_id: str, contact: str,
               rows: Sequence[tuple[str, int, str]], memo: str, day: str) -> L.Entry:
    """A document's entry: its lines (sign +1 debits, -1 credits) against its control account for the same total."""
    base = ctx.book.base_currency
    lines = [L.Line(account_id=account, amount=sign * amount, currency=base, original_amount=sign * amount,
                    fx_rate=Decimal(1), contact_id=contact, description=description[:200])
             for account, amount, description in rows]
    total = sum(amount for _, amount, _ in rows)
    control = documents.control(ctx, control_kind).id
    lines.append(L.Line(account_id=control, amount=-sign * total, currency=base, original_amount=-sign * total,
                        fx_rate=Decimal(1), contact_id=contact))
    return L.Entry(book_id=ctx.book.id, entry_date=day, memo=memo[:200], source=source, source_id=source_id,
                   lines=tuple(lines))


def _document(c: sqlite3.Connection, ctx: Ctx, cid: str, d: M.Document, at: str) -> None:
    """A bill or invoice (its own row, its lines and its entry) or a credit memo (its entry only). A newer revision
    replaces the entry and the lines; the document keeps its id, so the payments that settled it still point at it."""
    if d.ext.kind == "CreditMemo":
        _credit(c, ctx, cid, d, at)
        return
    table = DOC_TABLES[d.ext.kind]
    kind = "bill" if table == "fin_bills" else "invoice"
    row = SX.ref(c, cid, table, d.ext.key)
    verdict = M.decide(_have(row), d.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    contact = _mapped(c, cid, "fin_contacts", d.party)
    rows = _doc_lines(c, cid, d)
    total = sum(amount for _, amount, _ in rows)
    if verdict == M.REPLACE and row is not None and row["local_id"]:
        local = str(row["local_id"])
        doc = SAP.get(c, table, local)
        if doc is None:
            raise FinanceError(CONFLICT, "the document this revises is no longer in the books")
        old_entry = str(doc["entry_id"] or "")
        paid = int(doc["amount_paid"])
        written_off = int(doc["written_off"]) if kind == "invoice" else 0
        state = _doc_state(kind, total, paid, written_off)
        SAP.update(c, table, local, _doc_update(kind, contact, d, total, state, at))
        SAP.replace_lines(c, kind, local, _line_rows(rows, at))
    else:
        old_entry = ""
        local = SAP.insert(c, table, _doc_row(ctx, kind, contact, d, total, _doc_state(kind, total, 0, 0), at))
        SAP.replace_lines(c, kind, local, _line_rows(rows, at))
    sign = 1 if kind == "bill" else -1
    new_entry = _book(c, ctx, cid, d.ext, verdict, old_entry, d.day,
                      lambda when: _doc_entry(ctx, control_kind=kind, sign=sign, source=kind, source_id=local,
                                              contact=contact, rows=rows, memo=_doc_label(d), day=when), at)
    SAP.update(c, table, local, {"entry_id": new_entry, "updated_at": at})
    _bind(c, cid, table, local, d.ext, at)


def _credit(c: sqlite3.Connection, ctx: Ctx, cid: str, d: M.Document, at: str) -> None:
    """A credit memo: debits to income against receivables, one entry (it has no row of its own)."""
    row = SX.ref(c, cid, "fin_entries", d.ext.key)
    verdict = M.decide(_have(row), d.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    contact = _mapped(c, cid, "fin_contacts", d.party)
    rows = _doc_lines(c, cid, d)
    old = str(row["local_id"]) if verdict == M.REPLACE and row is not None and row["local_id"] else ""
    new_id = _book(c, ctx, cid, d.ext, verdict, old, d.day,
                   lambda when: _doc_entry(ctx, control_kind="invoice", sign=1, source="invoice",
                                           source_id=f"{d.ext.key}:{d.ext.revision}", contact=contact, rows=rows,
                                           memo=_doc_label(d), day=when), at)
    _bind(c, cid, "fin_entries", new_id, d.ext, at)


def _doc_label(d: M.Document) -> str:
    return f"{d.ext.kind} {d.number or d.ext.external_id} · {d.memo}".strip(" ·")


def _doc_state(kind: str, total: int, paid: int, written_off: int) -> str:
    """A document's status from what it owes: settled, partly paid (an invoice), or open."""
    if paid + written_off >= total:
        return "paid"
    if kind == "invoice":
        return "partial" if paid else "sent"
    return "open"


def _doc_update(kind: str, contact: str, d: M.Document, total: int, state: str, at: str) -> dict[str, object]:
    """The fields a provider revision sets on a document's row."""
    fields: dict[str, object] = {"number": d.number, "status": state, "total": total, "due_date": d.due or None,
                                 "updated_at": at}
    if kind == "bill":
        fields.update({"vendor_id": contact, "bill_date": d.day})
    else:
        fields.update({"customer_id": contact, "invoice_date": d.day})
    return fields


def _doc_row(ctx: Ctx, kind: str, contact: str, d: M.Document, total: int, state: str, at: str) -> dict[str, object]:
    """A new document row: its update fields plus the book, currency and creation of a row the books make."""
    return {**_doc_update(kind, contact, d, total, state, at), "book_id": ctx.book.id,
            "currency": ctx.book.base_currency, "created_at": at, "created_by": SYSTEM}


def _line_rows(rows: Sequence[tuple[str, int, str]], at: str) -> list[dict[str, object]]:
    return [{"account_id": account, "amount": amount, "description": description[:200], "tags_json": "{}",
             "created_at": at, "created_by": SYSTEM} for account, amount, description in rows]


def _settle_target(c: sqlite3.Connection, cid: str, key: str) -> tuple[str, str]:
    """A payment's link to a bill or invoice: (the document kind, its local id). Raises when it is not in the books."""
    name, _, _ = key.partition(":")
    if name not in DOC_TABLES:
        raise FinanceError(NOT_AVAILABLE, f"{key} is not a bill or an invoice")
    return ("bill" if name == "Bill" else "invoice"), _mapped(c, cid, DOC_TABLES[name], key)


def _settlement(c: sqlite3.Connection, ctx: Ctx, cid: str, s: M.Settlement, at: str) -> None:
    """A bill payment (Dr payables, Cr bank) or a received payment (Dr bank, Cr receivables), and the bills or invoices
    it settles: each link is a settlement, so the document's paid amount and status move with the money."""
    row = SX.ref(c, cid, "fin_entries", s.ext.key)
    verdict = M.decide(_have(row), s.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    if not s.party:
        raise FinanceError(NOT_AVAILABLE, "the payment names no party")
    if not s.bank:
        raise FinanceError(NOT_AVAILABLE, "the payment names no bank account the books hold")
    if sum(amount for _, amount in s.links) > s.total:
        raise FinanceError(NOT_AVAILABLE, "the payment settles more than its total")
    bill = s.ext.kind == "BillPayment"
    contact = _mapped(c, cid, "fin_contacts", s.party)
    bank = _mapped(c, cid, "fin_accounts", s.bank)
    targets = [(*_settle_target(c, cid, key), amount) for key, amount in s.links]
    control = documents.control(ctx, "bill" if bill else "invoice").id
    base = ctx.book.base_currency

    def line(account: str, amount: int, party: str) -> L.Line:
        return L.Line(account_id=account, amount=amount, currency=base, original_amount=amount, contact_id=party)

    lines = (line(control, s.total, contact), line(bank, -s.total, "")) if bill else \
        (line(bank, s.total, ""), line(control, -s.total, contact))
    old = str(row["local_id"]) if verdict == M.REPLACE and row is not None and row["local_id"] else ""
    entry = _book(c, ctx, cid, s.ext, verdict, old, s.day,
                  lambda when: L.Entry(book_id=ctx.book.id, entry_date=when, memo=(s.memo or s.ext.kind)[:200],
                                       source="sync", source_id=f"{s.ext.key}:{s.ext.revision}", lines=lines),
                  at)
    for kind, doc_id, amount in targets:
        documents.settle(ctx, kind, doc_id, amount=amount, day=s.day, from_kind="account", from_id=bank,
                         entry_id=entry, by=SYSTEM)
    _bind(c, cid, "fin_entries", entry, s.ext, at)


# ------------------------------------------------------------------------------------------------ bank rows
def _match(ctx: Ctx, gl: str, amount: int, txn_date: str) -> str | None:
    """The provider's entry a bank row is: a line on its account for the same amount, dated near it."""
    day = dates.day(txn_date)
    start, end = (day - timedelta(days=M.MATCH_DAYS)).isoformat(), (day + timedelta(days=M.MATCH_DAYS)).isoformat()
    return M.match_row(amount, txn_date, SX.candidates(ctx.c, ctx.book.id, gl, amount, start, end))


def _link(ctx: Ctx, fid: str, entry_id: str) -> None:
    SK.update(ctx.c, "fin_feed_txns", fid, {"status": "matched", "entry_id": entry_id, "updated_at": ctx.at})
    SX.cancel_feed_writes(ctx.c, fid, "the provider's entry for this bank row came first", ctx.at)


def _link_open_rows(c: sqlite3.Connection, cid: str, at: str) -> None:
    """Bank rows still waiting after a batch of records look for their entry among what just came in."""
    ctx = load(c, at)
    if ctx.book.mode != "mirror":
        return
    for row in SX.unmatched_rows(c, ctx.book.id, MATCH_MOST):
        hit = _match(ctx, str(row["gl_id"]), int(row["amount"]), str(row["txn_date"]))
        if hit:
            _link(ctx, str(row["id"]), hit)


def _still_matches(ctx: Ctx, row: Any, money: Any) -> bool:
    entry = SL.entry_row(ctx.c, str(row["entry_id"]))
    if entry is None or str(entry["status"]) != "posted":
        return False
    gl = str(money["account_id"])
    moved = sum(x.amount for _, x in SL.entry_lines(ctx.c, str(row["entry_id"])) if x.account_id == gl)
    return moved == int(row["amount"])


def settle_row(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> None:
    """A bank row as the feed states it, in a mirror. A row the bank cancelled is ignored (the provider's entry is never
    reversed from here). A settled row keeps the entry it has, or finds the provider's entry for it."""
    fid = str(row["id"])
    if t.void or t.reversed:
        SX.cancel_feed_writes(ctx.c, fid, "the bank cancelled this transaction", ctx.at)
        SK.update(ctx.c, "fin_feed_txns", fid, {"status": "ignored", "entry_id": None, "updated_at": ctx.at})
        return
    if t.pending or str(row["status"]) == "ignored":
        return
    if row["entry_id"]:
        if _still_matches(ctx, row, money):
            return
        SK.update(ctx.c, "fin_feed_txns", fid, {"status": "new", "entry_id": None, "updated_at": ctx.at})
    hit = _match(ctx, str(money["account_id"]), int(row["amount"]), str(row["txn_date"]))
    if hit:
        _link(ctx, fid, hit)


def check_feed(ctx: Ctx, row: Any, *, ignore: bool) -> None:
    """Plan time: a bank row changes in a mirror only while no provider entry holds it. Ignoring one also withdraws
    what was waiting to go out for it."""
    if row["entry_id"]:
        raise FinanceError(CONFLICT, "that row is matched to an entry in the provider; change that entry there")
    if not ignore and SX.writes_for_feed(ctx.c, str(row["id"])):
        raise FinanceError(CONFLICT, "that row's entry is already waiting to go to the provider")


def ignore_feed(ctx: Ctx, row: Any) -> None:
    fid = str(row["id"])
    SX.cancel_feed_writes(ctx.c, fid, "the bank row was ignored", ctx.at)
    SK.update(ctx.c, "fin_feed_txns", fid, {"status": "ignored", "entry_id": None, "updated_at": ctx.at})


def write_feed(ctx: Ctx, row: Any, account: L.Account, tags: Mapping[str, str], gl: L.Account, *, by: str,
               action_id: str) -> handlers.Result:
    """Categorize a bank row in a mirror: the provider's entry if one fits, else an entry written out to the provider
    (the row stays new until the provider confirms it)."""
    fid, amount = str(row["id"]), int(row["amount"])
    hit = _match(ctx, gl.id, amount, str(row["txn_date"]))
    if hit:
        _link(ctx, fid, hit)
        return {"feed_id": fid, "entry_id": hit, "matched": True}
    lines = [L.Line(account_id=gl.id, amount=amount, currency=ctx.book.base_currency, original_amount=amount,
                    role=L.MONEY_ROLE),
             L.Line(account_id=account.id, amount=-amount, currency=ctx.book.base_currency, original_amount=-amount,
                    tags=dict(tags))]
    oid = _queue(ctx.c, _cid(ctx), "journal.create",
                 _body(str(row["txn_date"]), str(row["counterparty"]), "bank", fid, lines, feed=fid),
                 by=by, action_id=action_id, at=ctx.at)
    return {"feed_id": fid, "outbox_id": oid, "matched": False}


# ------------------------------------------------------------------------------------------------ writes out
def _cid(ctx: Ctx) -> str:
    if ctx.book.mode != "mirror" or not ctx.book.mirror_connection_id:
        raise FinanceError(NOT_AVAILABLE, "the books are not mirrored")
    return ctx.book.mirror_connection_id


def _body(day: str, memo: str, source: str, source_id: str, lines: Sequence[L.Line], *, feed: str = "",
          local: str = "") -> dict[str, Any]:
    """A journal entry's write as Tico holds it: local account and contact ids, translated to the provider's keys
    when it is sent."""
    return {"date": day, "memo": memo, "source": source, "source_id": source_id, "feed_id": feed,
            "local_entry_id": local, "export": bool(local),
            "lines": [{"account_id": x.account_id, "amount": x.amount, "contact_id": x.contact_id,
                       "description": x.description, "tags": dict(x.tags), "role": x.role} for x in lines]}


def _queue(c: sqlite3.Connection, cid: str, op: str, body: Mapping[str, Any], *, by: str, at: str,
           action_id: str = "", key: str = "") -> str:
    return SK.outbox_add(c, connection_id=cid, action_id=action_id, op=op, payload=body,
                         key=key or f"{op}:{SL.new_id()}", by=by, at=at)


def _entry_ref(ctx: Ctx, entry_id: str) -> Any:
    ref = SX.ref_for_local(ctx.c, _cid(ctx), "fin_entries", entry_id)
    if ref is None or str(ref["status"]) != "active":
        raise FinanceError(CONFLICT, "that entry is not in the provider yet")
    return ref


def check_reversal(ctx: Ctx, entry_id: str) -> None:
    """Plan time: an entry can be reversed in a mirror only once the provider holds it."""
    _entry_ref(ctx, entry_id)


def write_entry(ctx: Ctx, e: L.Entry, *, by: str, action_id: str) -> handlers.Result:
    oid = _queue(ctx.c, _cid(ctx), "journal.create", _body(e.entry_date, e.memo, e.source, e.source_id, e.lines),
                 by=by, action_id=action_id, at=ctx.at)
    return {"outbox_id": oid}


def write_reversal(ctx: Ctx, entry_id: str, *, day: str, memo: str, by: str, action_id: str) -> handlers.Result:
    ref = _entry_ref(ctx, entry_id)
    body = {"ext": str(ref["external_id"]), "revision": str(ref["revision"]), "entry_id": entry_id, "date": day,
            "memo": memo, "feed_id": ""}
    oid = _queue(ctx.c, _cid(ctx), "journal.delete", body, by=by, action_id=action_id, at=ctx.at)
    return {"outbox_id": oid, "reverses": entry_id}


def _class_map(c: sqlite3.Connection) -> dict[str, dict[str, str]]:
    cfg = SX.as_map(SL.setting(c, TAG_SETTING))
    if not cfg.get("enabled") or not isinstance(cfg.get("map"), dict):
        return {}
    return cast(dict[str, dict[str, str]], cfg["map"])


def _key(c: sqlite3.Connection, cid: str, table: str, local_id: str) -> str | None:
    row = SX.ref_for_local(c, cid, table, local_id)
    return None if row is None or str(row["status"]) == "unmapped" else str(row["external_id"])


def _translate(c: sqlite3.Connection, cid: str, op: str, local: Mapping[str, Any]) -> dict[str, Any]:
    """What the provider is sent: its keys for accounts and parties, and the tags as its classes when that is on. A
    write waits (_Missing) while an account or party it names is not in the provider yet; an export drops a contact
    the provider does not hold."""
    if op != "journal.create":
        return dict(local)
    export = bool(local.get("local_entry_id"))
    mapping = _class_map(c)
    lines: list[dict[str, Any]] = []
    for x in local["lines"]:
        account = _key(c, cid, "fin_accounts", str(x["account_id"]))
        if account is None:
            raise _Missing(f"account {x['account_id']} is not in the provider yet")
        party = ""
        if x["contact_id"]:
            party = _key(c, cid, "fin_contacts", str(x["contact_id"])) or ""
            if not party and not export:
                raise _Missing(f"contact {x['contact_id']} is not in the provider yet")
        lines.append({"account": account, "party": party, "amount": x["amount"], "description": x["description"],
                      "role": x["role"], "classes": M.class_refs(x["tags"], mapping)})
    return {"date": local["date"], "memo": local["memo"], "source": local["source"], "lines": lines,
            "export": export}


def _finish(c: sqlite3.Connection, o: Any, why: str, now: str, status: str) -> None:
    SK.update(c, "fin_outbox", str(o["id"]), {"status": status, "claimed_at": None, "last_error": why[:300],
                                              "updated_at": now})


def _later(c: sqlite3.Connection, o: Any, why: str, now: str) -> None:
    SK.update(c, "fin_outbox", str(o["id"]), {"claimed_at": None, "next_try_at": M.stamp(now, RETRY[0] * 1_000_000),
                                              "last_error": why[:300], "updated_at": now})


def _prepare(c: sqlite3.Connection, cid: str, o: Any, now: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    local = SK.loads(o["payload_json"])
    feed = str(local.get("feed_id") or "")
    if feed:
        frow = SK.get(c, "fin_feed_txns", feed)
        if frow is None or str(frow["status"]) != "new":
            _finish(c, o, "the bank row was matched or ignored before this went out", now, "failed")
            return None
    try:
        provider = _translate(c, cid, str(o["op"]), local)
    except _Missing as e:
        _later(c, o, str(e), now)
        return None
    return local, provider


def _post(ctx: Ctx, cid: str, local: Mapping[str, Any], key: str, now: str) -> str:
    """A written-out entry posted here once the provider confirms it: in an open period when its own is closed."""
    day = str(local["date"])
    closed = SL.closed_on(ctx.c, ctx.book.id, day)
    lines = tuple(L.Line(account_id=str(x["account_id"]), amount=int(x["amount"]), currency=ctx.book.base_currency,
                         original_amount=int(x["amount"]), fx_rate=Decimal(1), contact_id=str(x["contact_id"]),
                         description=str(x["description"]), tags=dict(x["tags"]), role=str(x["role"]))
                  for x in local["lines"])
    e = L.Entry(book_id=ctx.book.id, entry_date=banking.open_day(ctx, day), memo=str(local["memo"]),
                source=str(local["source"]), source_id=str(local["source_id"]), lines=lines)
    entry_id, _ = posting.post(ctx, e, by=SYSTEM)
    if closed:
        _restate(ctx.c, ctx, day, "added", entry_id, None, cid, key, now)
    return entry_id


def _confirm_entry(c: sqlite3.Connection, cid: str, local: Mapping[str, Any], ext: M.Ext, now: str) -> None:
    ctx = load(c, now)
    exported = str(local.get("local_entry_id") or "")
    if exported:
        _bind(c, cid, "fin_entries", exported, ext, now)
        return
    row = SX.ref(c, cid, "fin_entries", ext.key)
    if row is not None and str(row["status"]) == "active":
        entry = str(row["local_id"])               # the change feed brought the entry in first
    else:
        entry = _post(ctx, cid, local, ext.key, now)
        _bind(c, cid, "fin_entries", entry, ext, now)
    feed = str(local.get("feed_id") or "")
    frow = SK.get(c, "fin_feed_txns", feed) if feed else None
    if frow is not None and str(frow["status"]) == "new":
        banking.mark_posted(ctx, [feed], entry)


def _confirm(c: sqlite3.Connection, cid: str, op: str, local: Mapping[str, Any], sent: Sent, now: str) -> None:
    """The provider confirmed a write: only now does the local book change."""
    ext = M.Ext(kind=sent.kind, external_id=sent.external_id, revision=sent.revision)
    if op == "account.create":
        _bind(c, cid, "fin_accounts", str(local["local_id"]), ext, now)
    elif op == "party.create":
        _bind(c, cid, "fin_contacts", str(local["local_id"]), ext, now)
    elif op == "journal.create":
        _confirm_entry(c, cid, local, ext, now)
    elif op == "journal.delete":
        entry = str(local["entry_id"])
        row = SX.ref_for_local(c, cid, "fin_entries", entry)
        if row is not None:
            SX.ref_set(c, str(row["id"]), local_id=entry, revision=str(row["revision"]), status="deleted", at=now)
        _retire(c, load(c, now), cid, entry, str(local["ext"]), now)


def _record(c: sqlite3.Connection, cid: str, o: Any, local: Mapping[str, Any], sent: Sent, now: str) -> None:
    oid, attempts = str(o["id"]), int(o["attempts"]) + 1
    if sent.ok:
        SK.update(c, "fin_outbox", oid, {"status": "sent", "attempts": attempts, "claimed_at": None,
                                         "last_error": None, "updated_at": now})
        _confirm(c, cid, str(o["op"]), local, sent, now)
    elif sent.final:
        SK.update(c, "fin_outbox", oid, {"status": "failed", "attempts": attempts, "claimed_at": None,
                                         "last_error": sent.error[:300], "updated_at": now})
    else:
        delay = RETRY[min(attempts, len(RETRY)) - 1]
        SK.update(c, "fin_outbox", oid, {"attempts": attempts, "claimed_at": None, "last_error": sent.error[:300],
                                         "next_try_at": M.stamp(now, delay * 1_000_000), "updated_at": now})


def outbox(store: Any, books: Books, cid: str, now: str) -> list[str]:
    """This connection's writes, each claimed in a short transaction before its call (so two workers never send one
    twice) and recorded after it. A claim left from a cut-off call is released to be sent again: every write carries
    an idempotency key the provider keeps."""
    with store.transaction() as c:
        for o in SK.outbox_stuck(c, M.stamp(now, -CLAIM_MINUTES * 60 * 1_000_000), cid):
            SK.update(c, "fin_outbox", str(o["id"]), {"claimed_at": None, "updated_at": now})
        due = SK.outbox_due(c, now, OUTBOX_MOST, cid)
    problems: list[str] = []
    for o in due:
        with store.transaction() as c:
            if not SK.outbox_claim(c, str(o["id"]), now):
                continue
            prepared = _prepare(c, cid, o, now)
        if prepared is None:
            continue
        local, provider = prepared
        sent = books.send(str(o["op"]), provider, str(o["idempotency_key"]))
        with store.transaction() as c:
            _record(c, cid, o, local, sent, now)
        if not sent.ok:
            problems.append(f"{o['op']}: {sent.error}")
    return problems


# ------------------------------------------------------------------------------------------------ passes
def _backfill_state(st: Mapping[str, object]) -> dict[str, object]:
    raw = st.get("backfill")
    return dict(cast(dict[str, object], raw)) if isinstance(raw, dict) else {}


def _backfill(store: Any, books: Books, cid: str, now: str, size: int) -> str:
    """One page of the first entity not yet read through (in order: accounts, parties, entries)."""
    with store.read() as c:
        state = _backfill_state(SX.mirror_state(c, cid))
    entity = next((e for e in books.entities if M.backfill_next(state, e) is not None), None)
    if entity is None:
        return ""
    start = M.backfill_next(state, entity) or 1
    read = books.page(entity, start, size)
    if not read.ok:
        return f"backfill {entity} from {start}: {read.error}"
    counted = read.seen if read.seen >= 0 else len(read.records)     # a row no record fits still moves the cursor
    with store.transaction() as c:
        apply(c, cid, read.records, now)
        done = M.backfill_after(_backfill_state(SX.mirror_state(c, cid)), entity, start, counted, size)
        SX.set_mirror_state(c, cid, {"backfill": done})
    return ""


def _changes(store: Any, books: Books, cid: str, now: str, waiting: bool = False) -> str:
    """The change feed, once the backfill is through and it is due, or at once when a webhook is waiting."""
    with store.read() as c:
        st = SX.mirror_state(c, cid)
    raw = st.get("changed_at")
    since = raw if isinstance(raw, str) else None
    if not M.backfill_done(_backfill_state(st), ENTITIES) or not (waiting or M.cdc_due(since, now)):
        return ""
    read = books.changed(M.cdc_since(since, now))
    if not read.ok:
        return f"changes: {read.error}"
    with store.transaction() as c:
        apply(c, cid, read.records, now)
        SX.set_mirror_state(c, cid, {"changed_at": now})
    return ""


def _position(value: object) -> tuple[str, str] | None:
    """The keyset cursor the export resumes from: [created_at, id] of the last entry sent."""
    if not isinstance(value, list):
        return None
    pair = cast(list[object], value)
    return (str(pair[0]), str(pair[1])) if len(pair) == 2 else None


def _export(store: Any, cid: str, now: str, size: int) -> None:
    """A page of the entries made before the switch to mirror, written out in the order they were made."""
    with store.read() as c:
        ex = SX.as_map(SX.mirror_state(c, cid).get("export"))
    if not ex or ex.get("done"):
        return
    until, after = str(ex["until"]), ex.get("after")
    pos = _position(after)
    with store.transaction() as c:
        book = SL.book(c)
        if book is None:
            return
        rows = SX.export_entries(c, book.id, pos, until, size)
        for i, row in enumerate(rows):
            eid = str(row["id"])
            if SX.ref_for_local(c, cid, "fin_entries", eid) is not None:
                continue
            lines = [line for _, line in SL.entry_lines(c, eid)]
            body = _body(str(row["entry_date"]), str(row["memo"]), str(row["source"]), str(row["source_id"]), lines,
                         local=eid)
            _queue(c, cid, "journal.create", body, by=SYSTEM, at=M.stamp(now, i), key=f"export:{cid}:entry:{eid}")
        last = rows[-1] if rows else None
        cursor = [str(last["created_at"]), str(last["id"])] if last is not None else after
        SX.set_mirror_state(c, cid, {"export": {"until": until, "after": cursor, "done": len(rows) < size}})


def sync_once(store: Any, books: Books, cid: str, now: str, *, size: int = M.BACKFILL_SIZE,
              waiting: bool = False) -> list[str]:
    """One pass over a mirrored connection: a backfill page, the change feed when due (or when a webhook waits), the
    export page, then the writes out. Provider calls run outside transactions; the outcome is recorded on the connection."""
    problems = [p for p in (_backfill(store, books, cid, now, size), _changes(store, books, cid, now, waiting)) if p]
    _export(store, cid, now, size)
    problems += outbox(store, books, cid, now)
    with store.transaction() as c:
        SX.synced(c, cid, now, problems[0] if problems else "")
    return problems


# ------------------------------------------------------------------------------------------------ planners
def mirror_start(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
    """Switch a native book to a provider's books. Preview first: what would be written out. Applying it sets the mode;
    the accounts and parties are queued now and the entries are written out by the passes that follow."""
    V.only(p, ("connection_id",))
    cid = V.text(p, "connection_id", 80)
    if ctx.book.mode != "native":
        raise FinanceError(CONFLICT, "the books are already a provider's (mirror mode)")
    row = SK.get(ctx.c, "fin_connections", cid)
    if row is None or str(row["purpose"]) != "books":
        raise FinanceError(NOT_FOUND, "no books connection with that id")
    parties = SX.contacts(ctx.c, ctx.book.id)
    counts: dict[str, object] = {"accounts": len(ctx.accounts), "parties": len(parties),
                                 "entries": SX.entry_count(ctx.c, ctx.book.id)}

    def apply_switch(aid: str) -> handlers.Result:
        SL.set_book_mode(ctx.c, ctx.book.id, "mirror", cid)
        queued = 0
        for i, a in enumerate(sorted(ctx.accounts.values(), key=lambda x: x.code)):
            if SX.ref_for_local(ctx.c, cid, "fin_accounts", a.id) is not None:
                continue
            body = {"local_id": a.id, "code": a.code, "name": a.name, "type": a.type, "subtype": a.subtype,
                    "active": a.active}
            _queue(ctx.c, cid, "account.create", body, by=who, at=M.stamp(ctx.at, i),
                   key=f"export:{cid}:account:{a.id}")
            queued += 1
        for i, row_c in enumerate(parties):
            if SX.ref_for_local(ctx.c, cid, "fin_contacts", str(row_c["id"])) is not None:
                continue
            body = {"local_id": str(row_c["id"]), "name": str(row_c["name"]), "kind": str(row_c["kind"]),
                    "emails": SK.loads_list(row_c["emails_json"]), "active": bool(row_c["active"])}
            _queue(ctx.c, cid, "party.create", body, by=who, at=M.stamp(ctx.at, 1000 + i),
                   key=f"export:{cid}:party:{row_c['id']}")
            queued += 1
        SX.set_mirror_state(ctx.c, cid, {"export": {"until": ctx.at, "after": None, "done": False}})
        return {"connection_id": cid, "mode": "mirror", "queued": queued}

    return handlers.Plan(K.default_gate(ctx.org, who, "mirror_start"),
                         f"Switch the books to a provider's books ({cid})"[:200], apply_switch, impact=counts)


def mirror_stop(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
    """Switch a mirrored book back to native: only once nothing waits to go out and the provider was read through and
    recently. The provider's balances at the switch are kept as a snapshot."""
    V.only(p, ())
    if ctx.book.mode != "mirror" or not ctx.book.mirror_connection_id:
        raise FinanceError(CONFLICT, "the books are not mirrored")
    cid = ctx.book.mirror_connection_id
    if SX.writes_waiting(ctx.c, cid):
        raise FinanceError(CONFLICT, "writes to the provider are still waiting to go out")
    state = _backfill_state(SX.mirror_state(ctx.c, cid))
    if not M.backfill_done(state, ENTITIES):
        raise FinanceError(CONFLICT, "the provider's books are not all read yet")
    last = SX.last_ok(ctx.c, cid)
    if last is None or M.parse(ctx.at) - M.parse(last) > timedelta(days=1):
        raise FinanceError(CONFLICT, "the provider was not read in the last day")

    def apply_stop(aid: str) -> handlers.Result:
        codes = {k: ctx.accounts[k].code for k in ctx.accounts}
        balances = {codes[k]: v for k, v in SX.trial_balances(ctx.c, ctx.book.id).items() if k in codes}
        snapshot = {"at": ctx.at, "entries": SX.entry_count(ctx.c, ctx.book.id), "balances": balances}
        SL.set_setting(ctx.c, f"mirror.snapshot.{cid}", snapshot, by=who, at=ctx.at)
        SL.set_book_mode(ctx.c, ctx.book.id, "native", "")
        SX.disconnect(ctx.c, cid, ctx.at)
        return {"connection_id": cid, "mode": "native"}

    return handlers.Plan(K.default_gate(ctx.org, who, "mirror_stop"), "Switch the books back to native", apply_stop,
                         impact={"entries": SX.entry_count(ctx.c, ctx.book.id)})


def mirror_tags(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
    """Send tags to the provider as its classes. Off by default; the owner turns it on and maps each tag value."""
    V.only(p, ("enabled", "map"))
    enabled = V.flag(p, "enabled")
    mapping: dict[str, dict[str, str]] = {}
    for kind, values in V.obj(p.get("map") or {}, "map").items():
        if kind not in L.TAG_KINDS:
            raise invalid(f"a tag kind is {'|'.join(L.TAG_KINDS)}")
        for value, cls in V.obj(values, kind).items():
            if not isinstance(cls, str) or not cls or len(cls) > 80:
                raise invalid("a provider class is a name or id up to 80 characters")
            mapping.setdefault(kind, {})[str(value)] = cls

    def apply_tags(aid: str) -> handlers.Result:
        SL.set_setting(ctx.c, TAG_SETTING, {"enabled": enabled, "map": mapping}, by=who, at=ctx.at)
        return {"enabled": enabled}

    return handlers.Plan(K.default_gate(ctx.org, who, "mirror_tags"),
                         f"Send tags as provider classes: {'on' if enabled else 'off'}", apply_tags)


PLANNERS: dict[str, handlers.Planner] = {"mirror_start": mirror_start, "mirror_stop": mirror_stop,
                                         "mirror_tags": mirror_tags}
