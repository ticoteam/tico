"""The tax tables and the rows the tax workpapers read (spec §11). All SQL for the tax tables and their workpapers lives
here; finance/tax.py reads these rows whole, only for a caller who sees everything."""
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

# Feed kinds that are card charges, credit or debit, even on a bank money account.
CARD_FEED_KINDS = ("debitCardTransaction", "creditCardTransaction")
# The SQL test that a feed row (f, with its money account m) is a card charge. The values are constants, not input.
_CARD_KINDS_SQL = ", ".join(f"'{kind}'" for kind in CARD_FEED_KINDS)
_CARD_ROW = (f"CASE WHEN f.card_id IS NOT NULL OR f.kind IN ({_CARD_KINDS_SQL}) "
             "OR COALESCE(m.kind, '') = 'card_program' THEN 1 ELSE 0 END")


def _marks(values: Sequence[object]) -> str:
    return ",".join("?" * len(values))


def jurisdictions(c: sqlite3.Connection, book_id: str) -> list[Any]:
    return c.execute("SELECT * FROM fin_tax_jurisdictions WHERE book_id=? ORDER BY code", (book_id,)).fetchall()


def jurisdiction_named(c: sqlite3.Connection, book_id: str, code: str) -> Any:
    return c.execute("SELECT * FROM fin_tax_jurisdictions WHERE book_id=? AND code=?", (book_id, code)).fetchone()


def insert_jurisdiction(c: sqlite3.Connection, row: Mapping[str, object]) -> None:
    names = ", ".join(row)
    c.execute(f"INSERT INTO fin_tax_jurisdictions ({names}) VALUES ({_marks(list(row.values()))})",
              tuple(row.values()))


def contact_kind(c: sqlite3.Connection, book_id: str, contact_id: str) -> str:
    row = c.execute("SELECT kind FROM fin_contacts WHERE book_id=? AND id=?", (book_id, contact_id)).fetchone()
    return str(row[0]) if row else ""


