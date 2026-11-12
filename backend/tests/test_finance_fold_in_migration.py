"""The finance_fold_in migration (Directory track 9, 20261108203140): fin_contacts rows become organization entities,
the ledger's keys point at entities, and nothing is lost.

The hub boots in the new shape (boot already ran this migration on empty books), then Finance makes a vendor, a
customer, a bill and an invoice through its actions. The pre-migration state is built from that: the contact views
become the old tables again with those rows, a second book adds a contact that shares the vendor's billing email, the
ledger tables point at fin_contacts again, and the entities Finance made are removed. The migration then runs as the
runner runs it (foreign keys off, the broken-reference check before and after)."""

import importlib.util
import json
import sqlite3
from collections import Counter
from pathlib import Path

from backend.entities import erase as EE
from backend.entities import store as ES
from backend.entities import visible as EV
from backend.finance.store import ap as SAP
from backend.store import Problem
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401
from backend.tests.helpers.finance_fold_in_migration import (
    CONTACT_COLUMNS,
    OLD_ALIASES,
    OLD_CONTACTS,
    old_contact_tables,
)  # noqa: F401

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "20261108203140_finance_fold_in.py"
LEDGER = ("fin_bills", "fin_invoices", "fin_estimates", "fin_purchase_orders", "projects")


