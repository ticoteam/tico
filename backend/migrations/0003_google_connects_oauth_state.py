"""google_connects.state is the OAuth nonce, so it is named oauth_state and is not read as the connect's lifecycle
(`status`). A file without the table, or with the column already renamed, is left alone: google_connect.ensure_schema
creates the new shape and its index."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(google_connects)")}
    if "state" not in columns or "oauth_state" in columns:
        return
    conn.execute("DROP INDEX IF EXISTS google_connects_state")
    conn.execute("ALTER TABLE google_connects RENAME COLUMN state TO oauth_state")
