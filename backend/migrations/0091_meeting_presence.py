"""Session presence and the live source of a task sent before Save."""
# Each open window has its own expiring presence; leaving one never removes another.
PRESENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS presences (
 id TEXT PRIMARY KEY, place TEXT NOT NULL, actor TEXT NOT NULL, session_id TEXT NOT NULL,
 seen_at TEXT NOT NULL, UNIQUE(place, session_id)
);
CREATE INDEX IF NOT EXISTS presences_place_seen ON presences(place, seen_at);
"""


def up(conn):
    for statement in PRESENCES_SCHEMA.split(';'):
        if statement.strip():
            conn.execute(statement)
    if 'live_meeting_id' not in {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}:
        conn.execute('ALTER TABLE tasks ADD COLUMN live_meeting_id TEXT')
