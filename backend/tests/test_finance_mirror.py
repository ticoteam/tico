"""Mirror mode (spec §10): a provider's records come into the books by ext ref, Tico's writes wait for the provider's
confirmation, bank rows match the provider's entries, and a closed month's change is flagged."""
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any

import pytest

from backend.finance import books_sync, mirror
from backend.finance import reads as FR
from backend.finance.context import load
from backend.finance.core import mirror as M
from backend.finance.core import qbo as Q
from backend.finance.core.errors import CONFLICT, NOT_AVAILABLE
from backend.finance.store import access as SA
from backend.finance.store import ap as SAP
from backend.finance.store import banking as SK
from backend.finance.store import ledger as SL
from backend.finance.store import mirror as SX
from backend.finance.store import schema
from backend.tests.test_finance_ap import _bill as _native_bill
from backend.tests.test_finance_ap import _ingest, _txn
from backend.tests.test_finance_ap import _vendor as _native_vendor
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, FakeMercury, account, act, books, connect, finance_template, store)
from backend.tests.test_finance_payments import _checking

LATER = "2026-10-15T13:00:00.000000Z"


@dataclass
class FakeBooks:
    """A provider's books in memory: records per entity, change feed, failures and confirmations queued by the test."""
    entities: tuple[str, ...] = mirror.ENTITIES
    records: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))
    changes: list[Any] = field(default_factory=list)
    fail: set[tuple[str, int]] = field(default_factory=set)
    replies: list[mirror.Sent] = field(default_factory=list)
    sent: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    found: dict[str, mirror.Sent] = field(default_factory=dict)      # by idempotency key: what the provider holds
    lookups: list[str] = field(default_factory=list)
    through: tuple[tuple[str, str], ...] = ()          # where each feed a read cut short is complete to
    feeds: tuple[str, ...] = ("Account", "Vendor", "Customer", "Bill", "Invoice", "CreditMemo", "BillPayment",
                              "Payment", "JournalEntry")

    def find(self, op: str, body: Any, key: str) -> mirror.Sent | None:
        self.lookups.append(key)
        return self.found.get(key)

    def page(self, entity: str, start: int, size: int) -> mirror.Read:
        if (entity, start) in self.fail:
            self.fail.discard((entity, start))
            return mirror.Read(False, error="the provider is down")
        return mirror.Read(True, tuple(self.records[entity][start - 1:start - 1 + size]))

    def changed(self, since: Mapping[str, str]) -> mirror.Read:
        out, self.changes = tuple(self.changes), []
        return mirror.Read(True, out, through=self.through)

    def send(self, op: str, body: Any, key: str) -> mirror.Sent:
        self.sent.append((op, dict(body), key))
        if self.replies:
            return self.replies.pop(0)
        kind = {"account.create": "Account", "party.create": "Vendor", "bill.create": "Bill", "bill.void": "Bill",
                "invoice.create": "Invoice", "invoice.void": "Invoice", "payment.bill": "BillPayment",
                "purchaseorder.create": "PurchaseOrder", "estimate.create": "Estimate",
                "payment.invoice": "Payment", "writeoff.create": "CreditMemo", "credit.apply": "Payment"}.get(
                    op, "JournalEntry")
        return mirror.Sent(True, kind=kind, external_id=str(900 + len(self.sent)), revision="1")


def acct(n: int, code: str, name: str, kind: str = "expense") -> M.ChartAccount:
    return M.ChartAccount(M.Ext("Account", str(n), "1"), code, name, kind)


def journal(n: int, rev: int, day: str, amount: int, debit: str = "Account:2", credit: str = "Account:1") -> M.Journal:
    return M.Journal(M.Ext("JournalEntry", str(n), str(rev)), day, "Acme Software", "sync",
                     (M.EntryLine(debit, amount), M.EntryLine(credit, -amount)))


def _connection(store: Any) -> str:
    cid = "conn-books"
    with store.transaction() as c:
        c.execute("INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) VALUES(?,?,?,?,?,?)",
                  (cid, "quickbooks", "books", AT, OWNER, "Acme books"))
    return cid


def _mirror(store: Any, cid: str) -> None:
    with store.transaction() as c:
        SL.set_book_mode(c, SL.book(c).id, "mirror", cid)  # type: ignore[union-attr]


def _drain(store: Any, fake: FakeBooks, cid: str, now: str = AT, passes: int = 6) -> list[str]:
    """Enough passes to read every entity's one page each and then the change feed."""
    problems: list[str] = []
    for _ in range(passes):
        problems += mirror.sync_once(store, fake, cid, now)
    return problems


def _local(c: sqlite3.Connection, ext: str, table: str) -> str:
    return str(c.execute("SELECT local_id FROM fin_external_refs WHERE external_id=? AND local_table=?",
                         (ext, table)).fetchone()[0])


def _entry_of(c: sqlite3.Connection, ext: str) -> str:
    return str(c.execute("SELECT local_id FROM fin_external_refs WHERE external_id=? AND local_table='fin_entries'",
                         (ext,)).fetchone()[0])


def _line_tags(c: sqlite3.Connection, entry: str, account: str) -> list[str]:
    """The team tags on an entry's lines on one account."""
    return [str(r[0]) for r in c.execute("SELECT t.value FROM fin_line_tags t JOIN fin_lines l ON l.id=t.line_id "
                                         "WHERE l.entry_id=? AND l.account_id=? AND t.kind='team'", (entry, account))]


def _chart(fake: FakeBooks) -> None:
    fake.records["accounts"] = [acct(1, "1000", "Bank", "asset"), acct(2, "6100", "Software")]


def test_a_failed_backfill_page_resumes_where_it_stopped_without_duplicates(books):
    fake = FakeBooks()
    fake.records["accounts"] = [acct(n, f"91{n:02d}", f"Account {n}") for n in range(1, 6)]
    fake.fail.add(("accounts", 3))
    cid = _connection(books)
    assert mirror.sync_once(books, fake, cid, AT, size=2) == []                  # accounts 1-2
    problems = mirror.sync_once(books, fake, cid, AT, size=2)                    # the page from 3 fails
    assert problems == ["backfill accounts from 3: the provider is down"]
    _drain(books, fake, cid, passes=3)                                           # 3-4, then 5, then the rest
    with books.read() as c:
        codes = [r[0] for r in c.execute("SELECT code FROM fin_accounts WHERE code LIKE '91%' ORDER BY code")]
        refs = c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE connection_id=?", (cid,)).fetchone()[0]
    assert codes == ["9101", "9102", "9103", "9104", "9105"]
    assert refs == 5
    assert mirror.sync_once(books, fake, cid, AT, size=2) == []                  # nothing left to read


def test_a_backfill_page_is_applied_in_chunks_so_a_failure_keeps_the_chunks_already_in(books, monkeypatch):
    fake = FakeBooks()
    fake.records["accounts"] = [acct(n, f"92{n:03d}", f"Account {n}") for n in range(1, 251)]
    cid = _connection(books)
    real, calls = mirror.apply, []

    def second_chunk_fails(c, connection, records, at):
        calls.append(len(records))
        if len(calls) == 2:
            raise RuntimeError("the store is locked")
        return real(c, connection, records, at)
    monkeypatch.setattr(mirror, "apply", second_chunk_fails)
    with pytest.raises(RuntimeError):
        mirror.sync_once(books, fake, cid, AT, size=250)                         # one page of 250 accounts
    assert calls == [100, 100]                                                   # applied 100 at a time, each its own write
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_accounts WHERE code LIKE '92%'").fetchone()[0] == 100
    monkeypatch.setattr(mirror, "apply", real)
    assert mirror.sync_once(books, fake, cid, AT, size=250) == []                # the page is read again from 1
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_accounts WHERE code LIKE '92%'").fetchone()[0] == 250
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE connection_id=?", (cid,)).fetchone()[0] == 250


