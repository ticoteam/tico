"""Shared records (backend/records) as Finance uses them: migration 76 keeps every project tag and moves the project
dimension to a record, a bill line's item supplies its account and an inactive item is refused, a line's location or
project tag must name an active record, and a price change asks before it takes effect."""
import sqlite3

import pytest

from backend.finance.store import schema as FS
from backend.records import store as RS
from backend.tests.test_finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401

# The line tags and dimensions tables as migration 75 left them: kinds without location, class, or item.
OLD_LINE_TAGS = """CREATE TABLE fin_line_tags (
  line_id TEXT NOT NULL REFERENCES fin_lines(id),
  kind TEXT NOT NULL CHECK (kind IN ('team','teammate','budget','project','custom')), value TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, PRIMARY KEY (line_id, kind))"""
OLD_DIMENSIONS = """CREATE TABLE fin_dimensions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('project','custom')), value TEXT NOT NULL,
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (kind, value))"""


def _as_before_76(c, table: str, ddl: str) -> None:
    """Rebuild `table` with its old definition, keeping its rows (what migration 75 left on an install)."""
    c.execute(f"ALTER TABLE {table} RENAME TO {table}_now")
    c.execute(ddl)
    cols = ",".join(str(r[1]) for r in c.execute(f"PRAGMA table_info({table}_now)").fetchall())
    c.execute(f"INSERT INTO {table}({cols}) SELECT {cols} FROM {table}_now")
    c.execute(f"DROP TABLE {table}_now")


def _projects(c) -> dict[str, tuple[str, int]]:
    return {str(r[0]): (str(r[1]), int(r[2])) for r in c.execute("SELECT id, name, archived_at IS NULL FROM projects")}


def test_a_legacy_project_tag_keeps_its_value_and_the_project_dimension_becomes_a_record(books):
    with books.transaction() as c:
        for pid in ("site", "Bldg 7"):
            c.execute("INSERT INTO projects(id, name, status, archived_at, created_at, created_by_id) "
                      "VALUES(?,?,'open',NULL,?,?)", (pid, pid, AT, OWNER))
        posted = act(c, OWNER, "journal", {"date": "2026-10-10", "memo": "Tagged", "lines": [
            {"account_id": account(c, "5100"), "amount": 1_000, "tags": {"project": "site"}},
            {"account_id": account(c, "5100"), "amount": 400, "tags": {"project": "Bldg 7"}},
            {"account_id": account(c, "1000"), "amount": -1_400}]})
        assert posted.status == "committed", posted.reason
        # Install state before migration 76: the project was a dimension and no record existed.
        c.execute("DELETE FROM projects")
        c.execute("INSERT INTO fin_dimensions(id, kind, value, name, active, created_at, created_by) "
                  "VALUES('dim-site','project','site','Site (dimension)',1,?,?)", (AT, OWNER))
        _as_before_76(c, "fin_line_tags", OLD_LINE_TAGS)
        _as_before_76(c, "fin_dimensions", OLD_DIMENSIONS)
        c.execute("DELETE FROM cloud_migrations WHERE version=76")

        FS.catalog(c, AT)
        assert _projects(c) == {"site": ("Site (dimension)", 1), "Bldg 7": ("Bldg 7", 1)}
        assert c.execute("SELECT COUNT(*) FROM fin_dimensions WHERE kind='project'").fetchone()[0] == 0
        assert sorted(str(r[0]) for r in c.execute("SELECT value FROM fin_line_tags WHERE kind='project'")) == \
            ["Bldg 7", "site"]
        assert "'location'" in str(c.execute("SELECT sql FROM sqlite_master WHERE name='fin_line_tags'").fetchone()[0])
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='fin_line_tags_value'").fetchone() is not None
        before = _projects(c)
        FS.catalog(c, AT)                                  # a second run changes nothing
        assert _projects(c) == before


def test_a_bill_line_naming_an_item_takes_its_expense_account_and_an_inactive_item_is_refused(books):
    with books.transaction() as c:
        item = act(c, OWNER, "item_create", {"name": "Widget", "kind": "non_inventory", "price": 500,
                                             "expense_account_id": account(c, "5100")})
        assert item.status == "committed", item.reason
        item_id = str(item.result["item_id"])
        vendor = str(act(c, OWNER, "contact_create", {"name": "Acme Supplies", "kind": "vendor"}).result["contact_id"])
        bill = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-10",
                                             "lines": [{"item_id": item_id, "amount": 1_200}]})
        assert bill.status == "committed", bill.reason
        lines = c.execute("SELECT account_id, item_id FROM fin_bill_lines WHERE bill_id=?",
                          (bill.result["bill_id"],)).fetchall()
        assert [(str(a), str(i)) for a, i in lines] == [(account(c, "5100"), item_id)]
        assert act(c, OWNER, "item_update", {"item_id": item_id, "active": False}).status == "committed"
        refused = act(c, OWNER, "bill_create", {"vendor_id": vendor, "bill_date": "2026-10-11",
                                                "lines": [{"item_id": item_id, "amount": 900}]})
        assert refused.code == "invalid"


