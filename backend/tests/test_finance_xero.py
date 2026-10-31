"""Xero mirror (spec §10): the client and its records on the shared mirror core. The tests drive the real client through a
transport that answers in Xero's shapes, so paging, If-Modified-Since, Idempotency-Keys, 401 and 429 are the client's."""
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.finance import books_link, books_sync, mirror, xero
from backend.finance.core import ledger as L
from backend.finance.core import mirror as M
from backend.finance.core import xero as X
from backend.finance.store import ledger as SL
from backend.finance.store import mirror as SX
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, account, act, books, finance_template, store)

LATER = "2026-10-15T13:00:00.000000Z"
LATER2 = "2026-10-15T14:00:00.000000Z"
TENANT = "acme-tenant"
BASE_PATH = "/api.xro/2.0"
BASE = "https://api.xero.test" + BASE_PATH
ENDPOINTS = ("Accounts", "Contacts", "Invoices", "CreditNotes", "Payments", "ManualJournals", "BankTransactions")
IDS = {"Accounts": "AccountID", "Contacts": "ContactID", "Invoices": "InvoiceID", "CreditNotes": "CreditNoteID",
       "Payments": "PaymentID", "ManualJournals": "ManualJournalID", "BankTransactions": "BankTransactionID"}
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
RETRY_AT = "2026-10-15T13:02:00.000000Z"       # the first retry: after the 60 second wait, before the change feed is due
_MS = re.compile(r"/Date\((-?\d+)")


