"""A file source can have a signed address (backend/growth.py): `growth_source_secrets` holds its HMAC key, sealed with
the credential vault (a signature check needs the secret itself). No row: no address. A table of its own, so this
migration does not depend on the order other migrations alter gtm_sources in."""


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS growth_source_secrets (source_id TEXT PRIMARY KEY, signing_secret TEXT NOT NULL)"
    )
