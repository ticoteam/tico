"""Retain a Make-page turn's person after the execution lease ends."""


def up(conn):
    # No backfill: only the new Make page route creates such runs, so existing rows stay NULL (the plain bot path).
    if "acting_person" not in {row[1] for row in conn.execute("PRAGMA table_info(attempts)")}:
        conn.execute("ALTER TABLE attempts ADD COLUMN acting_person TEXT")
