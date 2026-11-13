"""`task_relations.until`: a dependency that holds past its task's end. `released` (the one value, and only on a
`depends_on` row): the task stays blocked after the other one is Done, until production runs a release that holds that
task's merged PRs (backend/task_depends.py). NULL on every row it carries over.

SQLite cannot add a column with a CHECK that names another column the same way a rebuild writes it, and a pending
migration may run before or after the one that lets the table take `depends_on` (20261113060000), so this one makes the
table again in its final shape, whatever shape it finds: the same rows (an old `blocks` row, which nothing writes since
the Pipelines v3 cut-over, is not carried over), indexes and order. The other then finds `depends_on` allowed and leaves
it. A table that has the column already, or none yet (a new file: task_relations.SCHEMA makes it), is left as it is."""

FOREIGN_KEYS_OFF = True

TABLE = (
    "CREATE TABLE task_relations("
    "from_task TEXT NOT NULL REFERENCES tasks(id), to_task TEXT NOT NULL REFERENCES tasks(id), "
    "kind TEXT NOT NULL CHECK (kind IN ('parent','related','duplicate_of','follow_up','after','depends_on')), "
    "created_by TEXT, created_at TEXT NOT NULL, "
    "until TEXT CHECK (until IS NULL OR (kind = 'depends_on' AND until = 'released')), "
    "PRIMARY KEY(from_task, to_task, kind), "
    "CHECK (from_task <> to_task), CHECK (kind <> 'related' OR from_task < to_task))"
)
INDEXES = (
    "CREATE INDEX IF NOT EXISTS task_relations_to ON task_relations(to_task, kind)",
    "CREATE INDEX IF NOT EXISTS task_relations_from ON task_relations(from_task, kind)",
    "CREATE UNIQUE INDEX IF NOT EXISTS task_relations_one_parent ON task_relations(from_task) WHERE kind='parent'",
)
KINDS = ("parent", "related", "duplicate_of", "follow_up", "after", "depends_on")


def _aside(conn):
    """A rebuild re-checks every trigger and view that names the table: they step aside and go back as they were."""
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _sql_text in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    return aside


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_relations)")}
    # An old Draft #110 table (task_id, related_id) is task_relations.migrate's to replace, at start.
    if "from_task" not in columns or "until" in columns:
        return
    aside = _aside(conn)
    conn.execute("ALTER TABLE task_relations RENAME TO task_relations_before_until")
    for index in ("task_relations_to", "task_relations_from", "task_relations_one_parent"):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute(TABLE)
    # rowid order is kept: relations list in (created_at, rowid) order.
    marks = ",".join("?" * len(KINDS))
    conn.execute(
        "INSERT INTO task_relations(rowid, from_task, to_task, kind, created_by, created_at) "
        "SELECT rowid, from_task, to_task, kind, created_by, created_at FROM task_relations_before_until "
        f"WHERE kind IN ({marks})",
        KINDS,
    )
    conn.execute("DROP TABLE task_relations_before_until")
    for statement in INDEXES:
        conn.execute(statement)
    for _kind, _name, statement in aside:
        conn.execute(statement)
