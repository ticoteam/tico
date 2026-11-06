"""Who can see an API tool (backend/api_tools.py `visible`): `tools.audience`, NULL for Everyone (the rules before it),
else the audience JSON pages use (`pages.audience`). Every existing tool keeps NULL."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tools)")}
    if columns and "audience" not in columns:
        conn.execute("ALTER TABLE tools ADD COLUMN audience TEXT")
