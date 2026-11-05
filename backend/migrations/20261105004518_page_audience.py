"""Pages may narrow their file's inherited audience; existing pages keep it unchanged."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)")}
    if columns and "audience" not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN audience TEXT")
