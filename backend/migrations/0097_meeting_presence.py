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

    if 'discarded_at' not in {r[1] for r in conn.execute('PRAGMA table_info(live_meetings)')}:
        conn.execute('ALTER TABLE live_meetings ADD COLUMN discarded_at TEXT')
    if 'version' not in {r[1] for r in conn.execute('PRAGMA table_info(meeting_items)')}:
        conn.execute('ALTER TABLE meeting_items ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
    conn.execute('CREATE INDEX IF NOT EXISTS presences_expiry ON presences(seen_at)')
    conn.execute("CREATE TRIGGER IF NOT EXISTS presences_meeting_delete BEFORE DELETE ON meetings BEGIN "
                 "DELETE FROM presences WHERE place='meeting:'||OLD.id; "
                 "DELETE FROM task_roles WHERE role='participant' AND task_id IN "
                 "(SELECT result_ref FROM meeting_items WHERE meeting_id=OLD.id AND status='pushed' UNION SELECT t.id FROM tasks t JOIN live_meetings lm ON lm.id=t.live_meeting_id WHERE lm.imported_meeting_id=OLD.id); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS participants_live_delete BEFORE DELETE ON live_meetings BEGIN "
                 "DELETE FROM task_roles WHERE role='participant' AND task_id IN "
                 "(SELECT id FROM tasks WHERE live_meeting_id=OLD.id); END")