def _now() -> str:
    """The time Xero stamps the rows the fake writes: now, so a lookup for a write just sent finds it."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ms(iso: str) -> str:
    """A time as Xero writes it: the Microsoft form it keeps for UTC times."""
    when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return f"/Date({(when - EPOCH) // timedelta(milliseconds=1)}+0000)/"


def _ms_of(value: object) -> int:
    found = _MS.search(str(value or ""))
    return int(found.group(1)) if found else 0


@dataclass
class XeroBooks:
    """Xero's books behind the client's transport: rows per list, the calls made, and what to refuse or expire."""
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {name: [] for name in ENDPOINTS})
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: set[tuple[str, int]] = field(default_factory=set)       # (list, page): a 503 once
    refuse_writes: list[xero.Reply] = field(default_factory=list)  # answers for the next writes, before the books take them
    keys: dict[str, xero.Reply] = field(default_factory=dict)      # Idempotency-Key -> the answer it got
    expire: int = 0                                                # calls refused with 401 before one is taken
    lost: int = 0                                                  # writes that land, whose answers are then lost
    list_refusals: list[xero.Reply] = field(default_factory=list)  # answers for the next lists, before any rows
    serial: int = 0

    def __call__(self, method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> xero.Reply:
        parts = urlsplit(url)
        path = parts.path.removeprefix(BASE_PATH).strip("/")
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        body = json.loads(data, parse_float=Decimal) if data else {}
        self.calls.append({"method": method, "path": path, "query": query, "headers": dict(headers), "body": body})
        if self.expire:
            self.expire -= 1
            return xero.Reply(401, {"Message": "token expired"})
        if headers.get("xero-tenant-id") != TENANT:
            return xero.Reply(403, {"Message": "Forbidden"})
        if method == "GET":
            return self._list(path, query, headers.get("If-Modified-Since", ""))
        if method == "PUT":
            key = headers.get("Idempotency-Key", "")
            if key in self.keys:
                return self.keys[key]
            if self.refuse_writes:
                return self.refuse_writes.pop(0)
            answer = self._create(path, body)
            if key and answer.status == 200:
                self.keys[key] = answer
            if self.lost and answer.status == 200:
                self.lost -= 1
                return xero.Reply(0, note="Xero did not answer")
            return answer
        if method == "POST" and path.startswith("ManualJournals/"):
            return self._void(path.split("/", 1)[1])
        return xero.Reply(404, {"Message": "no such endpoint"})

    def _list(self, name: str, query: Mapping[str, str], since: str) -> xero.Reply:
        if name not in self.rows:
            return xero.Reply(404, {"Message": "no such endpoint"})
        if self.list_refusals:
            return self.list_refusals.pop(0)
        page = int(query.get("page") or 1)
        if (name, page) in self.fail:
            self.fail.discard((name, page))
            return xero.Reply(503, {"Message": "the service is down"})
        rows = [dict(r) for r in self.rows[name]]
        if name == "Contacts" and query.get("includeArchived") != "true":
            rows = [r for r in rows if r.get("ContactStatus") != "ARCHIVED"]
        if since:
            floor = (parsedate_to_datetime(since) - EPOCH) // timedelta(milliseconds=1)
            rows = [r for r in rows if _ms_of(r.get("UpdatedDateUTC")) > floor]
        order = query.get("order", "")
        if order:
            rows.sort(key=lambda r: _ms_of(r.get("UpdatedDateUTC")), reverse=order.endswith("DESC"))
        if name == "Accounts":
            return xero.Reply(200, {name: rows})
        return xero.Reply(200, {name: rows[(page - 1) * 100:page * 100]})

    def _create(self, name: str, body: Mapping[str, Any]) -> xero.Reply:
        made: list[dict[str, Any]] = []
        for raw in [body] if name == "Accounts" else body.get(name) or []:     # PUT /Accounts takes the account itself
            row = dict(raw)
            if name == "ManualJournals" and sum(Decimal(str(x["LineAmount"])) for x in row["JournalLines"]) != 0:
                return xero.Reply(400, {"Message": "A validation exception occurred", "Elements": [
                    {"ValidationErrors": [{"Message": "Journal lines do not balance"}]}]})
            if name == "BankTransactions" and not isinstance(row.get("BankAccount"), dict):
                return xero.Reply(400, {"Message": "A validation exception occurred", "Elements": [
                    {"ValidationErrors": [{"Message": "A bank account is required"}]}]})
            self.serial += 1
            row.setdefault(IDS[name], f"{name.lower()}-{self.serial}")
            row["UpdatedDateUTC"] = ms(_now())
            self.rows[name].append(row)
            made.append(row)
        return xero.Reply(200, {name: made})

    def _void(self, ident: str) -> xero.Reply:
        for row in self.rows["ManualJournals"]:
            if row["ManualJournalID"] == ident:
                row["Status"], row["UpdatedDateUTC"] = "VOIDED", ms(_now())
                return xero.Reply(200, {"ManualJournals": [row]})
        return xero.Reply(400, {"Message": f"ManualJournal {ident} was not found"})

    def change(self, name: str, row: dict[str, Any]) -> None:
        """A row as Xero holds it after an edit: the row with its id is replaced, else it is added."""
        found = [r for r in self.rows[name] if r.get(IDS[name]) == row[IDS[name]]]
        if found:
            self.rows[name][self.rows[name].index(found[0])] = row
        else:
            self.rows[name].append(row)


def _client(xbooks: XeroBooks, sleep: Any = lambda seconds: None) -> xero.Xero:
    return xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=sleep)


def _connection(store: Any) -> str:
    cid = "conn-xero"
    with store.transaction() as c:
        c.execute("INSERT INTO fin_connections(id, provider, purpose, created_at, created_by, name) VALUES(?,?,?,?,?,?)",
                  (cid, "xero", "books", AT, OWNER, "Acme Xero"))
    return cid


def _mirror(store: Any, cid: str) -> None:
    with store.transaction() as c:
        SL.set_book_mode(c, SL.book(c).id, "mirror", cid)  # type: ignore[union-attr]


def _drain(store: Any, client: xero.Xero, cid: str, now: str = AT, passes: int = 12) -> list[str]:
    problems: list[str] = []
    for _ in range(passes):
        problems += mirror.sync_once(store, client, cid, now)
    return problems


def _account_row(code: str, name: str, cls: str, kind: str, system: str = "") -> dict[str, Any]:
    return {"AccountID": f"acct-{code}", "Code": code, "Name": name, "Class": cls, "Type": kind, "Status": "ACTIVE",
            "SystemAccount": system, "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")}


def _chart(xbooks: XeroBooks) -> None:
    xbooks.rows["Accounts"] = [
        _account_row("1000", "Bank", "ASSET", "BANK"),
        _account_row("1200", "Debtors", "ASSET", "CURRENT", "DEBTORS"),
        _account_row("2000", "Creditors", "LIABILITY", "CURRLIAB", "CREDITORS"),
        _account_row("2300", "GST", "LIABILITY", "CURRLIAB", "GST"),
        _account_row("4000", "Sales", "REVENUE", "REVENUE"),
        _account_row("6100", "Software", "EXPENSE", "EXPENSE")]
    xbooks.rows["Contacts"] = [{"ContactID": "contact-1", "Name": "Acme Customer", "IsCustomer": True,
                                "IsSupplier": False, "ContactStatus": "ACTIVE", "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")}]


def _line(account: str, amount: str, tax: str = "0.00") -> dict[str, Any]:
    return {"Description": "Consulting", "LineAmount": amount, "TaxAmount": tax, "AccountID": account}


def _invoice(ident: str, *, status: str = "AUTHORISED", total: str = "100.00", kind: str = "ACCREC",
             currency: str = "USD", updated: str = "2026-10-05T00:00:00Z", day: str = "2026-10-05") -> dict[str, Any]:
    account = "acct-4000" if kind == "ACCREC" else "acct-6100"
    return {"InvoiceID": ident, "Type": kind, "InvoiceNumber": f"INV-{ident}", "Status": status,
            "Date": ms(f"{day}T00:00:00Z"), "DateString": f"{day}T00:00:00", "Contact": {"ContactID": "contact-1"},
            "CurrencyCode": currency, "LineAmountTypes": "Exclusive", "LineItems": [_line(account, total)],
            "UpdatedDateUTC": ms(updated)}


def _credit_note(ident: str, invoice: str, amount: str, updated: str = "2026-10-06T00:00:00Z",
                 kind: str = "ACCRECCREDIT") -> dict[str, Any]:
    """A credit note: a receivable one (ACCRECCREDIT) applied to an invoice, or a supplier's (ACCPAYCREDIT) to a bill."""
    side, account = ("ACCREC", "acct-4000") if kind == "ACCRECCREDIT" else ("ACCPAY", "acct-6100")
    return {"CreditNoteID": ident, "Type": kind, "CreditNoteNumber": f"CN-{ident}", "Status": "AUTHORISED",
            "Date": ms("2026-10-06T00:00:00Z"), "DateString": "2026-10-06T00:00:00", "Contact": {"ContactID": "contact-1"},
            "CurrencyCode": "USD", "LineAmountTypes": "Exclusive", "LineItems": [_line(account, amount)],
            "Allocations": [{"AllocationID": f"alloc-{ident}", "Invoice": {"InvoiceID": invoice, "Type": side},
                             "Amount": amount, "Date": ms("2026-10-06T00:00:00Z"), "IsDeleted": False}],
            "UpdatedDateUTC": ms(updated)}


def _spend(ident: str, items: list[dict[str, Any]], *, kind: str = "SPEND", lat: str = "Exclusive",
           currency: str = "USD") -> dict[str, Any]:
    """A bank transaction on the 1000 bank account (SPEND takes money out, RECEIVE brings it in)."""
    return {"BankTransactionID": ident, "Type": kind, "Status": "AUTHORISED", "Date": ms("2026-10-05T00:00:00Z"),
            "DateString": "2026-10-05T00:00:00", "BankAccount": {"AccountID": "acct-1000"},
            "Contact": {"ContactID": "contact-1"}, "CurrencyCode": currency, "LineAmountTypes": lat, "Reference": "",
            "LineItems": items, "UpdatedDateUTC": ms("2026-10-05T00:00:00Z")}


