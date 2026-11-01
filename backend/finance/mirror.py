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
from .core import ap as AP
from .core import dates
from .core import feeds as F
from .core import kinds as K
from .core import ledger as L
from .core import mirror as M
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, denied, invalid
from .core.org import SYSTEM
from .store import access as SA
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL
from .store import mirror as SX
from .store import tico

OUTBOX_MOST = 50                            # writes out claimed in one pass
RETRY = (60, 300, 1800, 3600)               # seconds to wait after each failed try of a write (the last repeats)
CLAIM_MINUTES = 10                          # a write claimed this long ago with no answer was cut off mid-call
MATCH_MOST = 500                            # bank rows looked at in one pass to find their provider entry
PENDING_MOST = 500                          # provider records the books could not take in, kept to be read again
# In the order the books take them in: documents need the parties and accounts, payments need the documents.
ENTITIES = ("accounts", "parties", "documents", "payments", "entries")   # the groups, in the order the books take them
TAG_SETTING = "mirror.tags"                 # {"enabled": bool, "map": {tag kind: {tag value: provider class id}}}
# Actions a mirrored book refuses: the provider holds these records, so Tico cannot write them here.
REFUSED = ("bill_update", "bill_approve", "bill_schedule", "invoice_send",
           "payment_create", "payment_cancel", "recipient_add", "contact_create", "contact_update",
           "account_create", "account_update", "feed_match")

Record = M.ChartAccount | M.Party | M.Journal | M.Document | M.Settlement | M.Refused | M.Ext
DOC_TABLES = {"Bill": "fin_bills", "Invoice": "fin_invoices"}   # a document kind with a row of its own
POSTED = ("Purchase", "Deposit", "Transfer", "SalesReceipt", "RefundReceipt", "VendorCredit")   # posted entries
JOURNAL_KINDS = ("ManualJournal", "BankTransaction", "BankTransfer", "Overpayment", "Prepayment")   # posted as journals
TABLE_OF = {"Account": "fin_accounts", "JournalEntry": "fin_entries", "CreditMemo": "fin_entries",
            "BillPayment": "fin_entries", "Payment": "fin_entries", "Bill": "fin_bills", "Invoice": "fin_invoices",
            **{kind: "fin_entries" for kind in POSTED + JOURNAL_KINDS}}
GROUP_OF = {"Account": "accounts", "Vendor": "parties", "Customer": "parties", "Bill": "documents",
            "Invoice": "documents", "CreditMemo": "documents", "BillPayment": "payments", "Payment": "payments",
            "JournalEntry": "entries", **{kind: "entries" for kind in POSTED + JOURNAL_KINDS}}
# A payment that only applies credit memos (no money in it) keeps its settlements as its ref's local id, comma-separated
APPLICATION_TABLE = "fin_settlements"
FOREIGN_CURRENCY = "a record in another currency: the books hold one currency"


@dataclass(frozen=True)
class Read:
    ok: bool
    records: tuple[Record, ...] = ()
    error: str = ""
    seen: int = -1          # provider rows read (records can be fewer: a row no record fits); -1 when not counted
    through: tuple[tuple[str, str], ...] = ()   # a feed cut short by the provider's cap: (feed, time it is complete to)


@dataclass(frozen=True)
class Sent:
    ok: bool
    kind: str = ""
    external_id: str = ""
    revision: str = ""
    error: str = ""
    final: bool = False                     # the provider refused the write itself: it is not tried again
    unknown: bool = False                   # a final answer the provider could not settle: the write may have landed
    record: M.Document | M.Journal | M.Settlement | None = None     # a found record, as the change feed reads it


class Books(Protocol):
    """A provider's books as the mirror reads and writes them; each provider has an adapter (qbo.py for QuickBooks).
    `find` looks for a write sent under an idempotency key before it is sent again: the provider's Sent when it holds it,
    None when it does not, and a final Sent when it cannot tell (the write is then marked unknown)."""
    entities: tuple[str, ...]
    feeds: tuple[str, ...]       # the provider's entities the change feed reads, each with its own cursor

    def page(self, entity: str, start: int, size: int) -> Read: ...
    def changed(self, since: Mapping[str, str]) -> Read: ...
    def send(self, op: str, body: Mapping[str, Any], key: str) -> Sent: ...
    def find(self, op: str, body: Mapping[str, Any], key: str) -> Sent | None: ...


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
    if isinstance(r, M.Refused):
        return GROUP_OF.get(r.ext.kind, "parties")
    return GROUP_OF.get(r.kind, "parties")


def apply(c: sqlite3.Connection, cid: str, records: Sequence[Record], at: str) -> None:
    """Take in provider records: accounts first (parties and entries name them), then parties, then entries. Each
    record is its own savepoint. A record the books cannot take in is marked unmapped and kept (see _pending) to be
    read again with the next change feed; the rest go in."""
    _adopt(c, cid, records, at)
    pending = _pending(c, cid)
    changed = False
    for group in ENTITIES:
        for r in records:
            if _group(r) != group:
                continue
            key = _ext_of(r).key
            if _unit(c, cid, r, at):
                changed = pending.pop(key, None) is not None or changed
            else:
                pending[key] = M.encode(r)
                changed = True
    while len(pending) > PENDING_MOST:
        pending.pop(next(iter(pending)))
        changed = True
    if changed:
        SX.set_mirror_state(c, cid, {"pending": pending})
    _link_open_rows(c, cid, at)


def _unit(c: sqlite3.Connection, cid: str, r: Record, at: str) -> bool:
    """One record taken in under its own savepoint. False when the books could not take it: it is marked unmapped."""
    c.execute("SAVEPOINT mirror_unit")
    try:
        _do(c, load(c, at), cid, r, at)
    except (FinanceError, sqlite3.IntegrityError) as e:
        c.execute("ROLLBACK TO mirror_unit")
        _unmapped(c, cid, r, e.message if isinstance(e, FinanceError) else str(e), at)
        c.execute("RELEASE mirror_unit")
        return False
    c.execute("RELEASE mirror_unit")
    return True


def _ext_of(r: Record) -> M.Ext:
    return r if isinstance(r, M.Ext) else r.ext


def _pending(c: sqlite3.Connection, cid: str) -> dict[str, dict[str, Any]]:
    """Provider records the books could not take in, by key. Each is read again with the change feed, so a record that
    waits on a party or an account goes in once that arrives, even when the record itself has not changed."""
    raw = SX.mirror_state(c, cid).get("pending")
    if not isinstance(raw, dict):
        return {}
    return {str(k): cast(dict[str, Any], v) for k, v in cast(dict[str, object], raw).items()}


# Creates whose record the books take in as the provider holds it (no local row waits for their confirmation)
RECORD_IMPORTED = ("account.create", "party.create")


def _adopt(c: sqlite3.Connection, cid: str, records: Sequence[Record], at: str) -> None:
    """A write the provider took before its answer was recorded (the call was cut off) comes back in the change feed
    with its reference in its note. The write is recorded as sent here, from its local payload (which keeps the tags
    its lines carry), unless the record has changed since the write (see _revised): that one is recorded at the oldest
    revision, so the record the feed brings in replaces what was sent. A credit memo's write-off is confirmed, since its
    credit application is queued by that confirmation. A write whose record is not among these records waits for its
    retry."""
    found: dict[str, Record] = {}
    for r in records:
        ext = _ext_of(r)
        if ext.reference and not ext.deleted:
            found[ext.reference] = r
    if not found:
        return
    for o in SX.pending_writes(c, cid):
        r = found.get(M.reference(str(o["idempotency_key"])))
        op = str(o["op"])
        if r is None or op not in RETRIED_CREATES:
            continue
        ext = _ext_of(r)
        local = SK.loads(o["payload"])
        c.execute("SAVEPOINT mirror_adopt")
        try:
            revised = _revised(c, cid, op, local, ext, r)
            sent = Sent(ok=True, kind=ext.kind, external_id=ext.external_id,
                        revision=(_initial_revision(c, cid) or "") if revised else ext.revision)
            _record(c, cid, o, local, _translate(c, cid, op, local), sent, at,
                    confirm=op not in RECORD_IMPORTED)
        except (_Missing, FinanceError, sqlite3.IntegrityError):
            c.execute("ROLLBACK TO mirror_adopt")
        c.execute("RELEASE mirror_adopt")


def _sent_as(op: str, local: Mapping[str, Any], record: object) -> bool:
    """Whether a provider record holds what a write sent: a bill's or invoice's lines, a journal's lines, a payment's
    total. A write with no such check (a credit application, a write-off) is taken to hold what it sent."""
    if op in ("bill.create", "invoice.create"):
        return isinstance(record, M.Document) and \
            sorted(x.amount for x in record.lines if not x.rounding) == sorted(int(x["amount"]) for x in local["lines"])
    if op == "journal.create":
        return isinstance(record, M.Journal) and \
            sorted(x.amount for x in record.lines) == sorted(int(x["amount"]) for x in local["lines"])
    if op in ("payment.bill", "payment.invoice"):
        return isinstance(record, M.Settlement) and record.total == int(local["amount"])
    return True


def _revised(c: sqlite3.Connection, cid: str, op: str, local: Mapping[str, Any], ext: M.Ext, record: object) -> bool:
    """Whether a record the provider holds for a write has changed since the write: its revision is past the one a
    write makes, for a provider that counts revisions; for one that does not (Xero stamps times), its content is not
    what was sent, so an edit made after the write is never taken for the write itself."""
    initial = _initial_revision(c, cid)
    if initial:
        return M.revision_order(ext.revision, initial) > 0
    return not _sent_as(op, local, record)




def _do(c: sqlite3.Connection, ctx: Ctx, cid: str, r: Record, at: str) -> None:
    if not isinstance(r, M.Refused) and _ext_of(r).currency not in ("", ctx.book.base_currency):
        raise FinanceError(NOT_AVAILABLE, FOREIGN_CURRENCY)
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
    elif isinstance(r, M.Refused):
        raise FinanceError(NOT_AVAILABLE, r.why)
    else:
        _journal(c, ctx, cid, r, at)


def _have(row: Any) -> tuple[str, str] | None:
    return None if row is None else (str(row["revision"]), str(row["status"]))


def _bind(c: sqlite3.Connection, cid: str, table: str, local_id: str, ext: M.Ext, at: str,
          status: str = "active") -> None:
    if status != "unmapped":
        SX.ref_drop_unmapped(c, cid, ext.key, keep=table)
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
    first = row is None or not str(row["local_id"])     # the first import of this account, before it is bound
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
    if first:
        _payroll_private(c, new.id, a, at)


