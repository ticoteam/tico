"""The few fields a run's status line reads from a big output event (backend/step_briefs.py), kept beside the event.

- `attempt_event_briefs`: per attempt and seq, the brief of a delta, message or tool event whose payload is big. A
  separate table, not a column: a column stored after a big payload is read only by walking its overflow pages.
- Two triggers drop a brief when its event's payload is rewritten (a scrubbed credential, a wiped held reply, a revoked
  page) or the event is deleted, whichever code does it, so a brief is never stale.

The triggers sit on `attempt_events`, which the store's module code makes after the migrations. On a new file it does
not exist yet, so this makes it first with the store's own definition (backend/store.py), word for word; the store's
`CREATE TABLE IF NOT EXISTS` then leaves it as it is.
"""

EVENTS = """CREATE TABLE IF NOT EXISTS attempt_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL REFERENCES attempts(id),
 seq INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(attempt_id,seq))"""
STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS attempt_event_briefs("
    "attempt_id TEXT NOT NULL, seq INTEGER NOT NULL, brief TEXT NOT NULL, "
    "PRIMARY KEY(attempt_id,seq)) WITHOUT ROWID",
    "CREATE TRIGGER IF NOT EXISTS attempt_event_briefs_rewritten AFTER UPDATE OF payload ON attempt_events BEGIN "
    "DELETE FROM attempt_event_briefs WHERE attempt_id=OLD.attempt_id AND seq=OLD.seq; END",
    "CREATE TRIGGER IF NOT EXISTS attempt_event_briefs_deleted AFTER DELETE ON attempt_events BEGIN "
    "DELETE FROM attempt_event_briefs WHERE attempt_id=OLD.attempt_id AND seq=OLD.seq; END",
)


def up(conn):
    conn.execute(EVENTS)
    for sql in STATEMENTS:
        conn.execute(sql)
