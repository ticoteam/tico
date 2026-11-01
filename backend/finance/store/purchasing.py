"""SQL for purchase orders and estimates (spec §4.4, §4.5): the orders and estimates, their lines, the sums a bill's
lines bill against an order line, and the one-time link from an estimate to its invoice. Callers check rights, views
and states; this module writes rows and reads sums. A stored status is what was done to a document. The status a
reader sees can be derived: an approved order billed in part is partial, and an estimate with an invoice is invoiced."""
import json
import sqlite3
from collections.abc import Sequence
from collections.abc import Mapping, Sequence
from typing import Any

from .banking import like
from .ledger import new_id

TABLES = frozenset({"fin_purchase_orders", "fin_po_lines", "fin_estimates", "fin_estimate_lines"})
LINES = {"po": ("fin_po_lines", "po_id"), "estimate": ("fin_estimate_lines", "estimate_id")}
CONTACT = {"po": "vendor_id", "estimate": "customer_id"}
HEAD = {"po": "fin_purchase_orders", "estimate": "fin_estimates"}
DATED = {"po": "po_date", "estimate": "estimate_date"}
# What the bills on an order's lines have billed: the lines of every bill but a void one, and what a bill keeps billed
# on a line its provider lines no longer name (fin_bill_retained).
_BILLED_ON = ("SELECT COALESCE(SUM(x.amount), 0) FROM ("
              "SELECT bl.amount AS amount FROM fin_bill_lines bl JOIN fin_bills b ON b.id=bl.bill_id "
              "JOIN fin_po_lines l ON l.id=bl.po_line_id WHERE l.po_id={po} AND b.status!='void' "
              "UNION ALL "
              "SELECT r.amount AS amount FROM fin_bill_retained r JOIN fin_bills b ON b.id=r.bill_id "
              "JOIN fin_po_lines l ON l.id=r.po_line_id WHERE l.po_id={po} AND b.status!='void') x")
# What one order line has been billed: the same two sums, on the line.
_BILLED_LINE = ("SELECT COALESCE((SELECT SUM(bl.amount) FROM fin_bill_lines bl JOIN fin_bills b ON b.id=bl.bill_id "
                "WHERE bl.po_line_id=? AND b.status!='void'), 0) + COALESCE((SELECT SUM(r.amount) "
                "FROM fin_bill_retained r JOIN fin_bills b ON b.id=r.bill_id WHERE r.po_line_id=? "
                "AND b.status!='void'), 0)")


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


def delete_lines(c: sqlite3.Connection, kind: str, parent_id: str) -> None:
    """Drop an order's lines, to be written again from the provider's revision (the caller checks nothing bills them)."""
    if kind == "po":
        c.execute("DELETE FROM fin_po_lines WHERE po_id=?", (parent_id,))
    else:
        c.execute("DELETE FROM fin_estimate_lines WHERE estimate_id=?", (parent_id,))


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
    """What the bills on an order line have billed so far: every bill but a void one, and what a bill keeps billed on
    the line after a provider revision that no longer names it."""
    return int(c.execute(_BILLED_LINE, (po_line_id, po_line_id)).fetchone()[0])


def bill_retained(c: sqlite3.Connection, bill_id: str) -> dict[str, int]:
    """What a bill keeps billed on the order lines its provider lines no longer name, by order line."""
    return {str(r[0]): int(r[1]) for r in c.execute(
        "SELECT po_line_id, amount FROM fin_bill_retained WHERE bill_id=?", (bill_id,))}


def set_bill_retained(c: sqlite3.Connection, bill_id: str, amounts: Mapping[str, int]) -> None:
    """Replace what a bill keeps billed on order lines it no longer names. An order line kept at none is not stored."""
    c.execute("DELETE FROM fin_bill_retained WHERE bill_id=?", (bill_id,))
    for po_line_id, amount in amounts.items():
        if amount > 0:
            c.execute("INSERT INTO fin_bill_retained(bill_id, po_line_id, amount) VALUES(?, ?, ?)",
                      (bill_id, po_line_id, amount))


