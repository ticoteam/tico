"""QuickBooks Online (spec §3.4, §10): webhook signatures and relayed events, the HTTP client's retries and RequestIds, the
mapping of QuickBooks shapes, and the OAuth broker's contract with a fake HQ (the install's tokens live in the vault)."""
import base64
import hashlib
import hmac
import json
import urllib.parse
import uuid

import pytest

from backend import hook_relay
from backend import trigger_presets as P
from backend.finance import books_link, books_sync
from backend.finance import qbo as QC
from backend.finance.core import mirror as M
from backend.finance.core import qbo as Q
from backend.finance.store import banking as SK
from backend.finance.store import ledger as SL
from backend.finance.store import mirror as SX
from backend.finance.store import tico as T
from backend.tests.test_finance_fixtures import (  # noqa: F401
    AT, OWNER, books, finance_template, store)

LATER = "2026-10-15T12:40:00Z"
BASE = "https://updates.tico.team/v1/gateway"


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_a_wrong_intuit_or_xero_signature_is_refused_and_the_right_one_kept():
    body = b'{"eventNotifications":[]}'
    assert P.verify("intuit", {"intuit-signature": _sign("verifier", body)}, body, "verifier", 0) == (True, "")
    assert P.verify("intuit", {"intuit-signature": _sign("verifier", body)}, b'{"x":1}', "verifier", 0)[0] is False
    assert P.verify("intuit", {"intuit-signature": _sign("other", body)}, body, "verifier", 0)[0] is False
    assert P.verify("intuit", {}, body, "verifier", 0)[0] is False
    assert P.verify("xero", {"x-xero-signature": _sign("key", body)}, body, "key", 0) == (True, "")
    assert P.verify("xero", {"x-xero-signature": _sign("wrong", body)}, body, "key", 0)[0] is False


def _books_connection(store, cid: str = "conn-qb", company: str = "co_acme") -> None:
    with store.transaction() as c:
        SX.books_connection_insert(c, cid=cid, provider="quickbooks", name="Acme books", by=OWNER, at=AT)
        SX.set_books_settings(c, cid, {"provider": "quickbooks", "company_id": company})


def _envelope(**over) -> dict[str, object]:
    base = {"type": "tico.finance.webhook", "provider": "quickbooks", "company_id": "co_acme", "external_id": "evt-1",
            "payload": {"eventNotifications": []}}
    return {**base, **over}


def test_a_relayed_event_is_kept_once_for_the_company_it_names(books):
    _books_connection(books)
    with books.transaction() as c:
        assert books_sync.delivery(c, "intuit", _envelope()) == "kept"
        assert books_sync.delivery(c, "intuit", _envelope()) == "seen"                        # a replay
        assert books_sync.delivery(c, "intuit", _envelope(company_id="co_other")) == "refused"  # not this company
        assert books_sync.delivery(c, "xero", _envelope()) == "refused"                      # wrong provider
        assert books_sync.delivery(c, "intuit", _envelope(type="something.else")) == "refused"


def test_quickbooks_shapes_map_to_mirror_records_with_exact_money():
    bank = Q.account_record({"Id": "55", "SyncToken": "2", "Name": "Checking", "AccountType": "Bank", "AcctNum": "1000",
                             "Active": True})
    assert bank is not None and (bank.code, bank.type, bank.subtype) == ("1000", "asset", "bank")
    assert Q.account_record({"Id": "56", "AccountType": "Not A Type"}) is None
    entry = Q.journal_record({"Id": "777", "SyncToken": "1", "TxnDate": "2026-10-05", "PrivateNote": "Acme Software",
                              "Line": [{"Amount": "15.00", "DetailType": "JournalEntryLineDetail",
                                        "Description": "fee", "JournalEntryLineDetail": {
                                            "PostingType": "Debit", "AccountRef": {"value": "55"},
                                            "Entity": {"Type": "Vendor", "EntityRef": {"value": "9"}}}},
                                       {"Amount": "15.00", "DetailType": "JournalEntryLineDetail",
                                        "JournalEntryLineDetail": {"PostingType": "Credit",
                                                                   "AccountRef": {"value": "66"}}}]})
    assert [(line.account, line.amount, line.party) for line in entry.lines] == [
        ("Account:55", 1500, "Vendor:9"), ("Account:66", -1500, "")]
    cdc = Q.change_records({"CDCResponse": [{"QueryResponse": [{"Account": [{"Id": "55", "status": "Deleted"}]}]}]})
    assert cdc == [M.Ext("Account", "55", "", True)]


class Wire:
    """A fake QuickBooks transport: replies in order, and every request it was sent."""

    def __init__(self, *replies: QC.Reply) -> None:
        self.replies, self.sent = list(replies), []

    def __call__(self, method: str, url: str, headers: object, data: bytes | None) -> QC.Reply:
        self.sent.append((method, url, dict(headers), data))  # type: ignore[call-overload]
        return self.replies.pop(0)


def _client(wire: Wire, tokens=lambda force: "tok") -> QC.QuickBooks:
    return QC.QuickBooks("123", tokens, base="http://127.0.0.1:9", transport=wire, sleep=lambda s: None)


def test_a_401_takes_one_new_token_from_hq_and_the_call_is_tried_again():
    calls: list[bool] = []

    def tokens(force: bool) -> str:
        calls.append(force)
        return "new" if force else "old"

    wire = Wire(QC.Reply(401, {"Fault": {"Error": [{"Message": "expired"}]}}),
                QC.Reply(200, {"QueryResponse": {"Account": [{"Id": "55", "Name": "Checking",
                                                              "AccountType": "Bank", "AcctNum": "1000"}]}}))
    read = _client(wire, tokens).page("accounts", 1, 10)
    assert read.ok and len(read.records) == 1
    assert calls == [False, True]
    assert [sent[2]["Authorization"] for sent in wire.sent] == ["Bearer old", "Bearer new"]


