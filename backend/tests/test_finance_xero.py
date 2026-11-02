"""Xero mirror (spec §10): the client and its records on the shared mirror core. The tests drive the real client through a
transport that answers in Xero's shapes, so paging, If-Modified-Since, Idempotency-Keys, 401 and 429 are the client's."""
import json
import re
from collections.abc import Callable, Mapping
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
ENDPOINTS = ("Accounts", "Contacts", "Items", "TrackingCategories", "Invoices", "CreditNotes", "Payments",
             "ManualJournals", "BankTransactions", "BankTransfers", "Overpayments", "Prepayments")
IDS = {"Accounts": "AccountID", "Contacts": "ContactID", "Items": "ItemID", "Invoices": "InvoiceID",
       "CreditNotes": "CreditNoteID",
       "Payments": "PaymentID", "ManualJournals": "ManualJournalID", "BankTransactions": "BankTransactionID",
       "BankTransfers": "BankTransferID", "Overpayments": "OverpaymentID", "Prepayments": "PrepaymentID"}
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
        key = headers.get("Idempotency-Key", "")
        if method == "GET":
            if path.startswith("CreditNotes/"):
                return self._one("CreditNotes", path.split("/", 1)[1])
            return self._list(path, query, headers.get("If-Modified-Since", ""))
        if method == "PUT":
            if path.endswith("/Allocations"):
                return self._answer(key, lambda: self._allocate(path.split("/")[1], body))
            return self._answer(key, lambda: self._create(path, body))
        if method == "POST" and "/" in path and path.split("/")[0] in ("ManualJournals", "Invoices"):
            name, ident = path.split("/", 1)
            return self._void(name, ident)
        return xero.Reply(404, {"Message": "no such endpoint"})

    def _answer(self, key: str, make: Callable[[], xero.Reply]) -> xero.Reply:
        """A write's answer: the one Xero gave its key before, else what the books answer now."""
        if key in self.keys:
            return self.keys[key]
        if self.refuse_writes:
            return self.refuse_writes.pop(0)
        answer = make()
        if key and answer.status == 200:
            self.keys[key] = answer
        if self.lost and answer.status == 200:
            self.lost -= 1
            return xero.Reply(0, note="Xero did not answer")
        return answer

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
        if name == "BankTransfers":           # no paging and no change time: the whole list, deleted ones when asked
            rows = [r for r in rows if r.get("Status") != "DELETED" or query.get("includeDeleted") == "true"]
            return xero.Reply(200, {name: rows})
        if name == "Contacts" and query.get("includeArchived") != "true":
            rows = [r for r in rows if r.get("ContactStatus") != "ARCHIVED"]
        if name == "TrackingCategories":       # archived categories and options are listed only when asked for
            keep = query.get("includeArchived") == "true"
            rows = [{**r, "Options": [o for o in r.get("Options") or [] if keep or o.get("Status") != "ARCHIVED"]}
                    for r in rows if keep or r.get("Status") != "ARCHIVED"]
        if since:
            floor = (parsedate_to_datetime(since) - EPOCH) // timedelta(milliseconds=1)
            rows = [r for r in rows if _ms_of(r.get("UpdatedDateUTC")) > floor]
        order = query.get("order", "")
        if order:
            rows.sort(key=lambda r: _ms_of(r.get("UpdatedDateUTC")), reverse=order.endswith("DESC"))
        if name == "Accounts":
            return xero.Reply(200, {name: rows})
        return xero.Reply(200, {name: rows[(page - 1) * 100:page * 100]})

    def _one(self, name: str, ident: str) -> xero.Reply:
        for row in self.rows[name]:
            if row[IDS[name]] == ident:
                return xero.Reply(200, {name: [dict(row)]})
        return xero.Reply(400, {"Message": f"{name} {ident} was not found"})

    def _create(self, name: str, body: Mapping[str, Any]) -> xero.Reply:
        made: list[dict[str, Any]] = []
        for raw in [body] if name == "Accounts" else body.get(name) or []:     # PUT /Accounts takes the account itself
            row = dict(raw)
            if name == "ManualJournals" and self._unbalanced(row):
                return xero.Reply(400, {"Message": "A validation exception occurred", "Elements": [
                    {"ValidationErrors": [{"Message": "Journal lines do not balance"}]}]})
            if name == "BankTransactions" and not isinstance(row.get("BankAccount"), dict):
                return xero.Reply(400, {"Message": "A validation exception occurred", "Elements": [
                    {"ValidationErrors": [{"Message": "A bank account is required"}]}]})
            if name == "BankTransactions" and not isinstance(row.get("Contact"), dict):
                return xero.Reply(400, {"Message": "A validation exception occurred", "Elements": [
                    {"ValidationErrors": [{"Message": "A contact is required"}]}]})
            if name == "Payments":
                target = dict(row.get("Invoice") or {})
                invoice = next((r for r in self.rows["Invoices"] if r["InvoiceID"] == target.get("InvoiceID")), None)
                if invoice is None:
                    return xero.Reply(400, {"Message": "An invoice is required"})
                row.setdefault("PaymentType", "ACCPAYPAYMENT" if invoice["Type"] == "ACCPAY" else "ACCRECPAYMENT")
                row["Invoice"] = {"InvoiceID": invoice["InvoiceID"], "Type": invoice["Type"]}
                row.setdefault("Status", "AUTHORISED")
            if name in ("Invoices", "CreditNotes"):
                row.setdefault("Status", "AUTHORISED")
            if name == "CreditNotes":
                row.setdefault("Allocations", [])
            self.serial += 1
            row.setdefault(IDS[name], f"{name.lower()}-{self.serial}")
            row["UpdatedDateUTC"] = ms(_now())
            self.rows[name].append(row)
            made.append(row)
        return xero.Reply(200, {name: made})

    def _allocate(self, ident: str, body: Mapping[str, Any]) -> xero.Reply:
        """A credit note applied to invoices: each allocation is kept on the note, which takes a new change time."""
        note = next((r for r in self.rows["CreditNotes"] if r["CreditNoteID"] == ident), None)
        if note is None:
            return xero.Reply(400, {"Message": f"CreditNote {ident} was not found"})
        side = "ACCREC" if note["Type"] == "ACCRECCREDIT" else "ACCPAY"
        made: list[dict[str, Any]] = []
        for raw in body.get("Allocations") or []:
            self.serial += 1
            made.append({"AllocationID": f"allocation-{self.serial}", "Invoice": {"InvoiceID": raw["Invoice"]["InvoiceID"],
                         "Type": side}, "Amount": raw["Amount"], "Date": raw["Date"], "IsDeleted": False})
        note.setdefault("Allocations", []).extend(made)
        note["UpdatedDateUTC"] = ms(_now())
        return xero.Reply(200, {"Allocations": made})

    @staticmethod
    def _unbalanced(row: Mapping[str, Any]) -> bool:
        """Xero's rule: the lines' amounts balance, and on an exclusive journal the tax on top of them does too."""
        lines = row["JournalLines"]
        total = sum(Decimal(str(x["LineAmount"])) for x in lines)
        if row.get("LineAmountTypes") == "Exclusive":
            total += sum(Decimal(str(x.get("TaxAmount") or 0)) for x in lines)
        return total != 0

    def _void(self, name: str, ident: str) -> xero.Reply:
        for row in self.rows[name]:
            if row[IDS[name]] == ident:
                row["Status"], row["UpdatedDateUTC"] = "VOIDED", ms(_now())
                return xero.Reply(200, {name: [row]})
        return xero.Reply(400, {"Message": f"{name} {ident} was not found"})

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


def _no_system(name: str) -> str:
    """Xero holds no system account of any name: for records that name none."""
    return ""


def _with_unspecified(xbooks: XeroBooks) -> None:
    """Xero's Unspecified contact, which a bank transaction with no counterparty is written against."""
    xbooks.rows["Contacts"].append({"ContactID": "contact-unspecified", "Name": "Unspecified", "IsCustomer": False,
                                    "IsSupplier": False, "ContactStatus": "ACTIVE",
                                    "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")})


def _doc_body(number: str) -> dict[str, Any]:
    """A document as the outbox translates it: its party and its lines by their provider keys."""
    return {"party": "Contact:contact-1", "number": number, "day": "2026-10-05", "due": "2026-11-04", "memo": "Consulting",
            "lines": [{"account": "Account:acct-4000", "amount": 10000, "description": "Consulting"}]}


def _pay_body(doc: str, amount: int) -> dict[str, Any]:
    """A payment of one bill or invoice from the bank account, as the outbox translates it."""
    return {"kind": "invoice", "day": "2026-10-10", "memo": "Acme payment", "party": "Contact:contact-1",
            "bank": "Account:acct-1000", "total": amount, "links": [[doc, amount]], "feed_id": "", "card": False}


class _Gateway:
    """HQ's answer to the books connection's relay request (`POST relays`). The pass is about Xero, so the relay is simply
    granted here; the books-link tests cover asking for it."""

    def call(self, method: str, path: str, body: Any = None, idempotency: Any = None, **_: Any) -> dict[str, Any]:
        relay = "hr_0123456789abcdef"
        return {"relay_id": relay, "address": f"https://hooks.tico.dev/{relay}"}


def _pass_setup(books: Any, xbooks: XeroBooks, monkeypatch: Any, transport: Any = None) -> str:
    """A mirrored Xero connection whose books pass (books_sync.pass_once) runs the real adapter against the fake."""
    cid = _connection(books)
    with books.transaction() as c:
        SX.set_books_settings(c, cid, {"provider": "xero", "tenant_id": TENANT})
        SX.set_connection_status(c, cid, "active", "", AT)
    _mirror(books, cid)
    real = xero.Xero
    monkeypatch.setattr("backend.hook_relay.link_of", lambda c, vault, settings: _Gateway())
    monkeypatch.setattr(books_link, "access_token", lambda *a, **k: "token-1")
    monkeypatch.setattr(xero, "Xero", lambda tenant, tokens, **kw: real(
        tenant, tokens, base=BASE, transport=transport or xbooks, sleep=lambda s: None, **kw))
    return cid


