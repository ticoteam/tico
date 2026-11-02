"""Prepaid bill lines and the months each one was expensed (backend/finance/prepaids.py)."""
import sqlite3
from typing import Any

# A prepaid line counts while its bill is entered and not voided: an entered bill keeps amortizing once it is paid.
_LINE = ("SELECT l.id AS line_id, l.bill_id, l.account_id, l.amount, l.description, l.tags_json, "
         "l.prepaid_start, l.prepaid_months, b.book_id, b.status, b.vendor_id, b.number, v.name AS vendor_name "
         "FROM fin_bill_lines l JOIN fin_bills b ON b.id=l.bill_id LEFT JOIN fin_contacts v ON v.id=b.vendor_id")


def lines(c: sqlite3.Connection, book_id: str) -> list[Any]:
    """The book's entered prepaid bill lines, oldest bill first."""
    return c.execute(f"{_LINE} WHERE b.book_id=? AND l.prepaid_start IS NOT NULL "
                     "AND b.status IN ('open','scheduled','paid') ORDER BY b.created_at, l.line_no", (book_id,)).fetchall()


def line(c: sqlite3.Connection, book_id: str, line_id: str) -> Any:
    return c.execute(f"{_LINE} WHERE b.book_id=? AND l.id=? AND l.prepaid_start IS NOT NULL", (book_id, line_id)).fetchone()


def posted_months(c: sqlite3.Connection, line_id: str) -> set[str]:
    return {str(r[0]) for r in c.execute("SELECT month FROM fin_prepaid_postings WHERE bill_line_id=?", (line_id,))}


def posted_entries(c: sqlite3.Connection, bill_id: str) -> list[str]:
    """The entries a bill's prepaid lines were expensed by, month by month."""
    return [str(r[0]) for r in c.execute(
        "SELECT p.entry_id FROM fin_prepaid_postings p JOIN fin_bill_lines l ON l.id=p.bill_line_id "
        "WHERE l.bill_id=? ORDER BY p.month, p.bill_line_id", (bill_id,))]


def insert(c: sqlite3.Connection, row: dict[str, object]) -> None:
    cols = sorted(row)
    c.execute(f"INSERT INTO fin_prepaid_postings({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
              [row[k] for k in cols])