def test_a_retried_write_carries_the_same_request_id_and_QuickBooks_amounts():
    body = {"date": "2026-10-05", "memo": "Acme Software", "source": "sync", "lines": [
        {"account": "Account:55", "party": "Vendor:9", "amount": 1500, "description": "fee", "classes": {}},
        {"account": "Account:66", "party": "", "amount": -1500, "description": "", "classes": {}}]}
    wire = Wire(QC.Reply(0), QC.Reply(200, {"JournalEntry": {"Id": "777", "SyncToken": "0"}}))
    client = _client(wire)
    first = client.send("journal.create", body, "outbox-key-1")
    second = client.send("journal.create", body, "outbox-key-1")
    assert first.ok is False and first.final is False                       # no answer: tried again later
    assert second.ok and (second.kind, second.external_id, second.revision) == ("JournalEntry", "777", "0")
    want = f"requestid={uuid.uuid5(QC.NAMESPACE, 'outbox-key-1')}"
    assert all(want in url for _, url, _, _ in wire.sent)
    sent = json.loads(wire.sent[1][3] or b"{}")
    debit, credit = sent["Line"]
    assert debit["Amount"] == "15.00" and debit["JournalEntryLineDetail"]["PostingType"] == "Debit"
    assert debit["JournalEntryLineDetail"]["Entity"]["EntityRef"]["value"] == "9"
    assert credit["Amount"] == "15.00" and credit["JournalEntryLineDetail"]["PostingType"] == "Credit"


def test_a_validation_refusal_from_quickbooks_is_final():
    wire = Wire(QC.Reply(400, {"Fault": {"Error": [{"Message": "Journal entry does not balance"}],
                                         "type": "ValidationFault"}}))
    sent = _client(wire).send("journal.create", {"date": "2026-10-05", "memo": "", "source": "sync", "lines": [
        {"account": "Account:55", "party": "", "amount": 5, "description": "", "classes": {}}]}, "key-2")
    assert sent.ok is False and sent.final is True and "does not balance" in sent.error


def test_a_refused_estimate_link_sends_the_invoice_again_under_a_new_request_with_the_same_marker():
    wire = Wire(QC.Reply(200, {"QueryResponse": {"Item": [{"Id": "7", "IncomeAccountRef": {"value": "3"}}]}}),
                QC.Reply(400, {"Fault": {"Error": [{"Message": "Estimate is not linkable"}],
                                         "type": "ValidationFault"}}),
                QC.Reply(200, {"Invoice": {"Id": "903", "SyncToken": "0"}}))
    sent = _client(wire).send("invoice.create", {"party": "Customer:4", "number": "I-7", "day": "2026-10-05",
                                                 "due": "", "memo": "", "linked_estimate": "41", "lines": [
                                                     {"account": "Account:3", "amount": 2000, "description": "x"}]},
                              "invoice-key-3")
    assert (sent.ok, sent.external_id) == (True, "903")
    first, second = (json.loads(data or b"{}") for _, _, _, data in wire.sent[1:])
    assert f"requestid={uuid.uuid5(QC.NAMESPACE, 'invoice-key-3')}" in wire.sent[1][1]
    assert f"requestid={uuid.uuid5(QC.NAMESPACE, 'invoice-key-3:unlinked')}" in wire.sent[2][1]
    assert first["PrivateNote"].endswith(QC._marker("invoice-key-3"))
    assert second["PrivateNote"].endswith(QC._marker("invoice-key-3"))   # the retry finds the same write
    assert "LinkedTxn" in first and "LinkedTxn" not in second


class FakeHQ:
    """HQ's broker answers, given to the Gateway link's call in place of the network (what the install sends and gets)."""

    def __init__(self, *answers: object) -> None:
        self.answers, self.calls = list(answers), []

    def as_link_call(self):  # type: ignore[no-untyped-def]
        def call(link, method, path, body=None, idempotency=None, *, area="hooks", **_):  # type: ignore[no-untyped-def]
            self.calls.append({"base": link.base, "area": area, "path": path, "body": body})
            answer = self.answers.pop(0)
            if callable(answer):                       # a step that changes the vault before HQ answers
                answer = answer()
            if isinstance(answer, Exception):
                raise answer
            return answer
        return call


def _tokens(access: str, refresh: str, expires: str = "2026-10-15T12:30:00Z") -> dict[str, object]:
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires,
            "refresh_expires_at": "2026-12-01T00:00:00Z", "realm_id": "123", "tenant_id": None}


def _link() -> hook_relay.Link:
    return hook_relay.Link("gk_test", BASE, "fp")


