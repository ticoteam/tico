"""Mirror mode (spec §10): a provider's records come into the books by ext ref, Tico's writes wait for the provider's
confirmation, bank rows match the provider's entries, and a closed month's change is flagged."""
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.finance import mirror
from backend.finance.core import mirror as M
from backend.finance.core.errors import CONFLICT, NOT_AVAILABLE
from backend.finance.store import ledger as SL
from backend.finance.store import mirror as SX
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
        kind = {"account.create": "Account", "party.create": "Vendor", "bill.create": "Bill", "bill.void": "Bill",
                "invoice.create": "Invoice", "invoice.void": "Invoice", "payment.bill": "BillPayment",
                "payment.invoice": "Payment"}.get(op, "JournalEntry")
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
                                              {"account": "Account:2", "amount": 1500, "description": "seats"}]}
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


def test_a_credit_memo_applied_to_an_invoice_settles_it_and_deleting_it_puts_the_invoice_back_owing(books):
    fake = FakeBooks()
    _chart(fake)
    _customer(fake)
    credit = M.Document(M.Ext("CreditMemo", "20", "1"), party="Customer:4", number="CM-20", day="2026-10-10", due="",
                        total=500, lines=(M.EntryLine("Account:3", 500),), links=(("Invoice:12", 500),))
    fake.records["documents"] = [_invoice(12, 1, 2000), credit]
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
    fake.changes = [M.Ext("CreditMemo", "20", "2", True)]
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
        M.Journal(M.Ext("JournalEntry", "7", "1"), "2026-10-05", "memo", "sync", (M.EntryLine("Account:2", 10),)),
        M.Ext("Bill", "12", "4", True),
    ]
    for r in held:
        assert M.decode(json.loads(json.dumps(M.encode(r)))) == r
    assert M.decide(("2", "deleted"), M.Ext("Invoice", "901", "2")) == M.SAME    # our own void, echoed back
    assert M.decide(("2", "deleted"), M.Ext("Invoice", "901", "3")) == M.CREATE


def test_a_write_off_goes_out_as_a_credit_memo_that_closes_the_invoice_and_its_echo_adds_nothing(books):
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
    fake.replies.append(mirror.Sent(True, kind="CreditMemo", external_id="901", revision="1"))
    assert _drain(books, fake, cid, now=LATER) == []
    assert _sent(fake, "writeoff.create") == {"party": "Customer:4", "account": "Account:2", "day": "2026-10-12",
                                              "memo": "Uncollectible", "total": 2000,
                                              "links": [["Invoice:12", 2000]]}
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)
        settled = c.execute("SELECT from_kind, to_kind, amount FROM fin_settlements WHERE to_id=? AND voided_at IS NULL",
                            (inv,)).fetchall()
        assert [tuple(row) for row in settled] == [("credit", "invoice", 2000)]
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "6100"),)).fetchone()[0] == 2000
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='CreditMemo:901'").fetchone()[0] == "active"
    # The provider's echo of the credit memo it was sent: the same revision, so nothing is booked or settled again.
    fake.changes = [M.Document(M.Ext("CreditMemo", "901", "1"), party="Customer:4", number="", day="2026-10-12",
                               due="", total=2000, lines=(M.EntryLine("Account:2", 2000),), memo="Uncollectible",
                               links=(("Invoice:12", 2000),))]
    assert _drain(books, fake, cid, now="2026-10-15T17:00:00.000000Z") == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE to_id=? AND voided_at IS NULL", (inv,)
                         ).fetchone()[0] == 1
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE id=?", (inv,)).fetchone()) == (
            "paid", 2000)