def _payroll_private(c: sqlite3.Connection, account_id: str, a: M.ChartAccount, at: str) -> None:
    """A payroll account's lines are private from its first import (spec §7.5), as an owner's private account is. The
    caller calls this on the first import only, so a later one never makes private again what the owner made public."""
    if a.payroll and account_id and SA.mark_private(c, account_id, SYSTEM, at):
        SA.bump_view(c, f"payroll account {account_id}", at)


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
        SX.contact_insert(c, book_id=ctx.book.id, contact_id=local, name=p.name, kind=p.kind or "both",
                          emails=p.emails, active=p.active, at=at, by=SYSTEM)
    else:
        local = str(current["id"])
        # A provider that does not say what the party is (no sales or purchase activity yet) keeps the kind it has.
        SX.contact_update(c, local, name=p.name, kind=p.kind or str(current["kind"]), emails=p.emails,
                          active=p.active, at=at)
    _bind(c, cid, "fin_contacts", local, p.ext, at)


def _gone(c: sqlite3.Connection, ctx: Ctx, cid: str, ext: M.Ext, at: str) -> None:
    """A record deleted at the provider: an entry is reversed; an account or party is made inactive; a bill or invoice is
    voided. A record the books never took in has nothing to undo."""
    if ext.kind in ("Payment", "BillPayment"):
        _gone_payment(c, ctx, cid, ext, at)
        return
    row = SX.ref(c, cid, _table(ext.kind), ext.key)
    if row is None or M.decide(_have(row), ext) != M.DELETE:
        return
    table, local = str(row["local_table"]), str(row["local_id"])
    if table == "fin_entries":
        _retire(c, ctx, cid, local, ext.key, at)
    elif table == "fin_accounts":
        account = ctx.accounts.get(local)
        if account is not None:
            SL.update_account(c, replace(account, active=False), at)
    elif table in ("fin_bills", "fin_invoices"):
        _void(c, ctx, cid, table, local, ext.key, at)
    else:
        SX.contact_deactivate(c, local, at)
    _bind(c, cid, table, local, ext, at, status="deleted")


def _gone_payment(c: sqlite3.Connection, ctx: Ctx, cid: str, ext: M.Ext, at: str) -> None:
    """A payment deleted at the provider: every settlement it made is undone (its cash and the credit it applied alike)
    and its entry, if it has one, is reversed. Both of its refs are then deleted."""
    app = SX.ref(c, cid, APPLICATION_TABLE, ext.key)
    entry = SX.ref(c, cid, "fin_entries", ext.key)
    if M.decide(_have(app if app is not None else entry), ext) != M.DELETE:
        return
    _unsettle_ids(ctx, _ids(app))
    if entry is not None and entry["local_id"]:
        local = str(entry["local_id"])
        _retire(c, ctx, cid, local, ext.key, at)
        _bind(c, cid, "fin_entries", local, ext, at, status="deleted")
    if app is not None:
        _bind(c, cid, APPLICATION_TABLE, str(app["local_id"]), ext, at, status="deleted")


def _mapped(c: sqlite3.Connection, cid: str, table: str, key: str) -> str:
    row = SX.ref(c, cid, table, key)
    if row is None or str(row["status"]) == "unmapped" or not str(row["local_id"]):
        raise FinanceError(NOT_FOUND, f"{key} is not in the books")
    return str(row["local_id"])


def _account_of(c: sqlite3.Connection, cid: str, ctx: Ctx, key: str) -> str:
    """The local account a provider line names: the books' sales tax account for the tax line."""
    return _tax_account(ctx) if key == M.TAX_ACCOUNT else _mapped(c, cid, "fin_accounts", key)


def _tax_account(ctx: Ctx) -> str:
    """The books' sales tax account (a liability with the sales tax subtype), which a taxed record's tax lands on."""
    found = next((a for a in ctx.accounts.values() if a.active and a.subtype == M.SALES_TAX), None)
    if found is None:
        raise FinanceError(NOT_AVAILABLE, "the books hold no sales tax account, so a taxed record cannot be taken in")
    return found.id


Carry = tuple[str, int, str, dict[str, str]]    # a replaced line: account, amount, provider line id, tags


def _entry_tags(c: sqlite3.Connection, entry_id: str) -> list[Carry]:
    """A posted entry's lines as (account, amount, provider line id, tags): what the entry that replaces it carries
    forward."""
    if not entry_id:
        return []
    return [(line.account_id, line.amount, line.external_line, dict(line.tags))
            for _, line in SL.entry_lines(c, entry_id)]


def _carried(old: Sequence[Carry], new: Sequence[tuple[str, int, str]]) -> list[dict[str, str]]:
    """Each new line's tags: from the replaced line with the same provider line id (so an amount edit keeps them), else
    from the replaced line on the same account and amount. Each replaced line is used once."""
    pool = list(old)
    out: list[dict[str, str] | None] = [None] * len(new)
    for i, (_, _, key) in enumerate(new):
        hit = next((x for x in pool if key and x[2] == key), None)
        if hit is not None:
            pool.remove(hit)
            out[i] = dict(hit[3])
    for i, (account, amount, _) in enumerate(new):
        if out[i] is None:
            hit = next((x for x in pool if x[0] == account and x[1] == amount), None)
            if hit is not None:
                pool.remove(hit)
                out[i] = dict(hit[3])
    return [mark or {} for mark in out]


def _entry(c: sqlite3.Connection, cid: str, ctx: Ctx, j: M.Journal, day: str, old: Sequence[Carry] = ()) -> L.Entry:
    """A provider entry as the books post it. A replaced entry's tags carry to the lines with the same provider line id,
    else to the lines on the same account and amount."""
    accounts = [_account_of(c, cid, ctx, x.account) for x in j.lines]
    tags = _carried(old, [(account, x.amount, x.key) for account, x in zip(accounts, j.lines, strict=True)])
    lines = tuple(L.Line(account_id=account, amount=x.amount, currency=ctx.book.base_currency,
                         original_amount=x.amount, fx_rate=Decimal(1),
                         contact_id=_mapped(c, cid, "fin_contacts", x.party) if x.party else "",
                         description=x.description, tags=mark, external_line=x.key)
                  for account, x, mark in zip(accounts, j.lines, tags, strict=True))
    return L.Entry(book_id=ctx.book.id, entry_date=day, memo=j.memo,
                   source=j.source if j.source in L.SOURCES else "sync",
                   source_id=f"{j.ext.key}:{j.ext.revision}", lines=lines, basis=j.basis)


def _restate(c: sqlite3.Connection, ctx: Ctx, day: str, change: str, entry_id: str | None,
             reversal_id: str | None, cid: str, key: str, at: str) -> None:
    """A change that landed in a closed month: the month stays closed, the change posted in the open period, and the
    month is named here so a person can see what moved."""
    period = SL.period(c, ctx.book.id, day)
    SX.restatement_insert(c, book_id=ctx.book.id, period_id=str(period["id"]), day=day, change=change,
                          entry_id=entry_id, reversal_id=reversal_id, cid=cid, external_id=key, at=at, by=SYSTEM)


def _unsettle_entry(c: sqlite3.Connection, ctx: Ctx, entry_id: str, *, keep_credit: bool = False) -> list[Any]:
    """Undo the settlements a payment's entry made, before the entry is reversed: each document owes its money again.
    A replaced credit memo keeps the credit applied from it: those applications are re-pointed at its new entry.
    A refund it made gives its share of the credit's P&L back to the accrual-only view. Returns the cash applications of
    an overpayment or a prepayment that it undid, which a revision applies again (see _resettle)."""
    undone: list[Any] = []
    for s in SX.settlements_of(c, entry_id):
        if keep_credit and str(s["from_kind"]) == "credit":
            continue
        documents.unsettle(ctx, s, cash=str(s["from_kind"]) != "credit")
        if str(s["from_kind"]) == "payment":
            undone.append(s)
    for r in SX.credit_refunds_of_refund(c, entry_id):
        _unrefund(c, ctx, r)
    return undone


def _refund_cash(c: sqlite3.Connection, ctx: Ctx, credit: str, before: int, after: int, day: str) -> None:
    """Move the cash view of a credit memo's P&L as its refunds move from `before` to `after`: each line takes its share
    of what is refunded, in proportion, as a settled document's lines do (see documents._cash). The control line is left
    out: it is the money owed, not revenue."""
    entry = SL.entry_row(c, credit)
    control = documents.control(ctx, "bill" if str(entry["source"]) == "bill" else "invoice").id
    lines = [line for _, line in SL.entry_lines(c, credit) if line.account_id != control]
    flip = -1 if sum(x.amount for x in lines) < 0 else 1       # a credit posts revenue or cost in one direction
    for x, share in zip(lines, AP.shares([flip * x.amount for x in lines], before, after), strict=True):
        account = ctx.accounts.get(x.account_id)
        if share and account is not None and account.type in L.PNL_TYPES:
            SAP.cash_roll(c, ctx.book.id, day, x.account_id, x.tags, x.contact_id, flip * share)


def _unrefund(c: sqlite3.Connection, ctx: Ctx, row: Any) -> None:
    """Undo one refund a credit memo took: its share of the credit's P&L leaves the cash view again."""
    credit = str(row["credit_entry_id"])
    before = SX.credit_refunded(c, credit)
    _refund_cash(c, ctx, credit, before, before - int(row["amount"]), str(row["day"]))
    SX.credit_refund_void(c, str(row["id"]), ctx.at)


def _move_refunds(c: sqlite3.Connection, ctx: Ctx, old: str, new: str) -> None:
    """A credit memo revised at the provider: the refunds it took move to its new entry, and their share of the P&L
    moves with the new lines."""
    rows = SX.credit_refunds_of_credit(c, old)
    running = sum(int(r["amount"]) for r in rows)
    for r in reversed(rows):
        _refund_cash(c, ctx, old, running, running - int(r["amount"]), str(r["day"]))
        running -= int(r["amount"])
    for r in rows:
        _refund_cash(c, ctx, new, running, running + int(r["amount"]), str(r["day"]))
        running += int(r["amount"])
        SX.credit_refund_move(c, str(r["id"]), new)


def _credit_entry(c: sqlite3.Connection, cid: str, key: str) -> str:
    """The local entry of a credit memo or supplier's credit a refund returns money for. Raises when it is not in the
    books yet, so the refund waits for it."""
    entry = SX.ref(c, cid, "fin_entries", key)
    if entry is None or str(entry["status"]) != "active" or not entry["local_id"]:
        raise FinanceError(NOT_AVAILABLE, f"{key} is not in the books yet")
    return str(entry["local_id"])