# ------------------------------------------------------------------------------------------------ the client
def test_a_backfill_read_that_fails_is_read_again_from_its_start_without_duplicates(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n}", "Name": f"Acme Contact {n:03d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE", "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")}
                               for n in range(150)]
    xbooks.fail.add(("Contacts", 2))
    cid = _connection(books)
    problems = _drain(books, _client(xbooks), cid, passes=4)
    assert problems == ["backfill parties from 1: the service is down"]
    assert _drain(books, _client(xbooks), cid, passes=6) == []
    pages = [c["query"].get("page") for c in xbooks.calls if c["path"] == "Contacts"]
    assert pages[:4] == ["1", "2", "1", "2"]                                  # the failed read is asked for again
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
    _with_unspecified(xbooks)
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
    row = _invoice("inv-inc")
    row.update({"LineAmountTypes": "Inclusive", "LineItems": [
        {"LineAmount": "120.00", "TaxAmount": "20.00", "AccountID": "acct-4000", "Description": "Consulting"}]})
    (doc,) = X.records("invoices", [row], _no_system)
    assert [(x.account, x.amount) for x in doc.lines] == [("Account:acct-4000", 10000)] and doc.tax == 2000
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
    voided = X.document_record(_invoice("inv-x", status="VOIDED"), _no_system)
    assert isinstance(voided, M.Ext) and voided.deleted and voided.kind == "Invoice"
    supplier = _credit_note("cn-s", "bill-1", "10.00", kind="ACCPAYCREDIT")
    credit, applied = X.records("credit_notes", [supplier], _no_system)
    assert isinstance(credit, M.Document) and credit.ext.kind == "VendorCredit" and credit.total == 1000
    assert isinstance(applied, M.Settlement) and applied.links == (("Bill:bill-1", 1000),)
    assert applied.credits == (("VendorCredit:cn-s", 1000),)


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
    _with_unspecified(xbooks)
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
        status, last, unknown = c.execute("SELECT status, last_error, unknown FROM fin_outbox").fetchone()
        assert status == "failed" and last.startswith("UNKNOWN:") and unknown == 1
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
    assert nameless is not None and nameless.final and nameless.unknown
    assert nameless.error.startswith("UNKNOWN:")
    assert client.find("journal.delete", {"ext": "ManualJournal:mj-1"}, key) is None
    rows = []
    for n in range(250):
        row = _spend(f"bt-{n + 10}", [_line("acct-6100", "1.00")])
        row["UpdatedDateUTC"] = ms(_now())
        rows.append(row)
    xbooks.rows["BankTransactions"] = rows
    monkeypatch.setattr(xero, "FIND_PAGES", 2)
    capped = client.find("journal.create", {"feed": True}, "unseen-key")
    assert capped is not None and capped.final and capped.unknown
    assert capped.error.startswith("UNKNOWN: too many rows")


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
    assert not first.ok and first.error == "rate limit" and client.hold == 120.0
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
    assert mirror.sync_once(books, client, cid, AT, size=100) == []             # accounts
    assert mirror.sync_once(books, client, cid, AT, size=100) == []             # contacts, the first hundred
    row = next(r for r in xbooks.rows["Contacts"] if r["ContactID"] == "contact-060")
    xbooks.change("Contacts", {**row, "Name": "Acme Contact 060 renamed", "UpdatedDateUTC": ms(_now())})
    assert mirror.sync_once(books, client, cid, AT, size=100) == []             # the rest of the contacts
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE connection_id=? AND kind='Contact'",
                         (cid,)).fetchone()[0] == 150
    reads = [c for c in xbooks.calls if c["path"] == "Contacts" and c["method"] == "GET"]
    assert [c["query"]["page"] for c in reads] == ["1", "2"]
    assert {c["query"]["order"] for c in reads} == {xero.ORDER}


def test_a_change_window_longer_than_the_page_cap_is_cut_to_the_changes_read(monkeypatch):
    monkeypatch.setattr(xero, "MAX_PAGES", 2)
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n:03d}", "Name": f"Acme Contact {n:03d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE",
                                "UpdatedDateUTC": ms(f"2026-10-15T12:{n // 60:02d}:{n % 60:02d}Z")}
                               for n in range(250)]
    read = _client(xbooks).changed({"parties": "2026-10-15T11:55:00Z"})
    assert read.ok and len(read.records) == 199
    assert read.through == (("parties", "2026-10-15T12:03:17.999000Z"),)


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
    row = _spend("bt-tax", [_line(account_id, line, tax_text)], kind=kind, lat=lat)
    (journal,) = X.records("bank_transactions", [row], _no_system)
    by_account = {x.account: x.amount for x in journal.lines}
    assert by_account["Account:acct-1000"] == bank
    assert by_account[f"Account:{account_id}"] == expense
    assert by_account.get(M.TAX_ACCOUNT, 0) == tax
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


def test_a_journal_or_bank_line_sends_the_location_it_was_for_as_a_tracking_option():
    location = "TrackingOption:cat-loc/opt-austin"
    journal = X.journal_body({"date": "2026-10-05", "memo": "", "marker": "[tico:x]", "lines": [
        {"account": "Account:acct-1000", "amount": 1500, "description": "", "location": location, "classes": {}},
        {"account": "Account:acct-6100", "amount": -1500, "description": "", "location": location,
         "classes": {"class": "cat-team/opt-field"}}]})
    assert journal["JournalLines"][0]["Tracking"] == [
        {"TrackingCategoryID": "cat-loc", "TrackingOptionID": "opt-austin"}]
    assert journal["JournalLines"][1]["Tracking"] == [
        {"TrackingCategoryID": "cat-team", "TrackingOptionID": "opt-field"},
        {"TrackingCategoryID": "cat-loc", "TrackingOptionID": "opt-austin"}]
    bank = X.bank_body({"date": "2026-10-05", "memo": "", "marker": "[tico:x]", "lines": [
        {"account": "Account:acct-1000", "amount": -1500, "role": L.MONEY_ROLE},
        {"account": "Account:acct-6100", "amount": 1500, "role": "", "description": "tool", "location": location,
         "party": "Contact:contact-1"}]})
    assert bank["LineItems"][0]["Tracking"] == [{"TrackingCategoryID": "cat-loc", "TrackingOptionID": "opt-austin"}]


def test_an_archived_tracking_option_comes_in_inactive_and_archiving_or_renaming_it_changes_its_content():
    def category(status: str) -> dict[str, Any]:
        return {"TrackingCategoryID": "cat-1", "Name": "Team", "Status": "ACTIVE",
                "Options": [{"TrackingOptionID": "opt-1", "Name": "Field", "Status": status},
                            {"TrackingOptionID": "opt-2", "Name": "Office", "Status": "ACTIVE"}]}

    live = {r.ext.external_id: r for r in X.tracking_records(category("ACTIVE"), lambda key: "")}
    archived = {r.ext.external_id: r for r in X.tracking_records(category("ARCHIVED"), lambda key: "")}
    assert archived["cat-1/opt-1"].active is False and archived["cat-1/opt-2"].active is True
    assert archived["cat-1/opt-1"].ext.content != live["cat-1/opt-1"].ext.content
    assert archived["cat-1/opt-2"].ext.content == live["cat-1/opt-2"].ext.content


