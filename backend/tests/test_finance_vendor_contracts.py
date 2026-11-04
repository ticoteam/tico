"""Finance's QuickBooks Online and Xero contracts: the fields each reader takes, pinned to the recorded-style answers in
backend/tests/fixtures/vendors/ (the same fixtures and source notes as test_vendor_contracts.py).

These readers are Finance's own core layer, so their contracts sit with Finance's tests: scripts/finance_lint.py lets
test_finance_* files import backend/finance/core, and no other test file may. No network: block_outside_network is on.
"""
import json
from pathlib import Path

import pytest

from backend.finance.core import mirror as M
from backend.finance.core import qbo, xero
from backend.tests.live import block_outside_network

FIXTURES = Path(__file__).parent / "fixtures" / "vendors"


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    block_outside_network(monkeypatch, "the vendor contracts make no network calls")


def recorded(name):
    """A fixture's recorded answers, with its source note required."""
    data = json.loads((FIXTURES / name).read_text())
    assert isinstance(data.get("_source"), str) and data["_source"], f"{name} has no source note"
    return {key: value for key, value in data.items() if key != "_source"}


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
