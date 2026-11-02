"""The Operations record tools (docs/finance-tools.md): hub_locations, hub_items, hub_projects, hub_equipment,
hub_due_dates and hub_customers read the records through the Finance routes, as their caller, in pages cut to the
Finance payload budget. Each one is smoke-tested over the real server (X14); a bot reads what its routes give it."""
import json

from backend.finance import actions as FA
from backend.tests.test_api import api, setup_attempt  # noqa: F401
from backend.tests.test_mcp import call
from clients import hubtools


def books(api):
    store = api.app.state.store
    with store.transaction() as c:
        assert FA.ensure_setup(c, store.settings.owner_email)


def act(api, kind, payload, op):
    """One Finance action as the owner: it must commit, and its result comes back."""
    err, out = call(api, "hub_finance_act", {"kind": kind, "payload": payload, "operation_id": op})
    assert not err and out["status"] == "committed", out
    return out["result"]


def test_each_record_tool_lists_and_reads_one_record_by_id(api):
    books(api)
    loc = act(api, "location_create", {"name": "Denver depot", "kind": "warehouse"}, "loc-1")["location_id"]
    item = act(api, "item_create", {"name": "Install visit", "kind": "service", "sku": "VISIT-1", "price": 9_500},
               "item-1")["item_id"]
    project = act(api, "project_create", {"name": "Job 12"}, "proj-1")["project_id"]
    van = act(api, "equipment_create", {"name": "Van 3", "kind": "owned", "serial": "VIN-3"}, "eq-1")["equipment_id"]
    due = act(api, "due_date_create", {"name": "Service the furnace", "subject_type": "location", "subject_id": loc,
                                       "repeats": "yearly", "starts_on": "2026-12-01", "assignee_id": "human:ana"},
              "due-1")["due_date_id"]
    customer = act(api, "contact_create", {"name": "Northwind", "kind": "customer"}, "cust-1")["contact_id"]
    # tool, its list key, the record's id, the key its one-record read answers under, and the record's name.
    cases = [("hub_locations", "locations", loc, "location", "Denver depot"),
             ("hub_items", "items", item, "item", "Install visit"),
             ("hub_projects", "projects", project, "project", "Job 12"),
             ("hub_equipment", "equipment", van, "equipment", "Van 3"),
             ("hub_due_dates", "due_dates", due, "due_date", "Service the furnace"),
             ("hub_customers", "customers", customer, "contact", "Northwind")]
    for tool, key, ident, one_key, label in cases:
        err, page = call(api, tool, {})
        assert not err and [r["name"] for r in page[key]] == [label], (tool, page)
        assert page.get("next_cursor") is None, (tool, page)
        err, one = call(api, tool, {"id": ident})
        assert not err and one[one_key]["id"] == ident and one[one_key]["name"] == label, (tool, one)
        err, missing = call(api, tool, {"id": "nothing-here"})
        assert err and missing["error"] == "not_found", (tool, missing)
    err, found = call(api, "hub_locations", {"q": "denver", "kind": "warehouse"})
    assert not err and [r["id"] for r in found["locations"]] == [loc]
    err, none = call(api, "hub_locations", {"kind": "store"})
    assert not err and none["locations"] == []
    err, refused = call(api, "hub_locations", {"kind": "palace"})
    assert err, refused


def test_a_list_page_too_big_is_cut_to_the_finance_budget_and_next_cursor_carries_on(api):
    books(api)
    # Names near the 200-character cap (records/core.py MAX_NAME): the default page of 50 is over the 16k budget, so cut.
    for i in range(60):
        act(api, "location_create", {"name": f"Warehouse {i:03} " + "Long site name " * 12, "kind": "warehouse"},
            f"loc-big-{i}")
    err, first = call(api, "hub_locations", {})
    assert not err and len(json.dumps(first)) <= hubtools.FINANCE_CHARS, first
    rows = [r for r in first["locations"] if r.get("key") != hubtools.FIN_OTHER]
    other = first["locations"][-1]
    assert 0 < len(rows) < 50, len(rows)
    assert other["key"] == hubtools.FIN_OTHER and other["count"] == 50 - len(rows), other
    assert first["next_cursor"]
    err, second = call(api, "hub_locations", {"cursor": first["next_cursor"]})
    assert not err and second["locations"][0]["name"].startswith(f"Warehouse {len(rows):03}"), second
    err, wide = call(api, "hub_locations", {"limit": 200})
    assert not err and len(wide["locations"]) == 60 and len(json.dumps(wide)) <= hubtools.FINANCE_MAX_CHARS


def test_a_bot_reads_what_its_routes_give_it_and_no_customer_invoices(api):
    books(api)
    act(api, "location_create", {"name": "Plant 2", "kind": "plant"}, "loc-rights")
    customer = act(api, "contact_create", {"name": "Northwind", "kind": "customer"}, "cust-view")["contact_id"]
    bot = setup_attempt(api, "ops")[2]["token"]
    err, owner = call(api, "hub_customers", {"id": customer})
    assert not err and owner["invoices"] is not None
    err, seen = call(api, "hub_customers", {"id": customer}, token=bot)
    assert not err and seen["contact"]["name"] == "Northwind", seen
    assert seen["invoices"] is None and seen["estimates"] is None, seen
    err, locations = call(api, "hub_locations", {}, token=bot)
    assert not err and [r["name"] for r in locations["locations"]] == ["Plant 2"], locations