def test_a_line_tag_must_name_an_active_location_or_project(books):
    with books.transaction() as c:
        location = act(c, OWNER, "location_create", {"name": "Main St"})
        assert location.status == "committed", location.reason
        location_id = str(location.result["location_id"])

        def journal(tags: dict[str, str]):
            return act(c, OWNER, "journal", {"date": "2026-10-10", "memo": "Tagged", "lines": [
                {"account_id": account(c, "5100"), "amount": 700, "tags": tags},
                {"account_id": account(c, "1000"), "amount": -700}]})
        assert journal({"location": location_id}).status == "committed"
        assert journal({"location": "missing"}).code == "invalid"
        assert journal({"project": "nope"}).code == "invalid"
        assert act(c, OWNER, "location_update", {"location_id": location_id, "active": False}).status == "committed"
        assert journal({"location": location_id}).code == "invalid"
        assert [str(r[0]) for r in c.execute("SELECT value FROM fin_line_tags WHERE kind='location'")] == [location_id]


def test_a_price_change_asks_a_finance_admin_unless_the_owner_makes_it(books):
    with books.transaction() as c:
        item_id = str(act(c, OWNER, "item_create", {"name": "Desk", "kind": "service", "price": 1_000}).result["item_id"])
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        assert act(c, "human:dan", "item_update", {"item_id": item_id, "price": 1_200}).status == "needs_approval"
        assert c.execute("SELECT price FROM items WHERE id=?", (item_id,)).fetchone()[0] == 1_000
        assert act(c, OWNER, "item_update", {"item_id": item_id, "price": 1_200}).status == "committed"
        assert c.execute("SELECT price FROM items WHERE id=?", (item_id,)).fetchone()[0] == 1_200


def test_a_location_takes_a_listed_kind_and_refuses_an_unlisted_one(books):
    with books.transaction() as c:
        made = RS.insert(c, "locations", {"name": "Dock", "kind": "warehouse", "created_at": AT,
                                          "created_by_id": OWNER})
        assert c.execute("SELECT kind FROM locations WHERE id=?", (made,)).fetchone()[0] == "warehouse"
    with pytest.raises(sqlite3.IntegrityError):
        with books.transaction() as c:
            RS.insert(c, "locations", {"name": "Lot", "kind": "location", "created_at": AT, "created_by_id": OWNER})


def test_an_item_takes_a_raw_material_or_assembly_kind_and_refuses_an_unlisted_one(books):
    with books.transaction() as c:
        for kind in ("raw_material", "assembly"):
            RS.insert(c, "items", {"name": kind, "kind": kind, "created_at": AT, "created_by_id": OWNER})
    with pytest.raises(sqlite3.IntegrityError):
        with books.transaction() as c:
            RS.insert(c, "items", {"name": "Bolt", "kind": "gizmo", "created_at": AT, "created_by_id": OWNER})


def test_an_item_with_an_account_a_teammate_cannot_see_is_not_found_to_them_whatever_they_change(books):
    with books.transaction() as c:
        hidden = account(c, "4000")
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        item = str(act(c, OWNER, "item_create", {"name": "Desk", "kind": "service", "price": 1_000,
                                                 "income_account_id": hidden}).result["item_id"])
        changes = ({"price": 1_000}, {"price": 2_000}, {"name": "Desk"}, {"name": "Lamp"})
        answers = [act(c, "human:dan", "item_update", {"item_id": item, **change}) for change in changes]
        missing = act(c, "human:dan", "item_update", {"item_id": "no-such-item", "price": 2_000})
        assert missing.code == "not_found", missing
        assert {(a.status, a.code, a.reason) for a in answers} == {(missing.status, missing.code, missing.reason)}, \
            answers
        assert tuple(c.execute("SELECT price, income_account_id FROM items WHERE id=?", (item,)).fetchone()) == \
            (1_000, hidden)


def test_an_item_sets_only_accounts_its_setter_can_see(books):
    with books.transaction() as c:
        hidden = account(c, "4000")
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.records"}).status == "committed"
        assert act(c, OWNER, "right_grant", {"teammate": "human:dan", "right": "finance.book"}).status == "committed"
        item = str(act(c, OWNER, "item_create", {"name": "Desk", "kind": "service", "price": 1_000}).result["item_id"])
        refused = act(c, "human:dan", "item_create", {"name": "Lamp", "kind": "service", "income_account_id": hidden})
        missing = act(c, "human:dan", "item_create", {"name": "Lamp", "kind": "service",
                                                      "income_account_id": "no-such-account"})
        changed = act(c, "human:dan", "item_update", {"item_id": item, "income_account_id": hidden})
        # A hidden account is refused the same way a missing one is, so the refusal does not say it exists.
        assert refused.code == missing.code == "not_found" and refused.reason == missing.reason, (refused, missing)
        assert changed.code == "not_found", changed
        assert c.execute("SELECT COUNT(*) FROM items WHERE name='Lamp'").fetchone()[0] == 0
        assert c.execute("SELECT income_account_id FROM items WHERE id=?", (item,)).fetchone()[0] is None
        assert act(c, OWNER, "view_grant", {"subject": "human:dan", "what": "everything",
                                            "whose": "company"}).status == "committed"
        assert act(c, "human:dan", "item_update", {"item_id": item, "income_account_id": hidden}).status == "committed"
        assert c.execute("SELECT income_account_id FROM items WHERE id=?", (item,)).fetchone()[0] == hidden
