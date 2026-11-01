"""SQL for purchase orders and estimates (spec §4.4, §4.5): the orders and estimates, their lines, the sums a bill's
lines bill against an order line, and the one-time link from an estimate to its invoice. Callers check rights, views
and states; this module writes rows and reads sums."""
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .ledger import new_id

TABLES = frozenset({"fin_purchase_orders", "fin_po_lines", "fin_estimates", "fin_estimate_lines"})
LINES = {"po": ("fin_po_lines", "po_id"), "estimate": ("fin_estimate_lines", "estimate_id")}


def insert(c: sqlite3.Connection, table: str, row: Mapping[str, object]) -> str:
    """Insert one row (an id is made when the row has none) and return its id."""
    assert table in TABLES
    values = {"id": new_id(), **row}
    cols = sorted(values)
    c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [values[k] for k in cols])
    return str(values["id"])


def update(c: sqlite3.Connection, table: str, row_id: str, fields: Mapping[str, object], where: str = "",
           args: Sequence[object] = ()) -> int:
    """Set `fields` on one row, only when `where` also holds. How many rows changed."""
    assert table in TABLES
    if not fields:
        return 0
    cols = sorted(fields)
    sql = f"UPDATE {table} SET {','.join(k + '=?' for k in cols)} WHERE id=?" + (f" AND ({where})" if where else "")
    return c.execute(sql, [*(fields[k] for k in cols), row_id, *args]).rowcount


def get(c: sqlite3.Connection, table: str, row_id: str) -> Any:
    assert table in TABLES
    return c.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()


def insert_lines(c: sqlite3.Connection, kind: str, parent_id: str, rows: Sequence[Mapping[str, object]]) -> None:
    table, key = LINES[kind]
    for n, r in enumerate(rows):
        insert(c, table, {key: parent_id, "line_no": n, **r})


def lines(c: sqlite3.Connection, kind: str, parent_id: str) -> list[Any]:
    """An order's or estimate's lines, in the order they were written."""
    table, key = LINES[kind]
    return c.execute(f"SELECT * FROM {table} WHERE {key}=? ORDER BY line_no", (parent_id,)).fetchall()


def next_number(c: sqlite3.Connection, table: str, book_id: str, prefix: str) -> str:
    """The next free number in a book: PO-0001 onward (EST-0001 onward for estimates)."""
    assert table in TABLES
    n = int(c.execute(f"SELECT COUNT(*) FROM {table} WHERE book_id=?", (book_id,)).fetchone()[0]) + 1
    while c.execute(f"SELECT 1 FROM {table} WHERE book_id=? AND number=?", (book_id, f"{prefix}-{n:04d}")).fetchone():
        n += 1
    return f"{prefix}-{n:04d}"


def number_taken(c: sqlite3.Connection, table: str, book_id: str, number: str) -> bool:
    assert table in TABLES
    row = c.execute(f"SELECT 1 FROM {table} WHERE book_id=? AND number=? LIMIT 1", (book_id, number)).fetchone()
    return row is not None


def po_line(c: sqlite3.Connection, po_line_id: str) -> Any:
    """An order line with its order's book, vendor and status."""
    return c.execute("SELECT l.*, p.book_id, p.vendor_id, p.status FROM fin_po_lines l "
                     "JOIN fin_purchase_orders p ON p.id=l.po_id WHERE l.id=?", (po_line_id,)).fetchone()


def billed(c: sqlite3.Connection, po_line_id: str) -> int:
    """What the bills on an order line have billed so far: every bill but a void one."""
    row = c.execute("SELECT COALESCE(SUM(bl.amount), 0) FROM fin_bill_lines bl JOIN fin_bills b ON b.id=bl.bill_id "
                    "WHERE bl.po_line_id=? AND b.status!='void'", (po_line_id,)).fetchone()
    return int(row[0])


def bill_orders(c: sqlite3.Connection, bill_id: str) -> list[str]:
    """The orders a bill bills a line of, in id order."""
    return [str(r[0]) for r in c.execute(
        "SELECT DISTINCT p.id FROM fin_bill_lines bl JOIN fin_po_lines l ON l.id=bl.po_line_id "
        "JOIN fin_purchase_orders p ON p.id=l.po_id WHERE bl.bill_id=? ORDER BY p.id", (bill_id,))]


def claim_invoice(c: sqlite3.Connection, estimate_id: str, invoice_id: str, at: str) -> int:
    """Link an accepted estimate to the invoice made from it. Only an estimate not yet invoiced takes the link."""
    return c.execute("UPDATE fin_estimates SET status='invoiced', invoice_id=?, updated_at=? "
                     "WHERE id=? AND status='accepted' AND invoice_id IS NULL", (invoice_id, at, estimate_id)).rowcount
