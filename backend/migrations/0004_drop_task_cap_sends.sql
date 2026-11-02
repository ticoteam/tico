-- The retired per-task cap log. task_cap_log (one row per send) replaced it and copied its rows in long ago; nothing
-- reads or writes it.
DROP TABLE IF EXISTS task_cap_sends;
