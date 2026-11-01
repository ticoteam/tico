"""The cleanup's table and column renames, applied to an existing database before anything else touches its schema.

Each wave is a migration file (backend/migrations/0013_rails_renames_wave1.py for wave 1) whose up(conn) calls `apply`
for that wave. The migration files run right after hubdb's numbered steps and before any other CREATE, ALTER or
add_column: startup code names the new columns, so an `add_column('t', 'created_at')` that ran first would add an empty
column beside the old one and the rename would then collide. The map is backend/migrations/renames_data.py (generated from
scripts/cleanup/renames.yaml).

Each rename is SQLite's own ALTER TABLE ... RENAME, with legacy_alter_table off so SQLite rewrites every trigger,
view, index and foreign key that names the column (index names stay as they are). `apply` runs in the caller's
transaction and never commits, for a migration file's up(conn); `apply_now` wraps it in one transaction of its own.
A rename whose old name is gone is done already, so a second boot does nothing.
"""

from .renames_data import COLUMNS, TABLES


class RenameConflict(RuntimeError):
    """A table has both the old and the new name of a column: refuse rather than guess which holds the data."""


def pending(conn, waves=None):
    """The (kind, table, old, new) renames this database still needs, in order: tables first, then columns. `waves`
    limits the columns to those waves (all of the map's when None)."""
    columns = {}
    for table, column in conn.execute("SELECT m.name, p.name FROM sqlite_master m "
                                      "JOIN pragma_table_info(m.name) p WHERE m.type='table'"):
        columns.setdefault(table, set()).add(column)
    todo = []
    for old, new in TABLES:
        if old in columns and new not in columns:
            todo.append(("table", old, old, new))
            columns[new] = columns.pop(old)
        elif old in columns:
            raise RenameConflict(f"both tables {old} and {new} exist")
    for table, old, new, wave in COLUMNS:
        if waves is not None and wave not in waves:
            continue
        have = columns.get(table)
        if not have or old not in have:
            continue
        if new in have:
            raise RenameConflict(f"{table} has both {old} and {new}")
        todo.append(("column", table, old, new))
        have.discard(old)
        have.add(new)
    return todo


def apply(conn, waves=None):
    """Rename what this database still has under an old name, inside the caller's transaction: no BEGIN, no COMMIT
    (a migration file's up(conn) calls this). Tables and columns the database does not have (a fresh database, a
    module's tables not made yet) are skipped. Returns how many renames ran."""
    todo = pending(conn, waves)
    if not todo:
        return 0
    legacy = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table=OFF")      # SQLite rewrites the triggers, views and indexes that name it
    try:
        for kind, table, old, new in todo:
            if kind == "table":
                conn.execute(f'ALTER TABLE "{old}" RENAME TO "{new}"')
            else:
                conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')
    finally:
        conn.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
    return len(todo)


def apply_now(conn, waves=None):
    """`apply` in a transaction of its own (one transaction: a failure leaves the file as it was), for a caller that
    is not in one."""
    if conn.in_transaction:
        return apply(conn, waves)
    conn.execute("BEGIN IMMEDIATE")
    try:
        done = apply(conn, waves)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return done
