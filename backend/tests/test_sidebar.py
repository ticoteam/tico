"""The company's sidebar (backend/sidebar.py): the business type's defaults are set once, in setup, and never over a choice;
the owner and admins change the sidebar while a member only reads it; an upgrade keeps the Operations tabs that records
and connections need; and hiding changes no permission, no data and no API or MCP answer. Cross-company isolation needs no test here:
each hosted company runs its own server and database (docs/cell.md), so no process holds two companies' choices."""

import json

from fastapi.testclient import TestClient

from backend import sidebar
from backend.app import create_app
from backend.finance.store import mirror as SX
from backend.tests.test_api import api, headers  # noqa: F401  (the api fixture)
from backend.tests.test_mcp import call as mcp


def config(api):
    return api.get("/api/v2/config", headers=headers()).json()


def put_sidebar(api, body, token="ana-test", expected=200):
    r = api.put("/api/v2/company/sidebar", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def setup_save(api, body, expected=200):
    """The owner's setup save. The fixture completes setup, so each test reopens it first."""
    r = api.put("/api/v2/onboarding", json=body, headers=headers("ana-test"))
    assert r.status_code == expected, r.text


def reopen_setup(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='onboarding'")


def test_each_business_type_hides_the_operations_tabs_its_table_leaves_out():
    assert sidebar.defaults("field_service") == []
    assert sidebar.defaults("restaurant") == ["operations.projects"]
    assert sidebar.defaults("software") == ["operations.locations", "operations.equipment"]
    assert sidebar.defaults("retail") == ["operations"]
    assert sidebar.defaults("other") == ["operations"]


def test_setup_derives_the_defaults_and_never_overwrites_a_choice(api):
    """X1 and X5c through setup: the answer sets the defaults; a changed answer still in setup derives them again; once a
    person has chosen a list, a later answer leaves it alone; a bad answer is refused and writes nothing."""
    reopen_setup(api)
    setup_save(api, {"business_type": "software"})
    assert config(api)["hidden_sections"] == ["operations.locations", "operations.equipment"]
    setup_save(api, {"business_type": "field_service"})           # still the untouched default: derived again
    assert config(api)["business_type"] == "field_service"
    assert config(api)["hidden_sections"] == []
    put_sidebar(api, {"hidden_sections": ["operations.equipment"]})  # a person's choice
    setup_save(api, {"business_type": "retail"})
    assert config(api)["business_type"] == "retail"
    assert config(api)["hidden_sections"] == ["operations.equipment"]
    setup_save(api, {"business_type": "bakery"}, expected=422)
    assert config(api)["business_type"] == "retail"


def test_the_owner_and_admins_change_the_sidebar_and_a_member_reads_it(api):
    """X4 on the server: unset is not empty; a member reads who may change it and cannot; an admin can; a bad key is
    refused by name; each change is an audited event with its before and after."""
    assert config(api)["hidden_sections"] is None
    shown = api.get("/api/v2/company/sidebar", headers=headers("cara-test")).json()
    assert shown["may_change"] is False and shown["who_may_change"] == "the owner or an admin"
    assert api.put("/api/v2/company/sidebar", json={"hidden_sections": []},
                   headers=headers("cara-test")).status_code == 403
    assert put_sidebar(api, {"hidden_sections": []}, token="ben-test")["hidden_sections"] == []
    assert config(api)["hidden_sections"] == []                    # empty stays empty: everything shows
    assert put_sidebar(api, {"hidden_sections": ["operations", "growth", "growth"]})["hidden_sections"] == [
        "growth", "operations"]
    put_sidebar(api, {"hidden_sections": ["operations.spreadsheet"]}, expected=422)
    put_sidebar(api, {"hidden_sections": None}, expected=422)
    put_sidebar(api, {"business_type": "bakery"}, expected=422)
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT actor, detail_json FROM events WHERE action='settings.sidebar' ORDER BY ts, rowid").fetchall()
    assert [row[0] for row in rows][-2:] == ["human:ben", "human:ana"]
    assert json.loads(rows[-1][1])["after"]["hidden_sections"] == ["growth", "operations"]


LOCATION = ("INSERT INTO locations (id, name, created_at, created_by_id) "
            "VALUES ('loc-acme', 'Acme Shop', '2026-01-01T00:00:00Z', 'human:ana')")
ITEM = ("INSERT INTO items (id, name, kind, created_at, created_by_id) "
        "VALUES ('item-acme', 'Acme visit', 'service', '2026-01-01T00:00:00Z', 'human:ana')")
BOOKS = ("INSERT INTO fin_connections (id, provider, purpose, status, created_at, created_by) "
         "VALUES ('conn-books', 'quickbooks', 'books', 'active', '2026-01-01T00:00:00Z', 'human:ana')")
STRIPE = ("INSERT INTO fin_connections (id, provider, purpose, status, created_at, created_by) "
          "VALUES ('conn-stripe', 'stripe', 'payments', 'active', '2026-01-01T00:00:00Z', 'human:ana')")


def upgrade(api, *sql):
    """A start of the server on an install set up before business types: the sidebar is unset, `sql` adds the data the
    install holds, and `ensure` runs once, as it does at start. Returns what the clients read from /api/v2/config."""
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (sidebar.KEY,))
        for statement in sql:
            c.execute(statement)
        sidebar.ensure(c)
    return config(api)


def test_an_upgrade_with_no_records_or_connections_hides_operations(api):
    """X6: an install set up before business types, holding nothing Operations is for, hides the whole section."""
    cfg = upgrade(api)
    assert cfg["hidden_sections"] == ["operations"]
    assert cfg["business_type"] is None


def test_an_upgrade_shows_the_operations_tabs_whose_tables_hold_rows(api):
    """X7: a QuickBooks import's locations and items keep their tabs on; Projects and Equipment have no rows, so they
    stay hidden, and Operations itself stays on."""
    cfg = upgrade(api, LOCATION, ITEM)
    assert cfg["hidden_sections"] == ["operations.projects", "operations.equipment"]
    assert "operations" not in sidebar.hidden_now(cfg["hidden_sections"])


def test_a_books_connection_shows_locations_and_products_before_its_first_sync(api):
    """A connected book with no rows yet: the sync has not run at the upgrade, so the connection alone keeps Locations and
    Products & services on, and the sync's records land on a visible section."""
    cfg = upgrade(api, BOOKS)
    assert cfg["hidden_sections"] == ["operations.projects", "operations.equipment"]


def test_a_stripe_connection_shows_products_and_services_only(api):
    cfg = upgrade(api, STRIPE)
    assert cfg["hidden_sections"] == ["operations.locations", "operations.projects", "operations.equipment"]


def test_a_disconnected_books_connection_counts_for_nothing(api):
    cfg = upgrade(api, BOOKS.replace("'active'", "'disconnected'"))
    assert cfg["hidden_sections"] == ["operations"]


def test_a_books_connection_with_records_waiting_to_come_in_shows_locations_and_products(api):
    """X7 with pending imports: a books connection still holding provider records to take in (the mirror's `pending`)
    keeps Locations and Products & services on, though its sync has written no rows yet. Once it is disconnected, its
    waiting records count for nothing."""
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (sidebar.KEY,))
        c.execute(BOOKS)
        SX.set_mirror_state(c, "conn-books", {"pending": {"Item:9": {"kind": "Item", "id": "9"}}})
        assert sidebar._connections(c) == (True, True, False)
        sidebar.ensure(c)
    assert config(api)["hidden_sections"] == ["operations.projects", "operations.equipment"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE fin_connections SET status='disconnected' WHERE id='conn-books'")
        assert sidebar._connections(c) == (False, False, False)


def test_setup_that_completes_without_an_answer_keeps_what_its_records_show(api):
    """Plan §5 through setup: a company that completes setup with no business type is Other, and keeps the Operations
    tabs its records need on, rather than the whole section hidden."""
    reopen_setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (sidebar.KEY,))
        c.execute(LOCATION)
        c.execute(ITEM)
        api.app.state.onboarding.complete(c, api.app.state.auth.owner_identity(c), seats=False)
    assert config(api)["business_type"] == "other"
    assert config(api)["hidden_sections"] == ["operations.projects", "operations.equipment"]


