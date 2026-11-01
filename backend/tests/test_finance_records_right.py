"""The records right and the view split (plan §3, §4): who writes locations, items and projects, what each viewer
reads of them, the item and location fields, what a bot may do to an archived item, and the source and read time every
record result carries. Case IDs are the operations test cases (X11, X12, X13, B5, P2a, X16, X21, H2a)."""
import json
import sqlite3

import pytest

from backend.finance import catalog
from backend.finance import reads as FR
from backend.finance.core.errors import FinanceError
from backend.records import store as RS
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

WINDOW = ("2026-10-01", "2026-10-31")


def _company_admins(c, *emails: str) -> None:
    """Names the company Admins by email on the access record, as Settings does."""
    stored = json.loads(c.execute("SELECT value_json FROM registry_metadata WHERE key='access'").fetchone()[0])
    stored["admins"] = list(emails)
    c.execute("UPDATE registry_metadata SET value_json=? WHERE key='access'", (json.dumps(stored),))


def _spend(c, amount: int, tags: dict[str, str]) -> None:
    made = act(c, OWNER, "journal", {"date": "2026-10-10", "memo": "Spend", "lines": [
        {"account_id": account(c, "5100"), "amount": amount, "tags": tags},
        {"account_id": account(c, "1000"), "amount": -amount}]})
    assert made.status == "committed", made.reason


def _ok(answer):
    """The action's answer, once it is shown to have committed."""
    assert answer.status == "committed", answer.reason
    return answer


def _by_name(rows: list[dict]) -> dict[str, dict]:
    return {str(r["name"]): r for r in rows}


def test_x11_a_member_without_a_finance_view_reads_names_addresses_and_sale_prices(books):
    with books.transaction() as c:
        customer = str(_ok(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}))
                       .result["contact_id"])
        denver_id = str(_ok(act(c, OWNER, "location_create", {"name": "Denver", "kind": "store",
                                                              "address": {"city": "Denver"}})).result["location_id"])
        _ok(act(c, OWNER, "item_create", {"name": "Gadget", "kind": "service", "sku": "GAD-1", "price": 2_500,
                                          "description": "Blue, two-pack", "taxable": True,
                                          "income_account_id": account(c, "4000"),
                                          "expense_account_id": account(c, "5100")}))
        _ok(act(c, OWNER, "project_create", {"name": "Job 1", "customer_id": customer}))
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        eve = "human:eve"
        item = _by_name(FR.records(c, eve, kind="item", at=AT)["items"])["Gadget"]
        assert {"sku", "kind", "price", "description", "taxable"} <= set(item) and item["price"] == 2_500
        assert "income_account_id" not in item and "expense_account_id" not in item     # set accounts stay hidden
        denver = _by_name(FR.records(c, eve, kind="location", at=AT)["locations"])["Denver"]
        assert denver["address"] == {"city": "Denver"}
        assert _by_name(FR.records(c, eve, kind="project", at=AT)["projects"])["Job 1"]["customer_id"] is None
        _spend(c, 1_000, {"location": str(denver_id)})
        left_out = FR.spend_by(c, eve, "location", *WINDOW, top_n=50, at=AT)     # costs are not in eve's view
        assert left_out["total"] == 0 and left_out["rows"] == []


def test_x11a_a_manager_whose_view_is_their_teams_expenses_opens_a_location(books):
    with books.transaction() as c:
        denver = str(_ok(act(c, OWNER, "location_create", {"name": "Boise", "kind": "store"})).result["location_id"])
        _spend(c, 1_000, {"location": denver, "team": "web"})
        _spend(c, 9_000, {"location": denver, "team": "sales"})
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:cara", "what": "expenses", "whose": "team",
                                            "whose_team": "web"}).status == "committed"
        opened = FR.record(c, "human:cara", kind="location", record_id=denver, at=AT)["location"]
        assert opened["name"] == "Boise"
        # Her P&L by location holds only the lines her view covers, never the location's full total.
        mine = FR.spend_by(c, "human:cara", "location", *WINDOW, top_n=50, at=AT)
        assert mine["total"] == 1_000
        assert FR.spend_by(c, OWNER, "location", *WINDOW, top_n=50, at=AT)["total"] == 10_000


