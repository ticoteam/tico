ALTER TABLE watcher_items ADD COLUMN verify_after TEXT;
CREATE INDEX watcher_items_verification ON watcher_items(verify_after) WHERE verify_after IS NOT NULL;
CREATE INDEX watcher_items_task ON watcher_items(task_id);
