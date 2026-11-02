"""Contract tests for the outside systems Tico reads: the fields each reader takes, pinned to recorded-style answers.

The other tests fake these systems with shapes their authors wrote by hand. These tests feed the same readers the
answers in backend/tests/fixtures/vendors/, and each fixture's "_source" says where its shape comes from: Tico HQ's
sender (its checkout is read-only here) or the vendor's public API reference. None is a live capture, so a vendor that
renames a field shows up only when someone re-derives the fixture. Each assertion checks a value, not just that a key
exists: a renamed key reads as zero or empty and fails here.

Granola's recorded shapes are covered by test_granola_recorded_shapes.py. No network: block_outside_network is on.
"""
import contextlib
import hashlib
import hmac
import json
from datetime import timezone
from pathlib import Path

import httpx
import pytest

from backend import company_billing, github, tico_storage
from backend.finance import stripe_sync
from backend.finance.core import mirror as M
from backend.finance.core import qbo, xero
from backend.finance.mercury import Answer
from backend.github_app import GitHubApp
from backend.tests.live import block_outside_network
from connectors import slack

FIXTURES = Path(__file__).parent / "fixtures" / "vendors"


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    block_outside_network(monkeypatch, "the vendor contracts make no network calls")


def recorded(name):
    """A fixture's recorded answers, with its source note required."""
    data = json.loads((FIXTURES / name).read_text())
    assert isinstance(data.get("_source"), str) and data["_source"], f"{name} has no source note"
    return {key: value for key, value in data.items() if key != "_source"}


# -- Tico HQ: file hosting credentials (backend/tico_storage.py parse) -------------------------------------------------

def test_hq_hands_the_hosting_credentials_under_its_own_expiry_name():
    hq = recorded("hq_storage_answer.json")["answer"]
    out = tico_storage.parse(hq)
    assert out["state"] == "on"
    assert out["address"] == "acme-files.ticofiles.com"
    assert out["bucket"] == "tico-files-us"
    assert out["prefix"] == "c/acme"
    assert out["keys"] == ("AKIATICOHOSTING0001", "contract-secret-access-key", "contract-session-token")
    assert out["expires_at"] == "2099-01-01T00:00:00Z"
    assert out["signing"] == ("k1", hq["signing"]["secret"])


def test_a_renamed_credentials_expiry_is_refused_rather_than_read_as_missing():
    hq = recorded("hq_storage_answer.json")["answer"]
    renamed = json.loads(json.dumps(hq))
    renamed["credentials"]["expires_at"] = renamed["credentials"].pop("expires")
    with pytest.raises(ValueError, match="credentials"):
        tico_storage.parse(renamed)


# -- Tico HQ: company billing (backend/company_billing.py parse) ------------------------------------------------------

def test_hq_billing_answer_reads_every_field_the_page_shows():
    body = json.dumps(recorded("hq_billing_answer.json")["answer"]).encode()
    out = company_billing.parse(body)
    assert out["state"] == "low"
    assert out["balance_micros"] == 4_200_000
    assert out["min_balance_micros"] == 5_000_000
    assert out["last_day_micros"] == 150_000
    assert out["record_only"] is False          # the reader's default is True: a renamed key would read as record-only
    assert out["months"] == ["2026-10", "2026-09"]
    [credit] = out["credits"]
    assert (credit["label"], credit["amount_micros"], credit["left_micros"], credit["expires"], credit["current"]) == (
        "Purchased credit", 10_000_000, 4_200_000, "2027-10-01", True)
    [period] = out["usage"]
    assert (period["period"], period["lines"][0]["product"], period["lines"][0]["amount_micros"]) == (
        "2026-10-03", "Gateway", 5_800_000)
    assert out["statement"]["credits_by_source"] == {"purchase": 10_000_000}
    assert out["statement"]["closing_micros"] == 4_200_000
    assert out["booking_url"] == "https://book.acme.example/credit"
    assert out["cloud"] is None                 # HQ main sends no cloud key, which reads as not a cloud company


def test_an_unknown_billing_state_is_refused():
    data = recorded("hq_billing_answer.json")["answer"]
    data["state"] = "frozen"
    with pytest.raises(ValueError, match="not a billing answer"):
        company_billing.parse(json.dumps(data).encode())


# -- Stripe: the probe that connects an account (backend/finance/stripe_sync.py probe) --------------------------------

class StripeAnswers:
    """The two calls probe() makes, answered with the recorded objects."""

    def __init__(self, account, balance):
        self._account, self._balance = account, balance

    def account(self):
        return self._account

    def balance(self):
        return self._balance


def test_stripe_probe_reads_the_account_id_and_balances_by_currency():
    parts = recorded("stripe_answers.json")
    got = stripe_sync.probe(StripeAnswers(Answer(200, parts["account"]), Answer(200, parts["balance"])))
    assert got == ({"account": "acct_1ContractTest0001", "balances": [
        {"currency": "EUR", "available": 0, "pending": 900},
        {"currency": "USD", "available": 12_345, "pending": 678},
    ]}, "")


def test_a_stripe_refusal_is_said_without_echoing_the_body():
    refused = Answer(401, {"error": {"message": "Invalid API Key provided: sk_test_contract"}})
    got, why = stripe_sync.probe(StripeAnswers(refused, Answer(200, {})))
    assert got == {}
    assert "refused the key" in why
    assert "sk_test" not in why


