"""API tools and their keys (backend/api_tools.py): a `tools` row per service a bot calls with a key, each key a vault
credential that names its tool and its level (`read` or `read_write`), each grant its level, and the tool's checks and
proxied calls.

The three new tables are made here on every file. The columns go onto `credentials` and `credential_grants` only where
those tables exist: on a new file they do not yet, and backend/store.py's SCHEMA then makes them with the columns. A
grant that exists keeps Read and write, the level it has always had.
"""

TABLES = (
    "CREATE TABLE IF NOT EXISTS tools("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, service TEXT NOT NULL DEFAULT '',"
    " base_url TEXT NOT NULL DEFAULT '', auth_scheme TEXT NOT NULL DEFAULT 'bearer',"
    " check_path TEXT NOT NULL DEFAULT '', delivery TEXT NOT NULL DEFAULT 'proxy',"
    " created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tool_checks("
    " id TEXT PRIMARY KEY, tool_id TEXT NOT NULL REFERENCES tools(id), credential_id TEXT,"
    " ok INTEGER NOT NULL, status_code INTEGER, detail TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS tool_checks_by_tool ON tool_checks(tool_id, checked_at DESC)",
    "CREATE TABLE IF NOT EXISTS tool_calls("
    " id TEXT PRIMARY KEY, tool_id TEXT NOT NULL REFERENCES tools(id), credential_id TEXT,"
    " actor TEXT NOT NULL, bot TEXT NOT NULL DEFAULT '', access_level TEXT NOT NULL,"
    " method TEXT NOT NULL, path TEXT NOT NULL, status_code INTEGER, result TEXT NOT NULL,"
    " detail TEXT NOT NULL DEFAULT '', duration_ms INTEGER, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS tool_calls_by_tool ON tool_calls(tool_id, created_at DESC)",
)
COLUMNS = (
    ("credentials", "tool_id", "TEXT REFERENCES tools(id)"),
    ("credentials", "access_level", "TEXT NOT NULL DEFAULT 'read_write'"),
    ("credentials", "expires_on", "TEXT"),
    ("credential_grants", "access_level", "TEXT NOT NULL DEFAULT 'read_write'"),
)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def up(conn):
    for statement in TABLES:
        conn.execute(statement)
    for table, column, kind in COLUMNS:
        if _table(conn, table) and column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
