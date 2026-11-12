"""A computer may be given a person's plans for a runtime instead of one subscription (`computer_subscriptions.person`,
backend/model_subscriptions.py): the person's actor id, or '' for one subscription as before. On a person's row
`subscription_id` is the plan in use now. Existing rows each name one subscription, so they keep ''. On a new file the
table does not exist yet: model_subscriptions.SCHEMA makes it with the column."""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='computer_subscriptions'").fetchone():
        return
    if "person" not in _columns(conn, "computer_subscriptions"):
        conn.execute("ALTER TABLE computer_subscriptions ADD COLUMN person TEXT NOT NULL DEFAULT ''")
