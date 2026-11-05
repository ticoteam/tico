ALTER TABLE meeting_items ADD COLUMN proposed_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;
