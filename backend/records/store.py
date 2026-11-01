"""Writes to the locations, items and projects tables: plain SQL on the caller's connection. Finance's planners check
rights and payloads first and run these inside the writer gate, so a record changes in the same commit as its action.
A record is archived by setting archived_at, never deleted: lines keep naming it."""
import sqlite3
import uuid
from collections.abc import Mapping

ROW_TABLES = ("locations", "items", "projects")
TABLES = """
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('store','office','warehouse','plant','customer_site','vehicle',
                                                       'other')),
  parent_id TEXT REFERENCES locations(id), address TEXT NOT NULL DEFAULT '{}', archived_at TEXT,
  created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, updated_at TEXT, updated_by_id TEXT);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, sku TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('service','inventory','non_inventory','bundle','category',
    'raw_material','assembly')),
  unit TEXT NOT NULL DEFAULT '', price BIGINT, currency TEXT NOT NULL DEFAULT '',
  income_account_id TEXT REFERENCES fin_accounts(id), expense_account_id TEXT REFERENCES fin_accounts(id),
  asset_account_id TEXT REFERENCES fin_accounts(id), archived_at TEXT,
  created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, updated_at TEXT, updated_by_id TEXT);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, customer_id TEXT REFERENCES fin_contacts(id),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')), archived_at TEXT,
  created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, updated_at TEXT, updated_by_id TEXT);

CREATE TABLE IF NOT EXISTS record_aliases (
  table_name TEXT NOT NULL CHECK (table_name IN ('locations','items','projects')), alias_id TEXT NOT NULL,
  record_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (table_name, alias_id));

CREATE TABLE IF NOT EXISTS due_dates (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('location','equipment','contact')), subject_id TEXT NOT NULL,
  repeats TEXT NOT NULL CHECK (repeats IN ('once','weekly','monthly','quarterly','yearly','every_days','every_months')),
  every_count INTEGER CHECK (every_count IS NULL OR every_count BETWEEN 1 AND 3660),
  starts_on TEXT NOT NULL, next_due_on TEXT,
  lead_days INTEGER NOT NULL DEFAULT 0 CHECK (lead_days BETWEEN 0 AND 366), assignee_id TEXT NOT NULL,
  archived_at TEXT, created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, updated_at TEXT, updated_by_id TEXT,
  CHECK ((repeats IN ('every_days','every_months')) = (every_count IS NOT NULL)));

CREATE INDEX IF NOT EXISTS due_dates_subject ON due_dates(subject_type, subject_id);

CREATE TABLE IF NOT EXISTS due_date_occurrences (
  id TEXT PRIMARY KEY, due_date_id TEXT NOT NULL REFERENCES due_dates(id), due_on TEXT NOT NULL,
  completed_at TEXT, completed_by_id TEXT, task_id TEXT, created_at TEXT NOT NULL,
  UNIQUE (due_date_id, due_on), CHECK ((completed_at IS NULL) = (completed_by_id IS NULL)));

CREATE INDEX IF NOT EXISTS due_date_occurrences_task ON due_date_occurrences(task_id) WHERE task_id IS NOT NULL;
"""


def create(c: sqlite3.Connection) -> None:
    """The locations, items and projects tables. Idempotent; runs on the caller's transaction."""
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


def resolve(c: sqlite3.Connection, table: str, row_id: str) -> str:
    """The row an id names: the id itself, or the row it is an alias of (see alias)."""
    row = c.execute("SELECT record_id FROM record_aliases WHERE table_name=? AND alias_id=?",
                    (table, row_id)).fetchone()
    return str(row[0]) if row is not None else row_id


def alias(c: sqlite3.Connection, table: str, alias_id: str, record_id: str, at: str) -> None:
    """Another id for a row that exists. A record the books took under two ids keeps the one row, and the id the other
    was made with resolves to it on reads and writes; nothing that names the alias is rewritten."""
    assert table in ROW_TABLES
    c.execute("INSERT OR IGNORE INTO record_aliases(table_name, alias_id, record_id, created_at) VALUES(?,?,?,?)",
              (table, alias_id, record_id, at))


def update(c: sqlite3.Connection, table: str, row_id: str, fields: Mapping[str, object]) -> None:
    """Set the given fields on one row."""
    assert table in ROW_TABLES
    if not fields:
        return
    row_id = resolve(c, table, row_id)
    cols = sorted(fields)
    c.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in cols)} WHERE id=?",
              [*(fields[k] for k in cols), row_id])


def delete(c: sqlite3.Connection, table: str, row_id: str) -> None:
    """Remove one row. A row that other rows, ledger lines or queued writes name is archived instead, by callers."""
    assert table in ROW_TABLES
    c.execute(f"DELETE FROM {table} WHERE id=?", (resolve(c, table, row_id),))
