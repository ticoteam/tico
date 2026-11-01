"""Reads of the locations, items and projects tables on the caller's connection. Rows only: who may see them is the
caller's question."""
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .store import ROW_TABLES, resolve

SEARCH_COLUMNS = {"locations": ("name",), "items": ("name", "sku"), "projects": ("name",),
                  "equipment": ("name", "serial", "make", "model")}
# Columns a search narrows by, in SQL so a page is filtered whole: `equal` keeps the rows holding the value, `by` the rows
# whose day is on or before it.
FILTER_COLUMNS = {"locations": {"kind", "customer_id"}, "items": {"kind"}, "projects": {"status", "customer_id"},
                  "equipment": {"kind", "customer_id", "location_id", "warranty_ends_on"}}


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
           after: tuple[str, str] | None = None, most: int = 200, equal: Mapping[str, str] | None = None,
           by: Mapping[str, str] | None = None, between: Mapping[str, tuple[str, str]] | None = None,
           ids: Sequence[str] | None = None) -> list[Any]:
    """Rows whose name (or sku, for items) contains the text, by name then id, past the cursor `after`. `active` None
    lists archived rows too; an empty text lists every row. `equal`, `by` (on or before) and `between` (from, to) narrow
    by the FILTER_COLUMNS of a table; `ids` keeps only those ids, so a list paged in SQL counts right. Empty `ids`
    matches nothing."""
    assert table in ROW_TABLES
    columns = SEARCH_COLUMNS[table]
    assert set(equal or {}) | set(by or {}) | set(between or {}) <= FILTER_COLUMNS.get(table, set())
    if ids is not None and not ids:
        return []
    needle = text.strip().lower()
    clause = " OR ".join(f"instr(lower({column}), ?) > 0" for column in columns)
    where = f"({clause})"
    args: list[object] = [*([needle] * len(columns))]
    if active is not None:
        where += " AND archived_at IS NULL" if active else " AND archived_at IS NOT NULL"
    for column, value in (equal or {}).items():
        where += f" AND {column}=?"
        args.append(value)
    for column, value in (by or {}).items():
        where += f" AND {column} IS NOT NULL AND {column}<=?"
        args.append(value)
    for column, (low, high) in (between or {}).items():
        where += f" AND {column} IS NOT NULL AND {column}>=? AND {column}<=?"
        args += [low, high]
    if ids is not None:
        where += f" AND id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    if after is not None:
        where += " AND (name, id) > (?, ?)"
        args += list(after)
    args.append(most)
    return c.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY name, id LIMIT ?", args).fetchall()


def equipment_for_customer(c: sqlite3.Connection, contact_id: str) -> list[Any]:
    """The active equipment a customer has, by name: the customer panel's list. Capped at the search's page."""
    return search(c, "equipment", equal={"customer_id": contact_id}, most=200)


def active_rows(c: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> list[tuple[Any, ...]]:
    """(id, *columns) of the rows not archived, for name and duplicate checks. The columns are this code's own names."""
    assert table in ROW_TABLES
    return c.execute(f"SELECT id, {', '.join(columns)} FROM {table} WHERE archived_at IS NULL").fetchall()


def active_equipment(c: sqlite3.Connection) -> list[tuple[Any, ...]]:
    """(id, name, serial) of the equipment not archived, for name and serial checks."""
    return c.execute("SELECT id, name, serial FROM equipment WHERE archived_at IS NULL").fetchall()
