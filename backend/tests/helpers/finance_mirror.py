"""Helpers that other tests share, moved out of test_finance_mirror.py so they import no test file."""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any
from backend.finance import mirror
from backend.finance.core import mirror as M
from backend.finance.store import ledger as SL
from backend.tests.helpers.finance_fixtures import AT, OWNER, account


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
    found: dict[str, mirror.Sent] = field(default_factory=dict)  # by idempotency key: what the provider holds
    lookups: list[str] = field(default_factory=list)
    through: tuple[tuple[str, str], ...] = ()  # where each feed a read cut short is complete to
    feeds: tuple[str, ...] = (
        "Account",
        "Vendor",
        "Customer",
        "Bill",
        "Invoice",
        "CreditMemo",
        "BillPayment",
        "Payment",
        "JournalEntry",
    )
    full_reread: bool = False  # QuickBooks: a feed the window or the cap cut short is read again in full
    read_kinds: tuple[str, ...] = (
        "Account",
        "Vendor",
        "Customer",
        "Bill",
        "Invoice",
        "CreditMemo",
        "BillPayment",
        "Payment",
        "JournalEntry",
    )  # the kinds a full read covers (swept when not returned)

    def find(self, op: str, body: Any, key: str) -> mirror.Sent | None:
        self.lookups.append(key)
        return self.found.get(key)

    def page(self, entity: str, start: int, size: int) -> mirror.Read:
        if (entity, start) in self.fail:
            self.fail.discard((entity, start))
            return mirror.Read(False, error="the provider is down")
        return mirror.Read(True, tuple(self.records[entity][start - 1 : start - 1 + size]))

    def changed(self, since: Mapping[str, str]) -> mirror.Read:
        out, self.changes = tuple(self.changes), []
        return mirror.Read(True, out, through=self.through)

    def send(self, op: str, body: Any, key: str) -> mirror.Sent:
        self.sent.append((op, dict(body), key))
        if self.replies:
            return self.replies.pop(0)
        kind = {
            "account.create": "Account",
            "party.create": "Vendor",
            "bill.create": "Bill",
            "bill.void": "Bill",
            "invoice.create": "Invoice",
            "invoice.void": "Invoice",
            "payment.bill": "BillPayment",
            "purchaseorder.create": "PurchaseOrder",
            "estimate.create": "Estimate",
            "payment.invoice": "Payment",
            "writeoff.create": "CreditMemo",
            "credit.apply": "Payment",
        }.get(op, "JournalEntry")
        return mirror.Sent(True, kind=kind, external_id=str(900 + len(self.sent)), revision="1")


def acct(n: int, code: str, name: str, kind: str = "expense") -> M.ChartAccount:
    return M.ChartAccount(M.Ext("Account", str(n), "1"), code, name, kind)


def _connection(store: Any) -> str:
    cid = "conn-books"
    with store.transaction() as c:
        c.execute(
            "INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) VALUES(?,?,?,?,?,?)",
            (cid, "quickbooks", "books", AT, OWNER, "Acme books"),
        )
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
    return str(
        c.execute(
            "SELECT local_id FROM fin_external_refs WHERE external_id=? AND local_table=?", (ext, table)
        ).fetchone()[0]
    )


def _entry_of(c: sqlite3.Connection, ext: str) -> str:
    return str(
        c.execute(
            "SELECT local_id FROM fin_external_refs WHERE external_id=? AND local_table='fin_entries'", (ext,)
        ).fetchone()[0]
    )


def _bank_row(c: sqlite3.Connection, amount: int, day: str) -> tuple[str, str]:
    """A bank account on the 1000 line and one new row on it (what the bank feed brings in)."""
    money, fid = SL.new_id(), SL.new_id()
    book = SL.book(c)
    c.execute(
        "INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (money, book.id, "bank", "Checking", "mercury", account(c, "1000"), AT, OWNER),
    )  # type: ignore[union-attr]
    c.execute(
        "INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
        "status, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (fid, money, f"txn-{fid[:8]}", day, amount, book.base_currency, "Acme Software", "new", AT, OWNER),
    )  # type: ignore[union-attr]
    return money, fid


def _sent(fake: FakeBooks, op: str) -> dict[str, Any]:
    return next(body for name, body, _ in fake.sent if name == op)