# -- QuickBooks Online: entity rows (backend/finance/core/qbo.py records) ----------------------------------------------

def test_a_quickbooks_account_is_read_by_its_entity_fields():
    [made] = qbo.records("Account", [recorded("quickbooks_answers.json")["account"]])
    assert (made.code, made.name, made.type, made.subtype, made.active) == ("1100", "Checking", "asset", "bank", True)
    assert (made.ext.kind, made.ext.external_id, made.ext.revision) == ("Account", "35", "0")


def test_a_quickbooks_vendor_is_a_vendor_with_its_email():
    [made] = qbo.records("Vendor", [recorded("quickbooks_answers.json")["vendor"]])
    assert (made.kind, made.name, made.emails, made.active) == ("vendor", "Acme Supplies", ("billing@acme.example",), True)
    assert (made.ext.kind, made.ext.external_id, made.ext.revision) == ("Vendor", "56", "1")


def test_a_quickbooks_change_feed_deletion_keeps_only_its_identity():
    [made] = qbo.records("Vendor", [recorded("quickbooks_answers.json")["deleted_vendor"]])
    assert isinstance(made, M.Ext)
    assert (made.kind, made.external_id, made.revision, made.deleted) == ("Vendor", "57", "2", True)


# -- Xero: contacts (backend/finance/core/xero.py contact_record, records) --------------------------------------------

def test_a_xero_contact_is_a_supplier_with_its_email():
    row = recorded("xero_answers.json")["contact"]
    made = xero.contact_record(row)
    assert (made.kind, made.name, made.emails, made.active) == ("vendor", "Acme Supplies", ("billing@acme.example",), True)
    assert (made.ext.kind, made.ext.external_id) == ("Contact", "5c1f8a0e-7d4b-4c3e-9b1e-2a6f0c9d8e71")
    assert "2024-11-01" in made.ext.revision     # UpdatedDateUTC read from Xero's /Date(ms+0000)/ form
    [via_page] = xero.records("parties", [row], lambda name: "")
    assert via_page == made


def test_an_archived_xero_contact_is_inactive():
    row = dict(recorded("xero_answers.json")["contact"], ContactStatus="ARCHIVED")
    assert xero.contact_record(row).active is False


# -- Slack: the Web API call and its error (connectors/slack.py call, slack_error) ------------------------------------

class FakeHTTP:
    """slack._request: answers with the recorded payload and keeps each URL asked for."""

    def __init__(self, payload):
        self.payload, self.urls = payload, []

    def __call__(self, url, data, headers, timeout):
        self.urls.append(url)
        return 200, {}, json.dumps(self.payload).encode()


def test_slack_history_is_read_by_its_message_fields(monkeypatch):
    http = FakeHTTP(recorded("slack_answers.json")["history"])
    monkeypatch.setattr(slack, "_request", http)
    data = slack.call("conversations.history", {"channel": "C012ABCDEFG", "limit": 200},
                      token="xoxb-contract", sleep=lambda s: None)
    assert http.urls == [slack.API + "conversations.history?channel=C012ABCDEFG&limit=200"]
    [message] = data["messages"]
    assert data["response_metadata"]["next_cursor"] == ""
    shaped = slack.shape(message, "C012ABCDEFG", {"U012ABCDEF": "ana-test"}, "https://acme.slack.com", timezone.utc)
    assert (shaped["author"], shaped["ts"]) == ("ana-test", "1730419200.000100")
    assert "Kickoff notes" in shaped["text"]


def test_a_slack_missing_scope_names_the_scope_to_add(monkeypatch):
    monkeypatch.setattr(slack, "_request", FakeHTTP(recorded("slack_answers.json")["missing_scope"]))
    with pytest.raises(slack.Failure) as caught:
        slack.call("conversations.history", {"channel": "C012ABCDEFG"}, token="xoxb-contract", sleep=lambda s: None)
    assert caught.value.msg == "Slack conversations.history failed: missing_scope"
    assert "channels:history" in caught.value.hint
    assert "channels:read" in caught.value.hint


# -- GitHub: the App's installations (backend/github_app.py installation) and the webhook signature --------------------

class InstallationStub:
    """The GitHubApp members installation() uses, answered with GitHub's recorded installation list."""

    def __init__(self, installations):
        self.installations, self.saved = installations, []
        self.store = self

    def row(self):
        return {"installation_id": None, "org": "acme"}

    def _jwt(self):
        return "contract-app-jwt"

    def _call(self, method, path, **kw):
        assert (method, path) == ("GET", "/app/installations")
        return httpx.Response(200, json=self.installations)

    def transaction(self):
        return contextlib.nullcontext(self)

    def execute(self, sql, params):
        self.saved.append(params)


def test_github_installations_are_matched_by_their_account_login():
    listed = recorded("github_answers.json")["installations"]
    stub = InstallationStub(listed)
    assert GitHubApp.installation(stub, refresh=True) == 4242424
    assert stub.saved == [(4242424,)]


def test_a_github_webhook_signature_is_the_sha256_of_the_body():
    body = b'{"action": "opened"}'
    signed = "sha256=" + hmac.new(b"contract-webhook-secret", body, hashlib.sha256).hexdigest()
    assert github.verify("contract-webhook-secret", {"x-hub-signature-256": signed}, body) is True
    assert github.verify("contract-webhook-secret", {"x-hub-signature-256": signed.replace("sha256=", "sha1=")}, body) is False
