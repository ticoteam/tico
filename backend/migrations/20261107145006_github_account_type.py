"""Whether the GitHub App's account is a personal account ('User') or an organization ('Organization'): the URLs that manage
the app differ (backend/github_app.py `links`). Every app connected before this was made in an organization. On a new
file the module code makes both tables after the migrations, with the column already in them, so only a table that
exists gets the column here."""


def up(conn):
    for table in ("github_app", "github_app_states"):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        if "account_type" not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN account_type TEXT NOT NULL DEFAULT 'Organization'")
