"""The names a stored row, snapshot or wire field was saved under before the cleanup's table and column renames, read
under this code's names. The map is backend/migrations/renames_data.py (from scripts/cleanup/renames.yaml).

Every database this code opens already has the new names (the renames ran in migration files the compatibility floor
has retired, backend/migrations/__init__.py). What still carries an old name is data: a task snapshot in the trash, a
stored rule's condition, an undo record, a runner's wire field. These read it under the name it has now.
"""

from .renames_data import COLUMNS, TABLES


def _wave_order(wave):
    return (0, int(wave), "") if str(wave).isdigit() else (1, 0, str(wave))


_ORDERED = [None, {}]  # [the COLUMNS it was built from, the map]: tests patch COLUMNS, so the tuple is the key


def _column_renames():
    """{table: [(old, new)]} in wave order, so a chain across waves (a -> b in one, b -> c in a later one) is followed.
    Built once per COLUMNS: every database a process opens asks, and callers only read it."""
    if _ORDERED[0] is not COLUMNS:
        out = {}
        for table, old, new, _wave in sorted(COLUMNS, key=lambda entry: _wave_order(entry[3])):
            out.setdefault(table, []).append((old, new))
        _ORDERED[:] = [COLUMNS, out]
    return _ORDERED[1]


def current_table(table):
    """A table's name in this code, for a name a stored row was saved under before a rename."""
    for old, new, _wave in TABLES:
        if table == old:
            table = new
    return table


def current_row(table, row):
    """A copy of a stored row (a dict keyed by column names, saved before one or more renames) keyed by this code's
    column names: every rename of its table, all waves, in wave order. A key whose new name the row also has is left
    as it is, for the caller to refuse rather than guess which holds the data. The table is this code's name."""
    out = dict(row)
    for old, new in _column_renames().get(table, ()):
        if old in out and new not in out:
            out[new] = out.pop(old)
    return out


def current_rows(rows):
    """{table: [row]} as stored (a snapshot keyed by table, then column) under this code's table and column names."""
    out = {}
    for table, found in rows.items():
        name = current_table(table)
        out.setdefault(name, []).extend(current_row(name, row) if isinstance(row, dict) else row for row in found)
    return out


def current_column(table, column):
    """One column's name in this code, for a table (this code's name) and a column name saved before a rename."""
    return current_row(table, {column: None}).popitem()[0]
