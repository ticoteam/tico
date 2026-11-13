"""`task_relations` takes one more kind, `after`: `from_task` merges after `to_task` (backend/merge_order.py). SQLite
cannot change a CHECK in place, so the table is made again with the same rows, indexes and order. A database whose
table already allows `after`, or has none yet (a new file: task_relations.SCHEMA makes it with the new CHECK), is left
as it is."""

FOREIGN_KEYS_OFF = True

TABLE = (
    "CREATE TABLE task_relations("
    "from_task TEXT NOT NULL REFERENCES tasks(id), to_task TEXT NOT NULL REFERENCES tasks(id), "
    "kind TEXT NOT NULL CHECK (kind IN ('parent','blocks','related','duplicate_of','follow_up','after')), "
    "created_by TEXT, created_at TEXT NOT NULL, "
    "PRIMARY KEY(from_task, to_task, kind), "
    "CHECK (from_task <> to_task), CHECK (kind <> 'related' OR from_task < to_task))"
)
INDEXES = (
    "CREATE INDEX IF NOT EXISTS task_relations_to ON task_relations(to_task, kind)",
    "CREATE INDEX IF NOT EXISTS task_relations_from ON task_relations(from_task, kind)",
    "CREATE UNIQUE INDEX IF NOT EXISTS task_relations_one_parent ON task_relations(from_task) WHERE kind='parent'",
)


def _sql(conn, name):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row[0] if row else None


def _aside(conn):
    """A rebuild re-checks every trigger and view that names the table: they step aside and go back as they were."""
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _sql_text in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return aside


def up(conn):
    sql = _sql(conn, "task_relations")
    if not sql or "'after'" in sql:
        return
    # An old Draft #110 table (task_id, related_id) is task_relations.migrate's to replace, at start.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_relations)")}
    if "from_task" not in columns:
        return
    aside = _aside(conn)
    conn.execute("ALTER TABLE task_relations RENAME TO task_relations_before_after")
    for index in ("task_relations_to", "task_relations_from", "task_relations_one_parent"):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute(TABLE)
    # rowid order is kept: relations list in (created_at, rowid) order.
    conn.execute(
        "INSERT INTO task_relations(rowid, from_task, to_task, kind, created_by, created_at) "
        "SELECT rowid, from_task, to_task, kind, created_by, created_at FROM task_relations_before_after"
    )
    conn.execute("DROP TABLE task_relations_before_after")
    for statement in INDEXES:
        conn.execute(statement)
    for _kind, _name, statement in aside:
        conn.execute(statement)