def _resettle(c: sqlite3.Connection, ctx: Ctx, cid: str, undone: Sequence[Any], entry_id: str, at: str) -> None:
    """Apply again the cash applications a revised overpayment or prepayment undid, to the same documents on the days
    they took. The books hold them as the provider's current allocations, so a revision that leaves the allocations as
    they were leaves the documents settled; one that moves them is taken in with its application record. Each
    application ref that named one of the undone settlements now names its replacement."""
    moved = {str(s["id"]): documents.settle(ctx, str(s["to_kind"]), str(s["to_id"]), amount=int(s["amount"]),
                                            day=str(s["settled_on"]), from_kind="payment", from_id=entry_id,
                                            entry_id=entry_id, by=SYSTEM, cash=True) for s in undone}
    refs = {str(r["id"]): r for old in moved for r in SX.refs_holding(c, cid, APPLICATION_TABLE, old)}
    for row in refs.values():
        SX.ref_set(c, str(row["id"]), local_id=",".join(moved.get(x, x) for x in _ids(row)),
                   revision=str(row["revision"]), status=str(row["status"]), at=at)


def _book(c: sqlite3.Connection, ctx: Ctx, cid: str, ext: M.Ext, verdict: str, old: str, day: str,
          make: Callable[[str], L.Entry], at: str) -> str:
    """Post the entry a provider record needs, reversing the entry it replaces (`old`, "" for none). The new entry posts
    first, so a failure reverses nothing. A replaced entry's settlements are undone before it is reversed, an
    overpayment's or prepayment's applications are applied again to the new entry (see _resettle), and a change that
    lands in a closed month is flagged as a restatement. Returns the new entry's id."""
    closed = SL.closed_on(c, ctx.book.id, day)
    new_id, _ = posting.post(ctx, make(banking.open_day(ctx, day)), by=SYSTEM, importing=True)
    replaced = SL.entry_row(c, old) if verdict == M.REPLACE and old else None
    replaced_closed = False
    undone: list[Any] = []
    if replaced is not None and str(replaced["status"]) == "posted" and not replaced["reverses_id"]:
        old_day = str(replaced["entry_date"])
        undone = _unsettle_entry(c, ctx, str(replaced["id"]), keep_credit=True)
        rid = posting.reverse(ctx, str(replaced["id"]), day=banking.open_day(ctx, old_day),
                              memo="revised at the provider", by=SYSTEM)
        SX.link_feed(c, str(replaced["id"]), new_id)
        replaced_closed = SL.closed_on(c, ctx.book.id, old_day)
        if replaced_closed:
            _restate(c, ctx, old_day, "replaced", new_id, rid, cid, ext.key, at)
    if undone:
        _resettle(c, ctx, cid, undone, new_id, at)
    if closed and not replaced_closed:
        _restate(c, ctx, day, "added", new_id, None, cid, ext.key, at)
    return new_id


