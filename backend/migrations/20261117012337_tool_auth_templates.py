"""Multi-secret auth templates on a tool (backend/tool_templates.py).

`tools.auth_template`: '' for a tool whose one key goes on a call as `auth_scheme` says, else JSON naming how its
several fields are sent: a known vendor's template (`{"id": "twilio"}`, tool_services.TEMPLATES) or a tool's own
(`{"fields": [...], "ways": [...]}`).

`credentials.former_tool_id`: the tool a key was on before it was grouped onto its vendor account's tool. A run's
proxy address variable (`TICO_TOOL_<ID>_URL`) for that key keeps the old tool's name and address, and the proxy answers
the old address for the grouped tool, so grouping never changes what a run reads. Cleared when the grouping is undone.
"""


def up(conn):
    tools = {row[1] for row in conn.execute("PRAGMA table_info(tools)")}
    if tools and "auth_template" not in tools:
        conn.execute("ALTER TABLE tools ADD COLUMN auth_template TEXT NOT NULL DEFAULT ''")
    keys = {row[1] for row in conn.execute("PRAGMA table_info(credentials)")}
    if keys and "former_tool_id" not in keys:
        conn.execute("ALTER TABLE credentials ADD COLUMN former_tool_id TEXT")