def test_a_newer_revision_reverses_and_replaces_the_entry_and_keeps_the_books_balanced(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["entries"] = [journal(55, 1, "2026-10-05", 1500)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        old = _entry_of(c, "JournalEntry:55")
        original = c.execute("SELECT id, account_id, amount FROM fin_lines WHERE entry_id=? ORDER BY id",
                             (old,)).fetchall()
    fake.changes = [journal(55, 2, "2026-10-05", 2000)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        new = _entry_of(c, "JournalEntry:55")
        assert new != old
        assert c.execute("SELECT status FROM fin_entries WHERE id=?", (old,)).fetchone()[0] == "reversed"
        assert c.execute("SELECT COUNT(*) FROM fin_entries WHERE reverses_id=?", (old,)).fetchone()[0] == 1
        assert c.execute("SELECT id, account_id, amount FROM fin_lines WHERE entry_id=? ORDER BY id",
                         (old,)).fetchall() == original
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE entry_id=?", (new,)).fetchone()[0] == 0
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0
        assert c.execute("SELECT revision, status FROM fin_external_refs WHERE external_id='JournalEntry:55'"
                         ).fetchone()[:] == ("2", "active")


def test_an_edit_to_a_closed_month_posts_in_the_open_period_and_is_flagged(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["entries"] = [journal(56, 1, "2026-09-10", 1500)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    fake.changes = [journal(56, 2, "2026-09-10", 2000)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        rows = c.execute("SELECT r.change, r.day, p.start_date, e.entry_date FROM fin_restatements r "
                         "JOIN fin_periods p ON p.id=r.period_id JOIN fin_entries e ON e.id=r.reversal_id").fetchall()
        assert [tuple(r) for r in rows] == [("replaced", "2026-09-10", "2026-09-01", "2026-10-15")]
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0


def test_a_bank_row_is_written_out_only_until_the_provider_confirms_it(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        money, fid = _bank_row(c, -1500, "2026-10-05")
        before = c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0]
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
        assert done.status == "committed", done
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == before
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[:] == ("new", None)
        assert c.execute("SELECT op, status FROM fin_outbox").fetchone()[:] == ("journal.create", "pending")
    assert _drain(books, fake, cid, now=LATER) == []
    op, body, _ = fake.sent[0]
    assert op == "journal.create"
    assert sorted(line["account"] for line in body["lines"]) == ["Account:1", "Account:2"]
    with books.read() as c:
        status, entry = c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()
        assert status == "posted" and entry
        assert c.execute("SELECT amount FROM fin_lines WHERE entry_id=? AND account_id=?",
                         (entry, account(c, "1000"))).fetchone()[0] == -1500
        assert c.execute("SELECT status FROM fin_outbox").fetchone()[0] == "sent"
        assert c.execute("SELECT external_id FROM fin_external_refs WHERE local_id=?", (entry,)).fetchone()[0] \
            == "JournalEntry:901"


def test_a_bank_row_takes_the_provider_entry_it_matches_instead_of_a_duplicate(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["entries"] = [journal(60, 1, "2026-10-05", 1500, debit="Account:2", credit="Account:1")]
    cid = _connection(books)
    with books.transaction() as c:
        _bank_row(c, -1500, "2026-10-05")
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        entry = _entry_of(c, "JournalEntry:60")
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns").fetchone()[:] == ("matched", entry)
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == 0
    with books.transaction() as c:
        fid = str(c.execute("SELECT id FROM fin_feed_txns").fetchone()[0])
        refused = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
        assert (refused.status, refused.code) == ("failed", CONFLICT)


def test_a_bank_row_written_out_is_not_sent_when_the_provider_entry_came_after_the_last_change_read(books):
    """Their own bank feed made the entry after the books were last read, and no change read is due: the write out waits
    for the books to be read up to now, so the row takes that entry and the provider is not sent a second one."""
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        _bank_row(c, -1500, "2026-10-05")
        fid = str(c.execute("SELECT id FROM fin_feed_txns").fetchone()[0])
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
        assert done.status == "committed", done
    fake.changes = [journal(60, 1, "2026-10-05", 1500, debit="Account:2", credit="Account:1")]
    assert mirror.sync_once(books, fake, cid, AT) == []
    assert fake.sent == []
    with books.read() as c:
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns").fetchone()[:] == (
            "matched", _entry_of(c, "JournalEntry:60"))
        assert c.execute("SELECT status FROM fin_outbox").fetchone()[0] == "failed"
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 1


def test_a_bank_row_write_out_waits_when_the_provider_books_cannot_be_read_first(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        _bank_row(c, -1500, "2026-10-05")
        fid = str(c.execute("SELECT id FROM fin_feed_txns").fetchone()[0])
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}}).status == "committed"
    fake.changed = lambda since: mirror.Read(False, error="the provider is down")
    assert mirror.sync_once(books, fake, cid, AT) == ["changes: the provider is down"]
    assert fake.sent == []
    with books.read() as c:
        assert c.execute("SELECT status, next_try_at IS NOT NULL FROM fin_outbox").fetchone()[:] == ("pending", 1)
        assert c.execute("SELECT status FROM fin_feed_txns").fetchone()[0] == "new"


def test_switching_previews_first_and_a_mirrored_book_refuses_what_the_provider_holds(books):
    cid = _connection(books)
    with books.transaction() as c:
        preview = act(c, OWNER, "mirror_start", {"connection_id": cid}, dry_run=True)
        assert preview.status == "committed" and preview.impact["accounts"] > 0
        assert SL.book(c).mode == "native"                                        # a preview changes nothing
        assert act(c, OWNER, "mirror_start", {"connection_id": cid}).status == "committed"
        assert SL.book(c).mode == "mirror"
        refused = act(c, OWNER, "contact_create", {"name": "Acme Vendor", "kind": "vendor"})
        assert (refused.status, refused.code) == ("denied", NOT_AVAILABLE)


def test_a_mode_switch_keeps_an_unclear_row_from_paying_a_provider_bill(books):
    bank = FakeMercury()
    connect(books, bank)
    with books.transaction() as c:
        vendor = _native_vendor(c)
        for n in (1, 2):
            bid = _native_bill(c, vendor, [{"account_id": account(c, "6100"), "amount": 5_000}], day="2026-10-01",
                               number=f"O-{n}")
            assert act(c, OWNER, "bill_approve", {"bill_id": bid}).status == "committed"
            # Marked five days before the row: the payment search reads that far, the bank matcher only three.
            paid = act(c, OWNER, "bill_mark_paid",
                       {"bill_id": bid, "money_account_id": _checking(c), "date": "2026-10-09"})
            assert paid.status == "committed", paid
        # Two equal payments still wait for this 50.00 debit, which names no one: it stays new for a person to link.
        row = _ingest(c, _txn("txn-o", -50.0, who="MERCURY ACH"))
        assert c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[:] == ("new", None)
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [_bill(12, 1, 5_000)]
    cid = _connection(books)
    with books.transaction() as c:
        assert act(c, OWNER, "mirror_start", {"connection_id": cid}).status == "committed"
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        provider_bill = _local(c, "Bill:12", "fin_bills")
        assert len(FR.feed_expected(c, OWNER, row, at=AT)["expected"]) == 2     # the payments still wait for the row
        for dry in (True, False):
            refused = act(c, OWNER, "bill_mark_paid", {"bill_id": provider_bill, "feed_id": row}, dry_run=dry)
            assert (refused.status, refused.code) == ("failed", CONFLICT), refused
            assert "feed_match" in refused.reason
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op LIKE 'payment.%'").fetchone()[0] == 0
        # Categorizing the row would queue a journal for it at the provider: a second bank line beside the payments'.
        queued = c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0]
        for dry in (True, False):
            tagged = act(c, OWNER, "categorize", {"feed_id": row, "account_id": account(c, "6100")}, dry_run=dry)
            assert (tagged.status, tagged.code) == ("failed", CONFLICT), tagged
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == queued


def _bank_row(c: sqlite3.Connection, amount: int, day: str) -> tuple[str, str]:
    """A bank account on the 1000 line and one new row on it (what the bank feed brings in)."""
    money, fid = SL.new_id(), SL.new_id()
    book = SL.book(c)
    c.execute("INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?,?)", (money, book.id, "bank", "Checking", "mercury", account(c, "1000"), AT, OWNER))  # type: ignore[union-attr]
    c.execute("INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
              "status, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (fid, money, f"txn-{fid[:8]}", day, amount, book.base_currency, "Acme Software", "new", AT, OWNER))  # type: ignore[union-attr]
    return money, fid


def _bill(n: int, rev: int, total: int) -> M.Document:
    return M.Document(M.Ext("Bill", str(n), str(rev)), party="Vendor:9", number=f"B-{n}", day="2026-10-05",
                      due="2026-11-05", total=total, lines=(M.EntryLine("Account:2", total, description="seats"),))


def _vendor(fake: FakeBooks) -> None:
    fake.records["parties"] = [M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor")]


def _customer(fake: FakeBooks) -> None:
    fake.records["parties"] = [M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor"),
                               M.Party(M.Ext("Customer", "4", "1"), "Beta Client", "customer")]
    fake.records["accounts"].append(acct(3, "4000", "Consulting", "income"))


def _sent(fake: FakeBooks, op: str) -> dict[str, Any]:
    return next(body for name, body, _ in fake.sent if name == op)


def _invoice(n: int, rev: int, total: int) -> M.Document:
    return M.Document(M.Ext("Invoice", str(n), str(rev)), party="Customer:4", number=f"I-{n}", day="2026-10-05",
                      due="2026-11-05", total=total, lines=(M.EntryLine("Account:3", total, description="consulting"),))


def test_a_bill_posts_its_accrual_and_a_newer_revision_replaces_it_and_stays_balanced(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [_bill(12, 1, 1500)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        bill = _local(c, "Bill:12", "fin_bills")
        old = str(c.execute("SELECT entry_id FROM fin_bills WHERE id=?", (bill,)).fetchone()[0])
        assert c.execute("SELECT source FROM fin_entries WHERE id=?", (old,)).fetchone()[0] == "bill"
        original = c.execute("SELECT id, account_id, amount FROM fin_lines WHERE entry_id=? ORDER BY id",
                             (old,)).fetchall()
    fake.changes = [_bill(12, 2, 2000)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        total, status, new = c.execute("SELECT total, status, entry_id FROM fin_bills WHERE id=?", (bill,)).fetchone()
        assert (total, status) == (2000, "open") and new != old
        assert c.execute("SELECT status FROM fin_entries WHERE id=?", (old,)).fetchone()[0] == "reversed"
        assert c.execute("SELECT id, account_id, amount FROM fin_lines WHERE entry_id=? ORDER BY id",
                         (old,)).fetchall() == original
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0
        assert c.execute("SELECT revision FROM fin_external_refs WHERE external_id='Bill:12'").fetchone()[0] == "2"


def test_a_bill_payment_settles_the_bill_and_deleting_it_puts_the_bill_back_owing(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [_bill(12, 1, 1500)]
    fake.records["payments"] = [M.Settlement(M.Ext("BillPayment", "30", "1"), day="2026-10-12", memo="Acme payment",
                                             party="Vendor:9", bank="Account:1", total=1500,
                                             links=(("Bill:12", 1500),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        bill = _local(c, "Bill:12", "fin_bills")
        pay = _local(c, "BillPayment:30", "fin_entries")
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_bills WHERE id=?", (bill,)).fetchone()) == (
            "paid", 1500)
        settled = c.execute("SELECT from_kind, to_kind, amount, entry_id FROM fin_settlements WHERE to_id=? "
                            "AND voided_at IS NULL", (bill,)).fetchall()
        assert [tuple(row) for row in settled] == [("account", "bill", 1500, pay)]
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "2000"),)
                         ).fetchone()[0] == 0
    fake.changes = [M.Ext("BillPayment", "30", "2", True)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_bills WHERE id=?", (bill,)).fetchone()) == (
            "open", 0)
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (bill,)
                         ).fetchone()[0] == 0
        assert c.execute("SELECT status FROM fin_entries WHERE id=?", (pay,)).fetchone()[0] == "reversed"
        # The bill is owed again: its accrual is the credit left on payables.
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "2000"),)
                         ).fetchone()[0] == -1500


def test_a_document_whose_lines_do_not_add_up_to_its_total_is_not_posted(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    short = M.Document(M.Ext("Bill", "13", "1"), party="Vendor:9", number="B-13", day="2026-10-05", due="", total=1600,
                       lines=(M.EntryLine("Account:2", 1500),))
    fake.records["documents"] = [short]
    cid = _connection(books)
    _mirror(books, cid)
    _drain(books, fake, cid)
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Bill:13'").fetchone()[0] == "unmapped"
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE number='B-13'").fetchone()[0] == 0


LATER_TWICE = "2026-10-15T15:00:00.000000Z"


def test_a_bill_made_here_goes_to_the_provider_and_is_booked_once_the_provider_confirms(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-77", "bill_date": "2026-10-05",
            "due_date": "2026-11-05", "lines": [{"account_id": account(c, "6100"), "amount": 1500,
                                                 "description": "seats"}]})
        assert made.status == "committed", made
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "bill.create") == {"party": "Vendor:9", "number": "B-77", "day": "2026-10-05",
                                          "due": "2026-11-05", "memo": "", "lines": [
                                              {"account": "Account:2", "amount": 1500, "description": "seats", "item":
                                              "", "item_code": "", "location": "",
                   "class": "", "project": ""}]}
    with books.read() as c:
        bill = _local(c, "Bill:901", "fin_bills")
        assert tuple(c.execute("SELECT status, total, number FROM fin_bills WHERE id=?", (bill,)).fetchone()) == (
            "open", 1500, "B-77")
        assert c.execute("SELECT source FROM fin_entries WHERE id=(SELECT entry_id FROM fin_bills WHERE id=?)",
                         (bill,)).fetchone()[0] == "bill"
        assert c.execute("SELECT status FROM fin_outbox WHERE op='bill.create'").fetchone()[0] == "sent"


def test_an_invoice_voided_here_is_voided_at_the_provider_and_its_echo_adds_nothing(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "invoice_create", {
            "customer_id": _local(c, "Customer:4", "fin_contacts"), "number": "I-5", "invoice_date": "2026-10-05",
            "lines": [{"account_id": account(c, "4000"), "amount": 2000, "description": "consulting"}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    with books.transaction() as c:
        inv = _local(c, "Invoice:901", "fin_invoices")
        assert act(c, OWNER, "invoice_void", {"invoice_id": inv}).status == "committed"
    fake.replies.append(mirror.Sent(True, kind="Invoice", external_id="901", revision="2"))
    assert _drain(books, fake, cid, now=LATER_TWICE) == []
    assert _sent(fake, "invoice.void") == {"ext": "Invoice:901", "revision": "1"}
    # The provider's echo of its own void: zeroed, at the revision the void left.
    fake.changes = [M.Document(M.Ext("Invoice", "901", "2"), party="Customer:4", number="I-5", day="2026-10-05",
                               due="", total=0, lines=(M.EntryLine("Account:3", 0),))]
    assert _drain(books, fake, cid, now="2026-10-15T16:00:00.000000Z") == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, total FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == ("void", 2000)
        assert c.execute("SELECT COUNT(*) FROM fin_entries WHERE source='invoice' AND status='posted' "
                         "AND reverses_id IS NULL").fetchone()[0] == 0
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Invoice:901'").fetchone()[0] == "deleted"


def test_a_payment_received_here_settles_the_invoice_once_the_provider_confirms(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [_invoice(12, 1, 2000)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")}
                   ).status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    body = _sent(fake, "payment.invoice")
    assert (body["party"], body["bank"], body["total"], body["links"]) == ("Customer:4", "Account:1", 2000,
                                                                       [["Invoice:12", 2000]])
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)
        settled = c.execute("SELECT from_kind, to_kind, amount FROM fin_settlements WHERE to_id=? AND voided_at IS NULL",
                            (inv,)).fetchall()
        assert [tuple(row) for row in settled] == [("account", "invoice", 2000)]


def test_a_credit_applied_by_a_payment_of_nothing_settles_the_invoice_and_deleting_it_puts_the_invoice_back_owing(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    credit = M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                        total=500, lines=(M.EntryLine("Account:3", 500),))
    applied = M.Settlement(M.Ext("Payment", "30", "1"), day="2026-10-11", memo="credit applied", party="Customer:4",
                           bank="", total=0, links=(("Invoice:12", 500),), credits=(("CreditMemo:20", 500),))
    fake.records["documents"] = [_invoice(12, 1, 2000), credit]
    fake.records["payments"] = [applied]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "partial", 500)
        assert [tuple(r) for r in c.execute("SELECT from_kind FROM fin_settlements WHERE to_id=? AND voided_at IS NULL",
                                            (inv,)).fetchall()] == [("credit",)]
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0
    fake.changes = [M.Ext("Payment", "30", "2", True)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "sent", 0)
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (inv,)
                         ).fetchone()[0] == 0


def test_a_bill_waiting_on_its_vendor_is_booked_on_the_next_change_feed_read_after_the_vendor_arrives(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["documents"] = [_bill(12, 1, 1500)]          # its vendor is not in the provider's parties yet
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Bill:12'").fetchone()[0] == "unmapped"
    fake.changes = [M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor")]     # the bill itself is unchanged
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE total=1500").fetchone()[0] == 1
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Bill:12'").fetchone()[0] == "active"
        assert SX.mirror_state(c, cid).get("pending") in (None, {})


def test_a_record_held_for_later_reads_back_as_the_record_it_was():
    held: list[M.Record] = [
        M.Document(M.Ext("Bill", "12", "3"), "Vendor:9", "B-12", "2026-10-05", "2026-11-05", 1500,
                   (M.EntryLine("Account:2", 1500, "Vendor:9", "seats"),), "memo", (("Bill:1", 5),)),
        M.Settlement(M.Ext("BillPayment", "30", "2"), "2026-10-12", "Acme", "Vendor:9", "Account:1", 1500,
                     (("Bill:12", 1500),)),
        M.Party(M.Ext("Vendor", "9", "1"), "Acme Software", "vendor", True, ("ap@acme.example",)),
        M.ChartAccount(M.Ext("Account", "2", "1"), "6100", "Software", "expense"),
        M.ChartAccount(M.Ext("Account", "7", "1"), "6200", "Payroll expenses", "expense", payroll=True),
        M.Journal(M.Ext("JournalEntry", "7", "1"), "2026-10-05", "memo", "sync", (M.EntryLine("Account:2", 10),)),
        M.Ext("Bill", "12", "4", True),
    ]
    for r in held:
        assert M.decode(json.loads(json.dumps(M.encode(r)))) == r
    assert M.decide(("2", "deleted"), M.Ext("Invoice", "901", "2")) == M.SAME    # our own void, echoed back
    # A deleted record stays deleted under a later revision: QuickBooks does not bring back an Id it deleted.
    assert M.decide(("2", "deleted"), M.Ext("Invoice", "901", "3")) == M.SAME


def test_a_write_off_is_a_credit_memo_then_a_payment_of_nothing_that_applies_it_to_the_invoice(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [_invoice(12, 1, 2000)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        made = act(c, OWNER, "invoice_write_off", {"invoice_id": inv, "account_id": account(c, "6100"),
                                                   "date": "2026-10-12", "memo": "Uncollectible"})
        assert made.status == "committed", made
    fake.replies += [mirror.Sent(True, kind="CreditMemo", external_id="901", revision="1"),
                     mirror.Sent(True, kind="Payment", external_id="902", revision="1")]
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "writeoff.create") == {"party": "Customer:4", "account": "Account:2", "day": "2026-10-12",
                                              "memo": "Uncollectible", "total": 2000, "invoice": "Invoice:12"}
    assert _sent(fake, "credit.apply") == {"invoice": "Invoice:12", "credit": "CreditMemo:901", "party": "Customer:4",
                                           "invoice_id": inv, "amount": 2000, "day": "2026-10-12",
                                           "memo": "Uncollectible"}
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)
        settled = c.execute("SELECT from_kind, to_kind, amount FROM fin_settlements WHERE to_id=? AND voided_at IS NULL",
                            (inv,)).fetchall()
        assert [tuple(row) for row in settled] == [("credit", "invoice", 2000)]
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "6100"),)).fetchone()[0] == 2000
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='CreditMemo:901'").fetchone()[0] == "active"
    # The echoes carry the revisions the writes were sent with, so nothing is booked or settled again.
    fake.changes = [M.Document(M.Ext("CreditMemo", "901", "1"), party="Customer:4", number="", day="2026-10-12",
                               due="", total=2000, lines=(M.EntryLine("Account:2", 2000),), memo="Uncollectible"),
                    M.Settlement(M.Ext("Payment", "902", "1"), day="2026-10-12", memo="Uncollectible",
                                 party="Customer:4", bank="", total=0, links=(("Invoice:12", 2000),),
                                 credits=(("CreditMemo:901", 2000),))]
    assert _drain(books, fake, cid, now="2026-10-15T17:00:00.000000Z") == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (inv,)
                         ).fetchone()[0] == 1
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)


def _bill_key(books, fake: FakeBooks, cid: str) -> str:
    """A bill made here, not yet sent: its outbox key."""
    with books.transaction() as c:
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-77", "bill_date": "2026-10-05",
            "due_date": "2026-11-05", "lines": [{"account_id": account(c, "6100"), "amount": 1500,
                                                 "description": "seats"}]})
        assert made.status == "committed", made
    with books.read() as c:
        return str(c.execute("SELECT idempotency_key FROM fin_outbox WHERE op='bill.create'").fetchone()[0])