def _load():
    spec = importlib.util.spec_from_file_location("finance_fold_in_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, module):
    """As backend/migrations runs a FOREIGN_KEYS_OFF migration: keys off, and no new broken reference."""
    assert module.FOREIGN_KEYS_OFF
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN")
    try:
        before = Counter((r[0], r[2]) for r in conn.execute("PRAGMA foreign_key_check"))
        module.up(conn)
        after = Counter((r[0], r[2]) for r in conn.execute("PRAGMA foreign_key_check"))
        broken = {k: n for k, n in after.items() if n > before.get(k, 0)}
        assert broken == {}, broken
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    conn.execute("PRAGMA foreign_keys=ON")


def _dump(conn):
    """Every row of the tables the migration touches, for a replay to compare."""
    tables = (
        "entities",
        "entity_identifiers",
        "entity_sources",
        "entity_links",
        "entity_values",
        "entity_merges",
        "fin_book_entities",
        "fin_recipients",
        "fin_external_refs",
        *LEDGER,
    )
    skip = {"updated_at", "created_at", "seq"}
    out = {}
    for table in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] not in skip]
        out[table] = sorted(repr(tuple(r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM {table}"))
    out["schema"] = sorted(
        repr(tuple(r)) for r in conn.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    return out


def _seed(store):
    """Through Finance's actions: a vendor with a billing email and an alias, a customer with no email, a vendor whose
    email the Directory already holds, a bill and an invoice. Returns their ids."""
    with store.transaction() as c:
        acme = act(
            c,
            OWNER,
            "contact_create",
            {"name": "Acme Supplies", "kind": "vendor", "emails": ["billing@acme.example.test"]},
        ).result["contact_id"]
        jane = act(c, OWNER, "contact_create", {"name": "Jane Doe Design", "kind": "customer"}).result["contact_id"]
        bill = act(
            c,
            OWNER,
            "bill_create",
            {
                "vendor_id": acme,
                "bill_date": "2026-10-10",
                "lines": [{"account_id": account(c, "6900"), "amount": 20_000}],
            },
        ).result["bill_id"]
        invoice = act(
            c,
            OWNER,
            "invoice_create",
            {
                "customer_id": jane,
                "invoice_date": "2026-10-10",
                "lines": [{"account_id": account(c, "4000"), "amount": 30_000}],
            },
        ).result["invoice_id"]
        book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
        SAP.alias_add(c, book, "acme supply co", acme, AT)
    return {"acme": acme, "jane": jane, "bill": bill, "invoice": invoice, "book": book}


def _before(conn, ids):
    """The state before the migration, from what Finance just wrote."""
    conn.execute("PRAGMA foreign_keys=OFF")
    made = old_contact_tables(conn)
    for table in ("entity_sources", "entity_values"):
        conn.execute(f"DELETE FROM {table} WHERE entity_id IN (SELECT value FROM json_each(?))", (json.dumps(made),))
    conn.execute(
        "DELETE FROM entity_identifiers WHERE entity_id IN (SELECT value FROM json_each(?))", (json.dumps(made),)
    )
    conn.execute(
        "DELETE FROM entity_links WHERE from_entity_id IN (SELECT value FROM json_each(?))", (json.dumps(made),)
    )
    conn.execute("DELETE FROM entities WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(made),))
    for table in LEDGER:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
        assert "REFERENCES entities(id)" in sql, table
        keep = [
            r[0]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND type = 'index' AND sql IS NOT NULL", (table,)
            )
        ]
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        conn.execute(sql.replace("REFERENCES entities(id)", "REFERENCES fin_contacts(id)"))
        conn.execute(f"INSERT INTO {table} SELECT * FROM {table}_old")
        conn.execute(f"DROP TABLE {table}_old")
        for statement in keep:
            conn.execute(statement)
    conn.execute("ALTER TABLE fin_recipients DROP COLUMN entity_id")
    # A second book, whose vendor shares Acme's billing email (spaced and cased differently) under another name, a
    # merged alias, and a bill of its own.
    conn.execute("CREATE TEMP TABLE book2 AS SELECT * FROM fin_books WHERE id = ?", (ids["book"],))
    conn.execute("UPDATE book2 SET id = 'book-2', name = 'Acme Two'")
    conn.execute("INSERT INTO fin_books SELECT * FROM book2")
    conn.execute(
        f"INSERT INTO fin_contacts ({CONTACT_COLUMNS}) VALUES ('k-acme2', 'book-2', 'ACME Wholesale', 'vendor', ?, NULL, 1, "
        "NULL, NULL, 1, '2026-10-16T00:00:00Z', 'human:ana', NULL, '1234', 3, '2027-01-01')",
        (json.dumps([" Billing@Acme.Example.Test "]),),
    )
    conn.execute(
        "INSERT INTO fin_vendor_aliases VALUES ('book-2', 'acme wholesale', 'k-acme2', '2026-10-12T00:00:00Z', NULL)"
    )
    conn.execute(
        "INSERT INTO fin_vendor_aliases VALUES ('book-2', 'acme old', 'k-acme2', '2026-10-12T00:00:00Z', 'k-x')"
    )
    conn.execute("CREATE TEMP TABLE bill2 AS SELECT * FROM fin_bills WHERE id = ?", (ids["bill"],))
    conn.execute("UPDATE bill2 SET id = 'bill-2', book_id = 'book-2', vendor_id = 'k-acme2'")
    conn.execute("INSERT INTO fin_bills SELECT * FROM bill2")
    # Odd rows: a contact with no name or email, a bill whose vendor names no contact, an alias of no contact.
    conn.execute(
        f"INSERT INTO fin_contacts ({CONTACT_COLUMNS}) VALUES ('k-blank', ?, '', 'vendor', 'not json', NULL, 0, NULL, "
        "NULL, 1, '2026-10-13T00:00:00Z', 'human:ana', NULL, NULL, NULL, NULL)",
        (ids["book"],),
    )
    conn.execute("CREATE TEMP TABLE bill3 AS SELECT * FROM fin_bills WHERE id = ?", (ids["bill"],))
    conn.execute("UPDATE bill3 SET id = 'bill-3', vendor_id = 'k-gone', number = 'B-gone'")
    conn.execute("INSERT INTO fin_bills SELECT * FROM bill3")
    conn.execute(
        "INSERT INTO fin_vendor_aliases VALUES (?, 'ghost', 'k-ghost', '2026-10-13T00:00:00Z', NULL)", (ids["book"],)
    )
    # Vendors the Directory already knows: one with no sources (it shows to everyone, so it is not reused) and one a
    # sync brought (a source), which is.
    _directory_org(conn, "organization/widgets", "Widgets", "ap@widgets.example.test", sourced=False)
    _directory_org(conn, "organization/gadgets", "Gadgets", "ap@gadgets.example.test", sourced=True)
    for cid, name, email in (
        ("k-widgets", "Widgets LLC", "ap@widgets.example.test"),
        ("k-gadgets", "Gadgets Inc", "ap@gadgets.example.test"),
    ):
        conn.execute(
            f"INSERT INTO fin_contacts ({CONTACT_COLUMNS}) VALUES (?, ?, ?, 'vendor', ?, NULL, 0, "
            "NULL, NULL, 1, '2026-10-14T00:00:00Z', 'human:ana', NULL, NULL, NULL, NULL)",
            (cid, ids["book"], name, json.dumps([email])),
        )
    conn.execute("DELETE FROM schema_migrations WHERE version = '20261108203140'")
    conn.execute("PRAGMA foreign_keys=ON")


def _directory_org(conn, entity_id, name, email, *, sourced):
    conn.execute(
        "INSERT INTO entities (id, type, name, name_key, created_by, created_at, updated_at) "
        "VALUES (?, 'organization', ?, lower(?), 'system', ?, ?)",
        (entity_id, name, name, AT, AT),
    )
    conn.execute(
        "INSERT INTO entity_identifiers (entity_id, kind, value, normalized_value) VALUES (?, 'email', ?, ?)",
        (entity_id, email, email),
    )
    if sourced:
        conn.execute(
            "INSERT INTO entity_sources (id, entity_id, source_type, source_id, visibility, created_at) "
            "VALUES (?, ?, 'sync', ?, 'company', ?)",
            (f"src_sync_{entity_id}", entity_id, entity_id, AT),
        )


def test_contacts_fold_into_entities_and_every_reference_resolves(books):  # noqa: F811
    ids = _seed(books)
    conn = sqlite3.connect(books.settings.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _before(conn, ids)
    module = _load()
    old_refs = {t: conn.execute(f"SELECT id, * FROM {t}").fetchall() for t in ("fin_bills", "fin_invoices")}
    old_ids = [r[0] for r in conn.execute("SELECT id FROM fin_contacts")]
    old_lines = sorted(map(tuple, conn.execute("SELECT id, contact_id FROM fin_lines")))
    _run(conn, module)

    kinds = dict(
        conn.execute("SELECT name, type FROM sqlite_master WHERE name IN ('fin_contacts', 'fin_vendor_aliases')")
    )
    assert kinds == {"fin_contacts": "view", "fin_vendor_aliases": "view"}
    # Every old contact id is still a contact, and an entity.
    assert sorted(r[0] for r in conn.execute("SELECT id FROM fin_contacts")) == sorted(old_ids)
    for cid in old_ids:
        assert conn.execute("SELECT 1 FROM entities WHERE id = ?", (cid,)).fetchone(), cid
    # The two books' Acme are one entity: the oldest contact's, the second a merged redirect to it.
    acme2 = conn.execute("SELECT * FROM entities WHERE id = 'k-acme2'").fetchone()
    assert (acme2["status"], acme2["merged_into_id"]) == ("merged", ids["acme"])
    assert conn.execute("SELECT entity_id FROM fin_book_entities WHERE id = 'k-acme2'").fetchone()[0] == ids["acme"]
    assert conn.execute("SELECT 1 FROM entity_merges WHERE id = 'mrg_finance_k-acme2'").fetchone()
    book2 = conn.execute("SELECT * FROM fin_contacts WHERE id = 'k-acme2'").fetchone()
    assert (book2["book_id"], book2["is_1099"], book2["tax_id_last4"], book2["seats"], book2["renewal_on"]) == (
        "book-2",
        1,
        "1234",
        3,
        "2027-01-01",
    )
    assert book2["name"] == "ACME Wholesale"  # each book keeps the name it uses
    assert conn.execute("SELECT name FROM entities WHERE id = ?", (ids["acme"],)).fetchone()[0] == "Acme Supplies"
    assert SAP.get(conn, "fin_contacts", ids["acme"])["name"] == "Acme Supplies"
    assert SAP.contact_named(conn, "book-2", "ACME Wholesale")["id"] == "k-acme2"
    assert SAP.alias_of(conn, "book-2", "acme wholesale") == "k-acme2"
    assert SAP.alias_row(conn, "book-2", "acme old")["merged_from"] == "k-x"
    assert SAP.alias_of(conn, ids["book"], "acme supply co") == ids["acme"]
    assert SAP.alias_of(conn, ids["book"], "ghost") == "k-ghost"  # an alias of no contact stays
    sources = {r[0] for r in conn.execute("SELECT source_id FROM entity_sources WHERE entity_id = ?", (ids["acme"],))}
    assert sources == {ids["acme"], "k-acme2"}
    emails = {
        r[0]
        for r in conn.execute(
            "SELECT normalized_value FROM entity_identifiers WHERE entity_id = ? AND kind = 'email'", (ids["acme"],)
        )
    }
    assert emails == {"billing@acme.example.test"}
    # A Directory entity with no sources is not reused (it shows to everyone): the vendor is its own entity.
    assert conn.execute("SELECT entity_id FROM fin_book_entities WHERE id = 'k-widgets'").fetchone()[0] == "k-widgets"
    assert conn.execute("SELECT 1 FROM entity_sources WHERE entity_id = 'organization/widgets'").fetchone() is None
    # One a sync brought is reused: the contact redirects to it, the entity keeps its name, the book keeps its own.
    gadgets = conn.execute("SELECT status, merged_into_id FROM entities WHERE id = 'k-gadgets'").fetchone()
    assert tuple(gadgets) == ("merged", "organization/gadgets")
    assert conn.execute("SELECT name FROM entities WHERE id = 'organization/gadgets'").fetchone()[0] == "Gadgets"
    assert SAP.get(conn, "fin_contacts", "k-gadgets")["name"] == "Gadgets Inc"
    assert conn.execute(
        "SELECT 1 FROM entity_sources WHERE entity_id = 'organization/gadgets' AND source_id = 'k-gadgets'"
    ).fetchone()
    # Odd rows are kept: a blank contact is an entity, a dangling vendor gets a placeholder.
    assert conn.execute("SELECT name FROM entities WHERE id = 'k-blank'").fetchone()[0] == ""
    assert conn.execute("SELECT name FROM entities WHERE id = 'k-gone'").fetchone()[0] == "Unknown contact"
    # Every ledger row keeps its counterparty, and its key now points at entities.
    for table, rows in old_refs.items():
        assert sorted(map(tuple, conn.execute(f"SELECT * FROM {table}"))) == sorted(tuple(r)[1:] for r in rows), table
    assert conn.execute("SELECT vendor_id FROM fin_bills WHERE id = 'bill-2'").fetchone()[0] == "k-acme2"
    for table in LEDGER:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
        assert "fin_contacts" not in sql and "REFERENCES entities(id)" in sql, table
    assert conn.execute("PRAGMA foreign_key_check(fin_bills)").fetchall() == []
    assert sorted(map(tuple, conn.execute("SELECT id, contact_id FROM fin_lines"))) == old_lines
    # Book visibility: the owner sees the book-only vendor, a teammate with no Finance view of it does not.
    assert EV.visible(conn, OWNER, ids["acme"])
    assert not EV.visible(conn, "human:gus", ids["acme"])
    assert EV.visible(conn, "human:gus", "organization/widgets")  # it showed to everyone, and still does
    assert EV.visible(conn, "human:gus", "organization/gadgets")

    # A replay changes nothing.
    first = _dump(conn)
    conn.execute("DELETE FROM schema_migrations WHERE version = '20261108203140'")
    _run(conn, module)
    assert _dump(conn) == first
    conn.close()

    with books.transaction() as c:
        # Self came after the fold: the contacts' vendor_to / customer_of links come with it.
        assert c.execute("SELECT COUNT(*) FROM entity_links WHERE source_type = 'finance_book'").fetchone()[0] == 0
        me = ES.ensure_self(c, company_name="Acme Lights")
        links = {
            tuple(r)
            for r in c.execute(
                "SELECT source_id, from_entity_id, link_key, to_entity_id FROM entity_links "
                "WHERE source_type = 'finance_book'"
            )
        }
        assert (ids["acme"], ids["acme"], "vendor_to", me) in links
        assert ("k-acme2", ids["acme"], "vendor_to", me) in links
        assert (ids["jane"], ids["jane"], "customer_of", me) in links
        assert ("k-gadgets", "organization/gadgets", "vendor_to", me) in links
        # Finance reads and writes the folded contacts as before. A rename in one book renames only that book's
        # contact, and the entity only when it is the contact's own.
        assert SAP.get(c, "fin_contacts", ids["acme"])["name"] == "Acme Supplies"
        assert SAP.update(c, "fin_contacts", "k-acme2", {"name": "Acme West"}) == 1
        assert SAP.get(c, "fin_contacts", "k-acme2")["name"] == "Acme West"
        assert SAP.get(c, "fin_contacts", ids["acme"])["name"] == "Acme Supplies"
        assert c.execute("SELECT name FROM entities WHERE id = ?", (ids["acme"],)).fetchone()[0] == "Acme Supplies"
        assert SAP.update(c, "fin_contacts", ids["acme"], {"name": "Acme Supplies Co"}) == 1
        assert c.execute("SELECT name FROM entities WHERE id = ?", (ids["acme"],)).fetchone()[0] == "Acme Supplies Co"
        assert SAP.get(c, "fin_contacts", "k-acme2")["name"] == "Acme West"
        assert SAP.update(c, "fin_contacts", "k-gadgets", {"name": "Gadgets Co"}) == 1
        assert c.execute("SELECT name FROM entities WHERE id = 'organization/gadgets'").fetchone()[0] == "Gadgets"
        assert "k-acme2" in SAP.vendor_keys(c, "book-2", "k-acme2")
        # The Directory never erases a ledger's counterparty: it refuses, and nothing goes.
        try:
            EE.erase(c, OWNER, ids["acme"], "request")
        except Problem as refused:
            assert (refused.status, refused.code) == (409, "kept_for_books")
            assert refused.detail == (
                "Kept for the books: Acme Supplies Co has Finance records (contacts, bills). Erase them in Finance first."
            )
        else:
            raise AssertionError("erase of a contact with bills went through")
        assert SAP.get(c, "fin_contacts", ids["acme"]) is not None
        # A Directory merge moves the entity's identifiers, but what a book row put there stays on the contact it
        # names: the book's vendor names and its billing emails (which the book keeps current, so a moved copy would
        # come back as a duplicate).
        ES.merge(c, OWNER, ids["jane"], ids["acme"])
        assert SAP.alias_of(c, ids["book"], "acme supply co") == ids["acme"]
        assert SAP.alias_of(c, "book-2", "acme wholesale") == "k-acme2"
        assert SAP.update(c, "fin_contacts", ids["acme"], {"emails": json.dumps(["billing@acme.example.test"])}) == 1
        held = sorted(
            tuple(r)
            for r in c.execute(
                "SELECT entity_id, normalized_value FROM entity_identifiers WHERE kind = 'email' "
                "AND normalized_value = 'billing@acme.example.test'"
            )
        )
        assert held == [(ids["acme"], "billing@acme.example.test")] * 2  # one per book row (acme, k-acme2), no copy
        ES.unmerge(c, OWNER, ids["acme"])
        assert SAP.alias_of(c, ids["book"], "acme supply co") == ids["acme"]


def test_a_new_contact_is_an_entity_and_reuses_one_the_directory_knows(books):  # noqa: F811
    with books.transaction() as c:
        _directory_org(c, "organization/widgets", "Widgets", "ap@widgets.example.test", sourced=True)
        _directory_org(c, "organization/gizmos", "Gizmos", "ap@gizmos.example.test", sourced=False)
        fresh = act(
            c, OWNER, "contact_create", {"name": "Acme Parts", "kind": "vendor", "emails": ["ap@acme.example.test"]}
        ).result["contact_id"]
        known = act(
            c, OWNER, "contact_create", {"name": "Widgets LLC", "kind": "vendor", "emails": ["ap@widgets.example.test"]}
        ).result["contact_id"]
        book = str(c.execute("SELECT id FROM fin_books").fetchone()[0])
        # A new vendor: its own entity, restricted to the book.
        assert tuple(c.execute("SELECT type, status FROM entities WHERE id = ?", (fresh,)).fetchone()) == (
            "organization",
            "active",
        )
        assert EV.visible(c, OWNER, fresh) and not EV.visible(c, "human:gus", fresh)
        # A vendor the Directory knew from a source: the contact id redirects to it, it keeps its name, the book its own.
        assert c.execute("SELECT merged_into_id FROM entities WHERE id = ?", (known,)).fetchone()[0] == (
            "organization/widgets"
        )
        assert EV.visible(c, "human:gus", "organization/widgets")
        assert SAP.get(c, "fin_contacts", known)["name"] == "Widgets LLC"
        assert SAP.contact_named(c, book, "Widgets LLC")["id"] == known
        assert c.execute("SELECT name FROM entities WHERE id = 'organization/widgets'").fetchone()[0] == "Widgets"
        assert SAP.update(c, "fin_contacts", known, {"active": 0, "updated_at": AT}) == 1
        assert SAP.get(c, "fin_contacts", known)["active"] == 0
        # One with no sources shows to everyone; a new contact never narrows it, so it gets its own entity.
        gizmo = act(
            c, OWNER, "contact_create", {"name": "Gizmos Ltd", "kind": "vendor", "emails": ["ap@gizmos.example.test"]}
        ).result["contact_id"]
        assert c.execute("SELECT merged_into_id FROM entities WHERE id = ?", (gizmo,)).fetchone()[0] is None
        assert c.execute("SELECT 1 FROM entity_sources WHERE entity_id = 'organization/gizmos'").fetchone() is None
        # No self yet, so no links; Market's company/self arrives unflagged, and the first read that flags it links
        # every contact to it.
        assert c.execute("SELECT COUNT(*) FROM entity_links WHERE source_type = 'finance_book'").fetchone()[0] == 0
        c.execute(
            "INSERT INTO entities (id, type, name, name_key, created_by, created_at, updated_at) "
            "VALUES ('company/self', 'organization', 'Acme Lights', 'acme lights', 'system', ?, ?)",
            (AT, AT),
        )
        assert ES.self_id(c) == "company/self"
        links = {
            tuple(r)
            for r in c.execute(
                "SELECT id, from_entity_id, link_key, to_entity_id FROM entity_links WHERE source_type = 'finance_book'"
            )
        }
        assert links == {
            (f"lnk_finance_{fresh}_vendor_to", fresh, "vendor_to", "company/self"),
            (f"lnk_finance_{known}_vendor_to", "organization/widgets", "vendor_to", "company/self"),
            (f"lnk_finance_{gizmo}_vendor_to", gizmo, "vendor_to", "company/self"),
        }
        assert c.execute("SELECT self_link_keys FROM entities WHERE id = ?", (fresh,)).fetchone()[0] == "vendor_to"
        ES.self_id(c)  # flagged now: a second read adds nothing
        assert c.execute("SELECT COUNT(*) FROM entity_links WHERE source_type = 'finance_book'").fetchone()[0] == 3
