"""An MCP tool's "Only reads" (backend/mcp_connect.py `only_reads`): `tools.mcp_only_reads`, JSON [{name, by, at}], the
tool names an administrator marked as only reading, so a bot at Read may call them though the server gives no hint.

An install may still have the earlier `tools` column migrations pending, and pending files run in any order: this adds
their columns first, with the same definitions and `mcp_only_reads` last, so the columns end in one order whichever runs
first (those migrations skip a column already there; see 20261122030528_tools_columns_in_one_order).
"""

COLUMNS = (
    ("kind", "TEXT NOT NULL DEFAULT 'api'"),
    ("sites", "TEXT"),
    ("central_only", "INTEGER NOT NULL DEFAULT 0"),
    ("mcp_url", "TEXT NOT NULL DEFAULT ''"),
    ("mcp_transport", "TEXT NOT NULL DEFAULT ''"),
    ("mcp_tools", "TEXT"),
    ("mcp_oauth", "TEXT"),
    ("mcp_client_secret", "TEXT"),
    ("everyone_reads", "INTEGER NOT NULL DEFAULT 1 CHECK (everyone_reads IN (0, 1))"),
    ("auth_template", "TEXT NOT NULL DEFAULT ''"),
    ("record_owner", "TEXT NOT NULL DEFAULT ''"),
    ("mcp_only_reads", "TEXT"),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tools)")}
    for column, kind in COLUMNS:
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE tools ADD COLUMN {column} {kind}")
