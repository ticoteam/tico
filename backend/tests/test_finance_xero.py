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

from backend.finance import books_link, mirror, xero
from backend.finance.core import mirror as M
from backend.finance.core import xero as X
from backend.finance.store import ledger as SL
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
CLOCK = "2026-10-15T12:30:00Z"                 # the time Xero stamps the rows the fake writes
_MS = re.compile(r"/Date\((-?\d+)")


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
            return answer
        if method == "POST" and path.startswith("ManualJournals/"):
            return self._void(path.split("/", 1)[1])
        return xero.Reply(404, {"Message": "no such endpoint"})

    def _list(self, name: str, query: Mapping[str, str], since: str) -> xero.Reply:
        if name not in self.rows:
            return xero.Reply(404, {"Message": "no such endpoint"})
        page = int(query.get("page") or 1)
        if (name, page) in self.fail:
            self.fail.discard((name, page))
            return xero.Reply(503, {"Message": "the service is down"})
        rows = [dict(r) for r in self.rows[name]]
        if since:
            floor = (parsedate_to_datetime(since) - EPOCH) // timedelta(milliseconds=1)
            rows = [r for r in rows if _ms_of(r.get("UpdatedDateUTC")) > floor]
        if name == "Accounts":
            return xero.Reply(200, {name: rows})
        return xero.Reply(200, {name: rows[(page - 1) * 100:page * 100]})

    def _create(self, name: str, body: Mapping[str, Any]) -> xero.Reply:
        made: list[dict[str, Any]] = []
        for raw in body.get(name) or []:
            row = dict(raw)
            if name == "ManualJournals" and sum(Decimal(str(x["LineAmount"])) for x in row["JournalLines"]) != 0:
                return xero.Reply(400, {"Message": "A validation exception occurred", "Elements": [
                    {"ValidationErrors": [{"Message": "Journal lines do not balance"}]}]})
            self.serial += 1
            row.setdefault(IDS[name], f"{name.lower()}-{self.serial}")
            row["UpdatedDateUTC"] = ms(CLOCK)
            self.rows[name].append(row)
            made.append(row)
        return xero.Reply(200, {name: made})

    def _void(self, ident: str) -> xero.Reply:
        for row in self.rows["ManualJournals"]:
            if row["ManualJournalID"] == ident:
                row["Status"], row["UpdatedDateUTC"] = "VOIDED", ms(CLOCK)
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
             currency: str = "USD", updated: str = "2026-10-05T00:00:00Z") -> dict[str, Any]:
    account = "acct-4000" if kind == "ACCREC" else "acct-6100"
    return {"InvoiceID": ident, "Type": kind, "InvoiceNumber": f"INV-{ident}", "Status": status,
            "Date": ms("2026-10-05T00:00:00Z"), "DateString": "2026-10-05T00:00:00", "Contact": {"ContactID": "contact-1"},
            "CurrencyCode": currency, "LineAmountTypes": "Exclusive", "LineItems": [_line(account, total)],
            "UpdatedDateUTC": ms(updated)}


def _credit_note(ident: str, invoice: str, amount: str,
                 updated: str = "2026-10-06T00:00:00Z") -> dict[str, Any]:
    return {"CreditNoteID": ident, "Type": "ACCRECCREDIT", "CreditNoteNumber": f"CN-{ident}", "Status": "AUTHORISED",
            "Date": ms("2026-10-06T00:00:00Z"), "DateString": "2026-10-06T00:00:00", "Contact": {"ContactID": "contact-1"},
            "CurrencyCode": "USD", "LineAmountTypes": "Exclusive", "LineItems": [_line("acct-4000", amount)],
            "Allocations": [{"AllocationID": f"alloc-{ident}", "Invoice": {"InvoiceID": invoice, "Type": "ACCREC"},
                             "Amount": amount, "Date": ms("2026-10-06T00:00:00Z"), "IsDeleted": False}],
            "UpdatedDateUTC": ms(updated)}


def _payment(ident: str, invoice: str, amount: str, status: str = "AUTHORISED",
             updated: str = "2026-10-10T00:00:00Z") -> dict[str, Any]:
    return {"PaymentID": ident, "Status": status, "Date": ms("2026-10-10T00:00:00Z"),
            "DateString": "2026-10-10T00:00:00", "Amount": amount, "Account": {"AccountID": "acct-1000"},
            "Invoice": {"InvoiceID": invoice, "Type": "ACCREC"}, "UpdatedDateUTC": ms(updated)}


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
            == "ManualJournal:manualjournals-1"
    writes = [c for c in xbooks.calls if c["path"] == "ManualJournals" and c["method"] == "PUT"]
    assert len(writes) == 2 and writes[0]["headers"]["Idempotency-Key"] == writes[1]["headers"]["Idempotency-Key"]
    assert writes[1]["body"]["ManualJournals"][0]["Status"] == "POSTED"


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
    (doc,) = X.records("invoices", [row], tax)
    assert [(x.account, x.amount) for x in doc.lines] == [("Account:acct-4000", 10000), ("Account:acct-2300", 2000)]
    assert doc.total == 12000 and doc.currency == "USD" and doc.party == "Contact:contact-1"


def test_accounts_map_to_the_books_types_and_the_control_accounts_by_their_system_role():
    debtors = X.account_record(_account_row("1200", "Debtors", "ASSET", "CURRENT", "DEBTORS"))
    assert isinstance(debtors, M.ChartAccount) and debtors.type == "asset" and debtors.subtype == "receivable"
    assert X.account_record(_account_row("6100", "Software", "EXPENSE", "EXPENSE")).type == "expense"
    assert X.account_record({**_account_row("1000", "Bank", "ASSET", "BANK"), "Code": ""}) is None
    deleted = X.account_record({**_account_row("1000", "Bank", "ASSET", "BANK"), "Status": "DELETED"})
    assert isinstance(deleted, M.Ext) and deleted.deleted and deleted.kind == "Account"
    assert X.stamp(ms("2026-10-05T00:00:00Z")) == "2026-10-05T00:00:00.000Z"


def test_a_voided_or_deleted_row_is_a_deleted_record_and_a_supplier_credit_note_waits():
    voided = X.document_record(_invoice("inv-x", status="VOIDED"), tax=lambda: "")
    assert isinstance(voided, M.Ext) and voided.deleted and voided.kind == "Invoice"
    supplier = {**_invoice("cn-s", kind="ACCPAY"), "Type": "ACCPAYCREDIT"}
    assert X.records("credit_notes", [supplier], lambda: "") == []


def test_the_bodies_sent_carry_exact_decimals_and_xeros_codes():
    body = X.journal_body({"date": "2026-10-05", "memo": "Software", "lines": [
        {"account": "Account:acct-6100", "amount": 1500, "description": "tool"},
        {"account": "Account:acct-1000", "amount": -1500, "description": "bank"}]})
    assert [x["LineAmount"] for x in body["JournalLines"]] == ["15.00", "-15.00"]
    assert body["Status"] == "POSTED" and body["LineAmountTypes"] == "NoTax"
    assert X.account_body({"code": "6100", "name": "Software", "type": "expense"})["Type"] == "EXPENSE"
    assert X.void_body("mj-1") == {"ManualJournals": [{"ManualJournalID": "mj-1", "Status": "VOIDED"}]}
