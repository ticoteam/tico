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
    conn.execute("CREATE INDEX IF NOT EXISTS meeting_items_sent_ref ON meeting_items(result_ref,meeting_id) WHERE status='pushed'")
    conn.execute("CREATE TRIGGER IF NOT EXISTS presences_meeting_delete BEFORE DELETE ON meetings BEGIN "
                 "DELETE FROM presences WHERE place='meeting:'||OLD.id; "
                 "DELETE FROM task_roles WHERE role='participant' AND task_id IN "
                 "(SELECT result_ref FROM meeting_items WHERE meeting_id=OLD.id AND status='pushed' UNION SELECT t.id FROM tasks t JOIN live_meetings lm ON lm.id=t.live_meeting_id WHERE lm.imported_meeting_id=OLD.id); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS participants_live_delete BEFORE DELETE ON live_meetings BEGIN "
                 "DELETE FROM task_roles WHERE role='participant' AND task_id IN "
                 "(SELECT id FROM tasks WHERE live_meeting_id=OLD.id); END")

    # A view keeps the source policy out of the parser stack of nested task/Health queries.
    conn.execute("""CREATE VIEW IF NOT EXISTS meeting_participant_readers AS
        SELECT m.id AS meeting_id, h.id AS human_id FROM meetings m CROSS JOIN humans h
        WHERE h.email<>'' AND CASE
          WHEN EXISTS (SELECT 1 FROM media_control mc WHERE mc.meeting_id=m.id AND mc.deleted_at IS NOT NULL) THEN 0
          WHEN lower(h.email)=lower(m.owner) THEN 1
          WHEN m.review_state<>'live' THEN 0
          WHEN coalesce(json_extract(m.metadata,'$.audience'),'')<>'participants' AND
            lower(h.email)=(SELECT lower(json_extract(value,'$.email')) FROM registry_metadata WHERE key='owner') THEN 1
          WHEN coalesce(json_extract(m.metadata,'$.kind'),'')<>'meeting' THEN 0
          WHEN json_extract(m.metadata,'$.audience')='participants' THEN
            EXISTS (SELECT 1 FROM json_each(m.metadata,'$.audience_emails') a WHERE lower(trim(a.value))=lower(h.email))
          WHEN NOT coalesce(json_extract(m.metadata,'$.private'),0) THEN 1
          WHEN EXISTS (SELECT 1 FROM json_each(m.metadata,'$.calendar.attendees') a WHERE lower(trim(a.value))=lower(h.email)) THEN 1
          ELSE EXISTS (SELECT 1 FROM json_each(m.metadata,'$.participants') a
            WHERE lower(trim(json_extract(a.value,'$.email')))=lower(h.email)) END""")
