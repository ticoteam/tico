"""Cleanup waves 2s, 2b and 3: the remaining time columns take named times (due -> due_at, ts -> created_at, a date-only
column ends in _on), actor columns in the stable tables take _id (owner -> owner_id, from_actor -> sender_id), operator
-> owner_id, grp -> group_id, dept -> department. The stored values do not change: an actor column still holds
`bot:<id>` or `human:<id>`.

The renames are backend/migrations/renames_data.py's entries for these waves, applied the way 0013 and 0014 apply theirs
(backend/migrations/renames.py).
"""
from backend.migrations import renames as schema_renames

WAVES = ("2s", "2b", "3")


def up(conn):
    schema_renames.apply(conn, waves=WAVES)
    # These look for a key in stored answers, which a column rename does not reach; idempotency_messages.ensure makes
    # them again knowing both names.
    conn.execute("DROP TRIGGER IF EXISTS idempotency_messages_insert")
    conn.execute("DROP TRIGGER IF EXISTS idempotency_messages_update")