# A bill write held against an order line's room until it is refused outright or confirmed. Confirmed writes are in
# billed(); a write refused outright (failed, not unknown) bills nothing.
_HELD_WRITES = ("SELECT payload_json FROM fin_outbox WHERE op='bill.create' AND "
                "NOT (status='sent' OR (status='failed' AND unknown=0)) AND instr(payload_json, ?) > 0")
# The writes that may already have reached the books before a write is sent: an unknown answer, or a retry that is
# sent again without a check. A write still waiting for its first try has taken no room, so of two that wait the one
# sent first keeps it and the second is refused when its turn comes.
_LANDED_WRITES = ("SELECT payload_json FROM fin_outbox WHERE op='bill.create' AND "
                  "((status='failed' AND unknown=1) OR (status IN ('pending','withheld') AND attempts>0)) "
                  "AND instr(payload_json, ?) > 0")


def _billed_on(c: sqlite3.Connection, query: str, po_line_id: str) -> int:
    total = 0
    for row in c.execute(query, (po_line_id,)):
        for line in json.loads(str(row[0])).get("lines", []):
            if line.get("po_line_id") == po_line_id:
                total += int(line["amount"])
    return total


def line_room(c: sqlite3.Connection, po_line_id: str) -> int:
    """What an order line has left to bill when a write is sent: its amount less what its bills bill and the writes
    that may already have landed on it, or none when it is not there."""
    row = c.execute("SELECT amount FROM fin_po_lines WHERE id=?", (po_line_id,)).fetchone()
    if row is None:
        return 0
    return int(row[0]) - billed(c, po_line_id) - _billed_on(c, _LANDED_WRITES, po_line_id)


def queued_for_line(c: sqlite3.Connection, po_line_id: str) -> int:
    """What the bill writes still held bill on an order line, from their payloads: every write but one refused outright
    and one confirmed."""
    return _billed_on(c, _HELD_WRITES, po_line_id)


def billed_total(c: sqlite3.Connection, po_id: str) -> int:
    """What the bills on an order's lines have billed so far: every bill but a void one."""
    return int(c.execute(_BILLED_ON.format(po="?"), (po_id, po_id)).fetchone()[0])


def held_total(c: sqlite3.Connection, po_id: str) -> int:
    """What an order's live holds keep of its budgets now (a hold shrinks as bills take it over)."""
    return int(c.execute("SELECT COALESCE(SUM(h.amount), 0) FROM fin_po_lines l JOIN fin_holds h "
                         "ON h.source='purchase_order' AND h.source_id=l.po_id || ':' || l.line_no AND h.status='held' "
                         "WHERE l.po_id=?", (po_id,)).fetchone()[0])


def order_bills(c: sqlite3.Connection, po_id: str) -> list[sqlite3.Row]:
    """The bills that bill an order's lines (a void one bills nothing), oldest first, with what each bills on it."""
    return c.execute(
        "SELECT b.*, k.name AS contact_name, SUM(x.amount) AS on_order FROM (SELECT bill_id, po_line_id, amount FROM "
        "fin_bill_lines UNION ALL SELECT bill_id, po_line_id, amount FROM fin_bill_retained) x "
        "JOIN fin_po_lines l ON l.id=x.po_line_id JOIN fin_bills b ON b.id=x.bill_id "
        "LEFT JOIN fin_contacts k ON k.id=b.vendor_id WHERE l.po_id=? AND b.status!='void' "
        "GROUP BY b.id ORDER BY b.bill_date, b.id", (po_id,)).fetchall()


def shown_po(status: str, billed_so_far: int, total: int) -> str:
    """The status a reader sees of an order: an approved order billed in part is partial."""
    return "partial" if status == "approved" and 0 < billed_so_far < total else status


def shown_estimate(status: str, invoice_id: object) -> str:
    """The status a reader sees of an estimate: one with an invoice is invoiced."""
    return "invoiced" if invoice_id else status