def test_a_create_cut_off_after_the_provider_took_it_is_found_and_not_sent_again(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    key = _bill_key(books, fake, cid)
    fake.replies.append(mirror.Sent(False, error="QuickBooks did not answer"))     # the write landed; no answer came
    _drain(books, fake, cid, now=LATER)
    fake.found[key] = mirror.Sent(True, kind="Bill", external_id="901", revision="0")
    assert _drain(books, fake, cid, now="2026-10-15T14:00:00.000000Z") == []
    assert [op for op, _, _ in fake.sent if op == "bill.create"] == ["bill.create"]
    assert fake.lookups == [key]
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE total=1500").fetchone()[0] == 1
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Bill:901'").fetchone()[0] == "active"
        assert c.execute("SELECT status FROM fin_outbox WHERE op='bill.create'").fetchone()[0] == "sent"


def test_a_retry_the_provider_cannot_rule_out_is_marked_unknown_and_never_sent_again(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    key = _bill_key(books, fake, cid)
    fake.replies.append(mirror.Sent(False, error="QuickBooks did not answer"))
    _drain(books, fake, cid, now=LATER)
    fake.found[key] = mirror.Sent(False, error="UNKNOWN: too many writes that day to tell", final=True)
    assert _drain(books, fake, cid, now="2026-10-15T14:00:00.000000Z") == [
        "bill.create: UNKNOWN: too many writes that day to tell"]
    assert [op for op, _, _ in fake.sent if op == "bill.create"] == ["bill.create"]
    with books.read() as c:
        row = c.execute("SELECT status, last_error FROM fin_outbox WHERE op='bill.create'").fetchone()
        assert row[0] == "failed" and row[1].startswith("UNKNOWN")
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 0


def test_a_write_left_claimed_by_a_cut_off_call_is_looked_up_before_it_is_sent_again(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    key = _bill_key(books, fake, cid)
    with books.transaction() as c:
        c.execute("UPDATE fin_outbox SET claimed_at=? WHERE op='bill.create'", ("2026-10-01T00:00:00.000000Z",))
    fake.found[key] = mirror.Sent(True, kind="Bill", external_id="901", revision="0")
    assert _drain(books, fake, cid, now=LATER) == []
    assert [op for op, _, _ in fake.sent if op == "bill.create"] == []
    assert fake.lookups == [key]
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Bill:901'").fetchone()[0] == "active"


def test_a_void_of_a_closed_month_bill_at_the_provider_is_flagged_as_a_restatement(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [M.Document(M.Ext("Bill", "12", "1"), party="Vendor:9", number="B-12",
                                            day="2026-09-10", due="2026-10-10", total=1500,
                                            lines=(M.EntryLine("Account:2", 1500),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    fake.changes = [M.Ext("Bill", "12", "2", True)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert [tuple(r) for r in c.execute("SELECT change, day FROM fin_restatements").fetchall()] == [
            ("deleted", "2026-09-10")]
        assert c.execute("SELECT status FROM fin_bills WHERE number='B-12'").fetchone()[0] == "void"
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0


def test_a_bill_the_provider_took_before_its_answer_was_kept_is_adopted_from_the_change_feed_and_not_sent_again(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    key = "bill.create:adopt-1"
    with books.transaction() as c:
        body = {"party": _local(c, "Vendor:9", "fin_contacts"), "number": "B-77", "day": "2026-10-05",
                "due": "2026-11-05", "memo": "", "lines": [{"account_id": _local(c, "Account:2", "fin_accounts"),
                                                            "amount": 1500, "description": "seats", "tags": {}}]}
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=body, key=key, by=OWNER, at=AT)
    # The provider took the bill, but the answer was cut off: its note carries the reference, and the feed brings it.
    fake.changes = [M.Document(M.Ext("Bill", "77", "0", reference=M.reference(key)), party="Vendor:9", number="B-77",
                               day="2026-10-05", due="2026-11-05", total=1500,
                               lines=(M.EntryLine("Account:2", 1500, description="seats"),))]
    assert _drain(books, fake, cid, now=LATER) == []
    assert not [op for op, _, _ in fake.sent if op == "bill.create"]
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE number='B-77'").fetchone()[0] == 1
        assert c.execute("SELECT status FROM fin_outbox WHERE idempotency_key=?", (key,)).fetchone()[0] == "sent"


def test_a_taxed_invoice_posts_its_tax_to_the_sales_tax_account_and_balances(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["accounts"].append(M.ChartAccount(M.Ext("Account", "9", "1"), "2300", "Sales tax payable", "liability",
                                                   subtype=M.SALES_TAX))
    fake.records["documents"] = [M.Document(M.Ext("Invoice", "12", "1"), party="Customer:4", number="I-12",
                                            day="2026-10-05", due="2026-11-05", total=2100,
                                            lines=(M.EntryLine("Account:3", 2000, description="consulting"),), tax=100)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        tax = c.execute("SELECT id FROM fin_accounts WHERE subtype=?", (M.SALES_TAX,)).fetchone()[0]
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (tax,)).fetchone()[0] == -100
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0


def test_a_bill_made_here_keeps_its_line_tags_and_a_newer_revision_carries_them(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-77", "bill_date": "2026-10-05",
            "due_date": "2026-11-05", "lines": [{"account_id": account(c, "6100"), "amount": 1500,
                                                 "description": "seats", "tags": {"team": "eng"}}]})
        assert made.status == "committed", made
    assert _drain(books, fake, cid, now=LATER) == []
    fake.changes = [M.Document(M.Ext("Bill", "901", "2"), party="Vendor:9", number="B-77", day="2026-10-05",
                               due="2026-11-05", total=1500,
                               lines=(M.EntryLine("Account:2", 1500, description="seats"),))]
    assert _drain(books, fake, cid, now="2026-10-15T14:00:00.000000Z") == []
    with books.read() as c:
        bill = _local(c, "Bill:901", "fin_bills")
        assert c.execute("SELECT revision FROM fin_external_refs WHERE external_id='Bill:901'").fetchone()[0] == "2"
        tags = [json.loads(row[0]) for row in c.execute("SELECT tags_json FROM fin_bill_lines WHERE bill_id=?", (bill,))]
        assert tags == [{"team": "eng"}]


def test_a_bank_row_written_out_waits_until_the_provider_books_are_read_in_full(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}}
                   ).status == "committed"
    mirror.sync_once(books, fake, cid, AT)                  # one page of one entity: the books are still being read
    assert fake.sent == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_outbox WHERE op='journal.create'").fetchone()[0] == "pending"
    assert _drain(books, fake, cid, now=LATER) == []        # the books finish reading; the write waits its retry
    assert fake.sent == []
    assert _drain(books, fake, cid, now="2026-10-15T13:02:00.000000Z") == []
    assert [op for op, _, _ in fake.sent] == ["journal.create"]


def test_a_payment_of_part_of_a_bank_row_leaves_the_row_open_until_the_rest_is_paid(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [_bill(12, 1, 1500)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        ctx = load(c, AT)
        part = M.Settlement(M.Ext("BillPayment", "31", "1"), day="2026-10-12", memo="Acme part", party="Vendor:9",
                            bank="Account:1", total=1000, links=(("Bill:12", 1000),))
        mirror._settlement(c, ctx, cid, part, AT, feed=fid)      # the bank row's own payment, as the write-out takes it
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == "new"
        assert SAP.settled_from(c, "feed", fid) == 1000
        rest = M.Settlement(M.Ext("BillPayment", "32", "1"), day="2026-10-12", memo="Acme rest", party="Vendor:9",
                            bank="Account:1", total=500, links=(("Bill:12", 500),))
        mirror._settlement(c, ctx, cid, rest, AT, feed=fid)
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == "posted"


def test_a_change_read_the_provider_cut_short_resumes_from_where_it_is_complete(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    fake.through = (("Invoice", "2026-10-15T13:30:00.000000Z"),)
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        cursors = SX.mirror_state(c, cid)["cursors"]
        assert cursors["Invoice"] == "2026-10-15T13:30:00.000000Z"
        assert cursors["Bill"] == LATER
        assert SX.mirror_state(c, cid)["changed_at"] == LATER          # the earliest stamp: complete to there


def test_a_bank_row_matched_while_its_entry_was_out_withdraws_the_entry_at_the_provider(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}}
                   ).status == "committed"
        c.execute("UPDATE fin_feed_txns SET status='ignored' WHERE id=?", (fid,))
        local = SK.loads(c.execute("SELECT payload_json FROM fin_outbox WHERE op='journal.create'").fetchone()[0])
        mirror._confirm_entry(c, cid, local, M.Ext("JournalEntry", "77", "1"), AT)
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 0
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='JournalEntry:77'").fetchone()[0] \
            == "deleted"
        payload = SK.loads(c.execute("SELECT payload_json FROM fin_outbox WHERE op='journal.delete'").fetchone()[0])
        assert (payload["ext"], payload["revision"]) == ("JournalEntry:77", "1")


def test_the_books_loop_reads_on_while_the_provider_books_are_not_yet_read_in_full(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert books_sync._reading(books)
    assert _drain(books, fake, cid) == []
    assert not books_sync._reading(books)


def test_a_bank_row_part_paid_by_a_payment_is_written_out_for_the_rest_only(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [_bill(12, 1, 1500)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        part = M.Settlement(M.Ext("BillPayment", "31", "1"), day="2026-10-12", memo="Acme part", party="Vendor:9",
                            bank="Account:1", total=1000, links=(("Bill:12", 1000),))
        mirror._settlement(c, load(c, AT), cid, part, AT, feed=fid)
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
        assert done.status == "committed", done
    assert _drain(books, fake, cid, now=LATER) == []
    op, body, _ = fake.sent[-1]
    assert op == "journal.create"
    assert sorted((line["account"], line["amount"]) for line in body["lines"]) == [("Account:1", -500),
                                                                                  ("Account:2", 500)]
    with books.read() as c:
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "1000"),)).fetchone()[0] \
            == -1500
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == "posted"


def test_a_bill_the_provider_revised_before_recovery_is_imported_as_the_provider_holds_it(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    key = "bill.create:adopt-2"
    with books.transaction() as c:
        body = {"party": _local(c, "Vendor:9", "fin_contacts"), "number": "B-78", "day": "2026-10-05",
                "due": "2026-11-05", "memo": "", "lines": [{"account_id": _local(c, "Account:2", "fin_accounts"),
                                                            "amount": 1500, "description": "seats", "tags": {}}]}
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=body, key=key, by=OWNER, at=AT)
    # The write landed, then an accountant changed it before the books recovered it: its newer record is imported.
    fake.changes = [M.Document(M.Ext("Bill", "78", "2", reference=M.reference(key)), party="Vendor:9", number="B-78",
                               day="2026-10-05", due="2026-11-05", total=2000,
                               lines=(M.EntryLine("Account:2", 2000, description="seats"),))]
    assert _drain(books, fake, cid, now=LATER) == []
    assert not [op for op, _, _ in fake.sent if op == "bill.create"]
    with books.read() as c:
        bill = _local(c, "Bill:78", "fin_bills")
        assert c.execute("SELECT total FROM fin_bills WHERE id=?", (bill,)).fetchone()[0] == 2000
        assert c.execute("SELECT COUNT(*) FROM fin_bills WHERE number='B-78'").fetchone()[0] == 1
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0


def test_a_write_off_moves_no_cash_so_it_recognizes_no_cash_income(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [_invoice(12, 1, 2000)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        income = account(c, "4000")
        before = c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_cash_rollups WHERE account_id=?", (income,)
                           ).fetchone()[0]
    with books.transaction() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        made = act(c, OWNER, "invoice_write_off", {"invoice_id": inv, "account_id": account(c, "6100"),
                                                   "date": "2026-10-12", "memo": "Uncollectible"})
        assert made.status == "committed", made
    fake.replies += [mirror.Sent(True, kind="CreditMemo", external_id="901", revision="1"),
                     mirror.Sent(True, kind="Payment", external_id="902", revision="1")]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)
        # No money moved, so the application adds no cash income: the income's cash view is what it was.
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_cash_rollups WHERE account_id=?", (income,)
                         ).fetchone()[0] == before


def test_a_zero_payment_revised_into_a_cash_payment_keeps_one_settlement_set_and_its_deletion_clears_the_invoice(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    credit = M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                        total=500, lines=(M.EntryLine("Account:3", 500),))
    applied = M.Settlement(M.Ext("Payment", "30", "1"), day="2026-10-11", memo="credit applied", party="Customer:4",
                           bank="", total=0, links=(("Invoice:12", 500),), credits=(("CreditMemo:20", 500),))
    fake.records["documents"] = [_invoice(12, 1, 2000), credit]
    fake.records["payments"] = [applied]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    fake.changes = [M.Settlement(M.Ext("Payment", "30", "2"), day="2026-10-11", memo="now cash", party="Customer:4",
                                 bank="Account:1", total=500, links=(("Invoice:12", 500),))]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "partial", 500)
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (inv,)
                         ).fetchone()[0] == 1
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "1000"),)).fetchone()[0] \
            == 500
    fake.changes = [M.Ext("Payment", "30", "3", True)]
    assert _drain(books, fake, cid, now="2026-10-15T14:00:00.000000Z") == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "sent", 0)
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "1000"),)).fetchone()[0] \
            == 0


