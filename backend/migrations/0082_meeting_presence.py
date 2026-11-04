"""Session presence and the live source of a task sent before Save."""
from backend.meetings_schema import PRESENCES_SCHEMA


def up(conn):
    for statement in PRESENCES_SCHEMA.split(';'):
        if statement.strip():
            conn.execute(statement)
    if 'live_meeting_id' not in {r[1] for r in conn.execute('PRAGMA table_info(tasks)')}:
        conn.execute('ALTER TABLE tasks ADD COLUMN live_meeting_id TEXT')
