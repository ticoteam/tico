"""Store the maximum number of live turns for each plan."""


def up(conn):
    # Older installs can still be waiting for the vault migration. Keep its column before the limit in either order.
    if "credential_id" not in {row[1] for row in conn.execute("PRAGMA table_info(model_subscriptions)")}:
        conn.execute("ALTER TABLE model_subscriptions ADD COLUMN credential_id TEXT REFERENCES credentials(id)")
    conn.execute(
        "ALTER TABLE model_subscriptions ADD COLUMN max_live_turns INTEGER NOT NULL DEFAULT 20 "
        "CHECK (max_live_turns BETWEEN 1 AND 200)"
    )
