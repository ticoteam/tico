-- The default run limit is now a day (runner-rs execute.rs DEFAULT_RUN_MINUTES). Only the values templates and the registry
-- defaults ever shipped (20, 30, 40, 45, 60 minutes, copied in at setup) go back to the default; any other stored limit was
-- chosen for that bot and stays. Runners pick the change up (changes_runner_bot_settings_update).
UPDATE bot_settings SET max_run_minutes=NULL WHERE max_run_minutes IN (20, 30, 40, 45, 60);
