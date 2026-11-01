"""Cleanup wave 2: time columns take their Rails names (created -> created_at, updated -> updated_at, started ->
started_at, last_seen -> last_seen_at, ...). The stored values do not change.

The renames are backend/migrations/renames_data.py's wave 2 entries, applied the way 0013 applies wave 1
(backend/migrations/renames.py): SQLite's own ALTER TABLE ... RENAME COLUMN rewrites every index, trigger and view that
names a column, a table or column the database does not have is skipped, and a database with both names is refused.
"""
from backend.migrations import renames as schema_renames

WAVE = "2"


def up(conn):
    schema_renames.apply(conn, waves=(WAVE,))
