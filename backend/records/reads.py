"""Reads of the locations, items and projects tables on the caller's connection. Rows only: who may see them is the
caller's question."""
import sqlite3
from typing import Any

from .store import ROW_TABLES, resolve

SEARCH_COLUMNS = {"locations": ("name",), "items": ("name", "sku"), "projects": ("name",)}


def get(c: sqlite3.Connection, table: str, row_id: str) -> Any:
    assert table in ROW_TABLES
    return c.execute(f"SELECT * FROM {table} WHERE id=?", (resolve(c, table, row_id),)).fetchone()


def is_active(c: sqlite3.Connection, table: str, row_id: str) -> bool:
    """A row that exists and is not archived."""
    assert table in ROW_TABLES
    row = c.execute(f"SELECT archived_at FROM {table} WHERE id=?", (resolve(c, table, row_id),)).fetchone()
    return row is not None and row[0] is None


def at_customer(c: sqlite3.Connection, customer_id: str) -> list[Any]:
    """Active locations whose customer contact is this one, by name then id."""
    return c.execute("SELECT * FROM locations WHERE customer_id=? AND archived_at IS NULL ORDER BY name, id",
                     (customer_id,)).fetchall()


def names(c: sqlite3.Connection, table: str) -> dict[str, str]:
    """Every row's name by id, active or not: a line keeps naming the record it was tagged with."""
    assert table in ROW_TABLES
    return {str(r[0]): str(r[1]) for r in c.execute(f"SELECT id, name FROM {table}")}


def ids_of(c: sqlite3.Connection, table: str, row_id: str) -> tuple[str, ...]:
    """Every id that names this row: its own and the ids merged into it. A line keeps the id it was tagged with."""
    assert table in ROW_TABLES
    row = resolve(c, table, row_id)
    aliases = c.execute("SELECT alias_id FROM record_aliases WHERE table_name=? AND record_id=?", (table, row))
    return tuple(sorted({row_id, row, *(str(r[0]) for r in aliases)}))


def search(c: sqlite3.Connection, table: str, text: str = "", *, active: bool | None = True,
           after: tuple[str, str] | None = None, most: int = 200) -> list[Any]:
    """Rows whose name (or sku, for items) contains the text, by name then id, past the cursor `after`. `active` None
    lists archived rows too; an empty text lists every row."""
    assert table in ROW_TABLES
    columns = SEARCH_COLUMNS[table]
    needle = text.strip().lower()
    clause = " OR ".join(f"instr(lower({column}), ?) > 0" for column in columns)
    where = f"({clause})"
    args: list[object] = [*([needle] * len(columns))]
    if active is not None:
        where += " AND archived_at IS NULL" if active else " AND archived_at IS NOT NULL"
    if after is not None:
        where += " AND (name, id) > (?, ?)"
        args += list(after)
    args.append(most)
    return c.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY name, id LIMIT ?", args).fetchall()
