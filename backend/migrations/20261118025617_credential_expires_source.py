"""Where a key's end date (`credentials.expires_on`) came from (backend/api_tools.py `vendor_end_date`).

`credentials.expires_source`: `vendor` when the tool's check read it from the vendor's reply (GitHub's token expiration
header, Cloudflare's token verify), which the next check keeps current; `person` when a person typed it on the key.
NULL for no end date, and for dates typed before this column existed (they count as typed).

An install may still have 20261117012337_tool_auth_templates (or the older website-account migrations it covers)
pending, and pending files run in any order: this adds their credential columns first, with the same definitions, so
the columns end in one order whichever runs first (those migrations skip a column already there).
"""

CREDENTIALS = (
    ("has_totp", "INTEGER NOT NULL DEFAULT 0"),
    ("holder", "TEXT"),
    ("has_password", "INTEGER NOT NULL DEFAULT 1"),
    ("former_tool_id", "TEXT"),
    ("expires_source", "TEXT"),
)


def up(conn):
    have = {row[1] for row in conn.execute("PRAGMA table_info(credentials)")}
    for column, kind in CREDENTIALS:
        if have and column not in have:
            conn.execute(f"ALTER TABLE credentials ADD COLUMN {column} {kind}")
