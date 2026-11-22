"""Outside references (docs/dev/pipelines-v3.md "Sources and items"): an item outside Tico is named by the connection
it came through (a `tools` row) as well as its source, type and key, so two connections holding the same item id are
two items.

- `tools.record_owner`: who owns the records the connection holds: 'outside', 'tico', or '' (not said).
- `task_links.connection_id` and `task_links.is_primary` (the link an intake made the task for); `item_metadata` gains
  `connection_id`. GitHub's rows keep ''.
- The unique indexes on `item_metadata` and `task_links` (one item per task) include the connection, and at most one
  task holds the primary link to an item of one connection.
- `entity_external_refs` already has `connection_id`; its inline UNIQUE did not include it. SQLite cannot change an
  inline constraint, so the table is built again with the same columns and rows (the rowid kept), its indexes and the
  references to it as they were.

The connection is the last column of each index, so a read that names the source, type and key without it (GitHub's)
still uses the index as before.
"""

FOREIGN_KEYS_OFF = True

REF_COLUMNS = (
    "id",
    "entity_id",
    "connection_id",
    "system",
    "external_type",
    "external_id",
    "url",
    "revision",
    "synced_at",
    "status",
)

REF_TABLE = """CREATE TABLE entity_external_refs_new (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(id),
  connection_id TEXT NOT NULL DEFAULT '',
  system TEXT NOT NULL,
  external_type TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  revision TEXT NOT NULL DEFAULT '',
  synced_at TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE (system, external_type, external_id, connection_id))"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add(conn, table, column, definition):
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _external_refs(conn):
    columns = _columns(conn, "entity_external_refs")
    if not columns:
        raise RuntimeError("The outside references migration found no entity_external_refs table.")
    if columns != set(REF_COLUMNS):
        raise RuntimeError(f"entity_external_refs has unexpected columns: {sorted(columns ^ set(REF_COLUMNS))}")
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='entity_external_refs' AND sql IS NOT NULL"
    ).fetchall()
    # SQLite rechecks every trigger and view during the rename, including ones that use tables built after migrations.
    aside = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view') AND sql IS NOT NULL"
    ).fetchall()
    for kind, name, _ in aside:
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    conn.execute(REF_TABLE)
    names = ", ".join(("rowid", *REF_COLUMNS))
    conn.execute(f"INSERT INTO entity_external_refs_new ({names}) SELECT {names} FROM entity_external_refs")
    conn.execute("DROP TABLE entity_external_refs")
    conn.execute("ALTER TABLE entity_external_refs_new RENAME TO entity_external_refs")
    for (statement,) in indexes:
        conn.execute(statement)
    for _, _, statement in aside:
        conn.execute(statement)


def up(conn):
    _add(conn, "tools", "record_owner", "TEXT NOT NULL DEFAULT ''")
    _add(conn, "task_links", "connection_id", "TEXT NOT NULL DEFAULT ''")
    _add(conn, "task_links", "is_primary", "INTEGER NOT NULL DEFAULT 0")
    _add(conn, "item_metadata", "connection_id", "TEXT NOT NULL DEFAULT ''")
    conn.execute("DROP INDEX IF EXISTS item_metadata_item")
    conn.execute("CREATE UNIQUE INDEX item_metadata_item ON item_metadata(source, item_type, item_key, connection_id)")
    conn.execute("DROP INDEX IF EXISTS task_links_item")
    conn.execute(
        "CREATE UNIQUE INDEX task_links_item ON task_links(task_id, source, item_type, item_key, connection_id) "
        "WHERE source IS NOT NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX task_links_primary_item ON task_links(source, item_type, item_key, connection_id) "
        "WHERE is_primary = 1 AND source IS NOT NULL"
    )
    _external_refs(conn)