def _journal(c: sqlite3.Connection, ctx: Ctx, cid: str, j: M.Journal, at: str) -> None:
    row = SX.ref(c, cid, "fin_entries", j.ext.key)
    verdict = M.decide(_have(row), j.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    _currency(ctx, j.currency)
    credit = _credit_entry(c, cid, j.refund_of) if j.refund_of else ""
    old = str(row["local_id"]) if verdict == M.REPLACE and row is not None and row["local_id"] else ""
    carry = _entry_tags(c, old)
    new_id = _book(c, ctx, cid, j.ext, verdict, old, j.day, lambda when: _entry(c, cid, ctx, j, when, carry), at)
    _bind(c, cid, "fin_entries", new_id, j.ext, at)
    if credit:
        amount = sum(x.amount for x in j.lines if x.amount > 0)
        day = banking.open_day(ctx, j.day)
        before = SX.credit_refunded(c, credit)
        _refund_cash(c, ctx, credit, before, before + amount, day)
        SX.credit_refund_insert(c, book_id=ctx.book.id, credit_entry_id=credit, refund_entry_id=new_id, amount=amount,
                                day=day, at=at, by=SYSTEM)


def _retire(c: sqlite3.Connection, ctx: Ctx, cid: str, local: str, key: str, at: str) -> None:
    """Reverse an entry the provider deleted, and let the bank rows that pointed at it wait again."""
    entry = SL.entry_row(c, local)
    if entry is None or str(entry["status"]) != "posted" or entry["reverses_id"]:
        return
    day = str(entry["entry_date"])
    closed = SL.closed_on(c, ctx.book.id, day)
    _unsettle_entry(c, ctx, local)
    for r in SX.credit_refunds_of_credit(c, local):
        _unrefund(c, ctx, r)
    rid = posting.reverse(ctx, local, day=banking.open_day(ctx, day), memo="deleted at the provider", by=SYSTEM)
    SX.unlink_feed(c, local, at)
    if closed:
        _restate(c, ctx, day, "deleted", local, rid, cid, key, at)


# ------------------------------------------------------------------------------------------------ documents and payments
def _void(c: sqlite3.Connection, ctx: Ctx, cid: str, table: str, local: str, key: str, at: str) -> None:
    """A bill or invoice deleted at the provider: its entry is reversed and the document is void. The payments that
    settled it stay as they are, since the provider keeps them too. A void that lands in a closed month is flagged as a
    restatement, as a deletion of an entry is."""
    doc = SAP.get(c, table, local)
    if doc is None:
        return
    entry = str(doc["entry_id"] or "")
    if entry:
        _retire_entry_only(c, ctx, cid, entry, key, at)
    SAP.update(c, table, local, {"status": "void", "updated_at": at})


def _retire_entry_only(c: sqlite3.Connection, ctx: Ctx, cid: str, entry_id: str, key: str, at: str) -> None:
    """Reverse a document's own entry (a posted one; a reversal or a reversed entry is left alone)."""
    entry = SL.entry_row(c, entry_id)
    if entry is None or str(entry["status"]) != "posted" or entry["reverses_id"]:
        return
    day = str(entry["entry_date"])
    closed = SL.closed_on(c, ctx.book.id, day)
    rid = posting.reverse(ctx, entry_id, day=banking.open_day(ctx, day), memo="deleted at the provider", by=SYSTEM)
    if closed:
        _restate(c, ctx, day, "deleted", entry_id, rid, cid, key, at)


def _doc_lines(c: sqlite3.Connection, ctx: Ctx, cid: str, d: M.Document) -> list[tuple[str, int, str]]:
    """A document's lines as (local account id, amount, description), amounts positive, and its tax on the sales tax
    account. Raises unless every line has an account the books hold and the lines add up to the document's total."""
    if not d.party:
        raise FinanceError(NOT_AVAILABLE, "the document names no party")
    rows: list[tuple[str, int, str]] = []
    for x in d.lines:
        if not x.account:
            raise FinanceError(NOT_AVAILABLE, "a line names no account the books hold (its item has none)")
        if x.amount < 0 and not x.rounding:
            raise FinanceError(NOT_AVAILABLE, "a line has a negative amount, which this books cannot take in")
        rows.append((_mapped(c, cid, "fin_accounts", x.account), x.amount, x.description))
    if not rows:
        raise FinanceError(NOT_AVAILABLE, "the document has no lines")
    if d.tax:
        rows.append((_tax_account(ctx), d.tax, "tax"))
    if sum(amount for _, amount, _ in rows) != d.total:
        raise FinanceError(NOT_AVAILABLE, "the lines do not add up to the total (discounts are not taken in yet)")
    return rows


def _line_keys(d: M.Document) -> list[str]:
    """The provider's line id for each row `_doc_lines` makes: the lines, then the tax line, which has none."""
    return [x.key for x in d.lines] + ([""] if d.tax else [])


def _doc_entry(ctx: Ctx, *, control_kind: str, sign: int, source: str, source_id: str, contact: str,
               rows: Sequence[tuple[str, int, str]], memo: str, day: str,
               tags: Sequence[Mapping[str, str]] = (), control_id: str = "", keys: Sequence[str] = ()) -> L.Entry:
    """A document's entry: its lines (sign +1 debits, -1 credits) against its control account for the same total. Each
    line carries the tags given for it (`tags` lines up with `rows`) and the provider's id for it (`keys`)."""
    base = ctx.book.base_currency
    marks = list(tags) or [{} for _ in rows]
    ids = list(keys) or [""] * len(rows)
    lines = [L.Line(account_id=account, amount=sign * amount, currency=base, original_amount=sign * amount,
                    fx_rate=Decimal(1), contact_id=contact, description=description[:200], tags=dict(mark),
                    external_line=key)
             for (account, amount, description), mark, key in zip(rows, marks, ids, strict=True)]
    total = sum(amount for _, amount, _ in rows)
    control = control_id or documents.control(ctx, control_kind).id
    lines.append(L.Line(account_id=control, amount=-sign * total, currency=base, original_amount=-sign * total,
                        fx_rate=Decimal(1), contact_id=contact))
    return L.Entry(book_id=ctx.book.id, entry_date=day, memo=memo[:200], source=source, source_id=source_id,
                   lines=tuple(lines))


def _currency(ctx: Ctx, code: str) -> None:
    """A provider document, payment or entry in another currency than the books' cannot be taken in: the books hold no
    rates for it yet, so it waits as unmapped."""
    if code and code != ctx.book.base_currency:
        raise FinanceError(NOT_AVAILABLE, f"it is in {code}, and the books hold {ctx.book.base_currency}")


def _document(c: sqlite3.Connection, ctx: Ctx, cid: str, d: M.Document, at: str) -> None:
    """A bill or invoice (its own row, its lines and its entry) or a credit memo or supplier's credit (its entry only).
    A newer revision replaces the entry and the lines; the document keeps its id, so the payments that settled it still
    point at it."""
    if d.ext.kind in ("CreditMemo", "VendorCredit"):
        _credit(c, ctx, cid, d, at)
        return
    table = DOC_TABLES[d.ext.kind]
    row = SX.ref(c, cid, table, d.ext.key)
    verdict = M.decide(_have(row), d.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    if d.ext.kind == "Invoice" and d.total == 0:
        # QuickBooks zeroes a voided invoice rather than deleting it: a zero invoice is one the books void.
        if verdict == M.REPLACE and row is not None and row["local_id"]:
            local = str(row["local_id"])
            _void(c, ctx, cid, table, local, d.ext.key, at)
            _bind(c, cid, table, local, d.ext, at, status="deleted")
        return
    _currency(ctx, d.currency)
    contact = _mapped(c, cid, "fin_contacts", d.party)
    rows = _doc_lines(c, ctx, cid, d)
    keys = _line_keys(d)
    _write_document(c, ctx, cid, d, verdict, row, contact, rows, at, keys=keys)


def _write_document(c: sqlite3.Connection, ctx: Ctx, cid: str, d: M.Document, verdict: str, row: Any, contact: str,
                    rows: Sequence[tuple[str, int, str]], at: str, tags: Sequence[Mapping[str, str]] = (),
                    keys: Sequence[str] = ()) -> str:
    """A bill or invoice's row, lines and entry, from its rows (local account ids). A replaced document keeps its row
    and id: its entry is reversed and posted again. Each line keeps the tags of the provider line it replaces (by its
    line id, else by account and amount). The cash payments that settled it are undone before the lines change and
    settled again on the new lines, so their cash moves to them; the credit applied to it stays, since the credit memo
    it came from is still in the books. Returns the document's local id."""
    table = DOC_TABLES[d.ext.kind]
    kind = "bill" if table == "fin_bills" else "invoice"
    total = sum(amount for _, amount, _ in rows)
    sign = 1 if kind == "bill" else -1
    control = _mapped(c, cid, "fin_accounts", d.control) if d.control else ""
    settled: list[Any] = []
    if verdict == M.REPLACE and row is not None and row["local_id"]:
        local = str(row["local_id"])
        settled = [s for s in SAP.settlements_to(c, kind, local) if str(s["from_kind"]) != "credit"]
        for s in settled:
            documents.unsettle(ctx, s)
        doc = SAP.get(c, table, local)
        if doc is None:
            raise FinanceError(CONFLICT, "the document this revises is no longer in the books")
        old_entry = str(doc["entry_id"] or "")
        written_off = int(doc["written_off"]) if kind == "invoice" else 0
        state = _doc_state(kind, total, int(doc["amount_paid"]), written_off)
        SAP.update(c, table, local, _doc_update(kind, contact, d, total, state, at))
        line_tags = _carry_lines(c, kind, local, old_entry, keys, rows, sign)
    else:
        old_entry = ""
        local = SAP.insert(c, table, _doc_row(ctx, kind, contact, d, total, _doc_state(kind, total, 0, 0), at))
        line_tags = [dict(mark) for mark in tags] or [{} for _ in rows]
    SAP.replace_lines(c, kind, local, _line_rows(rows, at, line_tags, keys))
    new_entry = _book(c, ctx, cid, d.ext, verdict, old_entry, d.day,
                      lambda when: _doc_entry(ctx, control_kind=kind, sign=sign, source=kind, source_id=local,
                                              contact=contact, rows=rows, memo=_doc_label(d), day=when,
                                              tags=line_tags, control_id=control, keys=keys), at)
    SAP.update(c, table, local, {"entry_id": new_entry, "updated_at": at})
    for s in settled:
        documents.settle(ctx, kind, local, amount=int(s["amount"]), day=str(s["settled_on"]),
                         from_kind=str(s["from_kind"]), from_id=str(s["from_id"]),
                         entry_id=str(s["entry_id"] or ""), by=SYSTEM)
    _bind(c, cid, table, local, d.ext, at)
    return local


def _credit(c: sqlite3.Connection, ctx: Ctx, cid: str, d: M.Document, at: str) -> None:
    """A credit memo (debits to income against receivables) or a supplier's credit (credits to expense against payables):
    one entry, with no row of its own. Its applications come as payments of nothing (see _settlement), so it settles
    nothing itself."""
    side = "bill" if d.ext.kind == "VendorCredit" else "invoice"
    sign = -1 if side == "bill" else 1
    row = SX.ref(c, cid, "fin_entries", d.ext.key)
    verdict = M.decide(_have(row), d.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    _currency(ctx, d.currency)
    contact = _mapped(c, cid, "fin_contacts", d.party)
    rows = _doc_lines(c, ctx, cid, d)
    keys = _line_keys(d)
    old = str(row["local_id"]) if verdict == M.REPLACE and row is not None and row["local_id"] else ""
    carry = _carried(_entry_tags(c, old),
                     [(account, sign * amount, key) for (account, amount, _), key in zip(rows, keys, strict=True)])
    control = _mapped(c, cid, "fin_accounts", d.control) if d.control else ""
    new_id = _book(c, ctx, cid, d.ext, verdict, old, d.day,
                   lambda when: _doc_entry(ctx, control_kind=side, sign=sign, source=side,
                                           source_id=f"{d.ext.key}:{d.ext.revision}", contact=contact, rows=rows,
                                           memo=_doc_label(d), day=when, tags=carry, control_id=control,
                                           keys=keys), at)
    if old:
        SAP.repoint_credit(c, old, new_id)      # the applications this memo made still point at it, now re-posted
        _move_refunds(c, ctx, old, new_id)
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


def _line_rows(rows: Sequence[tuple[str, int, str]], at: str, tags: Sequence[Mapping[str, str]],
               keys: Sequence[str] = ()) -> list[dict[str, object]]:
    """A document's line rows, each with the tags it carries and the provider's id for it (empty for a line Tico wrote)."""
    marks = list(keys) or [""] * len(rows)
    return [{"account_id": account, "amount": amount, "description": description[:200],
             "tags": SK.dump(dict(mark)), "external_line": key, "created_at": at, "created_by": SYSTEM}
            for (account, amount, description), mark, key in zip(rows, tags, marks, strict=True)]


def _carry_lines(c: sqlite3.Connection, kind: str, doc_id: str, old_entry: str, keys: Sequence[str],
                 rows: Sequence[tuple[str, int, str]], sign: int) -> list[dict[str, str]]:
    """Each new line's tags: from the line the document held with the same provider line id (so an amount edit keeps
    its tags), else from the replaced entry's line on the same account and amount."""
    by_key = {str(x["external_line"]): dict(SK.loads(x["tags"])) for x in SAP.lines(c, kind, [doc_id])[doc_id]
              if x["external_line"]}
    marks = list(keys) or [""] * len(rows)
    fallback = _carried(_entry_tags(c, old_entry),
                        [(account, sign * amount, key) for (account, amount, _), key in zip(rows, marks, strict=True)])
    return [by_key[key] if key and key in by_key else mark for key, mark in zip(marks, fallback, strict=True)]


def _settle_target(c: sqlite3.Connection, cid: str, key: str) -> tuple[str, str]:
    """A payment's link to a bill or invoice: (the document kind, its local id). Raises when it is not in the books."""
    name, _, _ = key.partition(":")
    if name not in DOC_TABLES:
        raise FinanceError(NOT_AVAILABLE, f"{key} is not a bill or an invoice")
    return ("bill" if name == "Bill" else "invoice"), _mapped(c, cid, DOC_TABLES[name], key)


def _ids(row: Any) -> list[str]:
    """The settlement ids a credit application's ref holds (its local id, comma-separated)."""
    return [x for x in str(row["local_id"] or "").split(",") if x] if row is not None else []


def _unsettle_ids(ctx: Ctx, ids: Sequence[str]) -> None:
    """Undo settlements by id; one already undone is left alone."""
    for sid in ids:
        s = SAP.get(ctx.c, "fin_settlements", sid)
        if s is not None and s["voided_at"] is None:
            documents.unsettle(ctx, s, cash=str(s["from_kind"]) != "credit")


def _allot(credits: Sequence[tuple[str, int]], targets: Sequence[tuple[str, int]]) -> list[tuple[str, str, int]]:
    """Each invoice's share from the credits, taken in order: (credit entry, invoice, amount). The credits must cover
    the invoices exactly, which the caller checks."""
    remaining = [amount for _, amount in credits]
    out: list[tuple[str, str, int]] = []
    for invoice, amount in targets:
        need = amount
        for i, (credit, _) in enumerate(credits):
            take = min(need, remaining[i])
            if take > 0:
                out.append((credit, invoice, take))
                remaining[i] -= take
                need -= take
        if need:
            raise FinanceError(NOT_AVAILABLE, "the credits do not cover the invoices they apply to")
    return out


def _target_party(c: sqlite3.Connection, targets: Sequence[tuple[str, str, int]]) -> str:
    """The local contact of the first document a payment settles, for a payment that names no party."""
    if not targets:
        raise FinanceError(NOT_AVAILABLE, "the payment names no party")
    kind, doc_id, _ = targets[0]
    table, _, _, _, contact_col = SAP.DOCS[kind]
    doc = SAP.get(c, table, doc_id)
    if doc is None or not doc[contact_col]:
        raise FinanceError(NOT_AVAILABLE, "the payment names no party")
    return str(doc[contact_col])


# Credits whose money the books already hold (an overpayment or prepayment was cash): applying one is a payment.
CASH_CREDITS = ("Overpayment", "Prepayment")


def _control_of(c: sqlite3.Connection, kind: str, doc_id: str, default: str) -> str:
    """The control account a bill or invoice posted against: the account its entry carries its total on (a provider may
    name its own payable or receivable account), else the default."""
    doc = SAP.get(c, SAP.DOCS[kind][0], doc_id)
    if doc is None or not doc["entry_id"]:
        return default
    want = -int(doc["total"]) if kind == "bill" else int(doc["total"])
    for _, line in SL.entry_lines(c, str(doc["entry_id"])):
        if line.amount == want:
            return line.account_id
    return default


def _settlement(c: sqlite3.Connection, ctx: Ctx, cid: str, s: M.Settlement, at: str, feed: str = "") -> None:
    """A payment and the bills or invoices it settles. One Payment id is one settlement set, whatever its revision: its
    cash posts an entry (a bill payment: Dr payables, Cr bank; a received payment: Dr bank, Cr receivables), and the
    credit memos or supplier's credits it applies settle their documents with no money moving. A revision or a deletion
    undoes the whole set before it is settled again. A payment sent for a bank row (feed) is that row's entry: the row
    is marked posted with it."""
    app = SX.ref(c, cid, APPLICATION_TABLE, s.ext.key)
    row = SX.ref(c, cid, "fin_entries", s.ext.key)
    verdict = M.decide(_have(app if app is not None else row), s.ext)
    if verdict not in (M.CREATE, M.REPLACE):
        return
    _currency(ctx, s.currency)
    credit = sum(amount for _, amount in s.credits)
    if credit:
        if sum(amount for _, amount in s.links) != s.total + credit:
            raise FinanceError(NOT_AVAILABLE, "the credits applied do not match the invoices they apply to")
    elif sum(amount for _, amount in s.links) > s.total:
        raise FinanceError(NOT_AVAILABLE, "the payment settles more than its total")
    posts = s.total > 0 or (not credit and bool(s.bank))  # a credit alone, or nothing with no bank, moves no money
    if posts and not s.bank:
        raise FinanceError(NOT_AVAILABLE, "the payment names no bank account the books hold")
    targets = [(*_settle_target(c, cid, key), amount) for key, amount in s.links]
    kinds = {kind for kind, _, _ in targets}
    if len(kinds) > 1:
        raise FinanceError(NOT_AVAILABLE, "a payment settles invoices or bills, not both")
    side = kinds.pop() if kinds else "invoice"
    # The credit covers the links in order; what each link does not take from the credit is paid in cash.
    credit_parts: list[tuple[str, int]] = []
    cash_parts: list[tuple[str, str, int]] = []
    left = credit
    for kind, doc, amount in targets:
        applied = min(amount, left)
        left -= applied
        if applied:
            credit_parts.append((doc, applied))
        if amount > applied:
            cash_parts.append((kind, doc, amount - applied))
    credits: list[tuple[str, int]] = []
    cash_credits: set[str] = set()
    for key, amount in s.credits:
        credit_entry = SX.ref(c, cid, "fin_entries", key)
        if credit_entry is None or str(credit_entry["status"]) != "active" or not credit_entry["local_id"]:
            raise FinanceError(NOT_AVAILABLE, f"{key} is not in the books yet")
        credits.append((str(credit_entry["local_id"]), amount))
        if key.partition(":")[0] in CASH_CREDITS:
            cash_credits.add(str(credit_entry["local_id"]))
    applications = _allot(credits, credit_parts)
    if verdict == M.REPLACE:
        _unsettle_ids(ctx, _ids(app))
    old = str(row["local_id"]) if verdict == M.REPLACE and row is not None and row["local_id"] else ""
    entry, bank = "", ""
    if posts:
        bank = _mapped(c, cid, "fin_accounts", s.bank)
        contact = _mapped(c, cid, "fin_contacts", s.party) if s.party else _target_party(c, targets)
        bill = s.ext.kind == "BillPayment"
        default = documents.control(ctx, "bill" if bill else "invoice").id
        base = ctx.book.base_currency

        def line(account: str, amount: int, party: str) -> L.Line:
            return L.Line(account_id=account, amount=amount, currency=base, original_amount=amount, contact_id=party)

        # Each document paid in cash clears the control account it posted against; what no document takes is cleared
        # on the default.
        owed: dict[str, int] = {}
        for kind, doc, amount in cash_parts:
            account = _control_of(c, kind, doc, default)
            owed[account] = owed.get(account, 0) + amount
        rest = s.total - sum(owed.values())
        if rest or not owed:
            owed[default] = owed.get(default, 0) + rest
        if bill:
            lines = tuple([line(account, amount, contact) for account, amount in owed.items()]
                          + [line(bank, -s.total, "")])
        else:
            lines = tuple([line(bank, s.total, "")]
                          + [line(account, -amount, contact) for account, amount in owed.items()])
        entry = _book(c, ctx, cid, s.ext, verdict, old, s.day,
                      lambda when: L.Entry(book_id=ctx.book.id, entry_date=when, memo=(s.memo or s.ext.kind)[:200],
                                           source="sync", source_id=f"{s.ext.key}:{s.ext.revision}", lines=lines),
                      at)
    elif old:
        _retire(c, ctx, cid, old, s.ext.key, at)
    source, source_id = ("feed", feed) if feed else ("account", bank)
    # A cash credit was money already in the books, so applying it recognizes cash as a payment would; a credit memo
    # applied moves no cash.
    ids = [documents.settle(ctx, side, invoice, amount=part, day=s.day,
                            from_kind="payment" if credit_entry in cash_credits else "credit", from_id=credit_entry,
                            entry_id=credit_entry, by=SYSTEM, cash=credit_entry in cash_credits)
           for credit_entry, invoice, part in applications]
    ids += [documents.settle(ctx, kind, doc, amount=amount, day=s.day, from_kind=source, from_id=source_id,
                             entry_id=entry, by=SYSTEM)
            for kind, doc, amount in cash_parts]
    if feed and entry:
        frow = SK.get(c, "fin_feed_txns", feed)
        if frow is not None and str(frow["status"]) == "new" and \
                abs(int(frow["amount"])) <= SAP.settled_from(c, "feed", feed):
            banking.mark_posted(ctx, [feed], entry)
    _bind(c, cid, APPLICATION_TABLE, ",".join(ids), s.ext, at)
    _bind(c, cid, "fin_entries", entry, s.ext, at, status="active" if entry else "deleted")


# ------------------------------------------------------------------------------------------------ bank rows
def _match(ctx: Ctx, gl: str, amount: int, txn_date: str) -> tuple[str, str] | None:
    """The provider's entry line a bank row is: a line on its account for its amount, dated near it. (entry, line)"""
    day = dates.day(txn_date)
    start, end = (day - timedelta(days=M.MATCH_DAYS)).isoformat(), (day + timedelta(days=M.MATCH_DAYS)).isoformat()
    return M.match_row(amount, txn_date, SX.candidates(ctx.c, ctx.book.id, gl, amount, start, end))


def _link(ctx: Ctx, fid: str, entry_id: str, line_id: str) -> None:
    SK.update(ctx.c, "fin_feed_txns", fid, {"status": "matched", "entry_id": entry_id, "entry_line_id": line_id,
                                            "updated_at": ctx.at})
    SX.cancel_feed_writes(ctx.c, fid, "the provider's entry for this bank row came first", ctx.at)


def _link_open_rows(c: sqlite3.Connection, cid: str, at: str) -> None:
    """Bank rows still waiting after a batch of records look for their entry among what just came in."""
    ctx = load(c, at)
    if ctx.book.mode != "mirror":
        return
    for row in SX.unmatched_rows(c, ctx.book.id, MATCH_MOST):
        hit = _match(ctx, str(row["gl_id"]), int(row["amount"]), str(row["txn_date"]))
        if hit:
            _link(ctx, str(row["id"]), *hit)


def _still_matches(ctx: Ctx, row: Any, money: Any) -> bool:
    """Whether a bank row is still the entry it was matched to: the entry is posted near the row's date (or on the open
    day it moved to), and it carries what no payment has applied to the row."""
    entry = SL.entry_row(ctx.c, str(row["entry_id"]))
    if entry is None or str(entry["status"]) != "posted":
        return False
    txn, entered = str(row["txn_date"]), str(entry["entry_date"])
    near = abs((dates.day(entered) - dates.day(txn)).days) <= M.MATCH_DAYS
    if not near and entered != banking.open_day(ctx, txn):
        return False
    lines = SL.entry_lines(ctx.c, str(row["entry_id"]))
    line = str(row["entry_line_id"] or "")
    if line:
        moved = sum(x.amount for r, x in lines if str(r["id"]) == line)
    else:       # matched without its line: the entry's lines on the bank's account, as before lines were kept
        moved = sum(x.amount for _, x in lines if x.account_id == str(money["account_id"]))
    return moved == banking.left_of(ctx, row)


def settle_row(ctx: Ctx, row: Any, money: Any, t: F.FeedTxn) -> None:
    """A bank row as the feed states it, in a mirror. A row the bank cancelled is ignored (the provider's entry is never
    reversed from here). A settled row keeps the entry it has, or finds the provider's entry for it."""
    fid = str(row["id"])
    if t.void or t.reversed:
        SX.cancel_feed_writes(ctx.c, fid, "the bank cancelled this transaction", ctx.at)
        SK.update(ctx.c, "fin_feed_txns", fid,
                  {"status": "ignored", "entry_id": None, "entry_line_id": None, "updated_at": ctx.at})
        return
    if t.pending or str(row["status"]) == "ignored":
        return
    if row["entry_id"]:
        if _still_matches(ctx, row, money):
            return
        SK.update(ctx.c, "fin_feed_txns", fid,
                  {"status": "new", "entry_id": None, "entry_line_id": None, "updated_at": ctx.at})
    hit = _match(ctx, str(money["account_id"]), int(row["amount"]), str(row["txn_date"]))
    if hit:
        _link(ctx, fid, *hit)


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
    SK.update(ctx.c, "fin_feed_txns", fid,
              {"status": "ignored", "entry_id": None, "entry_line_id": None, "updated_at": ctx.at})


def write_feed(ctx: Ctx, row: Any, account: L.Account, tags: Mapping[str, str], gl: L.Account, *, by: str,
               action_id: str) -> handlers.Result:
    """Categorize a bank row in a mirror: the provider's entry if one fits, else an entry written out to the provider
    (the row stays new until the provider confirms it)."""
    fid = str(row["id"])
    amount = banking.left_of(ctx, row)          # the part no payment has applied yet
    if amount == 0:
        raise FinanceError(CONFLICT, "that bank row is already applied in full")
    hit = _match(ctx, gl.id, amount, str(row["txn_date"]))
    if hit:
        _link(ctx, fid, *hit)
        return {"feed_id": fid, "entry_id": hit[0], "matched": True}
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


def _need(key: str | None, what: str) -> str:
    if key is None:
        raise _Missing(f"{what} is not in the provider yet")
    return key


def _doc_out(c: sqlite3.Connection, cid: str, local: Mapping[str, Any]) -> dict[str, Any]:
    """A bill or invoice as the provider takes it: its party and each line's account by their provider keys."""
    lines = [{"account": _need(_key(c, cid, "fin_accounts", str(x["account_id"])), f"account {x['account_id']}"),
              "amount": x["amount"], "description": x["description"]} for x in local["lines"]]
    return {"party": _need(_key(c, cid, "fin_contacts", str(local["party"])), f"contact {local['party']}"),
            "number": local["number"], "day": local["day"], "due_at": local["due_at"], "memo": local["memo"], "lines": lines}


def _void_out(c: sqlite3.Connection, cid: str, table: str, local: Mapping[str, Any]) -> dict[str, Any]:
    """The provider's id and the revision it holds now: a void sent with a stale revision is refused."""
    ref = SX.ref_for_local(c, cid, table, str(local["id"]))
    if ref is None or str(ref["status"]) != "active":
        raise _Missing(f"{local['id']} is not in the provider yet")
    return {"ext": str(ref["external_id"]), "revision": str(ref["revision"])}


def _pay_out(c: sqlite3.Connection, cid: str, local: Mapping[str, Any]) -> dict[str, Any]:
    """A bill payment or a received payment as the provider takes it: the party, the bank account, and the one bill or
    invoice it applies to, by their provider keys."""
    doc_kind = "Bill" if local["kind"] == "bill" else "Invoice"
    doc = _need(_key(c, cid, DOC_TABLES[doc_kind], str(local["doc_id"])), f"{doc_kind} {local['doc_id']}")
    amount = int(local["amount"])
    return {"kind": str(local["kind"]), "day": local["day"], "memo": local["memo"],
            "party": _need(_key(c, cid, "fin_contacts", str(local["party"])), f"contact {local['party']}"),
            "bank": _need(_key(c, cid, "fin_accounts", str(local["bank"])), f"account {local['bank']}"),
            "total": amount, "links": [[doc, amount]], "feed_id": str(local.get("feed_id") or ""),
            "card": bool(local.get("card"))}


def _writeoff_out(c: sqlite3.Connection, cid: str, local: Mapping[str, Any]) -> dict[str, Any]:
    """A write-off as the provider takes it: a credit memo to the write-off account, billed by the write-off item. The
    invoice it closes is applied afterwards (credit.apply), so the invoice is named here for that."""
    invoice = _need(_key(c, cid, "fin_invoices", str(local["invoice_id"])), f"invoice {local['invoice_id']}")
    party = _need(_key(c, cid, "fin_contacts", str(local["party"])), f"contact {local['party']}")
    account = _need(_key(c, cid, "fin_accounts", str(local["account_id"])), f"account {local['account_id']}")
    return {"party": party, "account": account, "day": local["day"], "memo": local["memo"],
            "total": int(local["amount"]), "invoice": invoice}


def _translate(c: sqlite3.Connection, cid: str, op: str, local: Mapping[str, Any]) -> dict[str, Any]:
    """What the provider is sent: its keys for accounts and parties, and the tags as its classes when that is on. A
    write waits (_Missing) while an account or party it names is not in the provider yet; an export drops a contact
    the provider does not hold."""
    if op in ("bill.create", "invoice.create"):
        return _doc_out(c, cid, local)
    if op in ("bill.void", "invoice.void"):
        return _void_out(c, cid, DOC_TABLES["Bill" if op.startswith("bill") else "Invoice"], local)
    if op in ("payment.bill", "payment.invoice"):
        return _pay_out(c, cid, local)
    if op == "writeoff.create":
        return _writeoff_out(c, cid, local)
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
            "export": export, "feed": bool(local.get("feed_id"))}


def _finish(c: sqlite3.Connection, o: Any, why: str, now: str, status: str) -> None:
    SK.update(c, "fin_outbox", str(o["id"]), {"status": status, "claimed_at": None, "last_error": why[:300],
                                              "updated_at": now})


def _later(c: sqlite3.Connection, o: Any, why: str, now: str) -> None:
    SK.update(c, "fin_outbox", str(o["id"]), {"claimed_at": None, "next_try_at": M.stamp(now, RETRY[0] * 1_000_000),
                                              "last_error": why[:300], "updated_at": now})


# The writes that take a document's balance before the provider holds them: a payment, and a write-off with the credit
# that applies it to its invoice. Each holds its amount against what is left until it is sent or refused.
BALANCE_OPS = {"bill": ("payment.bill",), "invoice": ("payment.invoice", "writeoff.create", "credit.apply")}
RECHECKED = tuple(op for ops in BALANCE_OPS.values() for op in ops)


def _held(c: sqlite3.Connection, cid: str, kind: str, doc_id: str, *, skip: str = "") -> int:
    """What the open writes against this document hold back from its balance. `skip` is the write asking, which
    does not hold against itself."""
    held = 0
    for o in SK.open_writes(c, cid, BALANCE_OPS[kind]):
        local = SK.loads(o["payload"])
        if str(o["id"]) != skip and doc_id in (str(local.get("doc_id", "")), str(local.get("invoice_id", ""))):
            held += int(local["amount"])
    return held


def _still_owed(c: sqlite3.Connection, cid: str, o: Any, local: Mapping[str, Any]) -> bool:
    """Whether the document still has the amount this unsent write asks for, not counting the write itself."""
    kind = "bill" if str(o["op"]) == "payment.bill" else "invoice"
    doc_id = str(local.get("doc_id") or local.get("invoice_id") or "")
    doc = SAP.doc_with_contact(c, kind, doc_id)
    if doc is None:
        return False
    return int(local["amount"]) <= documents.left_of(kind, doc) - _held(c, cid, kind, doc_id, skip=str(o["id"]))


def _prepare(c: sqlite3.Connection, cid: str, o: Any, now: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    local = SK.loads(o["payload"])
    feed = str(local.get("feed_id") or "")
    if feed:
        frow = SK.get(c, "fin_feed_txns", feed)
        if frow is None or str(frow["status"]) != "new":
            _finish(c, o, "the bank row was matched or ignored before this went out", now, "failed")
            return None
        if not M.backfill_done(_backfill_state(SX.mirror_state(c, cid)), _entities(c, cid)):
            # The provider's entries are not all in yet: a match against them must not be missed.
            _later(c, o, "the provider's books are still being read", now)
            return None
    # A write never sent is checked against its document again: a sync may have taken the balance since it was queued.
    # A write tried before keeps its identity and goes out as it is, the provider keeps its key.
    if int(o["attempts"]) == 0 and str(o["op"]) in RECHECKED and not _still_owed(c, cid, o, local):
        _finish(c, o, "the balance was taken before this went out", now, "failed")
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
    feed = str(local.get("feed_id") or "")
    frow = SK.get(c, "fin_feed_txns", feed) if feed else None
    row = SX.ref(c, cid, "fin_entries", ext.key)
    if row is not None and str(row["status"]) == "active":
        entry = str(row["local_id"])               # the change feed brought the entry in first
    elif frow is not None and str(frow["status"]) != "new":
        # The bank row was matched or ignored while the write was out, so the books keep their entry: the provider's
        # copy is deleted again.
        _bind(c, cid, "fin_entries", "", ext, now, status="deleted")
        _queue(c, cid, "journal.delete", {"ext": ext.key, "revision": ext.revision, "entry_id": "", "date": "",
                                          "memo": "", "feed_id": ""}, by=SYSTEM, at=now)
        tico.audit(c, SYSTEM, "finance.mirror.withdrawn", ext.key,
                   {"connection_id": cid, "why": "the bank row was matched or ignored while the write was out"})
        return
    else:
        # The row's date as it is now: a bank row re-dated while its write was out keeps its current day.
        dated = {**local, "date": str(frow["txn_date"])} if frow is not None else local
        entry = _post(ctx, cid, dated, ext.key, now)
        _bind(c, cid, "fin_entries", entry, ext, now)
    if frow is not None and str(frow["status"]) == "new":
        banking.mark_posted(ctx, [feed], entry)


def _confirm_document(c: sqlite3.Connection, cid: str, sent: Sent, local: Mapping[str, Any], now: str) -> None:
    """A bill or invoice the provider took from Tico: its row, lines and entry come in as its record would, from the
    local write (the provider took the same accounts and party). A change feed read that got there first stands."""
    ext = M.Ext(kind=sent.kind, external_id=sent.external_id, revision=sent.revision)
    row = SX.ref(c, cid, DOC_TABLES[ext.kind], ext.key)
    if row is not None and str(row["status"]) == "active":
        return
    rows = [(str(x["account_id"]), int(x["amount"]), str(x["description"])) for x in local["lines"]]
    # Only the head is read here: the lines come in as rows, and the party as its local id.
    head = M.Document(ext, party="", number=str(local["number"]), day=str(local["day"]), due=str(local["due_at"]),
                      total=sum(amount for _, amount, _ in rows), lines=(), memo=str(local["memo"]))
    tags = [dict(x.get("tags") or {}) for x in local["lines"]]
    _write_document(c, load(c, now), cid, head, M.CREATE, row, str(local["party"]), rows, now, tags)


def _confirm_void(c: sqlite3.Connection, cid: str, table: str, local: Mapping[str, Any], sent: Sent, now: str) -> None:
    """The provider voided a bill or invoice Tico voided: its entry is reversed, and its ref takes the revision the void
    left, so the change feed's echo of that void is the same record and brings nothing new."""
    doc_id = str(local["id"])
    _void(c, load(c, now), cid, table, doc_id, M.Ext(kind=sent.kind, external_id=sent.external_id,
                                                      revision=sent.revision).key, now)
    ref = SX.ref_for_local(c, cid, table, doc_id)
    if ref is not None:
        SX.ref_set(c, str(ref["id"]), local_id=doc_id, revision=sent.revision, status="deleted", at=now)


def _confirm_payment(c: sqlite3.Connection, cid: str, local: Mapping[str, Any], provider: Mapping[str, Any],
                     sent: Sent, now: str) -> None:
    """The provider recorded a bill payment or a received payment Tico sent: its entry and its settlement come in as
    the provider's record would, and a bank row it was sent for is marked posted with that entry. When that row was
    matched or ignored while the payment was out, the books do not take the payment: its owner deletes it in QuickBooks."""
    ext = M.Ext(kind=sent.kind, external_id=sent.external_id, revision=sent.revision)
    feed = str(local.get("feed_id") or "")
    frow = SK.get(c, "fin_feed_txns", feed) if feed else None
    row = SX.ref(c, cid, "fin_entries", ext.key)
    if (row is None or str(row["status"]) != "active") and frow is not None and str(frow["status"]) != "new":
        _bind(c, cid, "fin_entries", "", ext, now, status="deleted")
        tico.needs_you(c, SX.connection_owner(c, cid), "Delete a payment in QuickBooks",
                       f"QuickBooks holds {ext.key}, which was sent for a bank row that was matched meanwhile. "
                       "Delete it there.")
        tico.audit(c, SYSTEM, "finance.mirror.withdrawn", ext.key,
                   {"connection_id": cid, "why": "the bank row was matched or ignored while the payment was out"})
        return
    s = M.Settlement(ext, day=str(provider["day"]), memo=str(provider["memo"]), party=str(provider["party"]),
                     bank=str(provider["bank"]), total=int(provider["total"]),
                     links=tuple((str(k), int(a)) for k, a in provider["links"]))
    _settlement(c, load(c, now), cid, s, now, feed=feed)


def _confirm_writeoff(c: sqlite3.Connection, cid: str, local: Mapping[str, Any], provider: Mapping[str, Any],
                      sent: Sent, now: str) -> None:
    """The provider took a write-off as a credit memo: its entry comes in as the provider's record of one would. The
    invoice is settled only when the credit is applied to it, so that application is queued next, and its own
    confirmation settles the invoice."""
    ext = M.Ext(kind=sent.kind, external_id=sent.external_id, revision=sent.revision)
    total = int(provider["total"])
    memo = str(provider["memo"])
    day = str(provider["day"])
    d = M.Document(ext, party=str(provider["party"]), number="", day=day, due="", total=total,
                   lines=(M.EntryLine(str(provider["account"]), total, description=memo),), memo=memo)
    _credit(c, load(c, now), cid, d, now)
    _queue(c, cid, "credit.apply", {"invoice": str(provider["invoice"]), "credit": ext.key,
                                    "party": str(provider["party"]), "invoice_id": str(local["invoice_id"]),
                                    "amount": total, "day": day, "memo": memo},
           by=SYSTEM, at=now, key=f"credit.apply:{ext.key}")


def _confirm_apply(c: sqlite3.Connection, cid: str, provider: Mapping[str, Any], sent: Sent, now: str) -> None:
    """The provider applied a write-off's credit to its invoice: the invoice is settled as a payment of nothing with the
    credit would settle it (the echo of that payment then adds nothing)."""
    ext = M.Ext(kind=sent.kind, external_id=sent.external_id, revision=sent.revision)
    amount = int(provider["amount"])
    s = M.Settlement(ext, day=str(provider["day"]), memo=str(provider["memo"]), party=str(provider["party"]), bank="",
                     total=0, links=((str(provider["invoice"]), amount),),
                     credits=((str(provider["credit"]), amount),))
    _settlement(c, load(c, now), cid, s, now)


def _confirm(c: sqlite3.Connection, cid: str, op: str, local: Mapping[str, Any], provider: Mapping[str, Any],
             sent: Sent, now: str) -> None:
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
        if entry:
            row = SX.ref_for_local(c, cid, "fin_entries", entry)
            if row is not None:
                SX.ref_set(c, str(row["id"]), local_id=entry, revision=str(row["revision"]), status="deleted", at=now)
            _retire(c, load(c, now), cid, entry, str(local["ext"]), now)
    elif op in ("bill.create", "invoice.create"):
        _confirm_document(c, cid, sent, local, now)
    elif op in ("bill.void", "invoice.void"):
        _confirm_void(c, cid, DOC_TABLES["Bill" if op.startswith("bill") else "Invoice"], local, sent, now)
    elif op in ("payment.bill", "payment.invoice"):
        _confirm_payment(c, cid, local, provider, sent, now)
    elif op == "writeoff.create":
        _confirm_writeoff(c, cid, local, provider, sent, now)
    elif op == "credit.apply":
        _confirm_apply(c, cid, provider, sent, now)


def _record(c: sqlite3.Connection, cid: str, o: Any, local: Mapping[str, Any], provider: Mapping[str, Any],
            sent: Sent, now: str, confirm: bool = True) -> None:
    """A write's answer recorded: sent (confirmed here unless the provider's record is taken in as it is), refused for
    good (unknown when the provider could not tell whether it landed), or to be tried again."""
    oid, attempts = str(o["id"]), int(o["attempts"]) + 1
    if sent.ok:
        SK.update(c, "fin_outbox", oid, {"status": "sent", "attempts": attempts, "claimed_at": None,
                                         "last_error": None, "updated_at": now})
        if confirm:
            _confirm(c, cid, str(o["op"]), local, provider, sent, now)
    elif sent.final:
        SK.update(c, "fin_outbox", oid, {"status": "failed", "attempts": attempts, "claimed_at": None,
                                         "last_error": sent.error[:300], "unknown": int(sent.unknown),
                                         "updated_at": now})
    else:
        delay = RETRY[min(attempts, len(RETRY)) - 1]
        SK.update(c, "fin_outbox", oid, {"attempts": attempts, "claimed_at": None, "last_error": sent.error[:300],
                                         "next_try_at": M.stamp(now, delay * 1_000_000), "updated_at": now})


# The creates a provider may already hold when a retry comes: a cut-off call can have landed, and a provider's
# idempotency key is only kept for a while.
RETRIED_CREATES = ("bill.create", "invoice.create", "journal.create", "party.create", "account.create",
                   "payment.bill", "payment.invoice", "writeoff.create", "credit.apply")
# The revision a provider gives a record it has just created (QuickBooks SyncToken). A provider without counters has
# none: its found record is confirmed as sent (Xero stamps times, which are not a count of changes).
INITIAL_REVISION = {"quickbooks": "0"}


def _initial_revision(c: sqlite3.Connection, cid: str) -> str:
    """The revision the provider gives a record it has just created, "" for a provider without counters."""
    row = SK.get(c, "fin_connections", cid)
    return INITIAL_REVISION.get(str(row["provider"]) if row is not None else "", "")


def _send(books: Books, o: Any, provider: Mapping[str, Any]) -> tuple[Sent, bool]:
    """The provider's answer to a write, and whether it is the provider's own record of an earlier try (found) rather than
    this call's answer. A create tried before is looked up first: when the provider holds it, that is the answer; when it
    cannot tell, the answer is final, so the write is marked unknown and never sent again."""
    op, key = str(o["op"]), str(o["idempotency_key"])
    if int(o["attempts"]) > 0 and op in RETRIED_CREATES:
        found = books.find(op, provider, key)
        if found is not None:
            return found, True
    return books.send(op, provider, key), False


def outbox(store: Any, books: Books, cid: str, now: str) -> list[str]:
    """This connection's writes, each claimed in a short transaction before its call (so two workers never send one
    twice) and recorded after it. A claim left from a cut-off call is released to be sent again: every write carries
    an idempotency key the provider keeps."""
    with store.transaction() as c:
        for o in SK.outbox_stuck(c, M.stamp(now, -CLAIM_MINUTES * 60 * 1_000_000), cid):
            # A claim left by a cut-off call may have sent the write: counted as an attempt, so it is looked up first.
            SK.update(c, "fin_outbox", str(o["id"]), {"claimed_at": None, "attempts": int(o["attempts"]) + 1,
                                                       "updated_at": now})
        due = SK.outbox_due(c, now, OUTBOX_MOST, cid)
    problems: list[str] = []
    for o in due:
        with store.transaction(durable=True) as c:
            if not SK.outbox_claim(c, str(o["id"]), now):
                continue
            prepared = _prepare(c, cid, o, now)
        if prepared is None:
            continue
        local, provider = prepared
        sent, found = _send(books, o, provider)
        # A record the provider changed after the write is not confirmed as sent. A provider that counts revisions
        # leaves the write unconfirmed; one that does not binds it at its oldest revision. Either way the change feed
        # is read again from the start, to take the provider's record in as it holds it.
        with store.transaction() as c:
            op, initial = str(o["op"]), _initial_revision(c, cid)
            revised = found and sent.ok and \
                _revised(c, cid, op, local, M.Ext(sent.kind, sent.external_id, sent.revision), sent.record)
            if revised and not initial:
                sent = Sent(ok=True, kind=sent.kind, external_id=sent.external_id, revision="")
            _record(c, cid, o, local, provider, sent, now, confirm=not revised or not initial)
            if revised:
                SX.set_mirror_state(c, cid, {"cursors": {}, "changed_at": None, "exact": []})
        if not sent.ok:
            problems.append(f"{o['op']}: {sent.error}")
    return problems


# ------------------------------------------------------------------------------------------------ passes
def _backfill_state(st: Mapping[str, object]) -> dict[str, object]:
    raw = st.get("backfill")
    return dict(cast(dict[str, object], raw)) if isinstance(raw, dict) else {}


def _entities(c: sqlite3.Connection, cid: str) -> tuple[str, ...]:
    """The entities a connection's provider is read through in full, in the order the books take them."""
    row = SK.get(c, "fin_connections", cid)
    return M.BOOK_ENTITIES.get(str(row["provider"]), ENTITIES) if row is not None else ENTITIES


def reading(c: sqlite3.Connection, cid: str) -> bool:
    """Whether the provider's books are still being read in full: the next pass has a page to read at once."""
    return not M.backfill_done(_backfill_state(SX.mirror_state(c, cid)), _entities(c, cid))


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


def _changes(store: Any, books: Books, cid: str, now: str, waiting: bool = False, every: int = M.CDC_EVERY) -> str:
    """The change feed, once the backfill is through and a feed is due, or at once when a webhook is waiting. Each feed (a
    provider entity) has its own cursor and is read from its last stamp. Each read also brings in again the records the
    books could not take in before (a party that has come since is enough)."""
    with store.read() as c:
        st = SX.mirror_state(c, cid)
    changed = st.get("changed_at")
    legacy = changed if isinstance(changed, str) else None      # before feeds had cursors
    stored = SX.as_map(st.get("cursors"))
    cursors: dict[str, str | None] = {}
    for feed in books.feeds:
        raw = stored.get(feed)
        cursors[feed] = raw if isinstance(raw, str) else legacy
    # A feed the provider cut short at its page cap is read again from its exact stamp, not moved back by the overlap.
    exact_raw = st.get("exact")
    exact = {str(feed) for feed in cast(list[object], exact_raw)} if isinstance(exact_raw, list) else set[str]()
    due = bool(exact) or any(M.cdc_due(cursors[feed], now, every) for feed in books.feeds)
    if not M.backfill_done(_backfill_state(st), books.entities) or not (waiting or due):
        return ""
    read = books.changed({feed: (cursors[feed] or now) if feed in exact else M.cdc_since(cursors[feed], now)
                          for feed in books.feeds})
    if not read.ok:
        return f"changes: {read.error}"
    with store.transaction() as c:
        fresh = {_ext_of(r).key for r in read.records}
        waiting_records = [M.decode(v) for k, v in _pending(c, cid).items() if k not in fresh]
        apply(c, cid, [*read.records, *waiting_records], now)
        # A feed the provider cut short is complete only to its `through`, so its next read starts there; the rest are
        # complete to this read.
        cut = dict(read.through)
        stamps = {feed: cut.get(feed, now) for feed in books.feeds}
        SX.set_mirror_state(c, cid, {"cursors": stamps, "changed_at": min(stamps.values(), key=M.parse),
                                     "exact": sorted(cut)})
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
              waiting: bool = False, every: int = M.CDC_EVERY) -> list[str]:
    """One pass over a mirrored connection: a backfill page, the change feed when due (or when a webhook waits), the
    export page, then the writes out. Provider calls run outside transactions; the outcome is recorded on the connection."""
    problems = [p for p in (_backfill(store, books, cid, now, size),
                            _changes(store, books, cid, now, waiting, every)) if p]
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
                    "emails": SK.loads_list(row_c["emails"]), "active": bool(row_c["active"])}
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
    if not M.backfill_done(state, _entities(ctx.c, cid)):
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


# ------------------------------------------------------------------------------------------------ documents out
def _create_planner(kind: str) -> handlers.Planner:
    """A bill or invoice written to the provider first: QuickBooks posts it when it is saved, so the books take its entry
    when the provider confirms the write. Its lines are checked the way the native planner checks them."""
    party_field, day_field = ("vendor_id", "bill_date") if kind == "bill" else ("customer_id", "invoice_date")
    allowed = (party_field, "number", day_field, "due_date", "terms_days", "memo", "lines", "origin", "attachments")
    if kind == "invoice":
        allowed += ("payment_link",)

    def planner(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
        V.only(p, allowed)
        cid = _cid(ctx)
        if p.get("attachments") or p.get("payment_link") not in (None, ""):
            raise FinanceError(NOT_AVAILABLE, "attachments and payment links are not sent to the provider yet")
        contact = documents.contact_of(ctx, V.text(p, party_field, 80), kind)
        number = V.opt_text(p, "number", 80) or ("" if kind == "bill" else documents.next_number(ctx, str(contact["id"])))
        if len(number) > 21:
            raise invalid("a number is up to 21 characters (the provider's limit)")
        if number and SAP.number_taken(ctx.c, kind, str(contact["id"]), number):
            raise FinanceError(CONFLICT, f"{contact['name']} already has {kind} {number}")
        day = V.day(p, day_field)
        terms = V.integer(p, "terms_days", 0, AP.MAX_TERMS) if p.get("terms_days") is not None else None
        due = AP.due_date(day, V.opt_day(p, "due_date"), terms)
        lines, _ = documents.resolve_lines(ctx, who, kind, contact, AP.doc_lines(p.get("lines")))
        total = sum(x.amount for x in lines)
        body = {"party": str(contact["id"]), "number": number, "day": day, "due": due or "",
                "memo": V.opt_text(p, "memo", 500) or "",
                "lines": [{"account_id": x.account_id, "amount": x.amount, "description": x.description,
                           "tags": dict(x.tags)} for x in lines]}

        def apply(aid: str) -> handlers.Result:
            oid = _queue(ctx.c, cid, f"{kind}.create", body, by=who, action_id=aid, at=ctx.at)
            return {"outbox_id": oid, "status": "queued", "total": total}
        return handlers.Plan(K.default_gate(ctx.org, who, f"{kind}_create"),
                             f"Send a {documents.money_of(total)} {ctx.book.base_currency} {kind} for {contact['name']}",
                             apply, facts=documents.doc_facts(kind, contact, lines, ctx))
    return planner


def _void_planner(kind: str) -> handlers.Planner:
    """A bill or invoice voided at the provider (QuickBooks deletes a bill and voids an invoice). Money settled against it
    is undone first, as it is natively; the books void it when the provider confirms."""
    table = DOC_TABLES["Bill" if kind == "bill" else "Invoice"]
    now_allowed = ("open", "scheduled") if kind == "bill" else ("sent",)

    def planner(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
        V.only(p, (f"{kind}_id", "reason"))
        cid = _cid(ctx)
        doc = documents.doc_of(ctx, kind, V.text(p, f"{kind}_id", 80))
        documents.need_status(kind, doc, now_allowed, "it cannot be voided")
        if int(doc["amount_paid"]) or (kind == "invoice" and int(doc["written_off"])) or \
                (kind == "bill" and SK.live_payment(ctx.c, f"bill:{doc['id']}") is not None):
            raise FinanceError(CONFLICT, "money is settled or on its way against this; undo that first")
        ref = SX.ref_for_local(ctx.c, cid, table, str(doc["id"]))
        if ref is None or str(ref["status"]) != "active":
            raise FinanceError(CONFLICT, "that is not in the provider yet")
        body = {"id": str(doc["id"]), "reason": V.opt_text(p, "reason", 300) or ""}

        def apply(aid: str) -> handlers.Result:
            oid = _queue(ctx.c, cid, f"{kind}.void", body, by=who, action_id=aid, at=ctx.at)
            return {f"{kind}_id": str(doc["id"]), "outbox_id": oid, "status": "queued"}
        return handlers.Plan(K.default_gate(ctx.org, who, f"{kind}_void"),
                             f"Void {kind} {doc['number'] or doc['id']}", apply,
                             facts={"amount": int(doc["total"]), "counterparty": str(doc["contact_name"] or "")})
    return planner


def _payment_planner(kind: str) -> handlers.Planner:
    """A bill paid (or an invoice paid) written to the provider as a bill payment (or a received payment), from a bank
    account the books hold or from a bank feed row. The books take the entry and the settlement when the provider
    confirms, and a feed row is marked posted with that entry, so the bank line is not posted twice."""
    words = "bill_mark_paid" if kind == "bill" else "invoice_record_payment"
    table = DOC_TABLES["Bill" if kind == "bill" else "Invoice"]

    def planner(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
        V.only(p, (f"{kind}_id", "amount", "date", "feed_id", "money_account_id", "account_id"))
        cid = _cid(ctx)
        doc = documents.doc_of(ctx, kind, V.text(p, f"{kind}_id", 80))
        if kind == "bill" and who != SYSTEM and not documents.sees_doc(ctx, who, kind, doc):
            raise FinanceError(NOT_FOUND, "no such bill")          # the same refusal as a missing bill
        documents.need_status(kind, doc, documents.OPEN[kind], "only an open one is paid")
        if kind == "bill" and SK.live_payment(ctx.c, f"bill:{doc['id']}") is not None:
            raise FinanceError(CONFLICT, "a payment for this bill is live; it settles the bill when it clears")
        ref = SX.ref_for_local(ctx.c, cid, table, str(doc["id"]))
        if ref is None or str(ref["status"]) != "active":
            raise FinanceError(CONFLICT, "that is not in the provider yet")
        left = documents.left_of(kind, doc) - _held(ctx.c, cid, kind, str(doc["id"]))
        given = V.opt_amount(p)
        amount = left if given is None else given     # 0 is refused below, never read as the balance left
        if not 0 < amount <= left:
            raise invalid(f"the amount is more than 0 and at most what is left ({left})")
        if sum(k in p for k in ("feed_id", "money_account_id", "account_id")) > 1:
            raise invalid("say one of feed_id (the bank or card row), money_account_id or account_id")
        day = V.opt_day(p, "date") or ctx.today
        feed: Any = None
        if "feed_id" in p:
            feed = SK.get(ctx.c, "fin_feed_txns", V.text(p, "feed_id", 80))
            if feed is None or not banking.seen(ctx, who, feed):
                raise FinanceError(NOT_FOUND, "no such feed row in your view")
            if int(feed["pending"]) or str(feed["status"]) != "new":
                raise FinanceError(CONFLICT, "that row is pending, matched or ignored")
            if SX.writes_for_feed(ctx.c, str(feed["id"])):
                raise FinanceError(CONFLICT, "that row's entry is already waiting to go to the provider")
            if (int(feed["amount"]) < 0) != (kind == "bill"):
                raise invalid("a bill is paid by money out, an invoice by money in")
            free = abs(int(feed["amount"])) - SAP.settled_from(ctx.c, "feed", str(feed["id"]))
            if amount > free:
                raise invalid(f"that row has {free} left to apply")
            day = V.opt_day(p, "date") or str(feed["txn_date"])
            money = SK.get(ctx.c, "fin_money_accounts", str(feed["money_account_id"]))
            if money is not None and documents.may_wait(ctx, feed, money):
                raise FinanceError(CONFLICT, "that row may be a payment already marked paid that waits for its bank "
                                             "row; link it with feed_match")
            bank_id = str(money["account_id"] or "") if money is not None else ""
        elif "money_account_id" in p:
            money = SK.get(ctx.c, "fin_money_accounts", V.text(p, "money_account_id", 80))
            if money is None or str(money["book_id"]) != ctx.book.id:
                raise FinanceError(NOT_FOUND, "no such money account")
            bank_id = str(money["account_id"] or "")
        elif "account_id" in p:
            bank_id = V.text(p, "account_id", 80)
        else:
            raise FinanceError(NOT_AVAILABLE, "a payment in the provider needs a bank account: say feed_id, "
                                              "money_account_id or account_id")
        bank = ctx.accounts.get(bank_id)
        if bank is None or not bank.active or bank.type not in ("asset", "liability") or \
                bank.id == documents.control(ctx, kind).id:
            raise FinanceError(NOT_AVAILABLE, "that money account has no account the books hold")
        if not handlers.may_write(ctx, who, bank.id, {}):
            raise denied("that line is outside your view")
        party = str(doc["vendor_id"] if kind == "bill" else doc["customer_id"])
        body = {"kind": kind, "doc_id": str(doc["id"]), "amount": amount, "day": day, "party": party,
                "bank": bank.id, "memo": f"Pays {kind} {doc['number'] or doc['id']}"[:200],
                "feed_id": str(feed["id"]) if feed is not None else "",
                "card": kind == "bill" and bank.type == "liability"}

        def apply(aid: str) -> handlers.Result:
            oid = _queue(ctx.c, cid, f"payment.{kind}", body, by=who, action_id=aid, at=ctx.at)
            return {f"{kind}_id": str(doc["id"]), "outbox_id": oid, "status": "queued", "amount": amount}
        verb = "paid on" if kind == "bill" else "received on"
        return handlers.Plan(K.default_gate(ctx.org, who, words),
                             f"Record {documents.money_of(amount)} {verb} {kind} {doc['number'] or doc['id']}", apply,
                             facts={"amount": amount, "counterparty": str(doc["contact_name"] or "")})
    return planner


def _writeoff_planner(ctx: Ctx, who: str, p: V.Payload) -> handlers.Plan:
    """An invoice written off as a credit memo billed by the write-off item and applied to the invoice it closes. The
    provider takes it, and the books take the entry and the settlement when it confirms."""
    V.only(p, ("invoice_id", "amount", "account_id", "date", "memo"))
    cid = _cid(ctx)
    inv = documents.doc_of(ctx, "invoice", V.text(p, "invoice_id", 80))
    documents.need_status("invoice", inv, documents.OPEN["invoice"], "only a sent invoice is written off")
    ref = SX.ref_for_local(ctx.c, cid, "fin_invoices", str(inv["id"]))
    if ref is None or str(ref["status"]) != "active":
        raise FinanceError(CONFLICT, "that is not in the provider yet")
    left = documents.left_of("invoice", inv) - _held(ctx.c, cid, "invoice", str(inv["id"]))
    given = V.opt_amount(p)
    amount = left if given is None else given     # 0 is refused below, never read as the balance left
    if not 0 < amount <= left:
        raise invalid(f"a write-off is more than 0 and at most what is left ({left})")
    if "account_id" in p:
        account = ctx.accounts.get(V.text(p, "account_id", 80))
    else:
        account = banking.by_code(ctx, banking.OTHER)
    if account is None or not account.active or account.type != "expense":
        raise FinanceError(NOT_FOUND, "no such expense account")
    if not handlers.may_write(ctx, who, account.id, {}):
        raise denied("that account is outside your view")
    if _key(ctx.c, cid, "fin_accounts", account.id) is None:
        raise FinanceError(NOT_AVAILABLE, "that account is not in the provider yet")
    day = V.opt_day(p, "date") or ctx.today
    memo = V.opt_text(p, "memo", 200) or f"Write off invoice {inv['number']}"
    body = {"invoice_id": str(inv["id"]), "party": str(inv["customer_id"]), "account_id": account.id,
            "amount": amount, "day": day, "memo": memo[:200]}

    def apply(aid: str) -> handlers.Result:
        oid = _queue(ctx.c, cid, "writeoff.create", body, by=who, action_id=aid, at=ctx.at)
        return {"invoice_id": str(inv["id"]), "outbox_id": oid, "status": "queued", "amount": amount}
    return handlers.Plan(K.default_gate(ctx.org, who, "invoice_write_off"),
                         f"Write off {documents.money_of(amount)} of invoice {inv['number']}", apply,
                         facts={"amount": amount, "counterparty": str(inv["contact_name"] or ""),
                                "accounts": [account.code]})


# Mirror-mode planners for the document actions: the provider takes the write first, so these queue it instead of
# posting here (actions.py uses them in place of the native planners while the books are a provider's).
DOC_PLANNERS: dict[str, handlers.Planner] = {
    "bill_create": _create_planner("bill"), "invoice_create": _create_planner("invoice"),
    "bill_void": _void_planner("bill"), "invoice_void": _void_planner("invoice"),
    "bill_mark_paid": _payment_planner("bill"), "invoice_record_payment": _payment_planner("invoice"),
    "invoice_write_off": _writeoff_planner,
}
