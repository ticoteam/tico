"""`tools` columns in one order whichever pending migration runs first.

An install may still have 20261116133301_mcp_tools, 20261117000837_add_everyone_reads_to_tools and
20261117012337_tool_auth_templates pending when 20261122001352_outside_references runs. That file adds
`tools.record_owner` without adding their columns first, so run newest first it put `record_owner` before `mcp_url`
(release validation for v0.6.201, test_the_pending_migrations_apply_in_any_order). It is on main and cannot change, so
this one adds every pending `tools` column, `record_owner` last, with the same definitions, and runs before it when
pending files run newest first. Each of those migrations skips a column already there; on a database that has them all
this does nothing.
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
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tools)")}
    for column, kind in COLUMNS:
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE tools ADD COLUMN {column} {kind}")
