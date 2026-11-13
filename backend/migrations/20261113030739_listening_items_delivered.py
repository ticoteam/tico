"""`listening_items.delivered` (backend/listening.py `settle`): for an item a run's steps did not all deliver, which
of them did (a JSON list of step ids), so the next send hands it only to the others. NULL for none. Items now also
read `sending` while a run holds them; that status needs no column.

A database from before the listening tables (20261111190425_listening_feed, still pending there) may run this step
first: it makes those tables as that step does, and that step's CREATE ... IF NOT EXISTS then leaves them, so the
schema is the same in either order. Running it again changes nothing.

Rollback (a person's data step after reverting the release): `UPDATE listening_items SET status='kept',sent_at=NULL
WHERE status='sending'` puts every item a run still holds back to waiting.
"""

import importlib


def up(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(listening_items)")}
    if not columns:
        for statement in importlib.import_module("backend.migrations.20261111190425_listening_feed").TABLES:
            conn.execute(statement)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(listening_items)")}
    if "delivered" not in columns:
        conn.execute("ALTER TABLE listening_items ADD COLUMN delivered TEXT")
