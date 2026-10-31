"""Mirror mode (spec §10): a provider's records come into the books by ext ref, Tico's writes wait for the provider's
confirmation, bank rows match the provider's entries, and a closed month's change is flagged."""
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.finance import mirror
from backend.finance.core import mirror as M
from backend.finance.core.errors import CONFLICT, NOT_AVAILABLE
from backend.finance.store import ledger as SL
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, account, act, books, finance_template, store)

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

    def page(self, entity: str, start: int, size: int) -> mirror.Read:
        if (entity, start) in self.fail:
            self.fail.discard((entity, start))
            return mirror.Read(False, error="the provider is down")
        return mirror.Read(True, tuple(self.records[entity][start - 1:start - 1 + size]))

    def changed(self, since: str) -> mirror.Read:
        out, self.changes = tuple(self.changes), []
        return mirror.Read(True, out)

    def send(self, op: str, body: Any, key: str) -> mirror.Sent:
        self.sent.append((op, dict(body), key))
        if self.replies:
            return self.replies.pop(0)
        kind = {"account.create": "Account", "party.create": "Vendor"}.get(op, "JournalEntry")
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
