"""SQL for fin_dimensions: the classes a line's class tag names (QuickBooks classes, Xero's second tracking category),
and the moves that make a record's id another id (a record the books took under two local ids). Callers check rights
first."""
import sqlite3

ITEM_LINE_TABLES = ("fin_bill_lines", "fin_invoice_lines", "fin_po_lines", "fin_estimate_lines")
TAG_TABLES = ("fin_bill_lines", "fin_invoice_lines", "fin_payments", "fin_po_lines", "fin_estimate_lines")
TAG_KIND = {"places": "place", "projects": "project"}


def rename_record(c: sqlite3.Connection, table: str, from_id: str, to_id: str) -> None:
    """Every line and tag that names one record id names the other: an item on a document line, a place or a project
    on a tag (the line tag rows and the tags stored on each line)."""
    if table == "items":
        for name in ITEM_LINE_TABLES:
            c.execute(f"UPDATE {name} SET item_id=? WHERE item_id=?", (to_id, from_id))
        return
    kind = TAG_KIND[table]
    c.execute("UPDATE fin_line_tags SET value=? WHERE kind=? AND value=?", (to_id, kind, from_id))
    old, new = f'"{kind}":"{from_id}"', f'"{kind}":"{to_id}"'
    for name in TAG_TABLES:
        c.execute(f"UPDATE {name} SET tags_json=REPLACE(tags_json, ?, ?) WHERE instr(tags_json, ?) > 0",
                  (old, new, old))


def class_active(c: sqlite3.Connection, dimension_id: str) -> bool:
    row = c.execute("SELECT active FROM fin_dimensions WHERE id=? AND kind='class'", (dimension_id,)).fetchone()
    return row is not None and bool(row[0])


def class_exists(c: sqlite3.Connection, dimension_id: str) -> bool:
    return c.execute("SELECT 1 FROM fin_dimensions WHERE id=? AND kind='class'", (dimension_id,)).fetchone() is not None


def class_insert(c: sqlite3.Connection, *, dimension_id: str, value: str, name: str, active: bool, at: str,
                 by: str) -> None:
    """A provider class as a class dimension; `value` is the provider's key, unique among the classes."""
    c.execute("INSERT INTO fin_dimensions(id, kind, value, name, active, created_at, created_by) "
              "VALUES(?, 'class', ?, ?, ?, ?, ?)", (dimension_id, value, name, int(active), at, by))


def class_update(c: sqlite3.Connection, dimension_id: str, *, name: str, active: bool) -> None:
    c.execute("UPDATE fin_dimensions SET name=?, active=? WHERE id=? AND kind='class'",
              (name, int(active), dimension_id))


def class_set_active(c: sqlite3.Connection, dimension_id: str, active: bool) -> None:
    c.execute("UPDATE fin_dimensions SET active=? WHERE id=? AND kind='class'", (int(active), dimension_id))


def class_names(c: sqlite3.Connection) -> dict[str, str]:
    """Every class's name by id, active or not: a line keeps naming the class it was tagged with."""
    return {str(r[0]): str(r[1]) for r in c.execute("SELECT id, name FROM fin_dimensions WHERE kind='class'")}
