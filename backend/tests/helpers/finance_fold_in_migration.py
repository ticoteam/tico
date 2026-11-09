"""Shared by the tests that import it, moved out of test_finance_fold_in_migration.py so they import no test file."""

import importlib.util
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from backend.entities import erase as EE
from backend.entities import store as ES
from backend.entities import visible as EV
from backend.finance.store import ap as SAP
from backend.store import Problem
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, books, finance_template, store  # noqa: F401


OLD_CONTACTS = """CREATE TABLE fin_contacts (
  id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES fin_books(id), name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'vendor' CHECK (kind IN ('vendor','customer','both')), emails TEXT NOT NULL DEFAULT '[]',
  default_account_id TEXT REFERENCES fin_accounts(id), is_1099 INTEGER NOT NULL DEFAULT 0, tax_id_ref TEXT,
  payment_ref TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT, tax_id_last4 TEXT, seats INTEGER, renewal_on TEXT)"""


OLD_ALIASES = """CREATE TABLE fin_vendor_aliases (
  book_id TEXT NOT NULL REFERENCES fin_books(id), name_key TEXT NOT NULL,
  contact_id TEXT NOT NULL REFERENCES fin_contacts(id), created_at TEXT NOT NULL, merged_from TEXT,
  PRIMARY KEY (book_id, name_key))"""


CONTACT_COLUMNS = (
    "id, book_id, name, kind, emails, default_account_id, is_1099, tax_id_ref, payment_ref, active, created_at, "
    "created_by, updated_at, tax_id_last4, seats, renewal_on"
)


def old_contact_tables(conn):
    """fin_contacts and fin_vendor_aliases as tables again (the shape before the fold-in), holding the rows the views
    showed; the entities stay. Returns the contact ids."""
    contacts = conn.execute(f"SELECT {CONTACT_COLUMNS} FROM fin_contacts").fetchall()
    aliases = conn.execute(
        "SELECT book_id, name_key, contact_id, created_at, merged_from FROM fin_vendor_aliases"
    ).fetchall()
    conn.execute("DROP VIEW fin_contacts")
    conn.execute("DROP VIEW fin_vendor_aliases")
    conn.execute("DROP TABLE fin_book_entities")
    conn.execute("DROP INDEX entity_identifiers_finance_alias")
    conn.execute(OLD_CONTACTS)
    conn.execute(OLD_ALIASES)
    conn.execute("CREATE INDEX fin_contacts_name ON fin_contacts(book_id, name)")
    conn.execute("CREATE INDEX fin_vendor_aliases_contact ON fin_vendor_aliases(contact_id)")
    conn.executemany(f"INSERT INTO fin_contacts ({CONTACT_COLUMNS}) VALUES ({', '.join('?' * 16)})", contacts)
    conn.executemany("INSERT INTO fin_vendor_aliases VALUES (?, ?, ?, ?, ?)", aliases)
    return [row[0] for row in contacts]