def test_setup_that_completes_without_an_answer_and_no_records_hides_operations(api):
    reopen_setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (sidebar.KEY,))
        api.app.state.onboarding.complete(c, api.app.state.auth.owner_identity(c), seats=False)
    assert config(api)["business_type"] == "other"
    assert config(api)["hidden_sections"] == ["operations"]


def test_running_the_upgrade_again_changes_nothing_after_an_admin_choice(api):
    """Defaults apply once: after an admin has chosen, another run of `ensure` (a restart) changes nothing and writes no
    event."""
    upgrade(api, LOCATION)
    put_sidebar(api, {"hidden_sections": []}, token="ben-test")

    def audited():
        with api.app.state.store.read() as c:
            return c.execute("SELECT COUNT(*) FROM events WHERE action='settings.sidebar'").fetchone()[0]

    before, count = config(api), audited()
    with api.app.state.store.transaction() as c:
        sidebar.ensure(c)
    assert config(api) == before and before["hidden_sections"] == []
    assert audited() == count


def test_the_upgrade_default_applies_once_and_a_choice_survives_a_restart(api):
    """Defaults apply once: a person's choice is not overwritten when the server starts again on the same database."""
    put_sidebar(api, {"hidden_sections": ["operations.equipment"]})
    with TestClient(create_app(api.app.state.store.settings)) as restarted:
        assert restarted.get("/api/v2/config", headers=headers()).json()["hidden_sections"] == ["operations.equipment"]


def test_hiding_a_section_still_answers_the_api_and_mcp_and_changes_no_permission(api):
    """Hiding is display only: the API and MCP answer as before, and a member's access is the same before and after."""
    paths = ["/api/v2/growth/channels", "/api/v2/goals", "/api/v2/locations"]
    before = {(p, who): api.get(p, headers=headers(who)).status_code for p in paths for who in ("ana-test", "cara-test")}
    assert before[("/api/v2/goals", "ana-test")] == 200
    put_sidebar(api, {"hidden_sections": list(sidebar.KEYS)})
    after = {(p, who): api.get(p, headers=headers(who)).status_code for p in paths for who in ("ana-test", "cara-test")}
    assert after == before                       # the same answer to the owner and to a member, hidden or not
    assert after[("/api/v2/growth/channels", "ana-test")] == 200
    err, _ = mcp(api, "hub_growth_channel_list", {}, "ana-test")
    assert not err


def test_all_operations_tabs_hidden_hides_the_section():
    """A section with every one of its base tabs hidden is hidden; one tab still shown keeps it on."""
    tabs = list(sidebar.OPERATIONS_TABS)
    assert sidebar.hidden_now(tabs) == ["operations", *tabs]
    assert "operations" not in sidebar.hidden_now(tabs[:-1])
