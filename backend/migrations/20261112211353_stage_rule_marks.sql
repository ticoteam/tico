-- What a stage rule's timer (backend/stage_rule_timers.py) found for one task: one row per rule, task and mark (the
-- visit and the moment its timer started from), so a timer is judged once per mark and acts once. `outcome` is
-- `no` (its If did not match), `late` (already listed as Late), `no_manager` (no manager to tell, or the manager owns
-- it), `pending` (a Tell the manager not sent yet), `told`, `dropped` (the task ended before the wake) or `failed`
-- (the manager could not be woken). `manager_id` and `told_at` batch a manager's tells: one wake an hour at most.
CREATE TABLE IF NOT EXISTS stage_rule_marks (
  rule_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  mark TEXT NOT NULL,
  outcome TEXT NOT NULL,
  manager_id TEXT,
  matched_at TEXT NOT NULL,
  told_at TEXT,
  PRIMARY KEY (rule_id, task_id, mark)
);
CREATE INDEX IF NOT EXISTS stage_rule_marks_pending ON stage_rule_marks(outcome, manager_id) WHERE outcome = 'pending';
CREATE INDEX IF NOT EXISTS stage_rule_marks_told ON stage_rule_marks(manager_id, told_at) WHERE told_at IS NOT NULL;