def test_claim_stores_the_pair_in_the_vault_and_refresh_rotates_it_before_use(books, monkeypatch):
    hq = FakeHQ(_tokens("acc-1", "ref-1"), _tokens("acc-2", "ref-2", "2026-10-15T13:30:00Z"))
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    vault = T._VAULT[0]
    cid = books_link.connect_claim(books, vault, _link(), provider="quickbooks", company_id="co_acme",
                                   claim_id="clm_1", name="Acme books", by=OWNER, now=AT)
    with books.read() as c:
        settings = SX.books_settings(c, cid)
        assert T.credential_secret(c, vault, str(settings["refresh"])) == "ref-1"
        assert "ref-1" not in json.dumps(settings) and "acc-1" not in json.dumps(settings)   # ids, not secrets
    assert books_link.access_token(books, vault, _link(), cid, "quickbooks", now=AT) == "acc-1"   # not due: no call
    assert hq.calls[0]["base"] == "https://updates.tico.team/v1" and hq.calls[0]["area"] == "finance"
    assert hq.calls[0]["path"] == "oauth/quickbooks/claim" and len(hq.calls) == 1
    with books.read() as c:
        old_refresh = str(SX.books_settings(c, cid)["refresh"])
    assert books_link.access_token(books, vault, _link(), cid, "quickbooks", now=LATER) == "acc-2"
    assert hq.calls[1]["path"] == "oauth/quickbooks/refresh" and hq.calls[1]["body"] == {
        "company_id": "co_acme", "refresh_token": "ref-1"}
    with books.read() as c:
        settings = SX.books_settings(c, cid)
        assert T.credential_secret(c, vault, str(settings["refresh"])) == "ref-2"
        assert not T.credential_exists(c, old_refresh)


def test_a_refused_refresh_asks_for_a_reconnect_once_and_nothing_retries_it(books, monkeypatch):
    hq = FakeHQ(_tokens("acc-1", "ref-1"),
                hook_relay.Failed("reconnect_required", 409, {"error": "reconnect_required"}))
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    vault = T._VAULT[0]
    cid = books_link.connect_claim(books, vault, _link(), provider="quickbooks", company_id="co_acme",
                                   claim_id="clm_2", name="Acme books", by=OWNER, now=AT)
    with pytest.raises(books_link.ReconnectRequired):
        books_link.access_token(books, vault, _link(), cid, "quickbooks", now=LATER, force=True)
    with books.read() as c:
        assert SX.books_status(c, cid) == "reconnect"
    assert [call["path"] for call in hq.calls] == ["oauth/quickbooks/claim", "oauth/quickbooks/refresh"]


def test_disconnect_drops_the_vault_rows_only_after_hq_confirms(books, monkeypatch):
    hq = FakeHQ(_tokens("acc-1", "ref-1"), {"ok": True, "disconnected": True})
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    vault = T._VAULT[0]
    cid = books_link.connect_claim(books, vault, _link(), provider="quickbooks", company_id="co_acme",
                                   claim_id="clm_3", name="Acme books", by=OWNER, now=AT)
    with books.read() as c:
        before = dict(SX.books_settings(c, cid))
    books_link.disconnect_books(books, vault, _link(), cid, "quickbooks", now=AT)
    assert hq.calls[1]["path"] == "oauth/quickbooks/disconnect"
    with books.read() as c:
        assert not T.credential_exists(c, str(before["access"]))
        assert not T.credential_exists(c, str(before["refresh"]))
        assert SX.books_status(c, cid) == "disconnected"


def _cid_of(store) -> str:  # type: ignore[no-untyped-def]
    with store.read() as c:
        return str(c.execute("SELECT id FROM fin_connections WHERE provider='quickbooks'").fetchone()[0])


def _claim(store, vault: object, name: str = "Acme books") -> str:  # type: ignore[no-untyped-def]
    return books_link.connect_claim(store, vault, _link(), provider="quickbooks", company_id="co_acme",
                                    claim_id="clm_x", name=name, by=OWNER, now=AT)


def test_a_failed_vault_write_is_tried_again_with_the_pair_still_in_hand(books, monkeypatch):
    hq = FakeHQ(_tokens("acc-1", "ref-1"))
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    real_save, tries = books_link.save, []

    def flaky(c, cid, tokens, *, by, at):  # type: ignore[no-untyped-def]
        tries.append(cid)
        if len(tries) == 1:
            raise RuntimeError("vault is busy")
        real_save(c, cid, tokens, by=by, at=at)

    monkeypatch.setattr(books_link, "save", flaky)
    vault = T._VAULT[0]
    cid = _claim(books, vault)
    assert len(tries) == 2
    with books.read() as c:
        assert SX.books_status(c, cid) == "active"
    assert books_link.access_token(books, vault, _link(), cid, "quickbooks", now=AT) == "acc-1"
    assert [call["path"] for call in hq.calls] == ["oauth/quickbooks/claim"]     # the single-use claim, once


def test_a_vault_write_that_fails_twice_leaves_a_reconnect_and_one_needs_you_item(books, monkeypatch):
    hq = FakeHQ(_tokens("acc-1", "ref-1"))
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    asks: list[tuple[str, str]] = []

    def down(c, cid, tokens, *, by, at):  # type: ignore[no-untyped-def]
        raise RuntimeError("vault is down")

    monkeypatch.setattr(books_link, "save", down)
    monkeypatch.setattr(T, "needs_you", lambda c, person, title, body: asks.append((person, title)))
    with pytest.raises(books_link.ReconnectRequired) as err:
        _claim(books, T._VAULT[0])
    assert err.value.code == "vault_write_failed"
    assert asks == [(OWNER, "Connect quickbooks again")]
    with books.read() as c:
        assert SX.books_status(c, _cid_of(books)) == "reconnect"
    assert len(hq.calls) == 1


