-- The default run limit is now a day (runner-rs execute.rs DEFAULT_RUN_MINUTES). A shorter stored limit was almost always
-- 20-60 minutes copied from a template or the registry defaults at setup, so it goes back to the default; a person who
-- wants a short limit for a routine sets it again in Settings > Bots. Runners pick the change up (changes_runner_bot_settings_update).
UPDATE bot_settings SET max_run_minutes=NULL WHERE max_run_minutes IS NOT NULL AND max_run_minutes < 1440;