def test_an_invoice_revised_here_keeps_the_credit_applied_to_it_and_deleting_the_application_clears_it(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    credit = M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                        total=500, lines=(M.EntryLine("Account:3", 500),))
    applied = M.Settlement(M.Ext("Payment", "30", "1"), day="2026-10-11", memo="credit applied", party="Customer:4",
                           bank="", total=0, links=(("Invoice:12", 500),), credits=(("CreditMemo:20", 500),))
    fake.records["documents"] = [_invoice(12, 1, 2000), credit]
    fake.records["payments"] = [applied]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        app = c.execute("SELECT local_id FROM fin_external_refs WHERE external_id='Payment:30' "
                        "AND local_table='fin_settlements'").fetchone()[0]
    fake.changes = [M.Document(M.Ext("Invoice", "12", "2"), party="Customer:4", number="I-12", day="2026-10-05",
                               due="2026-11-05", total=2000, lines=(M.EntryLine("Account:3", 2000),), memo="revised")]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert c.execute("SELECT local_id FROM fin_external_refs WHERE external_id='Payment:30' "
                         "AND local_table='fin_settlements'").fetchone()[0] == app
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()[0] == 500
    fake.changes = [M.Ext("Payment", "30", "2", True)]
    assert _drain(books, fake, cid, now="2026-10-15T14:00:00.000000Z") == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "sent", 0)


