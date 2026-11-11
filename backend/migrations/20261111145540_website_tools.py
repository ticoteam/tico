"""Website tools (backend/website_tools.py): a `tools` row with `kind` 'website', its `sites` (a JSON list of hosts)
and `central_only` (1 = only the company's browser host may open it); each account a `login` credential with
`has_totp` and `holder` (NULL = shared through grants, else the one subject that owns it).

`tools` exists on every file by now (20261105220549_tools_and_keys). `credentials` does not on a new file: there
backend/store.py's SCHEMA makes it with the columns. Every existing tool stays an API tool.
"""

COLUMNS = (
    ("tools", "kind", "TEXT NOT NULL DEFAULT 'api'"),
    ("tools", "sites", "TEXT"),
    ("tools", "central_only", "INTEGER NOT NULL DEFAULT 0"),
    ("credentials", "has_totp", "INTEGER NOT NULL DEFAULT 0"),
    ("credentials", "holder", "TEXT"),
)


def up(conn):
    for table, column, kind in COLUMNS:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
