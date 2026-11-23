-- Who a task is for: the list's assignee filter (`assignee=me`, any status) and a person assignee's read clause
-- (auth.task_sql: their task ids, read once per statement so a list keeps tasks_queue_cover). Not limited to open
-- tasks: the list binds its status, which a `WHERE status='open'` index would never match.
CREATE INDEX IF NOT EXISTS tasks_assignee ON tasks(assignee_id, status, id) WHERE assignee_id IS NOT NULL;
