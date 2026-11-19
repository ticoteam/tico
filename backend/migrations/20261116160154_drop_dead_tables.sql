-- Nothing reads these since 0.6: stall verdicts, the old Rules page's suggestions, and the index of Slack task-done
-- notices (no writer since the Pipelines v3 cut-over). A new database never makes them.
DROP TABLE IF EXISTS stall_triage;
DROP TABLE IF EXISTS rule_suggestions;
DROP INDEX IF EXISTS slack_task_completion_notices;