def test_a_payment_keeps_the_kind_its_type_names_even_once_deleted():
    stamp_ = ms("2026-10-15T00:00:00Z")
    bill_gone = _recorded({"PaymentID": "p-b", "Status": "DELETED", "PaymentType": "ACCPAYPAYMENT",
                                  "UpdatedDateUTC": stamp_})
    received_gone = _recorded({"PaymentID": "p-r", "Status": "DELETED", "PaymentType": "ACCRECPAYMENT",
                                      "UpdatedDateUTC": stamp_})
    untyped_gone = _recorded({"PaymentID": "p-u", "Status": "DELETED",
                                     "Invoice": {"InvoiceID": "bill-1", "Type": "ACCPAY"}, "UpdatedDateUTC": stamp_})
    assert [x.kind for x in (bill_gone, received_gone, untyped_gone)] == ["BillPayment", "Payment", "BillPayment"]
    assert all(isinstance(x, M.Ext) and x.deleted for x in (bill_gone, received_gone, untyped_gone))
    paid = _recorded({"PaymentID": "p-c", "Status": "AUTHORISED", "PaymentType": "ACCPAYPAYMENT",
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


def test_a_tax_exclusive_manual_journal_keeps_its_amounts_and_posts_its_tax_on_top(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    row = {**_journal("mj-tax", "100.00"), "LineAmountTypes": "Exclusive", "JournalLines": [
        {"LineAmount": "100.00", "TaxAmount": "10.00", "AccountID": "acct-6100", "Description": "tool"},
        {"LineAmount": "-110.00", "TaxAmount": "0.00", "AccountID": "acct-1000", "Description": "bank"}]}
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
    both, only_accrual = X.records("manual_journals", [plain, accrual], _no_system)
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
    xbooks.rows["Accounts"].append(_account_row("4900", "Rounding", "EXPENSE", "EXPENSE", "ROUNDING"))
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


# ------------------------------------------------------------------------------------------------ the books beyond the basics
def _recorded(row: dict[str, Any]) -> Any:
    """A payment mapped with no system account: for payments that name none."""
    return X.payment_record(row, _no_system)


def _refund(ident: str, kind: str, amount: str, status: str = "AUTHORISED",
            updated: str = "2026-10-12T00:00:00Z") -> dict[str, Any]:
    """A payment of a credit note back to a customer (ARCREDITPAYMENT) or to a supplier (APCREDITPAYMENT)."""
    return {"PaymentID": ident, "PaymentType": kind, "Status": status, "Date": ms("2026-10-12T00:00:00Z"),
            "DateString": "2026-10-12T00:00:00", "Amount": amount, "Account": {"AccountID": "acct-1000"},
            "CreditNote": {"CreditNoteID": f"cn-{ident}"}, "UpdatedDateUTC": ms(updated)}


def test_every_document_payment_write_off_and_void_is_sent_to_xero_and_answered_with_its_kind():
    xbooks = XeroBooks()
    _chart(xbooks)
    client = _client(xbooks)
    invoice = client.send("invoice.create", _doc_body("INV-A"), "key-inv-a")
    second = client.send("invoice.create", _doc_body("INV-B"), "key-inv-b")
    bill = client.send("bill.create", _doc_body("BILL-1"), "key-bill")
    voided = client.send("invoice.void", {"ext": f"Invoice:{second.external_id}", "revision": ""}, "key-void-inv")
    gone = client.send("bill.void", {"ext": f"Bill:{bill.external_id}", "revision": ""}, "key-void-bill")
    paid = client.send("payment.invoice", _pay_body(f"Invoice:{invoice.external_id}", 1000), "key-pay")
    paid_bill = client.send("payment.bill", _pay_body(f"Bill:{bill.external_id}", 1000), "key-pay-bill")
    credit = client.send("writeoff.create", {"party": "Contact:contact-1", "account": "Account:acct-6100",
                                             "day": "2026-10-10", "memo": "Bad debt", "total": 500,
                                             "invoice": f"Invoice:{invoice.external_id}"}, "key-writeoff")
    applied = client.send("credit.apply", {"invoice": f"Invoice:{invoice.external_id}",
                                           "credit": f"CreditMemo:{credit.external_id}", "party": "Contact:contact-1",
                                           "invoice_id": invoice.external_id, "amount": 500, "day": "2026-10-10",
                                           "memo": "Bad debt"}, "key-apply")
    sent = [invoice, second, bill, voided, gone, paid, paid_bill, credit, applied]
    assert all(s.ok for s in sent), [s.error for s in sent]
    assert [s.kind for s in sent] == ["Invoice", "Invoice", "Bill", "Invoice", "Bill", "Payment", "BillPayment",
                                      "CreditMemo", "Payment"]
    assert any(c["method"] == "PUT" and c["path"].endswith("/Allocations") for c in xbooks.calls)
    assert not any("has no write" in (s.error or "") for s in sent)


def test_a_lost_invoice_write_is_found_by_its_marker_and_bound_not_sent_again():
    xbooks = XeroBooks()
    _chart(xbooks)
    client = _client(xbooks)
    xbooks.lost = 1                                                   # the write lands; its answer never arrives
    lost = client.send("invoice.create", _doc_body("INV-L"), "key-lost-inv")
    assert not lost.ok and not lost.final and lost.error == "Xero did not answer"
    found = client.find("invoice.create", _doc_body("INV-L"), "key-lost-inv")
    assert found is not None and found.ok and found.kind == "Invoice" and found.external_id and found.revision
    assert client.find("invoice.create", _doc_body("INV-L"), "another-key") is None
    assert len(xbooks.rows["Invoices"]) == 1


def test_a_lost_payment_write_is_found_by_its_marker_and_bound_not_sent_again():
    xbooks = XeroBooks()
    _chart(xbooks)
    client = _client(xbooks)
    invoice = client.send("invoice.create", _doc_body("INV-P"), "key-inv-p")
    body = _pay_body(f"Invoice:{invoice.external_id}", 1000)
    xbooks.lost = 1
    lost = client.send("payment.invoice", body, "key-pay-lost")
    assert not lost.ok and not lost.final
    found = client.find("payment.invoice", body, "key-pay-lost")
    assert found is not None and found.ok and found.kind == "Payment" and found.external_id
    assert len(xbooks.rows["Payments"]) == 1


def test_a_write_older_than_thirty_days_is_still_found_by_its_marker():
    xbooks = XeroBooks()
    _chart(xbooks)
    key = "outbox-key-old"
    bank = _spend("bt-old", [_line("acct-6100", "15.00")])
    bank.update({"Reference": f"Software {xero._marker(key)}", "UpdatedDateUTC": ms("2020-01-01T00:00:00Z")})
    xbooks.rows["BankTransactions"] = [bank]
    found = _client(xbooks).find("journal.create", {"feed": True}, key)
    assert found is not None and found.ok and found.external_id == "bt-old"


def test_a_bank_row_with_no_counterparty_is_written_against_one_unspecified_contact_created_once():
    xbooks = XeroBooks()
    _chart(xbooks)
    lines = [{"account": "Account:acct-1000", "amount": -1500, "role": L.MONEY_ROLE, "party": ""},
             {"account": "Account:acct-6100", "amount": 1500, "role": "", "party": "", "description": "tool"}]
    body = {"date": "2026-10-05", "memo": "Acme Software", "source": "feed", "lines": lines, "export": True,
            "feed": True}
    sent = [_client(xbooks).send("journal.create", {**body, "memo": f"Acme Software {n}"}, f"key-bank-{n}")
            for n in (1, 2)]
    sent.append(_client(xbooks).send("journal.create", {**body, "memo": "Acme Software 3"}, "key-bank-3"))
    assert all(s.ok for s in sent), [s.error for s in sent]
    contact_puts = [c for c in xbooks.calls if c["path"] == "Contacts" and c["method"] == "PUT"]
    assert len(contact_puts) == 1
    (unspecified,) = [r for r in xbooks.rows["Contacts"] if r["Name"] == "Unspecified"]
    puts = [c for c in xbooks.calls if c["path"] == "BankTransactions" and c["method"] == "PUT"]
    assert len(puts) == 3
    assert all(p["body"]["BankTransactions"][0]["Contact"] == {"ContactID": unspecified["ContactID"]} for p in puts)
    raw = xbooks("PUT", BASE + "/BankTransactions", {"xero-tenant-id": TENANT, "Idempotency-Key": "raw-key"},
                 json.dumps({"BankTransactions": [{"Type": "SPEND", "BankAccount": {"AccountID": "acct-1000"},
                                                   "LineItems": [{"AccountID": "acct-6100", "LineAmount": "15.00"}]}]}
                            ).encode())
    assert raw.status == 400 and "contact" in json.dumps(raw.body).lower()


def test_a_negative_rounding_within_a_penny_is_a_rounding_line_and_a_larger_one_waits(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Accounts"].append(_account_row("4900", "Rounding", "EXPENSE", "EXPENSE", "ROUNDING"))
    xbooks.rows["Invoices"] = [{**_invoice("inv-neg"), "Total": "99.90", "RoundingAmount": "-0.10"},
                               {**_invoice("inv-big"), "Total": "99.89", "RoundingAmount": "-0.11"}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_invoices WHERE number='INV-inv-neg'").fetchone()[0] == 9990
        entry = c.execute("SELECT entry_id FROM fin_invoices WHERE number='INV-inv-neg'").fetchone()[0]
        lines = {r[0]: r[1] for r in c.execute("SELECT account_id, SUM(amount) FROM fin_lines WHERE entry_id=? "
                                               "GROUP BY account_id", (entry,))}
        assert sum(lines.values()) == 0
        assert lines[account(c, "4000")] == -10000 and lines[account(c, "4900")] == 10
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Invoice:inv-big'").fetchone()[0] \
            == "unmapped"


def test_a_bank_transfer_is_one_entry_and_its_legs_are_not_counted_again(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Accounts"].append(_account_row("1010", "Savings", "ASSET", "BANK"))
    xbooks.rows["BankTransactions"] = [
        _spend("bt-out", [], kind="SPEND-TRANSFER"),
        {**_spend("bt-in", [], kind="RECEIVE-TRANSFER"), "BankAccount": {"AccountID": "acct-1010"}}]
    xbooks.rows["BankTransfers"] = [{"BankTransferID": "bxf-1", "Status": "AUTHORISED",
                                     "CreatedDateUTC": ms("2026-10-05T00:00:00Z"), "Date": ms("2026-10-05T00:00:00Z"),
                                     "Amount": "100.00", "FromBankAccount": {"AccountID": "acct-1000"},
                                     "ToBankAccount": {"AccountID": "acct-1010"}}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_external_refs WHERE external_id LIKE 'BankTransaction:%'") \
            .fetchone()[0] == 0
        entry = c.execute("SELECT local_id FROM fin_external_refs WHERE external_id='BankTransfer:bxf-1'").fetchone()[0]
        lines = {r[0]: r[1] for r in c.execute("SELECT account_id, amount FROM fin_lines WHERE entry_id=?", (entry,))}
        assert lines == {account(c, "1010"): 10000, account(c, "1000"): -10000}
    xbooks.calls.clear()
    _client(xbooks).changed({"changes": "2026-10-15T11:55:00Z"})
    (read,) = [c for c in xbooks.calls if c["method"] == "GET" and c["path"] == "BankTransfers"]
    assert read["query"].get("includeDeleted") == "true" and "If-Modified-Since" not in read["headers"]


def test_a_refund_of_a_credit_note_is_a_journal_that_moves_the_bank_once(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["CreditNotes"] = [{**_credit_note("cn-pay-ar", "", "50.00"), "Allocations": []},
                                  {**_credit_note("cn-pay-ap", "", "30.00", kind="ACCPAYCREDIT"), "Allocations": []}]
    xbooks.rows["Payments"] = [_refund("pay-ar", "ARCREDITPAYMENT", "50.00"),
                               _refund("pay-ap", "APCREDITPAYMENT", "30.00")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        # Each credit memo and its refund net to nothing on their control line; the bank pays out 50 and takes 30 in.
        sums = {r[0]: r[1] for r in c.execute("SELECT account_id, SUM(amount) FROM fin_lines GROUP BY account_id")}
        assert sums == {account(c, "1200"): 0, account(c, "2000"): 0, account(c, "1000"): -2000,
                        account(c, "4000"): 5000, account(c, "6100"): -3000}
        assert [r[0] for r in c.execute("SELECT DISTINCT source FROM fin_entries WHERE source_id LIKE 'Payment:%'")] \
            == ["sync"]
    xbooks.change("Payments", _refund("pay-ar", "ARCREDITPAYMENT", "50.00", status="DELETED",
                                      updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT COALESCE(SUM(amount), 0) FROM fin_lines WHERE account_id=?",
                         (account(c, "1200"),)).fetchone()[0] == -5000
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?",
                         (account(c, "1000"),)).fetchone()[0] == 3000


def test_refunds_post_against_their_control_account_and_the_bank_in_opposite_directions():
    def system(name: str) -> str:
        return {"DEBTORS": "Account:acct-1200", "CREDITORS": "Account:acct-2000"}.get(name, "")

    ar = X.payment_record(_refund("pay-ar", "ARCREDITPAYMENT", "50.00"), system)
    ap = X.payment_record(_refund("pay-ap", "APCREDITPAYMENT", "30.00"), system)
    assert isinstance(ar, M.Journal) and ar.source == "sync"
    assert {x.account: x.amount for x in ar.lines} == {"Account:acct-1200": 5000, "Account:acct-1000": -5000}
    assert {x.account: x.amount for x in ap.lines} == {"Account:acct-2000": -3000, "Account:acct-1000": 3000}


def test_refunding_an_overpayment_is_a_journal_that_moves_the_bank_once(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["BankTransactions"] = [{
        "BankTransactionID": "bt-ov", "Type": "RECEIVE-OVERPAYMENT", "Status": "AUTHORISED",
        "Date": ms("2026-10-08T00:00:00Z"), "DateString": "2026-10-08T00:00:00",
        "BankAccount": {"AccountID": "acct-1000"}, "Contact": {"ContactID": "contact-1"}, "CurrencyCode": "USD",
        "LineAmountTypes": "NoTax", "LineItems": [], "Total": "50.00", "OverpaymentID": "ov-1",
        "UpdatedDateUTC": ms("2026-10-08T00:00:00Z")}]
    xbooks.rows["Payments"] = [{"PaymentID": "pay-ov-r", "PaymentType": "AROVERPAYMENTPAYMENT", "Status": "AUTHORISED",
                                "Date": ms("2026-10-09T00:00:00Z"), "DateString": "2026-10-09T00:00:00",
                                "Amount": "50.00", "Account": {"AccountID": "acct-1000"},
                                "Overpayment": {"OverpaymentID": "ov-1"}, "UpdatedDateUTC": ms("2026-10-09T00:00:00Z")}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        # The overpayment came in and goes back out: the bank nets to nothing and no invoice is settled.
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "1000"),)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fin_settlements WHERE voided_at IS NULL").fetchone()[0] == 0
        assert [r[0] for r in c.execute("SELECT DISTINCT source FROM fin_entries WHERE source_id LIKE 'Payment:%'")] \
            == ["sync"]


def test_overpayment_and_prepayment_refunds_are_journals_against_their_control_account_like_credit_refunds():
    def system(name: str) -> str:
        return {"DEBTORS": "Account:acct-1200", "CREDITORS": "Account:acct-2000"}.get(name, "")

    for ptype, control, sign, field in (("AROVERPAYMENTPAYMENT", "acct-1200", 1, "Overpayment"),
                                        ("ARPREPAYMENTPAYMENT", "acct-1200", 1, "Prepayment"),
                                        ("APOVERPAYMENTPAYMENT", "acct-2000", -1, "Overpayment"),
                                        ("APPREPAYMENTPAYMENT", "acct-2000", -1, "Prepayment")):
        row = {"PaymentID": f"pay-{ptype}", "PaymentType": ptype, "Status": "AUTHORISED",
               "Date": ms("2026-10-12T00:00:00Z"), "DateString": "2026-10-12T00:00:00", "Amount": "50.00",
               "Account": {"AccountID": "acct-1000"}, field: {f"{field}ID": f"{field.lower()}-1"},
               "UpdatedDateUTC": ms("2026-10-12T00:00:00Z")}
        out = X.payment_record(row, system)
        assert isinstance(out, M.Journal) and out.refund_of == "", ptype
        assert {x.account: x.amount for x in out.lines} == {f"Account:{control}": sign * 5000,
                                                              "Account:acct-1000": -sign * 5000}


def test_an_unknown_payment_type_waits_unmapped_and_the_read_moves_past_it(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    xbooks.rows["Payments"] = [{**_payment("pay-x", "inv-1", "10.00"), "PaymentType": "XYZ"},
                               _payment("pay-ok", "inv-1", "100.00")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Payment:pay-x'").fetchone()[0] \
            == "unmapped"
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()) \
            == ("paid", 10000)
        assert not mirror.reading(c, cid)


def test_a_payment_in_another_currency_waits_unmapped(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    xbooks.rows["Payments"] = [{**_payment("pay-eur", "inv-1", "100.00"), "BankAmount": "90.00"}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Payment:pay-eur'").fetchone()[0] \
            == "unmapped"
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()) \
            == ("sent", 0)


def test_a_payment_in_another_currency_or_of_an_unknown_type_is_refused_by_the_mapping():
    row = _payment("p-fx", "inv-1", "100.00")
    assert isinstance(_recorded({**row, "CurrencyRate": "1.1"}), M.Refused)
    assert isinstance(_recorded({**row, "BankAmount": "90.00"}), M.Refused)
    assert isinstance(_recorded({**row, "PaymentType": "XYZ"}), M.Refused)


def test_a_tax_inclusive_and_exclusive_manual_journal_each_balance_with_its_tax_on_the_tax_account():
    bank = {"LineAmount": "-110.00", "TaxAmount": "0.00", "AccountID": "acct-1000", "Description": "bank"}
    exclusive = {**_journal("mj-ex", "100.00"), "LineAmountTypes": "Exclusive", "JournalLines": [
        {"LineAmount": "100.00", "TaxAmount": "10.00", "AccountID": "acct-6100", "Description": "tool"}, bank]}
    inclusive = {**_journal("mj-in", "100.00"), "LineAmountTypes": "Inclusive", "JournalLines": [
        {"LineAmount": "110.00", "TaxAmount": "10.00", "AccountID": "acct-6100", "Description": "tool"}, bank]}
    for row in (exclusive, inclusive):
        journal = X.manual_journal_record(row, _no_system)
        by_account = {x.account: x.amount for x in journal.lines}
        assert by_account == {"Account:acct-6100": 10000, M.TAX_ACCOUNT: 1000, "Account:acct-1000": -11000}


def test_account_body_sends_a_bank_account_as_xeros_bank_type():
    assert X.account_body({"code": "1010", "name": "Savings", "type": "asset", "subtype": "bank"})["Type"] == "BANK"
    receivable = X.account_body({"code": "1200", "name": "Customers", "type": "asset", "subtype": "receivable"})
    assert receivable["Type"] == "CURRENT"
    xbooks = XeroBooks()
    _chart(xbooks)
    made = _client(xbooks).send("account.create", {"code": "1010", "name": "Savings", "type": "asset",
                                                   "subtype": "bank", "active": True}, "key-bank-account")
    assert made.ok and made.kind == "Account"
    put = [c for c in xbooks.calls if c["path"] == "Accounts" and c["method"] == "PUT"][-1]
    assert put["body"]["Type"] == "BANK" and put["body"]["BankAccountType"] == "BANK"


def test_a_credit_note_voided_after_it_settled_an_invoice_unwinds_it(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.change("CreditNotes", _credit_note("cn-v", "inv-1", "100.00", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    xbooks.change("CreditNotes", {**_credit_note("cn-v", "inv-1", "100.00", updated="2026-10-15T13:30:00Z"),
                                  "Status": "VOIDED"})
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()) \
            == ("sent", 0)
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='CreditMemo:cn-v'").fetchone()[0] \
            == "deleted"


def test_a_backfill_page_of_a_thousand_is_read_as_ten_xero_pages():
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n:04d}", "Name": f"Acme Contact {n:04d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE", "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")}
                               for n in range(1050)]
    client = _client(xbooks)
    first = client.page("parties", 1, 1000)
    assert first.ok and first.seen == 1000 and len(first.records) == 1000
    gets = [c for c in xbooks.calls if c["method"] == "GET" and c["path"] == "Contacts"]
    assert len(gets) == 10
    last = client.page("parties", 1001, 1000)
    assert last.ok and last.seen == 50


def test_a_rate_limit_that_outlasts_a_pass_holds_the_books_until_retry_after_passes(books, monkeypatch):
    xbooks = XeroBooks()
    _chart(xbooks)
    limited = [True]
    calls: list[str] = []

    def transport(method: str, url: str, headers: Mapping[str, str], data: bytes | None) -> xero.Reply:
        calls.append(url)
        if limited[0]:
            return xero.Reply(429, {"Message": "rate limit"}, 3600.0)
        return xbooks(method, url, headers, data)

    cid = _pass_setup(books, xbooks, monkeypatch, transport=transport)
    held = M.stamp(AT, 3600_000_000)
    first = books_sync.pass_once(books, object(), None, now=AT)
    assert first and "rate limit" in first[0]
    with books.read() as c:
        assert SX.mirror_state(c, cid)["held_until"] == held
    made = len(calls)
    waiting = books_sync.pass_once(books, object(), None, now=M.stamp(AT, 60_000_000))
    assert waiting == [f"Xero is rate limiting; the books wait until {held}"]
    assert len(calls) == made
    limited[0] = False
    assert books_sync.pass_once(books, object(), None, now=M.stamp(AT, 3601_000_000)) == []
    assert len(calls) > made
    with books.read() as c:
        assert SX.mirror_state(c, cid).get("held_until", "") == ""


def test_the_xero_pass_reads_its_change_feed_at_least_every_five_minutes(books, monkeypatch):
    assert 0 < xero.CHANGE_EVERY <= 300
    xbooks = XeroBooks()
    _chart(xbooks)
    _pass_setup(books, xbooks, monkeypatch)
    seen: list[dict[str, Any]] = []
    real = mirror.sync_once

    def spy(*args: Any, **kwargs: Any) -> list[str]:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(mirror, "sync_once", spy)
    assert books_sync.pass_once(books, object(), None, now=AT) == []
    assert seen and all(kwargs.get("every") == xero.CHANGE_EVERY for kwargs in seen)


def _cash_pnl(c: Any) -> int:
    """Income on the cash basis: the accrual lines plus the cash view's corrections."""
    return c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id = l.account_id "
                     "WHERE a.type = 'income'").fetchone()[0] + \
        c.execute("SELECT COALESCE(SUM(r.amount), 0) FROM fin_cash_rollups r JOIN fin_accounts a "
                  "ON a.id = r.account_id "
                  "WHERE a.type = 'income'").fetchone()[0]


def test_a_credit_note_refund_brings_its_share_of_the_credit_into_the_cash_basis_as_it_is_paid_back(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    xbooks.rows["Payments"] = [_payment("pay-1", "inv-1", "100.00")]
    xbooks.rows["CreditNotes"] = [{**_credit_note("cn-1", "inv-1", "100.00"), "Allocations": []}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        # The receipt is revenue on the cash basis; the credit note is left out until money goes back.
        assert _cash_pnl(c) == -10000

    def refund(ident: str, amount: str, updated: str) -> dict[str, Any]:
        return {**_refund(ident, "ARCREDITPAYMENT", amount, updated=updated), "CreditNote": {"CreditNoteID": "cn-1"}}

    xbooks.change("Payments", refund("pay-r1", "40.00", "2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert _cash_pnl(c) == -6000
    xbooks.change("Payments", refund("pay-r2", "60.00", "2026-10-15T13:30:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert _cash_pnl(c) == 0
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?",
                         (account(c, "1000"),)).fetchone()[0] == 0


def test_a_refund_taken_back_and_a_revised_credit_note_keep_the_cash_basis_exact(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    xbooks.rows["Payments"] = [_payment("pay-1", "inv-1", "100.00")]
    xbooks.rows["CreditNotes"] = [{**_credit_note("cn-1", "inv-1", "100.00"), "Allocations": []}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []

    def refund(status: str, updated: str) -> dict[str, Any]:
        return {**_refund("pay-r1", "ARCREDITPAYMENT", "40.00", status=status, updated=updated),
                "CreditNote": {"CreditNoteID": "cn-1"}}

    xbooks.change("Payments", refund("AUTHORISED", "2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert _cash_pnl(c) == -6000
    xbooks.change("CreditNotes", {**_credit_note("cn-1", "inv-1", "100.00", updated="2026-10-15T13:50:00Z"),
                                  "Allocations": []})
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert _cash_pnl(c) == -6000
    xbooks.change("Payments", refund("DELETED", "2026-10-15T14:30:00Z"))
    assert _drain(books, _client(xbooks), cid, now="2026-10-15T15:00:00.000000Z", passes=1) == []
    with books.read() as c:
        assert _cash_pnl(c) == -10000


def _held(ident: str, type_: str, allocations: list[tuple[str, str, str]], *, updated: str = "2026-10-10T00:00:00Z",
          status: str = "AUTHORISED") -> dict[str, Any]:
    """An overpayment or a prepayment (by its type) with the amounts it has applied: each allocation is (document id,
    its type, amount)."""
    key = "PrepaymentID" if "PREPAYMENT" in type_ else "OverpaymentID"
    return {key: ident, "Type": type_, "Status": status, "Date": ms("2026-10-10T00:00:00Z"),
            "DateString": "2026-10-10T00:00:00", "Contact": {"ContactID": "contact-1"}, "CurrencyCode": "USD",
            "Allocations": [{"AllocationID": f"alloc-{ident}-{n}", "Invoice": {"InvoiceID": doc, "Type": side},
                             "Amount": amount, "Date": ms("2026-10-10T00:00:00Z")}
                            for n, (doc, side, amount) in enumerate(allocations)],
            "UpdatedDateUTC": ms(updated)}


def _held_bank(ident: str, type_: str, amount: str) -> dict[str, Any]:
    """The money an overpayment or a prepayment came in or went out with, as the bank transaction that took it."""
    key = "PrepaymentID" if "PREPAYMENT" in type_ else "OverpaymentID"
    return {"BankTransactionID": f"bt-{ident}", "Type": type_, "Status": "AUTHORISED",
            "Date": ms("2026-10-08T00:00:00Z"), "DateString": "2026-10-08T00:00:00",
            "BankAccount": {"AccountID": "acct-1000"}, "Contact": {"ContactID": "contact-1"}, "CurrencyCode": "USD",
            "LineAmountTypes": "NoTax", "LineItems": [], "Total": amount, key: ident,
            "UpdatedDateUTC": ms("2026-10-08T00:00:00Z")}


def test_an_overpayment_or_prepayment_settles_the_documents_its_allocations_name_until_they_are_removed(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("bill-1", kind="ACCPAY")]
    xbooks.rows["BankTransactions"] = [_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00"),
                                       _held_bank("pp-1", "SPEND-PREPAYMENT", "50.00")]
    xbooks.rows["Overpayments"] = [_held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00")])]
    xbooks.rows["Prepayments"] = [_held("pp-1", "SPEND-PREPAYMENT", [("bill-1", "ACCPAY", "50.00")])]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()) \
            == ("paid", 10000)
        assert c.execute("SELECT amount_paid FROM fin_bills WHERE number='INV-bill-1'").fetchone()[0] == 5000
    xbooks.change("Overpayments", _held("ov-1", "RECEIVE-OVERPAYMENT", [], updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert tuple(c.execute("SELECT status, amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()) \
            == ("sent", 0)
        # Applying money moves none: the bank still holds what the overpayment and the prepayment took in and paid out.
        assert c.execute("SELECT SUM(amount) FROM fin_lines WHERE account_id=?", (account(c, "1000"),)).fetchone()[0] \
            == 5000


def test_a_credit_note_whose_allocations_are_all_removed_is_a_revision_and_can_be_applied_again(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("inv-2")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.change("CreditNotes", _credit_note("cn-r", "inv-1", "40.00", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    xbooks.change("CreditNotes", {**_credit_note("cn-r", "inv-1", "40.00", updated=LATER2[:-1] + "0Z"),
                                  "Allocations": []})
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0] == 0
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Payment:CreditNote:cn-r'") \
            .fetchone()[0] == "active"
    xbooks.change("CreditNotes", _credit_note("cn-r", "inv-2", "40.00", updated="2026-10-15T14:30:00Z"))
    assert _drain(books, _client(xbooks), cid, now="2026-10-15T14:30:00.000000Z", passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-2'").fetchone()[0] == 4000
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0] == 0


def test_an_unallocated_credit_note_whose_memo_waits_leaves_no_application_waiting(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.change("CreditNotes", {**_credit_note("cn-eur", "inv-1", "40.00", updated="2026-10-15T12:45:00Z"),
                                  "LineItems": [_line("acct-missing", "40.00")], "Allocations": []})
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT status FROM fin_external_refs WHERE external_id='Payment:CreditNote:cn-eur'") \
            .fetchone()[0] == "active"


def test_an_explicit_negative_line_on_the_rounding_account_is_taken_in_as_rounding(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Accounts"].append(_account_row("4900", "Rounding", "EXPENSE", "EXPENSE", "ROUNDING"))
    xbooks.rows["Invoices"] = [{**_invoice("inv-x", total="99.99"),
                                "LineItems": [_line("acct-4000", "100.00"), _line("acct-4900", "-0.01")]}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_invoices WHERE number='INV-inv-x'").fetchone()[0] == 9999
        entry = c.execute("SELECT entry_id FROM fin_invoices WHERE number='INV-inv-x'").fetchone()[0]
        lines = {r[0]: r[1] for r in c.execute("SELECT account_id, SUM(amount) FROM fin_lines WHERE entry_id=? "
                                               "GROUP BY account_id", (entry,))}
        assert sum(lines.values()) == 0
        assert lines[account(c, "4900")] == 1


def test_a_deleted_allocation_of_an_overpayment_applies_nothing(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("inv-2")]
    xbooks.rows["BankTransactions"] = [_held_bank("ov-d", "RECEIVE-OVERPAYMENT", "100.00")]
    held = _held("ov-d", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00"), ("inv-2", "ACCREC", "40.00")])
    held["Allocations"][1]["IsDeleted"] = True
    xbooks.rows["Overpayments"] = [held]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0] == 10000
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-2'").fetchone()[0] == 0


def test_one_entitys_page_cap_does_not_hold_back_another_entitys_changes(books, monkeypatch):
    monkeypatch.setattr(xero, "MAX_PAGES", 2)
    xbooks = XeroBooks()
    _chart(xbooks)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.rows["Contacts"] = [{"ContactID": f"contact-{n:03d}", "Name": f"Acme Contact {n:03d}", "IsCustomer": True,
                                "ContactStatus": "ACTIVE",
                                "UpdatedDateUTC": ms(f"2026-10-15T12:{n // 60:02d}:{n % 60:02d}Z")}
                               for n in range(250)]
    xbooks.rows["Invoices"] = [_invoice("inv-x", updated="2026-10-15T12:30:00Z")]
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_invoices WHERE number='INV-inv-x'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM fin_contacts WHERE name LIKE 'Acme Contact %'").fetchone()[0] == 199
        cursors = SX.mirror_state(c, cid)["cursors"]
    # The capped entity is complete to just before the last change it read; the other moves on to now.
    assert cursors["parties"] == "2026-10-15T12:03:17.999000Z"
    assert cursors["invoices"] == LATER
    monkeypatch.setattr(xero, "MAX_PAGES", 200)
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=3) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_contacts WHERE name LIKE 'Acme Contact %'").fetchone()[0] == 250


def test_a_row_edited_between_two_pages_of_a_change_read_is_not_skipped(monkeypatch):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice(f"inv-{n:03d}", updated=f"2026-10-15T12:{n // 60:02d}:{n % 60:02d}Z")
                               for n in range(150)]
    read_list = xbooks._list
    edited: list[bool] = []

    def list_then_edit(name: str, query: Mapping[str, str], since: str) -> xero.Reply:
        reply = read_list(name, query, since)
        if name == "Invoices" and not edited:          # the first page is answered, then its first invoice is edited
            edited.append(True)
            xbooks.change("Invoices", _invoice("inv-000", updated="2026-10-15T14:00:00Z", total="200.00"))
        return reply

    monkeypatch.setattr(xbooks, "_list", list_then_edit)
    read = _client(xbooks).changed({"invoices": "2026-10-15T11:00:00Z"})
    assert read.ok and edited
    by_key = {r.ext.key: r for r in read.records}
    assert set(by_key) == {f"Invoice:inv-{n:03d}" for n in range(150)}
    assert by_key["Invoice:inv-000"].total == 20000


def test_a_capped_change_window_is_read_through_over_later_passes_without_stalling(books, monkeypatch):
    monkeypatch.setattr(xero, "MAX_PAGES", 2)
    xbooks = XeroBooks()
    _chart(xbooks)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.rows["Invoices"] = [_invoice(f"inv-{n:03d}", updated=f"2026-10-15T12:{n // 60:02d}:{n % 60:02d}Z")
                               for n in range(250)]
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=3) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_invoices WHERE number LIKE 'INV-inv-%'").fetchone()[0] == 250


def test_a_page_of_one_timestamp_moves_on_by_page_and_every_row_is_read():
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice(f"inv-{n:03d}", updated="2026-10-15T12:00:00Z") for n in range(250)]
    read = _client(xbooks).changed({"invoices": "2026-10-15T11:00:00Z"})
    assert read.ok and read.through == ()
    assert {r.ext.key for r in read.records} == {f"Invoice:inv-{n:03d}" for n in range(250)}
    assert sum(1 for c in xbooks.calls if c["path"] == "Invoices") == 4


def _later_than_now() -> str:
    """A time after the ones the fake gives its writes, so an edit made after a write reads as the newer record."""
    return ms((datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


def test_a_write_lost_and_then_edited_in_xero_is_recovered_as_the_edit_when_the_retry_finds_it(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid, client, fid = _categorized(books, xbooks)
    xbooks.lost = 1                                                   # the write lands; its answer never arrives
    assert mirror.sync_once(books, client, cid, LATER) == ["journal.create: Xero did not answer"]
    (bank,) = xbooks.rows["BankTransactions"]
    xbooks.change("BankTransactions", {**bank, "LineItems": [{**bank["LineItems"][0], "LineAmount": "20.00"}],
                                       "UpdatedDateUTC": _later_than_now()})     # edited before the retry
    assert mirror.sync_once(books, client, cid, RETRY_AT) == []
    assert _drain(books, client, cid, now=RETRY_AT, passes=2) == []
    _assert_recovered_as_edited(books, fid, xbooks)


def test_a_write_lost_and_then_edited_in_xero_is_recovered_from_the_change_feed_as_the_edit(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    cid, client, fid = _categorized(books, xbooks)
    xbooks.lost = 1
    assert mirror.sync_once(books, client, cid, LATER) == ["journal.create: Xero did not answer"]
    (bank,) = xbooks.rows["BankTransactions"]
    xbooks.change("BankTransactions", {**bank, "LineItems": [{**bank["LineItems"][0], "LineAmount": "20.00"}],
                                       "UpdatedDateUTC": _later_than_now()})
    assert _drain(books, client, cid, now=LATER2, passes=2) == []     # the feed brings the marked edit back first
    _assert_recovered_as_edited(books, fid, xbooks)


def _assert_recovered_as_edited(books: Any, fid: str, xbooks: XeroBooks) -> None:
    with books.read() as c:
        status, entry = c.execute("SELECT status, entry_id FROM fin_feed_txns WHERE id=?", (fid,)).fetchone()
        expense = c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a "
                            "ON a.id = l.account_id WHERE l.entry_id=? AND a.type='expense'",
                            (entry,)).fetchone()[0]
    assert status == "posted" and expense == 2000
    assert len(_bank_puts(xbooks)) == 1


def test_a_revised_credit_note_keeps_its_applications_on_the_invoice_and_points_them_at_its_new_entry(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.change("CreditNotes", _credit_note("cn-1", "inv-1", "100.00", updated="2026-10-15T12:45:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    xbooks.change("CreditNotes", _credit_note("cn-1", "inv-1", "100.00", updated="2026-10-15T13:30:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        entry = c.execute("SELECT local_id FROM fin_external_refs WHERE external_id='CreditMemo:cn-1'").fetchone()[0]
        applied = c.execute("SELECT from_id, entry_id FROM fin_settlements "
                            "WHERE from_kind='credit' AND voided_at IS NULL").fetchall()
        paid = c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0]
    assert [tuple(r) for r in applied] == [(entry, entry)]
    assert paid == 10000


def test_an_overpayment_applied_in_an_earlier_pass_stays_applied_when_its_journal_is_revised_in_a_later_one(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1")]
    xbooks.rows["BankTransactions"] = [_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00")]
    xbooks.rows["Overpayments"] = [_held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00")])]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        cash_before = _cash_pnl(c)
    # The application is revised in one pass (its allocation stays), its journal in a later one.
    xbooks.change("Overpayments", _held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00")],
                                        updated="2026-10-15T13:00:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER, passes=1) == []
    xbooks.change("BankTransactions", {**_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00"), "Reference": "revised",
                                       "UpdatedDateUTC": ms("2026-10-15T13:30:00Z")})
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0] == 10000
        assert _cash_pnl(c) == cash_before
        revisions = dict(c.execute("SELECT external_id, revision FROM fin_external_refs "
                                   "WHERE external_id LIKE '%ov-1'").fetchall())
    assert revisions == {"Overpayment:ov-1": "2026-10-15T13:30:00.000Z",
                         "Payment:Overpayment:ov-1": "2026-10-15T13:00:00.000Z"}


def _cash_expense(c: Any) -> int:
    """Expense on the cash basis: the accrual lines plus the cash view's corrections."""
    return c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_accounts a ON a.id = l.account_id "
                     "WHERE a.type = 'expense'").fetchone()[0] + \
        c.execute("SELECT COALESCE(SUM(r.amount), 0) FROM fin_cash_rollups r JOIN fin_accounts a "
                  "ON a.id = r.account_id "
                  "WHERE a.type = 'expense'").fetchone()[0]


def test_an_overpayment_or_prepayment_applied_to_documents_recognizes_its_cash_as_a_payment_of_them_would(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("bill-1", kind="ACCPAY")]
    xbooks.rows["BankTransactions"] = [_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00"),
                                       _held_bank("pp-1", "SPEND-PREPAYMENT", "50.00")]
    xbooks.rows["Overpayments"] = [_held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00")])]
    xbooks.rows["Prepayments"] = [_held("pp-1", "SPEND-PREPAYMENT", [("bill-1", "ACCPAY", "50.00")])]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        # The money came in with the overpayment and went out with the prepayment: applying each to a document
        # recognizes it on the cash basis, as cash received or paid for that document would.
        assert _cash_pnl(c) == -10000
        assert _cash_expense(c) == 5000


def test_a_revised_overpayment_or_prepayment_with_the_same_allocations_stays_applied_to_its_documents(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("bill-1", kind="ACCPAY")]
    xbooks.rows["BankTransactions"] = [_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00"),
                                       _held_bank("pp-1", "SPEND-PREPAYMENT", "50.00")]
    xbooks.rows["Overpayments"] = [_held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00")])]
    xbooks.rows["Prepayments"] = [_held("pp-1", "SPEND-PREPAYMENT", [("bill-1", "ACCPAY", "50.00")])]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        before = (_cash_pnl(c), _cash_expense(c))
    # The money is revised (a new note) and the overpayment and prepayment keep the allocations they had.
    xbooks.change("BankTransactions", {**_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00"), "Reference": "revised",
                                       "UpdatedDateUTC": ms("2026-10-15T13:30:00Z")})
    xbooks.change("BankTransactions", {**_held_bank("pp-1", "SPEND-PREPAYMENT", "50.00"), "Reference": "revised",
                                       "UpdatedDateUTC": ms("2026-10-15T13:30:00Z")})
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0] == 10000
        assert c.execute("SELECT amount_paid FROM fin_bills WHERE number='INV-bill-1'").fetchone()[0] == 5000
        assert (_cash_pnl(c), _cash_expense(c)) == before


def test_a_revised_overpayment_that_moves_its_allocation_settles_the_new_document_only(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Invoices"] = [_invoice("inv-1"), _invoice("inv-2")]
    xbooks.rows["BankTransactions"] = [_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00")]
    xbooks.rows["Overpayments"] = [_held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-1", "ACCREC", "100.00")])]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    xbooks.change("BankTransactions", {**_held_bank("ov-1", "RECEIVE-OVERPAYMENT", "100.00"), "Reference": "revised",
                                       "UpdatedDateUTC": ms("2026-10-15T13:30:00Z")})
    xbooks.change("Overpayments", _held("ov-1", "RECEIVE-OVERPAYMENT", [("inv-2", "ACCREC", "100.00")],
                                        updated="2026-10-15T13:30:00Z"))
    assert _drain(books, _client(xbooks), cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-1'").fetchone()[0] == 0
        assert c.execute("SELECT amount_paid FROM fin_invoices WHERE number='INV-inv-2'").fetchone()[0] == 10000


def test_an_overpayment_allocation_whose_invoice_omits_its_type_names_the_side_its_overpayment_is_on():
    received = {**_held("ov-9", "RECEIVE-OVERPAYMENT", [("inv-9", "ACCREC", "10.00")]),
                "Allocations": [{"Invoice": {"InvoiceID": "inv-9"}, "Amount": "10.00"}]}
    out = X.applied_record(received, "Overpayment")
    assert isinstance(out, M.Settlement) and out.links == (("Invoice:inv-9", 1000),)
    spent = {**received, "OverpaymentID": "ov-8", "Type": "SPEND-OVERPAYMENT"}
    out = X.applied_record(spent, "Overpayment")
    assert isinstance(out, M.Settlement) and out.links == (("Bill:inv-9", 1000),)


def test_a_full_refund_of_a_credit_note_with_a_negative_rounding_line_recognizes_the_whole_credit_as_cash(books):
    xbooks = XeroBooks()
    _chart(xbooks)
    xbooks.rows["Accounts"].append(_account_row("4900", "Rounding", "EXPENSE", "EXPENSE", "ROUNDING"))
    xbooks.rows["CreditNotes"] = [{**_credit_note("cn-r", "", "99.99"), "Allocations": [],
                                   "LineItems": [_line("acct-4000", "100.00"), _line("acct-4900", "-0.01")]}]
    xbooks.rows["Payments"] = [{**_refund("pay-r", "ARCREDITPAYMENT", "99.99"), "CreditNote": {"CreditNoteID": "cn-r"}}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        # Posting the credit note takes its P&L out of the cash view; the full refund puts all of it back on its day.
        credit = c.execute("SELECT COALESCE(SUM(l.amount), 0) FROM fin_lines l JOIN fin_entries e ON e.id = l.entry_id "
                           "JOIN fin_accounts a ON a.id = l.account_id WHERE e.source_id LIKE '%cn-r%' "
                           "AND a.type IN ('income', 'expense')").fetchone()[0]
        refund_day = c.execute("SELECT day FROM fin_credit_refunds").fetchone()[0]
        recognized = c.execute("SELECT COALESCE(SUM(r.amount), 0) FROM fin_cash_rollups r JOIN fin_accounts a "
                               "ON a.id = r.account_id WHERE a.type IN ('income', 'expense') AND r.day = ?",
                               (refund_day,)).fetchone()[0]
        rounding = c.execute("SELECT COALESCE(SUM(r.amount), 0) FROM fin_cash_rollups r JOIN fin_accounts a "
                             "ON a.id = r.account_id WHERE a.code = '4900' AND r.day = ?", (refund_day,)).fetchone()[0]
        assert abs(credit) == 9999 and recognized == credit and rounding == -1


def _local(c: Any, ext: str, table: str) -> str:
    """The local record a provider record is bound to."""
    return str(c.execute("SELECT local_id FROM fin_external_refs WHERE external_id=? AND local_table=?",
                         (ext, table)).fetchone()[0])


def _tracked_chart(xbooks: XeroBooks) -> None:
    """The chart, an item sold on account 4000 and bought on 6100, and two tracking categories: the location one and a
    team one (which the books take as classes)."""
    _chart(xbooks)
    xbooks.rows["Items"] = [{"ItemID": "item-1", "Code": "CONSULT", "Name": "Consulting", "IsTrackedAsInventory": False,
                             "SalesDetails": {"UnitPrice": 150.0, "AccountCode": "4000"},
                             "PurchaseDetails": {"AccountCode": "6100"}, "UpdatedDateUTC": ms("2026-10-01T00:00:00Z")}]
    xbooks.rows["TrackingCategories"] = [
        {"TrackingCategoryID": "cat-loc", "Name": "Location", "Status": "ACTIVE",
         "Options": [{"TrackingOptionID": "opt-austin", "Name": "Austin", "Status": "ACTIVE"}]},
        {"TrackingCategoryID": "cat-team", "Name": "Team", "Status": "ACTIVE",
         "Options": [{"TrackingOptionID": "opt-field", "Name": "Field", "Status": "ACTIVE"}]}]


def test_xero_items_and_tracking_options_come_in_and_a_line_takes_its_location_and_class(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    tracked = [{"Name": "Location", "Option": "Austin"}, {"Name": "Team", "Option": "Field"}]
    xbooks.rows["Invoices"] = [{**_invoice("inv-1"), "LineItems": [{**_line("acct-4000", "100.00"), "Tracking": tracked}]}]
    cid = _connection(books)
    _mirror(books, cid)
    with books.transaction() as c:
        SL.set_setting(c, mirror.XERO_LOCATION_SETTING, "cat-loc", by=OWNER, at=AT)
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=lambda s: None,
                       location="cat-loc")
    assert _drain(books, client, cid) == []
    with books.read() as c:
        item = c.execute("SELECT name, sku, price, income_account_id FROM items").fetchone()
        assert item[:3] == ("Consulting", "CONSULT", 15000)
        assert item[3] == _local(c, "Account:acct-4000", "fin_accounts")
        location = _local(c, "TrackingOption:cat-loc/opt-austin", "locations")
        clazz = _local(c, "TrackingOption:cat-team/opt-field", "fin_dimensions")
        assert {(str(k), str(v)) for k, v in c.execute("SELECT kind, value FROM fin_line_tags")} == {
            ("location", location), ("class", clazz)}


def test_a_bill_backfilled_on_an_archived_tracking_option_imports_with_its_tag(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    xbooks.rows["TrackingCategories"][1]["Options"][0]["Status"] = "ARCHIVED"      # Team/Field is archived by now
    xbooks.rows["Contacts"][0]["IsSupplier"] = True
    tracked = [{"Name": "Team", "Option": "Field"}]
    xbooks.rows["Invoices"] = [{**_invoice("bill-1", kind="ACCPAY"),
                                "LineItems": [{**_line("acct-6100", "100.00"), "Tracking": tracked}]}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 1
        clazz = _local(c, "TrackingOption:cat-team/opt-field", "fin_dimensions")
        assert c.execute("SELECT active FROM fin_dimensions WHERE id=?", (clazz,)).fetchone()[0] == 0
        assert {(str(k), str(v)) for k, v in c.execute("SELECT kind, value FROM fin_line_tags")} == {("class", clazz)}


def test_a_bill_backfilled_on_an_option_in_an_archived_category_imports_with_its_tag(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    xbooks.rows["TrackingCategories"][1]["Status"] = "ARCHIVED"      # the Team category is archived by now
    xbooks.rows["Contacts"][0]["IsSupplier"] = True
    tracked = [{"Name": "Team", "Option": "Field"}]
    xbooks.rows["Invoices"] = [{**_invoice("bill-1", kind="ACCPAY"),
                                "LineItems": [{**_line("acct-6100", "100.00"), "Tracking": tracked}]}]
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fin_bills").fetchone()[0] == 1
        clazz = _local(c, "TrackingOption:cat-team/opt-field", "fin_dimensions")
        assert c.execute("SELECT active FROM fin_dimensions WHERE id=?", (clazz,)).fetchone()[0] == 0
        assert {(str(k), str(v)) for k, v in c.execute("SELECT kind, value FROM fin_line_tags")} == {("class", clazz)}


@pytest.mark.parametrize("echoed", [True, False], ids=["item_code_echoed", "item_code_not_echoed"])
def test_a_bill_made_here_on_an_item_keeps_the_item_when_xero_edits_it(books, echoed):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    xbooks.rows["Contacts"][0]["IsSupplier"] = True
    cid = _connection(books)
    _mirror(books, cid)
    client = _client(xbooks)
    assert _drain(books, client, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "bill_create", {
            "vendor_id": _local(c, "Contact:contact-1", "fin_contacts"), "number": "B-1", "bill_date": "2026-10-05",
            "due_date": "2026-11-05",
            "lines": [{"item_id": _local(c, "Item:item-1", "items"), "amount": 10_000, "description": "Consulting"}]})
        assert made.status == "committed", made
    assert _drain(books, client, cid, now=LATER) == []
    (bill,) = [r for r in xbooks.rows["Invoices"] if r["Type"] == "ACCPAY"]
    assert bill["LineItems"][0]["ItemCode"] == "CONSULT"            # the item goes to Xero by its code
    if not echoed:
        del bill["LineItems"][0]["ItemCode"]
    bill["LineItems"][0]["LineAmount"] = "120.00"                   # the provider edits the bill
    bill["UpdatedDateUTC"] = _later_than_now()
    assert _drain(books, client, cid, now=LATER2) == []
    with books.read() as c:
        assert c.execute("SELECT total FROM fin_bills WHERE number='B-1'").fetchone()[0] == 12_000
        assert c.execute("SELECT l.item_id FROM fin_bill_lines l JOIN fin_bills b ON b.id = l.bill_id "
                         "WHERE b.number='B-1'").fetchone()[0] == _local(c, "Item:item-1", "items")


def test_a_renamed_xero_location_option_is_taken_in_as_a_newer_revision_and_an_unchanged_one_keeps_its_revision(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    options = xbooks.rows["TrackingCategories"][0]["Options"]
    options[0] = {"TrackingOptionID": "opt-austin", "Name": "West", "Status": "ACTIVE"}
    cid = _connection(books)
    _mirror(books, cid)
    with books.transaction() as c:
        SL.set_setting(c, mirror.XERO_LOCATION_SETTING, "cat-loc", by=OWNER, at=AT)
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=lambda s: None,
                       location="cat-loc")
    assert _drain(books, client, cid) == []
    with books.read() as c:
        location = _local(c, "TrackingOption:cat-loc/opt-austin", "locations")
        first = SX.ref(c, cid, "locations", "TrackingOption:cat-loc/opt-austin")["revision"]
        assert c.execute("SELECT name FROM locations WHERE id=?", (location,)).fetchone()[0] == "West"
    options[0] = {"TrackingOptionID": "opt-austin", "Name": "East", "Status": "ACTIVE"}
    assert _drain(books, client, cid, now=LATER) == []
    with books.read() as c:
        renamed = SX.ref(c, cid, "locations", "TrackingOption:cat-loc/opt-austin")["revision"]
        assert c.execute("SELECT name FROM locations WHERE id=?", (location,)).fetchone()[0] == "East"
    assert renamed > first
    assert _drain(books, client, cid, now=LATER2, passes=1) == []
    with books.read() as c:
        assert SX.ref(c, cid, "locations", "TrackingOption:cat-loc/opt-austin")["revision"] == renamed


def test_a_tracking_lookup_that_fails_is_asked_again_and_a_read_waits_until_its_options_are_known():
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    tracked = [{"Name": "Location", "Option": "Austin"}]
    xbooks.rows["Invoices"] = [{**_invoice("inv-1"),
                                "LineItems": [{**_line("acct-4000", "100.00"), "Tracking": tracked}]}]
    xbooks.fail.add(("TrackingCategories", 1))
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=lambda s: None,
                       location="cat-loc")
    waiting = client.page("invoices", 1, 10)
    assert not waiting.ok and "tracking options could not be read" in waiting.error
    read = client.page("invoices", 1, 10)
    assert read.ok
    assert [tuple(line.tags) for doc in read.records for line in doc.lines] == [
        (("location", "TrackingOption:cat-loc/opt-austin"),)]


def test_an_item_write_whose_tracking_lookup_fails_waits_and_is_sent_with_its_account_codes_once_it_answers():
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    xbooks.fail.add(("TrackingCategories", 1))
    client = _client(xbooks)
    body = {"name": "Consulting", "sku": "CONSULT", "kind": "service", "price": 15000,
            "income": "Account:acct-4000", "expense": "Account:acct-6100", "asset": ""}
    waiting = client.send("item.create", body, "key-item-tracking")
    assert not waiting.ok and not waiting.final
    assert not [c for c in xbooks.calls if c["method"] in ("PUT", "POST") and c["path"].startswith("Items")]
    sent = client.send("item.create", body, "key-item-tracking")
    assert sent.ok
    assert [c["body"]["Items"][0]["SalesDetails"]["AccountCode"] for c in xbooks.calls
            if c["method"] == "PUT" and c["path"] == "Items"] == ["4000"]


def test_a_lost_write_found_while_the_tracking_lookup_is_down_is_asked_again_and_not_bound_without_its_record():
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    client = _client(xbooks)
    xbooks.lost = 1                                                   # the write lands; its answer never arrives
    lost = client.send("invoice.create", _doc_body("INV-T"), "key-tracking-find")
    assert not lost.ok and not lost.final
    xbooks.fail.add(("TrackingCategories", 1))
    waiting = client.find("invoice.create", _doc_body("INV-T"), "key-tracking-find")
    assert waiting is not None and not waiting.ok and not waiting.final
    found = client.find("invoice.create", _doc_body("INV-T"), "key-tracking-find")
    assert found is not None and found.ok and found.record is not None
    assert len(xbooks.rows["Invoices"]) == 1


def test_a_line_on_a_tracking_option_the_books_do_not_hold_waits_on_its_document_journal_or_bank_entry():
    def system(name: str) -> str:
        return {"location": "cat-loc", "option:Location/Austin": "TrackingOption:cat-loc/opt-austin"}.get(name, "")

    gone = [{"Name": "Team", "Option": "Gone"}]
    invoice = _invoice("inv-9")
    invoice["LineItems"] = [{**_line("acct-4000", "100.00"), "Tracking": gone}]
    journal = _journal("mj-9", "15.00")
    journal["JournalLines"][0]["Tracking"] = gone
    bank = _spend("bt-9", [{**_line("acct-6100", "15.00"), "Tracking": gone}])
    assert isinstance(X.document_record(invoice, system), M.Refused)
    assert isinstance(X.manual_journal_record(journal, system), M.Refused)
    assert isinstance(X.bank_record(bank, system), M.Refused)
    mapped = [{"Name": "Location", "Option": "Austin"}]
    invoice["LineItems"] = [{**_line("acct-4000", "100.00"), "Tracking": mapped}]
    assert isinstance(X.document_record(invoice, system), M.Document)


def test_a_bank_transaction_line_takes_in_its_tracking_option_and_a_revision_keeps_it(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    tracked = [{"Name": "Location", "Option": "Austin"}]
    xbooks.rows["BankTransactions"] = [_spend("bt-1", [{**_line("acct-6100", "15.00"), "Tracking": tracked}])]
    cid = _connection(books)
    _mirror(books, cid)
    with books.transaction() as c:
        SL.set_setting(c, mirror.XERO_LOCATION_SETTING, "cat-loc", by=OWNER, at=AT)
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=lambda s: None,
                       location="cat-loc")
    assert _drain(books, client, cid) == []

    def expense_tags() -> list[tuple[str, str]]:
        with books.read() as c:
            entry = _local(c, "BankTransaction:bt-1", "fin_entries")
            return [tuple(r) for r in c.execute(
                "SELECT t.kind, t.value FROM fin_line_tags t JOIN fin_lines l ON l.id=t.line_id "
                "WHERE l.entry_id=? AND l.account_id=?", (entry, account(c, "6100")))]

    with books.read() as c:
        austin = _local(c, "TrackingOption:cat-loc/opt-austin", "locations")
    assert expense_tags() == [("location", austin)]
    revised = {**xbooks.rows["BankTransactions"][0], "UpdatedDateUTC": ms("2026-10-15T12:45:00Z"),
               "LineItems": [{**_line("acct-6100", "20.00"), "Tracking": tracked}]}
    xbooks.change("BankTransactions", revised)
    assert _drain(books, client, cid, now=LATER) == []
    assert expense_tags() == [("location", austin)]


def test_a_revised_invoice_naming_a_tracking_option_the_books_do_not_hold_keeps_its_tags_and_revision(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    both = [{"Name": "Location", "Option": "Austin"}, {"Name": "Team", "Option": "Field"}]
    xbooks.rows["Invoices"] = [{**_invoice("inv-1"), "LineItems": [{**_line("acct-4000", "100.00"), "Tracking": both}]}]
    cid = _connection(books)
    _mirror(books, cid)
    with books.transaction() as c:
        SL.set_setting(c, mirror.XERO_LOCATION_SETTING, "cat-loc", by=OWNER, at=AT)
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=lambda s: None,
                       location="cat-loc")
    assert _drain(books, client, cid) == []
    with books.read() as c:
        invoice = _local(c, "Invoice:inv-1", "fin_invoices")
        before = tuple(c.execute("SELECT status, total FROM fin_invoices WHERE id=?", (invoice,)).fetchone())
        tags = c.execute("SELECT tags_json FROM fin_invoice_lines WHERE invoice_id=?", (invoice,)).fetchone()[0]
        revision = c.execute("SELECT revision FROM fin_external_refs WHERE external_id='Invoice:inv-1'").fetchone()[0]
    assert sorted(json.loads(tags)) == ["class", "location"]
    gone = [{"Name": "Location", "Option": "Austin"}, {"Name": "Team", "Option": "Gone"}]
    xbooks.change("Invoices", {**xbooks.rows["Invoices"][0], "UpdatedDateUTC": ms("2026-10-15T12:45:00Z"),
                               "LineItems": [{**_line("acct-4000", "100.00"), "Tracking": gone}]})
    _drain(books, client, cid, now=LATER, passes=2)
    with books.read() as c:
        assert tuple(c.execute("SELECT status, total FROM fin_invoices WHERE id=?", (invoice,)).fetchone()) == before
        assert c.execute("SELECT tags_json FROM fin_invoice_lines WHERE invoice_id=?", (invoice,)).fetchone()[0] == tags
        assert c.execute("SELECT revision FROM fin_external_refs WHERE external_id='Invoice:inv-1'").fetchone()[0] \
            == revision


def test_xero_takes_an_item_by_its_account_codes_and_a_bill_line_by_its_tracking_options():
    item = X.item_body({"name": "Consulting", "sku": "CONSULT", "kind": "service", "price": 15_000,
                        "income_code": "4000", "expense_code": "6100"})
    assert item == {"Code": "CONSULT", "Name": "Consulting", "IsSold": True, "IsPurchased": True,
                    "SalesDetails": {"AccountCode": "4000", "UnitPrice": "150.00"},
                    "PurchaseDetails": {"AccountCode": "6100"}}
    bill = X.document_body({"party": "Contact:contact-1", "day": "2026-10-05", "memo": "", "lines": [
        {"account": "Account:acct-6100", "amount": 1500, "description": "seats",
         "location": "TrackingOption:cat-loc/opt-austin", "class": "cat-team/opt-field"}]}, "Bill")
    assert bill["Invoices"][0]["LineItems"][0]["Tracking"] == [
        {"TrackingCategoryID": "cat-loc", "TrackingOptionID": "opt-austin"},
        {"TrackingCategoryID": "cat-team", "TrackingOptionID": "opt-field"}]


def test_an_item_made_here_is_sent_to_xero_by_the_codes_of_its_accounts():
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    client = xero.Xero(TENANT, lambda force: "token-1", base=BASE, transport=xbooks, sleep=lambda s: None)
    sent = client.send("item.create", {"name": "Design", "sku": "DESIGN", "kind": "service", "price": 9_000,
                                       "income": "Account:acct-4000", "expense": "", "asset": ""}, "item:design")
    assert sent.ok and sent.kind == "Item" and sent.external_id, sent
    assert xbooks.rows["Items"][-1]["SalesDetails"] == {"AccountCode": "4000", "UnitPrice": "90.00"}


def test_an_item_made_here_in_a_xero_book_is_written_to_xero_and_its_row_appears_once_xero_confirms(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    cid = _connection(books)
    _mirror(books, cid)
    client = _client(xbooks)
    assert _drain(books, client, cid) == []
    with books.transaction() as c:
        made = act(c, OWNER, "item_create", {"name": "Design", "sku": "DESIGN", "kind": "service", "price": 9_000,
                                             "income_account_id": _local(c, "Account:acct-4000", "fin_accounts")})
        assert made.status == "committed", made
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Design'").fetchone()[0] == 0
    assert _drain(books, client, cid, now=LATER) == []
    assert [row["Code"] for row in xbooks.rows["Items"]] == ["CONSULT", "DESIGN"]
    with books.read() as c:
        assert c.execute("SELECT price FROM items WHERE id=?", (made.result["item_id"],)).fetchone()[0] == 9_000
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Design'").fetchone()[0] == 1


def test_a_location_or_job_made_here_in_a_xero_book_is_refused_since_xero_keeps_them_elsewhere(books):
    xbooks = XeroBooks()
    _tracked_chart(xbooks)
    cid = _connection(books)
    _mirror(books, cid)
    assert _drain(books, _client(xbooks), cid) == []
    with books.transaction() as c:
        assert act(c, OWNER, "location_create", {"name": "Dallas"}).code == "invalid"
        customer = _local(c, "Contact:contact-1", "fin_contacts")
        assert act(c, OWNER, "project_create", {"name": "Deck", "customer_id": customer}).code == "invalid"