def test_x12_a_granted_records_right_writes_records_and_revoke_takes_effect_on_the_next_request(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything",
                                            "whose": "company"}).status == "committed"
        site = act(c, "human:dan", "location_create", {"name": "Yard", "kind": "warehouse"})
        assert site.status == "committed", site.reason
        item = str(act(c, OWNER, "item_create", {"name": "Desk", "kind": "service", "price": 1_000}).result["item_id"])
        assert act(c, "human:dan", "item_update", {"item_id": item, "price": 1_200}).status == "needs_approval"
        accounts = act(c, "human:dan", "item_update", {"item_id": item, "income_account_id": account(c, "4000")})
        assert accounts.status == "denied" and "books right" in accounts.reason, accounts
        revoked = act(c, OWNER, "right_revoke", {"teammate": "human:dan", "right": "finance.records"})
        assert revoked.status == "committed", revoked.reason
        after = act(c, "human:dan", "location_create", {"name": "Dock", "kind": "warehouse"})
        assert after.status == "denied", after


def test_x12_a_company_admin_holds_the_records_right_and_grants_only_that_right(books):
    with books.transaction() as c:
        _company_admins(c, "ben@acme.example")
        assert act(c, "human:ben", "item_create", {"name": "Lamp", "kind": "service"}).status == "committed"
        assert act(c, "human:ben", "right_grant", {"teammate": "human:eve", "right": "finance.records"}).status == \
            "committed"
        refused = act(c, "human:ben", "right_grant", {"teammate": "human:eve", "right": "finance.book"})
        assert refused.status == "denied", refused
        assert act(c, "human:eve", "item_create", {"name": "Rug", "kind": "service"}).status == "committed"


def test_finance_admin_implies_the_records_right(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.admin"}).status == "committed"
        assert act(c, "human:dan", "item_create", {"name": "Vase", "kind": "service"}).status == "committed"


def test_x13_a_bot_without_the_records_right_asks_a_right_holder_and_writes_nothing(books):
    with books.transaction() as c:
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.book"}).status == "committed"
        asked = act(c, "bot:coder", "item_create", {"name": "Hose", "kind": "service"})
        assert asked.status == "needs_approval", asked
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Hose'").fetchone()[0] == 0
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.records"}).status == "committed"
        assert act(c, "bot:coder", "item_create", {"name": "Hose", "kind": "service"}).status == "committed"


def test_b5_a_bot_cannot_change_an_archived_item(books):
    with books.transaction() as c:
        item = str(act(c, OWNER, "item_create", {"name": "Old pump", "kind": "service"}).result["item_id"])
        assert act(c, OWNER, "item_update", {"item_id": item, "active": False}).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "bot:coder", "right": "finance.records"}).status == "committed"
        refused = act(c, "bot:coder", "item_update", {"item_id": item, "name": "New pump"})
        assert refused.code == "invalid" and "unarchives it" in refused.reason, refused
        assert c.execute("SELECT name FROM items WHERE id=?", (item,)).fetchone()[0] == "Old pump"


def test_p2a_a_category_cannot_go_on_a_line_and_a_bundle_is_refused_while_mirrored(books, monkeypatch):
    with books.transaction() as c:
        category = str(act(c, OWNER, "item_create", {"name": "Parts", "kind": "category"}).result["item_id"])
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        bill = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-10",
                                             "lines": [{"item_id": category, "amount": 1_200}]})
        assert bill.code == "invalid" and "category cannot go on a line" in bill.reason, bill
        monkeypatch.setattr(catalog, "_mirrored", lambda ctx: True)
        bundle = act(c, OWNER, "item_create", {"name": "Starter kit", "kind": "bundle", "price": 5_000})
        assert bundle.code == "invalid" and "bundle cannot be created while the books are mirrored" in bundle.reason


