-- What the old task engine left on a database from before the Pipelines v3 cut-over that the cut-over does not drop.
--
-- The change-feed triggers over the old task engine's tables and columns (backend/events.py before the Pipelines v3
-- cut-over). The cut-over drops tasks.carried_by, type_id and step_id, task_types, task_steps and approvals; a trigger
-- left on attempts or tasks that names one fails every write to its table ("no such column: carried_by"), before
-- events.ensure can drop it at the end of the start. A new database never has them.
DROP TRIGGER IF EXISTS changes_attempts_carried;
DROP TRIGGER IF EXISTS changes_task_types_update;
DROP TRIGGER IF EXISTS changes_task_steps_insert;
DROP TRIGGER IF EXISTS changes_task_steps_update;
DROP TRIGGER IF EXISTS changes_task_steps_delete;
DROP TRIGGER IF EXISTS changes_approvals_insert;
DROP TRIGGER IF EXISTS changes_approvals_update;
DROP TRIGGER IF EXISTS changes_stale_suggestions_insert;
DROP TRIGGER IF EXISTS changes_stale_suggestions_update;

-- The done-task close rule's table and indexes (task_rules.SCHEMA, never run since the cut-over) and the first-start
-- note for the old roadmap types (20261108011313_pipeline_manager_since), and the head records of the old checks
-- (check_head_provenance, made on first use by the step checks): nothing reads any of them.
DROP INDEX IF EXISTS tasks_rule_due;
DROP INDEX IF EXISTS tasks_rule_done_due;
DROP TABLE IF EXISTS task_rules;
DROP TABLE IF EXISTS first_start_seeds;
DROP TABLE IF EXISTS check_head_provenance;
