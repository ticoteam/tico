"""A stale Recorder proposal must not overwrite an item edited on the meeting."""


def up(conn):
    if 'version' not in {row[1] for row in conn.execute('PRAGMA table_info(meeting_items)')}:
        conn.execute('ALTER TABLE meeting_items ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
