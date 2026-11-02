"""Table-level state of a hub database, and the comparison that proves an upgrade kept it.

For each ordinary table: its row count, each row's cells (one digest per column, so a row's values stay tied together
across columns), and a digest of each column's values. Every comparison is order-free, so a migration that rebuilds a
table and changes its rowids still compares equal. FTS5 indexes and their shadow tables are derived from other rows
and are left out. Columns the upgrade adds are the new schema and are not compared.

The change counters (COUNTERS) are not data: a trigger adds one to them on every write, the no-op ones too, and the
caches read them. They are compared as "never went back", not as equal.
"""
import hashlib
import sqlite3
from contextlib import closing


COUNTERS = frozenset({"roster_clock", "provenance_clock", "row_clock"})


def _digest(value):
    return hashlib.sha256(repr((type(value).__name__, value)).encode()).hexdigest()


def _combine(digests):
    return hashlib.sha256("\n".join(sorted(digests)).encode()).hexdigest()


def ordinary_tables(conn):
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    virtual = {name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL")}
    return sorted(name for name, _ in rows if name not in virtual and not any(name.startswith(v + "_") for v in virtual))


def table_state(path):
    """{table: {"rows": n, "cells": [{column: digest}, ...], "columns": {column: digest}}} for every ordinary table;
    a change counter carries {"rows": n, "counter": value} instead."""
    state = {}
    with closing(sqlite3.connect(path)) as conn:
        for table in ordinary_tables(conn):
            if table in COUNTERS:
                state[table] = {"rows": conn.execute('SELECT count(*) FROM "%s"' % table).fetchone()[0],
                                "counter": conn.execute('SELECT max(value) FROM "%s"' % table).fetchone()[0]}
                continue
            cursor = conn.execute('SELECT * FROM "%s"' % table.replace('"', '""'))
            names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            cells = [{name: _digest(value) for name, value in zip(names, row, strict=True)} for row in rows]
            state[table] = {"rows": len(rows), "cells": cells,
                            "columns": {name: _combine([row[name] for row in cells]) for name in names}}
    return state


def integrity(path):
    """Foreign-key and integrity problems the database reports; an empty list is a clean database."""
    with closing(sqlite3.connect(path)) as conn:
        problems = [("foreign_key", tuple(row)) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
        verdict = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        if verdict != ["ok"]:
            problems += [("integrity", v) for v in verdict]
    return problems


def _cell_key(cells):
    return sorted(tuple(sorted(cell.items())) for cell in cells)


def compare(before, after, renames):
    """Every problem between a state before an upgrade and the state after it. The rename map (tables, columns) is
    applied to the before side: `columns` keys are "table.column" with the table's old name."""
    tables = renames.get("tables", {})
    columns = renames.get("columns", {})
    problems = []
    for old_table, old in sorted(before.items()):
        new_table = tables.get(old_table, old_table)
        if new_table not in after:
            problems.append(f"{old_table}: table is gone (expected {new_table})")
            continue
        new = after[new_table]
        if old["rows"] != new["rows"]:
            problems.append(f"{old_table}: {old['rows']} rows before, {new['rows']} after")
            continue
        if "counter" in old:
            if (new.get("counter") or 0) < (old["counter"] or 0):
                problems.append(f"{old_table}: change counter went back ({old['counter']} before, {new.get('counter')} after)")
            continue
        mapping = {column: columns.get(f"{old_table}.{column}", column) for column in old["columns"]}
        for column, new_column in sorted(mapping.items()):
            if new_column not in new["columns"]:
                problems.append(f"{old_table}.{column}: column is gone (expected {new_column})")
            elif new["columns"][new_column] != old["columns"][column]:
                problems.append(f"{old_table}.{column}: values changed")
        if not any(p.startswith(f"{old_table}.") for p in problems):
            # Same cells, row for row, as multisets: a value that moved to another row is a change too.
            before_cells = [{mapping[c]: d for c, d in cell.items()} for cell in old["cells"]]
            after_cells = [{c: cell[c] for c in mapping.values() if c in cell} for cell in new["cells"]]
            if _cell_key(before_cells) != _cell_key(after_cells):
                problems.append(f"{old_table}: row contents changed")
    return problems
