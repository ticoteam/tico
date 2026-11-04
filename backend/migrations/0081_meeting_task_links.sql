-- A link preserves the meeting and task's independent permissions.
CREATE TABLE meeting_task_links (
 id TEXT PRIMARY KEY,
 meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK (kind IN ('linked','sent','proposal')),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(meeting_id,task_id)
);
CREATE INDEX meeting_task_links_task ON meeting_task_links(task_id,created_at);