def bill_orders(c: sqlite3.Connection, bill_id: str) -> list[str]:
    """The orders a bill bills a line of, in id order: its own lines, and the lines it keeps billed."""
    return [str(r[0]) for r in c.execute(
        "SELECT DISTINCT p.id FROM (SELECT bill_id, po_line_id FROM fin_bill_lines UNION "
        "SELECT bill_id, po_line_id FROM fin_bill_retained) bl JOIN fin_po_lines l ON l.id=bl.po_line_id "
        "JOIN fin_purchase_orders p ON p.id=l.po_id WHERE bl.bill_id=? ORDER BY p.id", (bill_id,))]


def claim_invoice(c: sqlite3.Connection, estimate_id: str, invoice_id: str, at: str) -> int:
    """Link an accepted estimate to the invoice made from it. Only an estimate not yet invoiced takes the link; its
    stored status stays accepted, and it shows as invoiced."""
    return c.execute("UPDATE fin_estimates SET invoice_id=?, updated_at=? "
                     "WHERE id=? AND status='accepted' AND invoice_id IS NULL", (invoice_id, at, estimate_id)).rowcount


def _billed_expr(kind: str) -> str:
    return f"({_BILLED_ON.format(po='d.id')})" if kind == "po" else "0"


def _status_clause(kind: str, status: str) -> tuple[str, list[object]]:
    """The SQL that keeps the rows showing `status` (the shown status, see shown_po and shown_estimate)."""
    if kind == "po":
        billed = _billed_expr(kind)
        partial = f"d.status='approved' AND {billed} > 0 AND {billed} < d.total"
        if status == "partial":
            return partial, []
        if status == "approved":
            return f"d.status='approved' AND NOT ({partial})", []
        return "d.status=?", [status]
    if status == "invoiced":
        return "d.invoice_id IS NOT NULL", []
    return "d.status=? AND d.invoice_id IS NULL", [status]


def listing(c: sqlite3.Connection, kind: str, *, book_id: str, status: str = "", contact_id: str = "", q: str = "",
            before: tuple[str, str] | None = None, most: int = 200,
            tagged: Sequence[tuple[str, Sequence[str]]] = ()) -> list[sqlite3.Row]:
    """A book's orders or estimates, newest first by date (then id), with the contact's name and what each order has
    billed. A status (as shown), a contact, a search over number, memo and contact name, a line's tags (tag name, any
    of these values) and a cursor (the date and id of the last row before) narrow them."""
    table, contact, dated = HEAD[kind], CONTACT[kind], DATED[kind]
    lines_table, parent = LINES[kind]
    sql = (f"SELECT d.*, k.name AS contact_name, {_billed_expr(kind)} AS billed FROM {table} d "
           f"JOIN fin_contacts k ON k.id = d.{contact} WHERE d.book_id=?")
    args: list[object] = [book_id]
    if status:
        clause, extra = _status_clause(kind, status)
        sql += f" AND ({clause})"
        args += extra
    if contact_id:
        sql += f" AND d.{contact}=?"
        args.append(contact_id)
    for tag, values in tagged:
        marks = ",".join("?" * len(values))
        sql += (f" AND EXISTS (SELECT 1 FROM {lines_table} x WHERE x.{parent}=d.id "
                f"AND json_extract(x.tags_json, '$.' || ?) IN ({marks}))")
        args += [tag, *values]
    if q:
        sql += " AND (d.number LIKE ? ESCAPE '\\' OR d.memo LIKE ? ESCAPE '\\' OR k.name LIKE ? ESCAPE '\\')"
        args += [like(q)] * 3
    if before:
        sql += f" AND (d.{dated}<? OR (d.{dated}=? AND d.id<?))"
        args += [before[0], before[0], before[1]]
    return c.execute(sql + f" ORDER BY d.{dated} DESC, d.id DESC LIMIT ?", [*args, most]).fetchall()


def head(c: sqlite3.Connection, kind: str, doc_id: str) -> sqlite3.Row | None:
    """One order or estimate with its contact's name and what it has billed."""
    return c.execute(f"SELECT d.*, k.name AS contact_name, {_billed_expr(kind)} AS billed FROM {HEAD[kind]} d "
                     f"JOIN fin_contacts k ON k.id = d.{CONTACT[kind]} WHERE d.id=?", (doc_id,)).fetchone()
