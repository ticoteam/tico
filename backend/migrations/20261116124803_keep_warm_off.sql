-- Keep warm is off unless a company turns it on (backend/keep_warm.py): every pipeline's keep_warm_hours goes to 0, and
-- a stage's own hours are removed, so the stage takes its pipeline's (0). A stored 8 cannot be told from the old
-- default, so every pipeline is turned off, and its settings version goes up, so an editor open on the old record reads
-- it again before it saves. The column's DEFAULT stays 8 (changing it rebuilds the table): the one
-- insert, pipeline_settings, always names the hours (KEEP_WARM_DEFAULT, 0). Running it again changes nothing.
UPDATE pipelines SET keep_warm_hours = 0, settings_version = settings_version + 1 WHERE keep_warm_hours <> 0;
DELETE FROM stage_keep_warm WHERE hours > 0;