def test_a_credit_memo_revised_here_keeps_the_credit_applied_to_its_invoice(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    credit = M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                        total=500, lines=(M.EntryLine("Account:3", 500),))
    applied = M.Settlement(M.Ext("Payment", "30", "1"), day="2026-10-11", memo="credit applied", party="Customer:4",
                           bank="", total=0, links=(("Invoice:12", 500),), credits=(("CreditMemo:20", 500),))
    fake.records["documents"] = [_invoice(12, 1, 2000), credit]
    fake.records["payments"] = [applied]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    fake.changes = [M.Document(M.Ext("CreditMemo", "20", "2"), party="Customer:4", number="CM-20", day="2026-10-10",
                               due="", total=500, lines=(M.EntryLine("Account:3", 500),), memo="revised")]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "partial", 500)
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (inv,)
                         ).fetchone()[0] == 1
        # Invoice -2000 and credit memo +500: a reversal left undone would show -2000 or -1000 instead.
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "4000"),)).fetchone()[0] \
            == -1500


def test_a_write_the_provider_could_not_rule_out_still_blocks_its_bank_row_from_being_written_again(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
        assert done.status == "committed", done
    fake.replies = [mirror.Sent(False, error="UNKNOWN: the entry may have landed", final=True, unknown=True)]
    assert _drain(books, fake, cid, now=LATER) == ["journal.create: UNKNOWN: the entry may have landed"]
    with books.transaction() as c:
        again = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
    assert again.status != "committed"
    assert _drain(books, fake, cid, now="2026-10-15T14:00:00.000000Z") == []
    assert [op for op, _, _ in fake.sent].count("journal.create") == 1


def test_a_payment_of_cash_and_credit_posts_the_cash_to_the_bank_and_settles_both_parts(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [
        _invoice(12, 1, 10000),
        M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                   total=2000, lines=(M.EntryLine("Account:3", 2000),))]
    fake.records["payments"] = [M.Settlement(M.Ext("Payment", "40", "1"), day="2026-10-12", memo="mixed",
                                             party="Customer:4", bank="Account:1", total=8000,
                                             links=(("Invoice:12", 10000),), credits=(("CreditMemo:20", 2000),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 10000)
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "1000"),)).fetchone()[0] \
            == 8000
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (inv,)
                         ).fetchone()[0] == 2


def test_a_bank_row_re_dated_while_its_entry_is_out_is_confirmed_on_its_current_date(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        done = act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}})
        assert done.status == "committed", done
        c.execute("UPDATE fin_feed_txns SET txn_date='2026-11-05' WHERE id=?", (fid,))
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT e.entry_date FROM fin_entries e JOIN fin_feed_txns f ON f.entry_id=e.id WHERE f.id=?",
                         (fid,)).fetchone()[0] == "2026-11-05"