def test_a_refresh_hq_holds_no_grant_for_asks_for_a_reconnect_and_is_not_retried(books, monkeypatch):
    hq = FakeHQ(_tokens("acc-1", "ref-1"), hook_relay.Failed("not_connected", 404, {"error": "not_connected"}))
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    vault = T._VAULT[0]
    cid = _claim(books, vault)
    with pytest.raises(books_link.ReconnectRequired):
        books_link.access_token(books, vault, _link(), cid, "quickbooks", now=LATER, force=True)
    with books.read() as c:
        assert SX.books_status(c, cid) == "reconnect"
    assert [call["path"] for call in hq.calls] == ["oauth/quickbooks/claim", "oauth/quickbooks/refresh"]


def test_a_refresh_with_a_pair_already_rotated_is_tried_once_more_with_the_vault_pair(books, monkeypatch):
    def rotated_elsewhere():  # type: ignore[no-untyped-def]
        with books.transaction() as c:
            books_link.save(c, _cid_of(books), books_link.Tokens("acc-2", "ref-2", "2026-10-15T13:30:00Z", None,
                                                                  "123", None), by=OWNER, at=LATER)
        return hook_relay.Failed("not_connected", 404, {"error": "not_connected"})

    hq = FakeHQ(_tokens("acc-1", "ref-1"), rotated_elsewhere, _tokens("acc-3", "ref-3", "2026-10-15T14:30:00Z"))
    monkeypatch.setattr(hook_relay.Link, "call", hq.as_link_call())
    vault = T._VAULT[0]
    cid = _claim(books, vault)
    assert books_link.access_token(books, vault, _link(), cid, "quickbooks", now=LATER, force=True) == "acc-3"
    refreshes = [call["body"]["refresh_token"] for call in hq.calls if call["path"].endswith("/refresh")]
    assert refreshes == ["ref-1", "ref-2"]
    with books.read() as c:
        assert T.credential_secret(c, vault, str(SX.books_settings(c, cid)["refresh"])) == "ref-3"


def test_relayed_quickbooks_events_are_kept_for_the_realm_the_connection_holds(books):
    _books_connection(books)
    with books.transaction() as c:
        SX.set_books_settings(c, "conn-qb", {"realm_id": "123"})
    with books.transaction() as c:
        assert books_sync.delivery(c, "intuit", _envelope(
            external_id="evt-cloud", payload={"cloudEvents": [{"intuitaccountid": "123", "id": "a"}]})) == "kept"
        assert books_sync.delivery(c, "intuit", _envelope(
            external_id="evt-other", payload={"cloudEvents": [{"intuitaccountid": "999"}]})) == "refused"
        assert books_sync.delivery(c, "intuit", _envelope(external_id="evt-mixed", payload={"cloudEvents": [
            {"intuitaccountid": "123"}, {"intuitaccountid": "999"}]})) == "refused"
        assert books_sync.delivery(c, "intuit", _envelope(
            external_id="evt-legacy", payload={"eventNotifications": [{"realmId": "123"}]})) == "kept"
        assert books_sync.delivery(c, "intuit", _envelope(external_id="evt-junk", payload={"events": []})) == "refused"


