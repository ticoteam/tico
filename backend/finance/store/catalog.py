"""SQL for fin_dimensions: the classes a line's class tag names (QuickBooks classes, Xero's second tracking category).
Callers check rights first."""
import sqlite3


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


def classes_active(c: sqlite3.Connection) -> list[tuple[str, str]]:
    """The classes a new line may name, by name: (id, name)."""
    return [(str(r[0]), str(r[1])) for r in c.execute(
        "SELECT id, name FROM fin_dimensions WHERE kind='class' AND active=1 ORDER BY name, id")]
