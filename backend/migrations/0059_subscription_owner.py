"""A company plan (a Claude or ChatGPT plan) has an owner, a person (`human:<id>`): model_subscriptions.owner_id, and the
owner a Connect in progress will save it with, subscription_connects.owner_id. A saved plan's owner is whoever saved it
when that was a person, else empty. An API key has none. On a new file the tables do not exist yet:
backend/model_subscriptions.py SCHEMA makes them with the column."""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def up(conn):
    for table in ("model_subscriptions", "subscription_connects"):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        if "owner_id" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
        if table == "model_subscriptions":
            conn.execute("UPDATE model_subscriptions SET owner_id=added_by WHERE owner_id='' AND added_by LIKE 'human:%' "
                         "AND kind IN ('claude_token','codex_login')")
