-- A task's own thread is found by its conversation (hubdb.task_post_sql, the write-side trigger and the read-side unread,
-- inbox and sidebar predicates): an index on tasks.conversation_id keeps each EXISTS a point lookup.
CREATE INDEX IF NOT EXISTS tasks_conversation ON tasks(conversation_id);