def test_a_tag_on_a_bill_line_survives_an_amount_edit_to_the_same_provider_line(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [M.Document(M.Ext("Bill", "77", "1"), party="Vendor:9", number="B-77",
                                            day="2026-10-05", due="2026-11-05", total=1500,
                                            lines=(M.EntryLine("Account:2", 1500, description="seats", key="7"),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        bill = _local(c, "Bill:77", "fin_bills")
        line = c.execute("SELECT id FROM fin_bill_lines WHERE bill_id=?", (bill,)).fetchone()[0]
        SAP.set_line_tags(c, "bill", line, {"team": "eng"})
    fake.changes = [M.Document(M.Ext("Bill", "77", "2"), party="Vendor:9", number="B-77", day="2026-10-05",
                               due="2026-11-05", total=1600,
                               lines=(M.EntryLine("Account:2", 1600, description="seats", key="7"),))]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        tags = [json.loads(row[0]) for row in c.execute("SELECT tags_json FROM fin_bill_lines WHERE bill_id=?", (bill,))]
        assert tags == [{"team": "eng"}]


def test_a_tag_on_a_credit_memo_line_survives_an_amount_edit_to_the_same_provider_line(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20",
                                            day="2026-10-10", due="", total=500,
                                            lines=(M.EntryLine("Account:3", 500, description="consulting", key="7"),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        line = c.execute("SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?",
                         (_entry_of(c, "CreditMemo:20"), account(c, "4000"))).fetchone()[0]
        done = act(c, OWNER, "categorize", {"lines": [{"line_id": line, "tags": {"team": "web"}}]})
        assert done.status == "committed", done
    fake.changes = [M.Document(M.Ext("CreditMemo", "20", "2"), party="Customer:4", number="CM-20", day="2026-10-10",
                               due="", total=600,
                               lines=(M.EntryLine("Account:3", 600, description="consulting", key="7"),))]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert _line_tags(c, _entry_of(c, "CreditMemo:20"), account(c, "4000")) == ["web"]


def test_a_tag_on_a_journal_line_survives_an_amount_edit_to_the_same_provider_line(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["entries"] = [M.Journal(M.Ext("JournalEntry", "50", "1"), "2026-10-05", "Acme Software", "sync",
                                         (M.EntryLine("Account:2", 100, key="71"),
                                          M.EntryLine("Account:1", -100, key="72")))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        line = c.execute("SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?",
                         (_entry_of(c, "JournalEntry:50"), _local(c, "Account:2", "fin_accounts"))).fetchone()[0]
        done = act(c, OWNER, "categorize", {"lines": [{"line_id": line, "tags": {"team": "web"}}]})
        assert done.status == "committed", done
    fake.changes = [M.Journal(M.Ext("JournalEntry", "50", "2"), "2026-10-05", "Acme Software", "sync",
                              (M.EntryLine("Account:2", 150, key="71"), M.EntryLine("Account:1", -150, key="72")))]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert _line_tags(c, _entry_of(c, "JournalEntry:50"), _local(c, "Account:2", "fin_accounts")) == ["web"]


def _deposit(total: str, token: str) -> dict[str, Any]:
    """A QuickBooks deposit of one line on account 2 into account 1, the line keeping its provider id 7."""
    return {"Id": "70", "SyncToken": token, "TxnDate": "2026-10-05", "TotalAmt": total,
            "DepositToAccountRef": {"value": "1"},
            "Line": [{"Id": "7", "Amount": total, "DetailType": "DepositLineDetail",
                      "DepositLineDetail": {"AccountRef": {"value": "2"}}}]}


def test_a_tag_on_a_deposit_line_survives_an_amount_edit_to_the_same_provider_line(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["entries"] = [Q.records("Deposit", [_deposit("15.00", "0")], {})[0]]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        line = c.execute("SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?",
                         (_entry_of(c, "Deposit:70"), _local(c, "Account:2", "fin_accounts"))).fetchone()[0]
        done = act(c, OWNER, "categorize", {"lines": [{"line_id": line, "tags": {"team": "web"}}]})
        assert done.status == "committed", done
    fake.changes = [Q.records("Deposit", [_deposit("20.00", "1")], {})[0]]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert _line_tags(c, _entry_of(c, "Deposit:70"), _local(c, "Account:2", "fin_accounts")) == ["web"]


def test_phase_four_adds_the_line_key_to_an_install_made_before_it_and_running_it_again_changes_nothing(books):
    with books.transaction() as c:
        c.execute("ALTER TABLE fin_lines DROP COLUMN external_line")
        schema.phase4(c)
        schema.phase4(c)
        assert "external_line" in {str(r[1]) for r in c.execute("PRAGMA table_info(fin_lines)")}


def test_a_bill_posts_to_the_payable_account_the_provider_names(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["accounts"].append(M.ChartAccount(M.Ext("Account", "33", "1"), "QB33", "Accounts Payable",
                                                   "liability", "payable"))
    fake.records["documents"] = [M.Document(M.Ext("Bill", "77", "1"), party="Vendor:9", number="B-77",
                                            day="2026-10-05", due="2026-11-05", total=1500,
                                            lines=(M.EntryLine("Account:2", 1500, description="seats"),),
                                            control="Account:33")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        payable = _local(c, "Account:33", "fin_accounts")
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (payable,)).fetchone()[0] == -1500


def test_an_inactive_account_takes_in_the_entries_it_made_before_it_was_retired(books):
    fake = FakeBooks()
    fake.records["accounts"] = [acct(1, "1000", "Bank", "asset"),
                                M.ChartAccount(M.Ext("Account", "2", "1"), "6100", "Software", "expense", "",
                                               active=False)]
    fake.records["entries"] = [journal(50, 1, "2026-10-05", 100)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='JournalEntry:50'").fetchone()[0] \
            == "active"
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE status='unmapped'").fetchone()[0] == 0


def test_a_record_in_another_currency_is_not_taken_in_and_waits_as_unmapped(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [M.Document(M.Ext("Invoice", "12", "1", currency="EUR"), party="Customer:4",
                                            number="I-12", day="2026-10-05", due="2026-11-05", total=2000,
                                            lines=(M.EntryLine("Account:3", 2000),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_invoices").fetchone()[0] == 0
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Invoice:12'").fetchone()[0] == "unmapped"


def test_the_outbox_counts_pending_failed_unknown_and_withheld_apart(books):
    fake = FakeBooks()
    _chart(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        for n, status, unknown in (("a", "pending", 0), ("b", "failed", 1), ("c", "failed", 0), ("d", "withheld", 0)):
            key = f"bill.create:count-{n}"
            SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload={}, key=key, by=OWNER, at=AT)
            c.execute("UPDATE fin_outbox SET status=?, unknown=? WHERE idempotency_key=?", (status, unknown, key))
    with books.read() as c:
        assert SX.outbox_counts(c, cid) == {"pending": 1, "failed": 1, "unknown": 1, "withheld": 1}


def test_deleting_a_credit_application_leaves_the_cash_view_as_it_was(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    credit = M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                        total=500, lines=(M.EntryLine("Account:3", 500),))
    applied = M.Settlement(M.Ext("Payment", "30", "1"), day="2026-10-11", memo="credit applied", party="Customer:4",
                           bank="", total=0, links=(("Invoice:12", 500),), credits=(("CreditMemo:20", 500),))
    fake.records["documents"] = [_invoice(12, 1, 2000), credit]
    fake.records["payments"] = [applied]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        cash_applied = [tuple(r) for r in c.execute("SELECT * FROM fin_cash_rollups ORDER BY 1, 2, 3, 4")]
    # The credit memo is unchanged; only its application goes, which moves no money and so no cash.
    fake.changes = [M.Ext("Payment", "30", "2", True)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) \
            == ("sent", 0)
        assert [tuple(r) for r in c.execute("SELECT * FROM fin_cash_rollups ORDER BY 1, 2, 3, 4")] == cash_applied


def test_a_parked_refund_keeps_the_credit_it_refunds_when_it_is_decoded_again():
    refund = M.Journal(M.Ext("Payment", "ARCREDITPAYMENT:pay-1", "1"), "2026-10-12", "refund", "sync",
                       (M.EntryLine("Account:1", 4000), M.EntryLine("Account:9", -4000)), refund_of="CreditNote:cn-1")
    assert M.decode(M.encode(refund)).refund_of == "CreditNote:cn-1"


def test_a_payment_clears_the_payable_account_its_bill_posted_against(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["accounts"].append(M.ChartAccount(M.Ext("Account", "33", "1"), "QB33", "Accounts Payable",
                                                   "liability", "payable"))
    fake.records["documents"] = [M.Document(M.Ext("Bill", "12", "1"), party="Vendor:9", number="B-12",
                                            day="2026-10-05", due="2026-11-05", total=1500,
                                            lines=(M.EntryLine("Account:2", 1500, description="seats"),),
                                            control="Account:33")]
    fake.records["payments"] = [M.Settlement(M.Ext("BillPayment", "30", "1"), day="2026-10-12", memo="Acme payment",
                                             party="Vendor:9", bank="Account:1", total=1500,
                                             links=(("Bill:12", 1500),))]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        payable = _local(c, "Account:33", "fin_accounts")
        assert c.execute("SELECT status FROM fin_bills WHERE number='B-12'").fetchone()[0] == "paid"
        # The bill credited its own payable account, so the payment debits that one; the default is left alone.
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (payable,)).fetchone()[0] == 0
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_lines WHERE account_id=?",
                         (account(c, "2000"),)).fetchone()[0] == 0


def test_a_bill_recovered_from_its_lost_answer_keeps_the_tags_its_local_write_carried(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    key = "bill.create:adopt-tags"
    with books.transaction() as c:
        body = {"party": _local(c, "Vendor:9", "fin_contacts"), "number": "B-77", "day": "2026-10-05",
                "due": "2026-11-05", "memo": "", "lines": [{"account_id": _local(c, "Account:2", "fin_accounts"),
                                                            "amount": 1500, "description": "seats",
                                                            "tags": {"team": "eng"}}]}
        SK.outbox_add(c, connection_id=cid, action_id="", op="bill.create", payload=body, key=key, by=OWNER, at=AT)
    fake.changes = [M.Document(M.Ext("Bill", "77", "0", reference=M.reference(key)), party="Vendor:9", number="B-77",
                               day="2026-10-05", due="2026-11-05", total=1500,
                               lines=(M.EntryLine("Account:2", 1500, description="seats"),))]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        entry = c.execute("SELECT entry_id FROM fin_bills WHERE number='B-77'").fetchone()[0]
        # The provider's record carries no tags; the tags the local write named stay on its line.
        assert _line_tags(c, entry, _local(c, "Account:2", "fin_accounts")) == ["eng"]



def _bank_rows(c: sqlite3.Connection, code: str, name: str, rows: list[tuple[int, str]]) -> None:
    """One bank account on the chart line `code`, and a new bank row on it for each (amount, day): what the bank feed
    brings in."""
    money = SL.new_id()
    book = SL.book(c)
    c.execute("INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?,?)", (money, book.id, "bank", name, "mercury", account(c, code), AT, OWNER))  # type: ignore[union-attr]
    for amount, day in rows:
        fid = SL.new_id()
        c.execute("INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
                  "status, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (fid, money, f"txn-{fid[:8]}", day, amount, book.base_currency, name, "new", AT, OWNER))  # type: ignore[union-attr]


def _bank_on(c: sqlite3.Connection, code: str, amount: int, day: str, name: str) -> None:
    """A bank account on the chart line `code`, and one new bank row on it (what the bank feed brings in)."""
    _bank_rows(c, code, name, [(amount, day)])


def test_a_payroll_expense_account_is_private_when_it_is_imported_and_so_is_its_salary_line(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["accounts"].append(M.ChartAccount(M.Ext("Account", "7", "1"), "6200", "Payroll expenses", "expense",
                                                   payroll=True))
    fake.records["entries"] = [journal(71, 1, "2026-10-05", 123456, debit="Account:7", credit="Account:1")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        payroll = _local(c, "Account:7", "fin_accounts")
        private = SA.private_lines(c)
        assert payroll in private and private[payroll].others_see == "totals"
        entry = _entry_of(c, "JournalEntry:71")
        assert c.execute("SELECT amount FROM fin_lines WHERE entry_id=? AND account_id=?", (entry, payroll)
                         ).fetchone()[0] == 123456


def test_a_teammate_who_cannot_see_a_bill_is_refused_as_for_a_missing_bill_and_learns_nothing_of_it(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    fake.records["documents"] = [_bill(12, 1, 1500)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        bill = _local(c, "Bill:12", "fin_bills")
        bank = account(c, "1000")
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.ap"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "bs_lines",
                                            "account_ids": [bank]}).status == "committed"
        hidden = act(c, "human:dan", "bill_mark_paid", {"bill_id": bill, "account_id": bank})
        missing = act(c, "human:dan", "bill_mark_paid", {"bill_id": "no-such-bill", "account_id": bank})
        assert (hidden.status, hidden.code, hidden.reason) == (missing.status, missing.code, missing.reason)
        assert (hidden.status, hidden.code) == ("failed", "not_found")
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='payment.bill'").fetchone()[0] == 0


def test_a_zero_amount_is_refused_for_a_mirrored_payment_and_write_off_and_never_means_the_balance(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [_bill(12, 1, 1500), _invoice(13, 1, 10000)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        bill, inv, bank = _local(c, "Bill:12", "fin_bills"), _local(c, "Invoice:13", "fin_invoices"), account(c, "1000")
        for kind, payload in (("bill_mark_paid", {"bill_id": bill, "amount": 0, "account_id": bank}),
                              ("bill_mark_paid", {"bill_id": bill, "amount": -500, "account_id": bank}),
                              ("invoice_record_payment", {"invoice_id": inv, "amount": 0, "account_id": bank}),
                              ("invoice_write_off", {"invoice_id": inv, "amount": 0, "account_id": account(c, "6100")})):
            refused = act(c, OWNER, kind, payload)
            assert (refused.status, refused.code) == ("failed", "invalid"), (kind, refused)
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op LIKE 'payment.%' OR op LIKE 'writeoff.%'"
                         ).fetchone()[0] == 0
        assert act(c, OWNER, "bill_mark_paid", {"bill_id": bill, "account_id": bank}).status == "committed"


def test_a_deny_rule_on_a_team_tag_stops_a_bill_made_here_in_mirror_mode_as_it_does_natively(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        rule = act(c, OWNER, "rule_create", {
            "name": "No eng bills", "when": {"kind": "bill_create", "tags.team": "eng"},
            "outcome": {"outcome": "deny", "reason": "eng bills wait"},
            "examples": [{"input": {"kind": "bill_create", "tags.team": "eng"}, "expect": "deny"},
                         {"input": {"kind": "bill_create", "tags.team": "web"}, "expect": "allow"}]})
        assert rule.status == "committed", rule
        refused = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-78", "bill_date": "2026-10-05",
            "due_date": "2026-11-05", "lines": [{"account_id": account(c, "6100"), "amount": 1500,
                                                 "description": "seats", "tags": {"team": "eng"}}]})
        assert (refused.status, refused.reason) == ("denied", "eng bills wait")
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='bill.create'").fetchone()[0] == 0


def test_both_legs_of_an_imported_transfer_match_the_one_entry_and_neither_blocks_the_other(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["accounts"].append(acct(4, "1150", "Savings", "asset"))
    fake.records["entries"] = [journal(70, 1, "2026-10-05", 2500, debit="Account:4", credit="Account:1")]
    cid = _connection(books)
    with books.transaction() as c:
        assert act(c, OWNER, "account_create", {"code": "1150", "name": "Savings", "type": "asset"}).status == "committed"
        _bank_on(c, "1000", -2500, "2026-10-05", "Checking")
        _bank_on(c, "1150", 2500, "2026-10-05", "Savings")
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        entry = _entry_of(c, "JournalEntry:70")
        rows = c.execute("SELECT status, entry_id FROM fin_feed_txns ORDER BY amount").fetchall()
        assert [tuple(r) for r in rows] == [("matched", entry), ("matched", entry)]
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == 0


def test_wages_paid_through_a_quickbooks_payroll_liability_hide_their_bank_row_from_a_bank_line_viewer(books):
    fake = FakeBooks()
    _chart(fake)
    base = {"SyncToken": "1", "Active": True}
    fake.records["accounts"] += [
        Q.account_record({**base, "Id": "7", "Name": "Wages", "AcctNum": "6200", "AccountType": "Expense",
                          "AccountSubType": "PayrollExpenses"}),
        Q.account_record({**base, "Id": "8", "Name": "Wages payable", "AcctNum": "2150",
                          "AccountType": "Other Current Liability", "AccountSubType": "PayrollClearing"})]
    fake.records["entries"] = [journal(71, 1, "2026-10-05", 123456, debit="Account:7", credit="Account:8"),
                               journal(72, 1, "2026-10-12", 123456, debit="Account:8", credit="Account:1")]
    cid = _connection(books)
    with books.transaction() as c:
        _bank_on(c, "1000", -123456, "2026-10-12", "Gusto")
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        bank = account(c, "1000")
        assert act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "bs_lines",
                                            "account_ids": [bank]}).status == "committed"
    with books.read() as c:
        def amounts(who: str) -> list[int]:
            return [int(r["amount"]) for r in FR.transactions(c, who, at=AT)["rows"] if r["kind"] == "feed"]
        assert -123456 in amounts(OWNER)
        assert -123456 not in amounts("human:cara")
        assert c.execute("SELECT status FROM fin_feed_txns").fetchone()[0] == "matched"


def _pay_run(books: Any) -> str:
    """Wages (6200) accrue to payroll clearing (2150) and clear to the bank (1000) through a QuickBooks import."""
    fake = FakeBooks()
    _chart(fake)
    base = {"SyncToken": "1", "Active": True}
    fake.records["accounts"] += [
        Q.account_record({**base, "Id": "7", "Name": "Wages", "AcctNum": "6200", "AccountType": "Expense",
                          "AccountSubType": "PayrollExpenses"}),
        Q.account_record({**base, "Id": "8", "Name": "Wages payable", "AcctNum": "2150",
                          "AccountType": "Other Current Liability", "AccountSubType": "PayrollClearing"})]
    fake.records["entries"] = [journal(71, 1, "2026-10-05", 123456, debit="Account:7", credit="Account:8"),
                               journal(72, 1, "2026-10-12", 123456, debit="Account:8", credit="Account:1")]
    cid = _connection(books)
    with books.transaction() as c:
        _bank_on(c, "1000", -123456, "2026-10-12", "Gusto")
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    return cid


def test_a_payroll_liability_balance_shows_on_the_sheet_but_its_entries_do_not_without_detail(books):
    _pay_run(books)
    with books.transaction() as c:
        assert act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "balance_sheet"}).status == "committed"
    with books.read() as c:
        def line(who: str) -> dict[str, Any]:
            sheet = FR.balance_sheet(c, who, as_of="2026-10-06", at=AT)
            return next(r for kind in ("assets", "liabilities", "equity") for r in sheet[kind] if r["code"] == "2150")
        owner, cara = line(OWNER), line("human:cara")
        assert owner["balance"] == cara["balance"] == 123456     # a balance sheet grant sees whole balances (§7.5)
        assert owner["entry_sources"] and cara["entry_sources"] == [] and cara["sources"] == []   # no line detail

def test_an_accountant_granted_full_detail_on_wages_and_clearing_sees_the_clearing_entries(books):
    _pay_run(books)
    with books.transaction() as c:
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "balance_sheet"}).status == "committed"
        bank = act(c, OWNER, "private_lines_set", {"account_id": account(c, "1000"), "private": True})
        assert (bank.status, bank.code) == ("failed", "invalid")       # bank accounts keep their fixed rules
    with books.read() as c:
        assert FR.line_detail(c, "human:dan", account(c, "2150"), "2026-10-01", "2026-10-31", at=AT)["entries"] == []
    with books.transaction() as c:
        for code in ("6200", "2150"):
            granted = act(c, OWNER, "private_lines_set", {"account_id": account(c, code), "private": True,
                                                           "others_see": "totals", "full_detail": ["human:dan"]})
            assert granted.status == "committed", granted.reason
    with books.read() as c:
        entries = FR.line_detail(c, "human:dan", account(c, "2150"), "2026-10-01", "2026-10-31", at=AT)["entries"]
        assert sorted(int(e["amount"]) for e in entries) == [-123456, 123456]


def test_a_card_account_stays_refused_as_private_even_on_a_liability_line(books):
    _pay_run(books)
    with books.transaction() as c:
        book_id = SL.book(c).id  # type: ignore[union-attr]
        c.execute("INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, "
                  "created_by) VALUES(?,?,?,?,?,?,?,?)",
                  (SL.new_id(), book_id, "card_program", "Card", "mercury", account(c, "2150"), AT, OWNER))
        refused = act(c, OWNER, "private_lines_set", {"account_id": account(c, "2150"), "private": True})
        assert (refused.status, refused.code) == ("failed", "invalid")   # a card's line follows its fixed rules


_TEAM_RULE = {
    "name": "No eng bills", "when": {"kind": "bill_create", "tags.team": "eng"},
    "outcome": {"outcome": "deny", "reason": "eng bills wait"},
    "examples": [{"input": {"kind": "bill_create", "tags.team": "eng"}, "expect": "deny"},
                 {"input": {"kind": "bill_create", "tags.team": "web"}, "expect": "allow"}]}


def _two_team_lines(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """A bill whose first line is web and whose second is eng: a rule on eng must see the second line too."""
    return [{"account_id": account(c, "6100"), "amount": 1000, "description": "web seats", "tags": {"team": "web"}},
            {"account_id": account(c, "6100"), "amount": 500, "description": "eng seats", "tags": {"team": "eng"}}]


def test_a_deny_rule_on_a_team_tag_stops_a_bill_with_any_line_on_that_team_in_mirror_mode(books):
    fake = FakeBooks()
    _chart(fake)
    _vendor(fake)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        assert act(c, OWNER, "rule_create", _TEAM_RULE).status == "committed"
        refused = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Vendor:9", "fin_contacts"), "number": "B-79", "bill_date": "2026-10-05",
            "due_date": "2026-11-05", "lines": _two_team_lines(c)})
        assert (refused.status, refused.reason) == ("denied", "eng bills wait")
        assert c.execute("SELECT COUNT(*) FROM fin_outbox WHERE op='bill.create'").fetchone()[0] == 0


def test_a_deny_rule_on_a_team_tag_stops_a_native_bill_with_any_line_on_that_team(books):
    with books.transaction() as c:
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Parts", "kind": "vendor"}).result["contact_id"])
        assert act(c, OWNER, "rule_create", _TEAM_RULE).status == "committed"
        refused = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-05",
                                                "due_date": "2026-11-05", "lines": _two_team_lines(c)})
        assert (refused.status, refused.reason) == ("denied", "eng bills wait")


def test_a_reimport_does_not_make_an_account_private_again_once_the_owner_has_made_it_public(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["accounts"].append(M.ChartAccount(M.Ext("Account", "7", "1"), "6200", "Wages", "expense",
                                                   payroll=True))
    fake.records["entries"] = [journal(71, 1, "2026-10-05", 123456, debit="Account:7", credit="Account:1")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.transaction() as c:
        wages = _local(c, "Account:7", "fin_accounts")
        assert act(c, OWNER, "private_lines_set", {"account_id": wages, "private": False}).status == "committed"
    fake.changes = [M.ChartAccount(M.Ext("Account", "7", "2"), "6200", "Wages and salaries", "expense", payroll=True)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert c.execute("SELECT name FROM fin_accounts WHERE id=?", (wages,)).fetchone()[0] == "Wages and salaries"
        assert wages not in SA.private_lines(c)


def test_two_transfers_on_one_account_in_one_journal_each_match_their_own_leg_and_a_second_pass_keeps_them(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["accounts"].append(acct(4, "1150", "Savings", "asset"))
    fake.records["entries"] = [M.Journal(M.Ext("JournalEntry", "80", "1"), "2026-10-05", "Acme Software", "sync",
                                         (M.EntryLine("Account:4", 2500), M.EntryLine("Account:1", -2500),
                                          M.EntryLine("Account:4", 3000), M.EntryLine("Account:1", -3000)))]
    cid = _connection(books)
    with books.transaction() as c:
        assert act(c, OWNER, "account_create", {"code": "1150", "name": "Savings", "type": "asset"}).status == "committed"
        _bank_rows(c, "1000", "Checking", [(-2500, "2026-10-05"), (-3000, "2026-10-05")])
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        entry = _entry_of(c, "JournalEntry:80")
        rows = c.execute("SELECT amount, status, entry_id FROM fin_feed_txns ORDER BY amount").fetchall()
        assert [tuple(r) for r in rows] == [(-3000, "matched", entry), (-2500, "matched", entry)]
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == 0


def test_migration_75_gives_a_row_matched_before_lines_were_kept_its_line_and_running_it_again_changes_nothing(books):
    with books.transaction() as c:
        entry = act(c, OWNER, "journal", {"date": "2026-10-05", "memo": "rent", "lines": [
            {"account_id": account(c, "6100"), "amount": 1500}, {"account_id": account(c, "1000"), "amount": -1500}]})
        assert entry.status == "committed", entry
        _bank_on(c, "1000", -1500, "2026-10-05", "Acme Software")
        row = str(c.execute("SELECT id FROM fin_feed_txns").fetchone()[0])
        line = str(c.execute("SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?",
                             (entry.result["entry_id"], account(c, "1000"))).fetchone()[0])
        c.execute("UPDATE fin_feed_txns SET status='matched', entry_id=? WHERE id=?", (entry.result["entry_id"], row))
        c.execute("DROP INDEX fin_feed_txns_entry_line")
        c.execute("ALTER TABLE fin_feed_txns DROP COLUMN entry_line_id")
        schema.feed_lines(c)
        schema.feed_lines(c)
        assert c.execute("SELECT entry_line_id FROM fin_feed_txns WHERE id=?", (row,)).fetchone()[0] == line


def test_a_revised_transfer_keeps_each_bank_row_on_its_own_leg_of_the_new_entry(books):
    fake = FakeBooks()
    _chart(fake)
    fake.records["accounts"].append(acct(4, "1150", "Savings", "asset"))

    def transfers(rev: int) -> M.Journal:
        return M.Journal(M.Ext("JournalEntry", "80", str(rev)), "2026-10-05", "Acme Software", "sync",
                         (M.EntryLine("Account:4", 2500), M.EntryLine("Account:1", -2500),
                          M.EntryLine("Account:4", 3000), M.EntryLine("Account:1", -3000)))
    fake.records["entries"] = [transfers(1)]
    cid = _connection(books)
    with books.transaction() as c:
        made = act(c, OWNER, "account_create", {"code": "1150", "name": "Savings", "type": "asset"})
        assert made.status == "committed"
        _bank_rows(c, "1000", "Checking", [(-2500, "2026-10-05"), (-3000, "2026-10-05")])
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    fake.changes = [transfers(2)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        new = _entry_of(c, "JournalEntry:80")
        checking = _local(c, "Account:1", "fin_accounts")
        leg = {int(r[1]): str(r[0]) for r in c.execute(
            "SELECT id, amount FROM fin_lines WHERE entry_id=? AND account_id=?", (new, checking))}
        rows = [tuple(r) for r in c.execute("SELECT amount, status, entry_id, entry_line_id FROM fin_feed_txns "
                                            "ORDER BY amount").fetchall()]
        assert rows == [(-3000, "matched", new, leg[-3000]), (-2500, "matched", new, leg[-2500])]
        assert c.execute("SELECT COUNT(*) FROM fin_outbox").fetchone()[0] == 0


def test_a_revised_journal_with_two_equal_legs_gives_each_bank_row_its_own_replacement_leg(books):
    fake = FakeBooks()
    _chart(fake)

    def pair(rev: int) -> M.Journal:
        return M.Journal(M.Ext("JournalEntry", "81", str(rev)), "2026-10-05", "Acme Software", "sync",
                         (M.EntryLine("Account:2", 2500), M.EntryLine("Account:1", -2500),
                          M.EntryLine("Account:2", 2500), M.EntryLine("Account:1", -2500)))
    fake.records["entries"] = [pair(1)]
    cid = _connection(books)
    with books.transaction() as c:
        _bank_rows(c, "1000", "Checking", [(-2500, "2026-10-05"), (-2500, "2026-10-05"), (-2500, "2026-10-05")])
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    fake.changes = [pair(2)]
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        new = _entry_of(c, "JournalEntry:81")
        checking = _local(c, "Account:1", "fin_accounts")
        legs = sorted(str(r[0]) for r in c.execute(
            "SELECT id FROM fin_lines WHERE entry_id=? AND account_id=?", (new, checking)).fetchall())
        matched = sorted(str(r[0]) for r in c.execute(
            "SELECT entry_line_id FROM fin_feed_txns WHERE status='matched'").fetchall())
        assert len(legs) == 2 and matched == legs                         # each row on its own leg, none shared
        assert [str(r[0]) for r in c.execute("SELECT status FROM fin_feed_txns WHERE status='new'").fetchall()] == ["new"]


def _invoice_mirror(books: Any, total: int) -> tuple[FakeBooks, str, str]:
    """A mirrored book holding one invoice to Customer:4, sent to the provider; its connection and local id."""
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    fake.records["documents"] = [_invoice(12, 1, total)]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, fake, cid) == []
    with books.read() as c:
        inv = _local(c, "Invoice:12", "fin_invoices")
    return fake, cid, inv


def test_two_payment_requests_before_a_sync_cannot_both_take_the_same_invoice_balance(books):
    fake, cid, inv = _invoice_mirror(books, 2000)
    with books.transaction() as c:
        first = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")})
        second = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")})
    assert first.status == "committed"
    assert (second.status, second.code) == ("failed", "invalid")
    assert _drain(books, fake, cid, now=LATER) == []
    assert [body["total"] for name, body, _ in fake.sent if name == "payment.invoice"] == [2000]
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)


def test_two_partial_payments_before_a_sync_cannot_take_more_than_the_invoice_owes(books):
    fake, cid, inv = _invoice_mirror(books, 2000)
    with books.transaction() as c:
        bank = account(c, "1000")
        first = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": bank, "amount": 1200})
        over = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": bank, "amount": 1200})
        rest = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": bank, "amount": 800})
    assert [first.status, over.status, rest.status] == ["committed", "failed", "committed"]
    assert over.code == "invalid"
    assert _drain(books, fake, cid, now=LATER) == []
    assert sorted(body["total"] for name, body, _ in fake.sent if name == "payment.invoice") == [800, 1200]
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)


def test_a_write_off_holds_its_amount_against_a_payment_requested_before_it_is_sent(books):
    fake, cid, inv = _invoice_mirror(books, 2000)
    with books.transaction() as c:
        off = act(c, OWNER, "invoice_write_off", {"invoice_id": inv, "amount": 1200, "account_id": account(c, "6100")})
        over = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000"),
                                                        "amount": 1000})
        rest = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000"),
                                                        "amount": 800})
    assert [off.status, over.status, rest.status] == ["committed", "failed", "committed"]
    assert over.code == "invalid"


def test_a_queued_payment_the_balance_no_longer_covers_when_it_goes_out_is_refused_and_never_sent(books):
    fake, cid, inv = _invoice_mirror(books, 2000)
    with books.transaction() as c:
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")}
                   ).status == "committed"
    with books.transaction() as c:      # a sync takes the balance meanwhile
        SAP.update(c, "fin_invoices", inv, {"amount_paid": 2000, "status": "paid"})
    assert _drain(books, fake, cid, now=LATER) == []
    assert [name for name, _, _ in fake.sent if name == "payment.invoice"] == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, unknown FROM fin_outbox WHERE op='payment.invoice'").fetchone()) == (
            "failed", 0)


def test_a_payment_the_provider_refused_gives_its_amount_back(books):
    fake, cid, inv = _invoice_mirror(books, 2000)
    with books.transaction() as c:
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")}
                   ).status == "committed"
    fake.replies.append(mirror.Sent(False, final=True, error="the provider refused the payment"))
    assert _drain(books, fake, cid, now=LATER) == ["payment.invoice: the provider refused the payment"]
    with books.transaction() as c:
        again = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")})
    assert again.status == "committed"
    assert _drain(books, fake, cid, now=LATER) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)


def test_a_payment_the_provider_could_not_rule_out_keeps_its_amount_held(books):
    fake, cid, inv = _invoice_mirror(books, 2000)
    with books.transaction() as c:
        assert act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")}
                   ).status == "committed"
    fake.replies.append(mirror.Sent(False, final=True, unknown=True, error="the provider did not answer"))
    assert _drain(books, fake, cid, now=LATER) == ["payment.invoice: the provider did not answer"]
    with books.transaction() as c:
        again = act(c, OWNER, "invoice_record_payment", {"invoice_id": inv, "account_id": account(c, "1000")})
    assert (again.status, again.code) == ("failed", "invalid")
    with books.read() as c:
        assert tuple(c.execute("SELECT status, unknown FROM fin_outbox WHERE op='payment.invoice'").fetchone()) == (
            "failed", 1)
