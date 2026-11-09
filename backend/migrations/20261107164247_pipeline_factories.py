"""Pipeline F0: factories. A factory groups pipelines (Product factory: Roadmap and Dev; Lead factory: Campaigns and Sales).
A pipeline belongs to at most one factory: task_types.factory_id, NULL for a pipeline in none. This is schema only; no
code reads or writes a factory yet."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


TABLE = """CREATE TABLE IF NOT EXISTS factories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT,
    position INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    retired_at TEXT
)"""
NAME_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS factories_name ON factories(name) WHERE retired_at IS NULL"


def up(conn):
    conn.execute(TABLE)
    conn.execute(NAME_INDEX)
    types = _columns(conn, "task_types")
    if types and "factory_id" not in types:
        conn.execute("ALTER TABLE task_types ADD COLUMN factory_id TEXT")