def seeded(c: sqlite3.Connection, key: str) -> str | None:
    """The external rule a seed key made, or None when it is not seeded yet."""
    row = c.execute("SELECT rule_id FROM fin_tax_seeds WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else None


def add_seed(c: sqlite3.Connection, key: str, rule_id: str, at: str) -> None:
    c.execute("INSERT INTO fin_tax_seeds(key, rule_id, created_at) VALUES(?,?,?)", (key, rule_id, at))


def vendors_1099(c: sqlite3.Connection, book_id: str) -> list[Any]:
    """The vendors flagged for a 1099: id, name, and the vault ref of their TIN (empty when none is on file)."""
    return c.execute("SELECT id, name, COALESCE(tax_id_ref, '') AS tin_ref FROM fin_contacts WHERE book_id=? "
                     "AND is_1099=1 AND kind IN ('vendor','both') ORDER BY name", (book_id,)).fetchall()


def invoice_lines(c: sqlite3.Connection, book_id: str, start: str, end: str) -> list[Any]:
    """Every line of the posted invoices dated in the window, with its account's type and subtype and whether it is
    taxable."""
    return c.execute("SELECT i.id AS invoice_id, i.invoice_date, l.account_id, l.amount, l.taxable, a.type, a.subtype "
                     "FROM fin_invoice_lines l JOIN fin_invoices i ON i.id=l.invoice_id "
                     "JOIN fin_accounts a ON a.id=l.account_id WHERE i.book_id=? "
                     "AND i.status IN ('sent','partial','paid') AND i.invoice_date BETWEEN ? AND ? "
                     "ORDER BY i.id, l.id", (book_id, start, end)).fetchall()


def remittance_lines(c: sqlite3.Connection, book_id: str, account_ids: Sequence[str]) -> list[Any]:
    """The lines on these sales tax accounts of the bills not void, each with its bill's total and what has been paid on
    the bill, so a partial payment counts for its share of the line."""
    if not account_ids:
        return []
    return c.execute(
        "SELECT b.id AS bill_id, bl.account_id, bl.tags_json, bl.amount, "
        "(SELECT COALESCE(SUM(x.amount), 0) FROM fin_bill_lines x WHERE x.bill_id=b.id) AS bill_total, "
        "(SELECT COALESCE(SUM(s.amount), 0) FROM fin_settlements s WHERE s.to_kind='bill' AND s.to_id=b.id "
        "AND s.voided_at IS NULL) AS bill_paid "
        "FROM fin_bill_lines bl JOIN fin_bills b ON b.id=bl.bill_id "
        f"WHERE b.book_id=? AND b.status<>'void' AND bl.account_id IN ({_marks(account_ids)}) ORDER BY bl.id",
        (book_id, *account_ids)).fetchall()


def bill_lines(c: sqlite3.Connection, bill_ids: Sequence[str]) -> list[Any]:
    """Every line of these bills, each with the bill and the account it is on."""
    if not bill_ids:
        return []
    return c.execute(f"SELECT bill_id, account_id FROM fin_bill_lines WHERE bill_id IN ({_marks(bill_ids)})",
                     tuple(bill_ids)).fetchall()


def settlements(c: sqlite3.Connection, book_id: str, start: str, end: str) -> list[Any]:
    """Money settled against bills of a vendor in the window, with what it came from: the kind, the money account kind (a
    bank feed row, a payment's account, an expected payment's account) or GL subtype it went through, whether a feed row
    was a card charge, and the bill it paid."""
    return c.execute(
        "SELECT b.vendor_id, s.from_kind, s.amount, COALESCE(m.kind, pm.kind, em.kind, '') AS money_kind, "
        "COALESCE(ga.subtype, '') AS subtype, b.id AS bill_id, "
        f"CASE WHEN s.from_kind='feed' THEN {_CARD_ROW} ELSE 0 END AS by_card "
        "FROM fin_settlements s JOIN fin_bills b ON b.id=s.to_id "
        "LEFT JOIN fin_feed_txns f ON s.from_kind='feed' AND f.id=s.from_id "
        "LEFT JOIN fin_money_accounts m ON m.id=f.money_account_id "
        "LEFT JOIN fin_payments p ON s.from_kind='payment' AND p.id=s.from_id "
        "LEFT JOIN fin_money_accounts pm ON pm.id=p.money_account_id "
        "LEFT JOIN fin_money_accounts em ON s.from_kind='expected' AND em.id=s.from_id "
        "LEFT JOIN fin_accounts ga ON s.from_kind='account' AND ga.id=s.from_id "
        "WHERE s.book_id=? AND s.to_kind='bill' AND s.voided_at IS NULL AND s.settled_on BETWEEN ? AND ? "
        "AND b.vendor_id IS NOT NULL ORDER BY s.id", (book_id, start, end)).fetchall()


def expense_lines(c: sqlite3.Connection, book_id: str, start: str, end: str) -> list[Any]:
    """Expense lines a vendor is named on, in the window, each with whether it came from a card: the feed row that posted
    its entry, or the feed row a reclassification beside it settled. A reversal takes its original's row, and an entry from
    no feed row is a card charge when it posted under the card source. Bank and card rows post straight to an expense (a
    bill's lines post under the bill, so they are not here); a reversal keeps its source, so the two net."""
    origin = "COALESCE(e.reverses_id, e.id)"
    posted = (f"(SELECT {_CARD_ROW} FROM fin_feed_txns f LEFT JOIN fin_money_accounts m ON m.id=f.money_account_id "
              f"WHERE f.entry_id={origin} LIMIT 1)")
    reclass = (f"(SELECT {_CARD_ROW} FROM fin_settlements s JOIN fin_feed_txns f ON f.id=s.from_id "
               "LEFT JOIN fin_money_accounts m ON m.id=f.money_account_id "
               f"WHERE s.from_kind='feed' AND s.voided_at IS NULL AND s.entry_id={origin} LIMIT 1)")
    return c.execute("SELECT l.account_id, l.contact_id AS vendor_key, l.amount, "
                     f"COALESCE({posted}, {reclass}, CASE WHEN e.source='card' THEN 1 ELSE 0 END) AS by_card "
                     "FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id JOIN fin_accounts a ON a.id=l.account_id "
                     "WHERE l.book_id=? AND l.entry_date BETWEEN ? AND ? AND e.source IN ('bank','card') "
                     "AND a.type='expense' AND l.contact_id<>'' ORDER BY l.id", (book_id, start, end)).fetchall()
