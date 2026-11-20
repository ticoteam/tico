-- A task's row shows the newest email send that belongs to it or whose OK sits on it (email_sending.for_tasks): a page
-- of tasks looks its sends up by these two columns, which nothing indexed before.
CREATE INDEX IF NOT EXISTS email_sends_task ON email_sends(task_id) WHERE task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS email_sends_approval_task ON email_sends(approval_task_id) WHERE approval_task_id IS NOT NULL;
