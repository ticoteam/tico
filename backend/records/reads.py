"""Reads of the rec_* tables on the caller's connection. Rows only: who may see them is the caller's question."""
import sqlite3
from typing import Any

from .store import ROW_TABLES

SEARCH_COLUMNS = {"rec_places": ("name",), "rec_items": ("name", "sku"), "rec_projects": ("name",)}


def get(c: sqlite3.Connection, table: str, row_id: str) -> Any:
    assert table in ROW_TABLES
    return c.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()


def is_active(c: sqlite3.Connection, table: str, row_id: str) -> bool:
    assert table in ROW_TABLES
    row = c.execute(f"SELECT active FROM {table} WHERE id=?", (row_id,)).fetchone()
    return row is not None and bool(row[0])


def search(c: sqlite3.Connection, table: str, text: str = "", *, active_only: bool = True,
           most: int = 200) -> list[Any]:
    """Rows whose name (or sku, for items) contains the text, by name. An empty text lists every row."""
    assert table in ROW_TABLES
    columns = SEARCH_COLUMNS[table]
    needle = text.strip().lower()
    clause = " OR ".join(f"instr(lower({column}), ?) > 0" for column in columns)
    sql = f"SELECT * FROM {table} WHERE ({clause})" + (" AND active=1" if active_only else "") + \
        " ORDER BY name LIMIT ?"
    return c.execute(sql, [*([needle] * len(columns)), most]).fetchall()
