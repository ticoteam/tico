-- A send names the task it belongs to (`task_id`: the sender's task, or the task its bot's turn works on), and records
-- the task its OK sits on (`approval_task_id`), so the send's own answer links to where a person gives the OK.
ALTER TABLE email_sends ADD COLUMN task_id TEXT;
ALTER TABLE email_sends ADD COLUMN approval_task_id TEXT;

UPDATE email_sends SET approval_task_id=(
    SELECT v.task_id FROM requirements r JOIN visits v ON v.id=r.visit_id WHERE r.id=email_sends.approval_id
)
WHERE approval_id!='';
