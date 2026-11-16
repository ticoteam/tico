"""Remote MCP servers as Tools (backend/mcp_connect.py): an API tool with `mcp_url` is an MCP server. `mcp_transport`
(http or sse), `mcp_tools` (JSON: the tool names its last check listed and which were marked read-only), `mcp_oauth`
(JSON: the server's OAuth endpoints and the client Tico signs in as, never a secret) and `mcp_client_secret` (that
client's secret when it has one, sealed with the vault's key). A sign-in is a `credentials` row of kind `oauth` on the
tool, its tokens sealed like any value. `tool_oauth_states` holds a sign-in on its way: the hash of its state and its
PKCE verifier, sealed, for at most STATE_TTL.

`tools` exists on every file by now (20261105220549_tools_and_keys). Every existing tool stays as it is.
"""

COLUMNS = (
    ("mcp_url", "TEXT NOT NULL DEFAULT ''"),
    ("mcp_transport", "TEXT NOT NULL DEFAULT ''"),
    ("mcp_tools", "TEXT"),
    ("mcp_oauth", "TEXT"),
    ("mcp_client_secret", "TEXT"),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tools)")}
    for column, kind in COLUMNS:
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE tools ADD COLUMN {column} {kind}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tool_oauth_states("
        " id TEXT PRIMARY KEY, tool_id TEXT NOT NULL REFERENCES tools(id), actor TEXT NOT NULL,"
        " verifier TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
