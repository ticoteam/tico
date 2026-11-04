"""Cleanup wave 1: every `*_json` column drops the suffix (refs_json -> refs, labels_json -> labels, ...). The stored
text does not change: each column still holds JSON.

The renames are backend/migrations/renames_data.py's wave 1 entries, applied by SQLite's own ALTER TABLE ... RENAME COLUMN
(backend/migrations/renames.py), which rewrites every index, trigger and view that names a column. A table or column the
database does not have is skipped: on a new file hubdb's steps and the module schema code already make the new names,
and a module's table made later is created under them. A database with both names is refused rather than guessed at.
"""
from backend.migrations import renames as schema_renames

WAVE = "1"


def up(conn):
    schema_renames.apply(conn, waves=(WAVE,))
