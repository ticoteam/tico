"""A website account's `has_password` (backend/website_tools.py): 0 for an account a person signs in to by hand (Google
or SSO sign-in, an emailed code, a passkey), which stores no password. Every account stored before this had one.

An install may still have 20261111145540_website_tools pending, and pending files run in any order: this adds that
migration's `has_totp` and `holder` too, first and with the same definitions, so the columns end in one order whichever
runs first (that migration skips a column already there).

`credentials` does not exist on a new file: there backend/store.py's SCHEMA makes it with the columns.
"""

COLUMNS = (
    ("has_totp", "INTEGER NOT NULL DEFAULT 0"),
    ("holder", "TEXT"),
    ("has_password", "INTEGER NOT NULL DEFAULT 1"),
)


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(credentials)")}
    if not columns:
        return
    for column, kind in COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE credentials ADD COLUMN {column} {kind}")
