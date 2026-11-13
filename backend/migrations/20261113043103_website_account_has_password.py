"""A website account's `has_password` (backend/website_tools.py): 0 for an account a person signs in to by hand (Google
or SSO sign-in, an emailed code, a passkey), which stores no password. Every account stored before this had one.

`credentials` does not exist on a new file: there backend/store.py's SCHEMA makes it with the column.
"""


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(credentials)")}
    if columns and "has_password" not in columns:
        conn.execute("ALTER TABLE credentials ADD COLUMN has_password INTEGER NOT NULL DEFAULT 1")