def test_x16_the_database_refuses_a_taxable_flag_outside_zero_or_one(books):
    with pytest.raises(sqlite3.IntegrityError):
        with books.transaction() as c:
            RS.insert(c, "items", {"name": "Bolt", "kind": "service", "taxable": 2, "created_at": AT,
                                   "created_by_id": OWNER})


def test_quantity_on_hand_is_never_set_by_an_action(books):
    with books.transaction() as c:
        refused = act(c, OWNER, "item_create", {"name": "Bolt", "kind": "inventory", "quantity_on_hand": 5})
        assert refused.code == "invalid", refused
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Bolt'").fetchone()[0] == 0


def test_x21_every_record_result_carries_its_source_and_read_time(books):
    with books.transaction() as c:
        item = str(_ok(act(c, OWNER, "item_create", {"name": "Gadget", "kind": "service", "price": 2_500}))
                   .result["item_id"])
        found = FR.search(c, OWNER, kind="item", q="gad", at=AT)["results"]
        assert [(r["source"], r["as_of"]) for r in found] == [("tico", AT)]
        listed = FR.records(c, OWNER, kind="item", at=AT)["items"]
        assert all(r["source"] == "tico" and r["as_of"] == AT for r in listed)
        c.execute("INSERT INTO fin_connections(id, provider, purpose, created_at, created_by) "
                  "VALUES('conn-books','quickbooks','books',?,?)", (AT, OWNER))
        c.execute("INSERT INTO fin_external_refs(id, connection_id, local_table, local_id, external_id, created_at, "
                  "created_by) VALUES('ref-gadget','conn-books','items',?,'Item:9',?,?)", (item, AT, OWNER))
        assert FR.search(c, OWNER, kind="item", q="gad", at=AT)["results"][0]["source"] == "quickbooks"
        assert FR.record(c, OWNER, kind="item", record_id=item, at=AT)["item"]["source"] == "quickbooks"


def test_h2a_a_customer_site_names_its_customer_to_the_receivables_view_only(books):
    with books.transaction() as c:
        customer = str(_ok(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}))
                       .result["contact_id"])
        site = str(act(c, OWNER, "location_create", {"name": "Acme Retail site", "kind": "customer_site",
                                                     "customer_id": customer}).result["location_id"])
        assert act(c, OWNER, "view_defaults_set", {"member": "none"}).status == "committed"
        assert FR.record(c, OWNER, kind="location", record_id=site, at=AT)["location"]["customer_id"] == customer
        assert FR.record(c, "human:eve", kind="location", record_id=site, at=AT)["location"]["customer_id"] is None
        panel = FR.customer_panel(c, OWNER, customer, at=AT)
        assert panel["contact"]["id"] == customer
        assert [loc["id"] for loc in panel["locations"]] == [site]
        assert panel["estimates"] == [] and "invoices" in panel
        with pytest.raises(FinanceError) as hidden:
            FR.customer_panel(c, "human:eve", customer, at=AT)
        assert hidden.value.code == "not_found"


def test_a_project_names_a_customer_the_asker_cannot_see_as_a_missing_one(books):
    with books.transaction() as c:
        customer = str(_ok(act(c, OWNER, "contact_create", {"name": "Acme Retail", "kind": "customer"}))
                       .result["contact_id"])
        assert act(c, OWNER, "right_grant", {"teammate": "human:eve", "right": "finance.records"}).status == "committed"
        real = act(c, "human:eve", "project_create", {"name": "Job 2", "customer_id": customer})
        missing = act(c, "human:eve", "project_create", {"name": "Job 2", "customer_id": "no-such-contact"})
        assert real.code == missing.code == "not_found" and real.reason == missing.reason, (real, missing)
