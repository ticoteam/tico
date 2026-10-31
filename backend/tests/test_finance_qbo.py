"""QuickBooks Online (spec §3.4, §10): webhook signatures and relayed events, the HTTP client's retries and RequestIds, the
mapping of QuickBooks shapes, and the OAuth broker's contract with a fake HQ (the install's tokens live in the vault)."""
import base64
import hashlib
import hmac
import json
import uuid

import pytest

from backend import hook_relay
from backend import trigger_presets as P
from backend.finance import books_link, books_sync
from backend.finance import qbo as QC
from backend.finance.core import qbo as Q
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
    assert len(cdc) == 1 and cdc[0].ext.deleted is True and cdc[0].ext.external_id == "55"


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


class FakeHQ:
    """HQ's broker answers, given to the Gateway link's call in place of the network (what the install sends and gets)."""

    def __init__(self, *answers: object) -> None:
        self.answers, self.calls = list(answers), []

    def as_link_call(self):  # type: ignore[no-untyped-def]
        def call(link, method, path, body=None, idempotency=None, *, area="hooks", **_):  # type: ignore[no-untyped-def]
            self.calls.append({"base": link.base, "area": area, "path": path, "body": body})
            answer = self.answers.pop(0)
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