def _payment(ident: str, invoice: str, amount: str, status: str = "AUTHORISED",
             updated: str = "2026-10-10T00:00:00Z", kind: str = "ACCRECPAYMENT") -> dict[str, Any]:
    side = "ACCPAY" if kind == "ACCPAYPAYMENT" else "ACCREC"
    return {"PaymentID": ident, "PaymentType": kind, "Status": status, "Date": ms("2026-10-10T00:00:00Z"),
            "DateString": "2026-10-10T00:00:00", "Amount": amount, "Account": {"AccountID": "acct-1000"},
            "Invoice": {"InvoiceID": invoice, "Type": side}, "UpdatedDateUTC": ms(updated)}


def _journal(ident: str, amount: str, status: str = "POSTED", day: str = "2026-10-05T00:00:00",
             updated: str = "2026-10-05T00:00:00Z") -> dict[str, Any]:
    negated = str(-Decimal(amount))
    return {"ManualJournalID": ident, "Status": status, "Date": ms(day + "Z"), "DateString": day,
            "Narration": "Software", "LineAmountTypes": "NoTax",
            "JournalLines": [{"LineAmount": amount, "AccountID": "acct-6100", "Description": "tool"},
                             {"LineAmount": negated, "AccountID": "acct-1000", "Description": "bank"}],
            "UpdatedDateUTC": ms(updated)}


def _bank_row(c: Any, amount: int, day: str) -> tuple[str, str]:
    """A bank account on the 1000 line and one new row on it (what the bank feed brings in)."""
    money, fid = SL.new_id(), SL.new_id()
    book = SL.book(c)
    c.execute("INSERT INTO fin_money_accounts(id, book_id, kind, name, provider, account_id, created_at, created_by) "
              "VALUES(?,?,?,?,?,?,?,?)", (money, book.id, "bank", "Checking", "mercury", account(c, "1000"), AT, OWNER))
    c.execute("INSERT INTO fin_feed_txns(id, money_account_id, provider_id, txn_date, amount, currency, counterparty, "
              "status, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (fid, money, f"txn-{fid[:8]}", day, amount, book.base_currency, "Acme Software", "new", AT, OWNER))
    return money, fid


# ------------------------------------------------------------------------------------------------ the client
def test_a_backfill_page_that_fails_resumes_at_its_position_without_duplicates(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n}", "Name": f"Acme Contact {n:03d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE", "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")}
                               for n in range(150)]
    xbooks.fail.add(("Contacts", 2))
    cid = _connection(books)
    problems = _drain(books, _client(xbooks), cid, passes=4)
    assert problems == ["backfill parties from 101: the service is down"]
    assert _drain(books, _client(xbooks), cid, passes=6) == []
    pages = [c["query"].get("page") for c in xbooks.calls if c["path"] == "Contacts"]
    assert pages[:3] == ["1", "2", "2"]                                       # the failed page is asked for again
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE connection_id=? AND kind='Contact'",
                         (cid,)).fetchone()[0] == 150


def test_the_client_refreshes_its_token_once_after_a_401_and_gives_up_when_hq_cannot(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.expire = 1
    forced: list[bool] = []

    def tokens(force: bool) -> str:
        forced.append(force)
        return "token-2"

    client = xero.Xero(TENANT, tokens, base=BASE, transport=xbooks, sleep=lambda s: None)
    assert client.page("accounts", 1, 100).ok
    assert forced == [False, True]

    def refused(force: bool) -> str:
        raise books_link.BrokerFailed("refused")

    read = xero.Xero(TENANT, refused, base=BASE, transport=xbooks).page("accounts", 1, 100)
    assert not read.ok and "could not" in read.error


def test_a_429_waits_out_retry_after_and_is_tried_again_then_gives_up_after_a_few(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    sleeps: list[float] = []
    waits = [xero.Reply(429, {"Message": "rate limit"}, 2.0), xero.Reply(200, {"Accounts": xbooks.rows["Accounts"]})]
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, sleep=sleeps.append,
                       transport=lambda m, u, h, d: waits.pop(0) if waits else xbooks(m, u, h, d))
    assert client.page("accounts", 1, 100).ok
    assert sleeps == [2.0]
    always = xero.Xero(TENANT, lambda force: "token-1", base=BASE, sleep=sleeps.append,
                       transport=lambda m, u, h, d: xero.Reply(429, {"Message": "rate limit"}, 0.0))
    sleeps.clear()
    read = always.page("accounts", 1, 100)
    assert not read.ok and read.error == "rate limit" and len(sleeps) == xero.TRIES_429


def test_a_list_counts_every_row_even_the_drafts_the_books_do_not_take_in(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("inv-draft", status="DRAFT")]
    read = _client(xbooks).page("invoices", 1, 100)
    assert read.ok and read.seen == 2 and [r.ext.external_id for r in read.records] == ["inv-1"]
    assert _client(xbooks).page("invoices", 101, 100).seen == 0               # past the last row: nothing, short page


def test_a_write_is_confirmed_by_xero_before_the_local_book_changes(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid = _connection(books)
    _mirror(books, cid)
    client = _client(xbooks)
    assert _drain(books, client, cid) == []
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}}).status \
            == "committed"
        before = c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0]
    xbooks.refuse_writes.append(xero.Reply(503, {"Message": "try later"}))
    assert mirror.sync_once(books, client, cid, LATER) == ["journal.create: try later"]
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_entries").fetchone()[0] == before
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == "new"
        assert c.execute("SELECT status FROM fin_outbox").fetchone()[0] == "pending"
    assert mirror.sync_once(books, client, cid, LATER2) == []
    with books.read() as c:
        status, entry = c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()
        assert status == "posted" and entry
        assert c.execute("SELECT external_id FROM fin_external_refs WHERE local_id=?", (entry,)).fetchone()[0] \
            == "BankTransaction:banktransactions-1"
    writes = [c for c in xbooks.calls if c["path"] == "BankTransactions" and c["method"] == "PUT"]
    assert len(writes) == 2 and writes[0]["headers"]["Idempotency-Key"] == writes[1]["headers"]["Idempotency-Key"]
    sent = writes[1]["body"]["BankTransactions"][0]
    assert sent["Type"] == "SPEND" and sent["BankAccount"] == {"AccountID": "acct-1000"}
    assert sent["LineItems"][0]["AccountID"] == "acct-6100" and sent["LineItems"][0]["LineAmount"] == "15.00"
    assert sent["Reference"].endswith("]") and "[tico:" in sent["Reference"]


