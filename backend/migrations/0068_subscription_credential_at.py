"""A plan's credential time (`model_subscriptions.credential_at`, backend/model_subscriptions.py): when its value was last
replaced or reconnected. A usage reading taken before it is the old account's, so it is not shown. A rename, an owner
change or a same-account refresh does not move it. A plan that exists from before starts from its last change
(`updated_at`), which hides a little more than it must, never another account's reading. On a new file the table does
not exist yet: model_subscriptions.SCHEMA makes it with the column."""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_subscriptions'").fetchone():
        return
    if "credential_at" not in _columns(conn, "model_subscriptions"):
        conn.execute("ALTER TABLE model_subscriptions ADD COLUMN credential_at TEXT")
        conn.execute("UPDATE model_subscriptions SET credential_at=updated_at WHERE credential_at IS NULL")
