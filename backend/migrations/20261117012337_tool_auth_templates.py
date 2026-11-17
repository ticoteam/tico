"""Multi-secret auth templates on a tool (backend/tool_templates.py).

`tools.auth_template`: '' for a tool whose one key goes on a call as `auth_scheme` says, else JSON naming how its
several fields are sent: a known vendor's template (`{"id": "twilio"}`, tool_services.TEMPLATES) or a tool's own
(`{"fields": [...], "ways": [...]}`).

`credentials.former_tool_id`: the tool a key was on before it was grouped onto its vendor account's tool. A run's
proxy address variable (`TICO_TOOL_<ID>_URL`) for that key keeps the old tool's name and address, and the proxy answers
the old address for the grouped tool, so grouping never changes what a run reads. Cleared when the grouping is undone.

An install may still have 20261111145540_website_tools, 20261113043103_website_account_has_password,
20261116133301_mcp_tools or 20261117000837_add_everyone_reads_to_tools pending, and pending files run in any order: this
adds their columns first, with the same definitions, so the columns end in one order whichever runs first (those
migrations skip a column already there).
"""

TOOLS = (
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
)
CREDENTIALS = (
    ("has_totp", "INTEGER NOT NULL DEFAULT 0"),
    ("holder", "TEXT"),
    ("has_password", "INTEGER NOT NULL DEFAULT 1"),
    ("former_tool_id", "TEXT"),
)


def up(conn):
    for table, columns in (("tools", TOOLS), ("credentials", CREDENTIALS)):
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns:
            if have and column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
