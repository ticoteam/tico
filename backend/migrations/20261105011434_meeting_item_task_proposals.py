"""A task proposal preserves its target without widening the meeting's access."""


def up(conn):
    if 'proposed_task_id' not in {row[1] for row in conn.execute('PRAGMA table_info(meeting_items)')}:
        conn.execute('ALTER TABLE meeting_items ADD COLUMN proposed_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL')