# ------------------------------------------------------------------------------------------------ the mirror
def test_a_newer_updated_date_reverses_and_replaces_the_journal_and_keeps_the_books_balanced(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["ManualJournals"] = [_journal("mj-1", "15.00")]
    cid = _connection(books)
    _mirror(books, cid)
    client = _client(xbooks)
    assert _drain(books, client, cid) == []
    with books.read() as c:
        old = c.execute("SELECT local_id FROM fin_external_refs WHERE external_id='ManualJournal:mj-1'").fetchone()[0]
    xbooks.change("ManualJournals", _journal("mj-1", "20.00", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, client, cid, now=LATER, passes=1) == []
    with books.read() as c:
        new = c.execute("SELECT local_id FROM fin_external_refs WHERE external_id='ManualJournal:mj-1'").fetchone()[0]
        assert new != old
        assert c.execute("SELECT status FROM fin_entries WHERE id=?", (old,)).fetchone()[0] == "reversed"
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE entry_id=?", (new,)).fetchone()[0] == 0
        assert c.execute("SELECT amount FROM fin_lines WHERE entry_id=? AND account_id=?",
                         (new, account(c, "6100"))).fetchone()[0] == 2000
        assert c.execute("SELECT SUM(amount) FROM fin_lines").fetchone()[0] == 0


def test_an_edit_to_a_closed_month_is_posted_in_the_open_period_and_flagged(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["ManualJournals"] = [_journal("mj-9", "15.00", day="2026-09-10T00:00:00")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    xbooks.change("ManualJournals", _journal("mj-9", "20.00", day="2026-09-10T00:00:00", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert [r[0] for r in c.execute("SELECT change FROM fin_restatements ORDER BY id")] == ["replaced"]
        posted = c.execute("SELECT entry_date FROM fin_entries WHERE id=(SELECT local_id FROM fin_external_refs "
                           "WHERE external_id='ManualJournal:mj-9')").fetchone()[0]
        assert posted > "2026-09-30"


def test_a_credit_note_applied_to_an_invoice_settles_it_without_moving_cash(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        cash_before = [tuple(r) for r in c.execute("SELECT * FROM fin_cash_rollups ORDER BY 1, 2")]
    xbooks.change("CreditNotes", _credit_note("cn-1", "inv-1", "100.00", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        status, paid = c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()
        entry = c.execute("SELECT source FROM fin_entries WHERE id=("
                          "SELECT local_id FROM fin_external_refs WHERE external_id='CreditMemo:cn-1')").fetchone()
        settled = [r[0] for r in c.execute("SELECT from_kind FROM fin_settlements")]
        cash_after = [tuple(r) for r in c.execute("SELECT * FROM fin_cash_rollups ORDER BY 1, 2")]
        pnl = c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id = l.account_id "
                        "WHERE a.type IN ('income', 'expense') AND l.entry_id=("
                        "SELECT local_id FROM fin_external_refs WHERE external_id='CreditMemo:cn-1')").fetchone()[0]
    assert (status, paid) == ("paid", 10000)
    assert entry[0] == "invoice"
    assert settled == ["credit"]
    # Posting the credit memo takes its P&L out of the cash view; the settlement itself must add nothing to it.
    assert pnl != 0
    assert sum(r[-1] for r in cash_after) - sum(r[-1] for r in cash_before) == -pnl


def test_a_payment_settles_its_invoice_and_its_deletion_gives_the_money_back(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    xbooks.rows["Payments"] = [_payment("pay-1", "inv-1", "100.00")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[:] \
            == ("paid", 10000)
    xbooks.change("Payments", _payment("pay-1", "inv-1", "100.00", status="DELETED", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[:] \
            == ("sent", 0)


def test_a_document_in_another_currency_waits_as_unmapped(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-eur", currency="EUR")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Invoice:inv-eur'").fetchone()[0] \
            == "unmapped"


# ------------------------------------------------------------------------------------------------ the records
def test_the_records_take_exact_money_and_net_the_tax_out_of_inclusive_lines():
    assert xero._reply(200, b'{"x": 0.10, "y": 0.20}', None).body == {"x": Decimal("0.10"), "y": Decimal("0.20")}
    assert X.amount_text(1500) == "15.00" and X.amount_text(-5) == "-0.05"
    tax = lambda: "Account:acct-2300"  # noqa: E731
    row = _invoice("inv-inc")
    row.update({"LineAmountTypes": "Inclusive", "LineItems": [
        {"LineAmount": "120.00", "TaxAmount": "20.00", "AccountID": "acct-4000", "Description": "Consulting"}]})
    (doc,) = X.records("invoices", [row], tax, lambda: "")
    assert [(x.account, x.amount) for x in doc.lines] == [("Account:acct-4000", 10000), ("Account:acct-2300", 2000)]
    assert doc.total == 12000 and doc.currency == "USD" and doc.party == "Contact:contact-1"


def test_accounts_map_to_the_books_types_and_the_control_accounts_by_their_system_role():
    debtors = X.account_record(_account_row("1200", "Debtors", "ASSET", "CURRENT", "DEBTORS"))
    assert isinstance(debtors, M.ChartAccount) and debtors.type == "asset" and debtors.subtype == "receivable"
    assert X.account_record(_account_row("6100", "Software", "EXPENSE", "EXPENSE")).type == "expense"
    kept = X.account_record({**_account_row("1000", "Bank", "ASSET", "BANK"), "Code": ""})
    assert isinstance(kept, M.ChartAccount) and kept.ext.key == "Account:acct-1000" and kept.code.startswith("X-")
    deleted = X.account_record({**_account_row("1000", "Bank", "ASSET", "BANK"), "Status": "DELETED"})
    assert isinstance(deleted, M.Ext) and deleted.deleted and deleted.kind == "Account"
    assert X.stamp(ms("2026-10-05T00:00:00Z")) == "2026-10-05T00:00:00.000Z"


def test_a_voided_or_deleted_row_is_a_deleted_record_and_a_supplier_credit_note_is_a_vendor_credit():
    voided = X.document_record(_invoice("inv-x", status="VOIDED"), tax=lambda: "", rounding=lambda: "")
    assert isinstance(voided, M.Ext) and voided.deleted and voided.kind == "Invoice"
    supplier = _credit_note("cn-s", "bill-1", "10.00", kind="ACCPAYCREDIT")
    (credit,) = X.records("credit_notes", [supplier], lambda: "", lambda: "")
    assert isinstance(credit, M.Document) and credit.ext.kind == "VendorCredit"
    assert credit.links == (("Bill:bill-1", 1000),)


def test_the_bodies_sent_carry_exact_decimals_and_xeros_codes():
    body = X.journal_body({"date": "2026-10-05", "memo": "Software", "lines": [
        {"account": "Account:acct-6100", "amount": 1500, "description": "tool"},
        {"account": "Account:acct-1000", "amount": -1500, "description": "bank"}]})
    assert [x["LineAmount"] for x in body["JournalLines"]] == ["15.00", "-15.00"]
    assert body["Status"] == "POSTED" and body["LineAmountTypes"] == "NoTax"
    assert X.account_body({"code": "6100", "name": "Software", "type": "expense"})["Type"] == "EXPENSE"
    assert X.void_body("mj-1") == {"ManualJournals": [{"ManualJournalID": "mj-1", "Status": "VOIDED"}]}


def test_a_relayed_xero_event_is_kept_once_for_the_company_it_names(books):
    with books.transaction() as c:
        SX.books_connection_insert(c, cid="conn-xero-hook", provider="xero", name="Acme Xero", by=OWNER, at=AT)
        SX.set_books_settings(c, "conn-xero-hook", {"provider": "xero", "company_id": "co_acme", "tenant_id": TENANT})
    body = {"type": "tico.finance.webhook", "provider": "xero", "company_id": "co_acme", "external_id": "evt-x1",
            "payload": {"events": [{"resourceId": "inv-1", "eventCategory": "INVOICE", "eventType": "UPDATE"}]}}
    with books.transaction() as c:
        assert books_sync.delivery(c, "xero", body) == "kept"
        assert books_sync.delivery(c, "xero", body) == "seen"
        assert books_sync.delivery(c, "xero", {**body, "external_id": "evt-x2", "company_id": "co_other"}) == "refused"
        assert books_sync.delivery(c, "xero", {**body, "external_id": "evt-x3",
                                               "payload": {"eventNotifications": []}}) == "refused"


# ------------------------------------------------------------------------------------------------ writes that may have landed
def _categorized(books: Any, xbooks: XeroBooks) -> tuple[str, xero.Xero, str]:
    """A mirrored connection on the chart, read through once, with one bank row categorized as an expense."""
    cid = _connection(books)
    _mirror(books, cid)
    client = _client(xbooks)
    assert _drain(books, client, cid) == []
    with books.transaction() as c:
        _, fid = _bank_row(c, -1500, "2026-10-05")
        assert act(c, OWNER, "categorize", {"feed_id": fid, "account_id": account(c, "6100"), "tags": {}}).status \
            == "committed"
    return cid, client, fid


def _bank_puts(xbooks: XeroBooks) -> list[dict[str, Any]]:
    return [c for c in xbooks.calls if c["path"] == "BankTransactions" and c["method"] == "PUT"]


def test_a_lost_write_is_found_by_its_marker_and_bound_not_sent_again(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid, client, fid = _categorized(books, xbooks)
    xbooks.lost = 1                                                   # the write lands; its answer never arrives
    assert mirror.sync_once(books, client, cid, LATER) == ["journal.create: Xero did not answer"]
    assert mirror.sync_once(books, client, cid, RETRY_AT) == []       # the retry finds the write and binds it
    with books.read() as c:
        status, entry = c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()
        assert status == "posted" and entry
        assert c.execute("SELECT external_id FROM fin_external_refs WHERE local_id=?", (entry,)).fetchone()[0] \
            == "BankTransaction:banktransactions-1"
        assert c.execute("SELECT status FROM fin_outbox").fetchone()[0] == "sent"
    assert len(xbooks.rows["BankTransactions"]) == 1
    assert len(_bank_puts(xbooks)) == 1
    assert any(c["method"] == "GET" and c["path"] == "BankTransactions" for c in xbooks.calls)


def test_a_refused_write_is_sent_again_under_its_key_once_the_lookup_finds_nothing(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid, client, fid = _categorized(books, xbooks)
    xbooks.refuse_writes.append(xero.Reply(503, {"Message": "try later"}))
    assert mirror.sync_once(books, client, cid, LATER) == ["journal.create: try later"]
    assert mirror.sync_once(books, client, cid, RETRY_AT) == []
    puts = _bank_puts(xbooks)
    assert len(puts) == 2 and puts[0]["headers"]["Idempotency-Key"] == puts[1]["headers"]["Idempotency-Key"]
    assert len(xbooks.rows["BankTransactions"]) == 1
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == "posted"


def test_a_lookup_xero_cannot_answer_marks_the_write_unknown_and_never_sends_it_again(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid, client, fid = _categorized(books, xbooks)
    xbooks.lost = 1
    assert mirror.sync_once(books, client, cid, LATER) == ["journal.create: Xero did not answer"]
    xbooks.list_refusals.append(xero.Reply(400, {"Message": "A validation exception occurred"}))
    assert mirror.sync_once(books, client, cid, RETRY_AT) == [
        "journal.create: UNKNOWN: A validation exception occurred"]
    assert _drain(books, client, cid, now=RETRY_AT, passes=3) == []    # the change feed is not due: only the outbox
    assert len(_bank_puts(xbooks)) == 1
    with books.read() as c:
        status, last = c.execute("SELECT status, last_error FROM fin_outbox").fetchone()
        assert status == "failed" and last.startswith("UNKNOWN:")
        assert c.execute("SELECT status FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()[0] == "new"


def test_find_matches_writes_by_their_marker_or_name_and_gives_up_on_a_scan_past_its_cap(monkeypatch):
    xbooks = XeroBooks()
    _chart(xbooks)
    key = "outbox-key-1"
    bank = _spend("bt-1", [_line("acct-6100", "15.00")])
    bank.update({"Reference": f"Software {xero._marker(key)}", "UpdatedDateUTC": ms(_now())})
    xbooks.rows["BankTransactions"] = [bank]
    xbooks.rows["ManualJournals"] = [{**_journal("mj-1", "15.00"), "Narration": f"Software {xero._marker(key)}",
                                      "UpdatedDateUTC": ms(_now())}]
    client = _client(xbooks)
    found = client.find("journal.create", {"feed": True}, key)
    assert found is not None and found.ok and (found.kind, found.external_id) == ("BankTransaction", "bt-1")
    assert found.revision
    assert client.find("journal.create", {"feed": True}, "another-key") is None
    manual = client.find("journal.create", {}, key)
    assert manual is not None and (manual.kind, manual.external_id) == ("ManualJournal", "mj-1")
    by_name = client.find("account.create", {"name": "software"}, key)
    assert by_name is not None and (by_name.kind, by_name.external_id) == ("Account", "acct-6100")
    assert client.find("account.create", {"name": "Nothing Like It"}, key) is None
    nameless = client.find("party.create", {"name": ""}, key)
    assert nameless is not None and nameless.final and nameless.error.startswith("UNKNOWN:")
    assert client.find("journal.delete", {"ext": "ManualJournal:mj-1"}, key) is None
    rows = []
    for n in range(250):
        row = _spend(f"bt-{n + 10}", [_line("acct-6100", "1.00")])
        row["UpdatedDateUTC"] = ms(_now())
        rows.append(row)
    xbooks.rows["BankTransactions"] = rows
    monkeypatch.setattr(xero, "FIND_PAGES", 2)
    capped = client.find("journal.create", {"feed": True}, "unseen-key")
    assert capped is not None and capped.final and capped.error.startswith("UNKNOWN: too many rows")


def test_accounts_are_sent_as_the_account_itself_and_contacts_without_their_kind_flags(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    made = _client(xbooks).send("account.create", {"code": "7000", "name": "Travel", "type": "expense",
                                                   "active": True}, "key-account")
    assert made.ok and made.kind == "Account"
    put = [c for c in xbooks.calls if c["path"] == "Accounts" and c["method"] == "PUT"][-1]
    assert put["body"]["Name"] == "Travel" and put["body"]["Type"] == "EXPENSE" and "Accounts" not in put["body"]
    assert _client(xbooks).send("party.create", {"name": "Acme Supplier", "kind": "vendor",
                                                 "emails": ["ap@acme.example"], "active": True}, "key-party").ok
    party = [c for c in xbooks.calls if c["path"] == "Contacts" and c["method"] == "PUT"][-1]
    assert party["body"] == {"Contacts": [{"Name": "Acme Supplier", "ContactStatus": "ACTIVE",
                                           "EmailAddress": "ap@acme.example"}]}


def test_a_long_rate_limit_stops_the_pass_without_sleeping_and_the_next_pass_resumes(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    sleeps: list[float] = []
    calls: list[str] = []

    def limited(method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> xero.Reply:
        calls.append(url)
        return xero.Reply(429, {"Message": "rate limit"}, 120.0)

    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=limited, sleep=sleeps.append)
    first = client.page("accounts", 1, 100)
    assert not first.ok and first.error == "rate limit"
    assert sleeps == [] and len(calls) == 1                           # past the budget: no sleep, the pass stops
    assert not client.page("invoices", 1, 100).ok
    assert len(calls) == 1                                            # the rest of the pass makes no call
    waits = [xero.Reply(429, {"Message": "rate limit"}, 3.0), xero.Reply(429, {"Message": "rate limit"}, 3.0)]
    short = xero.Xero(TENANT, lambda force: "token-1", base=BASE, sleep=sleeps.append,
                      transport=lambda m, u, h, d: waits.pop(0))
    assert not short.page("accounts", 1, 100).ok
    assert sleeps == [3.0]                                            # the first 3 seconds fit the budget, the next do not
    assert _client(xbooks, sleep=sleeps.append).page("accounts", 1, 100).ok


def test_a_backfill_that_meets_an_edit_mid_way_reads_every_contact_once(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    base = _ms_of(ms("2026-10-01T00:00:00Z"))
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n:03d}", "Name": f"Acme Contact {n:03d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE", "UpdatedDateUTC": f"/Date({base + n}+0000)/"}
                               for n in range(150)]
    cid = _connection(books)
    client = _client(xbooks)
    assert mirror.sync_once(books, client, cid, AT) == []             # accounts
    assert mirror.sync_once(books, client, cid, AT) == []             # contacts, the first hundred
    row = next(r for r in xbooks.rows["Contacts"] if r["ContactID"] == "contact-060")
    xbooks.change("Contacts", {**row, "Name": "Acme Contact 060 renamed", "UpdatedDateUTC": ms(_now())})
    assert mirror.sync_once(books, client, cid, AT) == []             # the rest of the contacts
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE connection_id=? AND kind='Contact'",
                         (cid,)).fetchone()[0] == 150
    reads = [c for c in xbooks.calls if c["path"] == "Contacts" and c["method"] == "GET"]
    assert [c["query"]["page"] for c in reads] == ["1", "2"]
    assert {c["query"]["order"] for c in reads} == {xero.ORDER}


def test_a_change_window_longer_than_the_page_cap_is_reported_not_read_short(monkeypatch):
    monkeypatch.setattr(xero, "MAX_PAGES", 2)
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n:03d}", "Name": f"Acme Contact {n:03d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE", "UpdatedDateUTC": ms(_now())} for n in range(250)]
    read = _client(xbooks).changed("2026-10-15T11:55:00Z")
    assert not read.ok and "changed too much" in read.error


def test_archived_contacts_are_read_with_the_archived_ones_and_kept_inactive(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Contacts"].append({"ContactID": "contact-old", "Name": "Acme Old Supplier", "IsSupplier": True,
                                    "IsCustomer": False, "ContactStatus": "ARCHIVED",
                                    "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")})
    cid = _connection(books)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT active, kind FROM fin_contacts WHERE name='Acme Old Supplier'").fetchone()) \
            == (0, "vendor")
    reads = [c for c in xbooks.calls if c["path"] == "Contacts" and c["method"] == "GET"]
    assert reads and all(c["query"].get("includeArchived") == "true" for c in reads)


# ------------------------------------------------------------------------------------------------ the records
@pytest.mark.parametrize("kind, lat, account_id, line, tax_text, bank, expense, tax", [
    ("SPEND", "Inclusive", "acct-6100", "120.00", "20.00", -12000, 10000, 2000),
    ("SPEND", "Exclusive", "acct-6100", "100.00", "20.00", -12000, 10000, 2000),
    ("SPEND", "NoTax", "acct-6100", "100.00", "0.00", -10000, 10000, 0),
    ("RECEIVE", "Exclusive", "acct-4000", "100.00", "20.00", 12000, -10000, -2000),
    ("RECEIVE", "Inclusive", "acct-4000", "120.00", "20.00", 12000, -10000, -2000),
])
def test_a_bank_transaction_takes_its_tax_by_its_line_type_and_its_direction(
        kind, lat, account_id, line, tax_text, bank, expense, tax):
    def tax_account() -> str:
        return "Account:acct-2300"

    row = _spend("bt-tax", [_line(account_id, line, tax_text)], kind=kind, lat=lat)
    (journal,) = X.records("bank_transactions", [row], tax_account, lambda: "")
    by_account = {x.account: x.amount for x in journal.lines}
    assert by_account["Account:acct-1000"] == bank
    assert by_account[f"Account:{account_id}"] == expense
    assert by_account.get("Account:acct-2300", 0) == tax
    assert sum(x.amount for x in journal.lines) == 0 and journal.currency == "USD"


def test_a_bank_transaction_in_another_currency_waits_as_unmapped(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["BankTransactions"] = [_spend("bt-eur", [_line("acct-6100", "15.00")], currency="EUR")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='BankTransaction:bt-eur'").fetchone()[0] \
            == "unmapped"


def test_a_categorized_bank_row_is_sent_as_a_bank_transaction_in_its_direction():
    def lines(amount: int, other: int | None = None) -> list[dict[str, Any]]:
        return [{"account": "Account:acct-1000", "amount": amount, "role": L.MONEY_ROLE},
                {"account": "Account:acct-6100", "amount": -amount if other is None else other, "role": "",
                 "description": "tool", "party": "Contact:contact-1"}]

    spend = X.bank_body({"date": "2026-10-05", "memo": "Acme Software", "marker": "[tico:x]", "lines": lines(-1500)})
    assert spend["Type"] == "SPEND" and spend["BankAccount"] == {"AccountID": "acct-1000"}
    assert spend["LineItems"][0]["AccountID"] == "acct-6100" and spend["LineItems"][0]["LineAmount"] == "15.00"
    assert spend["Contact"] == {"ContactID": "contact-1"} and spend["Reference"] == "Acme Software [tico:x]"
    receive = X.bank_body({"date": "2026-10-05", "memo": "", "marker": "[tico:x]", "lines": lines(1500)})
    assert receive["Type"] == "RECEIVE" and receive["LineItems"][0]["LineAmount"] == "15.00"
    with pytest.raises(ValueError):
        X.bank_body({"date": "2026-10-05", "memo": "", "marker": "", "lines": lines(-1500, other=-1000)})


def test_a_payment_keeps_the_kind_its_type_names_even_once_deleted():
    stamp_ = ms("2026-10-15T00:00:00Z")
    bill_gone = X.payment_record({"PaymentID": "p-b", "Status": "DELETED", "PaymentType": "ACCPAYPAYMENT",
                                  "UpdatedDateUTC": stamp_})
    received_gone = X.payment_record({"PaymentID": "p-r", "Status": "DELETED", "PaymentType": "ACCRECPAYMENT",
                                      "UpdatedDateUTC": stamp_})
    untyped_gone = X.payment_record({"PaymentID": "p-u", "Status": "DELETED",
                                     "Invoice": {"InvoiceID": "bill-1", "Type": "ACCPAY"}, "UpdatedDateUTC": stamp_})
    assert [x.kind for x in (bill_gone, received_gone, untyped_gone)] == ["BillPayment", "Payment", "BillPayment"]
    assert all(isinstance(x, M.Ext) and x.deleted for x in (bill_gone, received_gone, untyped_gone))
    paid = X.payment_record({"PaymentID": "p-c", "Status": "AUTHORISED", "PaymentType": "ACCPAYPAYMENT",
                             "Amount": "50.00", "Date": stamp_, "Account": {"AccountID": "acct-1000"},
                             "Invoice": {"InvoiceID": "bill-1", "Type": "ACCPAY"}, "UpdatedDateUTC": stamp_})
    assert isinstance(paid, M.Settlement) and paid.ext.kind == "BillPayment"
    assert paid.links == (("Bill:bill-1", 5000),)


def test_a_bill_payment_settles_its_bill_and_its_deletion_gives_the_money_back(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("bill-1", kind="ACCPAY")]
    xbooks.rows["Payments"] = [_payment("bp-1", "bill-1", "100.00", kind="ACCPAYPAYMENT")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status, amount_paid FROM fin_bills WHERE number='INV-bill-1'").fetchone()[:] \
            == ("paid", 10000)
    xbooks.change("Payments", _payment("bp-1", "bill-1", "100.00", status="DELETED",
                                       updated="2026-10-15T12:45:00Z", kind="ACCPAYPAYMENT"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT status, amount_paid FROM fin_bills WHERE number='INV-bill-1'").fetchone()[:] \
            == ("open", 0)


def test_a_supplier_credit_note_settles_its_bill_without_moving_cash(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("bill-1", kind="ACCPAY")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        cash_before = [tuple(r) for r in c.execute("SELECT * FROM fin_cash_rollups ORDER BY 1, 2")]
    xbooks.change("CreditNotes", _credit_note("cn-s", "bill-1", "100.00", updated="2026-10-15T12:45:00Z",
                                              kind="ACCPAYCREDIT"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        status, paid = c.execute("SELECT status, amount_paid FROM fin_bills WHERE number='INV-bill-1'").fetchone()
        entry = c.execute("SELECT source FROM fin_entries WHERE id=("
                          "SELECT local_id FROM fin_external_refs WHERE external_id='VendorCredit:cn-s')").fetchone()
        settled = [r[0] for r in c.execute("SELECT from_kind FROM fin_settlements")]
        cash_after = [tuple(r) for r in c.execute("SELECT * FROM fin_cash_rollups ORDER BY 1, 2")]
        pnl = c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id = l.account_id "
                        "WHERE a.type IN ('income', 'expense') AND l.entry_id=("
                        "SELECT local_id FROM fin_external_refs WHERE external_id='VendorCredit:cn-s')").fetchone()[0]
    assert (status, paid) == ("paid", 10000)
    assert entry[0] == "bill"
    assert settled == ["credit"]
    assert pnl != 0
    assert sum(r[-1] for r in cash_after) - sum(r[-1] for r in cash_before) == -pnl


def test_a_taxed_manual_journal_moves_its_tax_to_the_tax_account_and_still_balances(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    row = _journal("mj-tax", "110.00")
    row["JournalLines"][0]["TaxAmount"] = "10.00"
    xbooks.rows["ManualJournals"] = [row]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        lines = {r[0]: r[1] for r in c.execute(
            "SELECT account_id, SUM(amount) FROM fin_lines WHERE entry_id=(SELECT local_id FROM fin_external_refs "
            "WHERE external_id='ManualJournal:mj-tax') GROUP BY account_id")}
        assert sum(lines.values()) == 0
        assert lines[account(c, "6100")] == 10000
        assert lines[account(c, "2300")] == 1000
        assert lines[account(c, "1000")] == -11000


def test_a_journal_kept_out_of_the_cash_basis_reports_is_accrual_only(books):
    plain = _journal("mj-plain", "15.00")
    accrual = {**_journal("mj-acc", "15.00"), "ShowOnCashBasisReports": False}
    both, only_accrual = X.records("manual_journals", [plain, accrual], lambda: "", lambda: "")
    assert (both.basis, only_accrual.basis) == ("both", "accrual")
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["ManualJournals"] = [accrual]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT basis FROM fin_entries WHERE id=(SELECT local_id FROM fin_external_refs "
                         "WHERE external_id='ManualJournal:mj-acc')").fetchone()[0] == "accrual"


def test_an_account_without_a_code_is_kept_by_its_id_and_an_archived_one_is_inactive(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Accounts"].append({"AccountID": "acct-suspense", "Code": "", "Name": "Suspense", "Class": "ASSET",
                                    "Type": "CURRENT", "Status": "ACTIVE", "SystemAccount": "",
                                    "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")})
    xbooks.rows["Accounts"].append({**_account_row("6200", "Old Travel", "EXPENSE", "EXPENSE"), "Status": "ARCHIVED"})
    cid = _connection(books)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT code, active FROM fin_accounts WHERE name='Suspense'").fetchone()) \
            == ("X-acct-suspense", 1)
        assert c.execute("SELECT active FROM fin_accounts WHERE name='Old Travel'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE external_id='Account:acct-suspense' "
                         "AND status='active'").fetchone()[0] == 1


def test_a_rounding_amount_is_a_line_on_the_rounding_account_and_the_total_is_xeros(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Accounts"].append(_account_row("4900", "Rounding", "EXPENSE", "EXPENSE"))
    row = _invoice("inv-r", total="100.00")
    row.update({"Total": "100.01", "RoundingAmount": "0.01"})
    xbooks.rows["Invoices"] = [row]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_invoices WHERE number='INV-inv-r'").fetchone()[0] == 10001
        entry = c.execute("SELECT entry_id FROM fin_invoices WHERE number='INV-inv-r'").fetchone()[0]
        lines = {r[0]: r[1] for r in c.execute("SELECT account_id, SUM(amount) FROM fin_lines WHERE entry_id=? "
                                               "GROUP BY account_id", (entry,))}
        assert sum(lines.values()) == 0
        assert lines[account(c, "4900")] * 10000 == lines[account(c, "4000")]


def test_a_document_that_rounds_with_no_rounding_account_waits_as_unmapped(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    row = _invoice("inv-nr")
    row.update({"Total": "100.01", "RoundingAmount": "0.01"})
    xbooks.rows["Invoices"] = [row]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Invoice:inv-nr'").fetchone()[0] \
            == "unmapped"


def test_a_voided_invoice_in_a_closed_month_is_flagged_as_a_restatement(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-9", day="2026-09-10")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.transaction() as c:
        assert act(c, OWNER, "close_period", {"month": "2026-09"}).status == "committed"
    xbooks.change("Invoices", _invoice("inv-9", status="VOIDED", day="2026-09-10", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert [r[0] for r in c.execute("SELECT change FROM fin_restatements ORDER BY id")] == ["deleted"]
        assert c.execute("SELECT status FROM fin_invoices WHERE number='INV-inv-9'").fetchone()[0] == "void"


def test_a_contact_read_with_no_customer_or_supplier_flag_keeps_the_kind_the_books_hold(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid = _connection(books)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.change("Contacts", {**xbooks.rows["Contacts"][0], "Name": "Acme Client Renamed", "IsCustomer": False,
                               "IsSupplier": False, "UpdatedDateUTC": ms(_now())})
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT name, kind FROM fin_contacts WHERE name='Acme Client Renamed'").fetchone()) \
            == ("Acme Client Renamed", "customer")
    assert X.contact_record({**xbooks.rows["Contacts"][0], "IsCustomer": False, "IsSupplier": False}).kind == ""
    assert "IsCustomer" not in X.party_body({"name": "Acme Client", "active": True})
    assert "IsSupplier" not in X.party_body({"name": "Acme Client", "active": True})