def test_documents_and_settlements_map_to_mirror_records_with_their_accounts_and_links():
    items = Q.item_accounts([{"Id": "7", "ExpenseAccountRef": {"value": "66"}, "IncomeAccountRef": {"value": "40"}}])
    bill = Q.records("Bill", [{"Id": "12", "SyncToken": "3", "DocNumber": "B-12", "TxnDate": "2026-10-05",
                               "DueDate": "2026-11-05", "TotalAmt": "30.50", "VendorRef": {"value": "9"},
                               "PrivateNote": "seats", "Line": [
                                   {"Amount": "15.25", "DetailType": "ItemBasedExpenseLineDetail", "Description": "seat",
                                    "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "7"}}},
                                   {"Amount": "15.25", "DetailType": "AccountBasedExpenseLineDetail",
                                    "Description": "fee", "AccountBasedExpenseLineDetail": {
                                        "AccountRef": {"value": "55"}}},
                                   {"Amount": "30.50", "DetailType": "SubTotalLineDetail"}]}], items)[0]
    assert isinstance(bill, M.Document) and bill.ext == M.Ext("Bill", "12", "3")
    assert (bill.party, bill.number, bill.day, bill.due, bill.total) == (
        "Vendor:9", "B-12", "2026-10-05", "2026-11-05", 3050)
    assert [(line.account, line.amount) for line in bill.lines] == [("Account:66", 1525), ("Account:55", 1525)]
    paid = Q.records("BillPayment", [{"Id": "30", "SyncToken": "1", "TxnDate": "2026-10-12", "TotalAmt": "15.25",
                                      "VendorRef": {"value": "9"}, "CheckPayment": {
                                          "BankAccountRef": {"value": "35"}},
                                      "Line": [{"Amount": "15.25", "LinkedTxn": [
                                          {"TxnType": "Bill", "TxnId": "12"}]}]}])[0]
    assert isinstance(paid, M.Settlement)
    assert (paid.party, paid.bank, paid.total, paid.links) == ("Vendor:9", "Account:35", 1525, (("Bill:12", 1525),))
    assert Q.records("Bill", [{"Id": "12", "SyncToken": "4", "status": "Deleted"}]) == [
        M.Ext("Bill", "12", "4", True)]


def test_withheld_rehearsal_writes_stay_visible_and_are_not_sent(books):
    _books_connection(books)
    with books.transaction() as c:
        SL.set_book_mode(c, SL.book(c).id, "mirror", "conn-qb")  # type: ignore[union-attr]
        SK.outbox_add(c, connection_id="conn-qb", action_id="", op="journal.create",
                      payload={"date": "2026-10-05", "memo": "x", "source": "sync", "lines": []},
                      key="rehearse-1", by=OWNER, at=AT)
    assert books_sync.withhold(books, now=AT) == 1
    assert books_sync.withhold(books, now=AT) == 0
    with books.read() as c:
        row = c.execute("SELECT status, last_error FROM fin_outbox WHERE idempotency_key='rehearse-1'").fetchone()
    assert tuple(row) == ("withheld", books_sync.REHEARSAL_WHY)


def test_a_bill_made_here_is_posted_as_an_expense_on_its_account_with_its_vendor_and_number():
    wire = Wire(QC.Reply(200, {"Bill": {"Id": "901", "SyncToken": "0"}}))
    sent = _client(wire).send("bill.create", {"party": "Vendor:9", "number": "B-77", "day": "2026-10-05",
                                              "due": "2026-11-05", "memo": "", "lines": [
                                                  {"account": "Account:2", "amount": 1500, "description": "seats"}]},
                              "bill-key")
    assert (sent.ok, sent.kind, sent.external_id) == (True, "Bill", "901")
    body = json.loads(wire.sent[0][3] or b"{}")
    assert body["VendorRef"] == {"value": "9"} and body["DocNumber"] == "B-77" and body["DueDate"] == "2026-11-05"
    assert body["Line"] == [{"DetailType": "AccountBasedExpenseLineDetail", "Amount": "15.00", "Description": "seats",
                             "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "2"}}}]


def test_an_invoice_line_is_billed_by_the_item_that_sells_to_its_account_or_refused_before_any_post():
    wire = Wire(QC.Reply(200, {"QueryResponse": {"Item": [{"Id": "7", "IncomeAccountRef": {"value": "3"}}]}}),
                QC.Reply(200, {"Invoice": {"Id": "902", "SyncToken": "0"}}))
    sent = _client(wire).send("invoice.create", {"party": "Customer:4", "number": "I-5", "day": "2026-10-05",
                                                 "due": "", "memo": "", "lines": [
                                                     {"account": "Account:3", "amount": 2000, "description": "x"}]},
                              "invoice-key")
    assert (sent.ok, sent.kind, sent.external_id) == (True, "Invoice", "902")
    line = json.loads(wire.sent[1][3] or b"{}")["Line"][0]
    assert line["SalesItemLineDetail"] == {"ItemRef": {"value": "7"}, "Qty": 1, "UnitPrice": "20.00"}
    assert line["Amount"] == "20.00"

    wire = Wire(QC.Reply(200, {"QueryResponse": {"Item": [{"Id": "7", "IncomeAccountRef": {"value": "40"}}]}}))
    sent = _client(wire).send("invoice.create", {"party": "Customer:4", "number": "I-6", "day": "2026-10-05",
                                                 "due": "", "memo": "", "lines": [
                                                     {"account": "Account:3", "amount": 2000, "description": "x"}]},
                              "invoice-key-2")
    assert sent.ok is False and sent.final is True and "no QuickBooks item sells to account 3" in sent.error
    assert [method for method, *_ in wire.sent] == ["GET"]     # the only call was the read of the items


def test_voids_and_payments_are_the_quickbooks_writes_they_name_and_apply_to_what_they_settle():
    wire = Wire(QC.Reply(200, {"Invoice": {"Id": "901", "SyncToken": "2"}}),
                QC.Reply(200, {"Bill": {"Id": "902", "SyncToken": "1"}}),
                QC.Reply(200, {"Payment": {"Id": "903", "SyncToken": "0"}}),
                QC.Reply(200, {"BillPayment": {"Id": "904", "SyncToken": "0"}}))
    client = _client(wire)
    assert client.send("invoice.void", {"ext": "Invoice:901", "revision": "1"}, "void-1").ok
    assert client.send("bill.void", {"ext": "Bill:902", "revision": "0"}, "void-2").ok
    paid = client.send("payment.invoice", {"party": "Customer:4", "bank": "Account:1", "total": 2000, "day": "2026-10-12",
                                           "memo": "", "links": [["Invoice:12", 2000]]}, "pay-1")
    settled = client.send("payment.bill", {"party": "Vendor:9", "bank": "Account:1", "total": 1500, "day": "2026-10-12",
                                           "memo": "", "links": [["Bill:12", 1500]]}, "pay-2")
    assert (paid.kind, paid.external_id) == ("Payment", "903") and (settled.kind, settled.external_id) == (
        "BillPayment", "904")
    void_invoice, void_bill, received, sent_bill = wire.sent
    assert "/invoice?" in void_invoice[1] and "operation=void" in void_invoice[1]
    assert json.loads(void_invoice[3] or b"{}") == {"Id": "901", "SyncToken": "1"}
    assert "operation=delete" in void_bill[1] and json.loads(void_bill[3] or b"{}") == {"Id": "902", "SyncToken": "0"}
    got = json.loads(received[3] or b"{}")
    assert (got["CustomerRef"], got["DepositToAccountRef"], got["TotalAmt"]) == ({"value": "4"}, {"value": "1"}, "20.00")
    assert got["Line"] == [{"Amount": "20.00", "LinkedTxn": [{"TxnId": "12", "TxnType": "Invoice"}]}]
    got = json.loads(sent_bill[3] or b"{}")
    assert got["PayType"] == "Check" and got["CheckPayment"] == {"BankAccountRef": {"value": "1"}}
    assert got["Line"] == [{"Amount": "15.00", "LinkedTxn": [{"TxnId": "12", "TxnType": "Bill"}]}]


def test_a_credit_memo_settles_nothing_itself_its_applications_come_as_payments():
    items = Q.item_accounts([{"Id": "7", "IncomeAccountRef": {"value": "40"}}])
    memo = Q.document_record({"Id": "20", "SyncToken": "1", "DocNumber": "CM-20", "TxnDate": "2026-10-10",
                              "TotalAmt": "5.00", "CustomerRef": {"value": "4"}, "Line": [
                                  {"Amount": "5.00", "DetailType": "SalesItemLineDetail", "Description": "consulting",
                                   "SalesItemLineDetail": {"ItemRef": {"value": "7"}},
                                   "LinkedTxn": [{"TxnType": "Invoice", "TxnId": "12"}]}]}, "CreditMemo", items)
    assert (memo.party, memo.total, memo.links) == ("Customer:4", 500, ())


WRITE_OFF = {"party": "Customer:4", "account": "Account:2", "day": "2026-10-12", "memo": "Uncollectible",
             "total": 2000, "invoice": "Invoice:12"}


def test_a_write_off_is_one_credit_memo_billed_by_the_bad_debt_item_that_sells_to_its_account():
    wire = Wire(QC.Reply(200, {"QueryResponse": {"Item": [{"Id": "7", "IncomeAccountRef": {"value": "2"}}]}}),
                QC.Reply(200, {"CreditMemo": {"Id": "901", "SyncToken": "0"}}))
    sent = _client(wire).send("writeoff.create", WRITE_OFF, "wo-key")
    assert (sent.ok, sent.kind, sent.external_id) == (True, "CreditMemo", "901")
    memo = json.loads(wire.sent[1][3] or b"{}")
    assert memo["CustomerRef"] == {"value": "4"} and memo["TxnDate"] == "2026-10-12"
    line = memo["Line"][0]
    assert line["SalesItemLineDetail"] == {"ItemRef": {"value": "7"}, "Qty": 1, "UnitPrice": "20.00"}
    assert line["Amount"] == "20.00" and "LinkedTxn" not in line and len(memo["Line"]) == 1
    assert f"requestid={uuid.uuid5(QC.NAMESPACE, 'wo-key')}" in wire.sent[1][1]


def test_a_missing_bad_debt_item_is_created_once_with_its_own_request_id_and_then_billed_by():
    wire = Wire(QC.Reply(200, {"QueryResponse": {}}),
                QC.Reply(200, {"Item": {"Id": "8", "SyncToken": "0"}}),
                QC.Reply(200, {"CreditMemo": {"Id": "902", "SyncToken": "0"}}))
    sent = _client(wire).send("writeoff.create", WRITE_OFF, "wo-key-2")
    assert (sent.ok, sent.external_id) == (True, "902")
    item = json.loads(wire.sent[1][3] or b"{}")
    assert item == {"Name": "Bad debt", "Type": "NonInventory", "IncomeAccountRef": {"value": "2"}}
    assert f"requestid={uuid.uuid5(QC.NAMESPACE, 'wo-key-2:item')}" in wire.sent[1][1]
    assert json.loads(wire.sent[2][3] or b"{}")["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {"value": "8"}


def test_a_bad_debt_item_that_sells_to_another_account_refuses_the_write_off_before_anything_is_posted():
    wire = Wire(QC.Reply(200, {"QueryResponse": {"Item": [{"Id": "7", "IncomeAccountRef": {"value": "40"}}]}}))
    sent = _client(wire).send("writeoff.create", WRITE_OFF, "wo-key-3")
    assert sent.ok is False and sent.final is True and "already sells to another account" in sent.error
    assert [method for method, *_ in wire.sent] == ["GET"]


def test_a_retry_finds_a_bill_the_cut_off_call_made_by_its_tico_reference_and_says_when_it_cannot_tell():
    body = {"party": "Vendor:9", "number": "B-77", "day": "2026-10-05", "due": "2026-11-05", "memo": "seats",
            "lines": [{"account": "Account:2", "amount": 1500, "description": "seats"}]}
    marker = f"[tico:{uuid.uuid5(QC.NAMESPACE, 'bill-key')}]"
    wire = Wire(QC.Reply(0), QC.Reply(200, {"QueryResponse": {"Bill": [
        {"Id": "901", "SyncToken": "0", "PrivateNote": f"seats {marker}"}]}}))
    client = _client(wire)
    assert client.send("bill.create", body, "bill-key").ok is False
    assert json.loads(wire.sent[0][3] or b"{}")["PrivateNote"] == f"seats {marker}"
    found = client.find("bill.create", body, "bill-key")
    assert found is not None and (found.ok, found.kind, found.external_id, found.revision) == (True, "Bill", "901", "0")
    assert "TxnDate = '2026-10-05'" in urllib.parse.unquote_plus(wire.sent[1][1])
    assert Q.document_record({"Id": "901", "SyncToken": "0", "PrivateNote": f"seats {marker}", "TxnDate": "2026-10-05",
                              "TotalAmt": "15.00", "VendorRef": {"value": "9"}, "Line": []}, "Bill", {}).memo == "seats"

    assert _client(Wire(QC.Reply(200, {"QueryResponse": {}}))).find("bill.create", body, "bill-key") is None
    unknown = _client(Wire(QC.Reply(400, {"Fault": {"Error": [{"Message": "bad query"}]}}))).find(
        "bill.create", body, "bill-key")
    assert unknown is not None and unknown.final is True and unknown.error.startswith("UNKNOWN")
    assert unknown.unknown is True
    retry = _client(Wire(QC.Reply(503, {}))).find("bill.create", body, "bill-key")
    assert retry is not None and retry.ok is False and retry.final is False
    full = [{"Id": str(n), "PrivateNote": "someone else"} for n in range(1000)]
    capped = _client(Wire(QC.Reply(200, {"QueryResponse": {"Bill": full}}))).find(
        "bill.create", body, "bill-key")
    assert capped is not None and capped.final is True and capped.unknown is True


def test_a_credit_applied_to_an_invoice_is_a_payment_of_nothing_that_links_the_invoice_and_the_credit():
    wire = Wire(QC.Reply(200, {"Payment": {"Id": "930", "SyncToken": "0"}}))
    sent = _client(wire).send("credit.apply", {"invoice": "Invoice:12", "credit": "CreditMemo:20", "party": "Customer:4",
                                               "amount": 500, "day": "2026-10-11", "memo": "", "marker": ""}, "ca-1")
    assert (sent.ok, sent.kind, sent.external_id) == (True, "Payment", "930")
    got = json.loads(wire.sent[0][3] or b"{}")
    assert got["TotalAmt"] == "0.00" and got["CustomerRef"] == {"value": "4"}
    assert got["Line"] == [{"Amount": "5.00", "LinkedTxn": [{"TxnId": "12", "TxnType": "Invoice"}]},
                           {"Amount": "5.00", "LinkedTxn": [{"TxnId": "20", "TxnType": "CreditMemo"}]}]
    pay = Q.settlement_record({"Id": "30", "SyncToken": "1", "TxnDate": "2026-10-11", "TotalAmt": "0",
                               "CustomerRef": {"value": "4"}, "Line": got["Line"]}, "Payment")
    assert (pay.total, pay.links, pay.credits) == (0, (("Invoice:12", 500),), (("CreditMemo:20", 500),))


def test_a_bill_paid_from_a_credit_card_goes_out_as_a_card_payment_on_that_card_account():
    wire = Wire(QC.Reply(200, {"BillPayment": {"Id": "940", "SyncToken": "0"}}))
    client = _client(wire)
    sent = client.send("payment.bill", {"party": "Vendor:9", "bank": "Account:1", "total": 1500, "day": "2026-10-12",
                                        "memo": "", "links": [["Bill:12", 1500]], "card": True}, "pay-card")
    assert (sent.ok, sent.kind) == (True, "BillPayment")
    got = json.loads(wire.sent[0][3] or b"{}")
    assert got["PayType"] == "CreditCard" and got["CreditCardPayment"] == {"CCAccountRef": {"value": "1"}}
    assert "CheckPayment" not in got


def test_a_record_in_another_currency_carries_its_currency_even_at_a_rate_of_one():
    row = {"Id": "5", "SyncToken": "0", "TxnDate": "2026-10-10", "TotalAmt": "5.00", "CustomerRef": {"value": "4"},
           "CurrencyRef": {"value": "EUR"}, "ExchangeRate": "1", "Line": []}
    [made] = Q.records("Invoice", [row], {})
    assert isinstance(made, M.Document) and made.ext.currency == "EUR"


def test_an_active_item_is_the_one_an_income_account_sends_when_an_inactive_item_shares_it():
    items = Q.item_accounts([{"Id": "8", "Active": False, "IncomeAccountRef": {"value": "40"}},
                             {"Id": "7", "Active": True, "IncomeAccountRef": {"value": "40"}}])
    assert items["8"]["active"] is False and items["7"]["active"] is True
    assert Q.income_items(items) == {items["7"]["income"]: "7"}


def test_a_taxed_invoice_keeps_its_tax_apart_from_its_lines():
    items = Q.item_accounts([{"Id": "7", "IncomeAccountRef": {"value": "40"}}])
    doc = Q.document_record({"Id": "12", "SyncToken": "0", "DocNumber": "I-12", "TxnDate": "2026-10-05",
                             "TotalAmt": "105.00", "CustomerRef": {"value": "4"}, "TxnTaxDetail": {"TotalTax": "5.00"},
                             "Line": [{"Amount": "100.00", "DetailType": "SalesItemLineDetail",
                                       "SalesItemLineDetail": {"ItemRef": {"value": "7"}}}]}, "Invoice", items)
    assert (doc.total, doc.tax) == (10500, 500)


def test_quickbooks_account_types_keep_payables_receivables_and_sales_tax():
    base = {"Id": "9", "SyncToken": "1", "Name": "Tax", "AcctNum": "2300", "Active": True}
    tax = Q.account_record({**base, "AccountType": "Other Current Liability", "AccountSubType": "SalesTaxPayable"})
    payable = Q.account_record({**base, "AccountType": "Accounts Payable"})
    receivable = Q.account_record({**base, "AccountType": "Accounts Receivable"})
    assert tax is not None and tax.subtype == M.SALES_TAX
    assert payable is not None and (payable.type, payable.subtype) == ("liability", "payable")
    assert receivable is not None and (receivable.type, receivable.subtype) == ("asset", "receivable")


def test_a_change_feed_cut_at_its_cap_is_complete_only_to_a_second_before_its_newest_change_of_its_entity():
    def obj(i: int, when: str) -> dict[str, object]:
        return {"Id": str(i), "SyncToken": "0", "MetaData": {"LastUpdatedTime": when}}

    whole = {"CDCResponse": [{"QueryResponse": [{"Account": [obj(i, "2026-10-15T12:00:00-07:00") for i in range(10)]}]}]}
    assert Q.change_through(whole, "Account") == ""
    cut = {"CDCResponse": [{"QueryResponse": [{"Invoice": [obj(i, "2026-10-15T12:00:00-07:00") for i in range(999)]
                                                          + [obj(1000, "2026-10-15T12:00:05-07:00")]}]}]}
    assert Q.change_through(cut, "Invoice") == "2026-10-15T19:00:04.000000Z"
    assert Q.change_through(cut, "Account") == ""


def test_items_are_read_page_by_page_past_a_thousand_and_active_or_not():
    first = [{"Id": str(i), "IncomeAccountRef": {"value": "40"}} for i in range(1000)]
    second = [{"Id": str(i), "IncomeAccountRef": {"value": "40"}} for i in range(1000, 1003)]
    wire = Wire(QC.Reply(200, {"QueryResponse": {"Item": first}}), QC.Reply(200, {"QueryResponse": {"Item": second}}))
    known = _client(wire)._item_accounts()
    assert isinstance(known, dict) and len(known) == 1003
    urls = [urllib.parse.unquote_plus(sent[1]) for sent in wire.sent]
    assert len(urls) == 2 and all("WHERE Active IN (true,false)" in url for url in urls)
    assert "STARTPOSITION 1001" in urls[1]


def test_a_change_feed_answer_cut_at_its_cap_is_read_through_to_a_second_before_its_newest_change():
    objects = [{"Id": str(i), "SyncToken": "0", "MetaData": {"LastUpdatedTime": "2026-10-15T12:00:00-07:00"}}
               for i in range(999)]
    objects.append({"Id": "1000", "SyncToken": "0", "MetaData": {"LastUpdatedTime": "2026-10-15T12:00:05-07:00"}})
    wire = Wire(QC.Reply(200, {"CDCResponse": [{"QueryResponse": [{"Invoice": objects}]}]}),
                QC.Reply(200, {"QueryResponse": {"Item": []}}))
    read = _client(wire).changed({"Invoice": "2026-10-15T10:00:00Z"})
    assert read.ok and read.through == (("Invoice", "2026-10-15T19:00:04.000000Z"),)
    assert "entities=Invoice&" in wire.sent[0][1] or "entities=Invoice" in wire.sent[0][1]


def test_a_journal_line_and_a_posted_line_read_to_the_other_side_keep_the_provider_line_ids():
    journal = {"Id": "50", "SyncToken": "0", "TxnDate": "2026-10-09", "Line": [
        {"Id": "1", "Amount": "100.00", "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": "40"}}},
        {"Id": "2", "Amount": "100.00", "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": "1"}}}]}
    sale = {"Id": "60", "SyncToken": "0", "TxnDate": "2026-10-09", "TotalAmt": "100.00",
            "DepositToAccountRef": {"value": "1"},
            "Line": [{"Id": "9", "Amount": "100.00", "DetailType": "AccountBasedExpenseLineDetail",
                      "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "40"}}}]}
    [entry] = Q.records("JournalEntry", [journal], {})
    [posted] = Q.records("SalesReceipt", [sale], {})
    assert sorted((x.account, x.key) for x in entry.lines) == [("Account:1", "2"), ("Account:40", "1")]
    assert sorted((x.account, x.key) for x in posted.lines) == [("Account:1", ""), ("Account:40", "9")]


def test_a_deposit_line_keeps_its_provider_line_id():
    row = {"Id": "70", "SyncToken": "0", "TxnDate": "2026-10-09", "TotalAmt": "15.00",
           "DepositToAccountRef": {"value": "1"},
           "Line": [{"Id": "7", "Amount": "15.00", "DetailType": "DepositLineDetail",
                     "DepositLineDetail": {"AccountRef": {"value": "40"}}}]}
    [made] = Q.records("Deposit", [row], {})
    assert sorted((x.account, x.amount, x.key) for x in made.lines) == [
        ("Account:1", 1500, ""), ("Account:40", -1500, "7")]


def test_a_purchase_is_read_as_an_entry_between_its_bank_and_its_expense_account():
    row = {"Id": "50", "SyncToken": "0", "TxnDate": "2026-10-09", "TotalAmt": "100.00", "PaymentType": "Cash",
           "AccountRef": {"value": "1"}, "Line": [{"Amount": "100.00", "DetailType": "AccountBasedExpenseLineDetail",
                                                   "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "40"}}}]}
    [made] = Q.records("Purchase", [row], {})
    assert isinstance(made, M.Journal)
    assert sorted((x.account, x.amount) for x in made.lines) == [("Account:1", -10000), ("Account:40", 10000)]


def test_a_quickbooks_payroll_expenses_account_is_marked_payroll_and_an_ordinary_expense_is_not():
    base = {"Id": "7", "SyncToken": "1", "Name": "Payroll expenses", "AccountType": "Expense", "Active": True}
    assert Q.account_record({**base, "AccountSubType": "PayrollExpenses"}).payroll is True
    assert Q.account_record({**base, "AccountSubType": "OtherMiscellaneousServiceExpense"}).payroll is False


def test_a_quickbooks_payroll_liability_that_wages_pass_through_is_marked_payroll_and_an_ordinary_one_is_not():
    base = {"Id": "8", "SyncToken": "1", "Name": "Wages payable", "AccountType": "Other Current Liability",
            "Active": True}
    assert Q.account_record({**base, "AccountSubType": "PayrollClearing"}).payroll is True
    assert Q.account_record({**base, "AccountSubType": "PayrollTaxPayable"}).payroll is True
    assert Q.account_record({**base, "AccountSubType": "OtherCurrentLiabilities"}).payroll is False
