"""Keep sources beside cached Assistant action prose."""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(assistant_actions)")}
    if columns and "refs" not in columns:
        conn.execute("ALTER TABLE assistant_actions ADD COLUMN refs TEXT")
