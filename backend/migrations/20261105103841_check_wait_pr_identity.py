"""Keep a queued review tied to its full PR identity, even when numbers repeat across repos."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(check_waits)")}
    if columns and "pr_url" not in columns:
        conn.execute("ALTER TABLE check_waits ADD COLUMN pr_url TEXT")
