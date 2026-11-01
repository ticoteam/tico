"""SQL for fin_dimensions: the classes a line's class tag names (QuickBooks classes, Xero's second tracking category).
Callers check rights first."""
import sqlite3


def class_active(c: sqlite3.Connection, dimension_id: str) -> bool:
    row = c.execute("SELECT active FROM fin_dimensions WHERE id=? AND kind='class'", (dimension_id,)).fetchone()
    return row is not None and bool(row[0])
