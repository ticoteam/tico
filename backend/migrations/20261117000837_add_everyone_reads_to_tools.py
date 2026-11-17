"""Default read (backend/default_read.py): `tools.everyone_reads`, the tool's "Everyone can read" switch. Every bot reads
a company tool whose Read can be held to reads unless it is off; on for every tool, existing ones included. A tool where
Read cannot be enforced, an Only listed one or a website tool never default-reads whatever the switch says.

An install may still have 20261111145540_website_tools or 20261116133301_mcp_tools pending, and pending files run in any
order: this adds their columns first, with the same definitions, so the columns end in one order whichever runs first
(those migrations skip a column already there).
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
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tools)")}
    for column, kind in COLUMNS:
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE tools ADD COLUMN {column} {kind}")
