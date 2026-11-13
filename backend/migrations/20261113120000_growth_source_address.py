"""A file source can have a signed address (backend/growth.py): `gtm_sources.signing_secret` is its HMAC key, sealed with
the credential vault (a signature check needs the secret itself). NULL: no address."""


def up(conn):
    have = {row[1] for row in conn.execute("PRAGMA table_info(gtm_sources)")}
    if have and "signing_secret" not in have:
        conn.execute("ALTER TABLE gtm_sources ADD COLUMN signing_secret TEXT")
