"""Writes to the rec_* tables: plain SQL on the caller's connection. Finance's planners check rights and payloads
first and run these inside the writer gate, so a record changes in the same commit as its action."""
import sqlite3
import uuid
from collections.abc import Mapping

ROW_TABLES = ("rec_places", "rec_items", "rec_projects")
TABLES = """
CREATE TABLE IF NOT EXISTS rec_places (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'location',
  parent_id TEXT REFERENCES rec_places(id), address_json TEXT NOT NULL DEFAULT '{}',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT, updated_by TEXT);

CREATE TABLE IF NOT EXISTS rec_items (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, sku TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT '', price BIGINT, currency TEXT NOT NULL DEFAULT '',
  income_account_id TEXT REFERENCES fin_accounts(id), expense_account_id TEXT REFERENCES fin_accounts(id),
  asset_account_id TEXT REFERENCES fin_accounts(id), active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, updated_by TEXT);

CREATE TABLE IF NOT EXISTS rec_projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, customer_id TEXT REFERENCES fin_contacts(id),
  status TEXT NOT NULL DEFAULT 'open', active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
"""


def create(c: sqlite3.Connection) -> None:
    """The rec_* tables. Idempotent; runs on the caller's transaction."""
    for statement in TABLES.split(";"):
        if statement.strip():
            c.execute(statement)


def insert(c: sqlite3.Connection, table: str, row: Mapping[str, object]) -> str:
    """Insert one row with a new id and return the id."""
    assert table in ROW_TABLES
    values = {"id": str(uuid.uuid4()), **row}
    cols = sorted(values)
    c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
              [values[k] for k in cols])
    return str(values["id"])


def update(c: sqlite3.Connection, table: str, row_id: str, fields: Mapping[str, object]) -> None:
    """Set the given fields on one row."""
    assert table in ROW_TABLES
    if not fields:
        return
    cols = sorted(fields)
    c.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in cols)} WHERE id=?",
              [*(fields[k] for k in cols), row_id])
